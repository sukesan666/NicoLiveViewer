import tempfile
import tkinter as tk
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
            self.assertIn('自動起動の予約（再生）',titles)
            self.assertIn('自動録画の予約',titles)

if __name__=='__main__':unittest.main()
