"""Desktop shell: one isolated controller for each playback or recording."""
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
from .scheduler import Scheduler
from .settings import clean_settings
from .settings_ui import SettingsDialog, FirstRunGuide
from .ui import App as LoginApp, app_dir, find_player
from .url import normalize_url
from . import svp
from .layout import fit_window, initialize_zoom, zoom_container, zoom_factor, WindowMemory
from .theme_ui import MenuBar, theme_widgets

MODE={'play':'再生','record':'録画','both':'再生＋録画'}
STATE={'waiting':'待機','queued':'起動待ち','started':'実行済み','ended':'終了','disabled':'無効'}

def clock_text(seconds):
    seconds=max(0,int(seconds or 0));h,rem=divmod(seconds,3600);m,s=divmod(rem,60)
    return f'{h}:{m:02}:{s:02}' if h else f'{m:02}:{s:02}'

def object_rows(value,limit):
    return [dict(item) for item in value[:limit] if isinstance(item,dict)] if isinstance(value,list) else []

def check_resources(settings,mode,active_count=0,player_count=0,local_path=''):
    if active_count>=settings['max_jobs']:return False,'同時処理数の上限です。空きができるまで待機します。'
    if mode!='record':
        player=settings['player_path']
        if not player_count and settings['svp_enabled']:player=settings['svp_player_path'] or player
        if not Path(player).is_file():return False,'設定から mpv の場所を指定してください。'
    if local_path and not Path(local_path).is_file():return False,'録画ファイルが見つかりません。'
    if mode in ('record','both'):
        directory=Path(settings['recording_dir'] or Path.home()/'Videos'/'NicoLive Studio')
        try:
            directory.mkdir(parents=True,exist_ok=True)
            if shutil.disk_usage(directory).free<512*1024*1024:return False,'録画先の空き容量が512MB未満です。'
            if not os.access(directory,os.W_OK):return False,'録画先に書き込めません。'
        except OSError:return False,'録画先を確認してください。'
    return True,''

