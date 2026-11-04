import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from nicoview.webpush import WebPushCrypto, WebPushClient
from nicoview.scheduler import Scheduler
from Crypto.PublicKey import ECC
from Crypto.Cipher import AES
from Crypto.Protocol.KDF import HKDF
from Crypto.Hash import SHA256

class WebPushCryptoTests(unittest.TestCase):
    def test_key_generation_and_serialization(self):
        crypto = WebPushCrypto()
        self.assertEqual(len(crypto.public_bytes), 65)
        self.assertEqual(crypto.public_bytes[0], 0x04)
        d = crypto.to_dict()
        restored = WebPushCrypto.from_dict(d)
        self.assertEqual(crypto.public_bytes, restored.public_bytes)
        self.assertEqual(crypto.auth, restored.auth)

    def test_decrypt_valid_payload(self):
        client = WebPushCrypto()
        server_key = ECC.generate(curve='P-256')
        server_pub = b'\x04' + int(server_key.pointQ.x).to_bytes(32, 'big') + int(server_key.pointQ.y).to_bytes(32, 'big')

        shared_point = client.key.pointQ * server_key.d
        shared_secret = int(shared_point.x).to_bytes(32, 'big')

        context = client.public_bytes + server_pub
        prk_info = b"WebPush: info\0" + context
        prk = HKDF(shared_secret, 32, client.auth_secret, SHA256, 1, prk_info)

        salt = os.urandom(16)
        cek = HKDF(prk, 16, salt, SHA256, 1, b"Content-Encoding: aes128gcm\0")
        nonce = HKDF(prk, 12, salt, SHA256, 1, b"Content-Encoding: nonce\0")

        plaintext = json.dumps({"title": "Test Stream", "data": {"on_click": "https://live.nicovideo.jp/watch/lv999"}}).encode('utf-8')
        cipher = AES.new(cek, AES.MODE_GCM, nonce=nonce)
        ciphertext, tag = cipher.encrypt_and_digest(plaintext + b'\x02' + b'\x00'*5)

        # Construct RFC 8291 payload:
        # salt (16) + record_size (4) + pub_len (1) + server_pub (65) + ciphertext (includes tag)
        record_size = (4096).to_bytes(4, 'big')
        pub_len = bytes([len(server_pub)])
        payload = salt + record_size + pub_len + server_pub + ciphertext + tag

        decrypted = client.decrypt(payload)
        self.assertEqual(json.loads(decrypted)['title'], "Test Stream")


