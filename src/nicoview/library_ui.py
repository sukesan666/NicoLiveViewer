import queue
import tempfile
import threading
import tkinter as tk
from tkinter import ttk,messagebox
from .library import scan_library,make_thumbnail
from .layout import fit_window, zoom_container

class LibraryWindow(tk.Toplevel):
    def __init__(self,root,recording_dir,history,on_play,on_watched=None,ffmpeg_path=''):
        super().__init__(root); self.title('録画ライブラリ'); self.geometry('930x620'); self.transient(root)
        self.on_play=on_play; self.on_watched=on_watched; self.ffmpeg_path=ffmpeg_path
        self.rows=[]; self.results=queue.Queue(); self.images={}; self.generation=0; self._closed=False; self.thumbnail_busy=False
        self.query=tk.StringVar(); self.status=tk.StringVar(value='録画を読み込み中…')
        content=zoom_container(self,always=True)
        bar=ttk.Frame(content,padding=12); bar.pack(fill='x'); ttk.Label(bar,text='タイトル・配信者で検索').pack(side='left')
        ttk.Entry(bar,textvariable=self.query).pack(side='left',fill='x',expand=True,padx=10)
        self.query.trace_add('write',lambda *_:self.render())
        self.tree=ttk.Treeview(content,columns=('author','state','resume'),show='tree headings',height=16)
        self.tree.heading('#0',text='番組'); self.tree.heading('author',text='配信者'); self.tree.heading('state',text='状態'); self.tree.heading('resume',text='再開位置')
        self.tree.column('#0',width=390); self.tree.column('author',width=150); self.tree.column('state',width=90); self.tree.column('resume',width=90)
        self.tree.pack(fill='both',expand=True,padx=12); self.tree.bind('<<TreeviewSelect>>',self.preview)
        bottom=ttk.Frame(content,padding=12); bottom.pack(side='bottom',fill='x',before=self.tree)
        self.thumbnail=ttk.Label(bottom,text='録画を選択するとプレビューを表示'); self.thumbnail.pack(side='left')
        actions=ttk.Frame(bottom); actions.pack(side='right')
        ttk.Button(actions,text='続きから再生',command=self.play).pack(fill='x',pady=3)
        ttk.Button(actions,text='最初から再生',command=lambda:self.play(False)).pack(fill='x',pady=3)
        if on_watched: ttk.Button(actions,text='視聴済みにする',command=self.mark_watched).pack(fill='x',pady=3)
        ttk.Label(content,textvariable=self.status,padding=(12,0,12,8),wraplength=800).pack(side='bottom',fill='x',before=bottom)
        def scan():
            try: self.results.put(('scan',scan_library(recording_dir,history)))
            except Exception: self.results.put(('error','録画フォルダーを読み込めませんでした'))
        threading.Thread(target=scan,daemon=True).start(); self.after(100,self.poll)
        self.protocol('WM_DELETE_WINDOW',self.destroy)
        fit_window(self,930,620,flex=self.tree)
    def destroy(self):
        self._closed=True; super().destroy()
    def poll(self):
        if self._closed: return
        try:
            while True:
                event=self.results.get_nowait()
                if event[0]=='scan': self.rows=event[1]; self.render()
                elif event[0]=='error': self.status.set(event[1])
                elif event[0]=='image':
                    self.thumbnail_busy=False
                    if event[1]!=self.generation:
                        self.preview(); continue
                    if event[2]:
                        try:
                            self.image=tk.PhotoImage(file=str(event[2])); self.thumbnail.configure(image=self.image,text='')
                        except tk.TclError: self.thumbnail.configure(image='',text='プレビューを表示できません')
                    else: self.thumbnail.configure(image='',text='プレビューなし（FFmpegで生成できる録画のみ）')
        except queue.Empty: pass
        self.after(100,self.poll)
    def render(self):
        self.tree.delete(*self.tree.get_children()); q=self.query.get().casefold()
        labels={'complete':'完了','interrupted':'中断 / 処理中','missing':'ファイルなし'}
        for i,row in enumerate(self.rows):
            if q not in (row['title']+' '+row['author']).casefold(): continue
            state='視聴済み' if row.get('watched') else labels.get(row['state'],row['state'])
            self.tree.insert('','end',iid=str(i),text=row['title'],values=(row['author'],state,f"{int(row['position'])//60}:{int(row['position'])%60:02}"))
        self.status.set(f'{len(self.tree.get_children())}件 — 中断・処理中のファイルは再生できない場合があります')
    def selected(self):
        selected=self.tree.selection(); return self.rows[int(selected[0])] if selected else None
    def play(self,resume=True):
        row=self.selected()
        if not row: return
        if row['state']=='missing': messagebox.showwarning('ファイルなし','録画ファイルが見つかりません。',parent=self); return
        self.on_play(row['path'],row.get('position',0) if resume else 0)
    def mark_watched(self):
        row=self.selected()
        if row and self.on_watched:
            self.on_watched(row['path']); row['watched']=True; self.render()
    def preview(self,_event=None):
        row=self.selected()
        if not row: return
        self.generation+=1; generation=self.generation
        self.thumbnail.configure(image='',text='プレビューを生成中…')
        if self.thumbnail_busy: return
        self.thumbnail_busy=True
        def generate():
            try: path=make_thumbnail(row['path'],self.ffmpeg_path,tempfile.gettempdir()+'/NicoLiveViewer-thumbnails')
            except Exception: path=None
            self.results.put(('image',generation,path))
        threading.Thread(target=generate,daemon=True).start()
