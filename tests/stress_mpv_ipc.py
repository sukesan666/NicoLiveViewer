"""Offline Windows/mpv IPC stress check. Run from the repository root.

    python tests/stress_mpv_ipc.py --output build/mpv-stress.json

Uses the bundled preview and local FFmpeg to create a longer test clip, then
runs the real comments.lua in a minimized mpv window. No Nico connection is used.
The outer process bounds the entire test and terminates only its own process tree
if a broken pipe implementation hangs.
"""
import argparse
import ctypes
from ctypes import wintypes
import gc
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from nicoview.comments import Comment
from nicoview.player_ipc import PlayerIPC
from nicoview.worker import Worker


class MeasuredIPC(PlayerIPC):
    def __init__(self, path):
        super().__init__(path)
        self.metrics_lock = threading.Lock()
        self.metrics_buffer = b''
        self.reply_count = 0
        self.comment_count = 0
        self.progress_times = []
        self.last_position = None

    def _consume(self, data):
        with self.metrics_lock:
            self.metrics_buffer += data
            while b'\n' in self.metrics_buffer:
                line, self.metrics_buffer = self.metrics_buffer.split(b'\n', 1)
                message = json.loads(line)
                if 'error' in message:
                    self.reply_count += 1
                if message.get('event') == 'property-change' and message.get('name') == 'time-pos':
                    position = message.get('data')
                    if isinstance(position, (int, float)) and position != self.last_position:
                        self.progress_times.append(time.monotonic())
                        self.last_position = position
        super()._consume(data)

    def send(self, command):
        super().send(command)
        if command[:2] == ['script-message', 'nico-comment']:
            with self.metrics_lock:
                self.comment_count += 1

    def counts(self):
        with self.metrics_lock:
            return self.reply_count, self.comment_count, list(self.progress_times)


