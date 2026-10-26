"""Application-owned menus and Windows title-bar appearance."""
import os
import tkinter as tk
from tkinter import ttk
from .layout import ScrollPage,work_area


def title_theme(window,dark):
    if os.name!='nt':return
    try:
        import ctypes
        from ctypes import wintypes
        user=ctypes.windll.user32
        user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];user.GetAncestor.restype=wintypes.HWND
        handle=user.GetAncestor(window.winfo_id(),2)
        set_attribute=ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attribute.argtypes=[wintypes.HWND,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD]
        for attribute,value in ((20,int(dark)),(35,0x171717 if dark else 0xFFFFFFFF),(36,0xFFFFFF if dark else 0xFFFFFFFF)):
            data=wintypes.DWORD(value);set_attribute(handle,attribute,ctypes.byref(data),ctypes.sizeof(data))
    except (OSError,AttributeError,tk.TclError):pass


def theme_widgets(window):
    dark=getattr(window._root(),'_dark_mode',False)
    bg='#212121' if dark else '#f4f6fa';fg='#ffffff' if dark else '#243149'
    def visit(widget):
        if isinstance(widget,(tk.Tk,tk.Toplevel)):
            widget.configure(bg=bg);title_theme(widget,dark)
        elif isinstance(widget,tk.Canvas):widget.configure(bg=bg)
        elif isinstance(widget,tk.Text):
            if not hasattr(widget,'_light_edge'):widget._light_edge=(widget.cget('highlightbackground'),widget.cget('highlightcolor'),widget.cget('relief'))
            widget.configure(bg='#303030' if dark else '#ffffff',fg=fg,insertbackground=fg,selectbackground='#505050',selectforeground='#ffffff')
            widget.configure(highlightbackground='#505050',highlightcolor='#505050',relief='flat') if dark else widget.configure(highlightbackground=widget._light_edge[0],highlightcolor=widget._light_edge[1],relief=widget._light_edge[2])
        elif isinstance(widget,(ttk.Label,ttk.LabelFrame)):widget.configure(foreground=fg) if isinstance(widget,ttk.Label) else None
        for child in widget.winfo_children():visit(child)
    visit(window)


class MenuBar(ttk.Frame):
    def __init__(self,root):
        super().__init__(root,style='MenuBar.TFrame')
        self.pack(fill='x');self.popup=None;self.root=root

    def add(self,name,items):
        button=ttk.Button(self,text=name,style='Menu.TButton')
        button.configure(command=lambda:self.open(button,items));button.pack(side='left')

    def close(self):
        if self.popup and self.popup.winfo_exists():
            self.popup.grab_release();self.popup.destroy()
        self.popup=None

    def open(self,anchor,items):
        self.close();pop=tk.Toplevel(self.root);self.popup=pop
        pop.withdraw();pop.overrideredirect(True)
        pane=ScrollPage(pop);pane.pack(fill='both',expand=True)
        buttons=[]
        for label,command in items:
            if label is None:ttk.Separator(pane.inner).pack(fill='x',pady=3)
            else:
                def invoke(action=command):self.close();action()
                b=ttk.Button(pane.inner,text=label,command=invoke,style='Menu.TButton');b.pack(fill='x');buttons.append(b)
        pop.update_idletasks();left,top,right,bottom=work_area(pop)
        width=min(pop.winfo_reqwidth(),right-left);height=min(pop.winfo_reqheight(),bottom-top)
        x=min(max(left,anchor.winfo_rootx()),right-width);y=min(max(top,anchor.winfo_rooty()+anchor.winfo_height()),bottom-height)
        pop.geometry(f'{width}x{height}+{x}+{y}');pop.deiconify();pop.lift();pop.grab_set()
        if buttons:buttons[0].focus_set()
        def outside(e):
            if not (pop.winfo_rootx()<=e.x_root<pop.winfo_rootx()+pop.winfo_width() and pop.winfo_rooty()<=e.y_root<pop.winfo_rooty()+pop.winfo_height()):self.close()
        pop.bind('<ButtonPress-1>',outside,add='+')
        pop.bind('<Escape>',lambda e:self.close())
        def lost_focus():
            if self.popup is pop and pop.winfo_exists():
                focused=pop.focus_displayof()
                if focused is None or focused.winfo_toplevel() is not pop:self.close()
        pop.bind('<FocusOut>',lambda e:pop.after_idle(lost_focus),add='+')
        for i,b in enumerate(buttons):
            b.bind('<Down>',lambda e,index=i:buttons[(index+1)%len(buttons)].focus_set())
            b.bind('<Up>',lambda e,index=i:buttons[(index-1)%len(buttons)].focus_set())
