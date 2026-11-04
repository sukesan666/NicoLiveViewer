import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from nicoview.comments import CommentReceiver
from nicoview.player_ipc import PlayerIPC
from nicoview.worker import Worker

class MediaRegressions(unittest.TestCase):
    def test_player_quit_is_not_a_connection_failure(self):
        for reason in ('quit','stop',None):
            events=[];worker=Worker(events.append);worker.seekable=False
            worker.player=MagicMock();worker.player.poll.return_value=0 if reason is None else None;worker.player.returncode=0
            worker.ipc=PlayerIPC('unused')
            if reason:worker.ipc._consume((json.dumps({'event':'end-file','reason':reason})+'\n').encode())
            worker._watch_player()
            self.assertTrue(any(e['type']=='player_closed' for e in events))
            self.assertFalse(any(e['type']=='error' for e in events))
            worker.player=None;worker.ipc=None;worker.close()

    def test_archive_backpressure_and_drain_all_segments(self):
        receiver=CommentReceiver();receiver.archive=True;receiver.uri='view'
        received=[];running=0;peak=0;lock=threading.Lock()
        def segment(uri):
            nonlocal running,peak
            with lock:running+=1;peak=max(peak,running)
            time.sleep(.035)
            with lock:received.append(uri);running-=1
        with patch.object(receiver,'_fetch',return_value=iter(range(20))),patch('nicoview.comments.decode_entry',side_effect=lambda n:('segment',str(n))),patch.object(receiver,'_segment',side_effect=segment):
            receiver.thread=threading.Thread(target=receiver._views);receiver.thread.start()
            self.assertTrue(receiver.wait_complete())
        self.assertEqual(sorted(received),sorted(map(str,range(20))))
        self.assertLessEqual(peak,4);self.assertFalse(receiver.workers)

    def test_recording_windows_device_names(self):
        from nicoview.recording import safe_name
        for name in ('CON','con.txt','PRN','AUX','NUL','COM1','LPT9.log'):
            self.assertTrue(safe_name(name).startswith('_'))
        self.assertLessEqual(len(safe_name('x'*200)),60)

    def test_player_end_reasons(self):
        for seekable,reason,successful in ((False,'eof',False),(False,'error',False),(True,'eof',True),(True,'error',False)):
            events=[];worker=Worker(events.append);worker.seekable=seekable
            worker.player=MagicMock();worker.player.poll.return_value=None
            worker.ipc=PlayerIPC('unused')
            worker.ipc._consume((json.dumps({'event':'end-file','reason':reason,'error':'failure'})+'\n').encode())
            worker._watch_player()
            self.assertEqual(worker.player_completed,successful)
            self.assertEqual(any(e['type']=='error' for e in events),not successful)
            worker.player=None;worker.ipc=None;worker.close()

    def test_both_natural_eof_keeps_recording_and_archive_drain_alive(self):
        import io
        import tempfile
        from nicoview.comments import Comment
        events=[];worker=Worker(events.append);worker.seekable=True
        archive_finished=threading.Event()
        class Source:
            def open(self):
                # Playback EOF precedes the completion of this recording source.
                worker.ipc._consume(b'{"event":"end-file","reason":"eof"}\n')
                time.sleep(.2)
                self.assert_running=not worker.stop.is_set()
                return io.BytesIO(b'video')
        source=Source()
        def launch(*args):
            worker.player=MagicMock();worker.player.poll.return_value=None
            worker.ipc=PlayerIPC('unused')
            threading.Thread(target=worker._watch_player,daemon=True).start()
        plugin=SimpleNamespace(title='offline',author='test',id='lv1',program={},posting_allowed=False,is_timeshift=True,streams=lambda:{'best':source})
        receiver=MagicMock();receiver.thread=threading.current_thread()
        def drain(stop):
            time.sleep(.05)
            self.assertFalse(stop.is_set())
            self.assertFalse(worker.recording.closed)
            worker.recording.add_comment(Comment('late','archive tail',0),2)
            archive_finished.set();return True
        receiver.wait_complete.side_effect=drain;worker.receiver=receiver
        with tempfile.TemporaryDirectory() as folder,patch('nicoview.worker.stream_plugin',return_value=plugin),patch('nicoview.worker.MediaProxy'),patch.object(worker,'_launch',side_effect=launch):
            worker.settings['recording_dir']=folder
            worker.run({'mode':'both','url':'https://live.nicovideo.jp/watch/lv1','player_path':__file__,'credentials':None})
            self.assertTrue(source.assert_running);self.assertTrue(archive_finished.is_set())
            self.assertEqual(worker.recording.metadata['state'],'complete')
            self.assertIn('archive tail',worker.recording.comment_path.read_text(encoding='utf-8'))
            self.assertTrue(next(e['ok'] for e in events if e['type']=='completed'))
        worker.close()

    def test_manual_stop_does_not_invent_player_failure(self):
        events=[];worker=Worker(events.append);worker.stop.set();worker.player=MagicMock()
        worker._watch_player();self.assertEqual(events,[])
        worker.player=None;worker.close()

    def test_stalled_playback_triggers_reconnect_error_unless_paused(self):
        events=[];worker=Worker(events.append);worker.seekable=False;worker.stall_timeout=0.15
        worker.proxy=object();worker.player=MagicMock();worker.player.poll.return_value=None
        worker.ipc=PlayerIPC('unused')
        worker.ipc._consume(b'{"event":"property-change","name":"pause","data":false}\n')
        worker.ipc._consume(b'{"event":"property-change","name":"time-pos","data":10.0}\n')
        worker._watch_player()
        self.assertTrue(worker.stop.is_set())
        self.assertTrue(any(e['type']=='error' and '再生が停止しました' in e.get('text','') for e in events),events)
        worker.player=None;worker.ipc=None;worker.close()

        events=[];worker=Worker(events.append);worker.seekable=False;worker.stall_timeout=0.15
        worker.proxy=object();worker.player=MagicMock();worker.player.poll.return_value=None
        worker.ipc=PlayerIPC('unused')
        worker.ipc._consume(b'{"event":"property-change","name":"pause","data":true}\n')
        worker.ipc._consume(b'{"event":"property-change","name":"time-pos","data":10.0}\n')
        threading.Thread(target=lambda:(time.sleep(0.25),worker.stop.set()),daemon=True).start()
        worker._watch_player()
        self.assertEqual(events,[])
        worker.player=None;worker.ipc=None;worker.close()

    def test_interrupted_both_remains_unsuccessful_after_player_stop(self):
        import io
        import tempfile
        events=[];worker=Worker(events.append)
        class Source:
            def open(self):return io.BytesIO(b'video')
        plugin=SimpleNamespace(title='offline',author='test',id='lv1',program={},posting_allowed=False,is_timeshift=False,streams=lambda:{'best':Source()})
        def launch(*args):
            # Playback stops only after recording reached an unexpected live EOF.
            threading.Thread(target=lambda:(time.sleep(.2),worker.stop.set()),daemon=True).start()
        with tempfile.TemporaryDirectory() as folder,patch('nicoview.worker.stream_plugin',return_value=plugin),patch('nicoview.worker.MediaProxy'),patch.object(worker,'_launch',side_effect=launch):
            worker.settings['recording_dir']=folder
            worker.run({'mode':'both','url':'https://live.nicovideo.jp/watch/lv1','player_path':__file__,'credentials':None})
            self.assertEqual(worker.recording.metadata['state'],'interrupted')
            self.assertFalse(next(e['ok'] for e in events if e['type']=='completed'))
            self.assertEqual(sum(e['type']=='job_ready' for e in events),1)
        worker.close()
