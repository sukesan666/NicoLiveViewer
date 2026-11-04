"""Interpret live-start push metadata without fetching a program page.

Icon layouts also used by guest-nico/nicoLiveCheckTool's PushReceiver.getItem.
This parser is independent of its implementation and does not execute payloads.
"""
from datetime import datetime
import math
import re
import time
from urllib.parse import parse_qs, urlsplit


def parse_notification(payload, now=None):
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), dict):
        return None
    now = time.time() if now is None else now
    data = payload['data']
    try:
        url = urlsplit(str(data.get('on_click', '')))
        if (url.scheme not in ('http', 'https') or url.hostname not in
                ('live.nicovideo.jp', 'live2.nicovideo.jp') or url.username or url.password
                or url.port not in (None, 80 if url.scheme == 'http' else 443)):
            return None
        match = re.fullmatch(r'/watch/(lv[0-9]+)/?', url.path)
        if not match:
            return None
        params = data.get('log_params')
        params = params if isinstance(params, dict) else {}
        kind = params.get('content_type') or parse_qs(url.query).get('_topic', [''])[0].replace('_', '.')
        if kind not in ('live.user.program.onairs', 'live.channel.program.onairs', 'live.official.program.onairs'):
            return None
        ttl = min(600, max(0, float(data.get('ttl', 600))))
        if not math.isfinite(ttl):
            return None
        created = datetime.fromisoformat(str(data['created_at']).replace('Z', '+00:00')) if data.get('created_at') else None
        if created and created.tzinfo is None:
            return None
        stamp = created.timestamp() if created else now
        if stamp > now + 120 or stamp + ttl <= now:
            return None
        icon = urlsplit(str(payload.get('icon') or ''))
        user_id = str(data.get('user_id') or params.get('user_id') or '')
        user_id = user_id if re.fullmatch(r'[0-9]{1,20}', user_id) else ''
        channel_id = ''
        if icon.hostname in ('secure-dcdn.cdn.nimg.jp', 'dcdn.cdn.nimg.jp', 'img.nicoprofile.nimg.jp'):
            user = re.fullmatch(r'/(?:nicoaccount/)?usericon/(?:s/)?(?:[0-9]+/)?([0-9]{1,20})\.(?:jpg|png|gif|webp)', icon.path)
            channel = re.fullmatch(r'/comch/channel-icon/[0-9]+x[0-9]+/(ch[0-9]+)\.(?:jpg|png|gif|webp)', icon.path)
            if user and not user_id:
                user_id = user[1]
            if channel:
                channel_id = channel[1]
        body = str(payload.get('body') or '')[:2000]
        title_match = re.search(r'「(.*)」を放送', body)
        title = title_match[1] if title_match else body or str(payload.get('title') or match[1])
        return dict(id=match[1], url='https://live.nicovideo.jp/watch/' + match[1],
                    title=title[:1000], user_id=user_id, channel_id=channel_id,
                    provider='user' if kind == 'live.user.program.onairs' else 'channel' if channel_id else 'official',
                    status='ON_AIR', start=stamp, expires_at=stamp + ttl)
    except (TypeError, ValueError, OverflowError):
        return None
