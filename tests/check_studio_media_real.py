"""Real, offline FFmpeg -> record-only -> sidecar/local mpv seek and pause check."""
import json
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from nicoview.comments import Comment
from nicoview.worker import Worker
from nicoview.timeline import Timeline

root=Path(__file__).resolve().parents[1]
player=root.parent/'player/mpv.exe'
ffmpeg=root.parent/'ffmpeg/ffmpeg.exe'
real_popen=subprocess.Popen

def wait_for(predicate,timeout=8):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.05)
    raise AssertionError('Timed out waiting for real media state')

with tempfile.TemporaryDirectory(dir=root,prefix='media-roundtrip-') as tmp:
    folder=Path(tmp);source=folder/'source.ts'
    subprocess.run([str(ffmpeg),'-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=size=320x180:rate=30','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','12','-c:v','libx264','-preset','ultrafast','-c:a','aac','-f','mpegts',str(source)],check=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    events=[];record=Worker(events.append)
    class Source:
        def open(self):
            record.recording.add_comment(Comment('one','offline comment',0,position=2),2)
            return source.open('rb')
    from streamlink.stream.ffmpegmux import MuxedStream
    Source.shortname=lambda self:'offline-file'
    muxed=MuxedStream(record.auth.session,Source())
    plugin=SimpleNamespace(title='Offline roundtrip',author='Test author',id='lv1',program={},posting_allowed=False,is_timeshift=True,streams=lambda:{'best':muxed})
    with patch('nicoview.worker.stream_plugin',return_value=plugin),patch.object(record,'_launch',side_effect=AssertionError('record-only started mpv')):
        record.handle({'type':'start','request_id':'record','mode':'record','url':'https://live.nicovideo.jp/watch/lv1','credentials':None,'player_path':'','ffmpeg_path':str(ffmpeg),'settings':{'recording_dir':str(folder/'records')}})
        record.thread.join(10)
    assert not record.thread.is_alive(),events
    finished=next(e for e in events if e['type']=='completed')
    assert finished['ok'],events
    video=Path(finished['path'])
    subprocess.run([str(ffmpeg),'-hide_banner','-loglevel','error','-i',str(video),'-f','null','-'],check=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert video.read_bytes()[0]==0x47
    assert sum(e['type']=='job_ready' for e in events)==1
    timeline=Timeline.from_sidecar(video);assert len(timeline.items)==1
    record.close()
    playback_events=[];worker=Worker(playback_events.append)
    def hidden_player(args,*a,**kw):
        args=args[:-1]+['--vo=null','--ao=null']+args[-1:]
        return real_popen(args,*a,**kw)
    try:
        with patch('nicoview.worker.subprocess.Popen',side_effect=hidden_player):
            worker.handle({'type':'start','request_id':'local','local_path':str(video),'player_path':str(player),'settings':{'fps':77},'start_time':1})
            wait_for(lambda:worker.ipc is not None and worker.position>1.2)
            worker.control('pause',True)
            wait_for(lambda:worker.ipc.snapshot().get('pause') is True)
            position=worker.position;time.sleep(.4)
            assert abs(worker.position-position)<.15,(position,worker.position)
            worker.control('seek',7)
            wait_for(lambda:worker.position>6.8)
            worker.control('pause',False)
            wait_for(lambda:worker.position>7.2)
            assert worker.seekable and worker.timeline.items[0].text=='offline comment'
            worker.control('volume',35);worker.control('mute',True)
            wait_for(lambda:worker.ipc.snapshot().get('volume')==35 and worker.ipc.snapshot().get('mute') is True)
            worker.handle({'type':'stop','request_id':'local'});worker.thread.join(8)
        assert not worker.thread.is_alive(),playback_events
        assert not any(e['type']=='error' for e in playback_events),playback_events
        print(json.dumps({'record_only_no_player':True,'video_bytes':video.stat().st_size,'sidecar_comments':len(timeline.items),'local_seek_pause_volume_mute':True,'metadata_state':json.loads(video.with_suffix('.metadata.json').read_text(encoding='utf-8'))['state']}))
    finally:worker.close()
    # Cross the actual worker subprocess/Controller protocol boundary, including
    # readiness and subsequent position events which an unknown event truncated.
    from nicoview.controller import Controller
    controller=Controller();received=[]
    try:
        controller.start_job('',str(player),None,local_path=str(video))
        def delivered():
            received.extend(controller.poll_events())
            return any(e['type']=='job_ready' for e in received) and sum(e['type']=='position' for e in received)>=3
        wait_for(delivered,12)
        assert sum(e['type']=='job_ready' for e in received)==1,received
        assert not any(e['type']=='error' for e in received),received
        def stopped():
            received.extend(controller.poll_events())
            return not controller.busy
        wait_for(stopped,16)
        assert next(e['ok'] for e in received if e['type']=='completed'),received
        assert not any(e['type']=='error' for e in received),received
        print('real Controller: one job_ready followed by continuing position updates')
    finally:controller.close()
