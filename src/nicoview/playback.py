from pathlib import Path
import sys

def asset_path(name):
    return Path(__file__).resolve().parent/'assets'/name

def build_player_args(player_path, pipe, script, svp_enabled=False):
    # Audio timing can leave OSD changes waiting for the next video frame (e.g.
    # 30Hz). Display sync repeats video frames at the monitor cadence, allowing
    # fresh comment positions to be composited between source frames.
    extra=(['--load-scripts=no','--hwdec=auto-copy','--hwdec-codecs=all',
            '--hr-seek-framedrop=no','--resume-playback=no'] if svp_enabled else [])
    return [str(player_path),'--no-config','--idle=yes','--force-window=yes',
            '--video-sync=display-resample',
            '--keep-open=no','--terminal=no','--input-terminal=no',
            '--input-ipc-server='+pipe,'--script='+str(script),
            '--title=ニコ生ビューア','--cache=yes','--cache-secs=3','--demuxer-max-bytes=32MiB']+extra+['-']

def pump(source, sink, stop):
    while not stop.is_set():
        chunk=source.read(64*1024)
        if not chunk: break
        sink.write(chunk)
        sink.flush()
