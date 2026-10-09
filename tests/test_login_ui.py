import sys
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import MagicMock, patch
from nicoview.ui import App

class LoginUITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=tk.Tk();self.root.withdraw()
        self.app=App(self.root,Path(self.tmp.name)/'settings.json')
        self.app.credential_store=MagicMock();self.app.credential_store.load.return_value=None
        self.app.controller=MagicMock();self.app.controller.start.return_value='test-id'
        self.app.controller.busy=False
        self.app.controller.poll_events.return_value=[]
        self.app.url.set('https://live.nicovideo.jp/watch/lv1');self.app.player.set(sys.executable)
        self.app.login.set(True);self.app.email.set('test@example.invalid');self.app.password.set('test-password')
    def tearDown(self):self.app.close();self.tmp.cleanup()
    def emit(self,kind):
        self.app.controller.poll_events.return_value=[{'type':kind,'request_id':'test-id','text':'test'}]
        self.app.poll()

    def test_browser_login_uses_session_without_password(self):
        self.app.session_store=MagicMock()
        cookies=[{'name':'user_session','value':'synthetic','domain':'.nicovideo.jp','expires':None}]
        self.app.browser_result(self.app.browser_generation,('success',cookies))
        self.app.session_store.save.assert_called_once_with(cookies)
        self.app.start()
        self.assertEqual(self.app.controller.start.call_args.args[2],{'session':cookies})
        self.assertIsNone(self.app.pending_credentials)

    def test_logout_discards_browser_result_and_persistent_session(self):
        self.app.session_store=MagicMock()
        generation=self.app.browser_generation
        self.app.browser_session=[{'name':'user_session'}]
        self.app.logout()
        self.app.browser_result(generation,('success',[{'name':'user_session'}]))
        self.assertEqual(self.app.browser_session,[])
        self.app.session_store.delete.assert_called_once()
        self.app.session_store.save.assert_not_called()

    def test_restart_restores_browser_session(self):
        cookies=[{'name':'user_session','value':'synthetic','domain':'.nicovideo.jp','expires':None}]
        another=tk.Toplevel(self.root);another.withdraw()
        with patch('nicoview.ui.SessionStore.load',return_value=cookies):
            restored=App(another,Path(self.tmp.name)/'restart'/'settings.json')
        try:
            self.assertTrue(restored.login.get())
            self.assertEqual(restored.browser_session,cookies)
        finally:restored.close()

    def test_opt_in_saved_only_after_success(self):
        self.app.remember.set(True);self.app.start()
        self.app.credential_store.save.assert_not_called()
        self.emit('authenticated')
        self.app.credential_store.save.assert_called_once_with('test@example.invalid','test-password')
        self.assertIsNone(self.app.pending_credentials)

    def test_failure_or_opt_out_never_saves(self):
        for remember,kind in [(True,'error'),(False,'authenticated')]:
            self.app.remember.set(remember);self.app.password.set('test-password');self.app.start();self.emit(kind)
            self.app.credential_store.save.assert_not_called()
            self.assertIsNone(self.app.pending_credentials)

    def test_revoke_and_logout_delete_storage(self):
        self.app.remember.set(False);self.app.toggle_remember()
        self.app.credential_store.delete.assert_called_once()
        self.app.credential_store.reset_mock();self.app.logout()
        self.app.credential_store.delete.assert_called_once()

    def test_saved_password_used_after_restart(self):
        self.app.password.set('');self.app.remember.set(True)
        self.app.credential_store.load.return_value={'email':'test@example.invalid','password':'restored'}
        self.app.start()
        self.assertEqual(self.app.controller.start.call_args.args[2]['password'],'restored')

    def test_busy_attempt_preserves_authenticating_account(self):
        self.app.remember.set(True);self.app.start()
        self.app.controller.busy=True
        self.app.email.set('other@example.invalid');self.app.password.set('other-password')
        self.app.start();self.emit('authenticated')
        self.assertEqual(self.app.auth_email,'test@example.invalid')
        self.app.credential_store.save.assert_called_once_with('test@example.invalid','test-password')
        self.assertEqual(self.app.controller.start.call_count,1)
        self.app.controller.busy=False;self.app.password.set('');self.app.start()
        self.assertEqual(self.app.controller.start.call_count,1)

    def test_stale_authentication_success_ignored(self):
        self.app.remember.set(True);self.app.start()
        self.app.authenticated_event({'type':'authenticated','request_id':'old-id','text':'old'})
        self.assertFalse(self.app.authenticated)
        self.app.credential_store.save.assert_not_called()

    def test_url_context_edit_actions(self):
        entry=self.app.url_entry
        self.assertTrue(entry.bind('<Button-3>'))
        self.app.url.set('https://example.invalid')
        entry.selection_range(0,5)
        self.app.url_edit('copy')
        self.assertEqual(self.root.clipboard_get(),'https')
        self.app.url_edit('cut')
        self.assertEqual(self.app.url.get(),'://example.invalid')
        self.app.url_edit('select_all')
        self.app.url_edit('paste')
        self.assertEqual(self.app.url.get(),'https')
        self.app.url_edit('select_all');self.app.url_edit('delete')
        self.assertEqual(self.app.url.get(),'')
