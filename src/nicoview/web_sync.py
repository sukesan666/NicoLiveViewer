"""Web integration status based on official player implementation inspection.

The audience's NG list uses LeoPlayer/NgStore browser storage (key 'list').
The server-side NG operations belong to broadcaster/moderator moderation.
Cookies do not provide access to local browser storage, so there is no honest
account-level sync operation to call here.
"""
import tkinter as tk
from tkinter import ttk
import webbrowser

NG_LIMITATION='公式Webプレーヤーの視聴者NG設定はブラウザ内に保存されています。ログイン情報だけで同期するAPIは確認できないため、Webとの自動同期は未対応です。このアプリのNG設定は設定画面で編集できます。'
GIFT_LIMITATION='公式と同一のギフト演出を外部プレーヤーに組み込む、公開・再配布可能な仕組みは確認できていません。完全なギフト演出は公式の視聴ページをご利用ください。'

def open_sync_dialog(root,cookies=None,settings=None,on_apply=None):
    window=tk.Toplevel(root);window.title('Web連携について');window.geometry('600x290')
    frame=ttk.Frame(window,padding=20);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='NG設定のWeb同期',font=('',12,'bold')).pack(anchor='w')
    ttk.Label(frame,text=NG_LIMITATION,wraplength=550).pack(anchor='w',pady=(6,18))
    ttk.Label(frame,text='ギフト演出',font=('',12,'bold')).pack(anchor='w')
    ttk.Label(frame,text=GIFT_LIMITATION,wraplength=550).pack(anchor='w',pady=6)
    ttk.Button(frame,text='閉じる',command=window.destroy).pack(anchor='e',pady=10)
    return window