class PushTransportTests(unittest.TestCase):
    def test_different_account_never_reuses_previous_push_identity(self):
        from nicoview.push_registration import session_fingerprint
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'push.json'
            first=[{'name':'user_session','value':'first'}]
            client=WebPushClient(lambda:first,Mock(),path)
            client.uaid='identity';client.channel_id='channel'
            client.endpoint='https://updates.push.services.mozilla.com/wpush/v2/example'
            client.registered_for=session_fingerprint(first)
            client.save_state()
            resumed=WebPushClient(lambda:first,Mock(),path)
            self.assertEqual(resumed.uaid,'identity')
            other=WebPushClient(lambda:[{'name':'user_session','value':'second'}],Mock(),path)
            self.assertIsNone(other.uaid)
            self.assertIsNone(other.channel_id)
            self.assertNotEqual(other.crypto.public_bytes,client.crypto.public_bytes)

    def test_late_connection_after_stop_does_not_register_old_account(self):
        with tempfile.TemporaryDirectory() as folder:
            client=WebPushClient(lambda:[{'name':'user_session','value':'old'}],Mock(),Path(folder)/'push.json')
            ws=Mock()
            ws.recv.side_effect=[json.dumps({'messageType':'hello','status':200,'uaid':'identity'}),
                                 json.dumps({'messageType':'register','status':200,'pushEndpoint':'https://example.invalid/push'})]
            def connect(*args,**kwargs):
                client.stop()
                return ws
            client.running=True
            with patch('nicoview.webpush.websocket.create_connection',side_effect=connect), patch.object(client,'register_to_niconico') as register:
                client._run()
                register.assert_not_called()
            ws.close.assert_called()

    def test_notification_is_acknowledged_and_duplicate_version_is_not_delivered(self):
        with tempfile.TemporaryDirectory() as folder:
            receive = Mock()
            client = WebPushClient(lambda: [], receive, Path(folder)/'push.json')
            client.channel_id = 'channel'
            client.crypto = Mock()
            client.crypto.decrypt.return_value = '{"data":{}}'
            ws = Mock()
            data = {'messageType':'notification', 'channelID':'channel', 'version':'v1', 'data':'AA'}
            client.handle_notification(ws, data)
            client.handle_notification(ws, data)
            self.assertEqual(receive.call_count, 1)
            ack = json.loads(ws.send.call_args.args[0])
            self.assertEqual(ack, {'messageType':'ack', 'updates':[{'channelID':'channel','version':'v1','code':100}]})

    def test_registration_checks_server_result_and_does_not_follow_redirects(self):
        with tempfile.TemporaryDirectory() as folder:
            cookies = [{'name':'user_session', 'value':'synthetic'}]
            status = Mock()
            client = WebPushClient(lambda: cookies, Mock(), Path(folder)/'push.json', on_status=status)
            response = Mock(status_code=200)
            response.json.return_value = {'meta':{'status':403}}
            with patch('nicoview.webpush.requests.post', return_value=response) as post:
                self.assertFalse(client.register_to_niconico('https://example.invalid/push'))
                self.assertFalse(post.call_args.kwargs['allow_redirects'])

    def test_idle_timeout_sends_keepalive_without_reconnecting(self):
        import websocket
        with tempfile.TemporaryDirectory() as folder:
            client = WebPushClient(lambda: [], Mock(), Path(folder)/'push.json')
            client.running = True
            ws = Mock()
            def receive():
                if ws.recv.call_count == 1:
                    raise websocket.WebSocketTimeoutException()
                client.running = False
                return '{}'
            ws.recv.side_effect = receive
            client.listen(ws)
            ws.send.assert_called_once_with('{}')

    def test_keepalive_is_not_sent_more_than_once_per_minute(self):
        import websocket
        with tempfile.TemporaryDirectory() as folder:
            client = WebPushClient(lambda: [], Mock(), Path(folder)/'push.json')
            client.running = True
            ws = Mock()
            def receive():
                if ws.recv.call_count > 3:
                    client.running = False
                    return '{}'
                raise websocket.WebSocketTimeoutException()
            ws.recv.side_effect = receive
            with patch('nicoview.webpush.time.monotonic', side_effect=[0,30,60]):
                client.listen(ws)
            self.assertEqual(ws.send.call_count, 1)

class SchedulerNotifyTests(unittest.TestCase):
    def test_notify_live_triggers_waiting_rule(self):
        s = Scheduler()
        s.add({'url': 'https://live.nicovideo.jp/watch/lv123'}, 'play')
        launched = []
        def launch(ident, p, mode):
            launched.append((ident, p, mode))

        prog = {'url': 'https://live.nicovideo.jp/watch/lv123', 'title': 'My Stream'}
        s.notify_live(prog, launch)

        self.assertEqual(len(launched), 1)
        self.assertEqual(launched[0][1]['id'], 'lv123')
        self.assertEqual(s.snapshot()[0]['state'], 'queued')

    def test_notify_live_user_rule_triggers_and_dedups(self):
        s = Scheduler()
        s.add({'user_id': '10001', 'title': 'Streamer A'}, 'record')
        launched = []
        def launch(ident, p, mode):
            launched.append((ident, p, mode))

        prog = {'id': 'lv555', 'url': 'https://live.nicovideo.jp/watch/lv555', 'user_id': '10001', 'title': 'Streamer A Stream'}
        s.notify_live(prog, launch)

        self.assertEqual(len(launched), 1)
        self.assertEqual(launched[0][1]['id'], 'lv555')
        self.assertEqual(launched[0][2], 'record')

if __name__ == '__main__':
    unittest.main()
