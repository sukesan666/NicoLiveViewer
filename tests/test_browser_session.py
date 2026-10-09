import json
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock
import threading
from nicoview.browser_login import BrowserLogin
from nicoview.sessions import SessionStore, clean_cookies
from nicoview.auth import AuthSession

def cookie(**changes):
    value=dict(name='user_session',value='test-session',domain='.nicovideo.jp',path='/',secure=True,expires=time.time()+3600)
    value.update(changes);return value

class BrowserSessionTests(unittest.TestCase):
    def test_only_live_niconico_session_cookies(self):
        self.assertEqual(len(clean_cookies([cookie(),cookie(name='tracking'),cookie(domain='evilnicovideo.jp'),cookie(expires=1)])),1)
        self.assertEqual(clean_cookies([cookie(domain='.nicovideo.jp.evil.test')]),[])
        self.assertEqual(clean_cookies([cookie(value='bad\r\nheader')]),[])
        self.assertEqual(clean_cookies([cookie(expires=0)]),[])
        self.assertEqual(clean_cookies([cookie(path='/private')]),[])
        self.assertEqual(len(clean_cookies([cookie(),cookie(domain='live.nicovideo.jp')])),1)

    def test_storage_encrypts_and_restores(self):
        with tempfile.TemporaryDirectory() as d, patch('nicoview.sessions.protect',side_effect=lambda b:b[::-1]),patch('nicoview.sessions.unprotect',side_effect=lambda b:b[::-1]):
            store=SessionStore(Path(d)/'session.dat');store.save([cookie()])
            self.assertNotIn(b'test-session',store.path.read_bytes())
            self.assertEqual(store.load()[0]['value'],'test-session')
            store.delete();self.assertIsNone(store.load())

    def test_crypto_failure_never_writes(self):
        with tempfile.TemporaryDirectory() as d,patch('nicoview.sessions.protect',side_effect=OSError):
            store=SessionStore(Path(d)/'session.dat')
            with self.assertRaises(OSError):store.save([cookie()])
            self.assertEqual(list(Path(d).iterdir()),[])

    def test_import_clears_old_session(self):
        auth=AuthSession.create()
        try:
            auth.session.http.cookies.set('old','old')
            self.assertTrue(auth.restore([cookie()]))
            self.assertEqual(len(auth.session.http.cookies),1)
            self.assertFalse(auth.restore([cookie(expires=1)]))
            self.assertEqual(len(auth.session.http.cookies),0)
        finally:auth.close()

    def test_session_cookie_without_expiry_survives_roundtrip(self):
        with tempfile.TemporaryDirectory() as d,patch('nicoview.sessions.protect',side_effect=lambda b:b[::-1]),patch('nicoview.sessions.unprotect',side_effect=lambda b:b[::-1]):
            store=SessionStore(Path(d)/'session.dat');store.save([cookie(expires=None)])
            self.assertIsNone(store.load()[0]['expires'])
            self.assertEqual(len(clean_cookies(store.load())),1)

    def test_cdp_only_requests_niconico_and_filters_result(self):
        browser=BrowserLogin(threading.Event());browser.base='http://127.0.0.1:45678'
        browser.http=MagicMock()
        browser.http.get.return_value.json.return_value=[{'type':'page','webSocketDebuggerUrl':'ws://127.0.0.1:45678/devtools/page/test'}]
        ws=MagicMock();ws.recv.return_value=json.dumps({'id':1,'result':{'cookies':[cookie(),cookie(domain='other.test')]}})
        with patch('nicoview.browser_login.websocket.create_connection',return_value=ws):
            result=browser.cookies()
        self.assertEqual(len(result),1)
        request=json.loads(ws.send.call_args.args[0])
        self.assertEqual(request['params']['urls'],['https://live.nicovideo.jp/','https://www.nicovideo.jp/'])
        ws.close.assert_called_once()

    def test_cdp_does_not_follow_nonlocal_websocket(self):
        browser=BrowserLogin(threading.Event());browser.base='http://127.0.0.1:45678'
        browser.http=MagicMock()
        browser.http.get.return_value.json.return_value=[{'type':'page','webSocketDebuggerUrl':'ws://evil.test:45678/x'}]
        with patch('nicoview.browser_login.websocket.create_connection') as connect:
            self.assertEqual(browser.cookies(),[])
            connect.assert_not_called()
