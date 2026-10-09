import re
from urllib.parse import urlsplit


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
