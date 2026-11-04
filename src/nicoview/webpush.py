"""Web Push notification receiver using Mozilla AutoPush (RFC 8291)."""
import base64
import json
import os
from pathlib import Path
from collections import deque
import re
import threading
import time
from urllib.parse import urlparse
import uuid
import requests
import websocket
from Crypto.PublicKey import ECC
from Crypto.Cipher import AES
from Crypto.Protocol.KDF import HKDF
from Crypto.Hash import SHA256
from .ui import app_dir
from .preferences import atomic_json
from .push_registration import session_fingerprint

PUSH_SERVICE = "wss://push.services.mozilla.com/"
NICONICO_VAPID = "BC08Fdr2JChSL0kr5imO99L6zZG6Rn0tBAWNTlrZfJtsDoeAvmJSa7CnUOHpNhd5zOk0YnRToEOT47YLet8Dpig="
NICONICO_REGISTER_ENDPOINT = "https://api.push.nicovideo.jp/v1/nicopush/webpush/endpoints.json"


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def b64url_decode(s: str) -> bytes:
    padding = 4 - (len(s) % 4)
    if padding != 4:
        s += '=' * padding
    return base64.urlsafe_b64decode(s)


class WebPushCrypto:
    def __init__(self, key=None, auth_secret=None):
        if key is None:
            self.key = ECC.generate(curve='P-256')
        else:
            self.key = key
        if auth_secret is None:
            self.auth_secret = os.urandom(16)
        else:
            self.auth_secret = auth_secret

    @property
    def public_bytes(self) -> bytes:
        point = self.key.pointQ
        x = int(point.x).to_bytes(32, 'big')
        y = int(point.y).to_bytes(32, 'big')
        return b'\x04' + x + y

    @property
    def p256dh(self) -> str:
        return base64.b64encode(self.public_bytes).decode('ascii')

    @property
    def auth(self) -> str:
        return base64.b64encode(self.auth_secret).decode('ascii')

    def to_dict(self):
        return {
            'd': int(self.key.d),
            'auth': base64.b64encode(self.auth_secret).decode('ascii')
        }

    @classmethod
    def from_dict(cls, data):
        if not data or 'd' not in data or 'auth' not in data:
            return cls()
        try:
            key = ECC.construct(curve='P-256', d=data['d'])
            auth = base64.b64decode(data['auth'])
            return cls(key=key, auth_secret=auth)
        except Exception:
            return cls()

    def decrypt(self, payload_bytes: bytes) -> str:
        # RFC 8291 aes128gcm layout:
        # salt: 16 bytes
        # record size: 4 bytes (big endian)
        # public key length: 1 byte
        # server public key: 65 bytes
        # ciphertext: remaining bytes (last 16 bytes is GCM tag)
        if len(payload_bytes) < 16 + 4 + 1 + 65 + 16:
            raise ValueError('Payload too short')
        salt = payload_bytes[:16]
        pub_len = payload_bytes[20]
        server_pub_bytes = payload_bytes[21:21 + pub_len]
        ciphertext_and_tag = payload_bytes[21 + pub_len:]
        if len(ciphertext_and_tag) < 16:
            raise ValueError('Ciphertext too short')
        ciphertext = ciphertext_and_tag[:-16]
        tag = ciphertext_and_tag[-16:]

        # Import server public key
        if len(server_pub_bytes) != 65 or server_pub_bytes[0] != 4:
            raise ValueError('Invalid server public key format')
        sx = int.from_bytes(server_pub_bytes[1:33], 'big')
        sy = int.from_bytes(server_pub_bytes[33:65], 'big')
        server_key = ECC.construct(curve='P-256', point_x=sx, point_y=sy)

        # ECDH shared secret
        shared_point = server_key.pointQ * self.key.d
        shared_secret = int(shared_point.x).to_bytes(32, 'big')

        # Try RFC 8291 standard first, fallback if leading 0 stripped
        def try_decrypt(secret):
            context = self.public_bytes + server_pub_bytes
            prk_info = b"WebPush: info\0" + context
            prk = HKDF(secret, 32, self.auth_secret, SHA256, 1, prk_info)
            cek = HKDF(prk, 16, salt, SHA256, 1, b"Content-Encoding: aes128gcm\0")
            nonce = HKDF(prk, 12, salt, SHA256, 1, b"Content-Encoding: nonce\0")
            cipher = AES.new(cek, AES.MODE_GCM, nonce=nonce)
            decrypted = cipher.decrypt_and_verify(ciphertext, tag)
            # RFC 8188 puts a delimiter before optional zero padding.
            decrypted = decrypted.rstrip(b'\x00')
            if not decrypted.endswith(b'\x02'):
                raise ValueError('Invalid final record delimiter')
            return decrypted[:-1].decode('utf-8')

        try:
            return try_decrypt(shared_secret)
        except Exception:
            if shared_secret.startswith(b'\x00'):
                return try_decrypt(shared_secret[1:])
            raise


