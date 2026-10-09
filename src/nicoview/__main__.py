import os
import sys

def main():
    if '--worker' in sys.argv:
        for stream in (sys.stdin,sys.stdout):
            if stream:stream.reconfigure(encoding='utf-8')
        from nicoview.worker import main as worker_main
        worker_main()
    elif '--self-test' in sys.argv:
        import json
        from nicoview.auth import AuthSession
        from nicoview.playback import asset_path
        from nicoview.url import normalize_url
        a=AuthSession.create()
        result={'url':normalize_url('https://live.nicovideo.jp/watch/lv1'),
                'memory_only':a.session.get_option('no-plugin-cache'),
                'lua':asset_path('comments.lua').is_file(),'preview':asset_path('preview.mp4').is_file()}
        import tkinter as tk
        import tempfile
        from nicoview.studio_ui import StudioApp
        from nicoview.preferences import PreferenceStore
        with tempfile.TemporaryDirectory() as directory:
            root=tk.Tk();root.withdraw();app=StudioApp(root,PreferenceStore(directory),background=False);root.update_idletasks()
            result['gui']=len(app.account.fonts)>0
            app.shutdown()
        a.close();print(json.dumps(result));sys.exit(0 if all(result.values()) else 1)
    else:
        if os.name=='nt' and getattr(sys,'frozen',False):
            import ctypes
            ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(),0)
        from nicoview.studio_ui import main as ui_main
        ui_main()

if __name__=='__main__':main()
