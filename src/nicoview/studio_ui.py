"""Desktop shell: one isolated controller for each playback or recording."""
from .i18n import get_language, set_language, tr
import copy
from datetime import datetime
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import uuid

from .controller import Controller
from .desktop import SleepInhibitor, TrayIcon, NotificationSound, set_startup
from .post_window import PostWindow
from .preferences import PreferenceStore
from .scheduler import Scheduler, reservation_key, JST
from .settings import clean_settings
from .settings_ui import SettingsDialog, FirstRunGuide
from .ui import App as LoginApp, app_dir, find_player
from .url import normalize_url
from . import svp
from .layout import fit_window, initialize_zoom, zoom_container, zoom_factor, WindowMemory, AutoScrollbar, ScrollableTree
from .theme_ui import MenuBar, theme_widgets

from .webpush import WebPushClient
from .push_notifications import parse_notification
from .push_registration import FollowSubscriptions, session_fingerprint

MODE={'play':'再生','record':'録画','both':'再生＋録画'}
STATE={'waiting':'待機','queued':'起動待ち','started':'実行済み','ended':'終了','disabled':'無効'}

def clock_text(seconds):
    seconds=max(0,int(seconds or 0));h,rem=divmod(seconds,3600);m,s=divmod(rem,60)
    return f'{h}:{m:02}:{s:02}' if h else f'{m:02}:{s:02}'

def object_rows(value,limit):
    return [dict(item) for item in value[:limit] if isinstance(item,dict)] if isinstance(value,list) else []

def check_resources(settings,mode,active_count=0,player_count=0,local_path=''):
    if active_count>=settings['max_jobs']:return False,tr('同時処理数の上限です。空きができるまで待機します。')
    if mode!='record':
        player=settings['player_path']
        if not player_count and settings['svp_enabled']:player=settings['svp_player_path'] or player
        if not Path(player).is_file():return False,tr('設定から mpv の場所を指定してください。')
    if local_path and not Path(local_path).is_file():return False,tr('録画ファイルが見つかりません。')
    if mode in ('record','both'):
        directory=Path(settings['recording_dir'] or Path.home()/'Videos'/'NicoLive Studio')
        try:
            directory.mkdir(parents=True,exist_ok=True)
            if shutil.disk_usage(directory).free<512*1024*1024:return False,tr('録画先の空き容量が512MB未満です。')
            if not os.access(directory,os.W_OK):return False,tr('録画先に書き込めません。')
        except OSError:return False,tr('録画先を確認してください。')
    return True,''

