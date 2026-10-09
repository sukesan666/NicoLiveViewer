"""Niconico session-only persistence under Windows DPAPI CurrentUser."""
import json
import math
import os
from pathlib import Path
import tempfile
import time
from .credentials import protect, unprotect

MAGIC=b'NICOVIEW-SESSION-1\0'

def clean_cookies(cookies):
    if not isinstance(cookies,list):return []
    result=[]
    for c in cookies[:32]:
        if not isinstance(c,dict):continue
        if c.get('name') not in ('user_session','user_session_secure'):continue
        if c.get('domain') not in ('.nicovideo.jp','nicovideo.jp','live.nicovideo.jp','.live.nicovideo.jp'):continue
        if c.get('path','/')!='/':continue
        value=c.get('value')
        if not isinstance(value,str) or not value or len(value)>8192 or any(ord(ch)<33 or ord(ch)>126 for ch in value):continue
        try:expires=float(c.get('expires') if c.get('expires') is not None else -1)
        except (TypeError,ValueError):continue
        if not math.isfinite(expires) or (expires>=0 and expires<=time.time()):continue
        # Requests' get(name) is ambiguous with multiple domains. Prefer the
        # first applicable browser cookie, bounded to the two session names.
        if any(old['name']==c['name'] for old in result):continue
        result.append(dict(name=c['name'],value=value,domain=c['domain'],path='/',secure=True,
                           expires=int(expires) if expires>0 else None))
    return result

class SessionStore:
    def __init__(self,path):self.path=Path(path)
    def save(self,cookies):
        cookies=clean_cookies(cookies)
        if not any(c['name']=='user_session' for c in cookies):raise ValueError('有効なログインセッションがありません。')
        data=MAGIC+protect(json.dumps(cookies).encode('utf-8'))
        self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent,prefix='.session-',delete=False) as f:
                tmp=Path(f.name);f.write(data);f.flush();os.fsync(f.fileno())
            tmp.replace(self.path)
        finally:
            if tmp:tmp.unlink(missing_ok=True)
    def load(self):
        try:
            if self.path.stat().st_size>131072:raise ValueError()
            raw=self.path.read_bytes()
        except FileNotFoundError:return None
        try:
            if not raw.startswith(MAGIC):raise ValueError()
            data=json.loads(unprotect(raw[len(MAGIC):]))
            # JSON null represents a browser session cookie, not an expired cookie.
            if isinstance(data,list):
                for c in data:
                    if isinstance(c,dict) and c.get('expires') is None:c['expires']=-1
            result=clean_cookies(data)
            return result if any(c['name']=='user_session' for c in result) else None
        except (ValueError,OSError,UnicodeError):
            raise ValueError('保存したセッションを読み込めません。ブラウザでログインし直してください。') from None
    def delete(self):self.path.unlink(missing_ok=True)
