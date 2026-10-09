import json
import unittest
from nicoview.url import normalize_url
from nicoview.protocol import decode_message


class URLTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_url(' https://live.nicovideo.jp/watch/lv123?from=x '),
                         'https://live.nicovideo.jp/watch/lv123')

    def test_reject(self):
        for url in ['http://live.nicovideo.jp/watch/lv1', 'https://live.nicovideo.jp.evil/watch/lv1',
                    'https://a@live.nicovideo.jp/watch/lv1', 'https://live.nicovideo.jp/watch/lv１２',
                    'https://live.nicovideo.jp/watch/lv1/other', 'https://live.nicovideo.jp:99/watch/lv1',
                    'https://live.nicovideo.jp/watch/lv1\nfoo', '', 'file:///a']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_url(url)


class ProtocolTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(decode_message('{"type":"stop","request_id":"a"}')['type'], 'stop')

    def test_invalid(self):
        for data in ['{}', '[]', 'bad', '{"type":"run","request_id":"a"}',
                     json.dumps({'type':'start','request_id':3}), 'x'*65537]:
            with self.subTest(data=data[:30]), self.assertRaises(ValueError):
                decode_message(data)


if __name__ == '__main__':
    unittest.main()
