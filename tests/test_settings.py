import json
from pathlib import Path
import tempfile
import unittest
from nicoview.settings import clean_settings, save_settings, load_settings
from nicoview.overlay import OverlayQueue
from nicoview.comments import Comment

class SettingsTests(unittest.TestCase):
    def test_ui_zoom_values_and_persistence(self):
        for value in range(100,251,10):
            self.assertEqual(clean_settings({'ui_scale':str(value)})['ui_scale'],value)
        for value in (0,99,115,300,'bad',None):
            self.assertEqual(clean_settings({'ui_scale':value})['ui_scale'],100)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'settings.json'
            save_settings(path,{'ui_scale':250})
            self.assertEqual(load_settings(path)['ui_scale'],250)

    def test_limits_and_font(self):
        s=clean_settings({'font':'bad{\\fnX}','size':300,'delay':-4},['Meiryo'])
        self.assertEqual(s['font'],'Meiryo'); self.assertEqual(s['size'],96); self.assertEqual(s['delay'],0)
        self.assertEqual(clean_settings({},['Meiryo'])['size'],28)
        self.assertEqual(clean_settings({'size':'nan','delay':float('nan')})['delay'],0)

    def test_save_allowlist(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'settings.json'
            save_settings(p,{'font':'Meiryo','password':'secret','cookie':'secret','size':33})
            self.assertNotIn('secret',p.read_text())
            self.assertEqual(load_settings(p)['size'],33)
            p.write_text('invalid'); self.assertEqual(load_settings(p)['size'],28)

    def test_comment_queue_delay_bound_clear(self):
        q=OverlayQueue()
        for i in range(600): q.add(Comment(str(i),'text',10))
        self.assertEqual(len(q.pending),500)
        self.assertEqual(q.ready(11,delay=2),[])
        self.assertEqual(len(q.ready(13,delay=2)),500)
        q.add(Comment('x','t',0)); q.clear(); self.assertEqual(q.ready(99),[])
