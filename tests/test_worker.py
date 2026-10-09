import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from nicoview.worker import Worker

class WorkerTests(unittest.TestCase):
    def test_posting_requires_ready_logged_in_live_session(self):
        events=[];worker=Worker(events.append);worker.current='r1'
        worker.handle({'type':'post_comment','request_id':'r1','post_id':'p1','text':'test'})
        self.assertEqual(events[-1]['type'],'post_result');self.assertFalse(events[-1]['ok'])
        count=len(events)
        worker.handle({'type':'post_comment','request_id':'old','post_id':'p2','text':'test'})
        self.assertEqual(len(events),count)
        worker.close()
    def test_anonymous_playback_reports_login_cleared(self):
        events=[];worker=Worker(events.append)
        worker.auth.session.http.cookies.set('user_session','token',domain='nicovideo.jp')
        with tempfile.NamedTemporaryFile() as f, patch('nicoview.worker.stream_plugin') as factory:
            factory.return_value.streams.return_value={}
            worker.handle({'type':'start','request_id':'anon','url':'https://live.nicovideo.jp/watch/lv1',
                           'player_path':f.name,'credentials':None})
            worker.thread.join(5)
        self.assertEqual(len(worker.auth.session.http.cookies),0)
        self.assertTrue(any(e['type']=='auth_status' for e in events))
        worker.close()

    def test_old_comment_callbacks_cannot_affect_next_program(self):
        from nicoview.comments import Comment
        events=[];worker=Worker(events.append);worker.current='old'
        with patch('nicoview.worker.CommentReceiver') as factory:
            receiver=MagicMock();factory.return_value=receiver
            worker._on_comments({'viewUri':'https://example'},'old')
            report=factory.call_args.args[0]
            deliver=receiver.start.call_args.args[2]
            worker.current='new';worker.receiver=None
            report('stale');deliver(Comment('old','old comment',0))
            self.assertEqual(events,[]);self.assertEqual(len(worker.comments.pending),0)
            factory.reset_mock()
            worker._on_comments({'viewUri':'https://example'},'old')
            factory.assert_not_called()
        worker.close()

    def test_missing_player_does_not_leak_credentials(self):
        events=[];worker=Worker(events.append)
        msg={'type':'start','request_id':'1','url':'https://live.nicovideo.jp/watch/lv1',
             'player_path':'does-not-exist','credentials':{'email':'private','password':'secret'}}
        worker.handle(msg); worker.thread.join(5)
        self.assertFalse(worker.thread.is_alive())
        self.assertTrue(any(e['type']=='error' for e in events))
        self.assertEqual(events[-1]['type'],'stopped')
        self.assertNotIn('secret',str(events));self.assertNotIn('credentials',msg)
        worker.close()

    def test_player_start_failure_is_sanitized(self):
        events=[];worker=Worker(events.append)
        with tempfile.NamedTemporaryFile() as f, patch('nicoview.worker.subprocess.Popen',side_effect=OSError('password=SECRET')):
            worker.handle({'type':'start','request_id':'a','player_path':f.name,'preview':True})
            worker.thread.join(5)
        self.assertNotIn('SECRET',str(events));self.assertEqual(events[-1]['type'],'stopped')
        worker.close()

    def test_received_color_reaches_player(self):
        from nicoview.comments import Comment
        worker=Worker(lambda e:None);worker.current='color-test'
        worker.ipc=MagicMock()
        worker.comments.add(Comment('c','red',time.monotonic(),'FF0000'))
        with patch.object(worker.stop,'wait',side_effect=[False,True]):
            worker._overlay()
        worker.ipc.send.assert_any_call(['script-message','nico-comment','red','FF0000'])
        worker.ipc=None;worker.close()
