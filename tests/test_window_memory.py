import tempfile
import json
import os
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from nicoview.layout import WindowMemory, fit_window

class WindowMemoryTests(unittest.TestCase):
    def test_restore_normal_geometry_and_keep_dialogs_separate(self):
        with tempfile.TemporaryDirectory() as d,patch('nicoview.layout.work_area',return_value=(0,0,1920,1040)):
            root=tk.Tk();root.withdraw();path=Path(d)/'layout.json'
            root._window_memory=WindowMemory(root,path)
            for name,x in [('play',210),('record',450)]:
                w=tk.Toplevel(root);w.title(name);fit_window(w,600,400);root.update()
                w.geometry(f'700x450+{x}+160');root.update()
                root._window_memory.capture(w);w.destroy()
            root._window_memory.flush()
            root._window_memory=WindowMemory(root,path)
            for name,x in [('play',210),('record',450)]:
                w=tk.Toplevel(root);w.title(name);fit_window(w,600,400);root.update()
                self.assertEqual((w.winfo_width(),w.winfo_height()),(700,450))
                self.assertAlmostEqual(w.winfo_x(),x,delta=12)
                w.destroy()
            root.destroy()

    def test_offscreen_restore_clamped_and_minimized_not_saved(self):
        with tempfile.TemporaryDirectory() as d,patch('nicoview.layout.work_area',return_value=(0,0,1280,720)):
            root=tk.Tk();root.withdraw();m=WindowMemory(root,Path(d)/'layout.json');root._window_memory=m
            m.records['test']={'width':9000,'height':9000,'x':50000,'y':-50000,'maximized':False}
            w=tk.Toplevel(root);w.title('test');fit_window(w,500,300);root.update()
            self.assertLessEqual(w.winfo_width(),1280);self.assertLessEqual(w.winfo_height(),720)
            self.assertGreaterEqual(w.winfo_x(),0);self.assertGreaterEqual(w.winfo_y(),0)
            m.capture(w);before=dict(m.records['test']);w.withdraw();root.update();m.capture(w)
            self.assertEqual(m.records['test'],before)
            w.destroy();root.destroy()

    def test_saved_size_takes_priority_over_automatic_minimum(self):
        with tempfile.TemporaryDirectory() as d,patch('nicoview.layout.work_area',return_value=(0,0,1920,1040)):
            root=tk.Tk();root.withdraw();m=WindowMemory(root,Path(d)/'layout.json');root._window_memory=m
            m.records['dialog']={'width':700,'height':420,'x':210,'y':160,'maximized':False}
            w=tk.Toplevel(root);w.title('dialog');tk.Frame(w,width=1000,height=750).pack()
            fit_window(w,1000,750);root.update()
            self.assertEqual((w.winfo_width(),w.winfo_height()),(700,420))
            w.destroy();root.destroy()

    def test_large_saved_window_is_not_shrunk_by_first_run_margins(self):
        with tempfile.TemporaryDirectory() as d,patch('nicoview.layout.work_area',return_value=(0,0,1920,1040)):
            root=tk.Tk();root.withdraw();m=WindowMemory(root,Path(d)/'layout.json');root._window_memory=m
            m.records['large']={'width':1850,'height':980,'x':10,'y':10,'maximized':False}
            w=tk.Toplevel(root);w.title('large');fit_window(w,600,400);root.update()
            self.assertEqual((w.winfo_width(),w.winfo_height()),(1850,980))
            w.destroy();root.destroy()

    def test_scroll_content_fills_taller_window(self):
        from nicoview.layout import ScrollPage
        root=tk.Tk();root.geometry('700x600');pane=ScrollPage(root);pane.pack(fill='both',expand=True)
        tk.Frame(pane.inner,width=300,height=100).pack(fill='both',expand=True)
        root.update()
        self.assertGreaterEqual(pane.inner.winfo_height(),pane.canvas.winfo_height())
        root.destroy()

    def test_restart_restores_user_geometry_at_each_zoom(self):
        from nicoview.layout import initialize_zoom, zoom_container
        for percent in (100,150,250):
            with self.subTest(percent=percent),tempfile.TemporaryDirectory() as d:
                path=Path(d)/'layout.json'
                for reopening in (False,True):
                    root=tk.Tk();root.withdraw();initialize_zoom(root,percent)
                    memory=WindowMemory(root,path);root._window_memory=memory
                    w=tk.Toplevel(root);w.title('record');body=zoom_container(w,always=True)
                    tk.Frame(body,width=1000,height=700).pack(fill='both',expand=True)
                    fit_window(w,1000,700);root.update()
                    if not reopening:
                        w.geometry('700x420+200+100');root.update()
                    self.assertEqual((w.winfo_width(),w.winfo_height()),(700,420))
                    w.tk.call(w.protocol('WM_DELETE_WINDOW'));root.update();root.destroy()


