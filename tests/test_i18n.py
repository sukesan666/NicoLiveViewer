import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
import ast
import string
import sys
from pathlib import Path

from nicoview.i18n import ENGLISH, get_language, set_language, tr
from nicoview.settings import clean_settings, load_settings, save_settings
from nicoview.settings_ui import SettingsDialog


class TranslationTests(unittest.TestCase):
    def tearDown(self):
        set_language('ja')

    def test_language_validation_and_persistence(self):
        self.assertEqual(clean_settings({})['language'], 'ja')
        for value in ('xx', '', None, [], {}):
            self.assertEqual(clean_settings({'language': value})['language'], 'ja')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            save_settings(path, {'language': 'en'})
            self.assertEqual(load_settings(path)['language'], 'en')

    def test_translation_fallback_and_formatting(self):
        set_language('en')
        self.assertEqual(get_language(), 'en')
        self.assertEqual(tr('再生'), 'Play')
        self.assertEqual(tr('終了', context='state'), 'Ended')
        self.assertEqual(tr('{0}件。').format(12), '12 results.')
        self.assertEqual(tr('10分ごとに確認・今回3件を追加'), 'Checked every 10 minutes; added 3 this time')
        self.assertEqual(tr('user-supplied 番組名'), 'user-supplied 番組名')
        set_language('ja')
        self.assertEqual(tr('再生'), '再生')
        self.assertEqual(tr('Waiting to reconnect'), '再接続待ち')
        set_language('unsupported')
        self.assertEqual(get_language(), 'ja')

    def test_follow_error_details_are_translated(self):
        from nicoview.studio_ui import StudioApp
        set_language('en')
        message = StudioApp.follow_result_message({
            'ok': False, 'state': 'rate_limited', 'stage': 'channel_page', 'http_status': 429})
        self.assertIn('rate limit', message)
        self.assertIn('Channel page', message)
        self.assertIn('HTTP 429', message)
        deferred = StudioApp.follow_result_message({'ok': False, 'state': 'follow_deferred'})
        self.assertIn('deferred', deferred)
        self.assertNotIn('failed', deferred)

    def test_settings_language_row_and_save(self):
        set_language('en')
        root = tk.Tk()
        root.withdraw()
        saved = []
        try:
            dialog = SettingsDialog(root, clean_settings({'language': 'en'}), saved.append)
            dialog.withdraw()
            self.assertEqual(dialog.title(), 'Settings — NicoLive Studio')
            theme = language = None
            def visit(widget):
                nonlocal theme, language
                if isinstance(widget, ttk.Label):
                    if widget.cget('text') == 'Theme': theme = widget
                    if widget.cget('text') == 'Language (applies after restart)': language = widget
                for child in widget.winfo_children(): visit(child)
            visit(dialog)
            self.assertIsNotNone(theme)
            self.assertIsNotNone(language)
            self.assertEqual(theme.master, language.master)
            self.assertEqual(int(language.grid_info()['row']), int(theme.grid_info()['row']) + 1)
            dialog.variables['language'].set('日本語')
            dialog.save()
            self.assertEqual(saved[0]['language'], 'ja')
            self.assertEqual(get_language(), 'en')  # No partial switch during playback.
        finally:
            root.destroy()

    def test_gui_catalog_covers_literals_and_preserves_placeholders(self):
        package = Path(__file__).resolve().parents[1] / 'src/nicoview'
        for name in ('studio_ui', 'settings_ui', 'ui', 'discovery_ui', 'library_ui', 'post_window', 'web_sync'):
            tree = ast.parse((package / (name + '.py')).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == 'tr' and node.args and isinstance(node.args[0], ast.Constant)):
                    self.assertIn(node.args[0].value, ENGLISH, (name, node.lineno))
        formatter = string.Formatter()
        for source, translated in ENGLISH.items():
            fields = lambda text: sorted(field for _, field, _, _ in formatter.parse(text) if field is not None)
            self.assertEqual(fields(source), fields(translated), source)

    def test_english_startup_keeps_program_data_and_language_changes_wait_for_restart(self):
        from nicoview.preferences import PreferenceStore
        from nicoview.studio_ui import StudioApp
        from test_studio_shell import FakeController
        with tempfile.TemporaryDirectory() as folder:
            store = PreferenceStore(folder)
            store.save_settings({'language': 'en', 'first_run_complete': True,
                                 'player_path': sys.executable, 'svp_enabled': False})
            root = tk.Tk()
            root.withdraw()
            app = None
            try:
                app = StudioApp(root, store, FakeController, background=False)
                self.assertEqual(get_language(), 'en')
                self.assertEqual(app.tree.heading('title', 'text'), 'Program')
                program = {'url': 'https://live.nicovideo.jp/watch/lv1', 'title': '日本語の番組名'}
                self.assertTrue(app.launch(program, 'play'))
                ident = next(iter(app.jobs))
                values = app.tree.item(ident, 'values')
                self.assertEqual(values[:2], ('Play', '日本語の番組名'))
                app.handle_event(ident, {'type': 'status', 'text': '番組に接続しています…'})
                self.assertEqual(app.tree.item(ident, 'values')[2], 'Connecting to the program…')
                app.apply_settings({**app.settings, 'language': 'ja'})
                self.assertEqual(store.load_settings()['language'], 'ja')
                self.assertEqual(get_language(), 'en')
                self.assertIn('after restart', app.status.get())
            finally:
                if app: app.shutdown()
                else: root.destroy()
