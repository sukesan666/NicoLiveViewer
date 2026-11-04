"""Bounded disk thumbnails with shared downloads and least-recent-use eviction."""
from collections import OrderedDict
from concurrent.futures import Future
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time

THUMBNAIL_LIMIT=1000


class ThumbnailCache:
    def __init__(self,directory,max_items=THUMBNAIL_LIMIT,max_age_days=30):
        self.directory=Path(directory)
        self.max_items=max_items
        self.max_age_days=max_age_days
        self.lock=threading.RLock()
        self.entries=None
        self.inflight={}

    @staticmethod
    def key(url):
        return hashlib.sha256(str(url).encode('utf-8')).hexdigest()+'.png'

    def _prepare(self,force=False):
        if self.entries is not None and not force:return
        entries=[]
        threshold=time.time()-self.max_age_days*86400 if self.max_age_days is not None else None
        try:
            self.directory.mkdir(parents=True,exist_ok=True)
            for path in self.directory.glob('*.png'):
                try:
                    stat=path.stat();used=max(stat.st_mtime,stat.st_atime)
                    if threshold is not None and used<threshold:path.unlink(missing_ok=True)
                    else:entries.append((used,path.name))
                except OSError:pass
        except OSError:pass
        self.entries=OrderedDict((name,None) for _,name in sorted(entries))
        self._evict()

    def _evict(self):
        while len(self.entries)>self.max_items:
            name,_=self.entries.popitem(last=False)
            try:(self.directory/name).unlink(missing_ok=True)
            except OSError:pass

    def prune(self):
        with self.lock:self._prepare(force=True)

    def _read(self,key):
        self._prepare()
        path=self.directory/key
        try:
            data=path.read_bytes()
            if not data:return None
        except OSError:
            self.entries.pop(key,None)
            return None
        try:
            now=time.time();os.utime(path,(now,now))
        except OSError:pass
        self.entries[key]=None;self.entries.move_to_end(key)
        self._evict()
        return data

    def read(self,url):
        """Read cached bytes only; this method cannot make a network request."""
        with self.lock:return self._read(self.key(url))

    def _store(self,key,data):
        temporary=None
        try:
            self.directory.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.directory,prefix='.thumbnail-',suffix='.tmp',delete=False) as output:
                temporary=Path(output.name);output.write(data)
            temporary.replace(self.directory/key)
            self.entries[key]=None;self.entries.move_to_end(key)
            self._evict()
        except OSError:pass
        finally:
            if temporary:
                try:temporary.unlink(missing_ok=True)
                except OSError:pass

    def get(self,url,fetch,cancel=None):
        """fetch(url, cancelled) starts only while a subscriber still needs it."""
        key=self.key(url)
        with self.lock:
            if cancel is not None and cancel.is_set():return None
            cached=self._read(key)
            if cached:return cached
            flight=self.inflight.get(key)
            owner=flight is None
            if owner:
                flight={'future':Future(),'subscribers':[]};self.inflight[key]=flight
            flight['subscribers'].append(cancel)
            future=flight['future']
        if not owner:return future.result()
        aborted=False
        def cancelled():
            nonlocal aborted
            with self.lock:
                stopped=all(event is not None and event.is_set() for event in flight['subscribers'])
                aborted=aborted or stopped
                return stopped
        try:
            while True:
                aborted=False
                data=fetch(url,cancelled)
                with self.lock:
                    # An active consumer may have joined just after an obsolete
                    # owner was refused network admission. Serve that consumer.
                    if data is None and aborted and not cancelled():continue
                    if data:self._store(key,data)
                    future.set_result(data)
                    if self.inflight.get(key) is flight:self.inflight.pop(key,None)
                    return data
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            with self.lock:
                if self.inflight.get(key) is flight:self.inflight.pop(key,None)
