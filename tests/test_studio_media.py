import html
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from nicoview.comments import Comment, decode_comment
from nicoview.controller import Controller
from nicoview.worker import Worker
from test_comments import field, vi

class StudioMediaTests(unittest.TestCase):
    def test_controller_modes_and_controls(self):
        c=Controller();sent=[]
        with patch.object(c,'_ensure'),patch.object(c,'_send',side_effect=sent.append):
            c.start_job('', '', None,mode='play',local_path='C:/movie.mp4',start_time=5)
            c.control('seek',9)
        self.assertEqual(sent[0]['local_path'],'C:/movie.mp4')
        self.assertEqual(sent[-1]['action'],'seek')
        with self.assertRaises(ValueError):c.control('seek',float('nan'))

    def test_ndgr_identity_and_vpos(self):
        raw=field(2,field(1,field(1,b'hello')+bytes([24])+vi(1234)+field(6,b'userhash')))
        c=decode_comment(raw)
        self.assertEqual(c.user_id,'userhash');self.assertEqual(c.position,12.34)

    def test_timeline_pause_seek_offset_and_ng(self):
        from nicoview.timeline import Timeline, allowed_comment
        c=Comment('1','hello',0,user_id='blocked',position=5)
        t=Timeline([c,Comment('2','ok',0,position=7)])
        self.assertEqual(t.ready(4),[]);self.assertEqual(t.ready(5),[c]);self.assertEqual(t.ready(5),[])
        t.seek(4);self.assertEqual(t.ready(5),[c])
        self.assertFalse(allowed_comment(c,{'ng_users':['blocked']}))
        self.assertFalse(allowed_comment(c,{'ng_words':['ell']}))

    def test_recording_partial_collision_and_sidecar(self):
        from nicoview.recording import Recording
        with tempfile.TemporaryDirectory() as d:
            r=Recording(d,{'title':'test','id':'lv1'})
            r.write(b'video');r.add_comment(Comment('1','hello',0,position=1),1)
            a=r.path;r.finish(False)
            self.assertTrue(a.exists());self.assertIn('.partial',a.name)
            r2=Recording(d,{'title':'test','id':'lv1'});r2.write(b'other');r2.finish(True)
            self.assertNotEqual(a,r2.path);self.assertTrue(r2.path.exists())
            meta=json.loads(r2.metadata_path.read_text(encoding='utf-8'))
            self.assertEqual(meta['state'],'complete')
            self.assertNotIn('credentials',meta)

    def test_record_only_never_requires_player(self):
        events=[];w=Worker(events.append)
        with tempfile.TemporaryDirectory() as d,patch('nicoview.worker.stream_plugin') as factory,patch.object(w,'_launch') as launch:
            plugin=factory.return_value;plugin.streams.return_value={'best':MagicMock()}
            plugin.streams.return_value['best'].open.return_value=io.BytesIO(b'x'*(65536*3+7))
            plugin.title='Test';plugin.id='lv1';plugin.author='Author';plugin.program={};plugin.posting_allowed=False;plugin.is_timeshift=True
            w.handle({'type':'start','request_id':'record','mode':'record','url':'https://live.nicovideo.jp/watch/lv1','player_path':'','credentials':None,'settings':{'recording_dir':d}})
            w.thread.join(10);launch.assert_not_called()
            self.assertTrue(any(e['type']=='recording' for e in events),events)
            self.assertTrue(any(e['type']=='completed' and e['ok'] for e in events),events)
            ready=[e for e in events if e['type']=='job_ready']
            self.assertEqual(len(ready),1,events)
            self.assertEqual(ready[0]['mode'],'record')
        w.close()

    def test_live_recording_completes_when_authenticated_end_check_confirms(self):
        from requests import Response
        from nicoview.worker import stream_plugin

        events=[]
        worker=Worker(events.append)
        self.addCleanup(worker.close)
        url='https://live.nicovideo.jp/watch/lv1'

        def watch_page(session, request, **kwargs):
            response=Response()
            response.status_code=200
            response.url=request.url
            response.encoding='utf-8'
            status='ENDED' if request.headers.get('Cookie')=='user_session=fixture_session' else 'ON_AIR'
            data=html.escape(json.dumps({'program':{'nicoliveProgramId':'lv1','status':status}}))
            response._content=f'<script id="embedded-data" data-props="{data}"></script>'.encode()
            return response

        plugin=stream_plugin(worker.auth.session,url,lambda info:None)
        plugin.title='LiveTest'
        plugin.id='lv1'
        plugin.author='Author'
        plugin.program={'status':'ON_AIR'}
        plugin.posting_allowed=False
        plugin.is_timeshift=False
        stream=MagicMock()
        stream.open.return_value=io.BytesIO(b'video')
        credentials={'session':[{'name':'user_session','value':'fixture_session','domain':'.nicovideo.jp','path':'/'}]}
        with (
            tempfile.TemporaryDirectory() as directory,
            patch('requests.Session.send',autospec=True,side_effect=watch_page) as send,
            patch('nicoview.worker.stream_plugin',return_value=plugin),
            patch.object(plugin,'streams',return_value={'best':stream}),
        ):
            worker.handle({'type':'start','request_id':'live-record','mode':'record',
                'url':url,'credentials':credentials,'settings':{'recording_dir':directory}})
            worker.thread.join(10)
            self.assertFalse(worker.thread.is_alive())
            self.assertTrue(any(e['type']=='completed' and e['ok'] for e in events),events)
            self.assertFalse(any(e['type']=='error' for e in events),events)
            self.assertEqual(send.call_count,1)
            self.assertEqual(send.call_args.args[1].url,url)
            ts_files=list(Path(directory).glob('*/*/*.ts'))
            self.assertTrue(any(not f.name.endswith('.partial.ts') for f in ts_files))

    def test_no_streams_uses_program_status_from_original_watch_page(self):
        events=[]
        worker=Worker(events.append)
        self.addCleanup(worker.close)
        data={'program':{'nicoliveProgramId':'lv1','status':'ENDED'},'site':{'relive':{'webSocketUrl':''}}}
        with (
            patch.object(worker.auth.session.http,'get',return_value=data) as get,
            patch('requests.Session.send',side_effect=AssertionError('Unexpected additional HTTP request')) as send,
        ):
            worker.handle({'type':'start','request_id':'ended-program','mode':'record',
                'url':'https://live.nicovideo.jp/watch/lv1','credentials':None})
            worker.thread.join(10)
            self.assertFalse(worker.thread.is_alive())
            self.assertIn('番組の放送は終了しました。',[e.get('text') for e in events if e['type']=='error'])
            get.assert_called_once()
            send.assert_not_called()

    def test_record_open_failure_never_acknowledges_job_ready(self):
        events=[];worker=Worker(events.append)
        try:
            with tempfile.TemporaryDirectory() as directory,patch('nicoview.worker.stream_plugin') as factory:
                plugin=factory.return_value
                stream=MagicMock();stream.open.side_effect=OSError('read failed')
                plugin.streams.return_value={'best':stream}
                plugin.title='Test';plugin.id='lv1';plugin.author='Author';plugin.program={};plugin.posting_allowed=False;plugin.is_timeshift=True
                worker.handle({'type':'start','request_id':'failed-record','mode':'record',
                    'url':'https://live.nicovideo.jp/watch/lv1','player_path':'','credentials':None,
                    'settings':{'recording_dir':directory}})
                worker.thread.join(10)
                self.assertFalse(worker.thread.is_alive())
                self.assertTrue(any(e['type']=='error' for e in events),events)
                self.assertFalse(any(e['type']=='job_ready' for e in events),events)
                self.assertTrue(any(e['type']=='completed' and not e['ok'] for e in events),events)
        finally:worker.close()

