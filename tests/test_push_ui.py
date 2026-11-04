import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from nicoview.preferences import PreferenceStore
from nicoview.settings import clean_settings
from nicoview.studio_ui import StudioApp
from nicoview.push_registration import session_fingerprint
from test_studio_shell import FakeController
from test_push_reservations import notification
from test_push_registration import channel_state


class PushUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = StudioApp(self.root, PreferenceStore(self.temp.name), FakeController, background=False)

    def tearDown(self):
        self.app.shutdown()
        self.temp.cleanup()

    def test_polling_is_off_for_new_and_existing_settings(self):
        self.assertNotIn('reservation_polling',clean_settings({}))
        self.app.scheduler.add({'url':'https://live.nicovideo.jp/watch/lv1'},'play')
        with patch('nicoview.catalog.Catalog') as catalog, patch('nicoview.studio_ui.threading.Thread') as thread:
            self.app.schedule_tick()
            catalog.assert_not_called()
            thread.assert_not_called()

    def test_due_official_search_has_no_program_state_polling_or_start_callback(self):
        import time
        from nicoview.scheduler import Scheduler
        now=time.time()
        self.app.scheduler=Scheduler([{'keyword':'test','mode':'play','search_next_at':now-1}])
        with patch('nicoview.catalog.Catalog') as catalog, patch('nicoview.studio_ui.threading.Thread') as thread:
            catalog.return_value.official_week.return_value=[]
            self.app.schedule_tick()
            self.app.schedule_tick()
            self.assertEqual(thread.call_count,1)
            thread.call_args.kwargs['target']()
            catalog.return_value.official_week.assert_called_once()
            catalog.return_value.program.assert_not_called()
            catalog.return_value.following.assert_not_called()
            self.assertFalse(self.app.jobs)

    def test_login_after_start_and_account_change_refresh_transport(self):
        with patch('nicoview.studio_ui.WebPushClient') as client, patch.object(self.app,'cookies',return_value=[]) as cookies:
            self.app.ensure_webpush()
            client.assert_not_called()
            cookies.return_value=[{'name':'user_session','value':'first'}]
            self.app.ensure_webpush()
            self.app.ensure_webpush()
            self.assertEqual(client.call_count,1)
            cookies.return_value=[{'name':'user_session','value':'second'}]
            self.app.ensure_webpush()
            self.assertEqual(client.call_count,2)
            client.return_value.stop.assert_called_once()

    def test_push_callback_queues_work_and_does_not_touch_tk_or_fetch_catalog(self):
        with patch('nicoview.studio_ui.WebPushClient') as client, patch.object(self.app,'cookies',return_value=[{'name':'user_session','value':'test'}]):
            self.app.ensure_webpush()
            callback=client.call_args.args[1]
            with patch.object(self.app,'handle_push') as handle:
                callback(notification())
                handle.assert_not_called()
                self.app.poll()
                handle.assert_called_once()

    def test_stale_account_push_is_ignored(self):
        with patch.object(self.app,'handle_push') as handle:
            self.app.events.put(('push',('old-account',notification())))
            self.app.poll()
            handle.assert_not_called()

    def test_retry_does_not_wait_for_follow_network_lock_on_tk_thread(self):
        self.app.follow_subscriptions=Mock()
        with patch.object(self.app,'ensure_webpush') as ensure:
            self.app.retry_push_setup()
            self.app.follow_subscriptions.reset_failures.assert_not_called()
            ensure.assert_called_once()

    def test_follow_batch_uses_snapshot_and_never_repeats_success(self):
        self.app.scheduler.add({'user_id':'10001'},'play')
        self.app.scheduler.add({'url':'https://live.nicovideo.jp/watch/lv123','user_id':'10001'},'record')
        self.app.follow_subscriptions = Mock()
        self.app.follow_subscriptions.follow.return_value = {'ok':True,'state':'followed'}
        cookies=[{'name':'user_session','value':'test'}]
        self.app.push_generation=session_fingerprint(cookies)
        self.app.start_push_follows(cookies)
        self.app.push_setup_thread.join(timeout=2)
        self.app.poll()
        self.app.start_push_follows(cookies)
        self.assertEqual(self.app.follow_subscriptions.follow.call_count,1)

    def test_matching_push_starts_user_reservation_without_page_queries(self):
        import time
        from datetime import datetime,timezone
        payload=notification()
        payload['data']['created_at']=datetime.now(timezone.utc).isoformat()
        self.app.scheduler.add({'user_id':'10001'},'record')
        self.app.settings.update(recording_dir=self.temp.name,ffmpeg_path=__file__)
        with patch('nicoview.catalog.Catalog') as catalog, patch.object(self.app,'launch',return_value=True) as launch:
            self.app.handle_push(payload)
            self.app.poll()
            self.assertEqual(launch.call_count,1)
            catalog.assert_not_called()

    def run_follow_setup(self, cookies):
        # Execute the real worker synchronously, skipping its network pacing delay.
        with patch('nicoview.studio_ui.threading.Thread') as thread:
            thread.return_value.is_alive.return_value = False
            self.app.start_push_follows(cookies)
            self.assertTrue(thread.called)
            with patch.object(self.app.push_follow_cancel, 'wait', return_value=False):
                thread.call_args.kwargs['target']()
        with patch.object(self.app, 'cookies', return_value=cookies):
            self.app.poll()

    def test_channel_failure_does_not_fail_users_or_disconnect_receiver(self):
        self.app.scheduler.add({'channel_id': 'ch12345'}, 'play')
        self.app.scheduler.add({'user_id': '10001'}, 'play')
        self.app.scheduler.add({'user_id': '10002'}, 'record')
        cookies = [{'name': 'user_session', 'value': 'synthetic'}]
        self.app.push_generation = session_fingerprint(cookies)
        self.app.handle_push_status({'state': 'ready'})
        transport_label = self.app.push_status.get()
        success = Mock(status_code=200)
        success.json.return_value = {'meta': {'status': 200}}
        with patch('nicoview.push_registration.requests.get', return_value=channel_state(False)) as get, \
                patch('nicoview.push_registration.requests.post', side_effect=[Mock(status_code=404), success, success]) as post:
            self.run_follow_setup(cookies)
            self.assertEqual(post.call_count, 3)
            self.assertEqual(get.call_count, 1)
        rows = self.app.scheduler.snapshot()
        self.assertIn('404', rows[0]['message'])
        self.assertEqual([r['message'] for r in rows[1:]], ['プッシュ通知待ち'] * 2)
        self.assertTrue(self.app.push_ready)
        self.assertEqual(self.app.push_status.get(), transport_label)
        self.assertIn('失敗', self.app.push_follow_status.get())

    def test_throttle_defers_unprocessed_users_and_keeps_cached_success(self):
        cookies = [{'name': 'user_session', 'value': 'synthetic'}]
        self.app.push_generation = session_fingerprint(cookies)
        success = Mock(status_code=200)
        success.json.return_value = {'meta': {'status': 200}}
        with patch('nicoview.push_registration.requests.post', return_value=success):
            self.app.follow_subscriptions.follow('10001', cookies)
        self.app.scheduler.add({'channel_id': 'ch12345'}, 'play')
        self.app.scheduler.add({'user_id': '10001'}, 'play')
        self.app.scheduler.add({'user_id': '10002'}, 'record')
        self.app.handle_push_status({'state': 'ready'})
        with patch('nicoview.push_registration.requests.get', return_value=channel_state(False)) as get, \
                patch('nicoview.push_registration.requests.post', return_value=Mock(status_code=429)) as post:
            self.run_follow_setup(cookies)
            self.app.start_push_follows(cookies)
            self.assertEqual(post.call_count, 1)
            self.assertEqual(get.call_count, 1)
        rows = self.app.scheduler.snapshot()
        self.assertIn('429', rows[0]['message'])
        self.assertEqual(rows[1]['message'], 'プッシュ通知待ち')
        self.assertIn('保留', rows[2]['message'])
        self.assertNotIn('失敗', rows[2]['message'])
        self.assertTrue(self.app.push_ready)

    def test_follow_retry_preserves_healthy_receiver(self):
        receiver = self.app.webpush = Mock()
        self.app.handle_push_status({'state': 'ready'})
        with patch.object(self.app, 'ensure_webpush') as ensure:
            self.app.retry_push_setup()
        receiver.stop.assert_not_called()
        self.assertIs(self.app.webpush, receiver)
        self.assertTrue(self.app.push_ready)
        self.assertFalse(self.app.force_push_registration)
        self.assertTrue(self.app.push_reset_requested)
        ensure.assert_called_once()

    def test_follow_retry_keeps_success_receipts(self):
        cookies = [{'name': 'user_session', 'value': 'synthetic'}]
        self.app.push_generation = session_fingerprint(cookies)
        self.app.scheduler.add({'channel_id': 'ch12345'}, 'play')
        self.app.scheduler.add({'user_id': '10001'}, 'record')
        success = Mock(status_code=200)
        success.json.return_value = {'meta': {'status': 200}}
        with patch('nicoview.push_registration.requests.get', return_value=channel_state(False)) as get, \
                patch('nicoview.push_registration.requests.post', side_effect=[Mock(status_code=404), success, Mock(status_code=204)]) as post:
            self.run_follow_setup(cookies)
            self.app.webpush = Mock()
            self.app.handle_push_status({'state': 'ready'})
            with patch.object(self.app, 'ensure_webpush'):
                self.app.retry_push_setup()
            self.run_follow_setup(cookies)
            self.assertEqual(post.call_count, 3)
            self.assertEqual(get.call_count, 2)
        self.assertTrue(all(r['message'] == 'プッシュ通知待ち' for r in self.app.scheduler.snapshot()))

    def test_cancelled_follow_result_is_ignored_after_manual_retry(self):
        import threading
        cookies = [{'name': 'user_session', 'value': 'synthetic'}]
        self.app.push_generation = session_fingerprint(cookies)
        self.app.scheduler.add({'user_id': '10001'}, 'play')
        cancelled = threading.Event()
        cancelled.set()
        self.app.events.put(('push_follow', (self.app.push_generation, cancelled, '10001',
                                             {'ok': False, 'state': 'registration_failed'})))
        self.app.poll()
        self.assertEqual(self.app.scheduler.snapshot()[0]['message'], '待機中')
        self.assertFalse(self.app.push_follow_results)

    def test_readded_reservations_reuse_failed_or_deferred_result_without_requests(self):
        from nicoview.scheduler import Scheduler
        for result, expected in (({'ok': False, 'state': 'registration_failed', 'http_status': 404}, '404'),
                                 ({'ok': False, 'state': 'follow_deferred'}, '保留')):
            with self.subTest(state=result['state']):
                self.app.scheduler = Scheduler()
                self.app.push_follow_results = {'10001': result}
                with patch('nicoview.push_registration.requests.get') as get, \
                        patch('nicoview.push_registration.requests.post') as post:
                    for mode in ('play', 'record'):
                        self.app.add_reservation({'user_id': '10001'}, mode, show=False)
                        self.assertIn(expected, self.app.scheduler.snapshot()[-1]['message'])
                    self.app.scheduler.remove_key('user:10001', 'play')
                    self.app.add_reservation({'user_id': '10001'}, 'play', show=False)
                    self.assertIn(expected, self.app.scheduler.snapshot()[-1]['message'])
                    get.assert_not_called()
                    post.assert_not_called()

    def test_ch67_program_reservations_share_one_official_registration(self):
        cookies = [{'name': 'user_session', 'value': 'synthetic'}]
        self.app.push_generation = session_fingerprint(cookies)
        program = {'url': 'https://live.nicovideo.jp/watch/lv351633554',
                   'provider': 'official', 'channel_id': 'ch67'}
        self.app.scheduler.add(program, 'record')
        self.app.scheduler.add(program, 'play')
        self.app.handle_push_status({'state': 'ready'})
        with patch('nicoview.push_registration.requests.get', return_value=channel_state(False)) as get, \
                patch('nicoview.push_registration.requests.post', return_value=Mock(status_code=204)) as post:
            self.run_follow_setup(cookies)
            self.app.start_push_follows(cookies)
            post.assert_called_once()
            self.assertEqual(post.call_args.args[0], 'https://public-api.ch.nicovideo.jp/v1/user/follow')
            self.assertEqual(post.call_args.kwargs['json'], {'mode': 'add', 'channelId': 67})
            self.assertEqual(get.call_count, 1)
        self.assertTrue(self.app.push_ready)
        self.assertTrue(all(r['message'] == 'プッシュ通知待ち' for r in self.app.scheduler.snapshot()))

    def test_ch67_existing_follow_becomes_notification_waiting_without_post(self):
        cookies = [{'name': 'user_session', 'value': 'synthetic'}]
        self.app.push_generation = session_fingerprint(cookies)
        self.app.scheduler.add({'url': 'https://live.nicovideo.jp/watch/lv351633554',
                                'channel_id': 'ch67'}, 'record')
        self.app.handle_push_status({'state': 'ready'})
        with patch('nicoview.push_registration.requests.get', return_value=channel_state(True)) as get, \
                patch('nicoview.push_registration.requests.post') as post:
            self.run_follow_setup(cookies)
            self.assertEqual(self.app.scheduler.snapshot()[0]['message'], 'プッシュ通知待ち')
            self.assertTrue(self.app.push_ready)
            self.assertEqual(get.call_count, 1)
            post.assert_not_called()
