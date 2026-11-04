import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from pathlib import Path
from unittest.mock import patch
from nicoview.preferences import PreferenceStore
from nicoview.studio_ui import StudioApp

class FakeController:
    instances=[]
    def __init__(self):
        self.busy=False; self.settings={}; self.events=[]; self.current='test'; self.closed=False
        self.instances.append(self)
    def configure(self,settings): self.settings=dict(settings)
    def start_job(self,*args,**kw): self.args=args;self.kw=kw;self.busy=True;return self.current
    def poll_events(self):
        events,self.events=self.events,[]
        if any(e['type']=='stopped' for e in events):self.busy=False
        return events
    def control(self,*args):self.control_args=args
    def stop(self):self.busy=False
    def close(self):self.closed=True;self.busy=False

class ShellTests(unittest.TestCase):
    def test_background_startup_removes_restored_expired_recording(self):
        store=PreferenceStore(Path(self.temp.name)/'startup')
        store.save_settings({'first_run_complete':True})
        store.save_state({'reservations':[
            {'id':'expired','url':'https://live.nicovideo.jp/watch/lv1','start':1000,'mode':'record'},
            {'id':'recurring','user_id':'123','mode':'record'}]})
        root=tk.Tk();root.withdraw();startup=None
        try:
            with patch.object(store,'migrate'),patch('nicoview.studio_ui.time.time',return_value=4600):
                startup=StudioApp(root,store,FakeController,background=True)
            self.assertEqual([r['id'] for r in startup.scheduler.snapshot()],['recurring'])
            saved=store.load_state()
            self.assertEqual([r['id'] for r in saved['reservations']],['recurring'])
            self.assertEqual(saved['executions'][-1]['status'],'予約期限切れ')
        finally:
            if startup:startup.shutdown()
            else:root.destroy()

    def test_schedule_tick_expires_recording_reservation_and_saves_reason(self):
        ident=self.app.scheduler.add({'url':'https://live.nicovideo.jp/watch/lv1','start':1000},'record')
        self.app.open_reservations()
        with patch('nicoview.studio_ui.time.time',return_value=4600),patch.object(self.root,'after'),patch('nicoview.catalog.Catalog') as catalog:
            self.app.schedule_tick()
        self.assertEqual(self.app.scheduler.snapshot(),[])
        self.assertFalse(self.app.reservations_tree.exists(ident))
        saved=self.app.store.load_state()
        self.assertEqual(saved['reservations'],[])
        self.assertEqual(saved['executions'][-1]['status'],'予約期限切れ')
        self.assertIn('1時間',saved['executions'][-1]['reason'])
        catalog.assert_not_called()

    def test_expiry_keeps_busy_recording_and_reconnect_wait(self):
        self.app.settings.update(recording_dir=self.temp.name,ffmpeg_path=__file__)
        program={'url':'https://live.nicovideo.jp/watch/lv1','start':1000}
        ident=self.app.scheduler.add(program,'record')
        self.assertTrue(self.app.launch(program,'record',schedule_id=ident))
        job=next(iter(self.app.jobs.values()))
        for busy,restarting in ((True,False),(False,True)):
            job['controller'].busy=busy;job['restart_pending']=restarting
            with patch('nicoview.studio_ui.time.time',return_value=4600),patch.object(self.root,'after'):
                self.app.schedule_tick()
            self.assertEqual([r['id'] for r in self.app.scheduler.snapshot()],[ident])

    def test_queued_launch_cannot_start_after_recording_deadline(self):
        self.app.settings.update(recording_dir=self.temp.name,ffmpeg_path=__file__)
        program={'url':'https://live.nicovideo.jp/watch/lv1','start':1000}
        ident=self.app.scheduler.add(program,'record')
        self.app.scheduler.records[0]['state']='queued'
        self.app.events.put(('launch',(ident,program,'record')))
        with patch('nicoview.studio_ui.time.time',return_value=4600),patch.object(self.root,'after'):
            self.app.poll()
        self.assertEqual(self.app.jobs,{})
        self.assertEqual(self.app.scheduler.snapshot(),[])
        self.assertEqual(self.app.store.load_state()['reservations'],[])

    def test_late_push_cannot_queue_expired_recording(self):
        self.app.settings.update(recording_dir=self.temp.name,ffmpeg_path=__file__)
        program={'url':'https://live.nicovideo.jp/watch/lv1','id':'lv1','start':1000}
        self.app.scheduler.add(program,'record')
        with patch('nicoview.studio_ui.time.time',return_value=4600),patch('nicoview.studio_ui.parse_notification',return_value=program):
            self.app.handle_push({})
        self.assertEqual(self.app.scheduler.snapshot(),[])
        self.assertTrue(self.app.events.empty())

    def test_clear_execution_history_is_saved_and_recording_continues(self):
        self.app.settings.update(recording_dir=self.temp.name,ffmpeg_path=__file__)
        recording=Path(self.temp.name)/'recording.ts'
        recording.write_bytes(b'offline recording')
        self.app.scheduler.add({'url':'https://live.nicovideo.jp/watch/lv1'},'record')
        self.app.executions=[{'time':number+1,'title':'Earlier execution','status':'開始'}
                             for number in range(230)]
        self.assertTrue(self.app.launch({'url':'https://live.nicovideo.jp/watch/lv2'},'record'))
        job=next(iter(self.app.jobs.values()))
        self.app.history=[{'local_path':str(recording),'title':'Recording','position':42}]
        self.app.save_state()
        reservations=self.app.scheduler.snapshot()
        history=list(self.app.history)
        self.app.open_reservations()

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        widgets=list(descendants(self.app.reservation_window))
        button=next((w for w in widgets if isinstance(w,ttk.Button)
                     and w.cget('text')=='実行履歴をクリア'),None)
        self.assertIsNotNone(button)
        book=next(w for w in widgets if isinstance(w,ttk.Notebook))
        book.select(1);self.root.update()
        self.assertTrue(button.winfo_ismapped())
        self.assertLess(button.winfo_rootx()-self.app.reservation_window.winfo_rootx(),40)
        self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),book.winfo_rooty())
        self.assertGreater(len(self.app.executions),len(self.app.execution_tree.get_children()))
        button.invoke()
        self.assertEqual(self.app.executions,[])
        self.assertEqual(self.app.execution_tree.get_children(),())
        saved=self.app.store.load_state()
        self.assertEqual(saved['executions'],[])
        self.assertEqual(saved['reservations'],reservations)
        self.assertEqual(saved['history'],history)
        self.assertTrue(job['controller'].busy)
        self.assertTrue(recording.exists())
        self.app.execution(job,'完了');self.app.save_state();self.app.refresh_reservations()
        self.assertEqual(len(self.app.execution_tree.get_children()),1)
        self.assertEqual(self.app.store.load_state()['executions'][0]['status'],'完了')

    def test_same_program_allows_one_play_and_one_record(self):
        self.app.settings.update(recording_dir=self.temp.name,ffmpeg_path=__file__)
        p={'url':'https://live.nicovideo.jp/watch/lv1'}
        self.assertTrue(self.app.launch(p,'play'))
        self.assertFalse(self.app.launch({'url':p['url']+'?status=onair'},'play'))
        self.assertTrue(self.app.launch(p,'record'))
        self.assertFalse(self.app.launch(p,'record'))
        self.assertFalse(self.app.launch(p,'both'))
        self.assertEqual(len(self.app.jobs),2)

    def test_player_closed_removes_only_play_row_without_reconnect(self):
        self.app.settings.update(recording_dir=self.temp.name,ffmpeg_path=__file__)
        p={'url':'https://live.nicovideo.jp/watch/lv1'}
        self.app.launch(p,'play');ident=next(iter(self.app.jobs))
        self.app.launch(p,'record')
        job=self.app.jobs[ident];job['error']=True;job['controller'].busy=False
        with patch.object(self.root,'after') as after:
            self.app.handle_event(ident,{'type':'player_closed'})
            self.app.handle_event(ident,{'type':'stopped'})
            after.assert_not_called()
        self.assertNotIn(ident,self.app.jobs)
        self.assertFalse(self.app.tree.exists(ident))
        self.assertEqual([j['mode'] for j in self.app.jobs.values()],['record'])

    def test_retry_reuses_row_and_pending_retry_blocks_duplicate(self):
        p={'url':'https://live.nicovideo.jp/watch/lv1'}
        self.app.launch(p);ident=next(iter(self.app.jobs));job=self.app.jobs[ident]
        job['error']=True;job['controller'].busy=False
        with patch.object(self.root,'after'):
            self.app.handle_event(ident,{'type':'stopped'})
        self.assertFalse(self.app.launch(p))
        self.app.restart(ident)
        self.assertEqual(list(self.app.jobs),[ident])
        self.assertEqual(self.app.jobs[ident]['retries'],1)
        self.assertEqual(self.app.tree.get_children(),(ident,))

    def test_stop_during_retry_removes_row_and_cancels_retry(self):
        self.app.launch({'url':'https://live.nicovideo.jp/watch/lv1'})
        ident=next(iter(self.app.jobs));job=self.app.jobs[ident]
        job['error']=True;job['controller'].busy=False
        with patch.object(self.root,'after'):self.app.handle_event(ident,{'type':'stopped'})
        self.app.stop_job(job);self.app.restart(ident)
        self.assertEqual(self.app.jobs,{})

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=tk.Tk();self.root.withdraw()
        self.app=StudioApp(self.root,PreferenceStore(self.temp.name),FakeController,background=False)
        self.app.settings.update(player_path=__file__,svp_player_path=__file__,svp_enabled=True)
    def tearDown(self):self.app.shutdown();self.temp.cleanup()
    def test_only_first_player_has_svp_recording_does_not_consume_slot(self):
        self.app.settings.update(recording_dir=self.temp.name,ffmpeg_path=__file__)
        self.assertTrue(self.app.launch({'url':'https://live.nicovideo.jp/watch/lv1'},'record'))
        self.assertTrue(self.app.launch({'url':'https://live.nicovideo.jp/watch/lv2'},'play'))
        self.assertTrue(self.app.launch({'url':'https://live.nicovideo.jp/watch/lv3'},'play'))
        jobs=list(self.app.jobs.values())
        self.assertFalse(jobs[0]['controller'].settings['svp_enabled'])
        self.assertTrue(jobs[1]['controller'].settings['svp_enabled'])
        self.assertFalse(jobs[2]['controller'].settings['svp_enabled'])
        self.assertTrue(jobs[2]['controller'].settings['muted'])
    def test_limit_does_not_start_another_worker(self):
        self.app.settings['max_jobs']=1
        self.assertTrue(self.app.launch({'url':'https://live.nicovideo.jp/watch/lv1'}))
        self.assertFalse(self.app.launch({'url':'https://live.nicovideo.jp/watch/lv2'}))
        self.assertEqual(len(self.app.jobs),1)
    def test_position_is_saved_and_selected_job_controlled(self):
        self.app.launch({'url':'https://live.nicovideo.jp/watch/lv1','title':'test'})
        ident=next(iter(self.app.jobs));job=self.app.jobs[ident]
        self.app.handle_event(ident,{'type':'position','position':31,'duration':120,'paused':False,'seekable':True})
        self.app.save_state()
        self.assertEqual(self.app.store.load_state()['history'][0]['position'],31)
        self.app.selected_control('seek',45)
        self.assertEqual(job['controller'].control_args,('seek',45))

    def test_reservation_acknowledges_only_ready_and_retries_failed_start(self):
        program={'url':'https://live.nicovideo.jp/watch/lv1'}
        rid=self.app.scheduler.add(program,'play')
        self.app.launch(program,schedule_id=rid)
        self.assertNotEqual(self.app.scheduler.snapshot()[0]['state'],'started')
        ident=next(iter(self.app.jobs))
        self.app.handle_event(ident,{'type':'job_ready'})
        self.assertEqual(self.app.scheduler.snapshot()[0]['state'],'started')

    def test_stale_auth_failure_cannot_clear_new_login(self):
        self.app.account.login.set(True);self.app.account.browser_session=[{'value':'old'}]
        self.app.launch({'url':'https://live.nicovideo.jp/watch/lv1'})
        self.app.account.browser_session=[{'value':'new'}]
        with patch.object(self.app.account.session_store,'delete') as delete:
            self.app.handle_event(next(iter(self.app.jobs)),{'type':'session_invalid'})
            delete.assert_not_called()
        self.assertEqual(self.app.account.browser_session,[{'value':'new'}])

    def test_exit_rejects_late_launch(self):
        self.app.closing=True
        self.assertFalse(self.app.launch({'url':'https://live.nicovideo.jp/watch/lv1'}))

    def test_discovery_and_schedule_dialogs_accept_shell_options(self):
        with patch('nicoview.discovery_ui.Catalog'):
            self.app.open_discovery()
            self.app.open_schedule_picker()
            self.app.open_schedule_picker('record')
            titles=[w.title() for w in self.root.winfo_children() if isinstance(w,tk.Toplevel)]
            self.assertIn('番組を探す',titles)
            self.assertEqual(titles.count('予約'),2)

    def test_reservations_and_execution_history_columns_are_sortable(self):
        self.app.scheduler.add({'title':'B番組','url':'https://live.nicovideo.jp/watch/lv2','start':2000,'source':'公式タグ：ゲーム'},'record')
        self.app.scheduler.add({'title':'A番組','url':'https://live.nicovideo.jp/watch/lv1','start':3000},'play')
        self.app.executions=[
            {'time':1000,'title':'Z実行','status':'自動起動しました'},
            {'time':2000,'title':'A実行','status':'自動録画を開始しました'},
        ]
        self.app.open_reservations()
        self.app.sort_reservations('title')
        titles=[self.app.reservations_tree.item(i,'values')[2] for i in self.app.reservations_tree.get_children()]
        sources=[self.app.reservations_tree.item(i,'values')[3] for i in self.app.reservations_tree.get_children()]
        self.assertEqual(titles,['A番組','B番組'])
        self.assertEqual(sources,['手動','公式タグ：ゲーム'])
        self.assertTrue(self.app.reservations_tree.heading('title')['text'].endswith('▲'))
        self.app.sort_reservations('title')
        titles=[self.app.reservations_tree.item(i,'values')[2] for i in self.app.reservations_tree.get_children()]
        self.assertEqual(titles,['B番組','A番組'])
        self.assertTrue(self.app.reservations_tree.heading('title')['text'].endswith('▼'))
        self.app.sort_reservations('source')
        sources=[self.app.reservations_tree.item(i,'values')[3] for i in self.app.reservations_tree.get_children()]
        self.assertEqual(sources,['公式タグ：ゲーム','手動'])
        self.app.sort_reservations('start')
        titles=[self.app.reservations_tree.item(i,'values')[2] for i in self.app.reservations_tree.get_children()]
        self.assertEqual(titles,['B番組','A番組'])
        self.app.sort_executions('title')
        exec_titles=[self.app.execution_tree.item(i,'values')[1] for i in self.app.execution_tree.get_children()]
        self.assertEqual(exec_titles,['A実行','Z実行'])
        self.app.sort_executions('time')
        exec_titles=[self.app.execution_tree.item(i,'values')[1] for i in self.app.execution_tree.get_children()]
        self.assertEqual(exec_titles,['Z実行','A実行'])

    def test_tray_minimize_hides_toplevels_and_show_stops_tray_icon(self):
        self.app.settings['tray']=True
        self.root.deiconify()
        self.app.open_reservations()
        self.root.update_idletasks()
        with patch('nicoview.studio_ui.TrayIcon') as tray_cls:
            tray=tray_cls.return_value
            tray.start.return_value=True
            self.app.close_requested()
            tray.start.assert_called_once()
            self.assertEqual(self.root.state(),'withdrawn')
            self.assertEqual(self.app.reservation_window.state(),'withdrawn')
            self.app.show()
            tray.stop.assert_called_once()
            self.assertEqual(self.root.state(),'normal')
            self.assertNotEqual(self.app.reservation_window.state(),'withdrawn')

if __name__=='__main__':unittest.main()