class StudioApp:
    def __init__(self,root,store=None,controller_factory=Controller,background=True):
        self.root=root;self.store=store or PreferenceStore();self.controller_factory=controller_factory
        self.closed=False;self.closing=False;self.jobs={};self.events=queue.Queue();self.stop_event=threading.Event()
        self.background=background;self.tray=None;self.account=None;self.account_window=None
        self.sleep=SleepInhibitor();self.sound=NotificationSound();self.reservation_window=None
        self.reservation_sort=(None,False);self.execution_sort=(None,False);self._hidden_toplevels=[]
        self.webpush=None
        self.push_generation='';self.push_ready=False;self.last_push_check=0
        self.push_attempted=set();self.push_setup_thread=None
        self.push_follow_results={}
        self.push_reset_requested=False;self.force_push_registration=False;self.push_follow_cancel=threading.Event()
        self.follow_subscriptions=FollowSubscriptions(self.store.directory/'push-follows.json')
        if background:self.store.migrate(app_dir())
        self.settings=self.store.load_settings();initialize_zoom(root,self.settings['ui_scale']);self.state=self.store.load_state()
        set_language(self.settings['language'])
        self.scheduler=Scheduler(self.state.get('reservations',[]))
        self.history=object_rows(self.state.get('history'),200)
        self.executions=object_rows(self.state.get('executions'),500)
        self.last_saved=time.monotonic()
        if not Path(self.settings['player_path']).is_file():self.settings['player_path']=find_player()
        if not Path(self.settings['ffmpeg_path']).is_file():
            candidates=[app_dir()/'player'/'ffmpeg.exe',app_dir().parent/'ffmpeg'/'ffmpeg.exe']
            self.settings['ffmpeg_path']=next((str(p) for p in candidates if p.is_file()),shutil.which('ffmpeg') or '')
        if not self.settings['svp_player_path']:self.settings['svp_player_path']=svp.find_player()
        root._window_memory=WindowMemory(root,self.store.directory/'window-layout.json')
        root.title('NicoLive Studio')
        self.url=tk.StringVar(root);self.status=tk.StringVar(root,value=tr('番組URLを入力するか、番組を探して再生できます。'))
        self.detail=tk.StringVar(root,value=tr('再生・録画する番組がここに並びます。'))
        self.push_status=tk.StringVar(root,value=tr('プッシュ通知: ログインが必要です。'))
        self.push_follow_status=tk.StringVar(root)
        self.position=tk.DoubleVar(root,value=0);self.time_label=tk.StringVar(root,value='00:00 / --:--')
        self.volume=tk.IntVar(root,value=self.settings['volume']);self.muted=tk.BooleanVar(root,value=self.settings['muted'])
        self.comments_enabled=tk.BooleanVar(root,value=self.settings['enabled']);self.seeking=False
        self.build_menu();self.build_body();self.apply_theme()
        root.bind_all('<Map>',lambda e:theme_widgets(e.widget) if isinstance(e.widget,(tk.Tk,tk.Toplevel)) else None,add='+')
        from .playback import asset_path
        try:
            root.iconbitmap(str(asset_path('studio.ico')))
        except tk.TclError:pass
        root.protocol('WM_DELETE_WINDOW',self.close_requested)
        root.bind('<Control-l>',lambda e:self.url_entry.focus_set())
        root.bind('<Control-o>',lambda e:self.open_local())
        root.bind('<Control-comma>',lambda e:self.open_settings())
        root.bind('<F5>',lambda e:self.reconnect_selected())
        # Login state stays in its DPAPI file; never enters settings/history.
        self.ensure_account()
        self.fit_initial_window()
        root._window_memory.track(root)
        if background:
            with self.scheduler.lock:
                seen_targets=set();deduped=[]
                for r in self.scheduler.records:
                    tkey=(reservation_key(r) or ('kw:'+r['keyword'].casefold() if r.get('keyword') else 'tag:'+r['official_tag'].casefold() if r.get('official_tag') else r['id']))
                    slots={(tkey,'play'),(tkey,'record')} if r['mode']=='both' else {(tkey,r['mode'])}
                    if not (slots & seen_targets):
                        seen_targets.update(slots);deduped.append(r)
                if len(deduped)!=len(self.scheduler.records):
                    self.scheduler.records=deduped;self.save_state()
            self.expire_recording_reservations()
            root.after(100,self.poll);root.after(1000,self.schedule_tick)
            if not self.settings['first_run_complete']:
                root.after(400,lambda:FirstRunGuide(root,self.settings,self.apply_settings,self.show_account))

    def fit_initial_window(self):
        """Fit themed widgets after login styles initialize, within the desktop."""
        root=self.root
        if zoom_factor(root)>1:
            fit_window(root,1040,690);return
        root.update_idletasks()
        left=top=0;right=root.winfo_screenwidth();bottom=root.winfo_screenheight()
        if os.name=='nt':
            try:
                import ctypes
                from ctypes import wintypes
                area=wintypes.RECT()
                if ctypes.windll.user32.SystemParametersInfoW(48,0,ctypes.byref(area),0):
                    left,top,right,bottom=area.left,area.top,area.right,area.bottom
            except (OSError,AttributeError):pass
        # Leave room for the title/menu bars, borders and desktop margins.
        scale=max(1,float(root.tk.call('tk','scaling'))/(96/72))
        available_w=max(1,right-left-int(32*scale))
        available_h=max(1,bottom-top-int(90*scale))
        width=min(max(1040,root.winfo_reqwidth()+24),available_w)
        height=min(root.winfo_reqheight()+24,available_h)
        fixed_height=root.winfo_reqheight()-self.tree.winfo_reqheight()
        root.minsize(min(root.winfo_reqwidth(),width),min(fixed_height+100,height))
        root.geometry(f'{width}x{height}+{left+max(0,(right-left-width)//2)}+{top+int(12*scale)}')

    def build_menu(self):
        self.menu_bar=MenuBar(self.root)
        def menu(name,items):self.menu_bar.add(name,items)
        menu(tr('ファイル'),[(tr('番組URLを入力  Ctrl+L'),lambda:self.url_entry.focus_set()),(tr('録画ファイルを開く  Ctrl+O'),self.open_local),(tr('録画一覧'),self.open_library),(tr('視聴履歴'),self.open_history),(None,None),(tr('設定を書き出す'),self.export_settings),(tr('設定を読み込む'),self.import_settings),(tr('設定のバックアップを復元'),self.restore_settings),(None,None),(tr('終了'),self.quit_requested)])
        menu(tr('再生'),[(tr('一時停止・再開'),lambda:self.selected_control('pause')),(tr('再接続  F5'),self.reconnect_selected),(tr('コメント投稿'),self.open_post),(tr('選択した番組を停止'),self.stop_selected),(tr('コメントのテスト'),self.preview)])
        menu(tr('録画・予約'),[(tr('予約'),self.open_schedule_picker),(tr('予約と実行履歴'),self.open_reservations),(tr('入力したURLを録画'),lambda:self.launch_url('record')),(tr('入力したURLを再生＋録画'),lambda:self.launch_url('both'))])
        menu(tr('表示'),[(tr('番組を探す'),self.open_discovery),(tr('ダーク／ライト切り替え'),self.toggle_theme),(tr('録画一覧'),self.open_library)])
        menu(tr('設定'),[(tr('設定  Ctrl+,'),self.open_settings),(tr('ログイン・アカウント'),self.show_account)])
        menu(tr('ヘルプ'),[(tr('使い方と制限'),self.help),(tr('診断情報を書き出す'),self.export_diagnostics)])

    def build_body(self):
        content=zoom_container(self.root)
        body=ttk.Frame(content,padding=24);body.pack(fill='both',expand=True)
        row=ttk.Frame(body);row.pack(fill='x')
        self.url_entry=ttk.Entry(row,textvariable=self.url,font=('Yu Gothic UI',12))
        self.url_entry.pack(side='left',fill='x',expand=True,padx=(0,10));self.url_entry.bind('<Return>',lambda e:self.launch_url())
        self.url_entry.bind('<Button-3>',self.url_context);self.url_entry.bind('<Shift-F10>',self.url_context)
        ttk.Button(row,text=tr('▶ 再生'),style='Accent.TButton',command=self.launch_url).pack(side='left')
        nav=ttk.Frame(body);nav.pack(fill='x',pady=(12,20))
        for label,command in [(tr('番組を探す'),self.open_discovery),(tr('予約'),self.open_schedule_picker),(tr('予約と履歴'),self.open_reservations),(tr('録画一覧'),self.open_library)]:
            ttk.Button(nav,text=label,command=command).pack(side='left',padx=(0,8))
        ttk.Button(nav,text=tr('ログイン'),command=self.show_account).pack(side='right')
        ttk.Label(body,text=tr('再生・録画中'),font=('Yu Gothic UI',13,'bold')).pack(anchor='w',pady=(0,8))
        footer=ttk.Frame(body);footer.pack(side='bottom',fill='x')
        frame=ScrollableTree(body,columns=('mode','title','state'),show='headings',selectmode='browse',height=4)
        frame.pack(fill='x')
        self.tree=frame.tree
        for key,label,width in [('mode',tr('操作'),110),('title',tr('番組'),470),('state',tr('状態'),245)]:
            self.tree.heading(key,text=label);self.tree.column(key,width=width,minwidth=70,stretch=key!='mode')
        self.tree.bind('<<TreeviewSelect>>',lambda e:self.refresh_selection())
        ttk.Label(footer,textvariable=self.detail,style='Muted.TLabel',wraplength=940).pack(anchor='w',pady=(12,5))
        timeline=ttk.Frame(footer);timeline.pack(fill='x')
        self.seek=ttk.Scale(timeline,from_=0,to=1,variable=self.position,state='disabled')
        self.seek.pack(side='left',fill='x',expand=True,padx=(0,14))
        self.seek.bind('<ButtonPress-1>',lambda e:setattr(self,'seeking',True))
        self.seek.bind('<ButtonRelease-1>',self.seek_released)
        ttk.Label(timeline,textvariable=self.time_label,width=20).pack(side='right')
        controls=ttk.Frame(footer);controls.pack(fill='x',pady=10)
        for label,command in [(tr('一時停止／再開'),lambda:self.selected_control('pause')),(tr('停止'),self.stop_selected),(tr('コメント投稿'),self.open_post)]:
            ttk.Button(controls,text=label,command=command).pack(side='left',padx=(0,7))
        ttk.Checkbutton(controls,text=tr('コメント'),variable=self.comments_enabled,command=self.toggle_comments).pack(side='left',padx=8)
        ttk.Checkbutton(controls,text=tr('消音'),variable=self.muted,command=lambda:self.selected_control('mute',self.muted.get())).pack(side='right')
        ttk.Scale(controls,from_=0,to=100,variable=self.volume,command=self.volume_changed,length=110).pack(side='right',padx=8)
        ttk.Label(controls,text=tr('音量')).pack(side='right')
        ttk.Label(footer,textvariable=self.status,style='Muted.TLabel',wraplength=950).pack(anchor='w',pady=(6,0))
        pushbar=ttk.Frame(footer);pushbar.pack(fill='x',pady=(4,0))
        ttk.Label(pushbar,textvariable=self.push_status,style='Muted.TLabel',wraplength=760).pack(side='left',fill='x',expand=True)
        ttk.Button(pushbar,text=tr('通知登録を再試行'),command=self.retry_push_setup).pack(side='right')
        ttk.Label(footer,textvariable=self.push_follow_status,style='Muted.TLabel',wraplength=950).pack(anchor='w')

    def ensure_account(self):
        if self.account:return
        top=tk.Toplevel(self.root);top.withdraw()
        self.account=LoginApp(top,self.store.settings_path);self.account_window=top
        # Preserve the proven browser login flow while exposing only its account panel.
        for canvas in top.winfo_children():
            if isinstance(canvas,tk.Canvas):
                canvas.unbind_all('<MouseWheel>')
                for outer in canvas.winfo_children():
                    for child in outer.winfo_children():
                        if not isinstance(child,ttk.LabelFrame) or child.cget('text')!=tr('ログイン'):child.pack_forget()
        top.title(tr('ログイン・アカウント'));top.minsize(700,370);top.geometry('760x420')
        top.protocol('WM_DELETE_WINDOW',top.withdraw)
        def attach_logout(widget):
            for child in widget.winfo_children():
                if isinstance(child,ttk.Button) and child.cget('text')==tr('ログアウト・保存削除'):child.configure(command=self.logout)
                attach_logout(child)
        attach_logout(top)
        self.apply_theme()
        if zoom_factor(self.root)>1:
            for canvas in top.winfo_children():
                if isinstance(canvas,tk.Canvas):
                    bar=AutoScrollbar(top,orient='horizontal',command=canvas.xview)
                    bar.grid(row=1,column=0,sticky='ew');canvas.configure(xscrollcommand=bar.set)
                    def resize(event,c=canvas):
                        for item in c.find_all():
                            if c.type(item)=='window':
                                child=c.nametowidget(c.itemcget(item,'window'))
                                c.itemconfigure(item,width=max(event.width,child.winfo_reqwidth()))
                    canvas.bind('<Configure>',resize)

    def logout(self):
        if any(j['controller'].busy and j['mode']!='play' for j in self.jobs.values()):
            if not messagebox.askyesno(tr('録画中です'),tr('ログアウトすると録画も停止します。続けますか？'),parent=self.account_window):return
        for job in list(self.jobs.values()):self.stop_job(job)
        self.account.logout()
        self.ensure_webpush()

    def show_account(self):
        self.ensure_account();self.account_window.deiconify();self.account_window.lift()
        self.account_window.update_idletasks()
        for canvas in self.account_window.winfo_children():
            if isinstance(canvas,tk.Canvas):
                for outer in canvas.winfo_children():
                    canvas.configure(width=outer.winfo_reqwidth(),height=outer.winfo_reqheight())
        fit_window(self.account_window,760,420)

    def credentials(self):
        if not self.account or not self.account.login.get():return None
        if self.account.browser_session:return {'session':copy.deepcopy(self.account.browser_session)}
        email=self.account.email.get().strip();password=self.account.password.get()
        return {'email':email,'password':password} if email and password else None

    def cookies(self):return copy.deepcopy((self.credentials() or {}).get('session',[]))

    def url_context(self,event):
        entry=self.url_entry;entry.focus_set();menu=tk.Menu(self.root,tearoff=False)
        for label,action in [(tr('切り取り'),'<<Cut>>'),(tr('コピー'),'<<Copy>>'),(tr('貼り付け'),'<<Paste>>')]:
            menu.add_command(label=label,command=lambda a=action:entry.event_generate(a))
        menu.add_separator();menu.add_command(label=tr('すべて選択'),command=lambda:entry.selection_range(0,'end'))
        try:menu.tk_popup(event.x_root,event.y_root)
        finally:menu.grab_release()
        return 'break'

    def preflight(self,program,mode,local_path=''):
        active=[j for j in self.jobs.values() if j['controller'].busy]
        return check_resources(self.settings,mode,len(active),sum(j['mode']!='record' for j in active),local_path)

    def launch_url(self,mode='play'):
        try:url=normalize_url(self.url.get())
        except ValueError as exc:self.status.set(tr(str(exc)));return False
        return self.launch({'url':url},mode)

    def launch(self,program,mode='play',local_path='',start_time=0,preview=False,schedule_id=None,replace_ident=None):
        if self.closed or self.closing:return False
        program=dict(program);url=program.get('url','')
        try:
            if not local_path and not preview:url=normalize_url(url)
            run_key=('local:'+str(Path(local_path).resolve()).casefold()) if local_path else 'preview' if preview else url
            slots={'play','record'} if mode=='both' else {mode}
            for existing_id,existing in self.jobs.items():
                if existing_id==replace_ident:continue
                occupied={'play','record'} if existing['mode']=='both' else {existing['mode']}
                if existing.get('run_key')==run_key and slots & occupied:
                    self.tree.selection_set(existing_id)
                    raise ValueError(tr('この番組は同じ動作で実行中、または再接続待ちです。再生と録画は各1件までです。'))
            ok,reason=self.preflight(program,mode,local_path)
            if not ok:raise ValueError(reason)
            settings=dict(self.settings)
            first=not any(j['controller'].busy and j['mode']!='record' for j in self.jobs.values())
            settings['svp_enabled']=bool(settings['svp_enabled'] and first and mode!='record')
            settings['muted']=bool(settings['muted'] or (not first and mode!='record'))
            credentials=None if local_path or preview else self.credentials()
            controller=self.controller_factory();controller.configure(settings)
            controller.start_job(url,settings['player_path'],copy.deepcopy(credentials),
                                 settings['ffmpeg_path'],mode=mode,local_path=local_path,start_time=start_time,preview=preview)
        except Exception as exc:
            self.status.set(tr(str(exc))[:300])
            if 'controller' in locals():controller.close()
            if schedule_id:self.scheduler.ack(schedule_id,False,str(exc),program)
            return False
        ident=replace_ident or uuid.uuid4().hex
        retries=self.jobs[ident]['retries']+1 if ident in self.jobs else 0
        ever_ready=bool(self.jobs[ident].get('ever_ready') or self.jobs[ident].get('ready')) if ident in self.jobs else False
        title=str(program.get('title') or (Path(local_path).name if local_path else url) or tr('コメントのテスト'))
        self.jobs[ident]={'controller':controller,'program':program,'url':url,'title':title,'mode':mode,
            'local_path':local_path,'position':start_time,'duration':0,'seekable':False,'paused':False,
            'settings':settings,'state':tr('接続中'),'post_ready':False,'post':None,'manual_stop':False,
            'retries':retries,'error':False,'completed':False,'preview':preview,'schedule_id':schedule_id,
            'run_key':run_key,'restart_pending':False,
            'auth_snapshot':credentials,'ready':False,'ever_ready':ever_ready}
        if self.tree.exists(ident):self.tree.item(ident,values=(tr(MODE[mode]),title,tr('接続中')))
        else:self.tree.insert('',0,iid=ident,values=(tr(MODE[mode]),title,tr('接続中')))
        self.tree.selection_set(ident)
        self.status.set(tr('接続を開始しました。'));self.update_sleep()
        self.execution(self.jobs[ident],tr('開始'));self.save_state();return True

    def execution(self,job,status,reason=''):
        self.executions.append({'time':time.time(),'title':job['title'],'url':job['url'],'mode':job['mode'],'status':status,'reason':reason[:300]})
        self.executions=self.executions[-500:]

    def selected(self):
        selection=self.tree.selection();return self.jobs.get(selection[0]) if selection else None

    def selected_control(self,action,value=None):
        job=self.selected()
        if not job or not job['controller'].busy or job['mode']=='record':return
        try:
            job['controller'].control(action,value)
            if action=='volume':job['settings']['volume']=int(value)
            if action=='mute':job['settings']['muted']=bool(value)
        except (ValueError,OSError):self.status.set(tr('再生画面への操作を送れませんでした。'))

    def volume_changed(self,value):self.selected_control('volume',float(value))
    def seek_released(self,event=None):
        self.seeking=False;job=self.selected()
        if job and job['seekable']:self.selected_control('seek',self.position.get())

    def refresh_selection(self):
        job=self.selected()
        if not job:
            self.detail.set(tr('再生・録画する番組がここに並びます。'))
            self.seek.configure(state='disabled');self.time_label.set('00:00 / --:--');return
        self.detail.set(job['title']+'  ·  '+job.get('comment_status',''))
        self.volume.set(job['settings']['volume']);self.muted.set(job['settings']['muted'])
        self.seek.configure(to=max(1,job['duration']),state='normal' if job['seekable'] and job['controller'].busy else 'disabled')
        if not self.seeking:self.position.set(job['position'])
        self.time_label.set(clock_text(job['position'])+' / '+(clock_text(job['duration']) if job['duration'] else '--:--'))

    def stop_selected(self):
        job=self.selected()
        if job:self.stop_job(job)

    def stop_job(self,job,restarting=False):
        was_busy=job['controller'].busy
        job['manual_stop']=True;job['restart_pending']=restarting;job['post_ready']=False;job['state']=tr('停止中');job['controller'].stop()
        if job['post']:job['post'].stop()
        if not was_busy and not restarting:
            ident=next((i for i,j in self.jobs.items() if j is job),None)
            if ident:self.remove_job(ident)

    def remove_job(self,ident):
        job=self.jobs.pop(ident,None)
        if not job:return
        self.remember(job);job['controller'].close()
        if job['post']:job['post'].stop()
        if self.tree.exists(ident):self.tree.delete(ident)
        job['auth_snapshot']=None
        self.refresh_selection();self.update_sleep()

    def reconnect_selected(self):
        selection=self.tree.selection()
        if not selection:return
        ident=selection[0];job=self.jobs[ident]
        if job['mode']!='play':
            self.status.set(tr('録画中は停止してから新しく開始してください。録画済みファイルは保持されます。'));return
        self.stop_job(job,restarting=True);self.root.after(300,lambda:self.restart(ident,manual=True))

    def restart(self,ident,manual=False):
        if self.closed or self.closing:return
        job=self.jobs.get(ident)
        if not job or not job.get('restart_pending') or (job['manual_stop'] and not manual):return
        if job['controller'].busy:self.root.after(300,lambda:self.restart(ident,manual));return
        if not self.launch(job['program'],job['mode'],job['local_path'],job['position'] if job['seekable'] else 0,job['preview'],job['schedule_id'],replace_ident=ident):
            self.remove_job(ident)

    def open_post(self):
        job=self.selected()
        if not job or job['manual_stop'] or not job['post_ready'] or not job['controller'].busy:
            self.status.set(tr('コメント投稿は、ログインして再生中の生放送で利用できます。'));return
        if job['post'] and not job['post'].closed:job['post'].window.lift();return
        job['post']=PostWindow(self.root,job['controller'],job['controller'].current,job['url'])

    def handle_event(self,ident,event):
        job=self.jobs.get(ident)
        if not job:return
        kind=event.get('type');text=tr(event.get('text',''))
        if kind in ('status','error'):
            job['state']=text[:160]
            if kind=='error':job['error']=True;self.execution(job,tr('エラー'),text)
        elif kind=='metadata':
            job['title']=str(event.get('title') or job['title']);job['program'].update({k:event[k] for k in ('title','author','provider','id') if k in event})
            job['seekable']=bool(event.get('seekable',False))
        elif kind=='position':
            for key in ('position','duration','paused','seekable'):
                if key in event:job[key]=event[key]
            self.remember(job)
        elif kind=='recording':job['recording_path']=event.get('path','');job['state']=tr('録画中') if event.get('state')=='recording' else str(event.get('state',tr('録画')))
        elif kind=='completed':
            job['completed']=event.get('ok') is True;job['recording_path']=event.get('path',job.get('recording_path',''))
            self.execution(job,tr('完了') if event.get('ok') else tr('中断'));self.remember(job)
        elif kind=='player_closed':
            job['manual_stop']=True;job['restart_pending']=False;job['post_ready']=False;job['state']=tr('終了',context='state')
        elif kind=='comment_status':job['comment_status']=text
        elif kind=='post_ready':job['post_ready']=bool(event.get('enabled')) and not job['manual_stop']
        elif kind=='post_result':
            if job['post']:job['post'].receive(event)
        elif kind=='session_invalid' and job.get('auth_snapshot')==self.credentials():
            self.account.browser_session=[]
            try:self.account.session_store.delete()
            except OSError:pass
            self.account.auth_status.set(tr('ログイン期限が切れました。ブラウザでログインし直してください。'))
        elif kind=='otp_required':self.ask_otp(job,event.get('request_id',''))
        elif kind=='authenticated':
            credentials=job.get('auth_snapshot') or {}
            if self.account.remember.get() and credentials==self.credentials() and credentials.get('password'):
                try:self.account.credential_store.save(credentials['email'],credentials['password'])
                except (OSError,ValueError):pass
        elif kind=='job_ready':
            job['ready']=True;job['ever_ready']=True;job['retries']=0
            if job['schedule_id']:
                self.scheduler.ack(job['schedule_id'],True,tr('実行中'),job['program']);self.save_state();self.refresh_reservations()
                self.sound.notify(job['mode'],self.settings)
        elif kind=='stopped':
            job['post_ready']=False
            if job['post']:job['post'].stop()
            if job['state'] in (tr('接続中'),tr('停止中')):job['state']=tr('停止')
            if job['schedule_id'] and not job['ready'] and not job.get('ever_ready'):
                self.scheduler.ack(job['schedule_id'],job['manual_stop'],tr('手動で終了しました。') if job['manual_stop'] else tr('接続に失敗しました。再試行を待っています。'),job['program'])
            if job['error'] and not job['manual_stop'] and not job['completed'] and not job['local_path'] and (not job['schedule_id'] or job['ready'] or job.get('ever_ready')) and (job['mode']=='play' or not job['seekable']) and job['retries']<3 and self.settings['reconnect']:
                job['restart_pending']=True;job['state']=tr('再接続待ち');self.root.after(3000*(job['retries']+1),lambda:self.restart(ident))
            self.remember(job);self.save_state();self.update_sleep()
            job['controller'].close()
            job['auth_snapshot']=None
            if not job.get('restart_pending'):self.remove_job(ident)
        if self.tree.exists(ident):self.tree.item(ident,values=(tr(MODE[job['mode']]),job['title'],tr(job['state'])))
        self.refresh_selection()

    def ask_otp(self,job,request_id):
        code=simpledialog.askstring(tr('二段階認証'),tr('メールに届いた6桁の認証コード'),parent=self.root)
        job['controller'].submit_otp(request_id,code if code and len(code)==6 and code.isdecimal() else None)

    def remember(self,job):
        if job['preview'] or job['mode']=='record':return
        key=job['local_path'] or job['url']
        if not key:return
        old=next((h for h in self.history if (h.get('local_path') or h.get('url'))==key),{})
        entry={'url':job['url'],'title':job['title'],'position':job['position'],'duration':job['duration'],'seekable':job['seekable'],'local_path':job['local_path'],'seen_at':time.time(),'watched':old.get('watched',False)}
        if job['duration'] and job['position']>job['duration']*.95:entry['watched']=True
        self.history=[entry]+[h for h in self.history if (h.get('local_path') or h.get('url'))!=key][:199]

    def save_state(self):
        self.state.update(reservations=self.scheduler.snapshot(),executions=self.executions,history=self.history)
        try:self.store.save_state(self.state)
        except (OSError,ValueError):self.status.set(tr('状態を保存できません。保存先の空き容量を確認してください。'))

    def poll(self):
        if self.closed:return
        instance_event=getattr(self.root,'_instance_event',None)
        if instance_event and os.name=='nt':
            import ctypes
            if ctypes.windll.kernel32.WaitForSingleObject(instance_event,0)==0:self.show()
        if self.background and time.monotonic()-self.last_push_check>=1:
            self.last_push_check=time.monotonic();self.ensure_webpush()
        for ident,job in list(self.jobs.items()):
            for event in job['controller'].poll_events():self.handle_event(ident,event)
        for _ in range(50):
            try:kind,data=self.events.get_nowait()
            except queue.Empty:break
            if kind in ('launch','launch_push'):
                if kind=='launch_push':
                    generation,reservation,program,mode=data
                    if generation!=self.push_generation or generation!=session_fingerprint(self.cookies()):continue
                else:reservation,program,mode=data
                if not self.closing:
                    self.expire_recording_reservations()
                    if any(r['id']==reservation and r['state']=='queued' for r in self.scheduler.snapshot()):
                        self.launch(program,mode,schedule_id=reservation)
            elif kind=='schedule_done':self.save_state();self.refresh_reservations()
            elif kind=='push':
                generation,payload=data
                if generation==self.push_generation and generation==session_fingerprint(self.cookies()):self.handle_push(payload)
            elif kind=='push_resolved':
                generation,program=data
                if generation==self.push_generation and generation==session_fingerprint(self.cookies()):
                    self.expire_recording_reservations()
                    self._deliver_push(generation,program)
            elif kind=='push_status':
                generation,state=data
                if generation==self.push_generation:self.handle_push_status(state)
            elif kind=='push_follow':
                generation,cancel,user,result=data
                if generation!=self.push_generation or cancel.is_set():continue
                self.push_follow_results[user]=result
                message=self.follow_result_message(result)
                with self.scheduler.lock:
                    for r in self.scheduler.records:
                        if r['state']=='waiting' and (r.get('notify_user_id')==user or r.get('user_id')==user or r.get('channel_id')==user or r.get('notify_channel_id')==user):r['message']=message
                self.refresh_push_follow_status()
                self.save_state();self.refresh_reservations()
        if time.monotonic()-self.last_saved>=30:
            self.save_state();self.last_saved=time.monotonic()
        self.root.after(100,self.poll)

    def expire_recording_reservations(self,now=None):
        protected={job['schedule_id'] for job in self.jobs.values()
                   if job.get('schedule_id') and (job['controller'].busy or job.get('restart_pending'))}
        expired=self.scheduler.expire_recordings(now=now,protected_ids=protected)
        if expired:
            for record in expired:
                self.execution(record,tr('予約期限切れ'),tr('開始予定から1時間以内に実行できなかったため、予約を自動削除しました。'))
            self.save_state();self.refresh_reservations()
        return expired

    def schedule_tick(self):
        if self.closed or self.closing:return
        now=time.time()
        self.expire_recording_reservations(now)
        generation=self.push_generation
        self.scheduler.retry_push(lambda ident,p,mode:self.events.put(('launch_push',(generation,ident,p,mode))),
                                  preflight=lambda p,mode:self.preflight(p,mode),now=now)
        if self.scheduler.search_due(now) and not getattr(self,'scheduling',False):
            self.scheduling=True;cookies=self.cookies()
            def run():
                catalog=None
                try:
                    from .catalog import Catalog
                    catalog=Catalog(cookies)
                    self.scheduler.expand_rules(catalog,now)
                except Exception:
                    self.scheduler.defer_searches(now)
                finally:
                    if catalog:catalog.close()
                    self.scheduling=False;self.events.put(('schedule_done',None))
            threading.Thread(target=run,daemon=True,name='official-search').start()
        self.root.after(1000,self.schedule_tick)

    def ensure_webpush(self):
        if self.closed or self.closing:return
        cookies=self.cookies()
        generation=session_fingerprint(cookies)
        if generation!=self.push_generation:
            self.push_follow_cancel.set()
            if self.webpush:self.webpush.stop()
            self.webpush=None;self.push_ready=False;self.push_generation=generation
            with self.scheduler.lock:
                for r in self.scheduler.records:
                    if r['id'] in self.scheduler.pending_push and r['state']=='queued':r['state']='waiting'
            self.push_attempted.clear();self.scheduler.pending_push.clear()
            self.push_follow_results.clear();self.push_follow_status.set('')
        if not generation:
            self.push_status.set(tr('プッシュ通知: ログインが必要です。'))
            return
        if self.webpush:
            if self.push_ready:self.start_push_follows(cookies)
            return
        # Never call cookies()/Tk variables from the socket thread.
        snapshot=copy.deepcopy(cookies)
        self.webpush=WebPushClient(lambda:snapshot,
            lambda payload:self.events.put(('push',(generation,payload))),
            storage_path=self.store.directory/'push-session.json',
            on_status=lambda state:self.events.put(('push_status',(generation,state))))
        if self.force_push_registration:
            self.webpush.registered_for='';self.force_push_registration=False
        self.webpush.start()

    def retry_push_setup(self):
        self.push_follow_cancel.set()
        self.push_attempted.clear();self.push_follow_results.clear()
        self.push_reset_requested=True
        if not (self.webpush and self.push_ready):
            if self.webpush:self.webpush.stop()
            self.webpush=None;self.push_ready=False;self.force_push_registration=True
        self.ensure_webpush()

    def handle_push_status(self,state):
        labels={
            'connecting':tr('プッシュ通知: 接続しています…'),
            'registering':tr('プッシュ通知: 受信先を登録しています…'),
            'ready':tr('プッシュ通知: 接続済み・通知待ち'),
            'received':tr('プッシュ通知: 通知を受信しました。'),
            'disconnected':tr('プッシュ通知: 切断・再接続を待っています。'),
            'login_required':tr('プッシュ通知: ログインが必要です。'),
            'registration_failed':tr('プッシュ通知: 受信先の登録に失敗しました。再試行ボタンでやり直せます。'),
            'rate_limited':tr('プッシュ通知: 接続先から制限されました。自動登録を停止しました。'),
            'decode_failed':tr('プッシュ通知: 読み取れない通知を受信しました。'),
            'storage_failed':tr('プッシュ通知: 登録情報を保存できませんでした。'),
        }
        kind=state.get('state')
        if kind=='ready':self.push_ready=True
        elif kind in ('connecting','disconnected','login_required','registration_failed','rate_limited'):self.push_ready=False
        message=labels.get(kind,tr('プッシュ通知: 状態を確認してください。'))
        if isinstance(state.get('http_status'),int):message+=f" [HTTP {state['http_status']}]"
        self.push_status.set(message)

    @staticmethod
    def follow_result_message(result):
        if result['ok']:return tr('プッシュ通知待ち')
        labels={
            'follow_deferred':'通知用フォローは保留中です。原因を確認して再試行してください。',
            'rate_limited':'通知用フォロー: アクセス制限のため追加登録を停止しました。',
            'login_required':'通知用フォロー: ログインを確認して再試行してください。',
            'access_denied':'通知用フォロー: アクセスが拒否されたため追加登録を停止しました。',
            'network_error':'通知用フォロー: 通信に失敗したため追加登録を停止しました。',
            'follow_button_not_found':'通知用フォロー: フォローボタンを読み取れません。',
        }
        text=labels.get(result.get('state')) or labels.get(result.get('reason'))
        message=tr(text or '通知用フォローの登録に失敗しました。再試行してください。')
        stages={'channel_page':'チャンネルページ取得','channel_status':'チャンネルのフォロー状態確認',
                'channel_follow':'チャンネルのフォロー登録','user_follow':'ユーザーのフォロー登録'}
        if result.get('stage') in stages:message+=' ('+tr(stages[result['stage']])+')'
        if isinstance(result.get('http_status'),int):message+=f" [HTTP {result['http_status']}]"
        if isinstance(result.get('api_status'),int):message+=f" [API {result['api_status']}]"
        return message

    def refresh_push_follow_status(self):
        results=list(self.push_follow_results.values())
        if any(r.get('stop_batch') for r in results):
            message=tr('通知用フォロー: 追加登録を停止しました。予約一覧で理由を確認して再試行してください。')
        elif any(not r['ok'] and r.get('state')!='follow_deferred' for r in results):
            message=tr('通知用フォロー: 一部の登録に失敗しました。予約一覧で理由を確認してください。')
        elif any(r.get('state')=='follow_deferred' for r in results):
            message=tr('通知用フォローは保留中です。原因を確認して再試行してください。')
        else:message=tr('通知用フォロー: 登録済み') if results else ''
        self.push_follow_status.set(message)

    def start_push_follows(self,cookies):
        if self.push_setup_thread and self.push_setup_thread.is_alive():return
        generation=session_fingerprint(cookies)
        records=[r for r in self.scheduler.snapshot() if r['state']=='waiting']
        user_targets={r.get('notify_user_id') or r.get('user_id') for r in records}
        user_targets={('user', u) for u in user_targets if u and u.isascii() and u.isdigit() and (generation,u) not in self.push_attempted}
        channel_targets={r.get('notify_channel_id') or r.get('channel_id') for r in records if (r.get('notify_channel_id') or r.get('channel_id'))}
        channel_targets={('channel', ch) for ch in channel_targets if ch and (ch.startswith('ch') or ch.isdigit()) and (generation,ch) not in self.push_attempted}
        targets=user_targets | channel_targets
        if not targets:return
        snapshot=copy.deepcopy(cookies)
        reset=self.push_reset_requested;self.push_reset_requested=False
        cancel=threading.Event();self.push_follow_cancel=cancel
        self.push_attempted.update((generation,t[1]) for t in targets)
        self.push_follow_status.set(tr('通知用フォロー: 登録を確認しています…'))
        def work():
            if reset:self.follow_subscriptions.reset_failures()
            for target_type,target_id in sorted(targets):
                if self.stop_event.is_set() or cancel.is_set() or generation!=self.push_generation:break
                result=self.follow_subscriptions.follow(target_id,snapshot,target_type=target_type,cancel=cancel)
                if cancel.is_set() or generation!=self.push_generation:break
                self.events.put(('push_follow',(generation,cancel,target_id,result)))
                # The subscription store returns cached successes or distinct deferred
                # results after a batch refusal, without further network requests.
                if not result.get('cached') and result.get('state')!='follow_deferred' and cancel.wait(1):break
        self.push_setup_thread=threading.Thread(target=work,daemon=True,name='push-follow-setup')
        self.push_setup_thread.start()

    def handle_push(self,payload):
        program=parse_notification(payload)
        if not program:return
        self.expire_recording_reservations()
        generation=self.push_generation
        if (not program.get('user_id') and not program.get('channel_id')
                and any(r['state']=='waiting' and (r.get('user_id') or r.get('channel_id')) for r in self.scheduler.snapshot())):
            cookies=copy.deepcopy(self.cookies())
            if cookies:
                def resolve():
                    catalog=None;enriched=dict(program)
                    try:
                        from .catalog import Catalog
                        catalog=Catalog(cookies)
                        info=catalog.program(program['url'])
                        for k in ('user_id','channel_id','title','author'):
                            if info.get(k) and not enriched.get(k):enriched[k]=info[k]
                    except Exception:pass
                    finally:
                        if catalog:catalog.close()
                    self.events.put(('push_resolved',(generation,enriched)))
                threading.Thread(target=resolve,daemon=True,name='push-resolve').start()
                return
        self._deliver_push(generation,program)

    def _deliver_push(self,generation,program):
        self.scheduler.notify_live(program,
            lambda ident,p,mode:self.events.put(('launch_push',(generation,ident,p,mode))),
            preflight=lambda p,mode:self.preflight(p,mode))
        self.save_state();self.refresh_reservations()

    def open_discovery(self):
        from .discovery_ui import DiscoveryWindow
        DiscoveryWindow(self.root,self.cookies(),self.launch,ffmpeg_path=self.settings['ffmpeg_path'])
    def open_schedule_picker(self,mode='play'):
        from .discovery_ui import SchedulePicker
        SchedulePicker(self.root,self.cookies(),self.add_reservation,ffmpeg_path=self.settings['ffmpeg_path'],mode=mode,on_reservations=self.open_reservations,
                       reserved_keys=self.scheduler.keys,on_uncheck=self.remove_reservation)
    def add_reservation(self,program,mode,show=True):
        """Returns '' on success (or already reserved), otherwise the error text."""
        key=reservation_key(program)
        if key:
            wanted={'play','record'} if mode=='both' else {mode}
            occupied=({m for m in ('play','record') if key in self.scheduler.keys(m)})
            missing=wanted-occupied
            if not missing:
                self.status.set(tr('この番組・ユーザーは予約済みです。'))
                if show:self.open_reservations()
                return ''
            if len(missing)==1:mode=next(iter(missing))
        kw=str(program.get('keyword') or '').strip()
        if kw:
            wanted={'play','record'} if mode=='both' else {mode}
            occupied={m for r in self.scheduler.snapshot() if str(r.get('keyword') or '').strip().casefold()==kw.casefold() for m in (('play','record') if r['mode']=='both' else (r['mode'],))}
            missing=wanted-occupied
            if not missing:
                msg=tr('このキーワードは同じ動作で登録済みです。');self.status.set(msg);return msg
            if len(missing)==1:mode=next(iter(missing))
        tag=str(program.get('official_tag') or '').strip()
        if tag:
            wanted={'play','record'} if mode=='both' else {mode}
            occupied={m for r in self.scheduler.snapshot() if str(r.get('official_tag') or '').strip().casefold()==tag.casefold() for m in (('play','record') if r['mode']=='both' else (r['mode'],))}
            missing=wanted-occupied
            if not missing:
                msg=tr('このタグは同じ動作で登録済みです。');self.status.set(msg);return msg
            if len(missing)==1:mode=next(iter(missing))
        try:ident=self.scheduler.add(program,mode)
        except ValueError as exc:self.status.set(tr(str(exc)));return str(exc)
        # A handled target is not requested again. Reuse its result for a new
        # reservation too, including another mode and delete/re-add operations.
        with self.scheduler.lock:
            record=next(r for r in self.scheduler.records if r['id']==ident)
            known=[self.push_follow_results[target] for target in
                   (record.get('notify_user_id'),record.get('user_id'),record.get('notify_channel_id'),record.get('channel_id'))
                   if target in self.push_follow_results]
            if known:
                result=next((r for r in known if not r['ok']),known[0])
                record['message']=self.follow_result_message(result)
        self.save_state();self.status.set(tr('予約を追加しました。ソフトを起動している間に自動実行します。'))
        if show:self.open_reservations()
        else:self.refresh_reservations()
        return ''
    def remove_reservation(self,program,mode):
        self.scheduler.remove_key(reservation_key(program),mode)
        self.save_state();self.refresh_reservations();self.status.set(tr('予約を解除しました。'))

    def open_reservations(self):
        if self.reservation_window and self.reservation_window.winfo_exists():self.reservation_window.lift();self.refresh_reservations();return
        top=tk.Toplevel(self.root);self.reservation_window=top;top.title(tr('予約と実行履歴'));top.geometry('940x500')
        row=ttk.Frame(top);row.pack(side='top',fill='x',padx=16,pady=(10,0))
        reservation_actions=ttk.Frame(row);reservation_actions.pack(side='left')
        history_actions=ttk.Frame(row)
        content=zoom_container(top,always=True)
        book=ttk.Notebook(content);book.pack(fill='both',expand=True,padx=16,pady=16)
        tabs=[]
        for label in (tr('予約'),tr('実行履歴')):
            frame=ttk.Frame(book);book.add(frame,text=label);tabs.append(frame)
        reserved=ScrollableTree(tabs[0],columns=('start','mode','title','source','state'),show='headings')
        self.reservations_tree=reserved.tree
        for k,title,width in [('start',tr('開始予定'),130),('mode',tr('動作'),90),('title',tr('番組・ユーザー'),310),('source',tr('予約元'),160),('state',tr('状態'),220)]:self.reservations_tree.heading(k,text=title,command=lambda col=k:self.sort_reservations(col));self.reservations_tree.column(k,width=width)
        reserved.pack(fill='both',expand=True)
        ttk.Button(reservation_actions,text=tr('予約を追加'),command=self.open_schedule_picker).pack(side='left')
        def remove():
            for ident in self.reservations_tree.selection():self.scheduler.remove(ident)
            self.save_state();self.refresh_reservations()
        remove_button=ttk.Button(reservation_actions,text=tr('選択した予約を削除'),command=remove)
        remove_button.pack(side='left',padx=8)
        def clear_history():
            if book.index('current')!=1 or not self.executions:return
            self.executions.clear();self.save_state();self.refresh_reservations()
        ttk.Button(history_actions,text=tr('実行履歴をクリア'),command=clear_history).pack(side='left')
        def show_actions(event=None):
            reserved_tab=book.index('current')==0
            remove_button.configure(state='normal' if reserved_tab else 'disabled')
            if reserved_tab:
                history_actions.pack_forget();reservation_actions.pack(side='left')
            else:
                reservation_actions.pack_forget();history_actions.pack(side='left')
        book.bind('<<NotebookTabChanged>>',show_actions)
        show_actions()
        executed=ScrollableTree(tabs[1],columns=('time','title','status'),show='headings')
        self.execution_tree=executed.tree
        for k,title,width in [('time',tr('実行日時'),150),('title',tr('番組'),420),('status',tr('結果'),250)]:self.execution_tree.heading(k,text=title,command=lambda col=k:self.sort_executions(col));self.execution_tree.column(k,width=width)
        executed.pack(fill='both',expand=True);self.refresh_reservations()
        fit_window(top,940,500,flex=book)
    def sort_reservations(self,col):
        cur,rev=self.reservation_sort
        self.reservation_sort=(col,not rev if cur==col else False)
        self.refresh_reservations()
    def sort_executions(self,col):
        cur,rev=self.execution_sort
        self.execution_sort=(col,not rev if cur==col else False)
        self.refresh_reservations()
    def refresh_reservations(self):
        if not self.reservation_window or not self.reservation_window.winfo_exists():return
        col,rev=self.reservation_sort
        for k,title in [('start',tr('開始予定')),('mode',tr('動作')),('title',tr('番組・ユーザー')),('source',tr('予約元')),('state',tr('状態'))]:
            self.reservations_tree.heading(k,text=title+(' ▼' if rev else ' ▲') if k==col else title)
        selected=set(self.reservations_tree.selection())
        self.reservations_tree.delete(*self.reservations_tree.get_children())
        snapshot=self.scheduler.snapshot()
        rules=[r for r in snapshot if r.get('keyword') or r.get('official_tag')]
        rows=[]
        for idx,r in enumerate(snapshot):
            start=datetime.fromtimestamp(r['start']).strftime('%y/%m/%d %H:%M') if r['start'] else tr('随時')
            message=r.get('message') or STATE.get(r['state'],r['state'])
            if r['state']=='waiting':
                if (r.get('keyword') or r.get('official_tag')) and message in ('待機中','待機'):
                    message=tr('次の公式検索: {0}（日本時間）').format(datetime.fromtimestamp(r['search_next_at'],JST).strftime('%y/%m/%d %H:%M'))
                elif not (r.get('keyword') or r.get('official_tag') or r.get('notify_user_id') or r.get('user_id') or r.get('channel_id') or r.get('notify_channel_id') or r.get('url')):message=tr('通知対象が不明です。配信者の予約または通知設定を確認してください。')
                elif message in ('待機中','待機'):message=tr('プッシュ通知待ち')
            source=r.get('source') or ''
            if not source and r.get('url') and rules:
                matched=next((rule for rule in rules if r['url'] in rule.get('seen',[])),None)
                if matched:source=matched['title']
            rows.append((idx,r,(start,tr(MODE[r['mode']]),r['title'],source or tr('手動'),tr(message,context='state'))))
        if col:
            cidx={'start':0,'mode':1,'title':2,'source':3,'state':4}[col]
            if col=='start':rows.sort(key=lambda item:(item[1].get('start',0) or 0,item[2][2].casefold(),item[0]),reverse=rev)
            else:rows.sort(key=lambda item:(str(item[2][cidx]).casefold(),item[2][2].casefold(),item[0]),reverse=rev)
        for _,r,values in rows:self.reservations_tree.insert('','end',iid=r['id'],values=values)
        restore=[r['id'] for _,r,_ in rows if r['id'] in selected]
        if restore:self.reservations_tree.selection_set(restore)
        ecol,erev=self.execution_sort
        for k,title in [('time',tr('実行日時')),('title',tr('番組')),('status',tr('結果'))]:
            self.execution_tree.heading(k,text=title+(' ▼' if erev else ' ▲') if k==ecol else title)
        self.execution_tree.delete(*self.execution_tree.get_children())
        exec_rows=[]
        for idx,e in enumerate(reversed(self.executions[-200:])):
            exec_rows.append((idx,e,(datetime.fromtimestamp(e['time']).strftime('%y/%m/%d %H:%M'),e['title'],tr(e['status'])+(' · '+tr(e['reason']) if e.get('reason') else ''))))
        if ecol:
            ecidx={'time':0,'title':1,'status':2}[ecol]
            if ecol=='time':exec_rows.sort(key=lambda item:(item[1].get('time',0) or 0,item[2][1].casefold(),item[0]),reverse=erev)
            else:exec_rows.sort(key=lambda item:(str(item[2][ecidx]).casefold(),item[2][1].casefold(),item[0]),reverse=erev)
        for _,_,values in exec_rows:self.execution_tree.insert('','end',values=values)

    def open_local(self):
        path=filedialog.askopenfilename(parent=self.root,title=tr('録画を開く（同名のコメントを自動読込）'),filetypes=[(tr('動画'),'*.ts *.mp4 *.mkv *.webm'),(tr('すべて'),'*.*')])
        if path:
            old=next((h for h in self.history if h.get('local_path')==path),{})
            start=old.get('position',0) if old and messagebox.askyesno(tr('続きから再生'),tr('{0} から再生しますか？').format(clock_text(old.get('position',0))),parent=self.root) else 0
            self.play_local(path,start)
    def play_local(self,path,start_time=0):self.launch({'title':Path(path).name},local_path=path,start_time=start_time)
    def open_library(self):
        from .library_ui import LibraryWindow
        LibraryWindow(self.root,self.settings['recording_dir'] or str(Path.home()/'Videos'/'NicoLive Studio'),self.history,self.play_local,self.mark_watched,ffmpeg_path=self.settings['ffmpeg_path'])
    def open_history(self):
        top=tk.Toplevel(self.root);top.title(tr('視聴履歴'));top.geometry('860x460')
        content=zoom_container(top,always=True)
        listing=ScrollableTree(content,columns=('title','position','date'),show='headings',selectmode='browse')
        tree=listing.tree
        for key,label,width in [('title',tr('番組'),500),('position',tr('再開位置'),100),('date',tr('最終視聴'),160)]:tree.heading(key,text=label);tree.column(key,width=width)
        listing.pack(fill='both',expand=True,padx=16,pady=16)
        rows=copy.deepcopy(self.history)
        for i,h in enumerate(rows):
            date=datetime.fromtimestamp(h['seen_at']).strftime('%y/%m/%d %H:%M') if h.get('seen_at') else ''
            tree.insert('','end',iid=str(i),values=(h.get('title',''),clock_text(h.get('position',0)) if h.get('seekable') or h.get('local_path') else tr('生放送'),date))
        def play(resume):
            if not tree.selection():return
            h=rows[int(tree.selection()[0])];start=h.get('position',0) if resume and (h.get('seekable') or h.get('local_path')) else 0
            self.launch({'url':h.get('url',''),'title':h.get('title','')},local_path=h.get('local_path',''),start_time=start)
        buttons=ttk.Frame(content,padding=12);buttons.pack(side='bottom',fill='x',before=listing)
        ttk.Button(buttons,text=tr('続きから再生'),command=lambda:play(True)).pack(side='right',padx=8)
        ttk.Button(buttons,text=tr('最初から／生放送を再生'),command=lambda:play(False)).pack(side='right')
        fit_window(top,860,460,flex=listing)
    def mark_watched(self,path,watched=True):
        item=next((h for h in self.history if h.get('local_path')==path),None)
        if item:item['watched']=watched
        else:self.history.insert(0,{'local_path':path,'url':'','title':Path(path).name,'position':0,'watched':watched})
        self.save_state()

    def open_settings(self):SettingsDialog(self.root,self.settings,self.apply_settings)
    def toggle_theme(self):
        values=dict(self.settings);values['theme']='light' if values['theme']=='dark' else 'dark';self.apply_settings(values)
    def toggle_comments(self):
        values=dict(self.settings);values['enabled']=self.comments_enabled.get();self.apply_settings(values)
    def apply_settings(self,values):
        values=clean_settings(values)
        if values['startup']!=self.settings['startup']:
            command=None if getattr(sys,'frozen',False) else subprocess.list2cmdline([sys.executable,str(app_dir()/'launch.py')])
            set_startup(values['startup'],command)
        volume_changed=values['volume']!=self.settings['volume'];mute_changed=values['muted']!=self.settings['muted']
        self.settings=self.store.save_settings(values);self.comments_enabled.set(values['enabled'])
        for job in self.jobs.values():
            old=job['settings'];new=dict(values);new['svp_enabled']=old['svp_enabled']
            if not mute_changed:new['muted']=old['muted']
            if not volume_changed:new['volume']=old['volume']
            job['settings']=new;job['controller'].configure(new)
        self.apply_theme();self.update_sleep()
        restart=values['language']!=get_language() or values['ui_scale']!=round(zoom_factor(self.root)*100)
        self.status.set(tr('設定を保存しました。言語と画面の拡大率はソフトの再起動後に適用されます。') if restart else tr('設定を保存しました。画質と再生ソフトの変更は次の再生から適用されます。'))
    def apply_theme(self):
        dark=self.settings['theme']=='dark';self.root._dark_mode=dark;bg='#212121' if dark else '#f4f6fa';fg='#ffffff' if dark else '#243149';field='#303030' if dark else '#ffffff'
        self.root.configure(bg=bg);style=ttk.Style(self.root);style.theme_use('clam')
        style.configure('.',background=bg,foreground=fg,font=('Yu Gothic UI',10))
        for kind in ('TLabel','TLabelframe.Label','TCheckbutton','TRadiobutton'):
            style.configure(kind,background=bg,foreground=fg)
        zoom=zoom_factor(self.root)
        style.configure('TButton',background=field,padding=(int(12*zoom),int(8*zoom)));style.configure('TEntry',fieldbackground=field,foreground=fg,insertcolor=fg,padding=int(7*zoom))
        style.configure('TScrollbar',arrowsize=int(14*zoom))
        style.configure('TCheckbutton',padding=int(3*zoom),indicatorsize=int(14*zoom))
        style.configure('TRadiobutton',padding=int(3*zoom),indicatorsize=int(14*zoom))
        style.configure('TNotebook.Tab',padding=(int(8*zoom),int(4*zoom)))
        style.configure('TCombobox',fieldbackground=field,foreground=fg,arrowcolor=fg)
        self.root.option_add('*TCombobox*Listbox.foreground',fg)
        self.root.option_add('*TCombobox*Listbox.background',field)
        self.root.option_add('*TCombobox*Listbox.selectForeground','#ffffff')
        self.root.option_add('*TCombobox*Listbox.selectBackground','#505050')
        for kind in ('TButton','TCheckbutton','TRadiobutton','TCombobox','TEntry','TNotebook.Tab'):
            style.map(kind,foreground=[('disabled',fg),('active',fg),('!disabled',fg)])
        tab_fg=fg
        style.configure('TNotebook.Tab',foreground=tab_fg)
        style.map('TNotebook.Tab',foreground=[('disabled',tab_fg),('selected',tab_fg),('active',tab_fg),('!disabled',tab_fg)])
        style.map('TButton',background=[('active','#424242' if dark else '#e2e7ef')])
        style.map('TCombobox',fieldbackground=[('readonly',field)],selectforeground=[('readonly',fg)],selectbackground=[('readonly',field)])
        style.configure('MenuBar.TFrame',background='#171717' if dark else bg)
        style.configure('Menu.TButton',background='#171717' if dark else bg,foreground=fg,anchor='w',borderwidth=0)
        style.map('Menu.TButton',background=[('active','#424242' if dark else '#e2e7ef')],foreground=[('active',fg)])
        style.configure('Treeview',background=field,fieldbackground=field,foreground=fg,rowheight=int(38*zoom),borderwidth=0)
        style.configure('Treeview.Heading',padding=int(8*zoom),background=bg,foreground=fg)
        style.map('Treeview',background=[('selected','#424242' if dark else '#285da8')],foreground=[('selected','white')])
        style.configure('Accent.TButton',background='#424242' if dark else '#3678dc',foreground='white')
        style.map('Accent.TButton',background=[('active','#505050' if dark else '#245eb7')])
        style.configure('Muted.TLabel',foreground=fg)
        self.paint_ttk(style,dark,bg,fg,field)
        if self.account_window:
            self.account_window.configure(bg=bg)
            for child in self.account_window.winfo_children():
                if isinstance(child,tk.Canvas):child.configure(bg=bg)

        theme_widgets(self.root)

    def paint_ttk(self,style,dark,bg,fg,field):
        """Darken clam's remaining light borders, troughs and indicators; light mode restores clam's own values."""
        saved=self.__dict__.setdefault('_clam_light',{})
        edge,lit,shade,trough,thumb,hover='#505050','#424242','#303030','#171717','#424242','#505050'
        def paint(name,options=None,maps=None,light=None):
            for option in options or {}:
                if (name,option) not in saved:saved[(name,option)]=style.lookup(name,option)
            for option in maps or {}:
                if (name,option,'map') not in saved:saved[(name,option,'map')]=style.map(name,query_opt=option)
            if dark:
                if options:style.configure(name,**options)
                if maps:style.map(name,**maps)
            else:
                values={option:(light or {}).get(option,saved[(name,option)]) for option in options or {}}
                if values:style.configure(name,**{k:v for k,v in values.items() if v})
                for option in maps or {}:
                    value=saved[(name,option,'map')] or saved.get(('.',option,'map'))
                    if value:style.map(name,**{option:value})
        frame={'bordercolor':edge,'lightcolor':lit,'darkcolor':shade}
        paint('.',maps={'background':[('disabled',bg),('active',thumb)]})
        for name in ('TButton','TEntry','TCombobox','TSpinbox','TScrollbar','TScale','Treeview','Treeview.Heading','TCheckbutton','TRadiobutton','TLabelframe','TNotebook','TSeparator'):
            paint(name,frame)
        paint('TNotebook.Tab',{**frame,'background':field},{'background':[('selected',thumb),(field,)],'lightcolor':[('selected',lit),(shade,)]})
        paint('TButton',maps={'lightcolor':[('pressed',shade)],'darkcolor':[('pressed',shade)]})
        paint('TScrollbar',{'troughcolor':trough,'arrowcolor':fg},{'background':[('disabled',shade),('active',hover),(thumb,)]},light={'arrowcolor':'#000000'})
        paint('TScale',{'troughcolor':trough},{'background':[('disabled',thumb),('active','#6a6a6a'),(hover,)]})
        paint('TSeparator',maps={'background':[(edge,)]})
        paint('TCombobox',maps={'background':[('active',hover),('pressed',hover),(thumb,)],'bordercolor':[('focus',hover)]})
        paint('TSpinbox',{'fieldbackground':field,'arrowcolor':fg},{'background':[('readonly',field),(thumb,)],'bordercolor':[('focus',hover)]},light={'fieldbackground':'#ffffff','arrowcolor':'#000000'})
        paint('TEntry',maps={'background':[('readonly',field)],'bordercolor':[('focus',hover)],'lightcolor':[('focus',edge)]})
        paint('Treeview.Heading',maps={'background':[('active',thumb)]})
        for name in ('TCheckbutton','TRadiobutton'):
            paint(name,{'indicatorbackground':field,'indicatorforeground':fg},{'indicatorbackground':[('pressed',thumb),('disabled',bg)],'indicatorforeground':[('disabled','#999999')]})

    def export_settings(self):
        path=filedialog.asksaveasfilename(parent=self.root,defaultextension='.json',initialfile='NicoLive-settings.json')
        if path:self.file_action(lambda:self.store.export_settings(path),tr('設定を書き出しました（ログイン情報は含みません）。'))
    def import_settings(self):
        path=filedialog.askopenfilename(parent=self.root,filetypes=[(tr('設定'),'*.json')])
        if path:self.file_action(lambda:self.apply_settings(self.store.import_settings(path)),tr('設定を読み込みました。'))
    def restore_settings(self):
        def restore():self.store.restore_backup();self.apply_settings(self.store.load_settings())
        self.file_action(restore,tr('設定のバックアップを復元しました。'))
    def export_diagnostics(self):
        path=filedialog.asksaveasfilename(parent=self.root,defaultextension='.json',initialfile='NicoLive-diagnostics.json')
        if path:self.file_action(lambda:self.store.export_diagnostics(path,{'job_count':len(self.jobs),'running_count':sum(j['controller'].busy for j in self.jobs.values())}),tr('診断情報を書き出しました。'))
    def file_action(self,action,success):
        try:action();self.status.set(success)
        except (OSError,ValueError) as exc:messagebox.showerror(tr('操作できません'),tr(str(exc)),parent=self.root)
    def preview(self):self.launch({},preview=True)
    def help(self):messagebox.showinfo(tr('使い方'),tr('番組URLを入力して再生します。\n「自動起動」で再生、「自動録画」で録画を予約できます。ソフトの起動中に実行します。\n録画は映像とコメントを別ファイルで保存し、一緒に再生します。\nコメントFPSは設定画面で数値を指定できます。\n\nプレミアム限定番組は視聴権限のあるアカウントが必要です。\n公式ギフトの演出については同梱の機能確認表をご覧ください。'),parent=self.root)
    def update_sleep(self):
        active=bool(self.settings['inhibit_sleep'] and any(j['controller'].busy for j in self.jobs.values()))
        if active!=self.sleep.active:
            try:self.sleep.set_active(active)
            except OSError:self.status.set(tr('自動スリープの抑制を設定できませんでした。'))
    def show(self):
        if self.tray:self.tray.stop()
        self.root.deiconify()
        self.root.state('normal')
        self.root.update_idletasks()
        self.root.lift()
        while self._hidden_toplevels:
            win=self._hidden_toplevels.pop(0)
            if win.winfo_exists():win.deiconify();win.lift()
        if os.name == 'nt':
            try:
                import ctypes
                user = ctypes.windll.user32
                hwnd = user.GetParent(self.root.winfo_id())
                if hwnd:
                    user.ShowWindow(hwnd, 9)
                    user.SetForegroundWindow(hwnd)
            except Exception:
                pass
        self.root.focus_force()
    def close_requested(self):
        if self.settings['tray']:
            try:
                if not self.tray:self.tray=TrayIcon(self.show,self.quit_requested,self.root)
                if self.tray.start():
                    self._hidden_toplevels=[w for w in self.root.winfo_children() if isinstance(w,tk.Toplevel) and w.winfo_exists() and w.state()!='withdrawn']
                    for w in self._hidden_toplevels:w.withdraw()
                    self.root.withdraw();return
            except OSError:
                self.status.set(tr('通知領域に常駐できませんでした。画面を開いたままにします。終了する場合はファイルメニューを使ってください。'))
                return
        self.quit_requested()
    def quit_requested(self):
        if self.closing:return
        recording=any(j['controller'].busy and j['mode'] in ('record','both') for j in self.jobs.values())
        if recording and not messagebox.askyesno(tr('録画中です'),tr('録画を停止して終了しますか？'),parent=self.root):return
        self.closing=True
        for job in list(self.jobs.values()):self.stop_job(job)
        self.status.set(tr('保存して終了しています…'));self.exit_deadline=time.monotonic()+8;self.finish_exit()
    def finish_exit(self):
        if any(j['controller'].busy for j in self.jobs.values()) and time.monotonic()<self.exit_deadline:
            self.root.after(100,self.finish_exit);return
        self.shutdown()
    def shutdown(self):
        if self.closed:return
        self.root._window_memory.capture_all();self.root._window_memory.flush()
        self.closed=True;self.stop_event.set();self.save_state()
        if self.tray:self.tray.stop()
        if self.webpush:self.webpush.stop()
        self.sleep.close()
        for job in self.jobs.values():job['controller'].close()
        if self.account:self.account.close()
        instance_event=getattr(self.root,'_instance_event',None)
        if instance_event and os.name=='nt':
            import ctypes
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(instance_event))
            self.root._instance_event=None
        self.root.destroy()

def main():
    instance_event=None
    if os.name=='nt':
        import ctypes
        try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:pass
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateEventW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_wchar_p]
        kernel.CreateEventW.restype=ctypes.c_void_p
        kernel.SetEvent.argtypes=[ctypes.c_void_p]
        instance_event=kernel.CreateEventW(None,0,0,'Local\\NicoLiveStudio_SingleInstance')
        if instance_event and ctypes.get_last_error()==183:
            kernel.SetEvent(instance_event)
            kernel.CloseHandle(instance_event)
            return
    root=tk.Tk()
    if instance_event:root._instance_event=instance_event
    StudioApp(root);root.mainloop()
