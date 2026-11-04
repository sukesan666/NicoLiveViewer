-- Offline regression check, run with mpv --no-config --load-scripts=no
-- --idle=yes --vo=null --ao=null --script=tests/check_comment_scaling.lua.
local real_mp = require 'mp'
local utils = require 'mp.utils'
local overlay = {update=function() end, remove=function() end}
local messages = {}
local screen_w, screen_h = 1280, 720
local now = 0
local fake_mp = {
    create_osd_overlay=function() return overlay end,
    get_time=function() return now end,
    get_osd_size=function() return screen_w, screen_h, 1 end,
    get_property_bool=function() return false end,
    get_property_number=function(_, default) return default end,
    observe_property=function() end,
    register_event=function() end,
    add_key_binding=function() end,
    register_script_message=function(name, handler) messages[name]=handler end,
    command_native=function(args) return args[2] end,
    add_timeout=function(_, callback)
        return {resume=function() end, tick=callback}
    end,
}
local timer
local add_timeout = fake_mp.add_timeout
fake_mp.add_timeout=function(delay, callback)
    timer=add_timeout(delay, callback)
    return timer
end

local function check()
    package.loaded.mp = fake_mp
    dofile(utils.join_path(real_mp.get_property('working-directory'), 'src/nicoview/assets/comments.lua'))
    package.loaded.mp = real_mp

    local function render(w, h)
        screen_w, screen_h = w, h
        timer.tick()
        local fs = assert(overlay.data:match('\\fs([%d.]+)'), overlay.data)
        return tonumber(fs)*h/overlay.res_y, overlay.data
    end

    messages['nico-comment']('existing', 'FF0000')
    now=2
    local base_size, base_data = render(1280,720)
    assert(math.abs(base_size-37.3)<0.1, '720p must preserve the original font size')
    local full_size, full_data = render(2560,1440)
    assert(math.abs(full_size/base_size-2)<0.01, 'existing comments must grow with the screen')
    assert(base_data==full_data, 'same-aspect resize must preserve relative position and styling')
    local small_size = render(640,360)
    assert(math.abs(small_size/base_size-0.5)<0.01, 'comments must shrink when leaving fullscreen')
    local restored_size = render(1280,720)
    assert(restored_size==base_size, 'repeated resizing must not accumulate scaling')

    render(2560,1440)
    messages['nico-comment']('new', '0000FF')
    timer.tick()
    assert(overlay.data:find('existing',1,true) and overlay.data:find('new',1,true),
        'new and existing comments must coexist after resizing')
    local sizes={}
    for size in overlay.data:gmatch('\\fs([%d.]+)') do sizes[#sizes+1]=size end
    assert(#sizes==2 and sizes[1]==sizes[2], 'new and existing comments must use the same scale')

    render(1000,1000)
    assert(math.abs(overlay.res_x/overlay.res_y-1)<0.001, 'coordinate system must follow the aspect ratio')
    screen_w, screen_h = 0, 0
    timer.tick()
    messages['nico-comment']('invalid screen')
    messages['nico-clear']()
    timer.tick()
    real_mp.msg.info('COMMENT_SCALING PASS')
end

local ok, err = pcall(check)
package.loaded.mp = real_mp
if not ok then real_mp.msg.error('COMMENT_SCALING FAIL: '..tostring(err)) end
real_mp.commandv('quit', ok and '0' or '1')
