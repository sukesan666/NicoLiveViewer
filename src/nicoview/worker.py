import json
import logging
import math
from dataclasses import replace
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from .auth import AuthSession, Cancelled, CodePrompt
from .comments import Comment, CommentReceiver
from .overlay import OverlayQueue
from .playback import asset_path, build_player_args, pump
from .player_ipc import PlayerIPC
from .protocol import decode_message
from .settings import clean_settings
from .url import normalize_url
from . import svp
from .posting import CommentPoster
from .recording import Recording
from .timeline import Timeline, allowed_comment
from .media_proxy import MediaProxy


def stream_plugin(session, url, on_comments):
    from streamlink.plugins.nicolive import NicoLive, NicoLiveWsClient, NicoLiveHLSStream

    class ViewerSocket(NicoLiveWsClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.disconnect_reason = None

        def on_message(self, wsapp, data):
            try:
                message=json.loads(data)
                if message.get('type')=='messageServer':
                    info=dict(message['data'])
                    info['archive']=getattr(self,'archive',False)
                    if info['archive']:info['at']='now'
                    on_comments(info)
                if getattr(self,'poster',None):self.poster.receive(message)
                if message.get('type')=='disconnect':
                    self.disconnect_reason=message.get('data',{}).get('reason','')
            except (ValueError,KeyError,TypeError):pass
            super().on_message(wsapp,data)

    class ViewerPlugin(NicoLive):
        def _get_streams(self):
            data=self.get_data()
            if session.http.cookies.get('user_session') and data.get('user',{}).get('isLoggedIn') is False:
                session.http.cookies.clear()
                raise ValueError('ログインの有効期限が切れました。もう一度ログインしてください。')
            self.program=data.get('program',{})
            ws_url=self.find_wss_api_url(data)
            if not ws_url:return {}
            self.id,self.author,self.title=self.find_metadata(data)
            self.is_timeshift='timeshift' in ws_url or self.program.get('status')=='ENDED'
            self.posting_allowed=data.get('user',{}).get('isLoggedIn') is True and data.get('program',{}).get('status')=='ON_AIR'
            self.wsclient=ViewerSocket(session,ws_url)
            self.wsclient.archive=self.is_timeshift
            self.wsclient.poster=CommentPoster(self.wsclient.send_json,data.get('program',{}).get('openTime'))
            self.wsclient.start()
            hls=self._get_hls_stream_url()
            if not hls:return {}
            return NicoLiveHLSStream.parse_variant_playlist(session,hls,wsclient=self.wsclient,ffmpeg_options={'copyts':True,'format':'mpegts'})
    return ViewerPlugin(session,url)


class Worker:
    def __init__(self, emit):
        logging.disable(logging.CRITICAL)
        self.emit=emit;self.auth=AuthSession.create();self.stop=threading.Event()
        self.thread=None;self.current='';self.prompt=None;self.player=None;self.ipc=None
        self.source=None;self.receiver=None;self.plugin=None;self.settings=clean_settings({})
        self.comments=OverlayQueue();self.overlay_thread=None
        self.can_post=False
        self.timeline=None;self.recording=None;self.proxy=None;self.seekable=False;self.position=0.0;self.mode='play'
        self.media_started=None;self.begin_time=0;self.open_time=0
        self.ready_sent=False
        self.playback_finished=threading.Event()
        self.stall_timeout=12.0

    def is_program_ended(self, *, refresh=False):
        plugin=self.plugin
        if plugin is None:return False
        ws=getattr(plugin,'wsclient',None)
        if getattr(ws,'disconnect_reason',None) in ('END_PROGRAM','PROGRAM_ENDED','end_program'):
            return True
        try:
            program=plugin.get_data().get('program') if refresh else getattr(plugin,'program',None)
        except Exception:
            return False
        return isinstance(program,dict) and program.get('status')=='ENDED'

    def event(self,kind, text=None, **values):
        e={'type':kind,'request_id':self.current}
        if text is not None:e['text']=text
        e.update(values);self.emit(e)

    def handle(self,message):
        kind=message['type']
        if kind=='configure':
            self.settings=clean_settings(message.get('settings',{}))
            return
        if kind=='start':
            if self.thread and self.thread.is_alive():return
            self.current=message['request_id'];self.stop=threading.Event();self.comments.clear();self.can_post=False
            self.settings=clean_settings(message.get('settings',{}))
            self.thread=threading.Thread(target=self.run,args=(message,),daemon=True);self.thread.start()
        elif message['request_id']==self.current:
            if kind=='stop':
                self.stop.set()
                if self.source:
                    source=self.source
                    threading.Thread(target=lambda:source.close(),daemon=True).start()
            elif kind=='otp' and self.prompt:self.prompt.submit(message.get('code'))
            elif kind=='post_comment':self.post_comment(message)
            elif kind=='control':self.control(message.get('action'),message.get('value'))

    def control(self,action,value=None):
        if not self.ipc:return
        try:
            if action=='pause':
                if value is None:self.ipc.send(['cycle','pause'])
                elif isinstance(value,bool):self.ipc.send(['set_property','pause',value])
            elif action=='seek' and self.seekable:
                value=float(value)
                if math.isfinite(value) and value>=0:
                    self.ipc.send(['seek',value,'absolute+exact'])
                    self.ipc.send(['script-message','nico-clear'])
                    if self.timeline:self.timeline.seek(value-self.settings.get('comment_offset',0))
            elif action=='volume':
                value=float(value)
                if math.isfinite(value):self.ipc.send(['set_property','volume',max(0,min(100,value))])
            elif action=='mute' and isinstance(value,bool):self.ipc.send(['set_property','mute',value])
        except (OSError,ValueError,TypeError):self.event('status','プレイヤーの操作を適用できませんでした。')

    def post_comment(self,message):
        ident=self.current;stop=self.stop;post_id=message.get('post_id','')
        if not isinstance(post_id,str) or len(post_id)>100:return
        poster=getattr(getattr(self.plugin,'wsclient',None),'poster',None)
        if not self.can_post or not poster or stop.is_set():
            self.emit({'type':'post_result','request_id':ident,'post_id':post_id,'ok':False,
                       'text':'ログインして放送を再生してから投稿してください。'})
            return
        def work():
            try:ok,text=poster.post(message.get('text'),message.get('anonymous',True),stop)
            except (ValueError,UnicodeError):ok,text=False,'コメントを確認してください（75文字以内）。'
            if ident==self.current and not stop.is_set():
                self.emit({'type':'post_result','request_id':ident,'post_id':post_id,'ok':ok,'text':text})
        threading.Thread(target=work,daemon=True).start()

    def _overlay(self):
        previous=None;stop=self.stop;ipc=self.ipc;player=self.player;ident=self.current;reported=0;last_position=0
        while not stop.wait(.033) and ident==self.current and not self.playback_finished.is_set():
            try:
                settings=self.settings.copy()
                if settings!=previous:
                    ipc.send(['script-message','nico-config',json.dumps(settings,ensure_ascii=False)])
                    ipc.send(['set_property','volume',settings.get('volume',100)])
                    ipc.send(['set_property','mute',settings.get('muted',False)])
                    previous=settings
                now=time.monotonic()
                state=ipc.snapshot()
                if isinstance(state,dict):
                    position=state.get('time-pos')
                    if isinstance(position,(int,float)) and math.isfinite(position):
                        if self.mode=='play' and not self.ready_sent:
                            self.ready_sent=True;self.event('job_ready',mode=self.mode)
                        self.position=position
                        if position<last_position-.1 or position-last_position>2:
                            ipc.send(['script-message','nico-clear'])
                            if self.timeline:self.timeline.seek(position-settings.get('comment_offset',0))
                        last_position=position
                    if now-reported>=.5:
                        self.event('position',position=self.position,duration=state.get('duration') or 0,
                                   paused=state.get('pause') is True,seekable=self.seekable)
                        reported=now
                else:state={}
                if self.timeline is not None:
                    ready=[] if state.get('pause') else self.timeline.ready(self.position,settings.get('comment_offset',0)+settings['delay'])
                else:ready=self.comments.ready(now,max(0,settings['delay']+settings.get('comment_offset',0)))
                if settings['enabled']:
                    for c in [c for c in ready if allowed_comment(c,settings)][-60:]:
                        ipc.send(['script-message','nico-comment',c.text,c.color])
            except (OSError,ValueError):
                if player and player.poll() is not None:return
                elif ident==self.current and not stop.is_set():
                    self.event('comment_status','コメント表示との接続が切れました。停止して再生し直してください。')
                return

    def _on_comments(self, info, ident):
        if self.stop.is_set() or ident!=self.current:return
        if self.receiver:self.receiver.stop()
        if info.get('archive'):
            self.seekable=True
            if self.timeline is None:self.timeline=Timeline()
            program=getattr(self.plugin,'program',{})
            if isinstance(program,dict):
                self.begin_time=program.get('beginTime',0) or 0;self.open_time=program.get('openTime',0) or 0
        stop=self.stop
        def active():return ident==self.current and not stop.is_set() and self.receiver is receiver
        def report(text):
            if active():self.emit({'type':'comment_status','request_id':ident,'text':text})
        def deliver(comment):
            if active():
                if self.seekable:
                    position=(comment.timestamp-self.begin_time if comment.timestamp is not None and self.begin_time
                              else (comment.position or 0)-(self.begin_time-self.open_time if self.begin_time and self.open_time else 0))
                    comment=replace(comment,position=max(0,position))
                    if self.timeline is not None:self.timeline.add(comment)
                else:
                    position=max(0,time.monotonic()-self.media_started) if self.media_started else 0
                    self.comments.add(comment)
                if self.recording:self.recording.add_comment(comment,position)
        receiver=CommentReceiver(report);self.receiver=receiver
        receiver.start(info,self.auth.session,deliver)

    def _watch_player(self):
        player=self.player;stop=self.stop;ipc=self.ipc
        last_pos=None;last_progress=time.monotonic()
        while not stop.wait(.1):
            events=ipc.take_events() if ipc else []
            ending=next((e for e in events if e.get('event')=='end-file'),None)
            exited=player.poll() is not None
            stalled=False
            if not ending and not exited and ipc and self.proxy is not None and not self.recording:
                state=ipc.snapshot()
                now=time.monotonic()
                if state.get('pause') is True or any(e.get('event') in ('seek','playback-restart') for e in events):
                    last_progress=now
                else:
                    pos=state.get('time-pos')
                    if isinstance(pos,(int,float)) and math.isfinite(pos):
                        if last_pos is None or abs(pos-last_pos)>1e-3:
                            last_pos=pos;last_progress=now
                        elif now-last_progress>=self.stall_timeout:
                            stalled=True
                    elif (last_pos is not None or state.get('paused-for-cache') is True or state.get('core-idle') is True) and now-last_progress>=self.stall_timeout:
                        stalled=True
            if ending or exited or stalled:
                reason=ending.get('reason') if ending else None
                user_closed=not stalled and (reason in ('quit','stop') or (ending is None and exited and player.returncode==0))
                ended=bool(not stalled and reason=='eof' and (self.seekable or self.is_program_ended(refresh=True)))
                if stop.is_set():return
                self.player_completed=ended
                self.playback_finished.set()
                if self.player_completed and self.mode=='both':
                    # A VOD player can reach EOF while recording/comment downloads
                    # are still catching up. Only playback ends in this case.
                    return
                if user_closed:
                    self.event('player_closed')
                elif stalled:
                    self.event('error','再生が停止しました。再接続を確認しています。')
                elif not self.player_completed:
                    self.event('error','再生接続が終了しました。再接続を確認しています。')
                else:
                    self.event('status','番組の放送が終了しました。')
                stop.set()
                if self.source:
                    source=self.source
                    threading.Thread(target=source.close,daemon=True).start()
                return

    def _launch(self,path,preview,media=None,start_time=0):
        use_svp=self.settings['svp_enabled']
        if use_svp:
            path=svp.player_path(self.settings['svp_player_path'])
            svp.check_pipe_available()
        pipe=svp.PIPE if use_svp else r'\\.\pipe\nicoview-'+uuid.uuid4().hex
        args=build_player_args(path,pipe,asset_path('comments.lua'),svp_enabled=use_svp)
        if preview:args=args[:-1]+['--loop-file=inf',str(asset_path('preview.mp4'))]
        elif media:args=args[:-1]+(['--start='+str(float(start_time))] if start_time else [])+[str(media)]
        self.player=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                     env=svp.player_environment() if use_svp else None,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.ipc=PlayerIPC(pipe);self.ipc.connect(self.stop);self.ipc.observe()
        if use_svp:svp.verify_server(self.ipc,self.player.pid)
        # Script registration completes asynchronously after player startup.
        self.stop.wait(.3)
        self.overlay_thread=threading.Thread(target=self._overlay,daemon=True);self.overlay_thread.start()
        threading.Thread(target=self._watch_player,daemon=True).start()

    def run(self,message):
        completed=False;self.recording=None;self.proxy=None;self.timeline=None;self.position=0.0;self.seekable=False
        self.mode=message.get('mode','play');self.media_started=None
        self.ready_sent=False;self.player_completed=False;self.playback_finished.clear()
        try:
            if self.mode not in ('play','record','both'):raise ValueError('再生モードを確認してください。')
            path=message.get('player_path','')
            if self.mode!='record':
                if self.settings['svp_enabled']:path=svp.player_path(self.settings['svp_player_path'])
                if not path or not Path(path).is_file():raise ValueError('mpv.exeを設定してください。')
            preview=message.get('preview',False)
            if preview:
                self._launch(path,True);self.event('status','コメント表示のプレビュー中')
                examples=['コメント表示のプレビューです','フォントと文字サイズを変更できます',
                          'こんにちは！ ニコ生ビューア','表示ON/OFF・遅延も調整できます','日本語 ABC 123 ♪']
                i=0
                while not self.stop.wait(1.2):
                    self.comments.add(Comment(str(i),examples[i%len(examples)],time.monotonic(),('FFFFFF','FF0000','00FF00','3399FF','FFFF00')[i%5]));i+=1
                return
            local=message.get('local_path','')
            if local:
                local=Path(local).resolve()
                if not local.is_file():raise ValueError('録画ファイルが見つかりません。')
                self.seekable=True;self.timeline=Timeline.from_sidecar(local)
                self.event('metadata',title=local.stem,url='',local_path=str(local),seekable=True)
                self._launch(path,False,local,message.get('start_time',0))
                self.event('status','録画ファイルを再生中')
                while not self.stop.wait(.2):pass
                completed=self.player_completed;return
            url=normalize_url(message['url'])
            credentials=message.pop('credentials',None)
            if credentials is None:
                self.auth.clear();self.event('auth_status','ログインなしで視聴しています')
            elif 'session' in credentials:
                try:restored=self.auth.restore(credentials['session'])
                finally:credentials.clear()
                if not restored:
                    self.event('session_invalid','ログインの期限が切れました。ブラウザでログインし直してください。')
                    raise ValueError('ログインの期限が切れました。ブラウザでログインし直してください。')
                self.event('auth_status','保存したセッションで接続しています。')
            elif credentials.get('password'):
                self.event('auth_status','ログイン確認中…')
                self.event('status','ログインしています…')
                self.prompt=CodePrompt(lambda:self.event('otp_required'),self.stop)
                try:success=self.auth.login(credentials.get('email',''),credentials['password'],self.prompt.ask)
                finally:credentials.clear();self.prompt=None
                if not success:raise ValueError('ログインできませんでした。入力内容と認証コードを確認してください。')
                self.event('authenticated','ログインしました（アプリ終了まで保持）')
            elif not self.auth.session.http.cookies.get('user_session'):
                raise ValueError('メールアドレスとパスワードを入力してください。')
            if self.stop.is_set():raise Cancelled()
            ffmpeg=message.get('ffmpeg_path','')
            if ffmpeg:self.auth.session.set_option('ffmpeg-ffmpeg',ffmpeg)
            self.auth.session.set_option('ffmpeg-fout','mpegts')
            self.event('status','番組に接続しています…')
            ident=self.current
            self.plugin=stream_plugin(self.auth.session,url,lambda info:self._on_comments(info,ident))
            try:streams=self.plugin.streams()
            except ValueError as exc:
                if str(exc).startswith('ログインの有効期限'):
                    self.event('session_invalid','ログインが失効しました。ブラウザでログインし直してください。')
                raise
            if self.stop.is_set():raise Cancelled()
            if not streams:
                if self.is_program_ended():
                    raise ValueError('番組の放送は終了しました。')
                raise ValueError('再生できる配信がありません。放送中か、視聴権限があるか確認してください。')
            quality=self.settings.get('quality','best')
            stream=streams.get(quality) or streams.get('best') or next(iter(streams.values()))
            if quality not in streams:self.event('status','選択した画質がないため利用可能な最高画質を使用します。')
            program=getattr(self.plugin,'program',{})
            if not isinstance(program,dict):program={}
            self.begin_time=program.get('beginTime',0) or 0;self.open_time=program.get('openTime',0) or 0
            self.seekable=getattr(self.plugin,'is_timeshift',False) is True
            if self.seekable and self.timeline is None:self.timeline=Timeline()
            metadata={'url':url,'title':str(getattr(self.plugin,'title','番組')),'author':str(getattr(self.plugin,'author','配信者不明')),
                      'id':str(getattr(self.plugin,'id','')),'provider':str(program.get('providerType','')),'status':str(program.get('status',''))}
            self.event('metadata',**metadata,seekable=self.seekable,local_path='')
            if self.mode in ('record','both'):
                self.recording=Recording(self.settings.get('recording_dir',''),metadata)
                if self.timeline is not None:
                    with self.timeline.lock:
                        for c in self.timeline.items:self.recording.add_comment(c,c.position)
                self.event('recording',path=str(self.recording.path),state='recording',metadata_path=str(self.recording.metadata_path))
            if self.mode!='record':
                self.proxy=MediaProxy(self.auth.session,stream,getattr(self.plugin,'wsclient',None))
                self._launch(path,False,self.proxy.url,message.get('start_time',0) if self.seekable else 0)
            self.media_started=time.monotonic()
            self.can_post=self.mode!='record' and getattr(self.plugin,'posting_allowed',False) is True and any(c.name=='user_session' and not c.is_expired() for c in self.auth.session.http.cookies)
            self.emit({'type':'post_ready','request_id':self.current,'enabled':self.can_post,'url':url})
            self.event('status','録画中' if self.mode=='record' else ('再生・録画中' if self.mode=='both' else '再生中'))
            if self.recording:
                self.source=stream.open()
                while not self.stop.is_set():
                    chunk=self.source.read(65536)
                    if not chunk:
                        ended=self.seekable or (self.recording.bytes>0 and self.is_program_ended(refresh=True))
                        completed=ended
                        if not completed:
                            self.event('error','録画接続が終了しました。再接続を確認しています。')
                        else:
                            self.event('status','番組の放送が終了しました。')
                        break
                    self.recording.write(chunk)
                    if not self.ready_sent:
                        self.ready_sent=True;self.event('job_ready',mode=self.mode)
                if completed and self.seekable and self.receiver and self.receiver.thread:
                    self.event('status','過去コメントの保存を完了しています…')
                    completed=self.receiver.wait_complete(self.stop)
                self.recording.finish(completed)
                self.event('recording',path=str(self.recording.path),state=self.recording.metadata['state'],metadata_path=str(self.recording.metadata_path))
            if self.mode!='record':
                while not self.playback_finished.is_set() and not self.stop.wait(.2):pass
                if not self.recording:completed=self.player_completed
        except Cancelled:self.event('status','キャンセルしました。')
        except ValueError as exc:
            # Only explicit application messages, never raw network/credential payloads.
            text=str(exc)
            self.event('error',text if any(text.startswith(p) for p in ('SVP','VapourSynth','mpv.exe','ログイン','メール','再生できる','認証コード','https://','番組URL','録画','コメントファイル','番組の放送'))
                       else '入力または接続内容を確認してください。')
        except BrokenPipeError:
            if not self.stop.is_set():self.event('status','プレイヤーが終了しました。')
        except Exception:
            if not self.stop.is_set():self.event('error','接続に失敗しました。ネット接続・放送状態・ログイン・FFmpeg設定を確認してください。')
        finally:
            self.can_post=False
            message.pop('credentials',None)
            if not self.auth.session.http.cookies.get('user_session'):
                self.event('auth_status','未ログイン / 保存した情報がある場合は次回の再生に利用します')
            self.stop.set()
            if self.recording:
                self.recording.finish(False)
                self.event('recording',path=str(self.recording.path),state=self.recording.metadata['state'],metadata_path=str(self.recording.metadata_path))
            if self.proxy:self.proxy.close();self.proxy=None
            if self.receiver:self.receiver.stop();self.receiver=None
            if self.player:
                if self.player.poll() is None:
                    self.player.terminate()
                    try:self.player.wait(timeout=2)
                    except subprocess.TimeoutExpired:self.player.kill()
                try:self.player.stdin.close()
                except (OSError,ValueError):pass
            if self.ipc:self.ipc.close();self.ipc=None
            if self.overlay_thread:self.overlay_thread.join(timeout=1)
            if self.source:
                try:self.source.close()
                except Exception:pass
                self.source=None
            if self.plugin and hasattr(self.plugin,'wsclient'):
                try:self.plugin.wsclient.close()
                except Exception:pass
            self.plugin=None;self.player=None;self.comments.clear()
            self.event('completed',ok=completed,path=str(self.recording.path) if self.recording else message.get('local_path',''),position=self.position)
            self.event('stopped')

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=2)
        if self.player and self.player.poll() is None:self.player.kill()
        self.auth.close()


def main():
    lock=threading.Lock()
    def emit(event):
        with lock:
            try:sys.stdout.write(json.dumps(event,ensure_ascii=False)+'\n');sys.stdout.flush()
            except (BrokenPipeError,OSError):pass
    worker=Worker(emit)
    try:
        while True:
            line=sys.stdin.readline(65538)
            if not line:break
            try:message=decode_message(line)
            except ValueError:break
            if message['type']=='shutdown':break
            worker.handle(message)
    finally:worker.close()
