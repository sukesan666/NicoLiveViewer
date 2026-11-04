"""Compact posting window. Text is kept until an acknowledgement arrives."""
from .i18n import tr
import tkinter as tk
from tkinter import ttk
from .posting import post_payload
from .layout import fit_window, zoom_container

class PostWindow:
    def __init__(self,root,controller,request_id,url):
        self.controller=controller;self.request_id=request_id;self.pending=None;self.closed=False;self.active=True
        self.window=tk.Toplevel(root);self.window.title(tr('コメント投稿'));self.window.geometry('440x195');self.window.minsize(350,180)
        self.top=tk.BooleanVar(value=True);self.anonymous=tk.BooleanVar(value=True)
        self.window.attributes('-topmost',True)
        content=zoom_container(self.window,always=True)
        frame=ttk.Frame(content,padding=10);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text=tr('投稿先: ')+url.rsplit('/',1)[-1]).pack(anchor='w')
        self.text=tk.Text(frame,height=2,width=32,wrap='word',font=('Yu Gothic UI',11),undo=True)
        self.text.pack(fill='both',expand=True,pady=5)
        row=ttk.Frame(frame);row.pack(side='bottom',fill='x',before=self.text)
        ttk.Checkbutton(row,text=tr('匿名（184）'),variable=self.anonymous).pack(side='left')
        ttk.Checkbutton(row,text=tr('最前面'),variable=self.top,command=lambda:self.window.attributes('-topmost',self.top.get())).pack(side='left')
        self.button=ttk.Button(row,text=tr('投稿'),command=self.submit);self.button.pack(side='right')
        self.status=tk.StringVar(value=tr('Ctrl＋Enterで投稿 / Enterで改行 / 75文字以内'))
        ttk.Label(frame,textvariable=self.status,wraplength=410).pack(side='bottom',anchor='w',pady=(5,0),before=row)
        self.text.bind('<Control-Return>',self.submit)
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.text.focus_set()
        fit_window(self.window,440,195,flex=self.text)
    def submit(self,event=None):
        if not self.active or self.pending or self.closed:return 'break'
        text=self.text.get('1.0','end-1c')
        try:
            post_payload(text,self.anonymous.get(),1)
            self.pending=self.controller.post_comment(self.request_id,text,self.anonymous.get())
            self.button.configure(state='disabled');self.text.configure(state='disabled')
            self.status.set(tr('送信中…（結果が不明な場合、自動再送しません）'))
        except (ValueError,OSError,UnicodeError):
            self.status.set(tr('投稿先・ログイン状態と本文を確認してください（75文字以内）。'))
        return 'break'
    def receive(self,event):
        if self.closed or event.get('request_id')!=self.request_id or event.get('post_id')!=self.pending:return
        self.pending=None;self.text.configure(state='normal')
        if event.get('ok') is True:self.text.delete('1.0','end')
        self.button.configure(state='normal' if self.active else 'disabled')
        self.status.set(tr(event.get('text',tr('投稿結果を確認できません。'))))
    def stop(self):
        if self.closed:return
        self.active=False;self.pending=None;self.button.configure(state='disabled');self.text.configure(state='normal')
        self.status.set(tr('再生を停止しました。新しい投稿先はメイン画面から小窓を開き直してください。'))
    def close(self):
        self.closed=True;self.window.destroy()
