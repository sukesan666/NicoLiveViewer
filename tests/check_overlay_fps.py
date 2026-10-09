"""Measure real Lua overlay update cadence; explicit desktop integration test."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
source=Path(sys.argv[1]) if len(sys.argv)>1 else root/'src/nicoview/assets/comments.lua'
player=root.parent/'player/mpv.exe'
image=root/'src/nicoview/assets/preview.png'
with tempfile.TemporaryDirectory(dir=root.parent,prefix='fps-test-') as folder:
    script=Path(folder)/'measure.lua'
    code=source.read_text(encoding='utf-8')
    code='local measured_updates = {}\n'+code.replace(
        'overlay:update()', 'overlay:update(); table.insert(measured_updates, mp.get_time())')
    code+='''
mp.add_timeout(1, function()
    mp.commandv('script-message', 'nico-comment', '120fps 日本語コメント描画テスト')
end)
mp.add_timeout(6, function()
    local n=#measured_updates
    local elapsed=n>1 and measured_updates[n]-measured_updates[1] or 0
    mp.msg.info('FPS_RESULT '..utils.format_json({updates=n, elapsed=elapsed, fps=elapsed>0 and (n-1)/elapsed or 0}))
    mp.commandv('quit')
end)
'''
    script.write_text(code,encoding='utf-8')
    result=subprocess.run([str(player),'--no-config','--osc=no','--force-window=yes','--geometry=640x360',
        '--image-display-duration=inf','--script='+str(script),str(image)],
        capture_output=True,encoding='utf-8',errors='replace',timeout=15,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    match=re.search(r'FPS_RESULT (\{[^\n]+\})',result.stdout+result.stderr)
    assert result.returncode==0 and match,result.stdout+result.stderr
    stats=json.loads(match.group(1));print(json.dumps(stats),flush=True)
    assert stats['fps']>=100, f"120fps target not reached: {stats['fps']:.1f} updates/sec"
