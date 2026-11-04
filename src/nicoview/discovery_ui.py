"""Tk selectors: network and image decoding run off the Tk thread."""
from .i18n import tr
from pathlib import Path
import os
import re
import queue
import subprocess
import threading
from concurrent.futures import Future
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from .catalog import Catalog, CatalogError, thumbnail_bytes
from .thumbnail_cache import THUMBNAIL_LIMIT
from .layout import fit_window, zoom_container, zoom_factor, ScrollableTree

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
    def __init__(self, root, cookies, on_select, schedule=False,ffmpeg_path='',schedule_mode=None,on_reservations=None,reserved_keys=None,on_uncheck=None):
        self.on_select=on_select
        # When both are given, a checkbox mirrors a saved reservation (check = reserve, uncheck = cancel).
        self.reserved_keys=reserved_keys;self.on_uncheck=on_uncheck
        self.ffmpeg_path=ffmpeg_path
        self.cookies=[dict(c) for c in cookies]
        self.schedule=schedule
        self.schedule_mode=(schedule_mode or 'play') if schedule else schedule_mode
        self.window=tk.Toplevel(root)
        self.window.title(tr('予約') if schedule else tr('番組を探す'))
        self.window.geometry('1000x680')
        self.window.minsize(740,440)
        self.events=queue.Queue();self.generation=0;self.closed=False
        self.cancel=threading.Event();self.rows={};self.images={};self.checked=set();self._session_toggled={}
        self._load_pending=None;self._force_pending=False;self._poll_after=None
        self._loading_key=None;self._loaded_key=None;self._active_key=None
        self.visible_keys=()
        self._list_lock=threading.Lock();self._list_inflight={}
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.window.bind('<Destroy>',self._destroyed,add='+')
        actions_row=ttk.Frame(self.window)
        actions_row.pack(side='top',fill='x',padx=10,pady=(10,0))
        content=zoom_container(self.window,always=True)
        self.book=ttk.Notebook(content);self.book.pack(fill='x',padx=10,pady=(10,4))
        self.tabs=[(tr('フォローユーザー'),'followed_users'),(tr('フォロー中チャンネル'),'followed_channels'),(tr('今後7日・公式'),'official_week'),(tr('公式タグ'),'official_tag'),(tr('公式キーワード'),'keyword'),(tr('検索'),'search'),('URL / チャンネル','url')] if schedule else [(tr('フォロー中のユーザー'),'following_live_users'),(tr('フォロー中チャンネル'),'followed_channels'),(tr('ユーザーとチャンネルのランキング'),'ranking'),(tr('放送中の公式'),'official_live'),(tr('見逃し'),'missed'),(tr('検索'),'search')]
        for label,_ in self.tabs:self.book.add(ttk.Frame(self.book),text=label)
        bar=ttk.Frame(content);bar.pack(fill='x',padx=10,pady=5)
        self.query=tk.StringVar()
        self.entry=ttk.Entry(bar,textvariable=self.query);self.entry.pack(side='left',fill='x',expand=True)
        self.entry.bind('<Return>',lambda e:self.request_load(force=True))
        ttk.Button(bar,text=tr('読み込み / 更新'),command=lambda:self.request_load(force=True)).pack(side='left',padx=(8,0))
        self.status=tk.StringVar(value='')
        ttk.Label(content,textvariable=self.status,wraplength=940).pack(fill='x',padx=10,pady=4)
        ttk.Style(self.window).configure('Discovery.Treeview',rowheight=int(62*zoom_factor(self.window)))
        body=ScrollableTree(content,columns=('author','start','status'),style='Discovery.Treeview',selectmode='extended')
        body.pack(fill='both',expand=True,padx=10)
        self.tree=body.tree
        self.tree.heading('#0',text=tr('番組 / ユーザー'));self.tree.column('#0',width=490,minwidth=250)
        for name,label,width in [('author',tr('放送者'),130),('start',tr('開始予定'),130),('status',tr('状態'),90)]:
            self.tree.heading(name,text=label);self.tree.column(name,width=width,minwidth=70)
        def scrolled(first,last):
            body.scrollbar.set(first,last)
            children=self.tree.get_children()
            start=int(float(first)*len(children));end=int(float(last)*len(children))+1
            self.visible_keys=tuple(children[start:end])
        self.tree.configure(yscrollcommand=scrolled)
        self.tree.bind('<ButtonRelease-1>',self._toggle)
        self.tree.bind('<space>',self._toggle)
        self.tree.bind('<Double-1>',lambda e:self.choose('play') if not self.schedule else None)
        bottom=ttk.Frame(content);bottom.pack(side='bottom',fill='x',padx=10,pady=10,before=body)
        label=(tr('クリックでチェックすると、その場で予約されます（チェックを外すと解除）。チェックは次回も残ります。') if self.persistent else
               tr('チェックしたユーザーの今後の番組、または選択した番組を予約します。')) if schedule else tr('番組を選んで操作してください。')
        ttk.Label(bottom,text=label,wraplength=700).pack(anchor='w',pady=(0,6))
        if schedule:
            ttk.Label(bottom,text=tr('予約したユーザー・チャンネルをニコニコでフォローし、放送開始のプッシュ通知で実行します。'),wraplength=700).pack(anchor='w',pady=(0,6))
        actions=[(tr('再生＋録画を予約'),'both'),(tr('再生を予約'),'play'),(tr('録画を予約'),'record')] if schedule else [(tr('再生＋録画'),'both'),(tr('録画'),'record'),(tr('再生'),'play')]
        if schedule and on_reservations:
            ttk.Button(actions_row,text=tr('予約と履歴'),command=on_reservations).pack(side='left',padx=3)
        for label,mode in actions:
            ttk.Button(actions_row,text=label,command=lambda m=mode:self.choose(m)).pack(side='left',padx=3)
        self.book.bind('<<NotebookTabChanged>>',lambda e:self.request_load())
        self._poll_after=self.window.after(60,self._poll)
        fit_window(self.window,1000,680,flex=body)
        self.request_load()

    @property
    def kind(self):return self.tabs[self.book.index('current')][1]

    @property
    def persistent(self):return bool(self.schedule and self.schedule_mode and self.reserved_keys and self.on_uncheck)

    @property
    def loading(self):return self._loading_key is not None

    def _cancel_callbacks(self):
        for name in ('_load_pending','_poll_after'):
            handle=getattr(self,name,None)
            if handle:
                try:self.window.after_cancel(handle)
                except tk.TclError:pass
                setattr(self,name,None)

    def _destroyed(self,event):
        if event.widget is self.window:
            self.closed=True;self.cancel.set();self.cookies.clear();self._cancel_callbacks()

    def close(self):
        self.closed=True;self.cancel.set();self.cookies.clear();self._cancel_callbacks();self.window.destroy()

    def request_load(self,force=False):
        """Coalesce initial/tab/refresh events into one Tk idle callback."""
        if self.closed:return
        self._force_pending=self._force_pending or force
        if self._load_pending is None:self._load_pending=self.window.after_idle(self._dispatch_load)

    def _dispatch_load(self):
        self._load_pending=None
        force=self._force_pending;self._force_pending=False
        self.load(force=force)

    def load(self,force=True):
        if self.closed:return
        kind=self.kind;query=self.query.get().strip();key=(kind,query)
        if key==self._loading_key or not force and key==self._loaded_key:return
        self.cancel.set();self.cancel=threading.Event();cancel=self.cancel
        self.generation+=1;generation=self.generation
        self._loading_key=key;self._active_key=key;self._loaded_key=None
        self.rows.clear();self.images.clear();self.checked.clear();self._session_toggled.clear();self.visible_keys=()
        self.tree.delete(*self.tree.get_children())
        if kind=='keyword' and not query:
            self._loading_key=None;self._loaded_key=key
            self.status.set(tr('入力欄にキーワードを入力して「読み込み」を押すか、上の予約ボタンで登録してください。公式検索は毎日午前3時（日本時間）、取得失敗時は1時間おきです。'));return
        if kind=='official_tag' and not query:
            self._loading_key=None;self._loaded_key=key
            self.status.set(tr('入力欄にタグを入力して「読み込み」を押すか、上の予約ボタンで登録してください。公式検索は毎日午前3時（日本時間）、取得失敗時は1時間おきです。'));return
        if kind in ('search','url') and not query:
            self._loading_key=None;self._loaded_key=key
            self.status.set(tr('検索語を入力してください。') if kind=='search' else tr('番組URLまたは lv 番組番号、チャンネル番号（ch…）を入力してください。'));return
        self.status.set(tr('読み込み中…'))
        cookies=[dict(c) for c in self.cookies]
        def work():
            try:
                if cancel.is_set():return
                items=self._fetch_items(cookies,kind,query)
                if cancel.is_set():return
                self.events.put((generation,'rows',items))
                if self.ffmpeg_path and Path(self.ffmpeg_path).is_file():
                    self._load_thumbnails(items,generation,cancel)
            except CatalogError as e:
                if not cancel.is_set():self.events.put((generation,'error',str(e)))
            except Exception:
                if not cancel.is_set():self.events.put((generation,'error',tr('番組一覧を読み込めませんでした。更新して再試行してください。')))
            finally:
                cookies.clear()
                if not cancel.is_set():self.events.put((generation,'done',None))
        threading.Thread(target=work,daemon=True,name='catalog-selector').start()

    def _fetch_items(self,cookies,kind,query):
        # A tab switch cancels display work, not an HTTP request already in flight.
        # Returning to that tab can subscribe to the same pending result.
        key=(kind,query)
        with self._list_lock:
            future=self._list_inflight.get(key);owner=future is None
            if owner:future=Future();self._list_inflight[key]=future
        if not owner:return future.result()
        catalog=None
        try:
            catalog=Catalog(cookies)
            if kind=='url':
                m_ch = re.search(r'/(?:channels?/)?(ch[0-9]+)', query) or (re.fullmatch(r'ch[0-9]+', query) and [None, query])
                if m_ch:
                    items = [catalog.channel_info(m_ch[1])]
                else:
                    items = [catalog.program(query)]
            elif kind=='official_tag':items=catalog.search(query,is_tag=True,provider='official',statuses=('onair','reserved'))
            elif kind=='keyword':items=catalog.search(query,provider='official',statuses=('onair','reserved'))
            elif kind=='search':items=catalog.search(query)
            else:items=getattr(catalog,kind)()
            future.set_result(items)
            return items
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            try:
                if catalog:catalog.close()
            finally:
                with self._list_lock:self._list_inflight.pop(key,None)

    def _next_thumbnail(self,remaining):
        # The UI publishes this immutable tuple; background workers never call Tk.
        visible=set(self.visible_keys)
        index=next((i for i,p in enumerate(remaining) if self._key(p) in visible),0)
        return remaining.pop(index)

    def _load_thumbnails(self,items,generation,cancel):
        converted={};missing=[];remaining=list(items[:THUMBNAIL_LIMIT])
        def display(p,raw):
            url=p.get('thumbnail','')
            if url not in converted:converted[url]=thumbnail_png(raw,self.ffmpeg_path)
            picture=converted[url]
            if picture and not cancel.is_set():self.events.put((generation,'image',(self._key(p),picture)))
        # Populate all available disk images before requesting any missing ones.
        while remaining and not cancel.is_set():
            p=self._next_thumbnail(remaining);url=p.get('thumbnail','')
            if url in converted:
                display(p,None);continue
            raw=thumbnail_bytes(url,cache_only=True)
            if raw:display(p,raw)
            else:missing.append(p)
        fetched=set()
        while missing and not cancel.is_set():
            p=self._next_thumbnail(missing);url=p.get('thumbnail','')
            if url in converted:display(p,None)
            elif url not in fetched:
                fetched.add(url);raw=thumbnail_bytes(url,cancel=cancel)
                if raw:display(p,raw)

    @staticmethod
    def _key(p):
        ident = str(p.get('id') or '')
        if re.fullmatch(r'lv\d+', ident): return ident
        if p.get('channel_id'):
            ch = str(p['channel_id'])
            return 'channel:' + (ch if ch.startswith('ch') else f'ch{ch}')
        if p.get('user_id'): return f"user:{p['user_id']}"
        return ('channel:' + ident) if ident.startswith('ch') else ident

    def _poll(self):
        if self.closed:return
        for _ in range(100):
            try:g,kind,value=self.events.get_nowait()
            except queue.Empty:break
            if g!=self.generation:continue
            if kind=='error':self._loading_key=None;self.status.set(tr(value))
            elif kind=='done':self._loading_key=None
            elif kind=='rows':
                self._loading_key=None;self._loaded_key=self._active_key
                reserved=(self.reserved_keys('play')|self.reserved_keys('record')) if self.persistent else set()
                for p in value:
                    key=self._key(p)
                    if key in self.rows:continue
                    self.rows[key]=p
                    if key in reserved:self.checked.add(key)
                    start=datetime.fromtimestamp(p['start']).strftime('%y/%m/%d %H:%M') if p.get('start') else ''
                    state={'ON_AIR':tr('放送中'),'RESERVED':tr('放送予定'),'ENDED':tr('終了',context='state'),'USER_RULE':tr('自動予約'),'channel':tr('チャンネル')}.get(p.get('status'),p.get('status',''))
                    title=(('☑ ' if key in self.checked else '☐ ') if self.schedule else '')+p.get('title','')
                    self.tree.insert('', 'end',iid=key,text=title,values=(p.get('author',''),start,state))
                note=tr('  サムネイル表示には設定でFFmpegを指定してください。') if not self.ffmpeg_path else ''
                self.status.set((tr('{0}件。クリックで対象をチェックできます。').format(len(self.rows)) if self.schedule else tr('{0}件。').format(len(self.rows)))+note)
            elif kind=='image':
                key,im=value
                if key in self.rows:
                    try:
                        self.images[key]=tk.PhotoImage(data=im,master=self.window)
                        self.tree.item(key,image=self.images[key])
                    except tk.TclError:pass
        self._poll_after=self.window.after(60,self._poll)

    def set_schedule_mode(self,mode):
        if mode not in ('play','record'):return
        self.schedule_mode=mode
        if hasattr(self,'mode_var'):self.mode_var.set(mode)
        self._session_toggled.clear()
        self._refresh_checks()

    def _on_mode_changed(self):
        self.set_schedule_mode(self.mode_var.get())

    def _refresh_checks(self):
        if not self.schedule:return
        if self.persistent:
            self.checked=set(self.reserved_keys('play')|self.reserved_keys('record')) & set(self.rows)
        for key,p in self.rows.items():
            if self.tree.exists(key):
                self.tree.item(key,text=('☑ ' if key in self.checked else '☐ ')+p.get('title',''))

    def _toggle(self,event):
        if not self.schedule:return
        key=self.tree.identify_row(event.y) if getattr(event,'keysym','')!='space' else self.tree.focus()
        if key not in self.rows:return
        if self.persistent:
            program=dict(self.rows[key]);name=program.get('title','')
            if key in self.checked:
                self.on_uncheck(program,'play');self.on_uncheck(program,'record');self.checked.remove(key)
                self._session_toggled.pop(key,None)
                self.status.set(tr('予約を解除しました：')+name)
            else:
                error=self.on_select(program,self.schedule_mode,show=False)
                if error:self.status.set(tr(error));return
                self.checked.add(key);self._session_toggled[key]='auto'
                self.status.set(tr('予約しました：')+name+tr('（ソフト起動中に自動実行します）'))
        elif key in self.checked:self.checked.remove(key)
        else:self.checked.add(key)
        self.tree.item(key,text=('☑ ' if key in self.checked else '☐ ')+self.rows[key]['title'])

    def choose(self,mode=None):
        mode=mode or self.schedule_mode or 'play'
        if self.schedule and self.kind in ('keyword','official_tag'):
            val=self.query.get().strip()
            field='official_tag' if self.kind=='official_tag' else 'keyword'
            label=tr('タグ') if self.kind=='official_tag' else tr('キーワード')
            if not val or len(val)>100:
                self.status.set(tr('{0}は1～100文字で入力してください。').format(label));return
            self.on_select({field:val},mode)
            return
        if self.schedule:
            toggled=[k for k in self.checked if k in self._session_toggled]
            selected=[k for k in self.tree.selection() if k in self.rows]
            keys=selected or toggled or list(self.checked)
        else:
            keys=list(self.tree.selection())
        if not keys:
            messagebox.showinfo(tr('番組の選択'),tr('ユーザーをチェック、または番組を選択してください。'),parent=self.window);return
        for key in keys:
            if key in self.rows:
                program=dict(self.rows[key])
                prev_mode=self._session_toggled.get(key)
                if self.persistent and prev_mode=='auto' and mode!=self.schedule_mode:
                    self.on_uncheck(program,self.schedule_mode)
                error=self.on_select(program,mode)
                if not error and self.schedule:
                    self._session_toggled[key]=mode
                    self.checked.add(key)
                    if self.tree.exists(key):
                        self.tree.item(key,text='☑ '+self.rows[key]['title'])


class DiscoveryWindow(_Selector):
    def __init__(self,root,cookies,on_select,ffmpeg_path=''):super().__init__(root,cookies,on_select,False,ffmpeg_path)

class SchedulePicker(_Selector):
    def __init__(self,root,cookies,on_select,ffmpeg_path='',mode='play',on_reservations=None,reserved_keys=None,on_uncheck=None):
        if mode not in ('play','record','both'):raise ValueError(tr('予約方法が不正です。'))
        super().__init__(root,cookies,on_select,True,ffmpeg_path,mode,on_reservations,reserved_keys,on_uncheck)
