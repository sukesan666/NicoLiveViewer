import unittest
from unittest.mock import patch
from nicoview.catalog import Catalog, CatalogError, normalize, parse_page


class CatalogTests(unittest.TestCase):
    def test_normalize_verified_seed(self):
        p = normalize({'type':'seed','value':{'nicoliveProgramId':'lv123','title':'日本語',
            'beginTime':1234567890,'status':'RELEASED','providerType':'community',
            'supplier':{'name':'名前','programProviderId':'42'},'listingThumbnail':'https://example.invalid/a'}})
        self.assertEqual((p['id'],p['user_id'],p['status'],p['provider']),('lv123','42','RESERVED','user'))
        self.assertEqual(p['title'],'日本語')
        self.assertEqual(p['url'],'https://live.nicovideo.jp/watch/lv123')

    def test_reject_untrusted_url_before_request(self):
        c=Catalog([])
        for url in ['https://evil.test/watch/lv1','https://live.nicovideo.jp.evil.test/watch/lv1','file:///lv1']:
            with self.assertRaises(CatalogError):c.program(url)

    def test_page_schemas(self):
        self.assertEqual(parse_page('<script id="embedded-data" data-props="{&quot;program&quot;:{}}"></script>'),{'program':{}})
        self.assertEqual(parse_page('<script id="DAT-csr-data" data-value="{&quot;props&quot;:{&quot;view&quot;:{}}}"></script>'),{'view':{}})

    def test_week_filters_channels_and_sorts(self):
        c=Catalog([])
        def page(*args,**kwargs):
            return {'view':{'programs':[
                {'nicoliveProgramId':'lv1','beginTime':1002,'providerType':'official'},
                {'nicoliveProgramId':'lv2','beginTime':1001,'providerType':'channel'},
                {'nicoliveProgramId':'lv3','beginTime':1001,'providerType':'official'}]}}
        with patch.object(c,'_page',side_effect=page),patch('nicoview.catalog.time.time',return_value=1000):
            self.assertEqual([p['id'] for p in c.official_week()],['lv3','lv1'])

    def test_following_live_users_excludes_channels_official_and_non_live(self):
        c=Catalog([])
        items=[{'nicoliveProgramId':f'lv{i}', 'providerType':provider, 'status':status}
               for i,(provider,status) in enumerate([
                   ('community','ON_AIR'),('user','ON_AIR'),('channel','ON_AIR'),
                   ('official','ON_AIR'),('community','RELEASED'),('community','ENDED')],1)]
        data={'user':{'isLoggedIn':True},'view':{'favoriteProgramListSectionState':{'items':items}}}
        with patch.object(c,'_page',return_value=data):
            self.assertEqual([p['id'] for p in c.following_live_users()],['lv1','lv2'])
            self.assertEqual(len(c.following()),6)  # Scheduling retains its existing feed.
        c.close()

    def test_ranking_excludes_official_and_unknown_preserving_category_order(self):
        c=Catalog([])
        data={'ranking':{
            'officialAndChannelPrograms':[
                {'nicoliveProgramId':'lv1','providerType':'official','status':'ON_AIR'},
                {'nicoliveProgramId':'lv2','providerType':'channel','status':'ON_AIR'}],
            'userPrograms':[
                {'nicoliveProgramId':'lv3','providerType':'community','status':'ON_AIR'},
                {'nicoliveProgramId':'lv4','providerType':'community','status':'ON_AIR'},
                {'nicoliveProgramId':'lv5','providerType':'unknown','status':'ON_AIR'}]}}
        with patch.object(c,'_page',return_value=data):
            self.assertEqual([p['id'] for p in c.ranking()],['lv3','lv4','lv2'])
        c.close()

    def test_official_live_excludes_other_providers_and_non_live(self):
        c=Catalog([])
        data={'ranking':{'officialAndChannelPrograms':[
            {'nicoliveProgramId':f'lv{i}','providerType':provider,'status':status}
            for i,(provider,status) in enumerate([
                ('official','ON_AIR'),('channel','ON_AIR'),('official','ENDED'),
                ('official','RELEASED'),('official','ON_AIR')],1)],'userPrograms':[]}}
        with patch.object(c,'_page',return_value=data):
            self.assertEqual([p['id'] for p in c.official_live()],['lv1','lv5'])
        c.close()

    def test_week_requests_distinct_dates_in_site_format(self):
        from datetime import datetime, timezone, timedelta
        zone=timezone(timedelta(hours=9))
        now=datetime(2026,10,8,12,tzinfo=zone).timestamp()
        dates=[]
        def page(path,params):
            self.assertEqual(path,'timetable')
            dates.append(params['date'])
            # Like the live site, unrecognized dates return today's data.
            day=int(params['date'][-2:]) if len(params['date'])==8 else 8
            start=datetime(2026,10,day,13,tzinfo=zone).timestamp()
            return {'view':{'programs':[{'nicoliveProgramId':f'lv{day}',
                    'providerType':'official','beginTime':start}]}}
        c=Catalog([])
        with patch.object(c,'_page',side_effect=page),patch('nicoview.catalog.time.time',return_value=now):
            rows=c.official_week()
        self.assertEqual(dates,[f'202610{day:02}' for day in range(8,16)])
        self.assertEqual([p['id'] for p in rows],[f'lv{day}' for day in range(8,15)])
        c.close()

    def test_week_reports_missing_timetable_instead_of_empty_success(self):
        c=Catalog([])
        with patch.object(c,'_page',return_value={'view':{}}):
            with self.assertRaises(CatalogError):c.official_week()
        c.close()

    def test_personal_lists_require_actual_login(self):
        c=Catalog([])
        with patch.object(c,'_page',return_value={'user':{'isLoggedIn':False},'view':{}}):
            with self.assertRaises(CatalogError):c.following()


if __name__=='__main__':unittest.main()
