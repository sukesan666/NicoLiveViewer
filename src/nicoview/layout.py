"""Screen-bounded dialog sizing and scrollable form pages."""
import os
import tkinter as tk
from tkinter import ttk


def initialize_zoom(root,percent):
    from tkinter import font
    if not hasattr(root,'_native_scaling'):root._native_scaling=float(root.tk.call('tk','scaling'))
    root._ui_zoom=percent/100
    root.tk.call('tk','scaling',root._native_scaling*root._ui_zoom)
    for name in font.names(root):
        item=font.nametofont(name,root=root)
        item.configure(size=item.cget('size'))


def zoom_factor(widget):
    return getattr(widget._root(),'_ui_zoom',1)


def zoom_container(window,always=False):
    if not always and zoom_factor(window)==1:return window
    pane=ScrollPage(window);pane.pack(fill='both',expand=True)
    return pane.inner


def work_area(window,rectangle=None):
    area=(0,0,window.winfo_screenwidth(),window.winfo_screenheight())
    if os.name=='nt':
        try:
            import ctypes
            from ctypes import wintypes
            class MonitorInfo(ctypes.Structure):
                _fields_=[('size',wintypes.DWORD),('monitor',wintypes.RECT),('work',wintypes.RECT),('flags',wintypes.DWORD)]
            user=ctypes.windll.user32
            user.MonitorFromWindow.argtypes=[wintypes.HWND,wintypes.DWORD]
            user.MonitorFromWindow.restype=wintypes.HANDLE
            user.GetMonitorInfoW.argtypes=[wintypes.HANDLE,ctypes.POINTER(MonitorInfo)]
            owner=window.master or window
            if rectangle:
                user.MonitorFromRect.argtypes=[ctypes.POINTER(wintypes.RECT),wintypes.DWORD]
                user.MonitorFromRect.restype=wintypes.HANDLE
                rect=wintypes.RECT(*rectangle);handle=user.MonitorFromRect(ctypes.byref(rect),2)
            else:handle=user.MonitorFromWindow(owner.winfo_id(),2)
            info=MonitorInfo();info.size=ctypes.sizeof(info)
            if user.GetMonitorInfoW(handle,ctypes.byref(info)):
                r=info.work;area=(r.left,r.top,r.right,r.bottom)
        except (OSError,AttributeError):pass
    return area


def fit_window(window,width=0,height=0,flex=None):
    if getattr(window,'_geometry_tracking',False):return
    window.update_idletasks()
    left,top,right,bottom=work_area(window)
    scale=max(1,getattr(window._root(),'_native_scaling',float(window.tk.call('tk','scaling')))/(96/72))
    maxw=max(1,right-left-int(24*scale));maxh=max(1,bottom-top-int(80*scale))
    w=min(max(width,window.winfo_reqwidth()+16),maxw)
    h=min(max(height,window.winfo_reqheight()+16),maxh)
    minh=h if flex is None else min(h,max(240,window.winfo_reqheight()-flex.winfo_reqheight()+100))
    if window is not window._root() and any(isinstance(child,ScrollPage) for child in window.winfo_children()):
        window.minsize(min(w,360),min(h,260))
    else:window.minsize(min(w,window.winfo_reqwidth()),minh)
    window.geometry(f'{w}x{h}+{left+max(0,(right-left-w)//2)}+{top+int(12*scale)}')
    memory=getattr(window._root(),'_window_memory',None)
    if memory:memory.track(window)


def frame_size(window):
    """Actual non-client size, rather than first-run presentation margins."""
    if os.name=='nt':
        try:
            import ctypes
            from ctypes import wintypes
            user=ctypes.windll.user32
            user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];user.GetAncestor.restype=wintypes.HWND
            user.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
            user.GetClientRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
            handle=user.GetAncestor(window.winfo_id(),2)
            outer=wintypes.RECT();inner=wintypes.RECT()
            if user.GetWindowRect(handle,ctypes.byref(outer)) and user.GetClientRect(handle,ctypes.byref(inner)):
                return max(0,outer.right-outer.left-inner.right+inner.left),max(0,outer.bottom-outer.top-inner.bottom+inner.top)
        except (OSError,AttributeError):pass
    return 16,40


class AutoScrollbar(ttk.Scrollbar):
    """A grid-managed scrollbar that takes space only while content overflows."""
    def set(self,first,last):
        super().set(first,last)
        if float(first)<=0 and float(last)>=1:
            if self.winfo_manager():self.grid_remove()
        elif not self.winfo_manager():self.grid()


class ScrollableTree(ttk.Frame):
    """A flexible list with its optional scrollbar kept beside the viewport."""
    def __init__(self,master,**options):
        super().__init__(master)
        self.columnconfigure(0,weight=1);self.rowconfigure(0,weight=1)
        options.setdefault('height',1)
        self.tree=ttk.Treeview(self,**options)
        self.scrollbar=AutoScrollbar(self,orient='vertical',command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.scrollbar.set)
        self.tree.grid(row=0,column=0,sticky='nsew')
        self.scrollbar.grid(row=0,column=1,sticky='ns')
        self.scrollbar.grid_remove()


