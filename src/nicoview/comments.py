"""Small, bounded reader for the published N Air NDGR protobuf wire schema.

Schema reference: n-air-app/nicolive-comment-protobuf @ 2e852e08df016888aefa602ec6c5be6a558eb3d9.
Unknown protobuf fields are skipped; only routing, IDs, chat text and colors are used.
"""
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import threading
import time
from urllib.parse import urlsplit

MAX_FRAME = 1024 * 1024


def varint(data, offset=0):
    n = 0
    for i in range(10):
        if offset+i >= len(data): raise EOFError()
        b = data[offset+i]
        n |= (b & 127) << (7*i)
        if b < 128: return n, offset+i+1
    raise ValueError('Invalid varint')


class Frames:
    def __init__(self): self.buffer = bytearray()
    def feed(self, data):
        self.buffer.extend(data); result=[]
        while self.buffer:
            try: length, offset = varint(self.buffer)
            except EOFError: break
            if length > MAX_FRAME: raise ValueError('Oversized protobuf frame')
            if len(self.buffer) < offset+length: break
            result.append(bytes(self.buffer[offset:offset+length]))
            del self.buffer[:offset+length]
        return result


def fields(data):
    result={}; offset=0
    try:
        while offset < len(data):
            tag, offset = varint(data, offset)
            number, kind = tag >> 3, tag & 7
            if not number: raise ValueError('Invalid tag')
            if kind == 0: value, offset = varint(data, offset)
            elif kind in (1,2,5):
                if kind == 2: size, offset = varint(data,offset)
                else: size = 8 if kind==1 else 4
                if offset+size > len(data): raise ValueError('Truncated field')
                value=bytes(data[offset:offset+size]); offset+=size
            else: raise ValueError('Unsupported wire type')
            result[number]=value
    except EOFError as exc: raise ValueError('Truncated protobuf') from exc
    return result


def decode_entry(data):
    f=fields(data)
    if 1 in f: return 'segment', fields(f[1]).get(3,b'').decode('utf-8')
    if 2 in f: return 'backward',fields(fields(f[2]).get(2,b'')).get(1,b'').decode('utf-8')
    if 3 in f: return 'previous',fields(f[3]).get(3,b'').decode('utf-8')
    if 4 in f: return 'next', str(fields(f[4]).get(1,0))
    return None


@dataclass(frozen=True)
class Comment:
    id: str
    text: str
    received_at: float
    color: str = "FFFFFF"
    user_id: str = ""
    position: float | None = None
    timestamp: float | None = None


# NDGR ColorName order (standard and premium palette), in RGB.
COLORS = ('FFFFFF','FF0000','FF8080','FFC000','FFFF00','00FF00','00FFFF',
          '0000FF','C000FF','000000','CCCC99','CC0033','FF33CC','FF6600',
          '999900','00CC66','00CCCC','3399FF','6633CC','666666')


def decode_color(modifier):
    try:
        f=fields(modifier)
        if 4 in f:
            rgb=fields(f[4]); values=[rgb.get(i,0) for i in (1,2,3)]
            if all(isinstance(v,int) and 0<=v<=255 for v in values):
                return ''.join(f'{v:02X}' for v in values)
            return 'FFFFFF'
        index=f.get(3,0)
        return COLORS[index] if isinstance(index,int) and 0<=index<len(COLORS) else 'FFFFFF'
    except (ValueError,TypeError):
        return 'FFFFFF'


def decode_comment(data):
    f=fields(data)
    if 2 not in f: return None
    message=fields(f[2]); chat=message.get(1, message.get(20))
    if not isinstance(chat,bytes): return None
    chat_fields=fields(chat)
    content=chat_fields.get(1,b'')
    if not isinstance(content,bytes): return None
    text=content.decode('utf-8',errors='replace')[:1000]
    if not text: return None
    meta=fields(f.get(1,b''))
    ident=meta.get(1,b'').decode('utf-8',errors='replace') or hashlib.sha256(data).hexdigest()
    user=chat_fields.get(6,b'')
    user=user.decode('utf-8',errors='replace') if isinstance(user,bytes) else ''
    if not user and isinstance(chat_fields.get(5),int):user=str(chat_fields[5])
    position=chat_fields.get(3)
    if isinstance(position,int):
        if position & (1<<31):position-=1<<32
        position=position/100
    else:position=None
    stamp=fields(meta.get(2,b''))
    timestamp=stamp.get(1,0)+stamp.get(2,0)/1e9 if stamp else None
    return Comment(ident,text,time.monotonic(),decode_color(chat_fields.get(7,b'')),user,position,timestamp)


class RecentIDs:
    def __init__(self, limit=2000): self.values=OrderedDict(); self.limit=limit
    def add(self, value):
        if value in self.values: return False
        self.values[value]=None
        if len(self.values)>self.limit: self.values.popitem(last=False)
        return True


def allowed_uri(uri):
    p=urlsplit(uri)
    return (p.scheme=='https' and p.hostname=='mpn.live.nicovideo.jp'
            and not p.username and not p.password and p.port in (None,443))


