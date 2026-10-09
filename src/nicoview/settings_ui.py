"""Settings and first-run dialogs; parent applies saved changes to live jobs."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from .settings import clean_settings
from .layout import fit_window, ScrollPage

class SettingsDialog(tk.Toplevel):
    def __init__(self,root,settings,on_save):
        super().__init__(root)
        self.title('設定 — NicoLive Studio'); self.geometry('760x650'); self.minsize(680,580)
        self.transient(root); self.settings=clean_settings(settings); self.on_save=on_save; self.variables={}; self.texts={}
        notebook=ttk.Notebook(self); notebook.pack(fill='both',expand=True,padx=16,pady=12)
        panes={name:ScrollPage(notebook) for name in ('再生・コメント','録画・動作','通知')}
        pages={name:pane.inner for name,pane in panes.items()}
        for name,pane in panes.items(): notebook.add(pane,text=name); pane.inner.columnconfigure(1,weight=1)
        self._choice(pages['再生・コメント'],'ui_scale','画面の拡大率 ％（再起動後に適用）',tuple(str(n) for n in range(100,251,10)))
        self._entry(pages['再生・コメント'],'font','コメントのフォント')
        self._check(pages['再生・コメント'],'enabled','コメントを表示')
        self._entry(pages['再生・コメント'],'delay','コメント受信待機 秒 (0–30)')
        self._entry(pages['再生・コメント'],'size','文字サイズ (12–96)')
        self._entry(pages['再生・コメント'],'fps','コメント FPS (1–240)')
        self._choice(pages['再生・コメント'],'theme','テーマ',('dark','light'))
        self._entry(pages['再生・コメント'],'volume','音量 (0–100)')
        self._check(pages['再生・コメント'],'muted','消音')
        self._choice(pages['再生・コメント'],'quality','画質',('best','worst','high','normal','low','audio_only','1080p','720p','480p','360p'))
        self._entry(pages['再生・コメント'],'comment_offset','コメント時間補正 秒 (負数は早める)')
        for key,label in [('ng_words','NGワード（1行に1件）'),('ng_users','NGユーザーID（1行に1件）')]:
            page=pages['再生・コメント']; row=page.grid_size()[1]
            ttk.Label(page,text=label).grid(row=row,column=0,sticky='nw',pady=5)
            box=tk.Text(page,height=3,width=32); box.grid(row=row,column=1,sticky='ew',pady=5); box.insert('1.0','\n'.join(self.settings[key])); self.texts[key]=box
        page=pages['録画・動作']
        self._entry(page,'recording_dir','録画先',directory=True)
        self._entry(page,'max_jobs','同時処理数 (1–16)')
        self._entry(page,'player_path','mpv 実行ファイル',file=True)
        self._entry(page,'ffmpeg_path','FFmpeg 実行ファイル',file=True)
        self._check(page,'svp_enabled','最初の再生だけ SVP を利用')
        self._entry(page,'svp_player_path','SVP用 mpv',file=True)
        self._check(page,'reconnect','切断時に再接続')
        self._check(page,'inhibit_sleep','処理中は自動スリープを抑制')
        self._check(page,'startup','Windows サインイン時に起動')
        self._check(page,'tray','閉じる操作で通知領域に常駐')
        page=pages['通知']
        self._entry(page,'sound_play','予約再生の開始音 (PCM WAV)',file=True)
        self._entry(page,'sound_record','予約録画の開始音 (PCM WAV)',file=True)
        self._entry(page,'sound_volume','通知音量 (0–100)')
        self._entry(page,'notification_group_seconds','連続通知をまとめる秒数 (0–300)')
        self._check(page,'quiet_enabled','指定時間は通知音を鳴らさない')
        self._entry(page,'quiet_start','開始時刻 (HH:MM)'); self._entry(page,'quiet_end','終了時刻 (HH:MM)')
        testbar=ttk.Frame(page); testbar.grid(row=page.grid_size()[1],column=0,columnspan=3,sticky='w',pady=8)
        ttk.Button(testbar,text='再生の開始音を試す',command=lambda:self.test_sound('play')).pack(side='left',padx=4)
        ttk.Button(testbar,text='録画の開始音を試す',command=lambda:self.test_sound('record')).pack(side='left',padx=4)
        ttk.Label(page,text='同時刻なら時間制限なし。WAV は60秒・10MB以内。\n録画を伴う予約では録画の開始音を使います。',wraplength=500).grid(row=page.grid_size()[1],column=0,columnspan=3,sticky='w',pady=14)
        buttons=ttk.Frame(self,padding=(16,0,16,16)); buttons.pack(side='bottom',fill='x',before=notebook)
        ttk.Button(buttons,text='キャンセル',command=self.destroy).pack(side='right',padx=4)
        ttk.Button(buttons,text='保存して適用',command=self.save).pack(side='right',padx=4)
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
            ttk.Button(page,text='参照',command=choose).grid(row=row,column=2,padx=5)
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
            messagebox.showinfo('通知音', 'PCM WAVファイルを選び、音量を1以上にしてください。',parent=self)
    def save(self):
        values=dict(self.settings); values.update({k:v.get() for k,v in self.variables.items()})
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
            messagebox.showerror('設定を保存できません',f'入力値とファイルを確認してください。\n{exc}',parent=self); return
        self.destroy()

class FirstRunGuide(tk.Toplevel):
    def __init__(self,root,settings,on_save,on_login):
        super().__init__(root); self.title('NicoLive Studio へようこそ'); self.geometry('680x420'); self.transient(root)
        self.settings=clean_settings(settings)
        pane=ScrollPage(self);pane.pack(fill='both',expand=True);body=pane.inner;body.columnconfigure(0,weight=1)
        ttk.Label(body,text='はじめの設定',font=('Meiryo',18,'bold')).grid(row=0,column=0,sticky='w',pady=(0,12))
        ttk.Label(body,text='視聴に使うアカウントと録画先を設定します。\nあとから設定メニューで変更できます。').grid(row=1,column=0,sticky='w',pady=8)
        self.folder=tk.StringVar(value=self.settings['recording_dir'])
        ttk.Entry(body,textvariable=self.folder).grid(row=2,column=0,sticky='ew')
        def browse():
            path=filedialog.askdirectory(parent=self)
            if path: self.folder.set(path)
        ttk.Button(body,text='録画先を選択',command=browse).grid(row=2,column=1,padx=8)
        self.paths={}
        for index,(key,label) in enumerate((('player_path','mpv 実行ファイル'),('ffmpeg_path','FFmpeg 実行ファイル')),3):
            variable=tk.StringVar(value=self.settings[key]); self.paths[key]=variable
            row=ttk.Frame(body); row.grid(row=index,column=0,columnspan=2,sticky='ew',pady=6)
            ttk.Label(row,text=label).pack(side='left')
            ttk.Entry(row,textvariable=variable).pack(side='left',fill='x',expand=True,padx=8)
            def choose(v=variable):
                path=filedialog.askopenfilename(parent=self,filetypes=[('実行ファイル','*.exe'),('すべて','*.*')])
                if path: v.set(path)
            ttk.Button(row,text='参照',command=choose).pack(side='right')
        ttk.Button(body,text='ログインを設定',command=on_login).grid(row=5,column=0,sticky='w',pady=16)
        def finish():
            values=dict(self.settings); values.update(recording_dir=self.folder.get(),first_run_complete=True); values.update({k:v.get() for k,v in self.paths.items()})
            on_save(clean_settings(values)); self.destroy()
        ttk.Button(body,text='この設定で始める',command=finish).grid(row=6,column=0,sticky='e',pady=8)

        pane.refresh();fit_window(self,680,420,flex=pane)
