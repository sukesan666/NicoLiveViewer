"""Single in-flight NicoLive post; a server acknowledgement is required."""
import math
import threading
import time

def post_payload(text,anonymous,base,now=None):
    if not isinstance(text,str) or not text.strip():raise ValueError('コメントを入力してください。')
    if len(text.encode('utf-16-le'))//2>75:raise ValueError('コメントは75文字以内で入力してください。')
    if any(ord(c)<32 and c!='\n' for c in text):raise ValueError('使えない制御文字が含まれています。')
    if not isinstance(base,(int,float)) or not math.isfinite(base) or base<=0:
        raise ValueError('投稿に必要な放送情報がありません。再生し直してください。')
    return {'type':'postComment','data':{'text':text,'vpos':max(0,int(((time.time() if now is None else now)-base)*100)),
            'isAnonymous':anonymous is not False,'color':'white','size':'medium','position':'naka','font':'defont'}}

class CommentPoster:
    def __init__(self,send,base):
        self.send=send;self.base=base;self.lock=threading.Lock();self.waiter=threading.Event()
        self.pending=None;self.result=None;self.disabled=False;self.last_send=0
    def receive(self,message):
        if self.pending is None:return
        kind=message.get('type')
        if kind=='postCommentResult':
            data=message.get('data',{})
            if isinstance(data,dict) and isinstance(data.get('chat'),dict) and data['chat'].get('content')==self.pending:
                self.result=(True,'投稿を受け付けました。')
            else:
                self.disabled=True;self.result=(False,'投稿結果を確認できません。重複投稿を避けるため、再生し直してください。')
        elif kind in ('error','disconnect'):
            self.result=(False,'投稿できませんでした。ログイン・放送状態・投稿制限を確認してください。')
        else:return
        self.waiter.set()
    def post(self,text,anonymous,stop,timeout=10):
        if not self.lock.acquire(blocking=False):return False,'前の投稿の結果を待っています。'
        try:
            if stop.is_set():return False,'再生が停止しています。'
            if self.disabled:return False,'投稿結果が未確認です。放送側を確認してから再生し直してください。'
            if time.monotonic()-self.last_send<2:return False,'投稿間隔を2秒以上空けてください。'
            payload=post_payload(text,anonymous,self.base)
            self.waiter.clear();self.result=None;self.pending=text
            self.last_send=time.monotonic()
            try:self.send(payload)
            except Exception:
                self.disabled=True
                return False,'送信結果を確認できません。自動再送はしません。再生し直してください。'
            deadline=time.monotonic()+timeout
            while not stop.is_set() and time.monotonic()<deadline:
                if self.waiter.wait(min(.1,max(.001,deadline-time.monotonic()))):return self.result
            self.disabled=True
            return False,'投稿結果が未確認です。重複投稿を避けるため、自動再送はしません。'
        finally:self.pending=None;self.lock.release()