class ScrollPage(ttk.Frame):
    def __init__(self,master):
        super().__init__(master)
        self.columnconfigure(0,weight=1);self.rowconfigure(0,weight=1)
        self.canvas=tk.Canvas(self,highlightthickness=0)
        self.canvas.grid(row=0,column=0,sticky='nsew')
        self.inner=ttk.Frame(self.canvas,padding=16)
        self.item=self.canvas.create_window(0,0,window=self.inner,anchor='nw')
        self.inner.bind('<Configure>',self.refresh)
        self.canvas.bind('<Configure>',self.refresh)
        self.winfo_toplevel().bind('<MouseWheel>',self.wheel,add='+')
        self.winfo_toplevel().bind('<FocusIn>',self.reveal,add='+')

    def refresh(self,event=None):
        width=self.inner.winfo_reqwidth();height=self.inner.winfo_reqheight()
        self.canvas.configure(width=width,height=height,bg=ttk.Style(self).lookup('TFrame','background') or '#ffffff')
        self.canvas.itemconfigure(self.item,width=max(width,self.canvas.winfo_width()),height=max(height,self.canvas.winfo_height()))
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def owns(self,widget):
        return str(widget).startswith(str(self.inner)+'.')

    def wheel(self,event):
        if self.winfo_ismapped() and self.owns(event.widget) and not isinstance(event.widget,(tk.Text,ttk.Combobox)):
            self.canvas.yview_scroll(-int(event.delta/120),'units');return 'break'

    def reveal(self,event):
        if not self.winfo_ismapped() or not self.owns(event.widget):return
        self.update_idletasks()
        y=event.widget.winfo_rooty()-self.inner.winfo_rooty()
        h=event.widget.winfo_height();top=self.canvas.canvasy(0);height=self.canvas.winfo_height()
        total=max(1,self.inner.winfo_reqheight())
        if y<top:self.canvas.yview_moveto(y/total)
        elif y+h>top+height:self.canvas.yview_moveto((y+h-height)/total)

        x=event.widget.winfo_rootx()-self.inner.winfo_rootx()
        w=event.widget.winfo_width();left=self.canvas.canvasx(0);width=self.canvas.winfo_width()
        total_width=max(1,self.inner.winfo_width())
        if x<left:self.canvas.xview_moveto(x/total_width)
        elif x+w>left+width:self.canvas.xview_moveto((x+w-width)/total_width)


class WindowMemory:
    """Keep normal placement per app window; never store minimized dimensions."""
    def __init__(self,root,path):
        import json
        self.root=root;self.path=path;self.records={};self.pending=None
        try:
            if path.stat().st_size<100000:
                data=json.loads(path.read_text(encoding='utf-8'))
                for key,value in list(data.items())[:30]:
                    if isinstance(key,str) and isinstance(value,dict) and all(type(value.get(k)) is int and abs(value[k])<100000 for k in ('width','height','x','y')) and value['width']>0 and value['height']>0:
                        self.records[key[:100]]=dict(value,maximized=value.get('maximized') is True)
        except (OSError,ValueError,AttributeError,TypeError):pass

    def track(self,window):
        if getattr(window,'_geometry_tracking',False):return
        key=window.title()[:100];saved=self.records.get(key)
        if saved:
            window.update_idletasks()
            x,y=saved['x'],saved['y']
            left,top,right,bottom=work_area(window,(x,y,x+saved['width'],y+saved['height']))
            framew,frameh=frame_size(window)
            maxw=max(1,right-left-framew);maxh=max(1,bottom-top-frameh)
            minw,minh=window.minsize();minw=min(minw,maxw,max(300,saved['width']));minh=min(minh,maxh,max(200,saved['height']))
            window.minsize(minw,minh)
            width=min(maxw,max(minw,saved['width']));height=min(maxh,max(minh,saved['height']))
            x=max(left,min(x,right-width-framew));y=max(top,min(y,bottom-height-frameh))
            window.geometry(f'{width}x{height}')
            window.update_idletasks()
            if os.name=='nt':
                import ctypes
                from ctypes import wintypes
                user=ctypes.windll.user32
                user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];user.GetAncestor.restype=wintypes.HWND
                user.SetWindowPos.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT]
                user.SetWindowPos(user.GetAncestor(window.winfo_id(),2),None,x,y,0,0,0x15)
            else:window.geometry(f'{width}x{height}+{x}+{y}')
            if saved.get('maximized'):
                try:window.state('zoomed')
                except tk.TclError:pass
        window._geometry_tracking=True;window._placement_key=key
        previous=window.protocol('WM_DELETE_WINDOW')
        def close():
            self.capture(window);self.flush()
            if previous:window.tk.call(previous)
            else:window.destroy()
        window.protocol('WM_DELETE_WINDOW',close)
        window.bind('<Configure>',lambda e:self.capture(window) if e.widget is window else None,add='+')
        window.bind('<Destroy>',lambda e:self.flush() if e.widget is window else None,add='+')

    def capture(self,window):
        try:
            state=window.state();key=window._placement_key
            if state not in ('normal','zoomed') or not window.winfo_ismapped():return
            if state=='normal':
                width,height=window.winfo_width(),window.winfo_height()
                if width<100 or height<100:return
                self.records[key]={'width':width,'height':height,'x':window.winfo_x(),'y':window.winfo_y(),'maximized':False}
            elif key in self.records:self.records[key]['maximized']=True
            if self.pending:self.root.after_cancel(self.pending)
            self.pending=self.root.after(700,self.flush)
        except tk.TclError:pass

    def capture_all(self):
        def visit(window):
            if getattr(window,'_geometry_tracking',False):self.capture(window)
            for child in window.winfo_children():
                if isinstance(child,tk.Toplevel):visit(child)
        visit(self.root)

    def flush(self):
        from .preferences import atomic_json
        if self.pending:
            try:self.root.after_cancel(self.pending)
            except tk.TclError:pass
            self.pending=None
        try:atomic_json(self.path,self.records)
        except OSError:pass
