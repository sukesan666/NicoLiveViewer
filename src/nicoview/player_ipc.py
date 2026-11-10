import json
import os
import threading
import time

class PlayerIPC:
    """One connection per player. A reader drains replies to avoid pipe deadlocks."""
    def __init__(self, path):
        self.path=path; self.file=None; self.reader=None; self.lock=threading.Lock(); self.closed=False
        self.read_lock=threading.RLock()
        self.properties={};self.buffer=b'';self.events=[]
        self._windows_pipe=None

    def connect(self, stop=None, timeout=8):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline and not (stop and stop.is_set()):
            try:
                if os.name=='nt':
                    from .windows_pipe import WindowsPipe
                    self.file=self.reader=self._windows_pipe=WindowsPipe(self.path)
                else:
                    self.file=open(self.path,'r+b',buffering=0)
                    self.reader=os.fdopen(os.dup(self.file.fileno()),'rb',buffering=0)
                threading.Thread(target=self._drain,daemon=True).start()
                return
            except OSError: time.sleep(.1)
        raise TimeoutError('プレイヤーに接続できませんでした。')

    def _read_available(self):
        return self._windows_pipe.read_available() if self._windows_pipe else b''

    def _drain(self):
        try:
            if os.name=='nt':
                while not self.closed:
                    with self.read_lock:
                        if self.closed:break
                        data=self._read_available()
                        if data:self._consume(data)
                    if data is None:break
                    if not data:time.sleep(.01)
            else:
                while not self.closed:
                    data=self.reader.read(4096)
                    if not data:break
                    self._consume(data)
        except (OSError,ValueError): pass
        finally:
            if self.reader:
                try:self.reader.close()
                except OSError:pass

    def _consume(self,data):
        with self.read_lock:
            self.buffer+=data
            if len(self.buffer)>1024*1024:self.buffer=b'';return
            while b'\n' in self.buffer:
                line,self.buffer=self.buffer.split(b'\n',1)
                try:message=json.loads(line)
                except (ValueError,UnicodeError):continue
                if message.get('event')=='property-change':
                    self.properties[message.get('name')]=message.get('data')
                elif message.get('event') in ('end-file','seek','playback-restart'):
                    self.events.append(message)
                    self.events=self.events[-100:]

    def observe(self):
        for i,name in enumerate(('time-pos','duration','pause','seekable','eof-reached','volume','mute','paused-for-cache','core-idle'),1):
            self.send(['observe_property',i,name])

    def snapshot(self):
        with self.read_lock:
            return self.properties.copy()

    def take_events(self):
        with self.read_lock:
            events,self.events=self.events,[]
        return events

    def send(self, command):
        data=(json.dumps({'command':command},ensure_ascii=False)+'\n').encode('utf-8')
        with self.lock:
            if not self.file or self.closed: raise OSError('Player is not connected')
            if os.name=='nt':
                with self.read_lock:
                    incoming=self._read_available()
                    if incoming:self._consume(incoming)
            self.file.write(data)

    def close(self):
        self.closed=True
        if self.file:
            try: self.file.close()
            except OSError: pass
