import tempfile
import tkinter as tk
import types
import unittest
from unittest import mock
from nicoview import discovery_ui
from nicoview.preferences import PreferenceStore
from nicoview.scheduler import Scheduler, reservation_key
from nicoview.studio_ui import StudioApp

USER=dict(id='',url='',title='配信者A',author='配信者A',thumbnail='',start=0,status='USER_RULE',provider='user',user_id='12345')
PROGRAM=dict(id='lv9',url='https://live.nicovideo.jp/watch/lv9',title='番組',author='',thumbnail='',start=0,status='RESERVED',provider='official',user_id='')

class FakeCatalog:
    def __init__(self,cookies):pass
    def followed_users(self):return [dict(USER)]
    def close(self):pass

class ReservationKeyTests(unittest.TestCase):
    def test_keys_match_picker_rows_and_saved_records(self):
        self.assertEqual(reservation_key(USER),'user:12345')
        self.assertEqual(reservation_key(PROGRAM),'lv9')
        self.assertEqual(reservation_key({'url':'https://live.nicovideo.jp/watch/lv9'}),'lv9')
        self.assertEqual(reservation_key({'url':'https://live.nicovideo.jp/watch/lv9','channel_id':'ch67'}),'lv9')
        self.assertEqual(reservation_key({'keyword':'abc'}),'')
        self.assertEqual(reservation_key({'official_tag':'tag'}),'')
        s=Scheduler();s.add(USER,'play');s.add(PROGRAM,'both');s.add({'keyword':'abc'},'play');s.add({'official_tag':'tag'},'play')
        self.assertEqual(s.keys('play'),{'user:12345','lv9'})
        self.assertEqual(s.keys('record'),{'lv9'})
        with self.assertRaises(ValueError):
            s.add({'url':'https://live.nicovideo.jp/watch/lv9','channel_id':'ch67'},'record')

    def test_remove_key_only_drops_requested_mode(self):
        s=Scheduler();s.add(USER,'play');s.add(USER,'record');s.add(PROGRAM,'both')
        s.remove_key('user:12345','play');s.remove_key('lv9','play')
        rows=s.snapshot()
        self.assertEqual([(reservation_key(r),r['mode']) for r in rows],[('user:12345','record'),('lv9','record')])

class PickerPersistenceTests(unittest.TestCase):
    def pump(self,root,picker):
        for _ in range(100):
            root.update()
            if picker.rows:return
            root.after(10)

    def open_picker(self,root,app):
        picker=discovery_ui.SchedulePicker(root,[],app.add_reservation,mode='play',reserved_keys=app.scheduler.keys,on_uncheck=app.remove_reservation)
        self.pump(root,picker);return picker

    def click(self,picker,key):
        picker.tree.focus(key);picker._toggle(types.SimpleNamespace(keysym='space',y=0))

    def test_check_reserves_immediately_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(discovery_ui,'Catalog',FakeCatalog):
            root=tk.Tk();root.withdraw()
            try:
                app=StudioApp(root,PreferenceStore(d),background=False)
                picker=self.open_picker(root,app)
                self.click(picker,'user:12345')
                self.assertEqual(len(app.scheduler.snapshot()),1)
                app.shutdown()
            except Exception:
                root.destroy();raise
            self.assertEqual(len(PreferenceStore(d).load_state()['reservations']),1)

            root=tk.Tk();root.withdraw()
            app=StudioApp(root,PreferenceStore(d),background=False)
            try:
                picker=self.open_picker(root,app)
                self.assertIn('user:12345',picker.checked)
                self.assertTrue(picker.tree.item('user:12345','text').startswith('☑'))
                picker.choose('play') # Button on an already checked row must not duplicate.
                self.assertEqual(len(app.scheduler.snapshot()),1)
                picker.choose('record') # Up to 1 play and 1 record is allowed for the same target.
                self.assertEqual([r['mode'] for r in app.scheduler.snapshot()],['play','record'])
                picker.choose('record') # A second record reservation is blocked.
                self.assertEqual([r['mode'] for r in app.scheduler.snapshot()],['play','record'])
                self.click(picker,'user:12345')
                self.assertEqual(app.scheduler.snapshot(),[])
                self.assertNotIn('user:12345',picker.checked)
                self.click(picker,'user:12345')
                picker.choose('both')
                self.assertEqual([r['mode'] for r in app.scheduler.snapshot()],['both'])
                self.click(picker,'user:12345')
                self.assertEqual(app.scheduler.snapshot(),[])
                self.assertNotIn('user:12345',picker.checked)
                self.click(picker,'user:12345')
                picker.choose('record')
                self.assertEqual([r['mode'] for r in app.scheduler.snapshot()],['record'])
                picker.choose('play')
                self.assertEqual([r['mode'] for r in app.scheduler.snapshot()],['record','play'])
                self.click(picker,'user:12345')
                self.assertEqual(app.scheduler.snapshot(),[])
                self.assertNotIn('user:12345',picker.checked)
                self.click(picker,'user:12345')
                picker.choose('both')
                self.assertEqual([r['mode'] for r in app.scheduler.snapshot()],['both'])
                self.click(picker,'user:12345')
                self.assertEqual(app.scheduler.snapshot(),[])
                self.assertNotIn('user:12345',picker.checked)
            finally:app.shutdown()
            self.assertEqual(PreferenceStore(d).load_state()['reservations'],[])

if __name__=='__main__':unittest.main()
