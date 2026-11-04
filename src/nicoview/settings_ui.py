"""Settings and first-run dialogs; parent applies saved changes to live jobs."""
from .i18n import LANGUAGES, tr
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from tkinter import font as tkfont
from .settings import clean_settings
from .layout import fit_window, ScrollPage

class SettingsDialog(tk.Toplevel):
    def __init__(self,root,settings,on_save):
        super().__init__(root)
        self.title(tr('設定 — NicoLive Studio')); self.geometry('760x650'); self.minsize(680,580)
        self.transient(root); self.settings=clean_settings(settings); self.on_save=on_save; self.variables={}; self.texts={}
        notebook=ttk.Notebook(self); notebook.pack(fill='both',expand=True,padx=16,pady=12)
        panes={name:ScrollPage(notebook) for name in (tr('再生・コメント'),tr('録画・動作'),tr('通知'))}
        pages={name:pane.inner for name,pane in panes.items()}
        for name,pane in panes.items(): notebook.add(pane,text=name); pane.inner.columnconfigure(1,weight=1)
        self._choice(pages[tr('再生・コメント')],'ui_scale',tr('画面の拡大率 ％（再起動後に適用）'),tuple(str(n) for n in range(100,251,10)))
        self._font(pages[tr('再生・コメント')],'font',tr('コメントのフォント'))
        self._check(pages[tr('再生・コメント')],'enabled',tr('コメントを表示'))
        self._slider(pages[tr('再生・コメント')],'delay',tr('コメント受信待機 秒'),0,30,.5)
        self._slider(pages[tr('再生・コメント')],'size',tr('文字サイズ'),12,96)
        self._slider(pages[tr('再生・コメント')],'fps',tr('コメント FPS'),1,240)
        self._choice(pages[tr('再生・コメント')],'theme',tr('テーマ'),('dark','light'))
        self._language(pages[tr('再生・コメント')])
        self._slider(pages[tr('再生・コメント')],'volume',tr('音量'),0,100)
        self._check(pages[tr('再生・コメント')],'muted',tr('消音'))
        self._choice(pages[tr('再生・コメント')],'quality',tr('画質'),('best','worst','high','normal','low','audio_only','1080p','720p','480p','360p'))
        self._slider(pages[tr('再生・コメント')],'comment_offset',tr('コメント時間補正 秒 (負数は早める)'),-60,60,.5)
        for key,label in [('ng_words',tr('NGワード（1行に1件）')),('ng_users',tr('NGユーザーID（1行に1件）'))]:
            page=pages[tr('再生・コメント')]; row=page.grid_size()[1]
            ttk.Label(page,text=label).grid(row=row,column=0,sticky='nw',pady=5)
            box=tk.Text(page,height=3,width=32); box.grid(row=row,column=1,sticky='ew',pady=5); box.insert('1.0','\n'.join(self.settings[key])); self.texts[key]=box
        page=pages[tr('録画・動作')]
        self._entry(page,'recording_dir',tr('録画先'),directory=True)
        self._slider(page,'max_jobs',tr('同時処理数'),1,16)
        self._entry(page,'player_path',tr('mpv 実行ファイル'),file=True)
        self._entry(page,'ffmpeg_path',tr('FFmpeg 実行ファイル'),file=True)
        self._check(page,'svp_enabled',tr('最初の再生だけ SVP を利用'))
        self._entry(page,'svp_player_path',tr('SVP用 mpv'),file=True)
        self._check(page,'reconnect',tr('切断時に再接続'))
        self._check(page,'inhibit_sleep',tr('処理中は自動スリープを抑制'))
        self._check(page,'startup',tr('Windows サインイン時に起動'))
        self._check(page,'tray',tr('閉じる操作で通知領域に常駐'))
        page=pages[tr('通知')]
        self._entry(page,'sound_play',tr('予約再生の開始音 (PCM WAV)'),file=True)
        self._entry(page,'sound_record',tr('予約録画の開始音 (PCM WAV)'),file=True)
        self._slider(page,'sound_volume',tr('通知音量'),0,100)
        self._slider(page,'notification_group_seconds',tr('連続通知をまとめる秒数'),0,300)
        self._check(page,'quiet_enabled',tr('指定時間は通知音を鳴らさない'))
        self._entry(page,'quiet_start',tr('開始時刻 (HH:MM)')); self._entry(page,'quiet_end',tr('終了時刻 (HH:MM)'))
        testbar=ttk.Frame(page); testbar.grid(row=page.grid_size()[1],column=0,columnspan=3,sticky='w',pady=8)
        ttk.Button(testbar,text=tr('再生の開始音を試す'),command=lambda:self.test_sound('play')).pack(side='left',padx=4)
        ttk.Button(testbar,text=tr('録画の開始音を試す'),command=lambda:self.test_sound('record')).pack(side='left',padx=4)
        ttk.Label(page,text=tr('同時刻なら時間制限なし。WAV は60秒・10MB以内。\n録画を伴う予約では録画の開始音を使います。'),wraplength=500).grid(row=page.grid_size()[1],column=0,columnspan=3,sticky='w',pady=14)
        buttons=ttk.Frame(self,padding=(16,0,16,16)); buttons.pack(side='bottom',fill='x',before=notebook)
        ttk.Button(buttons,text=tr('キャンセル'),command=self.destroy).pack(side='right',padx=4)
        ttk.Button(buttons,text=tr('保存して適用'),command=self.save).pack(side='right',padx=4)
        for pane in panes.values():pane.refresh()
        fit_window(self,760,650,flex=notebook)
    def _entry(self,page,key,label,directory=False,file=False):
        row=page.grid_size()[1]; variable=tk.StringVar(value=str(self.settings[key])); self.variables[key]=variable
        ttk.Label(page,text=label).grid(row=row,column=0,sticky='w',pady=5,padx=(0,12))
        ttk.Entry(page,textvariable=variable).grid(row=row,column=1,sticky='ew',pady=5)
        if directory or file:
            def choose():
                value=filedialog.askdirectory(parent=self) if directory else filedialog.askopenfilename(parent=self)
                if value: variable.set(value)
            ttk.Button(page,text=tr('参照'),command=choose).grid(row=row,column=2,padx=5)
    def _slider(self,page,key,label,low,high,step=1):
        row=page.grid_size()[1]; digits=0 if step==1 else 1
        current=float(self.settings[key]); low=min(low,current); high=max(high,current)
        variable=tk.DoubleVar(value=current); self.variables[key]=variable
        ttk.Label(page,text=label).grid(row=row,column=0,sticky='w',pady=5,padx=(0,12))
        box=ttk.Frame(page); box.grid(row=row,column=1,sticky='ew',pady=5); box.columnconfigure(0,weight=1)
        shown=tk.StringVar(value=f'{current:.{digits}f}')
        def moved(value):
            rounded=round(round(float(value)/step)*step,digits); variable.set(rounded); shown.set(f'{rounded:.{digits}f}')
        ttk.Scale(box,from_=low,to=high,variable=variable,command=moved).grid(row=0,column=0,sticky='ew')
        ttk.Label(box,textvariable=shown,width=7,anchor='e').grid(row=0,column=1,padx=(10,0))
    def _font(self,page,key,label):
        row=page.grid_size()[1]; fonts=sorted({f for f in tkfont.families(self) if not f.startswith('@')})
        name=self.settings[key]
        if name not in fonts: name='Meiryo' if 'Meiryo' in fonts else (fonts[0] if fonts else name)
        v=tk.StringVar(value=name); self.variables[key]=v
        ttk.Label(page,text=label).grid(row=row,column=0,sticky='w',pady=5,padx=(0,12))
        ttk.Combobox(page,textvariable=v,values=fonts,state='readonly',height=15).grid(row=row,column=1,sticky='ew',pady=5)
    def _check(self,page,key,label):
        v=tk.BooleanVar(value=self.settings[key]); self.variables[key]=v
        ttk.Checkbutton(page,text=label,variable=v).grid(row=page.grid_size()[1],column=0,columnspan=3,sticky='w',pady=6)
    def _choice(self,page,key,label,values):
        row=page.grid_size()[1]; v=tk.StringVar(value=self.settings[key]); self.variables[key]=v
        ttk.Label(page,text=label).grid(row=row,column=0,sticky='w',pady=5)
        ttk.Combobox(page,textvariable=v,values=values,state='readonly').grid(row=row,column=1,sticky='ew',pady=5)
    def test_sound(self,kind):
        from .desktop import NotificationSound
        values=dict(self.settings); values.update({k:v.get() for k,v in self.variables.items()})
        values=clean_settings(values); values['quiet_enabled']=False; values['notification_group_seconds']=0
        if not NotificationSound().notify(kind,values):
            messagebox.showinfo(tr('通知音'), tr('PCM WAVファイルを選び、音量を1以上にしてください。'),parent=self)
    def _language(self,page):
        row=page.grid_size()[1]
        variable=tk.StringVar(value=LANGUAGES[self.settings['language']])
        self.variables['language']=variable
        ttk.Label(page,text=tr('言語（再起動後に適用）')).grid(row=row,column=0,sticky='w',pady=5)
        ttk.Combobox(page,textvariable=variable,values=tuple(LANGUAGES.values()),state='readonly').grid(row=row,column=1,sticky='ew',pady=5)
    def save(self):
        values=dict(self.settings); values.update({k:v.get() for k,v in self.variables.items()})
        values['language']=next((code for code,label in LANGUAGES.items() if label==values['language']),self.settings['language'])
        values.update({k:box.get('1.0','end').strip().splitlines() for k,box in self.texts.items()})
        try:
            for key in ('size','fps','volume','max_jobs','sound_volume','notification_group_seconds'): int(values[key])
            float(values['comment_offset']); float(values['delay'])
            for key in ('quiet_start','quiet_end'):
                h,m=map(int,values[key].split(':'))
                if not (0<=h<24 and 0<=m<60): raise ValueError()
            for key in ('sound_play','sound_record'):
                if values[key]:
                    from .desktop import scaled_wav
                    scaled_wav(values[key],values['sound_volume'])
            self.on_save(clean_settings(values))
        except Exception as exc:
            messagebox.showerror(tr('設定を保存できません'),tr('入力値とファイルを確認してください。\n{0}').format(tr(str(exc))),parent=self); return
        self.destroy()

