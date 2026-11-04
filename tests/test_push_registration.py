import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from nicoview.push_registration import FollowSubscriptions

COOKIES = [{'name': 'user_session', 'value': 'synthetic-session', 'domain': '.nicovideo.jp'}]


def channel_state(following=False, status=200, api_status=200):
    response = Mock(status_code=status)
    response.json.return_value = {'meta': {'status': api_status}, 'data': {'following': following}}
    return response


class FollowSubscriptionTests(unittest.TestCase):
    def test_nanime_follow_uses_successful_button_request(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Mock(status_code=200)
            state.json.return_value = {'meta': {'status': 200}, 'data': {'following': False}}
            success = Mock(status_code=200)
            success.json.return_value = {'meta': {'status': 200}}
            with patch('nicoview.push_registration.requests.get', return_value=state) as get, \
                    patch('nicoview.push_registration.requests.post', return_value=success) as post:
                result = FollowSubscriptions(Path(folder) / 'state.json').follow('ch67', COOKIES)
                self.assertTrue(result['ok'])
                get.assert_called_once()
                self.assertEqual(get.call_args.args[0], 'https://public-api.ch.nicovideo.jp/v1/user/followees/channel/67')
                self.assertEqual(post.call_args.kwargs['headers']['X-Frontend-Id'], '122')
                self.assertEqual(post.call_args.kwargs['headers']['Origin'], 'https://anime.nicovideo.jp')
                self.assertEqual(post.call_args.kwargs['headers']['X-Requested-With'],
                                 'https://anime.nicovideo.jp/live/reserved-ikkyo.html?from=ch_article_67')
                self.assertEqual(post.call_args.kwargs['json'], {'mode': 'add', 'channelId': 67})

    def test_nanime_existing_follow_needs_no_program_page_or_post(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Mock(status_code=200)
            state.json.return_value = {'meta': {'status': 200}, 'data': {'following': True}}
            with patch('nicoview.push_registration.requests.get', return_value=state) as get, \
                    patch('nicoview.push_registration.requests.post') as post:
                follow = FollowSubscriptions(Path(folder) / 'state.json')
                self.assertTrue(follow.follow('ch67', COOKIES)['ok'])
                self.assertTrue(follow.follow('ch67', COOKIES)['ok'])
                get.assert_called_once()
                post.assert_not_called()

    def test_follow_once_and_reuse_receipt_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'push-follows.json'
            response = Mock(status_code=200)
            response.json.return_value = {'meta': {'status': 200}}
            with patch('nicoview.push_registration.requests.post', return_value=response) as post:
                follow = FollowSubscriptions(path)
                self.assertTrue(follow.follow('10001', COOKIES)['ok'])
                self.assertTrue(follow.follow('10001', COOKIES)['ok'])
                self.assertTrue(FollowSubscriptions(path).follow('10001', COOKIES)['ok'])
                self.assertEqual(post.call_count, 1)
                self.assertEqual(post.call_args.args[0], 'https://user-follow-api.nicovideo.jp/v1/user/followees/niconico-users/10001.json')
                self.assertFalse(post.call_args.kwargs['allow_redirects'])
                self.assertNotIn('synthetic-session', path.read_text())

    def test_login_change_does_not_reuse_other_sessions_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            response = Mock(status_code=200)
            response.json.return_value = {'meta': {'status': 200}}
            with patch('nicoview.push_registration.requests.post', return_value=response) as post:
                follow = FollowSubscriptions(Path(folder) / 'state.json')
                follow.follow('10001', COOKIES)
                follow.follow('10001', [{**COOKIES[0], 'value': 'other-session'}])
                self.assertEqual(post.call_count, 2)

    def test_throttle_pauses_batch_without_automatic_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            follow = FollowSubscriptions(Path(folder) / 'state.json')
            with patch('nicoview.push_registration.requests.post', return_value=Mock(status_code=429)) as post:
                self.assertEqual(follow.follow('10001', COOKIES)['state'], 'rate_limited')
                self.assertFalse(follow.follow('10001', COOKIES)['ok'])
                self.assertFalse(follow.follow('10002', COOKIES)['ok'])
                self.assertEqual(post.call_count, 1)

    def test_bad_id_and_missing_login_never_send_a_request(self):
        with tempfile.TemporaryDirectory() as folder:
            follow = FollowSubscriptions(Path(folder) / 'state.json')
            with patch('nicoview.push_registration.requests.post') as post:
                for target in ('../1', 'abc', '', '１'):
                    self.assertFalse(follow.follow(target, COOKIES)['ok'])
                self.assertFalse(follow.follow('10001', [])['ok'])
                post.assert_not_called()

    def test_corrupt_follow_receipts_do_not_break_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state.json'
            path.write_text('{"session_hash":"old","users":{}}')
            self.assertEqual(FollowSubscriptions(path).users,set())

    def test_user_api_throttle_inside_http_success_still_pauses(self):
        with tempfile.TemporaryDirectory() as folder:
            follow = FollowSubscriptions(Path(folder) / 'state.json')
            response = Mock(status_code=200)
            response.json.return_value = {'meta': {'status': 429}}
            with patch('nicoview.push_registration.requests.post', return_value=response) as post:
                result = follow.follow('10001', COOKIES)
                self.assertEqual(result['state'], 'rate_limited')
                self.assertEqual(result['api_status'], 429)
                self.assertEqual(follow.follow('10002', COOKIES)['state'], 'follow_deferred')
                self.assertEqual(post.call_count, 1)

    def test_channel_registration_and_receipt_reuse_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            success = Mock(status_code=204)
            success.json.side_effect = AssertionError('204 has no body')
            with patch('nicoview.push_registration.requests.get', return_value=channel_state(False)) as get, patch('nicoview.push_registration.requests.post', return_value=success) as post:
                follow = FollowSubscriptions(path)
                self.assertTrue(follow.follow('ch12345', COOKIES)['ok'])
                self.assertTrue(follow.follow('12345', COOKIES, target_type='channel')['ok'])
                self.assertTrue(FollowSubscriptions(path).follow('ch12345', COOKIES)['ok'])
                self.assertEqual(get.call_count, 1)
                self.assertEqual(post.call_count, 1)
                self.assertEqual(get.call_args.args[0], 'https://public-api.ch.nicovideo.jp/v1/user/followees/channel/12345')
                self.assertFalse(get.call_args.kwargs['allow_redirects'])
                self.assertEqual(post.call_args.kwargs['json'], {'mode': 'add', 'channelId': 12345})
                self.assertEqual(post.call_args.kwargs['headers']['X-Frontend-Id'], '9')
                self.assertFalse(post.call_args.kwargs['allow_redirects'])

    def test_channel_receipt_is_not_reused_for_another_login(self):
        with tempfile.TemporaryDirectory() as folder:
            follow = FollowSubscriptions(Path(folder) / 'state.json')
            other = [{**COOKIES[0], 'value': 'other-session'}]
            with patch('nicoview.push_registration.requests.get', side_effect=[channel_state(True), channel_state(False)]) as get, patch('nicoview.push_registration.requests.post', return_value=Mock(status_code=204)) as post:
                self.assertTrue(follow.follow('ch67', COOKIES)['ok'])
                self.assertTrue(follow.follow('ch67', other)['ok'])
                self.assertEqual(get.call_count, 2)
                post.assert_called_once()

    def test_invalid_channel_ids_and_missing_login_never_request(self):
        with tempfile.TemporaryDirectory() as folder:
            follow = FollowSubscriptions(Path(folder) / 'state.json')
            with patch('nicoview.push_registration.requests.get') as get, patch('nicoview.push_registration.requests.post') as post:
                for target in ('ch67/evil', 'https://evil.example', 'ch', 'ch１', '../67'):
                    self.assertFalse(follow.follow(target, COOKIES, target_type='channel')['ok'])
                self.assertFalse(follow.follow('ch67', [])['ok'])
                get.assert_not_called()
                post.assert_not_called()

    def test_channel_state_refusal_pauses_new_requests(self):
        for status, state in ((401, 'login_required'), (403, 'access_denied'), (429, 'rate_limited'), (503, 'registration_failed'), (302, 'registration_failed')):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder:
                follow = FollowSubscriptions(Path(folder) / 'state.json')
                with patch('nicoview.push_registration.requests.get', return_value=Mock(status_code=status)) as get, patch('nicoview.push_registration.requests.post') as post:
                    result = follow.follow('ch67', COOKIES)
                    self.assertEqual(result['state'], state)
                    self.assertEqual(result['stage'], 'channel_status')
                    self.assertEqual(result['http_status'], status)
                    self.assertTrue(result['stop_batch'])
                    self.assertEqual(follow.follow('10002', COOKIES)['state'], 'follow_deferred')
                    self.assertEqual(get.call_count, 1)
                    post.assert_not_called()

    def test_channel_registration_refusal_pauses_new_requests(self):
        for status, state in ((401, 'login_required'), (403, 'access_denied'), (429, 'rate_limited'), (503, 'registration_failed'), (302, 'registration_failed')):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder:
                follow = FollowSubscriptions(Path(folder) / 'state.json')
                with patch('nicoview.push_registration.requests.get', return_value=channel_state(False)) as get, patch('nicoview.push_registration.requests.post', return_value=Mock(status_code=status)) as post:
                    result = follow.follow('ch67', COOKIES)
                    self.assertEqual(result['state'], state)
                    self.assertEqual(result['stage'], 'channel_follow')
                    self.assertTrue(result['stop_batch'])
                    self.assertEqual(follow.follow('10002', COOKIES)['state'], 'follow_deferred')
                    self.assertEqual(get.call_count, 1)
                    self.assertEqual(post.call_count, 1)

    def test_missing_channel_does_not_block_users_or_repeat_request(self):
        with tempfile.TemporaryDirectory() as folder:
            follow = FollowSubscriptions(Path(folder) / 'state.json')
            success = Mock(status_code=200)
            success.json.return_value = {'meta': {'status': 200}}
            with patch('nicoview.push_registration.requests.get', return_value=Mock(status_code=404)) as get, patch('nicoview.push_registration.requests.post', return_value=success) as post:
                failed = follow.follow('ch67', COOKIES)
                self.assertFalse(failed['stop_batch'])
                self.assertEqual(follow.follow('ch67', COOKIES), failed)
                self.assertTrue(follow.follow('10001', COOKIES)['ok'])
                self.assertEqual(get.call_count, 1)
                self.assertEqual(post.call_count, 1)

    def test_state_json_errors_never_trigger_registration(self):
        bodies = ({}, {'meta': {'status': 200}, 'data': {'following': 'true'}}, {'meta': {'status': 200}}, {'meta': {'status': 401}})
        for body in bodies:
            with self.subTest(body=body), tempfile.TemporaryDirectory() as folder:
                follow = FollowSubscriptions(Path(folder) / 'state.json')
                response = Mock(status_code=200)
                response.json.return_value = body
                with patch('nicoview.push_registration.requests.get', return_value=response), patch('nicoview.push_registration.requests.post') as post:
                    result = follow.follow('ch67', COOKIES)
                    self.assertFalse(result['ok'])
                    self.assertTrue(result['stop_batch'])
                    self.assertEqual(result['stage'], 'channel_status')
                    self.assertNotIn('ch67', follow.channels)
                    post.assert_not_called()

    def test_invalid_json_and_network_failure_pause_without_exposing_secrets(self):
        import requests
        for error in (ValueError('secret payload'), requests.ConnectionError('secret URL')):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as folder:
                follow = FollowSubscriptions(Path(folder) / 'state.json')
                response = Mock(status_code=200)
                response.json.side_effect = error
                with patch('nicoview.push_registration.requests.get', return_value=response), patch('nicoview.push_registration.requests.post') as post:
                    result = follow.follow('ch67', COOKIES)
                    self.assertFalse(result['ok'])
                    self.assertTrue(result['stop_batch'])
                    self.assertNotIn('secret', str(result))
                    self.assertEqual(follow.follow('10002', COOKIES)['state'], 'follow_deferred')
                    post.assert_not_called()

    def test_channel_api_error_in_http_success_is_not_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            follow = FollowSubscriptions(Path(folder) / 'state.json')
            response = Mock(status_code=200)
            response.json.return_value = {'meta': {'status': 429}}
            with patch('nicoview.push_registration.requests.get', return_value=channel_state(False)), patch('nicoview.push_registration.requests.post', return_value=response) as post:
                result = follow.follow('ch67', COOKIES)
                self.assertEqual(result['state'], 'rate_limited')
                self.assertNotIn('ch67', follow.channels)
                self.assertEqual(follow.follow('10002', COOKIES)['state'], 'follow_deferred')
                self.assertEqual(post.call_count, 1)

    def test_cached_success_remains_available_after_batch_pause(self):
        with tempfile.TemporaryDirectory() as folder:
            follow = FollowSubscriptions(Path(folder) / 'state.json')
            with patch('nicoview.push_registration.requests.get', side_effect=[channel_state(True), Mock(status_code=429)]) as get, patch('nicoview.push_registration.requests.post') as post:
                self.assertTrue(follow.follow('ch67', COOKIES)['ok'])
                self.assertFalse(follow.follow('ch12345', COOKIES)['ok'])
                self.assertTrue(follow.follow('ch67', COOKIES)['ok'])
                self.assertEqual(get.call_count, 2)
                post.assert_not_called()

    def test_cancellation_after_state_read_never_registers_or_caches(self):
        with tempfile.TemporaryDirectory() as folder:
            follow = FollowSubscriptions(Path(folder) / 'state.json')
            cancel = threading.Event()
            def read(*args, **kwargs):
                cancel.set()
                return channel_state(False)
            with patch('nicoview.push_registration.requests.get', side_effect=read), patch('nicoview.push_registration.requests.post') as post:
                result = follow.follow('ch67', COOKIES, cancel=cancel)
                self.assertFalse(result['ok'])
                self.assertIsNone(follow.paused)
                self.assertNotIn('ch67', follow.channels)
                post.assert_not_called()
