import io
import threading
import unittest
from unittest.mock import MagicMock, patch
from nicoview.playback import pump, build_player_args
from nicoview.controller import Controller

class PlaybackTests(unittest.TestCase):
    def test_pump_and_disconnect(self):
        out=io.BytesIO(); pump(io.BytesIO(b'video'),out,threading.Event())
        self.assertEqual(out.getvalue(),b'video')
        bad=MagicMock(); bad.write.side_effect=BrokenPipeError
        with self.assertRaises(BrokenPipeError): pump(io.BytesIO(b'video'),bad,threading.Event())

    def test_stop_before_read(self):
        stop=threading.Event();stop.set(); source=MagicMock()
        pump(source,io.BytesIO(),stop); source.read.assert_not_called()

    def test_args_no_shell_interpolation(self):
        args=build_player_args('C:/日本語 空白/mpv.exe','pipe-name','x.lua')
        self.assertEqual(args[0],'C:/日本語 空白/mpv.exe')
        self.assertIn('--no-config',args);self.assertIn('--input-ipc-server=pipe-name',args)

    def test_controller_duplicate_and_stale(self):
        c=Controller(); c._send=MagicMock();c._ensure=MagicMock()
        ident=c.start('https://live.nicovideo.jp/watch/lv1','mpv.exe',None)
        with self.assertRaises(RuntimeError): c.start('https://live.nicovideo.jp/watch/lv2','mpv.exe',None)
        c.submit_otp('old','123456'); self.assertEqual(c._send.call_count,1)
        c._events.put({'type':'stopped','request_id':'old'})
        self.assertEqual(c.poll_events(),[]); self.assertTrue(c.busy)
        c._events.put({'type':'stopped','request_id':ident})
        self.assertEqual(c.poll_events()[0]['type'],'stopped'); self.assertFalse(c.busy)
