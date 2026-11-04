from .i18n import set_language, tr
import os
from pathlib import Path
import shutil
import sys
import tkinter as tk
from tkinter import ttk, font as tkfont, filedialog
import webbrowser
import queue
import threading
from .controller import Controller
from .settings import clean_settings, load_settings, save_settings
from .url import normalize_url
from .credentials import CredentialStore
from . import svp
from .sessions import SessionStore
from .browser_login import BrowserLogin, BrowserLoginError
from .auth import Cancelled
from .post_window import PostWindow
from .layout import AutoScrollbar


def app_dir():
    return Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]


def find_player():
    candidates=[app_dir()/'player'/'mpv.exe',Path(__file__).resolve().parents[3]/'player'/'mpv.exe']
    for p in candidates:
        if p.is_file():return str(p)
    return shutil.which('mpv') or ''


class App:
    def __init__(self, root, settings_path=None):
        self.root=root;self.controller=Controller();self.otp_window=None;self.closed=False
        self.settings_path=Path(settings_path) if settings_path else app_dir()/'settings.json'
        if isinstance(root,tk.Tk):set_language(load_settings(self.settings_path)['language'])
        self.credential_store=CredentialStore(self.settings_path.with_name('credentials.dat'))
        self.session_store=SessionStore(self.settings_path.with_name('session.dat'))
        self.browser_session=[];self.browser_stop=threading.Event();self.browser_results=queue.Queue()
        self.browser_generation=0;self.browser_busy=False
        self.post_window=None;self.post_target=None
        self.pending_credentials=None;self.pending_request=None;self.pending_login_email=''
        self.authenticated=False;self.auth_email=''
        root.title(tr('ニコ生ビューア'));root.geometry('790x735');root.minsize(700,690)
        root.configure(bg='#f3f5f8')
        style=ttk.Style(root);style.theme_use('clam')
        style.configure('.',font=('Yu Gothic UI',10),background='#f3f5f8')
        style.configure('TLabel',foreground='#25334b')
        style.configure('TButton',padding=(12,7))
        style.configure('Accent.TButton',background='#315cdd',foreground='white',padding=(18,9))
        style.map('Accent.TButton',background=[('active','#254ab7'),('disabled','#a9b4d0')])
        style.configure('TLabelframe',padding=12)
        style.configure('TLabelframe.Label',font=('Yu Gothic UI',10,'bold'))
        self.fonts=sorted({f for f in tkfont.families(root) if not f.startswith('@')})
        self.settings=clean_settings(load_settings(self.settings_path),self.fonts)
        if not Path(self.settings['player_path']).is_file():self.settings['player_path']=find_player()
        if not Path(self.settings['ffmpeg_path']).is_file():
            bundled=app_dir()/'player'/'ffmpeg.exe'
            self.settings['ffmpeg_path']=str(bundled) if bundled.is_file() else (shutil.which('ffmpeg') or '')
        self.url=tk.StringVar();self.email=tk.StringVar();self.password=tk.StringVar();self.login=tk.BooleanVar(value=False)
        self.remember=tk.BooleanVar(value=False)
        self.font=tk.StringVar(value=self.settings['font']);self.size=tk.IntVar(value=self.settings['size'])
        self.delay=tk.DoubleVar(value=self.settings['delay']);self.enabled=tk.BooleanVar(value=self.settings['enabled'])
        self.player=tk.StringVar(value=self.settings['player_path']);self.ffmpeg=tk.StringVar(value=self.settings['ffmpeg_path'])
        self.svp_enabled=tk.BooleanVar(value=self.settings['svp_enabled'])
        self.svp_player=tk.StringVar(value=self.settings['svp_player_path'] or svp.find_player())
        self.status=tk.StringVar(value=tr('番組URLを入力して「再生」を押してください。'))
        self.comment_status=tk.StringVar(value=tr('コメントは再生画面の上に流れます。'))
        self.auth_status=tk.StringVar(value=tr('未ログイン / 保存は任意です'))
        try:
            saved=self.credential_store.load()
            if saved:
                self.email.set(saved['email']);self.password.set(saved['password'])
                self.remember.set(True);self.login.set(True)
                self.auth_status.set(tr('保存済みのログイン情報を読み込みました。再生時にログインします。'))
        except (OSError,ValueError):
            self.auth_status.set(tr('保存情報を読み込めません。再入力するか「ログアウト・保存削除」を押してください。'))
        try:
            self.browser_session=self.session_store.load() or []
            if self.browser_session:
                # The restored session is itself saved (DPAPI), so show the save option as on.
                self.login.set(True);self.remember.set(True)
                self.auth_status.set(tr('前回のログイン状態を復元しました。再生時に有効性を確認します。'))
        except (OSError,ValueError):
            self.auth_status.set(tr('保存セッションを読み込めません。ブラウザでログインし直してください。'))
        canvas=tk.Canvas(root,bg='#f3f5f8',highlightthickness=0)
        root.columnconfigure(0,weight=1);root.rowconfigure(0,weight=1)
        scroll=AutoScrollbar(root,orient='vertical',command=canvas.yview)
        scroll.grid(row=0,column=1,sticky='ns');canvas.grid(row=0,column=0,sticky='nsew')
        canvas.configure(yscrollcommand=scroll.set)
        outer=ttk.Frame(canvas,padding=24)
        window=canvas.create_window((0,0),window=outer,anchor='nw')
        outer.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(window,width=e.width))
        canvas.bind_all('<MouseWheel>',lambda e:canvas.yview_scroll(-int(e.delta/120),'units')
                        if e.widget.winfo_class() not in ('TCombobox','TSpinbox') else None)
        ttk.Label(outer,text=tr('ニコ生ビューア'),font=('Yu Gothic UI',23,'bold')).pack(anchor='w')
        ttk.Label(outer,text=tr('URLから再生。コメントも、自分の読みやすい文字で。'),foreground='#64728a').pack(anchor='w',pady=(2,18))
        ttk.Label(outer,text=tr('番組URL')).pack(anchor='w')
        self.url_entry=ttk.Entry(outer,textvariable=self.url,font=('Yu Gothic UI',12))
        self.url_entry.pack(fill='x',pady=(5,10));self.url_entry.bind('<Return>',lambda e:self.start())
        self.url_menu=tk.Menu(root,tearoff=False)
        for label,action in [(tr('切り取り'),'cut'),(tr('コピー'),'copy'),(tr('貼り付け'),'paste'),(tr('削除'),'delete')]:
            self.url_menu.add_command(label=label,command=lambda a=action:self.url_edit(a))
        self.url_menu.add_separator()
        self.url_menu.add_command(label=tr('すべて選択'),command=lambda:self.url_edit('select_all'))
        self.url_entry.bind('<Button-3>',self.show_url_menu)
        self.url_entry.bind('<Shift-F10>',self.show_url_menu)
        self.url_entry.bind('<Menu>',self.show_url_menu)

        row=ttk.Frame(outer);row.pack(fill='x')
        self.play_button=ttk.Button(row,text=tr('▶  再生'),style='Accent.TButton',command=self.start);self.play_button.pack(side='left')
        self.stop_button=ttk.Button(row,text=tr('■  停止'),command=self.stop,state='disabled');self.stop_button.pack(side='left',padx=8)
        ttk.Button(row,text=tr('コメント投稿'),command=self.open_post).pack(side='left')
        self.preview_button=ttk.Button(row,text=tr('コメントを試す'),command=lambda:self.start(preview=True));self.preview_button.pack(side='right')
        auth=ttk.LabelFrame(outer,text=tr('ログイン'),padding=12);auth.pack(fill='x',pady=(18,12))
        ttk.Checkbutton(auth,text=tr('ニコニコアカウントで視聴する'),variable=self.login).grid(row=0,column=0,columnspan=2,sticky='w')
        ttk.Button(auth,text=tr('ログアウト・保存削除'),command=self.logout).grid(row=0,column=2,sticky='e')
        ttk.Label(auth,text=tr('メール / 電話番号')).grid(row=1,column=0,sticky='w',pady=(8,3))
        ttk.Label(auth,text=tr('パスワード')).grid(row=1,column=1,sticky='w',pady=(8,3),padx=(12,0))
        ttk.Entry(auth,textvariable=self.email).grid(row=2,column=0,sticky='ew')
        ttk.Entry(auth,textvariable=self.password,show='●').grid(row=2,column=1,columnspan=2,sticky='ew',padx=(12,0))
        ttk.Checkbutton(auth,text=tr('ログイン情報を保存する（Windowsで暗号化）'),variable=self.remember,
                        command=self.toggle_remember).grid(row=3,column=0,columnspan=3,sticky='w',pady=(8,0))
        ttk.Label(auth,textvariable=self.auth_status,foreground='#64728a',wraplength=680).grid(row=4,column=0,columnspan=3,sticky='w',pady=(7,0))
        self.browser_button=ttk.Button(auth,text=tr('ブラウザでログイン（二段階認証・状態を保存）'),command=self.browser_login)
        self.browser_button.grid(row=5,column=0,columnspan=2,sticky='w',pady=(8,0))
        ttk.Button(auth,text=tr('ログインを中止'),command=self.cancel_browser_login).grid(row=5,column=2,sticky='e',pady=(8,0))
        auth.columnconfigure(0,weight=1);auth.columnconfigure(1,weight=1)
        comments=ttk.LabelFrame(outer,text=tr('コメント表示'),padding=12);comments.pack(fill='x')
        ttk.Checkbutton(comments,text=tr('映像にコメントを重ねる'),variable=self.enabled,command=self.apply).grid(row=0,column=0,columnspan=3,sticky='w')
        ttk.Label(comments,text=tr('フォント')).grid(row=1,column=0,sticky='w',pady=(9,3))
        ttk.Label(comments,text=tr('サイズ（pt）')).grid(row=1,column=1,sticky='w',padx=(12,0))
        ttk.Label(comments,text=tr('遅延（秒）')).grid(row=1,column=2,sticky='w',padx=(12,0))
        combo=ttk.Combobox(comments,textvariable=self.font,values=self.fonts,state='readonly',width=30)
        combo.grid(row=2,column=0,sticky='ew');combo.bind('<<ComboboxSelected>>',lambda e:self.apply())
        ttk.Spinbox(comments,from_=12,to=96,textvariable=self.size,width=7,command=self.apply).grid(row=2,column=1,sticky='w',padx=(12,0))
        ttk.Spinbox(comments,from_=0,to=30,increment=.5,textvariable=self.delay,width=7,command=self.apply).grid(row=2,column=2,sticky='w',padx=(12,0))
        ttk.Button(comments,text=tr('反映'),command=self.apply).grid(row=2,column=3,padx=(12,0))
        comments.columnconfigure(0,weight=1)
        ttk.Label(comments,text=tr('120fps目標・画面同期 / 変更は新しいコメントから反映 / 初期値: 28 pt・遅延 0 秒'),foreground='#64728a').grid(row=3,column=0,columnspan=4,sticky='w',pady=(8,0))
        smooth=ttk.LabelFrame(outer,text=tr('動画の60fps補間（SVP）'),padding=10);smooth.pack(fill='x',pady=(12,0))
        ttk.Checkbutton(smooth,text=tr('SVPを使う（変更は次回の再生から）'),variable=self.svp_enabled,
                        command=self.apply).pack(anchor='w')
        ttk.Label(smooth,text=tr('SVP Managerを起動し、使用するプロファイルの目標フレームレートを60fpsに設定してください。\nコメントは120fps目標のままです。再生画面のF9キーで補間フィルター・動画fpsを確認できます。'),
                  wraplength=680,foreground='#64728a').pack(anchor='w',pady=(5,0))
        paths=ttk.LabelFrame(outer,text=tr('再生ソフトの場所'),padding=10);paths.pack(fill='x',pady=(12,10))
        for r,(label,var) in enumerate([('mpv',self.player),(tr('FFmpeg（必要時）'),self.ffmpeg),(tr('SVP付属mpv'),self.svp_player)]):
            ttk.Label(paths,text=label).grid(row=r,column=0,sticky='w',pady=3)
            ttk.Entry(paths,textvariable=var).grid(row=r,column=1,sticky='ew',padx=8)
            ttk.Button(paths,text=tr('参照'),command=lambda v=var:self.browse(v)).grid(row=r,column=2)
        paths.columnconfigure(1,weight=1)
        links=ttk.Frame(paths);links.grid(row=3,column=0,columnspan=3,sticky='w')
        ttk.Button(links,text=tr('mpvの入手先'),command=lambda:webbrowser.open('https://mpv.io/installation/')).pack(side='left')
        ttk.Button(links,text=tr('FFmpegの入手先'),command=lambda:webbrowser.open('https://ffmpeg.org/download.html')).pack(side='left',padx=8)
        ttk.Label(outer,textvariable=self.status,wraplength=720,font=('Yu Gothic UI',10,'bold')).pack(anchor='w',pady=(4,5))
        ttk.Label(outer,textvariable=self.comment_status,wraplength=720,foreground='#64728a').pack(anchor='w')
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(100,self.poll)
        self.url_entry.focus_set()

    def show_url_menu(self,event):
        entry=self.url_entry;entry.focus_set()
        selected=entry.selection_present()
        editable=str(entry.cget('state'))=='normal'
        for index in (0,3):self.url_menu.entryconfigure(index,state='normal' if selected and editable else 'disabled')
        self.url_menu.entryconfigure(1,state='normal' if selected else 'disabled')
        try:has_text=bool(entry.clipboard_get())
        except tk.TclError:has_text=False
        self.url_menu.entryconfigure(2,state='normal' if editable and has_text else 'disabled')
        x,y=(event.x_root,event.y_root) if event.num==3 else (entry.winfo_rootx()+10,entry.winfo_rooty()+entry.winfo_height())
        try:self.url_menu.tk_popup(x,y)
        finally:self.url_menu.grab_release()
        return 'break'

    def url_edit(self,action):
        entry=self.url_entry
        entry.focus_set()
        try:
            if action=='select_all':
                entry.selection_range(0,'end');entry.icursor('end');return
            if action in ('copy','cut') and entry.selection_present():
                value=entry.get()[entry.index('sel.first'):entry.index('sel.last')]
                entry.clipboard_clear();entry.clipboard_append(value)
            if action=='copy' or str(entry.cget('state'))!='normal':return
            value=entry.clipboard_get() if action=='paste' else None
            if action in ('cut','delete','paste') and entry.selection_present():
                start=entry.index('sel.first');entry.delete(start,entry.index('sel.last'));entry.icursor(start)
            if action=='paste':entry.insert('insert',value)
        except tk.TclError:pass

    def browse(self,var):
        path=filedialog.askopenfilename(parent=self.root,filetypes=[(tr('実行ファイル'),'*.exe')])
        if path:var.set(path);self.apply()

    def values(self):
        return clean_settings({**self.settings,'font':self.font.get(),'size':self.size.get(),'delay':self.delay.get(),
            'enabled':self.enabled.get(),'player_path':self.player.get(),'ffmpeg_path':self.ffmpeg.get(),
            'svp_enabled':self.svp_enabled.get(),'svp_player_path':self.svp_player.get()},self.fonts)

    def apply(self):
        try:
            self.settings=self.values();save_settings(self.settings_path,self.settings)
            self.controller.configure(self.settings)
        except (ValueError,tk.TclError):self.status.set(tr('文字サイズと遅延には数値を入力してください。'))
        except OSError:self.status.set(tr('設定を保存または反映できませんでした。フォルダの書き込み権限を確認してください。'))

    def start(self,preview=False):
        if self.browser_busy:
            self.status.set(tr('ブラウザでログインを完了するか、中止してください。'));return
        if self.controller.busy:
            self.status.set(tr('再生・認証処理中です。先に停止してください。'))
            return
        try:
            if not preview:normalize_url(self.url.get())
            if self.svp_enabled.get():svp.player_path(self.svp_player.get())
            elif not Path(self.player.get()).is_file():
                self.status.set(tr('mpv.exeが見つかりません。「参照」から指定してください。'));return
            self.apply()
            credentials={'email':self.email.get().strip(),'password':self.password.get()} if self.login.get() and not preview else None
            if self.login.get() and self.browser_session and not preview:
                credentials={'session':[c.copy() for c in self.browser_session]}
            if credentials is not None and 'session' not in credentials and not credentials['password']:
                if not (self.authenticated and self.auth_email==credentials['email']):
                    saved=self.credential_store.load() if self.remember.get() else None
                    if saved and saved['email']==credentials['email']:credentials=saved
                    else:raise ValueError(tr('このアカウントのパスワードを入力してください。'))
            pending=credentials.copy() if credentials and credentials.get('password') and self.remember.get() else None
            ident=self.controller.start(self.url.get(),self.player.get(),credentials,self.ffmpeg.get(),preview)
            self.pending_request=ident
            self.pending_credentials=pending
            self.pending_login_email=credentials.get('email','') if credentials else ''
            self.password.set('');self.status.set(tr('起動しています…'));self.set_busy(True)
        except (ValueError,RuntimeError) as exc:self.pending_credentials=None;self.status.set(tr(str(exc)))
        except OSError:self.pending_credentials=None;self.status.set(tr('再生処理を起動できませんでした。'))

    def toggle_remember(self):
        if not self.remember.get():
            self.pending_credentials=None
            try:
                self.credential_store.delete()
                # Keep the in-memory browser session for this run; only stop persisting it.
                if self.browser_session:self.session_store.delete()
                self.auth_status.set(tr('保存したログイン情報を削除しました。')+(tr('この起動中はログインしたままです。次回起動時はログインし直してください。') if self.browser_session else ''))
            except OSError:
                self.remember.set(True)
                self.auth_status.set(tr('保存情報を削除できませんでした。フォルダの書き込み権限を確認してください。'))
        elif self.browser_session:
            try:
                self.session_store.save(self.browser_session)
                self.auth_status.set(tr('ログイン状態を暗号化して保存しました。次回起動時も自動でログインします。'))
            except (OSError,ValueError):
                self.remember.set(False)
                self.auth_status.set(tr('ログイン状態を保存できませんでした。この起動中のみ利用できます。'))
        else:self.auth_status.set(tr('次回のログイン成功時に、Windowsで暗号化して保存します。'))

    def authenticated_event(self,event):
        if event['request_id']!=self.pending_request:return
        self.authenticated=True;self.auth_email=self.pending_login_email
        self.auth_status.set(tr(event.get('text',tr('ログインしました。'))))
        if self.pending_credentials and self.remember.get() and event['request_id']==self.pending_request:
            try:
                self.credential_store.save(self.pending_credentials['email'],self.pending_credentials['password'])
                self.auth_status.set(tr('ログインしました。ログイン情報を暗号化して保存しました。'))
            except (OSError,ValueError):
                self.auth_status.set(tr('ログイン済みですが、暗号化して保存できませんでした。'))
        self.pending_credentials=None

    def set_busy(self,busy):
        self.play_button.configure(state='disabled' if busy else 'normal')
        self.preview_button.configure(state='disabled' if busy else 'normal')
        self.stop_button.configure(state='normal' if busy else 'disabled')
        if not busy:
            self.post_target=None
            if self.post_window:self.post_window.stop()

    def open_post(self):
        if not self.post_target or not self.controller.busy:
            self.status.set(tr('ログインして放送を再生すると、コメント投稿の小窓を開けます。'));return
        ident,url=self.post_target
        if self.post_window and not self.post_window.closed:
            if self.post_window.request_id==ident:
                self.post_window.window.lift();self.post_window.text.focus_set();return
            self.post_window.close()
        self.post_window=PostWindow(self.root,self.controller,ident,url)

    def stop(self):
        self.post_target=None
        if self.post_window:self.post_window.stop()
        self.status.set(tr('停止しています…'));self.controller.stop()

    def logout(self):
        self.cancel_browser_login();self.browser_session=[]
        session_deleted=True
        try:self.session_store.delete()
        except OSError:session_deleted=False
        self.controller.logout();self.password.set('');self.email.set('');self.login.set(False)
        self.authenticated=False;self.auth_email='';self.pending_credentials=None;self.remember.set(False)
        self.toggle_remember();self.status.set(tr('ログアウトして停止しました。'));self.set_busy(False);self.close_otp()
        if not session_deleted:self.auth_status.set(tr('保存セッションを削除できませんでした。フォルダの書き込み権限を確認してください。'))

    def browser_login(self):
        if self.controller.busy:
            self.status.set(tr('先に再生を停止してください。'));return
        if self.browser_busy:return
        self.browser_generation+=1;generation=self.browser_generation
        self.browser_stop=threading.Event();stop=self.browser_stop
        self.browser_busy=True;self.browser_button.configure(state='disabled')
        self.auth_status.set(tr('開いた専用Edgeでログイン・二段階認証を完了後、Edgeを×で閉じてください。閉じると状態を保存します。'))
        def work():
            try:result=('success',BrowserLogin(stop).run())
            except Cancelled:result=('cancel',tr('ブラウザログインを中止しました。'))
            except BrowserLoginError as e:result=('error',str(e))
            except Exception:result=('error',tr('ブラウザログインに失敗しました。Edgeを確認して再試行してください。'))
            self.browser_results.put((generation,result))
        threading.Thread(target=work,daemon=True).start()

    def cancel_browser_login(self):
        self.browser_stop.set();self.browser_generation+=1;self.browser_busy=False
        self.browser_button.configure(state='normal')

    def browser_result(self,generation,result):
        if generation!=self.browser_generation:return
        self.browser_busy=False;self.browser_button.configure(state='normal')
        kind,data=result
        if kind!='success':self.auth_status.set(tr(data));return
        self.browser_session=data;self.login.set(True);self.password.set('')
        try:
            self.session_store.save(data);self.remember.set(True)
            self.auth_status.set(tr('ブラウザログイン完了。ログイン状態を暗号化して保存しました。'))
        except (OSError,ValueError):
            # Never retain a different account's old persistent session.
            try:self.session_store.delete()
            except OSError:pass
            self.auth_status.set(tr('ログイン済みですが状態を保存できませんでした。この起動中のみ利用できます。'))

    def close_otp(self):
        if self.otp_window:
            self.otp_window.destroy();self.otp_window=None

    def ask_code(self,ident):
        self.close_otp();dialog=tk.Toplevel(self.root);self.otp_window=dialog
        dialog.title(tr('メール認証'));dialog.transient(self.root);dialog.resizable(False,False)
        frame=ttk.Frame(dialog,padding=22);frame.pack()
        ttk.Label(frame,text=tr('メールで届いた6桁の認証コードを入力してください。\n入力待ちは3分で終了します。')).pack()
        code=tk.StringVar();entry=ttk.Entry(frame,textvariable=code,font=('Yu Gothic UI',18),width=12);entry.pack(pady=14);entry.focus_set()
        def send():
            value=code.get().strip()
            if len(value)!=6 or not value.isascii() or not value.isdigit():return
            self.controller.submit_otp(ident,value);code.set('');self.close_otp()
        def cancel():self.controller.submit_otp(ident,None);self.close_otp()
        ttk.Button(frame,text=tr('認証する'),command=send).pack(side='left')
        ttk.Button(frame,text=tr('キャンセル'),command=cancel).pack(side='right')
        entry.bind('<Return>',lambda e:send());dialog.protocol('WM_DELETE_WINDOW',cancel)

    def poll(self):
        if self.closed:return
        while True:
            try:generation,result=self.browser_results.get_nowait()
            except queue.Empty:break
            self.browser_result(generation,result)
        for event in self.controller.poll_events():
            kind=event['type'];text=tr(event.get('text',''))
            if kind in ('status','error'):
                self.status.set(text)
                if kind=='error':self.pending_credentials=None
            elif kind=='authenticated':self.authenticated_event(event)
            elif kind=='auth_status':self.authenticated=False;self.auth_email='';self.auth_status.set(text)
            elif kind=='session_invalid':
                self.browser_session=[]
                try:self.session_store.delete()
                except OSError:pass
                self.auth_status.set(text)
            elif kind=='comment_status':self.comment_status.set(text)
            elif kind=='post_ready':
                self.post_target=(event['request_id'],event['url']) if event.get('enabled') else None
            elif kind=='post_result':
                if self.post_window:self.post_window.receive(event)
            elif kind=='otp_required':self.ask_code(event['request_id'])
            elif kind=='stopped':self.pending_credentials=None;self.set_busy(False);self.close_otp();self.comment_status.set(tr('コメント停止中'))
        self.root.after(100,self.poll)

    def close(self):
        self.browser_stop.set();self.browser_session=[]
        self.closed=True;self.pending_credentials=None;self.password.set('');self.close_otp();self.controller.close();self.root.destroy()

def main():
    if os.name=='nt':
        import ctypes
        try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:pass
    root=tk.Tk();App(root);root.mainloop()
