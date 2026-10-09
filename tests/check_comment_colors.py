"""Offline real-mpv check of generated ASS colors; no live account access."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='color-check-') as tmp:
    script=Path(tmp)/'colors.lua'
    code=(root/'src/nicoview/assets/comments.lua').read_text(encoding='utf-8')
    code="local seen={}\n"+code.replace('overlay:update()', "overlay:update(); for _,v in ipairs({'0000FF','FF0000','563412','FFFFFF','000000'}) do if overlay.data:find('1c&H'..v..'&',1,true) then seen[v]=true end end")
    code+="""
mp.add_timeout(1,function()
    for _,v in ipairs({'FF0000','0000FF','123456','bad-color','000000'}) do
        mp.commandv('script-message','nico-comment','Color '..v,v)
    end
end)
mp.add_timeout(3,function()
    mp.msg.info('COLOR_RESULT '..utils.format_json(seen))
    mp.commandv('quit')
end)
"""
    script.write_text(code,encoding='utf-8')
    r=subprocess.run([str(root.parent/'player/mpv.exe'),'--no-config','--osc=no',
        '--force-window=yes','--geometry=800x600','--image-display-duration=inf',
        '--script='+str(script),str(root/'src/nicoview/assets/preview.png')],
        capture_output=True,encoding='utf-8',errors='replace',timeout=15,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    match=re.search(r'COLOR_RESULT (\{[^\n]+\})',r.stdout+r.stderr)
    assert r.returncode==0 and match,r.stdout+r.stderr
    colors=json.loads(match.group(1))
    assert all(colors.get(v) for v in ['0000FF','FF0000','563412','FFFFFF','000000']),colors
    print('Real mpv ASS: red/blue/RGB/invalid fallback/black PASS')
