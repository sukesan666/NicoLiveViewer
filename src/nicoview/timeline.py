"""Playback-position based comment replay, including backward seeks."""
import bisect
import json
import math
import threading
from pathlib import Path
from .comments import Comment


def allowed_comment(comment, settings):
    return (not any(word and word.casefold() in comment.text.casefold() for word in settings.get('ng_words',[]))
            and comment.user_id not in settings.get('ng_users',[]))


class Timeline:
    def __init__(self, comments=()):
        self.lock=threading.RLock();self.items=[];self.times=[];self.ids=set();self.previous=-0.001
        for c in comments:self.add(c)

    def add(self,c):
        if c.position is None or not math.isfinite(c.position):return
        with self.lock:
            if c.id in self.ids:return
            self.ids.add(c.id)
            i=bisect.bisect_right(self.times,c.position)
            self.times.insert(i,c.position);self.items.insert(i,c)

    def seek(self,position):
        with self.lock:self.previous=max(-.001,float(position)-.001)

    def ready(self,position,offset=0):
        position=float(position)-offset
        with self.lock:
            if position<self.previous or position-self.previous>3:
                self.previous=position-.05
            start=bisect.bisect_right(self.times,self.previous)
            end=bisect.bisect_right(self.times,position)
            self.previous=position
            return self.items[start:end][-100:]

    @classmethod
    def from_sidecar(cls,video):
        path=Path(video).with_suffix('.comments.jsonl')
        result=cls()
        if not path.is_file():return result
        # Limit untrusted local sidecars, including lines with no newline.
        with path.open('r',encoding='utf-8') as f:
            for _ in range(500000):
                line=f.readline(65537)
                if not line:break
                if len(line)>65536:raise ValueError('コメントファイルが大きすぎます。')
                try:
                    row=json.loads(line);p=float(row['position'])
                    if not math.isfinite(p) or p<0:continue
                    c=Comment(str(row.get('id',''))[:200],str(row['text'])[:1000],0,
                              str(row.get('color','FFFFFF'))[:6],str(row.get('user_id',''))[:200],p)
                    result.add(c)
                except (ValueError,KeyError,TypeError):continue
        return result
