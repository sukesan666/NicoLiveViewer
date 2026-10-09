"""Private loopback HLS transport. Authentication stays inside the worker."""
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urljoin,urlsplit

class MediaProxy:
    def __init__(self,session,stream,wsclient=None):
        self.session=session;self.wsclient=wsclient;self.routes={};self.lock=threading.Lock();self.closed=False
        streams=list(getattr(stream,'substreams',[]) or [stream])
        self.origins={urlsplit(s.url).hostname for s in streams}
        for s in streams:self._validate(s.url)
        proxy=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):proxy._serve(self)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.base=f'http://127.0.0.1:{self.server.server_port}/'
        video=self.register(streams[0].url)
        if len(streams)>1:
            audio=self.register(streams[1].url)
            body=f'#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="audio",DEFAULT=YES,AUTOSELECT=YES,URI="{audio}"\n#EXT-X-STREAM-INF:BANDWIDTH=12000000,AUDIO="audio"\n{video}\n'
            key=uuid.uuid4().hex+'.m3u8';self.routes['/'+key]=body.encode();self.url=self.base+key
        else:self.url=video
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        if wsclient is not None:wsclient.opened.set()

    def _validate(self,url):
        p=urlsplit(url)
        if p.scheme!='https' or p.username or p.password or p.port not in (None,443):raise ValueError('Invalid media URL')
        host=p.hostname or ''
        if host not in self.origins and not any(host.endswith('.'+s) for s in ('nicovideo.jp','dmc.nico','domand.nicovideo.jp')):raise ValueError('Invalid media host')

    def register(self,url):
        self._validate(url)
        with self.lock:
            # Reuse routes so a long broadcast cannot grow this registry per reload.
            for key,value in self.routes.items():
                if value==url:return self.base+key[1:]
            if len(self.routes)>200000:raise ValueError('Media playlist too large')
            key='/'+uuid.uuid4().hex+('.m3u8' if '.m3u8' in urlsplit(url).path else '')
            self.routes[key]=url
            return self.base+key[1:]

    def rewrite(self,text,url):
        out=[]
        for line in text.splitlines():
            if line and not line.startswith('#'):line=self.register(urljoin(url,line.strip()))
            elif 'URI="' in line:
                line=re.sub(r'URI="([^"]+)"',lambda m:'URI="'+self.register(urljoin(url,m.group(1)))+'"',line)
            out.append(line)
        return ('\n'.join(out)+'\n').encode()

    def _serve(self,handler):
        route=self.routes.get(handler.path)
        if route is None or self.closed:handler.send_error(404);return
        response=None
        try:
            if isinstance(route,bytes):data=route;content='application/vnd.apple.mpegurl';status=200
            else:
                headers={}
                if handler.headers.get('Range'):headers['Range']=handler.headers['Range'][:100]
                response=self.session.http.get(route,headers=headers,timeout=(8,30),stream=True,allow_redirects=False,raise_for_status=False)
                if response.status_code==403 and self.wsclient is not None:
                    response.close();self.wsclient.reconnect()
                    self.wsclient.reconnect_done.wait(6);self.wsclient.ready.wait(6)
                    response=self.session.http.get(route,headers=headers,timeout=(8,30),stream=True,allow_redirects=False,raise_for_status=False)
                if response.status_code not in (200,206):handler.send_error(502);return
                content=response.headers.get('Content-Type','application/octet-stream');status=response.status_code
                if '.m3u8' in urlsplit(route).path or 'mpegurl' in content.lower():
                    chunks=[];size=0
                    for chunk in response.iter_content(65536):
                        size+=len(chunk)
                        if size>4*1024*1024:raise ValueError('Playlist too large')
                        chunks.append(chunk)
                    data=b''.join(chunks)
                    data=self.rewrite(data.decode('utf-8-sig'),route)
                else:data=None
            handler.send_response(status);handler.send_header('Content-Type',content)
            handler.send_header('Cache-Control','no-store')
            if data is not None:handler.send_header('Content-Length',str(len(data)))
            elif response.headers.get('Content-Length'):handler.send_header('Content-Length',response.headers['Content-Length'])
            if response is not None and response.headers.get('Content-Range'):handler.send_header('Content-Range',response.headers['Content-Range'])
            handler.end_headers()
            if data is not None:handler.wfile.write(data)
            else:
                for chunk in response.iter_content(65536):
                    if self.closed:break
                    handler.wfile.write(chunk)
        except Exception:
            # URLs, query tokens and response text must never leave this private transport.
            try:handler.send_error(502)
            except OSError:pass
        finally:
            if response is not None:response.close()

    def close(self):
        self.closed=True;self.server.shutdown();self.server.server_close();self.routes.clear()
