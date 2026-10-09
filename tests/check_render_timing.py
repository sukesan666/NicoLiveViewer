"""Real video-output timing regression. Does not measure physical monitor frames.

Run explicitly with PYTHONPATH=src. Optional argument: packaged app directory.
The diagnostic uses generated 30fps video, never a live account or stream.
"""
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tempfile

from nicoview.playback import asset_path, build_player_args

root = Path(__file__).resolve().parents[1]
package = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
player = package/'player/mpv.exe' if package else root.parent/'player/mpv.exe'
script = package/'_internal/nicoview/assets/comments.lua' if package else asset_path('comments.lua')

with tempfile.TemporaryDirectory(dir=root.parent, prefix='render-timing-') as folder:
    folder = Path(folder)
    measured_script = folder/'comments.lua'
    code = script.read_text(encoding='utf-8')
    code = 'local distinct_updates = 0\nlocal last_ass = ""\n' + code.replace(
        'overlay:update()', '''overlay:update()
    if overlay.data ~= last_ass then distinct_updates = distinct_updates + 1 end
    last_ass = overlay.data''')
    code += """
mp.add_timeout(5, function()
    mp.msg.info('COMMENT_UPDATES '..distinct_updates)
end)
"""
    measured_script.write_text(code, encoding='utf-8')
    probe = folder/'probe.lua'
    probe.write_text("""
local mp = require 'mp'
local utils = require 'mp.utils'
mp.add_timeout(1, function()
    mp.commandv('script-message', 'nico-comment', 'コメント描画テスト ABC 123')
end)
mp.add_timeout(5, function()
    mp.msg.info('TIMING_RESULT '..utils.format_json({
        synced=mp.get_property_native('display-sync-active', false),
        display_hz=mp.get_property_number('display-fps', 0),
        estimated_hz=mp.get_property_number('estimated-display-fps', 0)
        ,avsync=mp.get_property_number('avsync', 999)
        ,audio=mp.get_property_native('audio-params') ~= nil
    }))
end)
""", encoding='utf-8')
    results = {}
    for mode in ('baseline', 'current'):
        log = folder/(mode+'.log')
        stats = folder/(mode+'.stats')
        args = build_player_args(player, str(folder/(mode+'.pipe')), measured_script)[:-1]
        args += ['--idle=no', '--terminal=yes', '--osc=no', '--geometry=640x360',
                 '--length=6', '--script='+str(probe), '--log-file='+str(log),
                 '--dump-stats='+str(stats)]
        if mode == 'baseline':
            args += ['--video-sync=audio']
        args += ['av://lavfi:testsrc2=size=640x360:rate=30[out0];anullsrc=r=48000:cl=stereo[out1]']
        proc = subprocess.run(args, capture_output=True, timeout=20,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert proc.returncode == 0, proc.stderr.decode(errors='replace')
        match = re.search(r'TIMING_RESULT (\{[^\n]+\})', log.read_text(encoding='utf-8'))
        assert match, 'No timing result'
        result = json.loads(match.group(1))
        updates = re.search(r'COMMENT_UPDATES (\d+)', log.read_text(encoding='utf-8'))
        assert updates and int(updates.group(1)) >= 200, 'Comment positions did not update'
        result['distinct_comment_positions'] = int(updates.group(1))
        times = [int(line.split()[0])/1e9 for line in stats.read_text().splitlines()
                 if 'end video-flip' in line]
        times = [t for t in times if 2 <= t <= 5]
        assert len(times) > 20, 'Too few output samples'
        intervals = sorted((b-a)*1000 for a,b in zip(times,times[1:]))
        result.update(output_calls_hz=(len(times)-1)/(times[-1]-times[0]),
                      median_ms=statistics.median(intervals),
                      p95_ms=intervals[int((len(intervals)-1)*.95)])
        results[mode] = result
    print(json.dumps(results, indent=2), flush=True)
    assert results['current']['synced'], 'Video playback is not display-synchronized'
    assert results['current']['audio'], 'No audio decoded'
    assert abs(results['current']['avsync']) < .1, 'Audio/video drift over 100ms'
    # This environment must expose at least a 60Hz output cadence to run this check.
    assert results['current']['output_calls_hz'] >= 50, 'Display output below 50Hz'
    assert results['current']['p95_ms'] < 25, 'Unstable display output intervals'