class WebPushClient:
    """Observable, resumable push transport; it never queries program pages."""
    def __init__(self, cookies_fn, on_notification, storage_path=None, on_status=None):
        self.cookies_fn = cookies_fn
        self.on_notification = on_notification
        self.on_status = on_status or (lambda state: None)
        self.storage_path = Path(storage_path) if storage_path else app_dir() / 'push-session.json'
        self.crypto = WebPushCrypto()
        self.uaid = self.channel_id = self.endpoint = None
        self.registered_for = ''
        self.ws = self.thread = None
        self.running = False
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.delivered = deque(maxlen=500)
        self.registration_result = {'state': 'registration_failed'}
        self.load_state()

    def report(self, state, **details):
        self.on_status(dict(state=state, **details))

    def load_state(self):
        try:
            if self.storage_path.stat().st_size > 65536:
                return
            data = json.loads(self.storage_path.read_text(encoding='utf-8'))
            fingerprint = session_fingerprint(self.cookies_fn())
            if not fingerprint or data.get('registered_for') != fingerprint:
                # A channel belongs to one account session. Reusing it for a new
                # login would relabel queued notifications from the previous user.
                return
            crypto = data['crypto']
            auth = base64.b64decode(crypto['auth'], validate=True)
            if len(auth) != 16 or not isinstance(crypto['d'], int):
                return
            key = ECC.construct(curve='P-256', d=crypto['d'])
            endpoint = urlparse(data['endpoint'])
            if endpoint.scheme != 'https' or endpoint.hostname != 'updates.push.services.mozilla.com':
                return
            if not all(isinstance(data.get(k), str) and data[k] for k in ('uaid','channel_id')):
                return
            self.crypto = WebPushCrypto(key, auth)
            self.uaid, self.channel_id, self.endpoint = data['uaid'], data['channel_id'], data['endpoint']
            self.registered_for = str(data.get('registered_for') or '')
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass

    def save_state(self):
        try:
            atomic_json(self.storage_path, {'uaid':self.uaid, 'channel_id':self.channel_id,
                        'endpoint':self.endpoint, 'crypto':self.crypto.to_dict(),
                        'registered_for':self.registered_for}, backup=False)
        except OSError:
            self.report('storage_failed')

    def start(self):
        with self.lock:
            if self.running:
                return
            self.running = True
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, daemon=True, name='webpush-client')
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        with self.lock:
            self.running = False
            ws = self.ws
        if ws:
            # The close handshake may wait for the peer. Never wait on Tk.
            threading.Thread(target=self._close_socket,args=(ws,),daemon=True).start()

    @staticmethod
    def _close_socket(ws):
        try:
            ws.close()
        except Exception:
            pass

    def register_to_niconico(self, endpoint):
        if self.stop_event.is_set():
            return False
        cookies = self.cookies_fn()
        if not session_fingerprint(cookies):
            self.registration_result = {'state':'login_required'}
            return False
        try:
            response = requests.post(NICONICO_REGISTER_ENDPOINT,
                json={'destApp':'nico_account_webpush', 'endpoint':{
                    'endpoint':endpoint, 'auth':self.crypto.auth, 'p256dh':self.crypto.p256dh}},
                headers={'Content-Type':'application/json',
                    'X-Request-With':'https://account.nicovideo.jp/my/account',
                    'Accept':'application/json', 'X-Frontend-Id':'8',
                    'Origin':'https://account.nicovideo.jp', 'Referer':'https://account.nicovideo.jp/',
                    'User-Agent':'Mozilla/5.0 NicoLiveStudio/1.0'},
                cookies={c['name']:c['value'] for c in cookies if c.get('name') in ('user_session','user_session_secure')},
                timeout=(8,15), allow_redirects=False)
            status = response.status_code
            if status == 200 and response.json().get('meta',{}).get('status') == 200:
                return True
            self.registration_result = {'state':'rate_limited' if status==429 else
                'login_required' if status==401 else 'registration_failed', 'http_status':status}
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            self.registration_result = {'state':'registration_failed'}
        return False

    def handle_notification(self, ws, data):
        if data.get('channelID') != self.channel_id:
            return
        version = data.get('version')
        code = 100
        try:
            if not isinstance(version, (str,int)) or isinstance(version,bool):
                return
            if version not in self.delivered:
                raw = b64url_decode(data.get('data',''))
                if len(raw) > 65536:
                    raise ValueError('Oversized payload')
                payload = json.loads(self.crypto.decrypt(raw))
                if not isinstance(payload,dict):
                    raise ValueError('Invalid payload')
                self.on_notification(payload)
                self.delivered.append(version)
                self.report('received')
        except (ValueError, TypeError, KeyError, IndexError, UnicodeError):
            code = 101
            self.report('decode_failed')
        ws.send(json.dumps({'messageType':'ack','updates':[
            {'channelID':self.channel_id, 'version':version, 'code':code}]}))

    def listen(self, ws):
        last_ping = float('-inf')
        while self.running and not self.stop_event.is_set():
            try:
                msg = ws.recv()
            except websocket.WebSocketTimeoutException:
                # Idle is normal. Keep the same subscription instead of reconnecting
                # every recv timeout (which also created needless registration churn).
                now = time.monotonic()
                # AutoPush permits at most one application ping per minute.
                if now - last_ping >= 65:
                    ws.send('{}')
                    last_ping = now
                continue
            if not msg:
                raise ConnectionError('Push connection closed')
            data = json.loads(msg)
            if isinstance(data,dict) and data.get('messageType') == 'notification':
                self.handle_notification(ws, data)

    def _run(self):
        backoff = 5
        try:
            while self.running and not self.stop_event.is_set():
                ws = None
                try:
                    self.report('connecting')
                    # Mozilla accepts this header but does not echo a negotiated
                    # subprotocol; passing subprotocols= would reject its handshake.
                    ws = websocket.create_connection(PUSH_SERVICE,
                        header=['Sec-WebSocket-Protocol: push-notification'], timeout=30)
                    self.ws = ws
                    if self.stop_event.is_set():break
                    hello = {'messageType':'hello','broadcasts':None,'use_webpush':True}
                    if self.uaid:
                        hello['uaid'] = self.uaid
                    ws.send(json.dumps(hello))
                    response = json.loads(ws.recv())
                    if self.stop_event.is_set():break
                    if response.get('messageType') != 'hello' or response.get('status') != 200 or not response.get('uaid'):
                        raise ConnectionError('Push handshake failed')
                    if self.uaid != response['uaid'] or not self.endpoint:
                        self.uaid = response['uaid']
                        self.channel_id = str(uuid.uuid4())
                        self.delivered.clear()
                        self.registered_for = ''
                        ws.send(json.dumps({'messageType':'register','channelID':self.channel_id,'key':NICONICO_VAPID}))
                        response = json.loads(ws.recv())
                        if self.stop_event.is_set():break
                        if response.get('status') != 200 or not response.get('pushEndpoint'):
                            raise ConnectionError('Push channel registration failed')
                        self.endpoint = response['pushEndpoint']
                    fingerprint = session_fingerprint(self.cookies_fn())
                    if self.stop_event.is_set():break
                    if not fingerprint or self.registered_for != fingerprint:
                        self.report('registering')
                        if not self.register_to_niconico(self.endpoint):
                            self.on_status(dict(self.registration_result))
                            break  # Explicit retry/login change only after refusal.
                        self.registered_for = fingerprint
                        if self.stop_event.is_set():
                            break
                        self.save_state()
                    backoff = 5
                    self.report('ready')
                    self.listen(ws)
                except Exception:
                    if not self.stop_event.is_set():
                        self.report('disconnected', retry_seconds=backoff)
                finally:
                    if ws:
                        try:
                            ws.close()
                        except Exception:
                            pass
                    self.ws = None
                if self.stop_event.wait(backoff):
                    break
                backoff = min(300, backoff*2)
        finally:
            self.running = False
