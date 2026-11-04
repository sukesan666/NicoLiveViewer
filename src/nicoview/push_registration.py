"""One follow request per reserved user/channel and signed-in session.

User endpoint observed in guest-nico/nicoLiveCheckTool FollowUser.join3.
Channel endpoint/body/headers follow the official live player (November 2026).
Requests change account follows; callers must only use explicitly reserved targets.
"""
import hashlib
import json
from pathlib import Path
import re
import threading
import requests

from .preferences import atomic_json


CHANNEL_FOLLOW_ENDPOINT = 'https://public-api.ch.nicovideo.jp/v1/user/follow'
CHANNEL_FOLLOW_STATE_ENDPOINT = 'https://public-api.ch.nicovideo.jp/v1/user/followees/channel/'
NANIME_FOLLOW_PAGE = 'https://anime.nicovideo.jp/live/reserved-ikkyo.html?from=ch_article_67'


def session_fingerprint(cookies):
    token = next((c.get('value', '') for c in cookies if c.get('name') == 'user_session'), '')
    return hashlib.sha256(token.encode()).hexdigest() if token else ''


class FollowSubscriptions:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.session = ''
        self.users = set()
        self.channels = set()
        self.failures = {}
        self.paused = None
        try:
            if self.path.stat().st_size > 65536:
                return
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.session = str(data.get('session_hash') or '')
            users = data.get('users', [])
            if isinstance(users, list):
                self.users = {u for u in users[:500] if isinstance(u, str) and re.fullmatch(r'[0-9]{1,20}', u)}
            channels = data.get('channels', [])
            if isinstance(channels, list):
                self.channels = {c for c in channels[:500] if isinstance(c, str) and re.fullmatch(r'ch[0-9]{1,20}', c)}
        except (OSError, ValueError, TypeError, AttributeError):
            self.users = set()
            self.channels = set()

    def reset_failures(self, clear_receipts=False):
        with self.lock:
            self.failures.clear()
            self.paused = None
            if clear_receipts:
                self.users.clear()
                self.channels.clear()

    def _remember_followed(self, target, subscribed):
        subscribed.add(target)
        try:
            atomic_json(self.path, {'session_hash': self.session,
                                   'users': sorted(self.users),
                                   'channels': sorted(self.channels)}, backup=False)
        except OSError:
            # Keep the in-memory receipt; a disk error must not repeat a successful request.
            return {'ok': True, 'state': 'followed_unsaved'}
        return {'ok': True, 'state': 'followed'}

    def _failure(self, target, stage, status=None, *, reason='http_error',
                 api_status=None, stop_batch=None):
        code = api_status if api_status is not None else status
        state = ('login_required' if reason == 'login_required' else
                 {401: 'login_required', 403: 'access_denied', 429: 'rate_limited'}.get(code, 'registration_failed'))
        if stop_batch is None:
            stop_batch = code in (401, 403, 429) or isinstance(code, int) and (300 <= code < 400 or code >= 500)
        result = {'ok': False, 'state': state, 'stage': stage,
                  'reason': reason, 'stop_batch': bool(stop_batch)}
        if status is not None:
            result['http_status'] = status
        if api_status is not None:
            result['api_status'] = api_status
        self.failures[target] = result
        if stop_batch:
            self.paused = result
        return dict(result)

    def follow(self, target, cookies, target_type=None, *, cancel=None):
        if not isinstance(target, str):
            return {'ok': False, 'state': 'invalid_target'}
        if target_type is None:
            target_type = 'channel' if target.startswith('ch') else 'user'
        if target_type == 'channel':
            if not re.fullmatch(r'ch[0-9]{1,20}', target) and not re.fullmatch(r'[0-9]{1,20}', target):
                return {'ok': False, 'state': 'invalid_target'}
            target_id = target if target.startswith('ch') else f'ch{target}'
            numeric_id = target[2:] if target.startswith('ch') else target
        else:
            if not re.fullmatch(r'[0-9]{1,20}', target):
                return {'ok': False, 'state': 'invalid_target'}
            target_id = target
            numeric_id = target

        fingerprint = session_fingerprint(cookies)
        if not fingerprint:
            return {'ok': False, 'state': 'login_required'}
        with self.lock:
            if self.session != fingerprint:
                self.session = fingerprint
                self.users.clear()
                self.channels.clear()
                self.failures.clear()
                self.paused = None
            subscribed = self.channels if target_type == 'channel' else self.users
            if target_id in subscribed:
                return {'ok': True, 'state': 'followed', 'cached': True}
            if target_id in self.failures:
                return dict(self.failures[target_id])
            if self.paused:
                # This target was never requested. Do not attribute another target's
                # HTTP error to it; successful cached follows remain usable above.
                return {'ok': False, 'state': 'follow_deferred', 'reason': 'batch_paused'}
            stage = 'channel_follow' if target_type == 'channel' else 'user_follow'
            status = None
            try:
                if cancel is not None and cancel.is_set():
                    return {'ok': False, 'state': 'follow_cancelled'}
                if target_type == 'channel':
                    # Match Nアニメ's working ch67 button. Other channels retain
                    # the official live-player frontend identity.
                    nanime = int(numeric_id) == 67
                    origin = 'https://anime.nicovideo.jp' if nanime else 'https://live.nicovideo.jp'
                    referer = NANIME_FOLLOW_PAGE if nanime else origin + '/'
                    headers = {'X-Frontend-Id': '122' if nanime else '9',
                               'Origin': origin, 'Referer': referer}
                    cookie_dict = {c['name']: c['value'] for c in cookies if c.get('name') in ('user_session', 'user_session_secure')}
                    # One setup check per uncached channel, never periodic polling.
                    # This endpoint returns a real boolean for the signed-in account.
                    stage = 'channel_status'
                    response = requests.get(
                        CHANNEL_FOLLOW_STATE_ENDPOINT + str(int(numeric_id)),
                        headers=headers, cookies=cookie_dict,
                        timeout=(8, 15), allow_redirects=False)
                    if cancel is not None and cancel.is_set():
                        return {'ok': False, 'state': 'follow_cancelled'}
                    status = response.status_code
                    if status != 200:
                        return self._failure(target_id, stage, status)
                    state = response.json()
                    meta = state.get('meta') if isinstance(state, dict) else None
                    data = state.get('data') if isinstance(state, dict) else None
                    api_status = meta.get('status') if isinstance(meta, dict) else None
                    if isinstance(api_status, int) and api_status >= 400:
                        return self._failure(target_id, stage, status, api_status=api_status)
                    if api_status != 200 or not isinstance(data, dict) or not isinstance(data.get('following'), bool):
                        return self._failure(target_id, stage, status, reason='invalid_response', stop_batch=True)
                    if data['following']:
                        return self._remember_followed(target_id, subscribed)
                    if cancel is not None and cancel.is_set():
                        return {'ok': False, 'state': 'follow_cancelled'}
                    stage = 'channel_follow'
                    status = None
                    post_headers = {**headers, 'Content-Type': 'application/json; charset=utf-8',
                                    'X-Requested-With': referer if nanime else origin}
                    if not nanime:
                        post_headers.update({'Accept': 'application/json', 'X-Frontend-Version': '658.2.0'})
                    response = requests.post(
                        CHANNEL_FOLLOW_ENDPOINT,
                        json={'mode': 'add', 'channelId': int(numeric_id)},
                        headers=post_headers, cookies=cookie_dict,
                        timeout=(8, 15), allow_redirects=False)
                    status = response.status_code
                    if not 200 <= status < 300:
                        return self._failure(target_id, stage, status)
                    if status != 204:
                        data = response.json()  # The official client also requires valid JSON.
                        meta = data.get('meta') if isinstance(data, dict) else None
                        api_status = meta.get('status') if isinstance(meta, dict) else None
                        if isinstance(api_status, int) and api_status >= 400:
                            return self._failure(target_id, stage, status, api_status=api_status)
                    return self._remember_followed(target_id, subscribed)
                else:
                    url = 'https://user-follow-api.nicovideo.jp/v1/user/followees/niconico-users/' + numeric_id + '.json'
                    response = requests.post(
                        url,
                        headers={'X-Frontend-Id': '6', 'X-Frontend-Version': '0',
                                 'X-Request-With': 'https://www.nicovideo.jp', 'Referer': 'https://www.nicovideo.jp/'},
                        cookies={c['name']: c['value'] for c in cookies if c.get('name') in ('user_session', 'user_session_secure')},
                        timeout=(8,15), allow_redirects=False)
                    status = response.status_code
                    if status not in (200, 201):
                        return self._failure(target_id, stage, status)
                    api_status = response.json().get('meta', {}).get('status')
                    if api_status in (200, 201):
                        return self._remember_followed(target_id, subscribed)
                    if not isinstance(api_status, int) or isinstance(api_status, bool):
                        return self._failure(target_id, stage, status, reason='invalid_response', stop_batch=True)
                    return self._failure(target_id, stage, status, api_status=api_status)
            except (ValueError, TypeError, AttributeError):
                return self._failure(target_id, stage, status, reason='invalid_response', stop_batch=True)
            except requests.RequestException:
                return self._failure(target_id, stage, status, reason='network_error', stop_batch=True)