class StudioApp:
    def __init__(self,root,store=None,controller_factory=Controller,background=True):
        self.root=root;self.store=store or PreferenceStore();self.controller_factory=controller_factory
        self.closed=False;self.closing=False;self.jobs={};self.events=queue.Queue();self.stop_event=threading.Event()
        self.background=background;self.tray=None;self.account=None;self.account_window=None
        self.sleep=SleepInhibitor();self.sound=NotificationSound();self.reservation_window=None
        if background:self.store.migrate(app_dir())
        self.settings=self.store.load_settings();initialize_zoom(root,self.settings['ui_scale']);self.state=self.store.load_state()
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
        self.url=tk.StringVar(root);self.status=tk.StringVar(root,value='番組URLを入力するか、番組を探して再生できます。')
        self.detail=tk.StringVar(root,value='再生・録画する番組がここに並びます。')
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
        menu('ファイル',[('番組URLを入力  Ctrl+L',lambda:self.url_entry.focus_set()),('録画ファイルを開く  Ctrl+O',self.open_local),('録画一覧',self.open_library),('視聴履歴',self.open_history),(None,None),('設定を書き出す',self.export_settings),('設定を読み込む',self.import_settings),('設定のバックアップを復元',self.restore_settings),(None,None),('終了',self.quit_requested)])
        menu('再生',[('一時停止・再開',lambda:self.selected_control('pause')),('再接続  F5',self.reconnect_selected),('コメント投稿',self.open_post),('選択した番組を停止',self.stop_selected),('コメントのテスト',self.preview)])
        menu('録画・予約',[('自動起動（再生）',self.open_schedule_picker),('自動録画',lambda:self.open_schedule_picker('record')),('予約と実行履歴',self.open_reservations),('入力したURLを録画',lambda:self.launch_url('record')),('入力したURLを再生＋録画',lambda:self.launch_url('both'))])
        menu('表示',[('番組を探す',self.open_discovery),('ダーク／ライト切り替え',self.toggle_theme),('録画一覧',self.open_library)])
        menu('設定',[('設定  Ctrl+,',self.open_settings),('ログイン・アカウント',self.show_account)])
        menu('ヘルプ',[('使い方と制限',self.help),('診断情報を書き出す',self.export_diagnostics)])

    def build_body(self):
        content=zoom_container(self.root)
        body=ttk.Frame(content,padding=24);body.pack(fill='both',expand=True)
        header=ttk.Frame(body);header.pack(fill='x')
        ttk.Label(header,text='NicoLive Studio',font=('Yu Gothic UI',24,'bold')).pack(side='left')
        ttk.Button(header,text='ログイン',command=self.show_account).pack(side='right')
        ttk.Label(body,text='視聴も録画も、ひとつの場所で。',style='Muted.TLabel').pack(anchor='w',pady=(2,20))
        row=ttk.Frame(body);row.pack(fill='x')
        self.url_entry=ttk.Entry(row,textvariable=self.url,font=('Yu Gothic UI',12))
        self.url_entry.pack(side='left',fill='x',expand=True,padx=(0,10));self.url_entry.bind('<Return>',lambda e:self.launch_url())
        self.url_entry.bind('<Button-3>',self.url_context);self.url_entry.bind('<Shift-F10>',self.url_context)
        ttk.Button(row,text='▶ 再生',style='Accent.TButton',command=self.launch_url).pack(side='left')
        nav=ttk.Frame(body);nav.pack(fill='x',pady=(12,20))
        for label,command in [('番組を探す',self.open_discovery),('自動起動',self.open_schedule_picker),('自動録画',lambda:self.open_schedule_picker('record')),('予約と履歴',self.open_reservations),('録画一覧',self.open_library)]:
            ttk.Button(nav,text=label,command=command).pack(side='left',padx=(0,8))
        ttk.Label(body,text='再生・録画中',font=('Yu Gothic UI',13,'bold')).pack(anchor='w',pady=(0,8))
        footer=ttk.Frame(body);footer.pack(side='bottom',fill='x')
        frame=ttk.Frame(body);frame.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(frame,columns=('mode','title','state'),show='headings',selectmode='browse',height=7)
        for key,label,width in [('mode','操作',110),('title','番組',470),('state','状態',245)]:
            self.tree.heading(key,text=label);self.tree.column(key,width=width,minwidth=70,stretch=key!='mode')
        scroll=ttk.Scrollbar(frame,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.tree.pack(fill='both',expand=True)
        self.tree.bind('<<TreeviewSelect>>',lambda e:self.refresh_selection())
        ttk.Label(footer,textvariable=self.detail,style='Muted.TLabel',wraplength=940).pack(anchor='w',pady=(12,5))
        timeline=ttk.Frame(footer);timeline.pack(fill='x')
        self.seek=ttk.Scale(timeline,from_=0,to=1,variable=self.position,state='disabled')
        self.seek.pack(side='left',fill='x',expand=True,padx=(0,14))
        self.seek.bind('<ButtonPress-1>',lambda e:setattr(self,'seeking',True))
        self.seek.bind('<ButtonRelease-1>',self.seek_released)
        ttk.Label(timeline,textvariable=self.time_label,width=20).pack(side='right')
        controls=ttk.Frame(footer);controls.pack(fill='x',pady=10)
        for label,command in [('一時停止／再開',lambda:self.selected_control('pause')),('停止',self.stop_selected),('コメント投稿',self.open_post)]:
            ttk.Button(controls,text=label,command=command).pack(side='left',padx=(0,7))
        ttk.Checkbutton(controls,text='コメント',variable=self.comments_enabled,command=self.toggle_comments).pack(side='left',padx=8)
        ttk.Checkbutton(controls,text='消音',variable=self.muted,command=lambda:self.selected_control('mute',self.muted.get())).pack(side='right')
        ttk.Scale(controls,from_=0,to=100,variable=self.volume,command=self.volume_changed,length=110).pack(side='right',padx=8)
        ttk.Label(controls,text='音量').pack(side='right')
        ttk.Label(footer,textvariable=self.status,style='Muted.TLabel',wraplength=950).pack(anchor='w',pady=(6,0))

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
                        if not isinstance(child,ttk.LabelFrame) or child.cget('text')!='ログイン':child.pack_forget()
        top.title('ログイン・アカウント');top.minsize(700,370);top.geometry('760x420')
        top.protocol('WM_DELETE_WINDOW',top.withdraw)
        def attach_logout(widget):
            for child in widget.winfo_children():
                if isinstance(child,ttk.Button) and child.cget('text')=='ログアウト・保存削除':child.configure(command=self.logout)
                attach_logout(child)
        attach_logout(top)
        self.apply_theme()
        if zoom_factor(self.root)>1:
            for canvas in top.winfo_children():
                if isinstance(canvas,tk.Canvas):
                    bar=ttk.Scrollbar(top,orient='horizontal',command=canvas.xview)
                    bar.pack(side='bottom',fill='x',before=canvas);canvas.configure(xscrollcommand=bar.set)
                    def resize(event,c=canvas):
                        for item in c.find_all():
                            if c.type(item)=='window':
                                child=c.nametowidget(c.itemcget(item,'window'))
                                c.itemconfigure(item,width=max(event.width,child.winfo_reqwidth()))
                    canvas.bind('<Configure>',resize)

    def logout(self):
        if any(j['controller'].busy and j['mode']!='play' for j in self.jobs.values()):
            if not messagebox.askyesno('録画中です','ログアウトすると録画も停止します。続けますか？',parent=self.account_window):return
        for job in list(self.jobs.values()):self.stop_job(job)
        self.account.logout()

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
        for label,action in [('切り取り','<<Cut>>'),('コピー','<<Copy>>'),('貼り付け','<<Paste>>')]:
            menu.add_command(label=label,command=lambda a=action:entry.event_generate(a))
        menu.add_separator();menu.add_command(label='すべて選択',command=lambda:entry.selection_range(0,'end'))
        try:menu.tk_popup(event.x_root,event.y_root)
        finally:menu.grab_release()
        return 'break'

    def preflight(self,program,mode,local_path=''):
        active=[j for j in self.jobs.values() if j['controller'].busy]
        return check_resources(self.settings,mode,len(active),sum(j['mode']!='record' for j in active),local_path)

    def launch_url(self,mode='play'):
        try:url=normalize_url(self.url.get())
        except ValueError as exc:self.status.set(str(exc));return False
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
                    raise ValueError('この番組は同じ動作で実行中、または再接続待ちです。再生と録画は各1件までです。')
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
            self.status.set(str(exc)[:300])
            if 'controller' in locals():controller.close()
            if schedule_id:self.scheduler.ack(schedule_id,False,str(exc),program)
            return False
        ident=replace_ident or uuid.uuid4().hex
        retries=self.jobs[ident]['retries']+1 if ident in self.jobs else 0
        title=str(program.get('title') or (Path(local_path).name if local_path else url) or 'コメントのテスト')
        self.jobs[ident]={'controller':controller,'program':program,'url':url,'title':title,'mode':mode,
            'local_path':local_path,'position':start_time,'duration':0,'seekable':False,'paused':False,
            'settings':settings,'state':'接続中','post_ready':False,'post':None,'manual_stop':False,
            'retries':retries,'error':False,'completed':False,'preview':preview,'schedule_id':schedule_id,
            'run_key':run_key,'restart_pending':False,
            'auth_snapshot':credentials,'ready':False}
        if self.tree.exists(ident):self.tree.item(ident,values=(MODE[mode],title,'接続中'))
        else:self.tree.insert('',0,iid=ident,values=(MODE[mode],title,'接続中'))
        self.tree.selection_set(ident)
        self.status.set('接続を開始しました。');self.update_sleep()
        self.execution(self.jobs[ident],'開始');self.save_state();return True

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
        except (ValueError,OSError):self.status.set('再生画面への操作を送れませんでした。')

    def volume_changed(self,value):self.selected_control('volume',float(value))
    def seek_released(self,event=None):
        self.seeking=False;job=self.selected()
        if job and job['seekable']:self.selected_control('seek',self.position.get())

    def refresh_selection(self):
        job=self.selected()
        if not job:
            self.detail.set('再生・録画する番組がここに並びます。')
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
        job['manual_stop']=True;job['restart_pending']=restarting;job['post_ready']=False;job['state']='停止中';job['controller'].stop()
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
            self.status.set('録画中は停止してから新しく開始してください。録画済みファイルは保持されます。');return
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
            self.status.set('コメント投稿は、ログインして再生中の生放送で利用できます。');return
        if job['post'] and not job['post'].closed:job['post'].window.lift();return
        job['post']=PostWindow(self.root,job['controller'],job['controller'].current,job['url'])

    def handle_event(self,ident,event):
        job=self.jobs.get(ident)
        if not job:return
        kind=event.get('type');text=event.get('text','')
        if kind in ('status','error'):
            job['state']=text[:160]
            if kind=='error':job['error']=True;self.execution(job,'エラー',text)
        elif kind=='metadata':
            job['title']=str(event.get('title') or job['title']);job['program'].update({k:event[k] for k in ('title','author','provider','id') if k in event})
            job['seekable']=bool(event.get('seekable',False))
        elif kind=='position':
            for key in ('position','duration','paused','seekable'):
                if key in event:job[key]=event[key]
            self.remember(job)
        elif kind=='recording':job['recording_path']=event.get('path','');job['state']='録画中' if event.get('state')=='recording' else str(event.get('state','録画'))
        elif kind=='completed':
            job['completed']=event.get('ok') is True;job['recording_path']=event.get('path',job.get('recording_path',''))
            self.execution(job,'完了' if event.get('ok') else '中断');self.remember(job)
        elif kind=='player_closed':
            job['manual_stop']=True;job['restart_pending']=False;job['post_ready']=False;job['state']='終了'
        elif kind=='comment_status':job['comment_status']=text
        elif kind=='post_ready':job['post_ready']=bool(event.get('enabled')) and not job['manual_stop']
        elif kind=='post_result':
            if job['post']:job['post'].receive(event)
        elif kind=='session_invalid' and job.get('auth_snapshot')==self.credentials():
            self.account.browser_session=[]
            try:self.account.session_store.delete()
            except OSError:pass
            self.account.auth_status.set('ログイン期限が切れました。ブラウザでログインし直してください。')
        elif kind=='otp_required':self.ask_otp(job,event.get('request_id',''))
        elif kind=='authenticated':
            credentials=job.get('auth_snapshot') or {}
            if self.account.remember.get() and credentials==self.credentials() and credentials.get('password'):
                try:self.account.credential_store.save(credentials['email'],credentials['password'])
                except (OSError,ValueError):pass
        elif kind=='job_ready':
            job['ready']=True
            if job['schedule_id']:
                self.scheduler.ack(job['schedule_id'],True,'実行中',job['program']);self.save_state()
                self.sound.notify(job['mode'],self.settings)
        elif kind=='stopped':
            job['post_ready']=False
            if job['post']:job['post'].stop()
            if job['state'] in ('接続中','停止中'):job['state']='停止'
            if job['schedule_id'] and not job['ready']:
                self.scheduler.ack(job['schedule_id'],job['manual_stop'],'手動で終了しました。' if job['manual_stop'] else '接続に失敗しました。再試行を待っています。',job['program'])
            if job['error'] and not job['manual_stop'] and not job['completed'] and not job['local_path'] and (not job['schedule_id'] or job['ready']) and (job['mode']=='play' or not job['seekable']) and job['retries']<3 and self.settings['reconnect']:
                job['restart_pending']=True;job['state']='再接続待ち';self.root.after(3000*(job['retries']+1),lambda:self.restart(ident))
            self.remember(job);self.save_state();self.update_sleep()
            job['controller'].close()
            job['auth_snapshot']=None
            if not job.get('restart_pending'):self.remove_job(ident)
        if self.tree.exists(ident):self.tree.item(ident,values=(MODE[job['mode']],job['title'],job['state']))
        self.refresh_selection()

    def ask_otp(self,job,request_id):
        code=simpledialog.askstring('二段階認証','メールに届いた6桁の認証コード',parent=self.root)
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
        except (OSError,ValueError):self.status.set('状態を保存できません。保存先の空き容量を確認してください。')

    def poll(self):
        if self.closed:return
        for ident,job in list(self.jobs.items()):
            for event in job['controller'].poll_events():self.handle_event(ident,event)
        for _ in range(50):
            try:kind,data=self.events.get_nowait()
            except queue.Empty:break
            if kind=='launch':
                reservation,program,mode=data
                if not self.closing and any(r['id']==reservation and r['state']=='queued' for r in self.scheduler.snapshot()):
                    self.launch(program,mode,schedule_id=reservation)
            elif kind=='schedule_done':self.save_state();self.refresh_reservations()
        if time.monotonic()-self.last_saved>=30:
            self.save_state();self.last_saved=time.monotonic()
        self.root.after(100,self.poll)

    def schedule_tick(self):
        if self.closed or self.closing:return
        if self.scheduler.snapshot() and not getattr(self,'scheduling',False):
            self.scheduling=True;cookies=self.cookies()
            settings=copy.deepcopy(self.settings);active=[j for j in self.jobs.values() if j['controller'].busy]
            count=len(active);players=sum(j['mode']!='record' for j in active)
            def run():
                catalog=None
                try:
                    from .catalog import Catalog
                    catalog=Catalog(cookies)
                    self.scheduler.tick(catalog,lambda ident,p,mode:self.events.put(('launch',(ident,p,mode))),
                                        preflight=lambda p,mode:check_resources(settings,mode,count,players))
                finally:
                    if catalog:catalog.close()
                    self.scheduling=False;self.events.put(('schedule_done',None))
            threading.Thread(target=run,daemon=True,name='schedule-check').start()
        self.root.after(30000,self.schedule_tick)

    def open_discovery(self):
        from .discovery_ui import DiscoveryWindow
        DiscoveryWindow(self.root,self.cookies(),self.launch,ffmpeg_path=self.settings['ffmpeg_path'])
    def open_schedule_picker(self,mode='play'):
        from .discovery_ui import SchedulePicker
        SchedulePicker(self.root,self.cookies(),self.add_reservation,ffmpeg_path=self.settings['ffmpeg_path'],mode=mode,on_reservations=self.open_reservations)
    def add_reservation(self,program,mode):
        try:self.scheduler.add(program,mode);self.save_state();self.status.set('予約を追加しました。ソフトを起動している間に自動実行します。');self.open_reservations()
        except ValueError as exc:self.status.set(str(exc))

    def open_reservations(self):
        if self.reservation_window and self.reservation_window.winfo_exists():self.reservation_window.lift();self.refresh_reservations();return
        top=tk.Toplevel(self.root);self.reservation_window=top;top.title('予約と実行履歴');top.geometry('940x500')
        content=zoom_container(top,always=True)
        book=ttk.Notebook(content);book.pack(fill='both',expand=True,padx=16,pady=16)
        tabs=[]
        for label in ('予約','実行履歴'):
            frame=ttk.Frame(book);book.add(frame,text=label);tabs.append(frame)
        self.reservations_tree=ttk.Treeview(tabs[0],columns=('start','mode','title','state'),show='headings')
        for k,title,width in [('start','開始予定',140),('mode','動作',100),('title','番組・ユーザー',350),('state','状態',250)]:self.reservations_tree.heading(k,text=title);self.reservations_tree.column(k,width=width)
        self.reservations_tree.pack(fill='both',expand=True)
        row=ttk.Frame(tabs[0]);row.pack(side='bottom',fill='x',pady=8,before=self.reservations_tree)
        ttk.Button(row,text='自動起動を追加',command=self.open_schedule_picker).pack(side='left')
        ttk.Button(row,text='自動録画を追加',command=lambda:self.open_schedule_picker('record')).pack(side='left',padx=8)
        def remove():
            for ident in self.reservations_tree.selection():self.scheduler.remove(ident)
            self.save_state();self.refresh_reservations()
        ttk.Button(row,text='選択した予約を削除',command=remove).pack(side='left',padx=8)
        self.execution_tree=ttk.Treeview(tabs[1],columns=('time','title','status'),show='headings')
        for k,title,width in [('time','実行日時',150),('title','番組',420),('status','結果',250)]:self.execution_tree.heading(k,text=title);self.execution_tree.column(k,width=width)
        self.execution_tree.pack(fill='both',expand=True);self.refresh_reservations()
        fit_window(top,940,500,flex=book)
    def refresh_reservations(self):
        if not self.reservation_window or not self.reservation_window.winfo_exists():return
        self.reservations_tree.delete(*self.reservations_tree.get_children())
        for r in self.scheduler.snapshot():
            start=datetime.fromtimestamp(r['start']).strftime('%m/%d %H:%M') if r['start'] else '随時'
            self.reservations_tree.insert('','end',iid=r['id'],values=(start,MODE[r['mode']],r['title'],r.get('message') or STATE.get(r['state'],r['state'])))
        self.execution_tree.delete(*self.execution_tree.get_children())
        for e in reversed(self.executions[-200:]):self.execution_tree.insert('','end',values=(datetime.fromtimestamp(e['time']).strftime('%m/%d %H:%M'),e['title'],e['status']+(' · '+e['reason'] if e.get('reason') else '')))

    def open_local(self):
        path=filedialog.askopenfilename(parent=self.root,title='録画を開く（同名のコメントを自動読込）',filetypes=[('動画','*.ts *.mp4 *.mkv *.webm'),('すべて','*.*')])
        if path:
            old=next((h for h in self.history if h.get('local_path')==path),{})
            start=old.get('position',0) if old and messagebox.askyesno('続きから再生',clock_text(old.get('position',0))+' から再生しますか？',parent=self.root) else 0
            self.play_local(path,start)
    def play_local(self,path,start_time=0):self.launch({'title':Path(path).name},local_path=path,start_time=start_time)
    def open_library(self):
        from .library_ui import LibraryWindow
        LibraryWindow(self.root,self.settings['recording_dir'] or str(Path.home()/'Videos'/'NicoLive Studio'),self.history,self.play_local,self.mark_watched,ffmpeg_path=self.settings['ffmpeg_path'])
    def open_history(self):
        top=tk.Toplevel(self.root);top.title('視聴履歴');top.geometry('860x460')
        content=zoom_container(top,always=True)
        tree=ttk.Treeview(content,columns=('title','position','date'),show='headings',selectmode='browse')
        for key,label,width in [('title','番組',500),('position','再開位置',100),('date','最終視聴',160)]:tree.heading(key,text=label);tree.column(key,width=width)
        tree.pack(fill='both',expand=True,padx=16,pady=16)
        rows=copy.deepcopy(self.history)
        for i,h in enumerate(rows):
            date=datetime.fromtimestamp(h['seen_at']).strftime('%m/%d %H:%M') if h.get('seen_at') else ''
            tree.insert('','end',iid=str(i),values=(h.get('title',''),clock_text(h.get('position',0)) if h.get('seekable') or h.get('local_path') else '生放送',date))
        def play(resume):
            if not tree.selection():return
            h=rows[int(tree.selection()[0])];start=h.get('position',0) if resume and (h.get('seekable') or h.get('local_path')) else 0
            self.launch({'url':h.get('url',''),'title':h.get('title','')},local_path=h.get('local_path',''),start_time=start)
        buttons=ttk.Frame(content,padding=12);buttons.pack(side='bottom',fill='x',before=tree)
        ttk.Button(buttons,text='続きから再生',command=lambda:play(True)).pack(side='right',padx=8)
        ttk.Button(buttons,text='最初から／生放送を再生',command=lambda:play(False)).pack(side='right')
        fit_window(top,860,460,flex=tree)
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
        self.apply_theme();self.update_sleep();self.status.set('設定を保存しました。画面の拡大率はソフトの再起動後に適用されます。' if values['ui_scale']!=round(zoom_factor(self.root)*100) else '設定を保存しました。画質と再生ソフトの変更は次の再生から適用されます。')
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
        if path:self.file_action(lambda:self.store.export_settings(path),'設定を書き出しました（ログイン情報は含みません）。')
    def import_settings(self):
        path=filedialog.askopenfilename(parent=self.root,filetypes=[('設定','*.json')])
        if path:self.file_action(lambda:self.apply_settings(self.store.import_settings(path)),'設定を読み込みました。')
    def restore_settings(self):
        def restore():self.store.restore_backup();self.apply_settings(self.store.load_settings())
        self.file_action(restore,'設定のバックアップを復元しました。')
    def export_diagnostics(self):
        path=filedialog.asksaveasfilename(parent=self.root,defaultextension='.json',initialfile='NicoLive-diagnostics.json')
        if path:self.file_action(lambda:self.store.export_diagnostics(path,{'job_count':len(self.jobs),'running_count':sum(j['controller'].busy for j in self.jobs.values())}),'診断情報を書き出しました。')
    def file_action(self,action,success):
        try:action();self.status.set(success)
        except (OSError,ValueError) as exc:messagebox.showerror('操作できません',str(exc),parent=self.root)
    def preview(self):self.launch({},preview=True)
    def help(self):messagebox.showinfo('使い方','番組URLを入力して再生します。\n「自動起動」で再生、「自動録画」で録画を予約できます。ソフトの起動中に実行します。\n録画は映像とコメントを別ファイルで保存し、一緒に再生します。\nコメントFPSは設定画面で数値を指定できます。\n\nプレミアム限定番組は視聴権限のあるアカウントが必要です。\n公式ギフトの演出については同梱の機能確認表をご覧ください。',parent=self.root)
    def update_sleep(self):
        active=bool(self.settings['inhibit_sleep'] and any(j['controller'].busy for j in self.jobs.values()))
        if active!=self.sleep.active:
            try:self.sleep.set_active(active)
            except OSError:self.status.set('自動スリープの抑制を設定できませんでした。')
    def show(self):self.root.deiconify();self.root.lift()
    def close_requested(self):
        if self.settings['tray']:
            try:
                if not self.tray:self.tray=TrayIcon(self.show,self.quit_requested,self.root)
                if self.tray.start():self.root.withdraw();return
            except OSError:
                self.status.set('通知領域に常駐できませんでした。画面を開いたままにします。終了する場合はファイルメニューを使ってください。')
                return
        self.quit_requested()
    def quit_requested(self):
        if self.closing:return
        recording=any(j['controller'].busy and j['mode'] in ('record','both') for j in self.jobs.values())
        if recording and not messagebox.askyesno('録画中です','録画を停止して終了しますか？',parent=self.root):return
        self.closing=True
        for job in list(self.jobs.values()):self.stop_job(job)
        self.status.set('保存して終了しています…');self.exit_deadline=time.monotonic()+8;self.finish_exit()
    def finish_exit(self):
        if any(j['controller'].busy for j in self.jobs.values()) and time.monotonic()<self.exit_deadline:
            self.root.after(100,self.finish_exit);return
        self.shutdown()
    def shutdown(self):
        if self.closed:return
        self.root._window_memory.capture_all();self.root._window_memory.flush()
        self.closed=True;self.stop_event.set();self.save_state()
        if self.tray:self.tray.stop()
        self.sleep.close()
        for job in self.jobs.values():job['controller'].close()
        if self.account:self.account.close()
        self.root.destroy()

def main():
    if os.name=='nt':
        import ctypes
        try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:pass
    root=tk.Tk();StudioApp(root);root.mainloop()
