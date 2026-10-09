"""Interactive login in a new, app-owned Edge profile. No personal profiles read."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
import requests
import websocket
from .auth import Cancelled
from .sessions import clean_cookies
from .windows_job import Job

LOGIN_URL='https://account.nicovideo.jp/login'

class BrowserLoginError(ValueError):
    """Safe, user-facing messages only; never include browser data."""

def find_edge():
    for root in (os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'),
                 os.environ.get('ProgramFiles','C:/Program Files')):
        p=Path(root)/'Microsoft/Edge/Application/msedge.exe'
        if p.is_file():return str(p)
    raise BrowserLoginError('Microsoft Edgeが見つかりません。Edgeをインストールしてください。')

class BrowserLogin:
    def __init__(self,stop):
        self.stop=stop;self.proc=None;self.job=None;self.profile=None;self.http=None

    def prepare_profile(self):
        if self.profile is not None:return
        self.profile=Path(tempfile.mkdtemp(prefix='nicoview-login-')).resolve()
        default=self.profile/'Default';default.mkdir()
        (default/'Preferences').write_text(json.dumps({'credentials_enable_service':False,
                         'session':{'restore_on_startup':1},
                         'profile':{'password_manager_enabled':False}}),encoding='utf-8')

    def is_running(self):
        # Edge can replace its launcher and exit with code 0 before the actual
        # browser is ready. Only our job's descendants count, never other Edge windows.
        if self.proc and self.proc.poll() is None:return True
        return bool(self.job and self.job.active_process_count > 0)

    def open(self,url=LOGIN_URL,headless=False,debugging=True):
        if self.stop.is_set():raise Cancelled()
        self.prepare_profile()
        args=[find_edge(),'--user-data-dir='+str(self.profile),
              '--no-first-run','--no-default-browser-check',
              '--disable-sync','--disable-background-mode','--new-window',url]
        if debugging:
            # No debugging connection is available during interactive authentication.
            (self.profile/'DevToolsActivePort').unlink(missing_ok=True)
            args[1:1]=['--remote-debugging-port=0','--remote-debugging-address=127.0.0.1']
        if headless:args.insert(1,'--headless=new')
        try:
            self.proc=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.job=Job(self.proc)
        except OSError:
            raise BrowserLoginError('ログイン用Edgeを起動・管理できませんでした。Edgeを更新して再試行してください。') from None
        if not debugging:return
        self.http=requests.Session();self.http.trust_env=False
        deadline=time.monotonic()+25
        while time.monotonic()<deadline and not self.stop.is_set():
            if not self.is_running():
                raise BrowserLoginError('Edgeが終了したためログイン状態を引き継げませんでした。')
            try:
                lines=(self.profile/'DevToolsActivePort').read_text().splitlines()
                port=int(lines[0])
                if 0<port<65536:
                    self.base=f'http://127.0.0.1:{port}'
                    return
            except (OSError,ValueError,IndexError):pass
            self.stop.wait(.1)
        if self.stop.is_set():raise Cancelled()
        raise BrowserLoginError('ログイン状態の引き継ぎ用Edgeに接続できませんでした。')

    def cookies(self):
        response=self.http.get(self.base+'/json/list',timeout=2);response.raise_for_status()
        for tab in response.json():
            if tab.get('type')!='page':continue
            address=tab.get('webSocketDebuggerUrl','')
            parsed=urlsplit(address)
            if parsed.scheme!='ws' or parsed.hostname not in ('127.0.0.1','localhost') or parsed.port!=urlsplit(self.base).port:continue
            ws=websocket.create_connection(address,timeout=2,suppress_origin=True,http_no_proxy=['127.0.0.1','localhost'])
            try:
                ws.send(json.dumps({'id':1,'method':'Network.getCookies','params':{
                    'urls':['https://live.nicovideo.jp/','https://www.nicovideo.jp/']}}))
                for _ in range(32):
                    packet=json.loads(ws.recv())
                    if packet.get('id')==1:return clean_cookies(packet.get('result',{}).get('cookies',[]))
            finally:ws.close()
        return []

    def run(self):
        try:
            if self.stop.is_set():raise Cancelled()
            self.open(debugging=False)
            deadline=time.monotonic()+600
            while self.is_running():
                if self.stop.wait(.2):raise Cancelled()
                if time.monotonic()>=deadline:
                    raise BrowserLoginError('ログイン待ちが10分を超えました。もう一度ログインしてください。')
            if self.stop.is_set():raise Cancelled()
            if self.proc.returncode!=0:
                raise BrowserLoginError('ログイン用Edgeが正常に終了しませんでした。もう一度お試しください。')
            # Normal exit flushes the app-owned encrypted cookie store before restart.
            if self.job:self.job.close();self.job=None
            self.proc=None
            self.open('about:blank',headless=True)
            deadline=time.monotonic()+15
            while not self.stop.is_set() and time.monotonic()<deadline:
                if not self.is_running():
                    raise BrowserLoginError('ログイン状態の読み取り中にEdgeが終了しました。')
                try:
                    cookies=self.cookies()
                    if any(c['name']=='user_session' for c in cookies):return cookies
                except (requests.RequestException,websocket.WebSocketException,OSError,ValueError):pass
                self.stop.wait(.5)
            if self.stop.is_set():raise Cancelled()
            raise BrowserLoginError('ログイン状態を取得できませんでした。専用Edgeでログイン完了後、ログアウトせずにウィンドウを閉じてください。')
        finally:self.close()

    def close(self):
        if self.job:self.job.close();self.job=None
        if self.proc:
            if self.proc.poll() is None:self.proc.terminate()
            try:self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=3)
        if self.http:self.http.close()
        # Only the exact fresh directory allocated above is eligible for cleanup.
        if self.profile and self.profile.parent==Path(tempfile.gettempdir()).resolve() and self.profile.name.startswith('nicoview-login-'):
            for _ in range(5):
                try:shutil.rmtree(self.profile);break
                except FileNotFoundError:break
                except OSError:time.sleep(.2)
