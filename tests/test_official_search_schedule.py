from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, patch

from nicoview.scheduler import Scheduler, next_official_search
from nicoview.settings import clean_settings

JST=timezone(timedelta(hours=9))


def stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=JST).timestamp()


class OfficialSearchScheduleTests(unittest.TestCase):
    def scheduler(self):
        with patch('nicoview.scheduler.time.time', return_value=stamp('2026-11-01T23:00:00')):
            scheduler=Scheduler()
            scheduler.add({'keyword':'ABC'},'record')
            scheduler.add({'official_tag':'舞台'},'play')
        return scheduler

    def catalog(self):
        catalog=Mock()
        catalog.official_week.return_value=[]
        catalog.search.return_value=[]
        return catalog

    def test_no_on_off_setting_and_legacy_true_is_discarded(self):
        self.assertNotIn('reservation_polling',clean_settings({}))
        self.assertNotIn('reservation_polling',clean_settings({'reservation_polling':True}))

    def test_calendar_rolls_daily_and_includes_exact_due_time(self):
        for current,expected in (('2026-11-06T03:00:00','2026-11-07T03:00:00'),
                                 ('2026-11-08T03:00:00','2026-11-09T03:00:00'),
                                 ('2026-12-31T03:00:00','2027-01-01T03:00:00')):
            self.assertEqual(next_official_search(stamp(current)),stamp(expected))
        self.assertEqual(next_official_search(stamp('2026-11-02T03:00:00'),inclusive=True),stamp('2026-11-02T03:00:00'))
        utc=datetime(2026,11,1,18,tzinfo=timezone.utc).timestamp()
        self.assertEqual(next_official_search(utc,inclusive=True),stamp('2026-11-02T03:00:00'))

    def test_every_day_at_3am_jst_only(self):
        scheduler=self.scheduler();catalog=self.catalog()
        scheduler.tick(catalog,Mock(),now=stamp('2026-11-02T02:59:59'))
        catalog.official_week.assert_not_called();catalog.search.assert_not_called()
        for count,day in enumerate(range(2,9),1):
            due=stamp(f'2026-11-{day:02}T03:00:00')
            scheduler.tick(catalog,Mock(),now=due)
            scheduler.tick(catalog,Mock(),now=due+1)
            scheduler.tick(catalog,Mock(),now=due+86399)
            self.assertEqual(catalog.official_week.call_count,count)
            self.assertEqual(catalog.search.call_count,count)

    def test_only_failed_search_retries_hourly_until_success(self):
        scheduler=self.scheduler();catalog=self.catalog()
        catalog.official_week.side_effect=ValueError('unavailable')
        for date in ('2026-11-02T03:00:00','2026-11-02T03:59:59','2026-11-02T04:00:00','2026-11-02T04:59:59'):
            scheduler.tick(catalog,Mock(),now=stamp(date))
        self.assertEqual(catalog.official_week.call_count,2)
        self.assertEqual(catalog.search.call_count,1)
        catalog.official_week.side_effect=None
        scheduler.tick(catalog,Mock(),now=stamp('2026-11-02T05:00:00'))
        scheduler.tick(catalog,Mock(),now=stamp('2026-11-02T06:00:00'))
        self.assertEqual(catalog.official_week.call_count,3)
        self.assertEqual(catalog.search.call_count,1)
        self.assertEqual(scheduler.snapshot()[0]['search_next_at'],stamp('2026-11-03T03:00:00'))

    def test_retry_deadline_and_completed_search_survive_restart(self):
        scheduler=self.scheduler();catalog=self.catalog()
        catalog.search.side_effect=ValueError('unavailable')
        scheduler.tick(catalog,Mock(),now=stamp('2026-11-02T03:00:00'))
        scheduler=Scheduler(scheduler.snapshot())
        scheduler.tick(catalog,Mock(),now=stamp('2026-11-02T03:30:00'))
        self.assertEqual(catalog.search.call_count,1)
        scheduler.tick(catalog,Mock(),now=stamp('2026-11-02T04:00:00'))
        self.assertEqual(catalog.search.call_count,2)
        self.assertEqual(catalog.official_week.call_count,1)

    def test_program_and_user_states_are_never_polled_even_when_search_is_due(self):
        scheduler=self.scheduler();catalog=self.catalog();launch=Mock()
        scheduler.add({'url':'https://live.nicovideo.jp/watch/lv1'},'record')
        scheduler.add({'user_id':'123'},'play')
        scheduler.tick(catalog,launch,now=stamp('2026-11-02T03:00:00'))
        catalog.program.assert_not_called();catalog.following.assert_not_called();launch.assert_not_called()

    def test_overdue_search_runs_once_on_resume_and_clock_rollback_does_not_repeat(self):
        scheduler=self.scheduler();catalog=self.catalog()
        scheduler.tick(catalog,Mock(),now=stamp('2026-11-03T11:00:00'))
        scheduler=Scheduler(scheduler.snapshot())
        scheduler.tick(catalog,Mock(),now=stamp('2026-11-02T03:00:00'))
        self.assertEqual(catalog.official_week.call_count,1)

    def test_setup_failure_defers_only_due_searches_for_an_hour(self):
        scheduler=self.scheduler()
        now=stamp('2026-11-02T03:00:00')
        scheduler.defer_searches(now)
        self.assertFalse(scheduler.search_due(now+3599))
        self.assertTrue(scheduler.search_due(now+3600))

    def test_legacy_weekly_deadlines_move_to_daily_without_losing_overdue_search(self):
        with patch('nicoview.scheduler.time.time',return_value=stamp('2026-11-03T12:00:00')):
            scheduler=Scheduler([
                {'keyword':'ABC','mode':'record','search_next_at':stamp('2026-11-06T02:00:00')},
                {'official_tag':'舞台','mode':'play','search_next_at':stamp('2026-11-02T02:00:00')}])
        rows=scheduler.snapshot()
        self.assertEqual(rows[0]['search_next_at'],stamp('2026-11-04T03:00:00'))
        self.assertEqual(rows[1]['search_next_at'],stamp('2026-11-02T03:00:00'))
        self.assertTrue(scheduler.search_due(stamp('2026-11-03T12:00:00')))

    def test_legacy_two_am_deadline_waits_until_three_on_migration(self):
        with patch('nicoview.scheduler.time.time',return_value=stamp('2026-11-02T02:30:00')):
            scheduler=Scheduler([{'keyword':'ABC','mode':'record','search_next_at':stamp('2026-11-02T02:00:00')}])
        self.assertFalse(scheduler.search_due(stamp('2026-11-02T02:59:59')))
        self.assertTrue(scheduler.search_due(stamp('2026-11-02T03:00:00')))

    def test_legacy_hourly_retry_at_two_am_survives_repeated_restarts(self):
        rows=[{'keyword':'ABC','mode':'record','search_next_at':stamp('2026-11-06T02:00:00'),
               'message':'公式番組表を取得できません。1時間後に再試行します。'}]
        with patch('nicoview.scheduler.time.time',return_value=stamp('2026-11-06T01:30:00')):
            scheduler=Scheduler(rows)
            scheduler=Scheduler(scheduler.snapshot())
            scheduler=Scheduler(scheduler.snapshot())
        self.assertFalse(scheduler.search_due(stamp('2026-11-06T01:59:59')))
        self.assertTrue(scheduler.search_due(stamp('2026-11-06T02:00:00')))

    def test_daily_search_auto_registers_new_programs_without_duplicate_reservations(self):
        scheduler=self.scheduler();catalog=self.catalog()
        first={'id':'lv1','url':'https://live.nicovideo.jp/watch/lv1','title':'ABC program','provider':'official','status':'RESERVED'}
        second=dict(first,id='lv2',url='https://live.nicovideo.jp/watch/lv2')
        catalog.official_week.return_value=[first]
        scheduler.tick(catalog,Mock(),now=stamp('2026-11-02T03:00:00'))
        catalog.official_week.return_value=[first,second]
        scheduler.tick(catalog,Mock(),now=stamp('2026-11-03T03:00:00'))
        scheduler.tick(catalog,Mock(),now=stamp('2026-11-03T03:00:01'))
        self.assertEqual([r['url'] for r in scheduler.snapshot() if r.get('url')],[first['url'],second['url']])

    def test_malformed_tag_result_is_a_failure_but_valid_empty_result_is_success(self):
        from nicoview.catalog import Catalog, CatalogError
        catalog=Catalog([])
        try:
            for data in ({}, {'searchResult':{'statusData':{'onair':{'hasError':True,'programs':[]}}}}):
                with self.subTest(data=data), patch.object(catalog,'_page',return_value=data):
                    with self.assertRaises(CatalogError):catalog.search('test',statuses=('onair',))
            with patch.object(catalog,'_page',return_value={'searchResult':{'statusData':{'onair':{'programs':[]}}}}):
                self.assertEqual(catalog.search('test',statuses=('onair',)),[])
        finally:catalog.close()
