import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from nicoview.settings import clean_settings

class DesktopSettingsTests(unittest.TestCase):
    def test_new_settings_are_bounded_and_no_shared_arrays(self):
        s=clean_settings({'fps':999,'volume':-1,'comment_offset':-15,'startup':'yes','ng_words':['x','x',2]})
        self.assertEqual(s.get('fps'),240)
        self.assertEqual(s['volume'],0)
        self.assertEqual(s['comment_offset'],-15)
        self.assertFalse(s['startup'])
        self.assertEqual(s['ng_words'],['x'])
        s['ng_words'].append('y')
        self.assertEqual(clean_settings({})['ng_words'],[])

    def test_backup_import_excludes_credentials_and_state_secrets(self):
        from nicoview.preferences import PreferenceStore
        with tempfile.TemporaryDirectory() as d:
            store=PreferenceStore(d)
            store.save_settings({'fps':60})
            store.save_settings({'fps':90})
            store.restore_backup()
            self.assertEqual(store.load_settings()['fps'],60)
            file=Path(d)/'import.json'
            file.write_text(json.dumps({'settings':{'fps':144,'cookie':'secret','startup':True,'tray':True}}))
            self.assertEqual(store.import_settings(file)['fps'],144)
            self.assertFalse(store.load_settings()['startup'])
            store.save_state({'history':[{'id':'lv123','cookie':'secret','url':'https://example.com/?token=secret'}]})
            store.export_settings(file)
            self.assertNotIn('secret',file.read_text())
            store.export_diagnostics(file,{'message':'cookie=secret','token':'secret','job_count':3})
            self.assertNotIn('secret',file.read_text())
            self.assertNotIn('secret',(Path(d)/'state.json').read_text())

    def test_corrupt_current_recovers_last_valid_backup(self):
        from nicoview.preferences import PreferenceStore
        with tempfile.TemporaryDirectory() as d:
            store=PreferenceStore(d)
            store.save_settings({'fps':60}); store.save_settings({'fps':90})
            (Path(d)/'settings.json').write_text('broken')
            self.assertEqual(store.load_settings()['fps'],60)

    def test_quiet_hours_cross_midnight_and_end_exclusive(self):
        from nicoview.desktop import in_quiet_hours
        s={'quiet_enabled':True,'quiet_start':'22:00','quiet_end':'07:00'}
        self.assertTrue(in_quiet_hours(s,datetime(2026,1,1,23,0)))
        self.assertTrue(in_quiet_hours(s,datetime(2026,1,1,6,59)))
        self.assertFalse(in_quiet_hours(s,datetime(2026,1,1,7,0)))

    def test_grouping_suppresses_repeat_and_quiet(self):
        from nicoview.desktop import NotificationSound
        sound=NotificationSound()
        s=clean_settings({'sound_play':'missing.wav'})
        self.assertFalse(sound.notify('play',s))
        self.assertIsNone(sound.last_event)

    def test_wav_volume_does_not_modify_source(self):
        from nicoview.desktop import scaled_wav
        import wave, struct, io
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'sound.wav'
            with wave.open(str(p),'wb') as f:
                f.setparams((1,2,8000,0,'NONE','not compressed')); f.writeframes(struct.pack('<hh',2000,-2000))
            with wave.open(io.BytesIO(scaled_wav(p,50)),'rb') as f:
                self.assertEqual(struct.unpack('<hh',f.readframes(2)),(1000,-1000))
            with wave.open(str(p),'rb') as f:
                self.assertEqual(struct.unpack('<hh',f.readframes(2)),(2000,-2000))

class SettingsUiTests(unittest.TestCase):
    def test_dialog_saves_sanitized_values(self):
        import tkinter as tk
        from nicoview.settings_ui import SettingsDialog
        root=tk.Tk(); root.withdraw(); saved=[]
        try:
            dialog=SettingsDialog(root,{},saved.append)
            dialog.variables['fps'].set('144')
            dialog.texts['ng_words'].insert('1.0','spam\nspam')
            dialog.save()
            self.assertEqual(saved[0]['fps'],144)
            self.assertEqual(saved[0]['ng_words'],['spam'])
        finally: root.destroy()

    def test_state_preserves_scheduler_history_and_migration(self):
        from nicoview.preferences import PreferenceStore
        with tempfile.TemporaryDirectory() as d:
            store=PreferenceStore(Path(d)/'new')
            legacy=Path(d)/'old'; legacy.mkdir()
            (legacy/'credentials.dat').write_bytes(b'encrypted-private-data')
            store.migrate(legacy)
            self.assertEqual((store.directory/'credentials.dat').read_bytes(),b'encrypted-private-data')
            state={'reservations':[{'url':'https://live.nicovideo.jp/watch/lv1','start':123}], 'executions':[{'status':'done'}], 'history':[{'position':42,'local_path':'C:/movie.mp4'}]}
            store.save_state(state)
            self.assertEqual(store.load_state(),state)

    def test_sound_events_are_grouped_without_playing_audio(self):
        from nicoview.desktop import NotificationSound
        from unittest.mock import patch
        import wave
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'notice.wav'
            with wave.open(str(path),'wb') as f:
                f.setparams((1,1,8000,0,'NONE','not compressed')); f.writeframes(bytes([128]*80))
            s=clean_settings({'sound_play':str(path),'sound_record':str(path)})
            with patch('nicoview.desktop.threading.Thread.start'):
                sound=NotificationSound()
                self.assertTrue(sound.notify('play',s))
                self.assertFalse(sound.notify('record',s))
                sound.last_event-=6
                self.assertTrue(sound.notify('record',s))
