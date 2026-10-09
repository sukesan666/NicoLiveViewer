import json
import math
import os
import queue
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from .protocol import decode_message
from .url import normalize_url
from .windows_job import Job

class Controller:
    def __init__(self):
        self.proc=None; self.job=None; self.busy=False; self.current='';self._events=queue.Queue()
        self._lock=threading.Lock();self.generation=0;self.settings={}

    def _ensure(self):
        if self.proc and self.proc.poll() is None:return
        cmd=([sys.executable,'--worker'] if getattr(sys,'frozen',False)
             else [sys.executable,'-m','nicoview','--worker'])
        env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
        self.proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            encoding='utf-8',bufsize=1,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:self.job=Job(self.proc)
        except Exception:
            self.proc.kill();self.proc.wait();self.proc=None;raise
        self.generation+=1
        threading.Thread(target=self._read,args=(self.proc,self.generation),daemon=True).start()

    def _read(self,proc,generation):
        try:
            for line in proc.stdout:
                event=decode_message(line)
                if generation==self.generation:self._events.put(event)
        except (OSError,ValueError):pass
        finally:
            if generation==self.generation and self.busy:
                self._events.put({'type':'auth_status','request_id':self.current,'text':'ログイン状態を解除しました。再入力してください。'})
                self._events.put({'type':'error','request_id':self.current,'text':'再生処理が終了しました。もう一度再生してください。'})
                self._events.put({'type':'stopped','request_id':self.current})

    def _send(self,data):
        with self._lock:
            self.proc.stdin.write(json.dumps(data,ensure_ascii=False)+'\n');self.proc.stdin.flush()

    def start(self,url,player_path,credentials,ffmpeg_path='',preview=False):
        return self.start_job(url,player_path,credentials,ffmpeg_path,preview=preview)

    def start_job(self,url,player_path,credentials,ffmpeg_path='',mode='play',local_path='',start_time=0,preview=False):
        if self.busy:raise RuntimeError('再生中です。先に停止してください。')
        if mode not in ('play','record','both'):raise ValueError('Invalid playback mode')
        start_time=float(start_time)
        if not math.isfinite(start_time) or start_time<0:raise ValueError('Invalid start time')
        if local_path and mode!='play':raise ValueError('Local files are playback only')
        if not preview and not local_path:url=normalize_url(url)
        self._ensure();self.current=uuid.uuid4().hex;self.busy=True
        try:
            self._send({'type':'start','request_id':self.current,'url':url,'player_path':player_path,
                'credentials':credentials,'settings':self.settings,'ffmpeg_path':ffmpeg_path,'preview':preview,
                'mode':mode,'local_path':str(local_path),'start_time':start_time})
        except Exception:self.busy=False;raise
        return self.current

    def control(self,action,value=None):
        if action not in ('pause','seek','volume','mute'):raise ValueError('Invalid player control')
        if action in ('seek','volume'):
            value=float(value)
            if not math.isfinite(value) or value<0:raise ValueError('Invalid control value')
            if action=='volume':value=min(100,value)
        if action=='mute' and not isinstance(value,bool):raise ValueError('Invalid mute value')
        if action=='pause' and value is not None and not isinstance(value,bool):raise ValueError('Invalid pause value')
        if self.busy:self._send({'type':'control','request_id':self.current,'action':action,'value':value})

    def configure(self,settings):
        self.settings=settings.copy()
        if self.proc and self.proc.poll() is None:
            self._send({'type':'configure','request_id':self.current,'settings':settings})

    def submit_otp(self,request_id,code):
        if request_id==self.current and self.busy:
            self._send({'type':'otp','request_id':request_id,'code':code})

    def post_comment(self,request_id,text,anonymous):
        if not self.busy or request_id!=self.current:raise ValueError('投稿先が変わりました。投稿ウィンドウを開き直してください。')
        ident=uuid.uuid4().hex
        self._send({'type':'post_comment','request_id':request_id,'post_id':ident,'text':text,'anonymous':anonymous})
        return ident

    def stop(self):
        if not self.busy:return
        ident=self.current;generation=self.generation
        try:self._send({'type':'stop','request_id':ident})
        except OSError:pass
        def timeout():
            if self.busy and self.current==ident and self.generation==generation:
                self._terminate()
                self._events.put({'type':'auth_status','request_id':ident,'text':'ログイン状態を解除しました。再入力してください。'})
                self._events.put({'type':'status','request_id':ident,'text':'停止しました。ログイン状態を解除しました。'})
                self._events.put({'type':'stopped','request_id':ident})
        timer=threading.Timer(5,timeout);timer.daemon=True;timer.start()

    def poll_events(self):
        result=[]
        while True:
            try:event=self._events.get_nowait()
            except queue.Empty:break
            if event['request_id'] not in ('',self.current):continue
            if event['type']=='stopped':self.busy=False
            result.append(event)
        return result

    def _terminate(self):
        self.generation+=1
        if self.job:self.job.close();self.job=None
        if self.proc:
            if self.proc.poll() is None:self.proc.kill()
            try:self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:pass
            for f in (self.proc.stdin,self.proc.stdout):
                try:f.close()
                except (OSError,ValueError):pass
            self.proc=None

    def logout(self):
        self._terminate();self.busy=False;self.current=''
        while not self._events.empty():
            try:self._events.get_nowait()
            except queue.Empty:break

    def close(self):self.logout()
