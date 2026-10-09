"""Windows integrations, activated only by explicit shell calls."""
import ctypes
from ctypes import wintypes
from datetime import datetime
import io
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import wave


def in_quiet_hours(settings,now=None):
    if not settings.get('quiet_enabled',False): return False
    now=now or datetime.now()
    def minute(value):
        h,m=map(int,value.split(':')); return h*60+m
    try: start=minute(settings.get('quiet_start','22:00')); end=minute(settings.get('quiet_end','07:00'))
    except (ValueError,TypeError,AttributeError): return False
    current=now.hour*60+now.minute
    return start<=current<end if start<end else (current>=start or current<end) if start>end else False


def scaled_wav(path,volume):
    path=Path(path)
    if path.stat().st_size>10*1024*1024: raise ValueError('WAVは10MB以内にしてください')
    with wave.open(str(path),'rb') as source:
        params=source.getparams()
        if params.sampwidth not in (1,2,3,4) or params.nframes/params.framerate>60: raise ValueError('60秒以内のPCM WAVを指定してください')
        frames=source.readframes(params.nframes)
    gain=max(0,min(100,int(volume)))/100
    width=params.sampwidth
    data=bytearray(len(frames))
    for pos in range(0,len(frames),width):
        n=int.from_bytes(frames[pos:pos+width],'little',signed=width>1)
        if width==1: n-=128
        n=round(n*gain)
        if width==1: n+=128
        data[pos:pos+width]=n.to_bytes(width,'little',signed=width>1)
    output=io.BytesIO()
    with wave.open(output,'wb') as dest:
        dest.setparams(params); dest.writeframes(data)
    return output.getvalue()

class NotificationSound:
    def __init__(self): self.last_event=None; self._lock=threading.Lock()
    def notify(self,kind,settings,now=None):
        if kind not in ('play','record','both') or in_quiet_hours(settings,now): return False
        path=settings.get('sound_record' if kind in ('record','both') else 'sound_play','')
        if not path or not Path(path).is_file() or settings.get('sound_volume',80)<=0: return False
        with self._lock:
            current=time.monotonic()
            if self.last_event is not None and current-self.last_event<float(settings.get('notification_group_seconds',5)): return False
            try: data=scaled_wav(path,settings.get('sound_volume',80))
            except (OSError,ValueError,wave.Error,EOFError): return False
            if sys.platform!='win32': return False
            self.last_event=current
        def play():
            import winsound
            try: winsound.PlaySound(data,winsound.SND_MEMORY|winsound.SND_NODEFAULT)
            except RuntimeError: pass
        threading.Thread(target=play,daemon=True,name='notification-sound').start()
        return True

class SleepInhibitor:
    def __init__(self): self.active=False
    def set_active(self,active):
        if sys.platform!='win32': return False
        flags=0x80000000 | (0x00000001 if active else 0)
        if not ctypes.windll.kernel32.SetThreadExecutionState(flags): raise OSError('スリープ抑制を変更できません')
        self.active=bool(active); return True
    def close(self):
        if self.active: self.set_active(False)

def set_startup(enabled,command=None):
    if sys.platform!='win32': raise OSError('Windows専用です')
    import winreg
    if enabled and command is None:
        command=subprocess.list2cmdline([sys.executable] if getattr(sys,'frozen',False) else [sys.executable,'-m','nicoview'])
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        if enabled: winreg.SetValueEx(key,'NicoLiveViewer',0,winreg.REG_SZ,command)
        else:
            try: winreg.DeleteValue(key,'NicoLiveViewer')
            except FileNotFoundError: pass

