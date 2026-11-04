"""Read-only NicoLive catalog. Schemas observed in official pages, October 2026."""
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import requests
from lxml import html
import os
from pathlib import Path
import threading
from .ui import app_dir
from .sessions import clean_cookies
from .thumbnail_cache import ThumbnailCache

BASE = 'https://live.nicovideo.jp'

class CatalogError(ValueError):
    pass

def parse_page(text):
    try:
        doc = html.fromstring(text)
        nodes = doc.xpath('//*[@id="DAT-csr-data"]/@data-value')
        if nodes:
            result = json.loads(nodes[0])['props']
        else:
            nodes = doc.xpath('//*[@id="embedded-data"]/@data-props')
            result = json.loads(nodes[0])
        if not isinstance(result, dict): raise ValueError()
        return result
    except (ValueError, KeyError, IndexError, TypeError):
        raise CatalogError('番組情報を取得できません。サイトの仕様変更またはログイン状態を確認してください。') from None

def normalize(raw):
    if not isinstance(raw, dict): return None
    p = raw.get('value', raw) if raw.get('type') == 'seed' else raw
    ident = p.get('nicoliveProgramId', '')
    if not re.fullmatch(r'lv\d+', str(ident)): return None
    supplier = p.get('supplier') or {}
    group = p.get('socialGroup') or {}
    start = p.get('beginTime') or 0
    try: start = float(start)
    except (ValueError, TypeError): start = 0
    if not math.isfinite(start): start = 0
    provider = p.get('providerType', '')
    thumbnail = p.get('listingThumbnail') or (p.get('screenshot') or {}).get('urlSet', {}).get('small') or (p.get('thumbnail') or {}).get('small', '')
    channel_id = str(group.get('id') or '') if (group.get('type') == 'channel' or str(group.get('id','')).startswith('ch')) else ''
    if not channel_id and supplier.get('supplierType') == 'channel' and str(supplier.get('programProviderId','')).startswith('ch'):
        channel_id = str(supplier.get('programProviderId'))
    return dict(id=ident, url=BASE+'/watch/'+ident, title=str(p.get('title', ident))[:1000],
        author=str(supplier.get('name') or group.get('name') or '')[:500], thumbnail=thumbnail,
        start=start, status={'RELEASED':'RESERVED','BEFORE_OPEN':'RESERVED'}.get(p.get('status'),p.get('status','UNKNOWN')),
        provider='user' if provider=='community' else provider,
        user_id=str(supplier.get('programProviderId') or '') if provider in ('community','user') else '',
        channel_id=channel_id)

def _programs(items):
    result = {}
    for item in items:
        p = normalize(item)
        if p: result.setdefault(p['id'], p)
    return list(result.values())

