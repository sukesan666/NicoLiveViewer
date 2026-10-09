"""Explicit installed-SVP integration test, not a guarantee of interpolation.

Uses an installed mpv and the official mpvpipe connection. Reports actual filter
presence/FPS separately, because the running user's SVP Manager must attach.
"""
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time

from nicoview import svp
from nicoview.playback import asset_path, build_player_args
from nicoview.player_ipc import PlayerIPC

root=Path(__file__).resolve().parents[1]
svp.check_pipe_available()
player=svp.player_path('')
with tempfile.TemporaryDirectory(dir=root.parent,prefix='svp-check-') as d:
    d=Path(d); probe=d/'probe.lua'; log=d/'mpv.log'
    probe.write_text("""
local mp=require 'mp'
local utils=require 'mp.utils'
mp.add_timeout(7,function()
    local active=false
    for _,f in ipairs(mp.get_property_native('vf',{})) do
        if f.name=='vapoursynth' then active=true end
    end
    mp.msg.info('SVP_RESULT '..utils.format_json({
        vapoursynth_filter=active,
        input_fps=mp.get_property_number('container-fps',0),
        filtered_fps=mp.get_property_number('estimated-vf-fps',0)
    }))
end)
mp.add_timeout(9,function() mp.commandv('quit') end)
""",encoding='utf-8')
    args=build_player_args(player,svp.PIPE,asset_path('comments.lua'),True)[:-1]
    args+=['--geometry=640x360','--loop-file=inf','--script='+str(probe),
           '--log-file='+str(log),str(asset_path('preview.mp4'))]
    p=subprocess.Popen(args,env=svp.player_environment(),stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    ipc=PlayerIPC(svp.PIPE)
    try:
        ipc.connect();svp.verify_server(ipc,p.pid)
        try:
            svp.verify_server(ipc,p.pid+1)
        except ValueError:pass
        else:raise AssertionError('Wrong server PID was accepted')
        try:svp.check_pipe_available()
        except ValueError:pass
        else:raise AssertionError('Busy SVP pipe was accepted')
        time.sleep(1)
        ipc.send(['script-message','nico-comment','SVP連携・コメント表示テスト'])
        p.wait(timeout=15)
        assert p.returncode==0
        match=re.search(r'SVP_RESULT (\{[^\n]+\})',log.read_text(encoding='utf-8'))
        assert match,'No SVP diagnostic result'
        result=json.loads(match.group(1));result['ipc_and_server_guard']='PASS'
        print(json.dumps(result),flush=True)
    finally:
        ipc.close()
        if p.poll() is None:p.terminate();p.wait(timeout=5)
