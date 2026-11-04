import threading
import time
import tkinter as tk
from tkinter import ttk
from pathlib import Path
import unittest
from unittest.mock import Mock,patch

from nicoview.discovery_ui import DiscoveryWindow


def row(number):
    return {'id':f'lv{number}','url':f'https://live.nicovideo.jp/watch/lv{number}',
            'title':f'test {number}','author':'test','status':'ON_AIR','start':0,
            'thumbnail':f'https://secure-dcdn.cdn.nimg.jp/{number}.jpg'}


class DiscoveryLoadingTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw();self.window=None

    def tearDown(self):
        if self.window and not self.window.closed:self.window.close()
        self.root.destroy()

    def pump_until(self,condition,timeout=3):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            self.root.update()
            if condition():return
            time.sleep(.002)
        self.fail('Timed out waiting for offline UI result')

    def test_initial_load_once_and_explicit_refresh_still_fetches(self):
        with patch('nicoview.discovery_ui.Catalog') as catalog:
            catalog.return_value.following_live_users.return_value=[]
            self.window=DiscoveryWindow(self.root,[],Mock())
            self.pump_until(lambda:self.window.status.get().startswith('0'))
            self.root.update()
            self.assertEqual(catalog.return_value.following_live_users.call_count,1)
            self.window.load()
            self.pump_until(lambda:catalog.return_value.following_live_users.call_count==2)

    def test_scrollbar_only_appears_for_rows_outside_the_visible_area(self):
        with patch('nicoview.discovery_ui.Catalog') as catalog:
            catalog.return_value.following_live_users.return_value=[row(1),row(2)]
            self.window=DiscoveryWindow(self.root,[],Mock())
            self.pump_until(lambda:len(self.window.tree.get_children())==2)
            tree=self.window.tree
            scrollbar=next(child for child in tree.master.winfo_children()
                           if isinstance(child,ttk.Scrollbar))
            self.assertFalse(scrollbar.winfo_ismapped())
            for number in range(3,7):
                tree.insert('','end',iid=f'lv{number}',text=f'test {number}')
            self.window.window.geometry('1000x440')
            self.root.update()
            self.assertTrue(scrollbar.winfo_ismapped())
            self.window.window.geometry('1000x900')
            self.root.update()
            self.assertFalse(scrollbar.winfo_ismapped())
            for number in range(7,101):
                tree.insert('','end',iid=f'lv{number}',text=f'test {number}')
            self.root.update()
            self.assertTrue(scrollbar.winfo_ismapped())
            self.assertGreaterEqual(scrollbar.winfo_x(),tree.winfo_x()+tree.winfo_width())
            tree.yview_moveto(1)
            self.root.update()
            self.assertIn('lv100',self.window.visible_keys)
            tree.delete(*tree.get_children()[2:])
            self.root.update()
            self.assertFalse(scrollbar.winfo_ismapped())
            self.assertEqual(tree.yview(),(0.0,1.0))

    def test_refresh_while_same_list_is_inflight_does_not_duplicate(self):
        entered=threading.Event();release=threading.Event()
        with patch('nicoview.discovery_ui.Catalog') as catalog:
            def fetch():
                entered.set();release.wait(2);return []
            catalog.return_value.following_live_users.side_effect=fetch
            self.window=DiscoveryWindow(self.root,[],Mock())
            self.pump_until(entered.is_set)
            self.window.load();self.window.load()
            self.assertEqual(catalog.return_value.following_live_users.call_count,1)
            release.set()
            self.pump_until(lambda:not self.window.loading)

    def test_switching_tabs_and_back_reuses_the_pending_list_request(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        with patch('nicoview.discovery_ui.Catalog') as catalog:
            def fetch():
                entered.set();release.wait(2);return [row(1)]
            catalog.return_value.following_live_users.side_effect=fetch
            catalog.return_value.ranking.return_value=[]
            self.window=DiscoveryWindow(self.root,[],Mock())
            self.pump_until(entered.is_set)
            self.window.book.select(1);self.window.load(force=False)
            self.window.book.select(0);self.window.load(force=False)
            self.root.update()
            self.assertEqual(catalog.return_value.following_live_users.call_count,1)
            release.set()
            self.pump_until(lambda:'lv1' in self.window.rows)

    def test_cached_images_are_displayed_before_missing_images_are_downloaded(self):
        trace=[]
        def image(url,cache_only=False,cancel=None):
            if cache_only:return b'cached' if url.endswith('/2.jpg') else None
            trace.append('download');return b'new'
        def convert(raw,path):
            trace.append(raw.decode());return b'png'
        with patch('nicoview.discovery_ui.Catalog') as catalog,patch('nicoview.discovery_ui.thumbnail_bytes',side_effect=image),patch('nicoview.discovery_ui.thumbnail_png',side_effect=convert):
            catalog.return_value.following_live_users.return_value=[row(1),row(2)]
            self.window=DiscoveryWindow(self.root,[],Mock(),ffmpeg_path=__file__)
            self.pump_until(lambda:'download' in trace)
            self.assertLess(trace.index('cached'),trace.index('download'))

    def test_reads_up_to_1000_thumbnails_including_row_201(self):
        picture=tk.PhotoImage(master=self.root,width=1,height=1)
        png=self.root.tk.call(str(picture),'data','-format','png')
        with patch('nicoview.discovery_ui.Catalog') as catalog,patch('nicoview.discovery_ui.thumbnail_bytes',return_value=b'cached'),patch('nicoview.discovery_ui.thumbnail_png',return_value=png):
            catalog.return_value.following_live_users.return_value=[row(i) for i in range(1,1006)]
            self.window=DiscoveryWindow(self.root,[],Mock(),ffmpeg_path=__file__)
            self.pump_until(lambda:len(self.window.images)==1000,timeout=5)
            self.assertTrue(self.window.tree.item('lv201','image'))
            self.assertTrue(self.window.tree.item('lv1000','image'))
            self.assertFalse(self.window.tree.item('lv1001','image'))

    def test_no_thumbnail_requests_when_ffmpeg_is_missing(self):
        with patch('nicoview.discovery_ui.Catalog') as catalog,patch('nicoview.discovery_ui.thumbnail_bytes') as thumbnail:
            catalog.return_value.following_live_users.return_value=[row(1)]
            self.window=DiscoveryWindow(self.root,[],Mock(),ffmpeg_path='')
            self.pump_until(lambda:bool(self.window.rows))
            thumbnail.assert_not_called()

    def test_visible_row_is_prioritized_over_an_offscreen_row(self):
        with patch('nicoview.discovery_ui.Catalog') as catalog:
            catalog.return_value.following_live_users.return_value=[]
            self.window=DiscoveryWindow(self.root,[],Mock())
            self.window.visible_keys=('lv205',)
            remaining=[row(1),row(205),row(206)]
            self.assertEqual(self.window._next_thumbnail(remaining)['id'],'lv205')