@unittest.skipUnless(os.name=='nt','Windows uses absolute desktop coordinates via SetWindowPos')
class MonitorLayoutRestoreTests(unittest.TestCase):
    """Simulate monitor work areas, not physical monitor attachment or DPI changes.

    The saved client size must survive when it fits. Otherwise the client plus
    the simulated 16x40 non-client frame must fit in the selected work area.
    Negative desktop X is absolute, not Tk's distance from the right edge.
    """

    def assert_restored(self, saved_rect, work_area, expected_rect):
        from nicoview import layout
        import ctypes

        x,y,width,height=saved_rect
        expected_x,expected_y,expected_width,expected_height=expected_rect
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'window-layout.json'
            path.write_text(json.dumps({'dialog':dict(x=x,y=y,width=width,height=height,maximized=False)}),encoding='utf-8')
            root=Mock();root._native_scaling=96/72
            window=Mock();window._geometry_tracking=False
            window.title.return_value='dialog'
            window.tk.call.return_value=96/72
            window.minsize.return_value=(360,260)
            window.winfo_id.return_value=123
            window.protocol.return_value=''
            user32=Mock();user32.GetAncestor.return_value=456
            memory=WindowMemory(root,path)
            with patch.object(layout,'work_area',return_value=work_area) as area,patch.object(layout,'frame_size',return_value=(16,40)),patch.object(ctypes.windll,'user32',user32):
                memory.track(window)
                area.assert_called_once_with(window,(x,y,x+width,y+height))
                window.geometry.assert_called_once_with(f'{expected_width}x{expected_height}')
                user32.SetWindowPos.assert_called_once_with(456,None,expected_x,expected_y,0,0,0x15)
                # Later fitting must not recenter a restored, tracked window.
                fit_window(window,1000,680)
                window.geometry.assert_called_once()
                user32.SetWindowPos.assert_called_once()

    def test_single_monitor_preserves_in_bounds_position_and_size(self):
        self.assert_restored((200,100,700,420),(0,0,1920,1040),(200,100,700,420))

    def test_dual_left_monitor_preserves_negative_absolute_x(self):
        # Primary: (0,0,1920,1040); left secondary is the selected work area.
        self.assert_restored((-1700,120,700,420),(-1920,0,0,1040),(-1700,120,700,420))

    def test_dual_right_monitor_preserves_position_and_size(self):
        # Primary: (0,0,1920,1040); right secondary has a vertical offset.
        self.assert_restored((2100,220,700,420),(1920,100,3840,1140),(2100,220,700,420))

    def test_left_monitor_removed_moves_window_to_remaining_monitor(self):
        # The old negative-X monitor no longer exists; primary is selected.
        self.assert_restored((-1700,120,700,420),(0,0,1920,1040),(0,120,700,420))

    def test_right_monitor_removed_keeps_frame_inside_remaining_monitor(self):
        # 1920 - 700 client pixels - 16 frame pixels = rightmost valid X.
        self.assert_restored((2100,220,700,420),(0,0,1920,1040),(1204,220,700,420))

    def test_smaller_monitor_shrinks_only_to_available_client_area(self):
        # On the replacement 1280x720 work area, reserve only the actual frame.
        self.assert_restored((2100,800,1800,950),(0,0,1280,720),(0,0,1264,680))
