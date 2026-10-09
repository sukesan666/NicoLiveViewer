"""Crash-safe transport-stream recordings and bounded JSONL comment sidecars."""
import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


def era_date(now=None):
    now=now or datetime.now()
    return f'令和{now.year-2018}年{now.month:02}月{now.day:02}日' if now.year>=2019 else now.strftime('%Y年%m月%d日')


def safe_name(value):
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_',str(value)).strip(' .')[:60].rstrip(' .') or '番組'
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])',name.split('.')[0],re.IGNORECASE):name='_'+name
    return name



class Recording:
    def __init__(self,root,metadata):
        now=datetime.now()
        folder=Path(root or str(Path.home()/'Videos'/'NicoLive Studio'))/safe_name(metadata.get('author','配信者不明'))/(era_date(now)+f' {now:%H時%M分%S秒} '+safe_name(metadata.get('title','番組'))+' '+uuid.uuid4().hex[:6])
        folder.mkdir(parents=True,exist_ok=True)
        if shutil.disk_usage(folder).free<256*1024*1024:raise ValueError('録画先の空き容量が不足しています（256MB以上必要です）。')
        base='video'
        self.path=folder/(base+'.partial.ts')
        self.metadata_path=self.path.with_suffix('.metadata.json')
        self.comment_path=self.path.with_suffix('.comments.jsonl')
        self.video=self.path.open('xb');self.comments=self.comment_path.open('x',encoding='utf-8')
        self.lock=threading.RLock();self.closed=False;self.ids=set();self.bytes=0;self.started=time.monotonic()
        self.metadata={k:str(metadata.get(k,''))[:2000] for k in ('id','url','title','author','provider')}
        self.metadata.update(state='recording',created=datetime.now().astimezone().isoformat(),path=str(self.path))
        self._metadata()

    def _metadata(self):
        tmp=self.metadata_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.metadata,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(self.metadata_path)

    def write(self,data):
        with self.lock:
            if self.closed:return
            if self.bytes//(8*1024*1024)!=(self.bytes+len(data))//(8*1024*1024) and shutil.disk_usage(self.path.parent).free<64*1024*1024:
                raise ValueError('録画先の空き容量が不足しています。')
            self.video.write(data);self.bytes+=len(data)

    def add_comment(self,comment,position):
        with self.lock:
            if self.closed:return
            if comment.id in self.ids:return
            self.ids.add(comment.id)
            row=asdict(comment);row.pop('received_at',None);row['position']=max(0,float(position))
            self.comments.write(json.dumps(row,ensure_ascii=False)+'\n');self.comments.flush()

    def finish(self,complete=False):
        with self.lock:
            if self.closed:return
            self.closed=True
            for f in (self.video,self.comments):f.flush();os.fsync(f.fileno());f.close()
            complete=complete and self.bytes>0
            if complete:
                target=self.path.with_name(self.path.name.replace('.partial.ts','.ts'))
                comment_target=target.with_suffix('.comments.jsonl')
                metadata_target=target.with_suffix('.metadata.json')
                self.path.rename(target);self.comment_path.rename(comment_target)
                old=self.metadata_path;self.path=target;self.comment_path=comment_target;self.metadata_path=metadata_target
                self.metadata.update(state='complete',path=str(target));self._metadata();old.unlink(missing_ok=True)
            else:self.metadata.update(state='interrupted',path=str(self.path))
            self.metadata.update(bytes=self.bytes,elapsed=round(time.monotonic()-self.started,3))
            self._metadata()
