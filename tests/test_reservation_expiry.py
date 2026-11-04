import unittest
from unittest.mock import Mock

from nicoview.scheduler import Scheduler


class RecordingReservationExpiryTests(unittest.TestCase):
    def test_one_hour_boundary_removes_single_program_and_blocks_late_push(self):
        scheduler=Scheduler()
        ident=scheduler.add({'url':'https://live.nicovideo.jp/watch/lv1','start':1000},'record')
        self.assertEqual(scheduler.expire_recordings(now=4599),[])
        removed=scheduler.expire_recordings(now=4600)
        self.assertEqual([r['id'] for r in removed],[ident])
        self.assertEqual(scheduler.snapshot(),[])
        launch=Mock()
        scheduler.notify_live({'id':'lv1','url':'https://live.nicovideo.jp/watch/lv1','start':1000},launch,now=4601)
        launch.assert_not_called()

    def test_recording_both_and_idle_queued_reservations_expire(self):
        scheduler=Scheduler()
        expected=[]
        for number,mode in enumerate(('play','record','both'),1):
            expected.append(scheduler.add({'url':f'https://live.nicovideo.jp/watch/lv{number}','start':1000},mode))
        scheduler.records[1]['state']='queued'
        self.assertEqual({r['id'] for r in scheduler.expire_recordings(now=4600)},set(expected))

    def test_recurring_rules_unknown_dates_and_finished_states_are_kept(self):
        scheduler=Scheduler()
        for program,mode in (({'user_id':'123','start':1000},'record'),
                             ({'channel_id':'ch67','start':1000},'record'),
                             ({'keyword':'offline','start':1000},'record'),
                             ({'official_tag':'offline','start':1000},'record'),
                             ({'url':'https://live.nicovideo.jp/watch/lv1'},'record'),
                             ({'url':'https://live.nicovideo.jp/watch/lv2','start':5000},'record')):
            scheduler.add(program,mode)
        for number,state in enumerate(('started','ended','disabled'),4):
            scheduler.add({'url':f'https://live.nicovideo.jp/watch/lv{number}','start':1000},'record')
            scheduler.records[-1]['state']=state
        before=scheduler.snapshot()
        self.assertEqual(scheduler.expire_recordings(now=4600),[])
        self.assertEqual(scheduler.snapshot(),before)

    def test_active_recording_is_protected_and_failed_push_is_discarded_on_expiry(self):
        scheduler=Scheduler()
        protected=scheduler.add({'url':'https://live.nicovideo.jp/watch/lv1','start':1000},'record')
        failed=scheduler.add({'url':'https://live.nicovideo.jp/watch/lv2','start':1000},'record')
        launch=Mock()
        for number in (1,2):
            scheduler.notify_live({'id':f'lv{number}','url':f'https://live.nicovideo.jp/watch/lv{number}','start':1000},launch,
                                  preflight=lambda *_:(False,'busy'),now=4500)
        self.assertEqual({r['id'] for r in scheduler.expire_recordings(now=4600,protected_ids={protected})},{failed})
        self.assertNotIn(failed,scheduler.pending_push)
        self.assertIn(protected,scheduler.pending_push)
        self.assertEqual([r['id'] for r in scheduler.snapshot()],[protected])
        scheduler.expire_recordings(now=4601)
        scheduler.retry_push(launch,now=4602)
        launch.assert_not_called()

    def test_restored_channel_program_with_success_evidence_is_preserved(self):
        for program in ({'id':'lv1'},{'url':'https://live.nicovideo.jp/watch/lv1'}):
            with self.subTest(program=program):
                scheduler=Scheduler()
                ident=scheduler.add({'url':'https://live.nicovideo.jp/watch/lv1','channel_id':'ch67','start':1000},'record')
                scheduler.ack(ident,True,'recording',program,now=1001)
                self.assertEqual(scheduler.snapshot()[0]['state'],'started')
                restored=Scheduler(scheduler.snapshot())
                self.assertEqual(restored.expire_recordings(now=5000),[])
                self.assertEqual([r['id'] for r in restored.snapshot()],[ident])

    def test_restored_unexecuted_reservation_is_removed(self):
        scheduler=Scheduler()
        scheduler.add({'url':'https://live.nicovideo.jp/watch/lv1','channel_id':'ch67','start':1000},'record')
        restored=Scheduler(scheduler.snapshot())
        self.assertEqual(len(restored.expire_recordings(now=4600)),1)
        self.assertEqual(restored.snapshot(),[])

    def test_waiting_channel_program_with_stale_seen_still_expires(self):
        restored=Scheduler([{'id':'stale','url':'https://live.nicovideo.jp/watch/lv1','channel_id':'ch67',
                             'start':1000,'mode':'record','state':'waiting','seen':['lv1']}])
        self.assertEqual([r['id'] for r in restored.expire_recordings(now=4600)],['stale'])
        self.assertEqual(restored.snapshot(),[])


if __name__=='__main__':unittest.main()
