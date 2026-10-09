"""Tk selectors: network and image decoding run off the Tk thread."""
from pathlib import Path
import os
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from .catalog import Catalog, CatalogError, thumbnail_bytes
from .layout import fit_window, zoom_container, zoom_factor

def thumbnail_png(raw,ffmpeg_path):
    if not raw or len(raw)>2*1024*1024 or not ffmpeg_path or not Path(ffmpeg_path).is_file():return None
    args=[str(ffmpeg_path),'-nostdin','-hide_banner','-loglevel','error','-max_alloc','33554432',
          '-protocol_whitelist','pipe','-f','image2pipe','-i','pipe:0','-frames:v','1',
          '-vf','scale=96:54:force_original_aspect_ratio=decrease,pad=96:54:(ow-iw)/2:(oh-ih)/2',
          '-f','image2pipe','-vcodec','png','pipe:1']
    try:
        result=subprocess.run(args,input=raw,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=8,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode==0 and len(result.stdout)<65536 and result.stdout.startswith(b'\x89PNG'):return result.stdout
    except (OSError,subprocess.TimeoutExpired):pass
    return None


class _Selector:
    def __init__(self, root, cookies, on_select, schedule=False,ffmpeg_path='',schedule_mode=None,on_reservations=None):
        self.on_select=on_select
        self.ffmpeg_path=ffmpeg_path
        self.cookies=[dict(c) for c in cookies]
        self.schedule=schedule
        self.schedule_mode=schedule_mode
        self.window=tk.Toplevel(root)
        self.window.title(('自動録画の予約' if schedule_mode=='record' else '自動起動の予約（再生）') if schedule else '番組を探す')
        self.window.geometry('1000x680')
        self.window.minsize(740,440)
        self.events=queue.Queue();self.generation=0;self.closed=False
        self.cancel=threading.Event();self.rows={};self.images={};self.checked=set()
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.window.bind('<Destroy>',self._destroyed,add='+')
        if schedule:
            actions_row=ttk.Frame(self.window)
            actions_row.pack(side='top',fill='x',padx=10,pady=(10,0))
        content=zoom_container(self.window,always=True)
        self.book=ttk.Notebook(content);self.book.pack(fill='x',padx=10,pady=(10,4))
        self.tabs=[('フォローユーザー','followed_users'),('今後7日・公式','official_week'),('公式キーワード','keyword'),('検索','search'),('URL','url')] if schedule else [('フォロー中のユーザー','following_live_users'),('ユーザーとチャンネルのランキング','ranking'),('放送中の公式','official_live'),('見逃し','missed'),('検索','search')]
        for label,_ in self.tabs:self.book.add(ttk.Frame(self.book),text=label)
        bar=ttk.Frame(content);bar.pack(fill='x',padx=10,pady=5)
        self.query=tk.StringVar()
        self.entry=ttk.Entry(bar,textvariable=self.query);self.entry.pack(side='left',fill='x',expand=True)
        self.entry.bind('<Return>',lambda e:self.load())
        ttk.Button(bar,text='読み込み / 更新',command=self.load).pack(side='left',padx=(8,0))
        self.status=tk.StringVar(value='')
        ttk.Label(content,textvariable=self.status,wraplength=940).pack(fill='x',padx=10,pady=4)
        body=ttk.Frame(content);body.pack(fill='both',expand=True,padx=10)
        ttk.Style(self.window).configure('Discovery.Treeview',rowheight=int(62*zoom_factor(self.window)))
        self.tree=ttk.Treeview(body,columns=('author','start','status'),style='Discovery.Treeview',selectmode='extended')
        self.tree.heading('#0',text='番組 / ユーザー');self.tree.column('#0',width=490,minwidth=250)
        for name,label,width in [('author','放送者',130),('start','開始予定',130),('status','状態',90)]:
            self.tree.heading(name,text=label);self.tree.column(name,width=width,minwidth=70)
        scroll=ttk.Scrollbar(body,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        self.tree.bind('<ButtonRelease-1>',self._toggle)
        self.tree.bind('<space>',self._toggle)
        self.tree.bind('<Double-1>',lambda e:self.choose('play') if self.kind!='followed_users' else None)
        bottom=ttk.Frame(content);bottom.pack(side='bottom',fill='x',padx=10,pady=10,before=body)
        label='チェックしたユーザーの今後の番組、または選択した番組を予約します。' if schedule else '番組を選んで操作してください。'
        ttk.Label(bottom,text=label,wraplength=700).pack(anchor='w',pady=(0,6))
        if not schedule:
            actions_row=ttk.Frame(bottom);actions_row.pack(fill='x')
        actions=[('録画を予約','record')] if schedule_mode=='record' else [('再生を予約','play')] if schedule_mode=='play' else [('再生','play'),('録画','record'),('再生＋録画','both')]
        if schedule and on_reservations:
            ttk.Button(actions_row,text='予約と履歴',command=on_reservations).pack(side='left',padx=3)
        for label,mode in actions:
            ttk.Button(actions_row,text=label,command=lambda m=mode:self.choose(m)).pack(side='left' if schedule else 'right',padx=3)
        self.book.bind('<<NotebookTabChanged>>',lambda e:self.load())
        self.window.after(60,self._poll)
        fit_window(self.window,1000,680,flex=body)
        self.window.after_idle(self.load)

    @property
    def kind(self):return self.tabs[self.book.index('current')][1]

    def _destroyed(self,event):
        if event.widget is self.window:
            self.closed=True;self.cancel.set();self.cookies.clear()

    def close(self):
        self.closed=True;self.cancel.set();self.cookies.clear();self.window.destroy()

    def load(self):
        if self.closed:return
        self.cancel.set();self.cancel=threading.Event();cancel=self.cancel
        self.generation+=1;generation=self.generation
        kind=self.kind;query=self.query.get().strip()
        self.rows.clear();self.images.clear();self.checked.clear()
        self.tree.delete(*self.tree.get_children())
        if kind=='keyword':
            self.status.set('入力欄にタイトルの一部を入力し、上の予約ボタンで登録してください。公式番組を10分ごとに確認します。予約と履歴から削除できます。');return
        if kind in ('search','url') and not query:
            self.status.set('検索語を入力してください。' if kind=='search' else '番組URLまたは lv 番組番号を入力してください。');return
        self.status.set('読み込み中…')
        cookies=[dict(c) for c in self.cookies]
        def work():
            catalog=Catalog(cookies)
            try:
                items=[catalog.program(query)] if kind=='url' else catalog.search(query) if kind=='search' else getattr(catalog,kind)()
                if cancel.is_set():return
                self.events.put((generation,'rows',items))
                # FFmpeg is already an optional external dependency; no extra image runtime.
                for p in items[:200]:
                    if cancel.is_set():return
                    raw=thumbnail_bytes(p.get('thumbnail',''))
                    if raw:
                        picture=thumbnail_png(raw,self.ffmpeg_path)
                        if picture and not cancel.is_set():self.events.put((generation,'image',(self._key(p),picture)))
            except CatalogError as e:
                if not cancel.is_set():self.events.put((generation,'error',str(e)))
            except Exception:
                if not cancel.is_set():self.events.put((generation,'error','番組一覧を読み込めませんでした。更新して再試行してください。'))
            finally:catalog.close();cookies.clear()
        threading.Thread(target=work,daemon=True,name='catalog-selector').start()

    @staticmethod
    def _key(p):return p.get('id') or 'user:'+p.get('user_id','')

    def _poll(self):
        if self.closed:return
        for _ in range(100):
            try:g,kind,value=self.events.get_nowait()
            except queue.Empty:break
            if g!=self.generation:continue
            if kind=='error':self.status.set(value)
            elif kind=='rows':
                for p in value:
                    key=self._key(p)
                    if key in self.rows:continue
                    self.rows[key]=p
                    start=datetime.fromtimestamp(p['start']).strftime('%m/%d %H:%M') if p.get('start') else ''
                    state={'ON_AIR':'放送中','RESERVED':'放送予定','ENDED':'終了','USER_RULE':'自動予約'}.get(p.get('status'),p.get('status',''))
                    title=('☐ ' if self.schedule else '')+p.get('title','')
                    self.tree.insert('', 'end',iid=key,text=title,values=(p.get('author',''),start,state))
                note='  サムネイル表示には設定でFFmpegを指定してください。' if not self.ffmpeg_path else ''
                self.status.set((f'{len(self.rows)}件。クリックで対象をチェックできます。' if self.schedule else f'{len(self.rows)}件。')+note)
            elif kind=='image':
                key,im=value
                if key in self.rows:
                    try:
                        self.images[key]=tk.PhotoImage(data=im,master=self.window)
                        self.tree.item(key,image=self.images[key])
                    except tk.TclError:pass
        self.window.after(60,self._poll)

    def _toggle(self,event):
        if not self.schedule:return
        key=self.tree.identify_row(event.y) if getattr(event,'keysym','')!='space' else self.tree.focus()
        if key not in self.rows:return
        if key in self.checked:self.checked.remove(key)
        else:self.checked.add(key)
        self.tree.item(key,text=('☑ ' if key in self.checked else '☐ ')+self.rows[key]['title'])

    def choose(self,mode):
        if self.schedule_mode:mode=self.schedule_mode
        if self.schedule and self.kind=='keyword':
            keyword=self.query.get().strip()
            if not keyword or len(keyword)>100:
                self.status.set('キーワードは1～100文字で入力してください。');return
            self.on_select({'keyword':keyword},mode)
            return
        keys=list(self.checked) if self.schedule else list(self.tree.selection())
        if not keys:
            messagebox.showinfo('番組の選択','ユーザーをチェック、または番組を選択してください。',parent=self.window);return
        for key in keys:
            if key in self.rows:self.on_select(dict(self.rows[key]),mode)


class DiscoveryWindow(_Selector):
    def __init__(self,root,cookies,on_select,ffmpeg_path=''):super().__init__(root,cookies,on_select,False,ffmpeg_path)

class SchedulePicker(_Selector):
    def __init__(self,root,cookies,on_select,ffmpeg_path='',mode='play',on_reservations=None):
        if mode not in ('play','record'):raise ValueError('予約方法が不正です。')
        super().__init__(root,cookies,on_select,True,ffmpeg_path,mode,on_reservations)
