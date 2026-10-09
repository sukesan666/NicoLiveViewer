import threading
import unittest
from nicoview.comments import Frames, fields, decode_entry, decode_comment, RecentIDs
from nicoview.comments import CommentReceiver
from unittest.mock import MagicMock, patch

def vi(n):
    out=bytearray()
    while n>127: out.append((n&127)|128); n>>=7
    return bytes(out)+bytes([n])
def field(n,value):
    return vi(n*8+2)+vi(len(value))+value

class CommentTests(unittest.TestCase):
    def test_permanent_segment_failure_releases_capacity(self):
        receiver=CommentReceiver();receiver.active.add('bad')
        def wait(_):receiver.stop_event.set();return True
        with patch.object(receiver,'_fetch',side_effect=ValueError('invalid frame')), \
             patch.object(receiver.stop_event,'wait',side_effect=wait) as delay:
            receiver._segment('bad')
            delay.assert_not_called()
        self.assertEqual(receiver.active,set())

    def test_transient_segment_retry_is_bounded(self):
        receiver=CommentReceiver();receiver.active.add('offline')
        waits=[]
        def wait(seconds):
            waits.append(seconds)
            if len(waits)>3:receiver.stop_event.set();return True
            return False
        with patch.object(receiver,'_fetch',side_effect=OSError('offline')) as fetch, \
             patch.object(receiver.stop_event,'wait',side_effect=wait):
            receiver._segment('offline')
        self.assertLessEqual(fetch.call_count,3)
        self.assertEqual(receiver.active,set())

    def test_view_stream_read_timeout(self):
        receiver=CommentReceiver();receiver.cookies={}
        http=MagicMock(); response=http.get.return_value.__enter__.return_value
        response.is_redirect=False;response.iter_content.return_value=[]
        with patch('requests.Session') as factory:
            factory.return_value.__enter__.return_value=http
            list(receiver._fetch('https://mpn.live.nicovideo.jp/api/view/v4/test'))
        self.assertGreaterEqual(http.get.call_args.kwargs['timeout'][1],40)

    def test_fragmented_frames(self):
        payload=b'a'*300; data=vi(len(payload))+payload+vi(1)+b'b'
        parser=Frames(); out=[]
        for byte in data: out+=parser.feed(bytes([byte]))
        self.assertEqual(out,[payload,b'b'])

    def test_invalid_frames(self):
        with self.assertRaises(ValueError): Frames().feed(vi(1024*1024+1))
        with self.assertRaises(ValueError): fields(b'\x0a\x04a')
        with self.assertRaises(ValueError): Frames().feed(b'\xff'*11)

    def test_routing(self):
        self.assertEqual(decode_entry(field(1,field(3,b'https://mpn.live.nicovideo.jp/data/segment/1'))),
                         ('segment','https://mpn.live.nicovideo.jp/data/segment/1'))
        self.assertEqual(decode_entry(field(4,b'\x08'+vi(1234))),('next','1234'))

    def test_text_literal(self):
        text='日本語😀 {\\pos(1,1)}'
        raw=field(1,field(1,b'ID'))+field(2,field(1,field(1,text.encode())))
        comment=decode_comment(raw)
        self.assertEqual((comment.id,comment.text),('ID',text))
        self.assertIsNone(decode_comment(field(4,b'')))

    def test_recent_bound(self):
        ids=RecentIDs(2)
        self.assertTrue(ids.add('a')); self.assertFalse(ids.add('a'))
        ids.add('b');ids.add('c'); self.assertTrue(ids.add('a'))

class ColorTests(unittest.TestCase):
    def decode(self, modifier, kind=1):
        chat=field(1,b'color')+field(7,modifier)
        return decode_comment(field(2,field(kind,chat)))

    def test_named_colors_and_overflow(self):
        self.assertEqual(self.decode(bytes([24,1])).color,'FF0000')
        self.assertEqual(self.decode(bytes([24,7]),20).color,'0000FF')
        self.assertEqual(self.decode(bytes([24,15])).color,'00CC66')
        self.assertEqual(self.decode(b'').color,'FFFFFF')
        self.assertEqual(self.decode(bytes([24,99])).color,'FFFFFF')

    def test_rgb_and_invalid_color_keep_text(self):
        self.assertEqual(self.decode(field(4,bytes([8,18,16,52,24,86]))).color,'123456')
        self.assertEqual(self.decode(field(4,b'')).color,'000000')
        c=self.decode(field(4,b'\x08'+vi(256)))
        self.assertEqual((c.text,c.color),('color','FFFFFF'))
        self.assertEqual(self.decode(field(4,b'\xff')).color,'FFFFFF')
