"""Short native integration check; never changes startup or plays sound."""
import json
import ctypes
import tkinter as tk
from nicoview.desktop import TrayIcon,SleepInhibitor

root=tk.Tk();root.withdraw()
tray=TrayIcon(lambda:None,lambda:None,root);sleep=SleepInhibitor()
result={}
try:
    if ctypes.windll.user32.FindWindowW('Shell_TrayWnd',None):
        result['tray_start']=tray.start()
    else:result['tray_check']='unavailable: test desktop has no Windows taskbar'
    root.update()
    result['sleep_inhibit']=sleep.set_active(True)
finally:
    tray.stop();sleep.close();root.destroy()
print(json.dumps(result))