class FirstRunGuide(tk.Toplevel):
    def __init__(self,root,settings,on_save,on_login):
        super().__init__(root); self.title(tr('NicoLive Studio へようこそ')); self.geometry('680x420'); self.transient(root)
        self.settings=clean_settings(settings)
        pane=ScrollPage(self);pane.pack(fill='both',expand=True);body=pane.inner;body.columnconfigure(0,weight=1)
        ttk.Label(body,text=tr('はじめの設定'),font=('Meiryo',18,'bold')).grid(row=0,column=0,sticky='w',pady=(0,12))
        ttk.Label(body,text=tr('視聴に使うアカウントと録画先を設定します。\nあとから設定メニューで変更できます。')).grid(row=1,column=0,sticky='w',pady=8)
        self.folder=tk.StringVar(value=self.settings['recording_dir'])
        ttk.Entry(body,textvariable=self.folder).grid(row=2,column=0,sticky='ew')
        def browse():
            path=filedialog.askdirectory(parent=self)
            if path: self.folder.set(path)
        ttk.Button(body,text=tr('録画先を選択'),command=browse).grid(row=2,column=1,padx=8)
        self.paths={}
        for index,(key,label) in enumerate((('player_path',tr('mpv 実行ファイル')),('ffmpeg_path',tr('FFmpeg 実行ファイル'))),3):
            variable=tk.StringVar(value=self.settings[key]); self.paths[key]=variable
            row=ttk.Frame(body); row.grid(row=index,column=0,columnspan=2,sticky='ew',pady=6)
            ttk.Label(row,text=label).pack(side='left')
            ttk.Entry(row,textvariable=variable).pack(side='left',fill='x',expand=True,padx=8)
            def choose(v=variable):
                path=filedialog.askopenfilename(parent=self,filetypes=[(tr('実行ファイル'),'*.exe'),(tr('すべて'),'*.*')])
                if path: v.set(path)
            ttk.Button(row,text=tr('参照'),command=choose).pack(side='right')
        ttk.Button(body,text=tr('ログインを設定'),command=on_login).grid(row=5,column=0,sticky='w',pady=16)
        def finish():
            values=dict(self.settings); values.update(recording_dir=self.folder.get(),first_run_complete=True); values.update({k:v.get() for k,v in self.paths.items()})
            on_save(clean_settings(values)); self.destroy()
        ttk.Button(body,text=tr('この設定で始める'),command=finish).grid(row=6,column=0,sticky='e',pady=8)

        pane.refresh();fit_window(self,680,420,flex=pane)