class Catalog:
    def __init__(self, cookies):
        self.cookies = clean_cookies(cookies)
        self.session = requests.Session()
        self.session.headers.update({'User-Agent':'Mozilla/5.0 NicoLiveStudio/1.0','X-Frontend-Id':'9','X-Frontend-Version':'0'})
        for c in self.cookies:
            self.session.cookies.set(c['name'],c['value'],domain=c['domain'],path='/',secure=True,expires=c['expires'])

    def close(self):
        self.session.cookies.clear()
        self.session.close()
        self.cookies.clear()

    def _read(self, url, params=None):
        if urlparse(url).hostname not in ('live.nicovideo.jp','nvapi.nicovideo.jp','ch.nicovideo.jp'):
            raise CatalogError('対応していない接続先です。')
        try:
            with self.session.get(url,params=params,timeout=(8,20),allow_redirects=False,stream=True) as r:
                if r.status_code in (301,302,303,307,308,401,403):
                    raise CatalogError('ログインが必要です。ブラウザでログインし直してください。')
                r.raise_for_status()
                data=bytearray()
                for chunk in r.iter_content(65536):
                    data.extend(chunk)
                    if len(data)>8*1024*1024: raise CatalogError('番組情報が大きすぎるため読み込みを中止しました。')
                return bytes(data).decode('utf-8')
        except requests.RequestException:
            raise CatalogError('ニコニコに接続できません。通信状態を確認して再試行してください。') from None

    def _page(self, path='', params=None):
        return parse_page(self._read(BASE+'/'+path.lstrip('/'),params))

    @staticmethod
    def _logged(data):
        if not data.get('user',{}).get('isLoggedIn'):
            raise CatalogError('ログインが必要です。ブラウザでログインし直してください。')

    def program(self, url):
        url=str(url).strip()
        if re.fullmatch(r'lv\d+',url): ident=url
        else:
            p=urlparse(url)
            if p.scheme!='https' or p.hostname not in ('live.nicovideo.jp','live2.nicovideo.jp') or p.username or p.password or p.port not in (None,443):
                raise CatalogError('ニコニコ生放送の番組URLを入力してください。')
            m=re.fullmatch(r'/watch/(lv\d+)/?',p.path)
            if not m: raise CatalogError('番組URLに lv で始まる番組番号がありません。')
            ident=m.group(1)
        d=self._page('watch/'+ident)
        raw=dict(d.get('program') or {})
        raw.setdefault('socialGroup',d.get('socialGroup') or {})
        result=normalize(raw)
        if not result: raise CatalogError('番組情報が見つかりません。')
        return result

    def _top_list(self, key, login=False):
        d=self._page()
        if login:self._logged(d)
        state=d.get('view',{}).get(key)
        if not isinstance(state,dict) or state.get('hasError'):
            raise CatalogError('番組一覧を取得できません。しばらくして再試行してください。')
        return _programs(state.get('items',[]))

    def recommended(self): return self._top_list('recommendedProgramListSectionState',True)
    def following(self): return self._top_list('favoriteProgramListSectionState',True)
    def missed(self): return self._top_list('favoritePastProgramListSectionState',True)
    def following_live_users(self):
        return [p for p in self.following() if p['provider']=='user' and p['status']=='ON_AIR']

    def _ranked_programs(self):
        d=self._page('ranking').get('ranking',{})
        # The site publishes separate user and official/channel rankings.
        items=[]
        for key in ('userPrograms','officialAndChannelPrograms'):
            value=d.get(key)
            if not isinstance(value,list):
                raise CatalogError('番組一覧を取得できません。しばらくして再試行してください。')
            items.extend(value)
        return _programs(items)

    def ranking(self):
        return [p for p in self._ranked_programs() if p['provider'] in ('user','channel')]

    def official_live(self):
        return [p for p in self._ranked_programs() if p['provider']=='official' and p['status']=='ON_AIR']

    def search(self, query, is_tag=False, provider=None, statuses=('onair','reserved','past')):
        query=str(query).strip()[:200]
        if not query:return []
        result=[]
        for status in statuses:
            params={'keyword':query,'status':status}
            if is_tag:params['isTagSearch']='true'
            if provider:params['providerTypes']=provider
            d=self._page('search',params)
            try:
                search=d['searchResult'];data=search['statusData'];state=data[status]
                programs=state['programs']
                if not isinstance(programs,list) or any(part.get('hasError') for part in (search,data,state)):
                    raise ValueError()
            except (KeyError,TypeError,AttributeError,ValueError):
                raise CatalogError('番組一覧を取得できません。しばらくして再試行してください。') from None
            result.extend(programs)
            time.sleep(0.08)
        return _programs(result)

    def official_week(self):
        now=time.time(); end=now+7*86400
        today=datetime.fromtimestamp(now,timezone(timedelta(hours=9)))
        result=[]
        for offset in range(8):
            d=self._page('timetable',{'date':(today+timedelta(days=offset)).strftime('%Y%m%d')})
            programs=d.get('view',{}).get('programs')
            if not isinstance(programs,list):
                raise CatalogError('公式番組表を取得できません。更新して再試行してください。')
            result.extend(programs)
        return sorted((p for p in _programs(result) if p['provider']=='official' and now<=p['start']<end),key=lambda p:p['start'])

    def followed_users(self):
        if not self.cookies:raise CatalogError('フォロー中のユーザーにはログインが必要です。')
        result=[]; cursor=None
        for page in range(1,51):
            params={'pageSize':100,'page':page,'_frontendId':6,'_frontendVersion':'0'}
            if cursor:params['cursor']=cursor
            try:d=json.loads(self._read('https://nvapi.nicovideo.jp/v1/users/me/following/users',params))
            except (ValueError,TypeError):raise CatalogError('フォローユーザー情報を読み込めません。') from None
            if d.get('meta',{}).get('status')!=200:raise CatalogError('フォローユーザーの取得に失敗しました。ログインを確認してください。')
            data=d.get('data',{})
            for item in data.get('items',[]):
                user=item.get('user',item)
                ident=str(user.get('id',''))
                if not ident.isdigit():continue
                name=str(user.get('nickname',user.get('name',ident)))[:500]
                result.append(dict(id='',url='',title=name,author=name,thumbnail=(user.get('icons') or {}).get('large',''),start=0,status='USER_RULE',provider='user',user_id=ident))
            summary=data.get('summary',{})
            if not summary.get('hasNext'):return result
            cursor=summary.get('cursor')
        raise CatalogError('フォロー数が多いため全件を取得できませんでした。ユーザーURLで指定してください。')

    def channel_info(self, ch_id):
        ident = ch_id if ch_id.startswith('ch') else f'ch{ch_id}'
        try:
            raw = self._read(f'https://ch.nicovideo.jp/{ident}')
            doc = html.fromstring(raw)
            name_nodes = doc.xpath('//meta[@property="og:title"]/@content') or doc.xpath('//title/text()')
            icon_nodes = doc.xpath('//meta[@property="og:image"]/@content')
            name = name_nodes[0].split(' - ニコニコチャンネル')[0].strip() if name_nodes else ident
            icon = icon_nodes[0] if icon_nodes else ''
            return dict(id='', url=f'https://ch.nicovideo.jp/{ident}', title=name, author=name, thumbnail=icon, start=0, status='USER_RULE', provider='channel', channel_id=ident)
        except Exception:
            return dict(id='', url=f'https://ch.nicovideo.jp/{ident}', title=ident, author=ident, thumbnail='', start=0, status='USER_RULE', provider='channel', channel_id=ident)

    def followed_channels(self, limit_pages=5):
        import re
        channels = []
        seen = set()
        for page in range(1, limit_pages + 1):
            url = f'https://ch.nicovideo.jp/my/following?page={page}'
            try:
                raw = self._read(url)
                doc = html.fromstring(raw)
                items = doc.xpath('//ul[contains(@class,"items")]/li[contains(@class,"item")]')
                if not items:
                    break
                found_new = False
                for item in items:
                    links = item.xpath('.//a[contains(@class,"channel_name")]/@href') or item.xpath('.//a/@href')
                    if not links:
                        continue
                    m = re.search(r'/(ch[0-9]+)', links[0])
                    if not m:
                        continue
                    ch_id = m.group(1)
                    if ch_id in seen:
                        continue
                    seen.add(ch_id)
                    found_new = True

                    name_nodes = item.xpath('.//a[contains(@class,"channel_name")]/text()')
                    img_nodes = item.xpath('.//img')
                    img_alt = img_nodes[0].get('alt', '') if img_nodes else ''
                    img_src = img_nodes[0].get('src', '') if img_nodes else ''
                    title = name_nodes[0].strip() if name_nodes and name_nodes[0].strip() else img_alt or ch_id

                    desc_nodes = item.xpath('.//span[contains(@class,"channel_detail")]/text()')
                    desc = desc_nodes[0].strip() if desc_nodes else ''

                    channels.append(dict(
                        id=ch_id,
                        channel_id=ch_id,
                        title=title,
                        name=title,
                        owner_name=title,
                        thumbnail_url=img_src,
                        thumbnail=img_src,
                        description=desc,
                        url=f'https://ch.nicovideo.jp/{ch_id}',
                        status='channel',
                        type='channel'
                    ))
                if not found_new:
                    break
            except Exception:
                break
        return channels


