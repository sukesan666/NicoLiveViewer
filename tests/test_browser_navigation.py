import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from nicoview.url import web_url_for_item
from nicoview.discovery_ui import DiscoveryWindow, SchedulePicker
from nicoview.studio_ui import StudioApp
from nicoview.preferences import PreferenceStore
import tempfile


class WebUrlTests(unittest.TestCase):
    def test_web_url_for_item_resolution(self):
        self.assertEqual(web_url_for_item({'url': 'https://live.nicovideo.jp/watch/lv123'}), 'https://live.nicovideo.jp/watch/lv123')
        self.assertEqual(web_url_for_item({'id': 'lv456'}), 'https://live.nicovideo.jp/watch/lv456')
        self.assertEqual(web_url_for_item({'channel_id': 'ch789'}), 'https://ch.nicovideo.jp/ch789')
        self.assertEqual(web_url_for_item({'channel_id': '789'}), 'https://ch.nicovideo.jp/ch789')
        self.assertEqual(web_url_for_item({'id': 'ch101'}), 'https://ch.nicovideo.jp/ch101')
        self.assertEqual(web_url_for_item({'user_id': '12345'}), 'https://www.nicovideo.jp/user/12345')
        self.assertIn('keyword=%E3%82%B2%E3%83%BC%E3%83%A0', web_url_for_item({'official_tag': 'ゲーム'}))
        self.assertIn('isTagSearch=true', web_url_for_item({'official_tag': 'ゲーム'}))
        self.assertIn('keyword=%E3%82%A2%E3%83%8B%E3%83%A1', web_url_for_item({'keyword': 'アニメ', 'provider': 'official'}))
        self.assertEqual(web_url_for_item(None), '')

    def test_web_url_for_item_rejects_untrusted_destinations(self):
        for url in ('httpfoo:run', r'C:\test.exe', 'https://example.com/watch/lv456',
                    'https://live.nicovideo.jp@evil.example/', 'https://live.nicovideo.jp:1234/',
                    'https://[broken', 'https://live.nicovideo.jp\\@evil.example/'):
            with self.subTest(url=url):
                self.assertEqual(web_url_for_item({'url': url}), '')
        self.assertEqual(web_url_for_item({'channel_id': 'ch123/../../foo'}), '')


class ContextMenuNavigationTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = None

    def tearDown(self):
        if self.window and not getattr(self.window, 'closed', False):
            self.window.close()
        self.root.destroy()

    def test_discovery_menu_opens_browser(self):
        item = {'id': 'lv999', 'url': 'https://live.nicovideo.jp/watch/lv999', 'title': 'Test Live'}
        with patch('nicoview.discovery_ui.Catalog') as catalog:
            catalog.return_value.following_live_users.return_value = [item]
            self.window = DiscoveryWindow(self.root, [], Mock())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and 'lv999' not in self.window.tree.get_children():
                self.root.update()
                time.sleep(0.01)
            self.assertIn('lv999', self.window.tree.get_children())

            event = Mock(x=10, y=10, x_root=100, y_root=100)
            commands = {}
            original_add = tk.Menu.add_command
            def fake_add(self, **kwargs):
                commands[kwargs.get('label')] = kwargs.get('command')
                return original_add(self, **kwargs)

            with patch.object(self.window.tree, 'identify_row', return_value='lv999'), \
                 patch.object(tk.Menu, 'tk_popup'), \
                 patch.object(tk.Menu, 'add_command', side_effect=fake_add, autospec=True), \
                 patch('webbrowser.open') as open_browser:
                self.window._show_menu(event)
                # Verify exactly one browser command
                browser_labels = [lbl for lbl in commands if 'ブラウザ' in lbl]
                self.assertEqual(len(browser_labels), 1)
                self.assertEqual(browser_labels[0], '既定のブラウザで表示する')
                commands['既定のブラウザで表示する']()
                open_browser.assert_called_once_with('https://live.nicovideo.jp/watch/lv999')

    def test_discovery_left_click_does_not_popup_menu_right_click_does(self):
        item = {'id': 'lv999', 'url': 'https://live.nicovideo.jp/watch/lv999', 'title': 'Test Live'}
        with patch('nicoview.discovery_ui.Catalog') as catalog:
            catalog.return_value.following_live_users.return_value = [item]
            self.window = DiscoveryWindow(self.root, [], Mock())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and 'lv999' not in self.window.tree.get_children():
                self.root.update()
                time.sleep(0.01)

            with patch.object(self.window.tree, 'identify_row', return_value='lv999'), \
                 patch.object(tk.Menu, 'tk_popup') as popup_mock:
                self.window.tree.event_generate('<ButtonRelease-1>', x=10, y=10)
                popup_mock.assert_not_called()
                self.window.tree.event_generate('<Button-3>', x=10, y=10)
                popup_mock.assert_called_once()

    def test_schedule_picker_tag_and_keyword_search_page_browser(self):
        with patch('nicoview.discovery_ui.Catalog'):
            self.window = SchedulePicker(self.root, [], Mock(), mode='record')
            tag_tab_idx = next(idx for idx, (_, kind) in enumerate(self.window.tabs) if kind == 'official_tag')
            self.window.book.select(tag_tab_idx)
            self.window.query.set('音楽')

            with patch('webbrowser.open') as open_browser:
                self.window.open_in_browser()
                open_browser.assert_called_once()
                opened_url = open_browser.call_args[0][0]
                self.assertIn('isTagSearch=true', opened_url)
                self.assertIn('keyword=%E9%9F%B3%E6%A5%BD', opened_url)

            kw_tab_idx = next(idx for idx, (_, kind) in enumerate(self.window.tabs) if kind == 'keyword')
            self.window.book.select(kw_tab_idx)
            self.window.query.set('ボーカロイド')

            with patch('webbrowser.open') as open_browser:
                self.window.open_in_browser()
                open_browser.assert_called_once()
                opened_url = open_browser.call_args[0][0]
                self.assertIn('providerTypes=official', opened_url)
                self.assertIn('keyword=%E3%83%9C%E3%83%BC%E3%82%AB%E3%83%AD%E3%82%A4%E3%83%89', opened_url)

    def test_schedule_picker_url_tab_opens_only_nico_pages(self):
        self.window = SchedulePicker(self.root, [], Mock(), mode='record')
        index = next(idx for idx, (_, kind) in enumerate(self.window.tabs) if kind == 'url')
        self.window.book.select(index)
        for query, expected in (
                ('lv555', 'https://live.nicovideo.jp/watch/lv555'),
                ('ch101', 'https://ch.nicovideo.jp/ch101'),
                ('https://live2.nicovideo.jp/watch/lv555', 'https://live2.nicovideo.jp/watch/lv555'),
                ('httpfoo:run', 'https://live.nicovideo.jp/')):
            with self.subTest(query=query):
                self.window.query.set(query)
                self.assertEqual(self.window.get_browser_url(), expected)

    def test_search_result_menu_reserves_the_program_and_top_button_reserves_rule(self):
        from nicoview.i18n import tr

        select = Mock()
        self.window = SchedulePicker(self.root, [], select, mode='record')
        item = {'id': 'lv999', 'url': 'https://live.nicovideo.jp/watch/lv999', 'title': 'Test Live'}
        self.window.rows[item['id']] = item
        self.window.tree.insert('', 'end', iid=item['id'])
        event = Mock(y=10, x_root=100, y_root=100)
        for kind, field in (('official_tag', 'official_tag'), ('keyword', 'keyword')):
            with self.subTest(kind=kind):
                index = next(idx for idx, (_, value) in enumerate(self.window.tabs) if value == kind)
                self.window.book.select(index)
                self.window.query.set('音楽')
                select.reset_mock()
                with patch.object(self.window.tree, 'identify_row', return_value=item['id']), \
                     patch('nicoview.discovery_ui.tk.Menu') as menu:
                    self.window._show_menu(event)
                    commands = {call.kwargs['label']: call.kwargs['command']
                                for call in menu.return_value.add_command.call_args_list}
                    commands[tr('録画を予約')]()
                select.assert_called_once_with(item, 'record')
                select.reset_mock()
                self.window.choose('record')
                select.assert_called_once_with({field: '音楽'}, 'record')

    def test_reservations_menu_opens_browser_for_program_and_tag(self):
        with tempfile.TemporaryDirectory() as temp:
            store = PreferenceStore(temp)
            from test_studio_shell import FakeController
            app = StudioApp(self.root, store, FakeController, background=False)
            program = {'url': 'https://live.nicovideo.jp/watch/lv555', 'title': 'Reserved Program'}
            p_id = app.scheduler.add(program, 'record')
            t_id = app.scheduler.add({'official_tag': '歌ってみた'}, 'record')
            app.open_reservations()
            self.root.update()
            self.assertIn(p_id, app.reservations_tree.get_children())
            self.assertIn(t_id, app.reservations_tree.get_children())

            commands = {}
            original_add = tk.Menu.add_command
            def fake_add(self, **kwargs):
                commands[kwargs.get('label')] = kwargs.get('command')
                return original_add(self, **kwargs)

            # Test 1: Program reservation -> opens program page on right-click (<Button-3>)
            with patch.object(app.reservations_tree, 'identify_row', return_value=p_id), \
                 patch.object(tk.Menu, 'tk_popup') as popup_mock, \
                 patch.object(tk.Menu, 'add_command', side_effect=fake_add, autospec=True), \
                 patch('webbrowser.open') as open_browser:
                # Left click should not popup menu
                app.reservations_tree.event_generate('<ButtonRelease-1>', x=10, y=10)
                popup_mock.assert_not_called()
                # Right click should popup menu
                app.reservations_tree.event_generate('<Button-3>', x=10, y=10)
                popup_mock.assert_called_once()
                browser_labels = [lbl for lbl in commands if 'ブラウザ' in lbl]
                self.assertEqual(len(browser_labels), 1)
                commands['既定のブラウザで表示する']()
                open_browser.assert_called_once_with('https://live.nicovideo.jp/watch/lv555')

            commands.clear()
            # Test 2: Tag reservation -> opens search page on right-click (<Button-3>)
            with patch.object(app.reservations_tree, 'identify_row', return_value=t_id), \
                 patch.object(tk.Menu, 'tk_popup'), \
                 patch.object(tk.Menu, 'add_command', side_effect=fake_add, autospec=True), \
                 patch('webbrowser.open') as open_browser:
                app.reservations_tree.event_generate('<Button-3>', x=10, y=10)
                browser_labels = [lbl for lbl in commands if 'ブラウザ' in lbl]
                self.assertEqual(len(browser_labels), 1)
                commands['既定のブラウザで表示する']()
                open_browser.assert_called_once()
                opened_url = open_browser.call_args[0][0]
                self.assertIn('isTagSearch=true', opened_url)
                self.assertIn('keyword=%E6%AD%8C%E3%81%A3%E3%81%A6%E3%81%BF%E3%81%9F', opened_url)


if __name__ == '__main__':
    unittest.main()
