import time
import unittest
from unittest.mock import Mock

from nicoview.push_notifications import parse_notification
from nicoview.scheduler import Scheduler


def notification(ident='lv123', user='10001'):
    return {'title': '配信者さんが生放送を開始', 'body': '「番組」を放送',
            'icon': f'https://secure-dcdn.cdn.nimg.jp/nicoaccount/usericon/1/{user}.jpg?1',
            'data': {'on_click': f'https://live.nicovideo.jp/watch/{ident}?_topic=live_user_program_onairs',
                     'created_at': '2026-11-01T12:00:00Z', 'ttl': 600}}


class PushReservationTests(unittest.TestCase):
    def test_parse_start_without_fetching_program(self):
        result = parse_notification(notification(), now=1793534405)
        self.assertEqual(result['id'], 'lv123')
        self.assertEqual(result['user_id'], '10001')
        self.assertEqual(result['url'], 'https://live.nicovideo.jp/watch/lv123')
        self.assertEqual(result['title'], '番組')
        for icon in (
            'https://img.nicoprofile.nimg.jp/usericon/8121/81213858.jpg?1680055299',
            'https://img.nicoprofile.nimg.jp/usericon/s/8121/81213858.jpg?1680055299',
        ):
            p = notification('lv351739713')
            p['icon'] = icon
            res = parse_notification(p, now=1793534405)
            self.assertEqual(res['user_id'], '81213858')

    def test_reject_non_start_foreign_url_and_expired_notification(self):
        for field, value in [('on_click', 'https://evil.example/watch/lv123?_topic=live_user_program_onairs'),
                             ('on_click', 'https://live.nicovideo.jp/watch/lv123?_topic=video_uploaded'),
                             ('created_at', '2026-10-01T12:00:00Z')]:
            payload = notification()
            payload['data'][field] = value
            self.assertIsNone(parse_notification(payload, now=1793534405))
        self.assertIsNone(parse_notification({'data': []}))

    def test_default_icon_does_not_guess_user(self):
        payload = notification()
        payload['icon'] = 'https://secure-dcdn.cdn.nimg.jp/nicoaccount/usericon/defaults/blank.jpg'
        self.assertEqual(parse_notification(payload, now=1793534405)['user_id'], '')

    def test_exact_program_id_and_seen_dedup(self):
        scheduler = Scheduler()
        scheduler.add({'url': 'https://live.nicovideo.jp/watch/lv1234'}, 'play')
        launches = Mock()
        scheduler.notify_live({'id': 'lv123', 'url': 'https://live.nicovideo.jp/watch/lv123'}, launches)
        launches.assert_not_called()

        ident = scheduler.add({'user_id': '10001'}, 'record')
        program = parse_notification(notification(), now=1793534405)
        scheduler.notify_live(program, launches, now=100)
        scheduler.ack(ident, True, 'ready', program, now=100)
        scheduler.notify_live(program, launches, now=101)
        self.assertEqual(launches.call_count, 1)

    def test_follow_target_is_preserved_for_program_reservation(self):
        scheduler = Scheduler()
        scheduler.add({'url': 'https://live.nicovideo.jp/watch/lv123', 'user_id': '10001'}, 'play')
        restored = Scheduler(scheduler.snapshot())
        self.assertEqual(restored.snapshot()[0]['notify_user_id'], '10001')

    def test_resource_failure_retains_push_for_local_retry(self):
        scheduler = Scheduler()
        scheduler.add({'user_id': '10001'}, 'record')
        launch = Mock()
        program = parse_notification(notification(), now=1793534405)
        scheduler.notify_live(program, launch, preflight=lambda *_: (False, 'busy'), now=100)
        scheduler.retry_push(launch, preflight=lambda *_: (True, ''), now=161)
        self.assertEqual(launch.call_count, 1)

    def test_new_broadcast_received_during_connection_is_retained(self):
        for first_ok in (False, True):
            with self.subTest(first_ok=first_ok):
                scheduler=Scheduler()
                ident=scheduler.add({'user_id':'10001'},'play')
                first={'id':'lv1','url':'https://live.nicovideo.jp/watch/lv1','user_id':'10001','start':100}
                newer={'id':'lv2','url':'https://live.nicovideo.jp/watch/lv2','user_id':'10001','start':110}
                launch=Mock()
                scheduler.notify_live(first,launch,now=100)
                scheduler.notify_live(newer,launch,now=110)
                scheduler.ack(ident,first_ok,'result',first,now=120)
                scheduler.retry_push(launch,now=181)
                self.assertEqual(launch.call_args.args[1]['id'],'lv2')

    def test_channel_reservation_and_push(self):
        scheduler = Scheduler()
        ident = scheduler.add({'channel_id': 'ch2648723'}, 'record')
        channel_payload = {
            'title': 'チャンネルが生放送を開始', 'body': '「公式チャンネル特番」を放送',
            'icon': 'https://secure-dcdn.cdn.nimg.jp/comch/channel-icon/128x128/ch2648723.jpg?1',
            'data': {'on_click': 'https://live.nicovideo.jp/watch/lv9999?_topic=live_channel_program_onairs',
                     'created_at': '2026-11-01T12:00:00Z', 'ttl': 600}
        }
        program = parse_notification(channel_payload, now=1793534405)
        self.assertEqual(program['channel_id'], 'ch2648723')
        launch = Mock()
        scheduler.notify_live(program, launch, now=100)
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(launch.call_args.args[1]['id'], 'lv9999')
        self.assertEqual(launch.call_args.args[1]['channel_id'], 'ch2648723')
        scheduler.ack(ident, True, 'started', program, now=100)
        scheduler.notify_live(program, launch, now=101)
        self.assertEqual(launch.call_count, 1)

    def test_delayed_old_notification_cannot_replace_newer_pending_broadcast(self):
        scheduler=Scheduler()
        scheduler.add({'user_id':'10001'},'play')
        first={'id':'lv1','url':'https://live.nicovideo.jp/watch/lv1','user_id':'10001','start':100}
        newer={'id':'lv2','url':'https://live.nicovideo.jp/watch/lv2','user_id':'10001','start':110}
        launch=Mock()
        scheduler.notify_live(first,launch,lambda *_:(False,'busy'),now=100)
        scheduler.notify_live(newer,launch,lambda *_:(False,'busy'),now=110)
        scheduler.notify_live(first,launch,lambda *_:(False,'busy'),now=120)
        scheduler.retry_push(launch,now=181)
        self.assertEqual(launch.call_args.args[1]['id'],'lv2')