_THUMB_SESSION=None
_CLEANED_THUMBS=False
_THUMB_CACHES={}
_THUMB_CACHE_LOCK=threading.RLock()
_THUMB_NETWORK_LOCK=threading.Lock()


def thumbnail_dir():
    d=app_dir()/'thumbnails'
    try:d.mkdir(parents=True,exist_ok=True)
    except OSError:pass
    return d


def _thumbnail_cache():
    directory=thumbnail_dir()
    with _THUMB_CACHE_LOCK:
        key=str(directory.resolve())
        if key not in _THUMB_CACHES:_THUMB_CACHES[key]=ThumbnailCache(directory)
        return _THUMB_CACHES[key]


def clean_old_thumbnails(days=30):
    global _CLEANED_THUMBS
    with _THUMB_CACHE_LOCK:
        if _CLEANED_THUMBS:return
        _CLEANED_THUMBS=True
        cache=_thumbnail_cache();cache.max_age_days=days
    threading.Thread(target=cache.prune,daemon=True,name='clean-thumbnails').start()


def thumbnail_bytes(url,cache_only=False,cancel=None):
    """Read disk first; concurrent requests for one URL share one download."""
    try:
        p=urlparse(str(url));host=p.hostname or ''
        if p.scheme!='https' or p.username or p.password or p.port not in (None,443) or not any(host==d or host.endswith('.'+d) for d in ('nimg.jp','nicovideo.jp')):
            return None
    except ValueError:return None
    clean_old_thumbnails(30)
    cache=_thumbnail_cache()
    return cache.read(url) if cache_only else cache.get(url,_download_thumbnail,cancel=cancel)


def _download_thumbnail(url,cancelled=lambda:False):
    # requests.Session is shared; serialize network use across open list windows.
    global _THUMB_SESSION
    with _THUMB_NETWORK_LOCK:
        if cancelled():return None
        if _THUMB_SESSION is None:_THUMB_SESSION=requests.Session()
        try:
            time.sleep(0.06)
            if cancelled():return None
            with _THUMB_SESSION.get(url,timeout=(4,6),allow_redirects=False,stream=True) as r:
                if r.status_code!=200 or not r.headers.get('Content-Type','').startswith('image/'):return None
                data=bytearray()
                for part in r.iter_content(32768):
                    data.extend(part)
                    if len(data)>2*1024*1024:return None
                return bytes(data) or None
        except requests.RequestException:return None
