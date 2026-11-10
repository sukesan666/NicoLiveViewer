-- NicoLive Viewer: bounded scrolling comments rendered inside mpv.
local mp = require 'mp'
local utils = require 'mp.utils'
local assdraw = require 'mp.assdraw'
local overlay = mp.create_osd_overlay('ass-events')
local config = {font='Meiryo', size=28, enabled=true, fps=120}
local frame_interval = 1/120
local media_pos, media_at = nil, mp.get_time()
mp.observe_property('time-pos', 'number', function(_, value)
    if value then media_pos=value; media_at=mp.get_time() end
end)
local function comment_time()
    if not media_pos then return mp.get_time() end
    if mp.get_property_bool('pause', false) then return media_pos end
    return media_pos + (mp.get_time()-media_at) * mp.get_property_number('speed',1)
end
local comments = {}
local arrivals = {}
local duration = 8
local min_duration = 3.2

local function active_duration(now)
    local recent = {}
    for _, t in ipairs(arrivals) do
        if t <= now and now - t <= 2 then recent[#recent+1] = t end
    end
    arrivals = recent
    local pressure = math.max(#comments, #arrivals * 2)
    if pressure <= 4 then return duration end
    local scale = math.min(1, (pressure - 4) / 46)
    return duration - scale * (duration - min_duration)
end

-- Keep layout in 720p coordinates so fonts, lanes, borders and active comments
-- scale together when the player is resized or switched to fullscreen.
local function comment_canvas()
    local w,h = mp.get_osd_size()
    if not w or w<=0 or not h or h<=0 then return nil end
    return w*720/h, 720
end

local function video_info()
    local found=false
    for _,f in ipairs(mp.get_property_native('vf',{})) do
        if f.name=='vapoursynth' then found=true end
    end
    local fps=mp.get_property_number('estimated-vf-fps',0)
    local text='動画補間フィルター: '..(found and '検出' or '未検出')
    text=text..'\n動画の推定fps: '..(fps>0 and string.format('%.1f',fps) or '計測待ち')
    text=text..'\nコメント更新目標: '..tostring(config.fps)..'回/秒'
    mp.osd_message(text,8)
end
mp.add_key_binding('F9','nico-video-info',video_info)
mp.register_script_message('nico-video-info',video_info)

local function escape(text)
    -- escape-ass is supported by the bundled/current mpv. Older builds use a safe fallback.
    local ok, value = pcall(mp.command_native, {'escape-ass', text})
    if ok and value then return value end
    return text:gsub('\\', '＼'):gsub('{', '｛'):gsub('}', '｝'):gsub('[\r\n]', ' ')
end

mp.register_script_message('nico-config', function(data)
    local c = utils.parse_json(data)
    if not c then return end
    config.font = tostring(c.font or 'Meiryo'):gsub('[{}\\\r\n]', '')
    config.size = math.max(12, math.min(96, tonumber(c.size) or 28))
    config.enabled = c.enabled ~= false
    config.fps = math.max(1, math.min(240, tonumber(c.fps) or 120))
    frame_interval = 1/config.fps
    if not config.enabled then comments = {}; arrivals = {}; overlay:remove() end
end)

mp.register_script_message('nico-comment', function(text, color)
    if not config.enabled then return end
    local w,h = comment_canvas()
    if not w then return end
    local now = comment_time()
    local active = {}
    for _, c in ipairs(comments) do
        local d = c.duration or duration
        if now - c.at < d then active[#active+1] = c end
    end
    comments = active
    if #arrivals < 200 then table.insert(arrivals, now) end
    local cur_duration = active_duration(now)
    for _, c in ipairs(comments) do
        local d = c.duration or duration
        if d > cur_duration then
            local progress = math.max(0, (now - c.at) / d)
            c.duration = cur_duration
            c.at = now - progress * cur_duration
        end
    end
    if #comments >= 100 then return end
    color = tostring(color or 'FFFFFF'):upper()
    if not color:match('^%x%x%x%x%x%x$') then color='FFFFFF' end
    -- ASS color order is BGR, while NDGR and IPC use RGB.
    local bgr = color:sub(5,6)..color:sub(3,4)..color:sub(1,2)
    local border = (color=='000000' or color=='666666') and 'FFFFFF' or '000000'
    local size = config.size * 4/3
    -- UTF-8 continuation bytes do not count as glyphs; conservative width reserves CJK space.
    local _, glyphs = text:gsub('[^\128-\191]', '')
    local width = math.max(size, glyphs*size)
    local row_h = math.max(size+10, 34)
    local y = 12
    local chosen = nil
    while y + row_h < h do
        local free = true
        for _, c in ipairs(comments) do
            if math.abs(c.y-y) < math.max(c.size,size)+10 then
                local c_dur = c.duration or duration
                local x = w - ((now-c.at)/c_dur)*(w+c.width)
                if x+c.width+24 > w then free=false; break end
                -- Faster new comments must not catch the previous comment before it exits.
                local new_speed=(w+width)/cur_duration
                local old_speed=(w+c.width)/c_dur
                if new_speed>old_speed and (w-x-c.width)/(new_speed-old_speed) < (x+c.width)/old_speed then
                    free=false; break
                end
            end
        end
        if free then chosen=y; break end
        y=y+row_h
    end
    if not chosen then return end
    table.insert(comments,{text=escape(text),at=now,duration=cur_duration,y=chosen,size=size,font=config.font,width=width,color=bgr,border=border})
end)

mp.register_script_message('nico-clear', function() comments={}; arrivals={}; overlay:remove() end)
mp.register_event('seek', function() comments={}; arrivals={}; overlay:remove() end)

local function render_comments()
    if not config.enabled or #comments==0 then overlay:remove(); return end
    local w,h=comment_canvas()
    if not w then return end
    local now=comment_time(); local a=assdraw.ass_new(); local keep={}
    for _,c in ipairs(comments) do
        local d=c.duration or duration
        if now-c.at<d then
            local x=w-((now-c.at)/d)*(w+c.width)
            a:new_event(); a:append(string.format('{\\an7\\pos(%.1f,%.1f)\\fn%s\\fs%.1f\\bord2\\shad0\\1c&H%s&\\3c&H%s&}',x,c.y,c.font,c.size,c.color,c.border))
            a:append(c.text); table.insert(keep,c)
        end
    end
    comments=keep; overlay.res_x=w; overlay.res_y=h; overlay.data=a.text; overlay:update()
end

-- Fixed deadlines compensate for Lua/render time instead of accumulating timer drift.
-- Missed deadlines are skipped; elapsed-time positions preserve constant scroll speed.
local next_tick = mp.get_time() + frame_interval
local frame_timer
frame_timer = mp.add_timeout(frame_interval, function()
    render_comments()
    next_tick = next_tick + frame_interval
    local now = mp.get_time()
    if next_tick <= now then
        next_tick = next_tick + (math.floor((now-next_tick)/frame_interval)+1)*frame_interval
    end
    frame_timer.timeout = math.max(0.001, next_tick-now)
    frame_timer:resume()
end)
