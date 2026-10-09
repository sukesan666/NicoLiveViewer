import threading
import unittest
from nicoview.posting import CommentPoster, post_payload

class PostingTests(unittest.TestCase):
    def test_payload_escapes_and_targets_position(self):
        p=post_payload('a"\\日本語',True,100,102)
        self.assertEqual(p['data']['text'],'a"\\日本語')
        self.assertEqual(p['data']['vpos'],200)
        self.assertTrue(p['data']['isAnonymous'])
        self.assertFalse(post_payload('通常投稿',False,100,102)['data']['isAnonymous'])
    def test_invalid_text(self):
        for text in ('',' '*4,'a'*76,'a\x00b'):
            with self.assertRaises(ValueError):post_payload(text,True,100,102)
    def test_ack_required(self):
        sent=[];p=CommentPoster(sent.append,100)
        def send(payload):
            sent.append(payload)
            p.receive({'type':'postCommentResult','data':{'chat':{'content':'hello'}}})
        p.send=send
        self.assertTrue(p.post('hello',True,threading.Event(),timeout=.1)[0])
        self.assertEqual(len(sent),1)
    def test_timeout_disables_retry_to_avoid_late_ack(self):
        sent=[];p=CommentPoster(sent.append,100)
        self.assertFalse(p.post('hello',True,threading.Event(),timeout=.01)[0])
        p.receive({'type':'postCommentResult','data':{'chat':{'content':'hello'}}})
        self.assertFalse(p.post('hello',True,threading.Event(),timeout=.01)[0])
        self.assertEqual(len(sent),1)
    def test_cancel_never_sends(self):
        sent=[];p=CommentPoster(sent.append,100);stop=threading.Event();stop.set()
        self.assertFalse(p.post('hello',True,stop)[0]);self.assertEqual(sent,[])