class TrayIcon:
    """Native shell icon. Call start/stop on Tk's thread; callbacks run there.

    Uses the existing Tk HWND, polling callback messages by subclassing its
    window procedure. No persistent process or global user settings.
    """
    def __init__(self,on_show,on_quit,root=None):
        self.on_show=on_show; self.on_quit=on_quit; self.root=root
        self.active=False; self._old=None; self._proc=None
        self._owned_icon=None
    def start(self):
        if self.active: return True
        if sys.platform!='win32': return False
        import tkinter
        root=self.root or tkinter._default_root
        if root is None: raise RuntimeError('Tray requires a Tk root')
        root.update_idletasks()
        user=ctypes.windll.user32
        user.GetParent.argtypes=[wintypes.HWND]; user.GetParent.restype=wintypes.HWND
        self.hwnd=user.GetParent(root.winfo_id()) or root.winfo_id()
        class NOTIFYICONDATA(ctypes.Structure):
            _fields_=[('cbSize',wintypes.DWORD),('hWnd',wintypes.HWND),('uID',wintypes.UINT),('uFlags',wintypes.UINT),('uCallbackMessage',wintypes.UINT),('hIcon',wintypes.HICON),('szTip',wintypes.WCHAR*128),('dwState',wintypes.DWORD),('dwStateMask',wintypes.DWORD),('szInfo',wintypes.WCHAR*256),('uTimeoutOrVersion',wintypes.UINT),('szInfoTitle',wintypes.WCHAR*64),('dwInfoFlags',wintypes.DWORD),('guidItem',ctypes.c_byte*16),('hBalloonIcon',wintypes.HICON)]
        self.data=NOTIFYICONDATA(); self.data.cbSize=ctypes.sizeof(self.data); self.data.hWnd=self.hwnd
        self.data.uID=1; self.data.uFlags=7; self.data.uCallbackMessage=0x8000+81
        user.LoadIconW.argtypes=[wintypes.HINSTANCE,ctypes.c_void_p]; user.LoadIconW.restype=wintypes.HICON
        self.data.hIcon=user.LoadIconW(None,ctypes.c_void_p(32512)); self.data.szTip='NicoLive Studio — 左クリックで表示 / 右クリックで終了'
        from .playback import asset_path
        icon=asset_path('studio.ico')
        if icon.is_file():
            user.LoadImageW.argtypes=[wintypes.HINSTANCE,wintypes.LPCWSTR,wintypes.UINT,ctypes.c_int,ctypes.c_int,wintypes.UINT]
            user.LoadImageW.restype=wintypes.HANDLE
            self._owned_icon=user.LoadImageW(None,str(icon),1,32,32,0x10)
            if self._owned_icon:self.data.hIcon=self._owned_icon
        PROC=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM)
        user.CallWindowProcW.argtypes=[ctypes.c_void_p,wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]; user.CallWindowProcW.restype=ctypes.c_ssize_t
        setter=user.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p)==8 else user.SetWindowLongW
        setter.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_void_p]; setter.restype=ctypes.c_void_p
        self._setter=setter
        def callback(hwnd,msg,wparam,lparam):
            if msg==self.data.uCallbackMessage:
                if lparam==0x0202: root.after_idle(self.on_show)
                elif lparam==0x0205: root.after_idle(self.on_quit)
                return 0
            return user.CallWindowProcW(self._old,hwnd,msg,wparam,lparam)
        self._proc=PROC(callback); self._old=setter(self.hwnd,-4,ctypes.cast(self._proc,ctypes.c_void_p))
        if not self._old:
            self._release_icon();raise OSError('通知領域の操作を登録できません')
        if not ctypes.windll.shell32.Shell_NotifyIconW(0,ctypes.byref(self.data)):
            setter(self.hwnd,-4,self._old);self._release_icon();raise OSError('トレイアイコンを作成できません')
        self.active=True; return True
    def stop(self):
        if self.active:
            ctypes.windll.shell32.Shell_NotifyIconW(2,ctypes.byref(self.data))
            self._setter(self.hwnd,-4,self._old); self.active=False
        self._release_icon()
    def _release_icon(self):
        if self._owned_icon:
            ctypes.windll.user32.DestroyIcon.argtypes=[wintypes.HICON]
            ctypes.windll.user32.DestroyIcon(self._owned_icon);self._owned_icon=None
