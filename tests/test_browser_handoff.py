import json
import threading
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from nicoview.auth import Cancelled
from nicoview.browser_login import BrowserLogin


class BrowserHandoffTests(unittest.TestCase):
    def test_reader_waits_for_edge_child_after_launcher_exits(self):
        browser = BrowserLogin(threading.Event())
        proc = MagicMock(); proc.poll.return_value = 0
        job = MagicMock(); job.active_process_count = 2
        def ready(_):
            (browser.profile / 'DevToolsActivePort').write_text('12345\n/devtools/browser/test')
            return False
        with patch('nicoview.browser_login.find_edge', return_value='edge.exe'), \
             patch('nicoview.browser_login.subprocess.Popen', return_value=proc), \
             patch('nicoview.browser_login.Job', return_value=job), \
             patch.object(browser.stop, 'wait', side_effect=ready):
            try:
                browser.open('about:blank', headless=True)
                self.assertEqual(browser.base, 'http://127.0.0.1:12345')
            finally: browser.close()

    def test_interactive_child_must_close_before_handoff(self):
        browser = BrowserLogin(threading.Event())
        job = MagicMock(); job.active_process_count = 1
        proc = SimpleNamespace(poll=lambda: 0, returncode=0)
        waited = []
        def wait(_):
            waited.append(True); job.active_process_count = 0
            return False
        def opened(*args, **kwargs):
            if kwargs.get('debugging') is False:
                browser.proc=proc; browser.job=job
            else:
                self.assertTrue(waited, 'Started handoff while interactive child was still open')
                browser.proc=SimpleNamespace(poll=lambda: None)
        cookies=[{'name':'user_session','value':'synthetic'}]
        with patch.object(browser, 'open', side_effect=opened), \
             patch.object(browser.stop, 'wait', side_effect=wait), \
             patch.object(browser, 'cookies', return_value=cookies), \
             patch.object(browser, 'close'):
            self.assertEqual(browser.run(), cookies)

    def test_interactive_login_has_no_debugging_and_keeps_session(self):
        browser = BrowserLogin(threading.Event())
        proc = MagicMock()
        proc.poll.return_value = 0
        with patch('nicoview.browser_login.find_edge', return_value='edge.exe'), \
             patch('nicoview.browser_login.subprocess.Popen', return_value=proc) as launch, \
             patch('nicoview.browser_login.Job'):
            try:
                browser.open(debugging=False)
                args = launch.call_args.args[0]
                self.assertFalse(any('remote-debugging' in arg or 'headless' in arg for arg in args))
                prefs = json.loads((browser.profile / 'Default/Preferences').read_text())
                self.assertEqual(prefs['session']['restore_on_startup'], 1)
                profile = browser.profile
                browser.prepare_profile()
                self.assertEqual(browser.profile, profile)
            finally:
                browser.close()
        self.assertFalse(profile.exists())

    def test_handoff_waits_for_normal_exit_before_reading_cookies(self):
        browser = BrowserLogin(threading.Event())
        proc = MagicMock(); proc.poll.return_value = 0; proc.returncode = 0
        reader = MagicMock(); reader.poll.return_value = None
        def open_browser(*args, **kwargs):
            browser.proc = proc if not kwargs.get('debugging', True) else reader
        cookies = [{'name': 'user_session', 'value': 'synthetic'}]
        with patch.object(browser, 'open', side_effect=open_browser) as opened, \
             patch.object(browser, 'cookies', return_value=cookies), \
             patch.object(browser, 'close') as close:
            self.assertEqual(browser.run(), cookies)
        self.assertEqual(opened.call_args_list[0].kwargs, {'debugging': False})
        self.assertEqual(opened.call_args_list[1].args, ('about:blank',))
        self.assertTrue(opened.call_args_list[1].kwargs['headless'])
        close.assert_called_once()

    def test_cancel_never_starts_handoff(self):
        stop = threading.Event(); stop.set()
        browser = BrowserLogin(stop)
        with patch.object(browser, 'open') as opened, patch.object(browser, 'close'):
            with self.assertRaises(Cancelled):
                browser.run()
        opened.assert_not_called()

    def test_abnormal_exit_does_not_import_session(self):
        browser = BrowserLogin(threading.Event())
        browser.proc = MagicMock(); browser.proc.poll.return_value = 13
        with patch.object(browser, 'open') as opened, patch.object(browser, 'close'):
            with self.assertRaisesRegex(ValueError, '終了'):
                browser.run()
        self.assertEqual(opened.call_count, 1)

    def test_cancel_while_edge_is_open_never_reads_cookies(self):
        stop = MagicMock(); stop.is_set.return_value = False; stop.wait.return_value = True
        browser = BrowserLogin(stop)
        browser.proc = MagicMock(); browser.proc.poll.return_value = None
        with patch.object(browser, 'open') as opened, patch.object(browser, 'close') as close, \
             patch.object(browser, 'cookies') as cookies:
            with self.assertRaises(Cancelled):browser.run()
        self.assertEqual(opened.call_count, 1)
        cookies.assert_not_called(); close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
