import tkinter as tk
import unittest
from unittest.mock import MagicMock
from nicoview.post_window import PostWindow

class PostWindowTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
        self.controller=MagicMock();self.controller.post_comment.return_value='p1'
        self.window=PostWindow(self.root,self.controller,'r1','https://live.nicovideo.jp/watch/lv123')
        self.window.window.withdraw();self.window.text.insert('1.0','テスト本文')
    def tearDown(self):self.root.destroy()
    def result(self,ok,ident='p1'):
        self.window.receive({'request_id':'r1','post_id':ident,'ok':ok,'text':'結果'})
    def test_success_clears_only_after_matching_ack(self):
        self.window.submit();self.window.submit()
        self.controller.post_comment.assert_called_once_with('r1','テスト本文',True)
        self.result(True,'old');self.assertEqual(self.window.text.get('1.0','end-1c'),'テスト本文')
        self.result(True);self.assertEqual(self.window.text.get('1.0','end-1c'),'')
    def test_failure_keeps_text_and_stop_prevents_send(self):
        self.window.submit();self.result(False)
        self.assertEqual(self.window.text.get('1.0','end-1c'),'テスト本文')
        self.window.stop();self.window.submit()
        self.assertEqual(self.controller.post_comment.call_count,1)

    def test_anonymous_defaults_on_and_can_be_disabled(self):
        self.assertTrue(self.window.anonymous.get())
        self.window.anonymous.set(False)
        self.window.submit()
        self.controller.post_comment.assert_called_once_with('r1','テスト本文',False)
