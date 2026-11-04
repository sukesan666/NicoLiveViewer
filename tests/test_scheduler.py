import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta, timezone

NOW=datetime(2026,11,2,3,tzinfo=timezone(timedelta(hours=9))).timestamp()
from nicoview.scheduler import Scheduler
class Catalog:
    def program(self,url):return {'id':'lv1','url':url,'title':'test','status':'ON_AIR','start':100,'provider':'official'}
    def following(self):return [dict(self.program('https://live.nicovideo.jp/watch/lv1'),user_id='u1')]
class SchedulerTests(unittest.TestCase):
    def test_once_and_follow_rule_dedup(self):
        s=Scheduler();s.add({'id':'lv1','url':'https://live.nicovideo.jp/watch/lv1','start':100},'record')
        result=[];s.notify_live(Catalog().program('https://live.nicovideo.jp/watch/lv1'),lambda *a:result.append(a),now=101)
        s.notify_live(Catalog().program('https://live.nicovideo.jp/watch/lv1'),lambda *a:result.append(a),now=102)
        self.assertEqual(len(result),1)
        s.ack(result[0][0],True,'started',now=102)
        self.assertEqual(s.snapshot()[0]['state'],'started')
        s=Scheduler();s.add({'user_id':'u1','title':'user'},'both')
        result=[];s.notify_live(dict(Catalog().program('https://live.nicovideo.jp/watch/lv1'),user_id='u1'),lambda *a:result.append(a),now=101)
        s.ack(result[0][0],True,'started',program=result[0][1],now=102)
        s.notify_live(dict(Catalog().program('https://live.nicovideo.jp/watch/lv1'),user_id='u1'),lambda *a:result.append(a),now=140)
        self.assertEqual(len(result),1)
    def test_ended_never_launches(self):
        c=Catalog();c.program=lambda url:{'id':'lv1','url':url,'status':'ENDED'}
        s=Scheduler();s.add({'url':'https://live.nicovideo.jp/watch/lv1'},'play')
        result=[];s.tick(c,lambda *a:result.append(a),now=NOW)
        self.assertFalse(result);self.assertEqual(s.snapshot()[0]['state'],'waiting')
    def test_import_only_allowed_fields(self):
        s=Scheduler([{'url':'file:///x','mode':'record','cookie':'secret'}])
        self.assertFalse(s.snapshot())
    def test_corrupt_shapes_and_duplicate_saved_ids_are_safe(self):
        self.assertEqual(Scheduler({'unexpected':'object'}).snapshot(),[])
        row={'id':'same','url':'https://live.nicovideo.jp/watch/lv1','start':float('inf'),'seen':None}
        records=Scheduler([row,row]).snapshot()
        self.assertEqual(len(records),2)
        self.assertNotEqual(records[0]['id'],records[1]['id'])
        self.assertEqual(records[0]['start'],0)