class CommentReceiver:
    def __init__(self, report=lambda text:None):
        self.stop_event=threading.Event(); self.report=report
        self.lock=threading.Lock(); self.ids=RecentIDs(); self.segments=RecentIDs(200)
        self.active=set(); self.thread=None
        self.slots=threading.BoundedSemaphore(4);self.workers=set()

    def start(self, connection_info, session, emit):
        self.uri=connection_info['viewUri']; self.emit=emit
        self.archive=bool(connection_info.get('archive',False))
        self.archive_at=connection_info.get('at','now')
        self.cookies=session.http.cookies.copy()
        self.thread=threading.Thread(target=self._views,daemon=True); self.thread.start()

    def _fetch(self, uri, params=None):
        import requests
        if not allowed_uri(uri): raise ValueError('Invalid comment endpoint')
        with requests.Session() as http:
            http.cookies.update(self.cookies)
            # View streams can remain open for 32 seconds; segment streams for 24.
            with http.get(uri,params=params,stream=True,timeout=(8,40),allow_redirects=False) as response:
                response.raise_for_status()
                if response.is_redirect: raise ValueError('Unexpected redirect')
                parser=Frames()
                for data in response.iter_content(1024):
                    if self.stop_event.is_set(): return
                    for frame in parser.feed(data): yield frame

    def _segment(self, uri):
        delay=1
        try:
            for attempt in range(3):
                if self.stop_event.is_set():return
                try:
                    for frame in self._fetch(uri):
                        c=decode_comment(frame)
                        with self.lock: fresh = c is not None and self.ids.add(c.id)
                        if fresh and not self.stop_event.is_set(): self.emit(c)
                    return
                except ValueError:
                    if not self.stop_event.is_set():self.report('読み取れないコメント区間をスキップしました。')
                    return
                except Exception as exc:
                    response=getattr(exc,'response',None)
                    if response is not None and response.status_code in (400,401,403,404,410):return
                    if attempt==2 or self.stop_event.is_set():return
                    self.report('コメント接続を再試行しています…')
                    if self.stop_event.wait(delay): return
                    delay=min(delay*2,30)
        finally:
            with self.lock: self.active.discard(uri)

    def _views(self):
        at=getattr(self,'archive_at','now'); delay=1
        while not self.stop_event.is_set():
            try:
                for frame in self._fetch(self.uri, {'at':at}):
                    item=decode_entry(frame)
                    if not item: continue
                    kind,value=item
                    if kind=='next': at=value
                    elif kind=='backward' and getattr(self,'archive',False):
                        self._backward(value)
                    elif kind=='segment' or (kind=='previous' and getattr(self,'archive',False)):
                        self._schedule_segment(value)
                if getattr(self,'archive',False):
                    self.report('過去コメントを読み込みました');return
                self.report('コメント受信中')
                delay=1
                self.stop_event.wait(.3)
            except Exception:
                self.report('コメント接続を再試行しています（映像は継続）')
                if self.stop_event.wait(delay): return
                delay=min(delay*2,30)

    def _schedule_segment(self,uri):
        # Backpressure preserves one-pass archives while bounding concurrency.
        while not self.stop_event.is_set():
            if self.slots.acquire(timeout=.1):break
        else:return
        with self.lock:
            if not self.segments.add(uri):
                self.slots.release();return
            self.active.add(uri)
        def work():
            try:self._segment(uri)
            finally:
                with self.lock:self.workers.discard(threading.current_thread())
                self.slots.release()
        thread=threading.Thread(target=work,daemon=True)
        with self.lock:
            self.workers.add(thread)
            thread.start()

    def wait_complete(self,stop=None):
        # View traversal owns packed archives; it must finish before its children.
        while True:
            if stop and stop.is_set():return False
            with self.lock:workers=list(self.workers)
            if self.thread and self.thread.is_alive():workers.append(self.thread)
            if not workers:return True
            for thread in workers:thread.join(timeout=.1)

    def _backward(self,uri):
        """PackedSegment is one unframed protobuf with repeated message field 1."""
        import requests
        visited=set()
        with requests.Session() as http:
            http.cookies.update(self.cookies)
            for _ in range(10000):
                if not uri or uri in visited or self.stop_event.is_set():return
                if not allowed_uri(uri):raise ValueError('Invalid archive endpoint')
                visited.add(uri)
                with http.get(uri,stream=True,timeout=(8,40),allow_redirects=False) as response:
                    response.raise_for_status()
                    if response.is_redirect:raise ValueError('Unexpected redirect')
                    data=bytearray()
                    for chunk in response.iter_content(65536):
                        if self.stop_event.is_set():return
                        data.extend(chunk)
                        if len(data)>16*1024*1024:raise ValueError('Oversized archive segment')
                messages,next_uri=decode_packed(bytes(data))
                for c in messages:
                    with self.lock:fresh=self.ids.add(c.id)
                    if fresh and not self.stop_event.is_set():self.emit(c)
                uri=next_uri

    def stop(self):
        self.stop_event.set()
        self.cookies.clear() if hasattr(self,'cookies') else None


def decode_packed(data):
    messages=[];next_uri='';offset=0
    while offset<len(data):
        tag,offset=varint(data,offset);kind=tag&7;number=tag>>3
        if kind==0:_,offset=varint(data,offset);continue
        if kind not in (1,2,5):raise ValueError('Invalid packed wire type')
        if kind==2:size,offset=varint(data,offset)
        else:size=8 if kind==1 else 4
        if offset+size>len(data):raise ValueError('Truncated packed segment')
        value=data[offset:offset+size];offset+=size
        if number==1 and kind==2:
            comment=decode_comment(value)
            if comment:messages.append(comment)
        elif number==2 and kind==2:next_uri=fields(value).get(1,b'').decode('utf-8')
    return messages,next_uri
