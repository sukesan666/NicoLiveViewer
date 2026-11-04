from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from nicoview.thumbnail_cache import ThumbnailCache


class ThumbnailCacheTests(unittest.TestCase):
    def test_cached_image_never_contacts_network(self):
        with tempfile.TemporaryDirectory() as folder:
            url='https://secure-dcdn.cdn.nimg.jp/test.jpg'
            path=Path(folder)/(hashlib.sha256(url.encode()).hexdigest()+'.png')
            path.write_bytes(b'cached')
            fetch=Mock()
            self.assertEqual(ThumbnailCache(folder).get(url,fetch),b'cached')
            fetch.assert_not_called()

    def test_same_image_concurrent_requests_share_one_download(self):
        with tempfile.TemporaryDirectory() as folder:
            cache=ThumbnailCache(folder)
            entered=threading.Event();release=threading.Event()
            def fetch(url,cancelled):
                entered.set();release.wait(2)
                return b'image'
            fetch=Mock(side_effect=fetch)
            with ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(cache.get,'same-url',fetch)
                self.assertTrue(entered.wait(1))
                second=pool.submit(cache.get,'same-url',fetch)
                release.set()
                self.assertEqual(first.result(2),b'image')
                self.assertEqual(second.result(2),b'image')
            self.assertEqual(fetch.call_count,1)

    def test_1000_file_limit_evicts_least_recently_used_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            for i in range(1000):
                name=hashlib.sha256(str(i).encode()).hexdigest()+'.png'
                path=directory/name;path.write_bytes(b'image')
                os.utime(path,(1000000000+i,1000000000+i))
            cache=ThumbnailCache(folder,max_age_days=None)
            self.assertEqual(cache.read('0'),b'image')
            self.assertEqual(cache.get('new',lambda *_:b'new'),b'new')
            self.assertEqual(len(list(directory.glob('*.png'))),1000)
            self.assertTrue((directory/(hashlib.sha256(b'0').hexdigest()+'.png')).is_file())
            self.assertFalse((directory/(hashlib.sha256(b'1').hexdigest()+'.png')).is_file())
            self.assertEqual(ThumbnailCache(folder,max_age_days=None).read('new'),b'new')

    def test_cache_only_read_does_not_download_and_failure_is_not_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            cache=ThumbnailCache(folder)
            self.assertIsNone(cache.read('missing'))
            self.assertIsNone(cache.get('missing',lambda *_:None))
            self.assertFalse(list(Path(folder).glob('*.png')))

    def test_cache_write_failure_still_delivers_downloaded_image(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            cache=ThumbnailCache(folder)
            with patch.object(Path,'replace',side_effect=OSError('read only')):
                self.assertEqual(cache.get('image',lambda *_:b'data'),b'data')

    def test_readable_cached_image_is_used_even_if_timestamp_cannot_be_updated(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            cache=ThumbnailCache(folder)
            cache.get('image',lambda *_:b'cached')
            fetch=Mock(return_value=b'redownloaded')
            with patch('nicoview.thumbnail_cache.os.utime',side_effect=OSError('read only')):
                self.assertEqual(cache.get('image',fetch),b'cached')
            fetch.assert_not_called()

    def test_cancelled_download_waiting_for_network_admission_sends_no_request(self):
        from unittest.mock import patch
        from nicoview import catalog
        with tempfile.TemporaryDirectory() as folder:
            cache=ThumbnailCache(folder);cancel=threading.Event();entered=threading.Event()
            http=Mock()
            original=catalog._download_thumbnail
            def download(*args,**kwargs):
                entered.set();return original(*args,**kwargs)
            catalog._THUMB_NETWORK_LOCK.acquire()
            try:
                with patch.object(catalog,'_thumbnail_cache',return_value=cache),patch.object(catalog,'clean_old_thumbnails'),patch.object(catalog,'_THUMB_SESSION',http),patch.object(catalog,'_download_thumbnail',side_effect=download):
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        result=pool.submit(catalog.thumbnail_bytes,'https://secure-dcdn.cdn.nimg.jp/test.jpg',cancel=cancel)
                        self.assertTrue(entered.wait(1))
                        cancel.set();catalog._THUMB_NETWORK_LOCK.release()
                        self.assertIsNone(result.result(2))
                    http.get.assert_not_called()
            finally:
                if catalog._THUMB_NETWORK_LOCK.locked():catalog._THUMB_NETWORK_LOCK.release()

    def test_cancelled_owner_does_not_cancel_an_active_subscriber(self):
        with tempfile.TemporaryDirectory() as folder:
            cache=ThumbnailCache(folder);cancel=threading.Event()
            entered=threading.Event();release=threading.Event();joined=threading.Event()
            original_read=cache._read;reads=[]
            def read(key):
                result=original_read(key);reads.append(key)
                if len(reads)==2:joined.set()
                return result
            cache._read=read
            def fetch(url,cancelled):
                entered.set();release.wait(2)
                return None if cancelled() else b'image'
            with ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(cache.get,'same',fetch,cancel)
                self.assertTrue(entered.wait(1));cancel.set()
                second=pool.submit(cache.get,'same',fetch)
                self.assertTrue(joined.wait(1))
                with cache.lock:pass  # The second subscriber has released registration.
                release.set()
                self.assertEqual(first.result(2),b'image')
                self.assertEqual(second.result(2),b'image')

    def test_late_active_subscriber_can_resume_a_cancelled_admission(self):
        with tempfile.TemporaryDirectory() as folder:
            cache=ThumbnailCache(folder);cancel=threading.Event()
            entered=threading.Event();evaluate=threading.Event();aborted=threading.Event()
            release=threading.Event();joined=threading.Event();calls=[];reads=[]
            original=cache._read
            def read(key):
                result=original(key);reads.append(key)
                if len(reads)==2:joined.set()
                return result
            cache._read=read
            def fetch(url,cancelled):
                calls.append(url)
                if len(calls)==1:
                    entered.set();evaluate.wait(2)
                    stopped=cancelled();aborted.set();release.wait(2)
                    return None if stopped else b'image'
                return b'image'
            with ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(cache.get,'same',fetch,cancel)
                self.assertTrue(entered.wait(1));cancel.set();evaluate.set()
                self.assertTrue(aborted.wait(1))
                second=pool.submit(cache.get,'same',fetch)
                self.assertTrue(joined.wait(1))
                with cache.lock:pass
                release.set()
                self.assertEqual(second.result(2),b'image')
                first.result(2)