def wait_until(predicate, player, description, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if player.poll() is not None:
            raise AssertionError(f'mpv exited during {description}, code {player.returncode}')
        time.sleep(0.01)
    raise TimeoutError(description)


def run_check(args):
    build = ROOT / 'build'
    build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mpv-ipc-stress-', dir=build) as folder:
        directory = Path(folder)
        clip = directory / 'preview-long.mp4'
        subprocess.run(
            [str(args.ffmpeg), '-nostdin', '-hide_banner', '-loglevel', 'error',
             '-stream_loop', '-1', '-i', str(ROOT / 'src/nicoview/assets/preview.mp4'),
             '-t', str(args.overlay_seconds + 75), '-c', 'copy', str(clip)],
            check=True, timeout=20, capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        # Count calls to the real handler without replacing rendering or timing.
        bootstrap = directory / 'comments-stress.lua'
        bootstrap.write_text(
            "local mp = require 'mp'\n"
            "local original_register = mp.register_script_message\n"
            "local original_overlay = mp.create_osd_overlay\n"
            "local handled = 0\n"
            "local overlay_updates = 0\n"
            "mp.create_osd_overlay = function(format)\n"
            "  local overlay = original_overlay(format)\n"
            "  return setmetatable({}, {\n"
            "    __index = function(_, key)\n"
            "      if key == 'update' then return function()\n"
            "        if overlay.data and #overlay.data > 0 then overlay_updates = overlay_updates + 1 end\n"
            "        return overlay:update()\n"
            "      end end\n"
            "      if key == 'remove' then return function() return overlay:remove() end end\n"
            "      return overlay[key]\n"
            "    end,\n"
            "    __newindex = function(_, key, value) overlay[key] = value end,\n"
            "  })\n"
            "end\n"
            "mp.register_script_message = function(name, handler)\n"
            "  if name == 'nico-comment' then\n"
            "    original_register(name, function(...)\n"
            "      handler(...)\n"
            "      handled = handled + 1\n"
            "      if handled % 100 == 0 then\n"
            "        mp.set_property_number('user-data/nicoview-stress-count', handled)\n"
            "      end\n"
            "    end)\n"
            "  else original_register(name, handler) end\n"
            "end\n"
            f"dofile({json.dumps(str(ROOT / 'src/nicoview/assets/comments.lua'))})\n"
            "original_register('nico-stress-report', function()\n"
            "  mp.set_property_number('user-data/nicoview-stress-count', handled)\n"
            "  mp.set_property_number('user-data/nicoview-stress-overlays', overlay_updates)\n"
            "end)\n"
            "mp.set_property_bool('user-data/nicoview-stress-ready', true)\n",
            encoding='utf-8',
        )
        pipe = r'\\.\pipe\nicoview-stress-' + uuid.uuid4().hex
        log_path = directory / 'mpv.log'
        log = log_path.open('w', encoding='utf-8')
        player = subprocess.Popen(
            [str(args.mpv), '--no-config', '--load-scripts=no', '--idle=yes',
             '--vo=gpu-next', '--gpu-api=d3d11', '--gpu-context=d3d11', '--ao=null',
             '--force-window=yes', '--window-minimized=yes', '--geometry=1280x720',
             '--terminal=yes', '--input-terminal=no', '--no-resume-playback',
             '--input-ipc-server=' + pipe, '--script=' + str(bootstrap), str(clip)],
            stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        ipc = MeasuredIPC(pipe)
        worker = None
        overlay_thread = watcher_thread = None
        events = []
        try:
            ipc.connect()
            ipc.observe()
            for index, name in enumerate(('osd-width', 'osd-height',
                                           'user-data/nicoview-stress-ready',
                                           'user-data/nicoview-stress-count',
                                           'user-data/nicoview-stress-overlays'), 100):
                ipc.send(['observe_property', index, name])
            wait_until(lambda: ipc.snapshot().get('user-data/nicoview-stress-ready') is True,
                       player, 'comments.lua registration')
            wait_until(lambda: (ipc.snapshot().get('osd-width') or 0) > 0
                       and (ipc.snapshot().get('osd-height') or 0) > 0,
                       player, 'native OSD dimensions')
            wait_until(lambda: len(ipc.counts()[2]) >= 2, player, 'initial video progress')
            native_size = (ipc.snapshot()['osd-width'], ipc.snapshot()['osd-height'])
            ipc.send(['script-message', 'nico-config', json.dumps({'enabled': True, 'fps': 120})])
            replies_before, _, _ = ipc.counts()
            burst_started = time.monotonic()
            for number in range(args.burst):
                ipc.send(['script-message', 'nico-comment', f'burst {number} 日本語コメント', 'FFFFFF'])
            ipc.send(['script-message', 'nico-stress-report'])
            wait_until(lambda: (ipc.snapshot().get('user-data/nicoview-stress-count') or 0) >= args.burst,
                       player, 'all burst handlers', timeout=15)
            wait_until(lambda: ipc.counts()[0] - replies_before >= args.burst,
                       player, 'burst acknowledgments', timeout=15)
            burst_elapsed = time.monotonic() - burst_started

            worker = Worker(events.append)
            worker.current = 'offline-stress'
            worker.ipc = ipc
            worker.player = player
            worker.proxy = object()
            worker.settings['enabled'] = True
            overlay_thread = threading.Thread(target=worker._overlay, daemon=True)
            watcher_thread = threading.Thread(target=worker._watch_player, daemon=True)
            before_overlay = ipc.counts()[1]
            overlay_started = time.monotonic()
            overlay_thread.start()
            watcher_thread.start()
            offered = 0
            next_batch = overlay_started
            while time.monotonic() - overlay_started < args.overlay_seconds:
                if worker.stop.is_set() or player.poll() is not None:
                    raise AssertionError(f'Playback stopped under load: {events[-8:]}')
                now = time.monotonic()
                for offset in range(500):
                    worker.comments.add(Comment(str(offered + offset), f'overlay {offered + offset}', now))
                offered += 500
                next_batch += 0.033
                time.sleep(max(0, next_batch - time.monotonic()))
            worker.stop.set()
            overlay_thread.join(3)
            watcher_thread.join(3)
            if overlay_thread.is_alive() or watcher_thread.is_alive():
                raise AssertionError('Worker threads did not stop after the load')
            if any(event.get('type') in ('error', 'comment_status') for event in events):
                raise AssertionError(f'Worker reported errors: {events[-8:]}')
            overlay_sent = ipc.counts()[1] - before_overlay
            if overlay_sent < 5000:
                raise AssertionError(f'Too few comments traversed Worker._overlay: {overlay_sent}')
            expected_count = ipc.counts()[1]
            ipc.send(['script-message', 'nico-stress-report'])
            wait_until(lambda: (ipc.snapshot().get('user-data/nicoview-stress-count') or 0) >= expected_count,
                       player, 'all Worker overlay handlers')
            wait_until(lambda: (ipc.snapshot().get('user-data/nicoview-stress-overlays') or 0) > 0,
                       player, 'actual ASS overlay updates')

            control_times = {}
            control_started = time.monotonic()
            ipc.send(['set_property', 'pause', True])
            wait_until(lambda: ipc.snapshot().get('pause') is True, player, 'pause control')
            control_times['pause'] = round(time.monotonic() - control_started, 3)
            control_started = time.monotonic()
            ipc.send(['set_property', 'volume', 37])
            wait_until(lambda: ipc.snapshot().get('volume') == 37, player, 'volume control')
            control_times['volume'] = round(time.monotonic() - control_started, 3)
            target = float(ipc.snapshot()['time-pos']) + 3
            control_started = time.monotonic()
            ipc.send(['seek', target, 'absolute+exact'])
            wait_until(lambda: abs(float(ipc.snapshot().get('time-pos') or 0) - target) < 0.2,
                       player, 'seek control')
            control_times['seek'] = round(time.monotonic() - control_started, 3)
            control_started = time.monotonic()
            ipc.send(['set_property', 'pause', False])
            wait_until(lambda: ipc.snapshot().get('pause') is False, player, 'resume control')
            control_times['resume'] = round(time.monotonic() - control_started, 3)
            _, sent, progress = ipc.counts()
            gaps = [later - earlier for earlier, later in zip(progress, progress[1:])]
            max_gap = max(gaps, default=0)
            if max_gap >= worker.stall_timeout:
                raise AssertionError(f'Video progress gap exceeded watchdog limit: {max_gap:.3f}s')
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.GetProcessHandleCount.restype = wintypes.BOOL
            def handles():
                count = wintypes.DWORD()
                if not kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(count)):
                    raise ctypes.WinError(ctypes.get_last_error())
                return count.value
            def connect_and_close():
                extra = PlayerIPC(pipe)
                extra.connect()
                try:
                    extra.send(['get_property', 'time-pos'])
                finally:
                    extra.close()
            for _ in range(5):
                connect_and_close()
            time.sleep(0.05)
            gc.collect()
            drainers_before = {thread for thread in threading.enumerate() if '_drain' in thread.name}
            handles_before = handles()
            for _ in range(50):
                connect_and_close()
            wait_until(lambda: not ({thread for thread in threading.enumerate() if '_drain' in thread.name}
                                   - drainers_before), player, 'connection drain thread cleanup')
            gc.collect()
            handles_after = handles()
            if handles_after > handles_before:
                raise AssertionError(f'Handle leak after 50 connections: {handles_before} -> {handles_after}')
            ipc.send(['quit'])
            player.wait(5)
            if player.returncode != 0:
                raise AssertionError(f'mpv did not exit cleanly: {player.returncode}')
            close_started = time.monotonic()
            ipc.close()
            close_elapsed = time.monotonic() - close_started
            if close_elapsed > 2:
                raise AssertionError(f'IPC close took {close_elapsed:.3f}s')
            result = {
                'passed': True, 'renderer': 'gpu-next/d3d11, minimized real window',
                'osd_size': native_size, 'burst_comments': args.burst,
                'burst_seconds': round(burst_elapsed, 3),
                'overlay_seconds': args.overlay_seconds, 'overlay_offered': offered,
                'overlay_sent': overlay_sent, 'total_comments_handled': sent,
                'ass_overlay_updates': ipc.snapshot().get('user-data/nicoview-stress-overlays'),
                'progress_updates': len(progress), 'max_progress_gap_seconds': round(max_gap, 3),
                'controls': ['pause', 'volume', 'seek', 'resume', 'quit'],
                'control_seconds': control_times,
                'mpv_exit_code': player.returncode, 'ipc_close_seconds': round(close_elapsed, 3),
                'reconnect_cycles': 50, 'handles_before': handles_before, 'handles_after': handles_after,
            }
        except Exception:
            log.flush()
            print(log_path.read_text(encoding='utf-8', errors='replace')[-6000:], file=sys.stderr)
            raise
        finally:
            if worker:
                worker.stop.set()
            if player.poll() is None:
                player.kill()
                player.wait(5)
            ipc.close()
            if overlay_thread:
                overlay_thread.join(2)
            if watcher_thread:
                watcher_thread.join(2)
            if worker:
                worker.auth.close()
            log.close()
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mpv', type=Path, default=ROOT / 'mpv.exe')
    parser.add_argument('--ffmpeg', type=Path, default=ROOT / 'ffmpeg.exe')
    parser.add_argument('--burst', type=int, default=5000)
    parser.add_argument('--overlay-seconds', type=float, default=15)
    parser.add_argument('--timeout', type=float, default=90)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--run-child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('This regression targets Windows named pipes.')
    if args.burst < 5000 or args.overlay_seconds <= 12:
        parser.error('Use at least 5000 burst comments and more than 12 overlay seconds.')
    if args.run_child:
        result = run_check(args)
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + '\n', encoding='utf-8')
        print(text)
        return
    command = [sys.executable, '-B', str(Path(__file__).resolve()), *sys.argv[1:], '--run-child']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        output, _ = process.communicate(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        output, _ = process.communicate(timeout=5)
        print(output.decode('utf-8', errors='replace'))
        raise SystemExit('Timed out; stopped only the stress test process tree.')
    print(output.decode('utf-8', errors='replace'), end='')
    raise SystemExit(process.returncode)


if __name__ == '__main__':
    main()
