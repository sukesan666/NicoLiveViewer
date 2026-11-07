import re
from urllib.parse import urlsplit, quote


def normalize_url(value: str) -> str:
    value = value.strip()
    if any(ord(c) < 32 for c in value):
        raise ValueError('URLに制御文字を含めることはできません。')
    parts = urlsplit(value)
    if parts.scheme != 'https' or parts.netloc != 'live.nicovideo.jp':
        raise ValueError('https://live.nicovideo.jp/watch/lv… の番組URLを入力してください。')
    if not re.fullmatch(r'/watch/lv[0-9]+', parts.path):
        raise ValueError('番組URLの末尾は lv と数字で指定してください。')
    return 'https://live.nicovideo.jp' + parts.path


def _is_nico_web_url(url: str) -> bool:
    if any(c.isspace() or c == '\\' or ord(c) < 32 or ord(c) == 127 for c in url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = parts.hostname
    port = {'http': 80, 'https': 443}.get(parts.scheme)
    return bool(port and host in ('live.nicovideo.jp', 'live2.nicovideo.jp', 'ch.nicovideo.jp', 'www.nicovideo.jp')
                and parts.netloc.lower() in (host, f'{host}:{port}'))


def web_url_for_item(item: dict) -> str:
    if not isinstance(item, dict): return ''
    url = str(item.get('url') or '').strip()
    if _is_nico_web_url(url):
        return url
    tag = str(item.get('official_tag') or item.get('tag') or '').strip()
    if tag:
        return f"https://live.nicovideo.jp/search?keyword={quote(tag)}&isTagSearch=true&providerTypes=official"
    kw = str(item.get('keyword') or '').strip()
    if kw:
        is_official = item.get('provider') == 'official' or str(item.get('title','')).startswith('公式キーワード')
        provider = '&providerTypes=official' if is_official else ''
        return f"https://live.nicovideo.jp/search?keyword={quote(kw)}{provider}"
    ident = str(item.get('id') or '').strip()
    if re.fullmatch(r'lv[0-9]+', ident):
        return f"https://live.nicovideo.jp/watch/{ident}"
    m_lv = re.fullmatch(r'(?:/watch/)?(lv[0-9]+)/?', url)
    if m_lv:
        return f"https://live.nicovideo.jp/watch/{m_lv.group(1)}"
    ch_id = str(item.get('channel_id') or item.get('notify_channel_id') or '').strip()
    if not ch_id:
        match = re.fullmatch(r'/?(ch[0-9]+)/?', url) or re.fullmatch(r'(ch[0-9]+)', ident)
        if match: ch_id = match.group(1)
    if re.fullmatch(r'(?:ch)?[0-9]+', ch_id):
        ch = ch_id if ch_id.startswith('ch') else f'ch{ch_id}'
        return f"https://ch.nicovideo.jp/{ch}"
    user_id = str(item.get('user_id') or item.get('notify_user_id') or '').strip()
    if not user_id and ident.isdigit(): user_id = ident
    if user_id and user_id.isdigit():
        return f"https://www.nicovideo.jp/user/{user_id}"
    return ''