class TransportTests(unittest.TestCase):
    def test_ipc_observed_properties_fragmented(self):
        from nicoview.player_ipc import PlayerIPC
        ipc=PlayerIPC('unused')
        ipc._consume(b'{"event":"property-change","name":"time-pos","data":')
        ipc._consume(b'12.5}\n{"event":"property-change","name":"pause","data":true}\n')
        self.assertEqual(ipc.snapshot(),{'time-pos':12.5,'pause':True})

    def test_packed_archive_preserves_all_messages(self):
        from nicoview.comments import decode_packed,decode_entry
        a=field(1,field(1,b'1'))+field(2,field(1,field(1,b'first')))
        b=field(1,field(1,b'2'))+field(2,field(1,field(1,b'second')))
        comments,nxt=decode_packed(field(1,a)+field(1,b)+field(2,field(1,b'https://mpn.live.nicovideo.jp/next')))
        self.assertEqual([c.text for c in comments],['first','second'])
        self.assertTrue(nxt.endswith('/next'))
        self.assertEqual(decode_entry(field(2,field(2,field(1,b'https://mpn.live.nicovideo.jp/past'))))[0],'backward')

    def test_private_proxy_rewrites_playlist_without_remote_tokens(self):
        import requests
        from types import SimpleNamespace
        from nicoview.media_proxy import MediaProxy
        session=SimpleNamespace(http=MagicMock())
        response=MagicMock();response.status_code=200;response.headers={'Content-Type':'application/vnd.apple.mpegurl'}
        response.iter_content.return_value=[b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key?token=SECRET"\nseg.ts?token=SECRET\n']
        session.http.get.return_value=response
        p=MediaProxy(session,SimpleNamespace(url='https://delivery.nicovideo.jp/a/index.m3u8'))
        try:
            result=requests.get(p.url,timeout=3)
            self.assertEqual(result.status_code,200);self.assertNotIn('SECRET',result.text)
            self.assertNotIn('delivery.nicovideo.jp',result.text)
            self.assertIn('http://127.0.0.1:',result.text)
            with self.assertRaises(ValueError):p.register('https://evil.example/steal')
        finally:p.close()

    def test_recording_metadata_never_retains_account_payload(self):
        from nicoview.recording import Recording
        with tempfile.TemporaryDirectory() as d:
            r=Recording(d,{'title':'test','author':'host','credentials':'SECRET','cookies':'SECRET'})
            r.write(b'data');r.finish(True)
            self.assertNotIn('SECRET',r.metadata_path.read_text(encoding='utf-8'))
            self.assertEqual(r.path.parent.parent.name,'host')
            self.assertTrue(r.path.parent.name.startswith('令和'))