class KeywordTests(unittest.TestCase):
    def setUp(self):
        clock=patch('nicoview.scheduler.time.time',return_value=NOW-3600)
        clock.start();self.addCleanup(clock.stop)

    def catalog(self):
        from unittest.mock import Mock
        c=Mock()
        c.official_week.return_value=[
            dict(id='lv1',url='https://live.nicovideo.jp/watch/lv1',title='公式 ＡＢＣ 配信',provider='official',status='RESERVED',start=200),
            dict(id='lv2',url='https://live.nicovideo.jp/watch/lv2',title='ABC channel',provider='channel',status='RESERVED',start=200),
            dict(id='lv3',url='https://live.nicovideo.jp/watch/lv3',title='別番組',provider='official',status='RESERVED',start=200)]
        c.program.side_effect=lambda url:next(p for p in c.official_week.return_value if p['url']==url)
        return c

    def test_keyword_matches_official_partial_normalized_title_and_persists(self):
        s=Scheduler();s.add({'keyword':'abc'},'record');c=self.catalog()
        s.tick(c,lambda *args:None,now=NOW)
        rows=s.snapshot();self.assertEqual(len(rows),2)
        self.assertEqual(rows[1]['url'],'https://live.nicovideo.jp/watch/lv1')
        self.assertEqual(rows[1]['mode'],'record')
        s=Scheduler(rows);self.assertEqual(len(s.snapshot()),2);s.remove(rows[1]['id'])
        s.tick(c,lambda *args:None,now=NOW+4*86400)
        self.assertEqual(len(s.snapshot()),1) # Deleted auto reservation stays deleted.

    def test_overlapping_keywords_dedup_per_mode_and_poll_interval(self):
        s=Scheduler();s.add({'keyword':'abc'},'play');s.add({'keyword':'公式'},'play');s.add({'keyword':'abc'},'record')
        c=self.catalog();s.tick(c,lambda *args:None,now=NOW);s.tick(c,lambda *args:None,now=NOW+30)
        children=[r for r in s.snapshot() if r['url']]
        self.assertEqual([r['mode'] for r in children],['play','record'])
        self.assertEqual(c.official_week.call_count,1)
        s.tick(c,lambda *args:None,now=NOW+4*86400)
        self.assertEqual(len(s.snapshot()),5)
        self.assertEqual(c.official_week.call_count,2)

    def test_keyword_fetch_failure_does_not_block_existing_reservation(self):
        s=Scheduler();s.add({'keyword':'abc'},'record');s.add({'url':'https://live.nicovideo.jp/watch/lv1'},'play')
        c=self.catalog();c.official_week.side_effect=ValueError('offline')
        c.program.side_effect=lambda url:dict(url=url,status='ON_AIR')
        launched=[];s.tick(c,lambda *args:launched.append(args),now=NOW)
        self.assertEqual(len(launched),0)
        s.notify_live({'url':'https://live.nicovideo.jp/watch/lv1'},lambda *args:launched.append(args),now=NOW)
        self.assertEqual(len(launched),1)
        self.assertIn('再試行',s.snapshot()[0]['message'])

    def test_blank_and_duplicate_keyword_rejected(self):
        s=Scheduler()
        with self.assertRaises(ValueError):s.add({'keyword':'  '},'record')
        s.add({'keyword':'ABC'},'record')
        with self.assertRaises(ValueError):s.add({'keyword':'ａｂｃ'},'record')

    def test_matching_program_launches_once_and_failed_lookup_retries(self):
        s=Scheduler();s.add({'keyword':'abc'},'record');c=self.catalog()
        c.official_week.side_effect=ValueError('temporary')
        s.tick(c,lambda *args:None,now=NOW)
        c.official_week.side_effect=None
        c.program.side_effect=lambda url:dict(url=url,id='lv1',status='ON_AIR')
        result=[];s.tick(c,lambda *args:result.append(args),now=NOW+3600)
        self.assertFalse(result)
        s.notify_live({'url':'https://live.nicovideo.jp/watch/lv1'},lambda *args:result.append(args),now=NOW+3600)
        self.assertEqual(len(result),1);self.assertEqual(result[0][2],'record')
        s.ack(result[0][0],True,'started')
        s=Scheduler(s.snapshot());s.tick(c,lambda *args:result.append(args),now=NOW+4*86400)
        self.assertEqual(len(result),1)

    def test_existing_manual_both_reservation_prevents_auto_duplicates(self):
        s=Scheduler();s.add({'url':'https://live.nicovideo.jp/watch/lv1'},'both')
        s.add({'keyword':'abc'},'play');s.add({'keyword':'abc'},'record')
        s.tick(self.catalog(),lambda *args:None,now=NOW)
        self.assertEqual(len(s.snapshot()),3)

    def test_official_tag_reservation_expands_and_deduplicates(self):
        s=Scheduler();s.add({'official_tag':'舞台'},'record')
        self.assertEqual(s.snapshot()[0]['title'],'公式タグ：舞台')
        c=self.catalog()
        c.search.return_value=[
            dict(id='lv10',url='https://live.nicovideo.jp/watch/lv10',title='公式 舞台 配信',provider='official',status='RESERVED',start=300),
            dict(id='lv11',url='https://live.nicovideo.jp/watch/lv11',title='非公式 舞台',provider='user',status='RESERVED',start=300)]
        s.tick(c,lambda *args:None,now=NOW)
        c.search.assert_called_with('舞台',is_tag=True,provider='official',statuses=('onair','reserved'))
        rows=s.snapshot()
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1]['url'],'https://live.nicovideo.jp/watch/lv10')
        self.assertEqual(rows[1]['mode'],'record')
        with self.assertRaises(ValueError):s.add({'official_tag':'  '},'play')
        with self.assertRaises(ValueError):s.add({'official_tag':'舞台'},'record')
