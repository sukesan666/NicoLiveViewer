import threading
import unittest
from unittest.mock import MagicMock
from nicoview.auth import AuthSession, CodePrompt, Cancelled


class AuthTests(unittest.TestCase):
    def test_memory_only(self):
        auth = AuthSession.create()
        self.assertTrue(auth.session.get_option('no-plugin-cache'))
        auth.session.http.cookies.set('user_session','secret', domain='nicovideo.jp')
        auth.clear()
        self.assertEqual(len(auth.session.http.cookies), 0)

    def test_login_result_and_password_cleared(self):
        for success in (True,False):
            auth = AuthSession.create()
            plugin = MagicMock()
            plugin.options = {}
            def login():
                if success: auth.session.http.cookies.set('user_session','secret',domain='nicovideo.jp')
            plugin.niconico_web_login.side_effect = login
            self.assertEqual(auth.login('mail','password',lambda:'123456',plugin=plugin),success)
            self.assertIsNone(plugin.options.get('password'))

    def test_otp_wait_cancel_timeout(self):
        stop=threading.Event(); events=[]
        prompt=CodePrompt(lambda:events.append('ask'), stop, timeout=.04)
        with self.assertRaises(Cancelled): prompt.ask()
        stop.clear(); prompt=CodePrompt(lambda:prompt.submit('123456'),stop)
        self.assertEqual(prompt.ask(), '123456')
        stop.set()
        with self.assertRaises(Cancelled): prompt.ask()

    def test_auth_exception_clears_password(self):
        auth=AuthSession.create(); plugin=MagicMock(); plugin.options={}
        plugin.niconico_web_login.side_effect=RuntimeError('sensitive')
        with self.assertRaises(RuntimeError): auth.login('m','p',lambda:'',plugin=plugin)
        self.assertIsNone(plugin.options.get('password'))
