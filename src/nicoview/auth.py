import logging
import queue
import threading
import time


class Cancelled(Exception):
    pass


class CodePrompt:
    def __init__(self, notify, stop: threading.Event, timeout=180):
        self.notify, self.stop, self.timeout = notify, stop, timeout
        self.answers = queue.Queue(maxsize=1)

    def submit(self, code):
        try: self.answers.put_nowait(code)
        except queue.Full: pass

    def ask(self):
        if self.stop.is_set(): raise Cancelled()
        self.notify()
        deadline = time.monotonic() + self.timeout
        while not self.stop.is_set() and time.monotonic() < deadline:
            try:
                answer = self.answers.get(timeout=min(.1,max(.001,deadline-time.monotonic())))
                if answer is None: raise Cancelled()
                if not isinstance(answer,str) or len(answer)!=6 or not answer.isascii() or not answer.isdigit():
                    raise ValueError('認証コードは6桁の数字です。')
                return answer
            except queue.Empty: pass
        raise Cancelled()


class _Requester:
    def __init__(self, callback): self.callback = callback
    def ask(self, prompt): return self.callback()
    def ask_password(self, prompt): return self.callback()


class AuthSession:
    def __init__(self, session): self.session = session

    @classmethod
    def create(cls):
        from streamlink import Streamlink
        # Upstream debug output can contain cookies. Never attach a logger here.
        logging.getLogger('streamlink').setLevel(logging.CRITICAL)
        return cls(Streamlink({'no-plugin-cache': True, 'http-timeout': 15,
                               'stream-timeout': 15, 'stream-segment-timeout': 10}))

    def login(self, email, password, request_code, *, plugin=None):
        from streamlink.plugins.nicolive import NicoLive
        from streamlink.options import Options
        self.session.http.cookies.clear()
        self.session.set_option('user-input-requester', _Requester(request_code))
        if plugin is None:
            plugin = NicoLive(self.session, 'https://live.nicovideo.jp/watch/lv0', options=Options())
        plugin.options['email'], plugin.options['password'] = email, password
        try:
            plugin.niconico_web_login()
            return any(c.name=='user_session' and c.value and not c.is_expired()
                       for c in self.session.http.cookies)
        finally:
            plugin.options['password'] = None
            plugin.options['email'] = None
            self.session.set_option('user-input-requester', None)

    def clear(self):
        self.session.http.cookies.clear()
        self.session.set_option('user-input-requester', None)

    def restore(self,cookies):
        from .sessions import clean_cookies
        self.clear()
        for c in clean_cookies(cookies):
            self.session.http.cookies.set(c['name'],c['value'],domain=c['domain'],path=c['path'],
                                          secure=True,expires=c['expires'])
        return any(c.name=='user_session' for c in self.session.http.cookies)

    def close(self):
        self.clear()
        self.session.http.close()
