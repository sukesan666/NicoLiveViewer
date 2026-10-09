import json
import tempfile
import unittest
from pathlib import Path

class LibraryTests(unittest.TestCase):
    def test_scan_confines_untrusted_paths_and_marks_missing_partial(self):
        from nicoview.library import scan_library
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'video.partial.ts').write_bytes(b'partial')
            (root/'video.partial.metadata.json').write_text(json.dumps({'title':'A','state':'recording','path':'C:/outside.ts'}))
            (root/'missing.metadata.json').write_text(json.dumps({'title':'B','state':'complete'}))
            rows=scan_library(root,[])
            self.assertEqual({r['title']:r['state'] for r in rows},{'A':'interrupted','B':'missing'})
            self.assertEqual(next(r for r in rows if r['title']=='A')['path'],str(root/'video.partial.ts'))

    def test_history_merges_resume_without_duplicates(self):
        from nicoview.library import scan_library
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'video.ts'; p.write_bytes(b'video')
            p.with_suffix('.metadata.json').write_text(json.dumps({'title':'Broadcast','state':'complete','elapsed':100}))
            rows=scan_library(d,[{'local_path':str(p),'position':42,'watched':True}])
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['position'],42)
            self.assertTrue(rows[0]['watched'])

    def test_missing_root_and_malformed_metadata_are_safe(self):
        from nicoview.library import scan_library
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'bad.metadata.json').write_text('bad')
            self.assertEqual(scan_library(Path(d)/'none',[]),[])
            self.assertEqual(scan_library(d,[]),[])

class LibraryUiTests(unittest.TestCase):
    def test_selected_row_routes_resume_and_watched(self):
        import tkinter as tk
        from nicoview.library_ui import LibraryWindow
        root=tk.Tk(); root.withdraw(); played=[]; watched=[]
        try:
            window=LibraryWindow(root,'missing',[],lambda *args:played.append(args),watched.append)
            window.rows=[{'path':'C:/test.ts','position':42,'title':'Test','author':'Author','state':'complete','watched':False}]
            window.render(); window.tree.selection_set('0'); window.play()
            self.assertEqual(played,[('C:/test.ts',42)])
            window.mark_watched(); self.assertEqual(watched,['C:/test.ts'])
            window.destroy()
        finally: root.destroy()
