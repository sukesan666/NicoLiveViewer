"""Persistent reservation state machine. Network calls run off the Tk thread."""
import copy
import math
import re
import threading
import time
import uuid
import unicodedata
from datetime import datetime, timedelta, timezone
from .url import normalize_url

def keyword_text(value):
    return unicodedata.normalize('NFKC',str(value)).casefold().strip()

def reservation_key(item):
    """Key shared by picker rows and saved reservations: 'lv123', 'channel:ch456', or 'user:456'."""
    if not isinstance(item,dict) or item.get('keyword') or item.get('official_tag'):return ''
    ident=str(item.get('id') or '')
    if re.fullmatch(r'lv\d+',ident):return ident
    m=re.search(r'/watch/(lv\d+)',str(item.get('url') or ''))
    if m:return m.group(1)
    if item.get('channel_id'):
        ch=str(item['channel_id'])
        return 'channel:'+(ch if ch.startswith('ch') else 'ch'+ch)
    if ident.startswith('ch') and re.fullmatch(r'ch\d+',ident):
        return 'channel:'+ident
    if item.get('user_id'):return 'user:'+str(item['user_id'])
    m_ch=re.search(r'/(?:channels?/)?(ch\d+)',str(item.get('url') or ''))
    if m_ch:return 'channel:'+m_ch.group(1)
    return ''

JST=timezone(timedelta(hours=9))
SEARCH_CALENDAR='daily-03-jst'


def next_official_search(now, inclusive=False):
    """Next daily 03:00 in fixed Japan time, independent of host TZ."""
    local=datetime.fromtimestamp(now,JST)
    candidate=local.replace(hour=3,minute=0,second=0,microsecond=0)
    if candidate.timestamp()>now or inclusive and candidate.timestamp()==now:
        return candidate.timestamp()
    return (candidate+timedelta(days=1)).timestamp()


class Scheduler:
    def __init__(self,records=None):
        self.lock=threading.RLock();self.records=[];self.pending_push={}
        used=set()
        for r in (records if isinstance(records,list) else [])[:500]:
            try:
                ident=self.add(r,r.get('mode','play'),_restore=True)
                q=self.records[-1]
                saved_id=str(r.get('id',ident))[:100]
                q['id']=saved_id if saved_id and saved_id not in used else ident
                used.add(q['id'])
                q['state']=r.get('state','waiting') if r.get('state') in ('waiting','started','ended','disabled') else 'waiting'
                seen=r.get('seen',[])
                q['seen']=[str(x)[:100] for x in seen if isinstance(x,str)][-2000:] if isinstance(seen,list) else []
            except (ValueError,TypeError,AttributeError):pass
        rules=[r for r in self.records if r.get('keyword') or r.get('official_tag')]
        if rules:
            for r in self.records:
                if r.get('url') and not r.get('source'):
                    matched=next((rule for rule in rules if r['url'] in rule.get('seen',[]) and rule['mode'] in (r['mode'],'both')),None)
                    if not matched:matched=next((rule for rule in rules if r['url'] in rule.get('seen',[])),None)
                    if matched:r['source']=matched['title']
    def add(self,program,mode,_restore=False):
        if mode not in ('play','record','both'):raise ValueError('実行方法を選択してください。')
        url=program.get('url','');user=str(program.get('user_id',''))[:100]
        keyword=str(program.get('keyword','')).strip()
        official_tag=str(program.get('official_tag','')).strip()
        channel=str(program.get('channel_id') or '').strip()
        if not channel and user.startswith('ch') and re.fullmatch(r'ch[0-9]+', user):
            channel = user
            user = ''
        if not channel and url:
            m_ch = re.search(r'/(?:channels?/)?(ch[0-9]+)', url)
            if m_ch:
                channel = m_ch.group(1)
                url = ''
        if 'keyword' in program and (not keyword or len(keyword)>100 or url or user or official_tag or channel):
            raise ValueError('キーワードは1～100文字で入力してください。')
        if 'official_tag' in program and (not official_tag or len(official_tag)>100 or url or user or keyword or channel):
            raise ValueError('タグは1～100文字で入力してください。')
        if url:url=normalize_url(url)
        if not url and not user and not keyword and not official_tag and not channel:
            raise ValueError('番組またはユーザー、チャンネルを選択してください。')
        if not url and not keyword and not official_tag and not channel and not user.isdecimal():
            # tests and alternate provider IDs may use alphanumerics; never form URLs from it.
            if not user.isalnum():raise ValueError('ユーザーIDを確認してください。')
        start=program.get('start',0)
        try:start=max(0,float(start or 0))
        except (ValueError,TypeError):start=0
        if not math.isfinite(start) or start>4102444800:start=0
        channel_id=channel if re.fullmatch(r'ch[0-9]+',channel) else (f'ch{channel}' if re.fullmatch(r'[0-9]+',channel) and program.get('provider')=='channel' else '')
        item={'id':uuid.uuid4().hex,'url':url,'user_id':user if not url and not channel_id else '',
              'channel_id':channel_id,
              'title':str(program.get('title') or program.get('author') or channel_id or url or user)[:300],
              'start':start,'mode':mode,'state':'waiting','seen':[],'message':'待機中','retry_at':0,
              'source':str(program.get('source') or '')[:300] if url else ''}
        notify_user=str(program.get('notify_user_id') or user)
        item['notify_user_id']=notify_user if re.fullmatch(r'[0-9]{1,20}',notify_user) else ''
        notify_channel=str(program.get('notify_channel_id') or channel_id)
        item['notify_channel_id']=notify_channel if re.fullmatch(r'ch[0-9]+',notify_channel) else ''
        stamp=program.get('last_push_at',0)
        item['last_push_at']=stamp if isinstance(stamp,(int,float)) and math.isfinite(stamp) and stamp>=0 else 0
        if keyword or official_tag:
            now=time.time()
            deadline=program.get('search_next_at')
            if not isinstance(deadline,(int,float)) or isinstance(deadline,bool) or not math.isfinite(deadline) or not 0<deadline<=4102444800:
                deadline=next_official_search(now,inclusive=True)
            elif program.get('search_schedule')!=SEARCH_CALENDAR:
                previous=datetime.fromtimestamp(deadline,JST)
                retrying=str(program.get('message','')).endswith('1時間後に再試行します。')
                if (previous.weekday() in (0,4) and previous.hour==2
                        and previous.minute==previous.second==previous.microsecond==0 and not retrying):
                    # Pull future weekly dates forward; preserve one missed run,
                    # but never execute its old 02:00 slot before the new 03:00.
                    deadline=min(deadline+3600,next_official_search(now,inclusive=True))
            item['search_next_at']=deadline
            item['search_schedule']=SEARCH_CALENDAR
        if keyword:item.update(keyword=keyword,title='公式キーワード：'+keyword)
        if official_tag:item.update(official_tag=official_tag,title='公式タグ：'+official_tag)
        with self.lock:
            if keyword:
                if any(keyword_text(r.get('keyword',''))==keyword_text(keyword) and (r['mode']==mode or r['mode']=='both' or mode=='both') for r in self.records):
                    raise ValueError('このキーワードは同じ動作で登録済みです。')
            if official_tag:
                if any(keyword_text(r.get('official_tag',''))==keyword_text(official_tag) and (r['mode']==mode or r['mode']=='both' or mode=='both') for r in self.records):
                    raise ValueError('このタグは同じ動作で登録済みです。')
            key=reservation_key(item)
            if not _restore and key:
                if any(reservation_key(r)==key and (r['mode']==mode or r['mode']=='both' or mode=='both') for r in self.records):
                    raise ValueError('この番組・ユーザーは予約済みです。')
            if len(self.records)>=500:raise ValueError('予約は500件までです。')
            self.records.append(item)
        return item['id']
    def snapshot(self):
        with self.lock:return copy.deepcopy(self.records)
    def remove(self,ident):
        with self.lock:self.records=[r for r in self.records if r['id']!=ident]
    def expire_recordings(self,now=None,protected_ids=()):
        """Drop unexecuted single-program reservations one hour after their start."""
        now=time.time() if now is None else now
        protected=set(protected_ids)
        with self.lock:
            expired=[];kept=[]
            for r in self.records:
                url=r.get('url','')
                if (url and not r.get('keyword') and not r.get('official_tag')
                        and r['mode'] in ('play','record','both') and r['state'] in ('waiting','queued')
                        and r['start']>0 and r['start']+3600<=now
                        and r['id'] not in protected):
                    expired.append(r);self.pending_push.pop(r['id'],None)
                else:kept.append(r)
            self.records=kept
            return copy.deepcopy(expired)
    def keys(self,mode):
        """Program/user keys that already have a reservation covering mode."""
        with self.lock:
            return {k for k in (reservation_key(r) for r in self.records if r['mode'] in (mode,'both')) if k}
    def remove_key(self,key,mode):
        """Drop mode from reservations matching key; a 'both' keeps the other mode."""
        if not key:return
        with self.lock:
            kept=[]
            for r in self.records:
                if reservation_key(r)==key and r['mode'] in (mode,'both'):
                    if r['mode']=='both':
                        r['mode']='record' if mode=='play' else 'play';kept.append(r)
                    continue
                kept.append(r)
            self.records=kept
    def ack(self,ident,ok,message,program=None,now=None):
        now=time.time() if now is None else now
        with self.lock:
            r=next((r for r in self.records if r['id']==ident),None)
            if not r:return
            r['message']=str(message)[:300]
            is_rule=bool((r.get('user_id') or r.get('channel_id')) and not r.get('url'))
            if ok:
                pending=self.pending_push.get(ident)
                completed=str((program or {}).get('id') or (program or {}).get('url','').rsplit('/',1)[-1])
                if not is_rule or not pending or not program or pending['program']['id']==completed:
                    self.pending_push.pop(ident,None)
                r['retry_at']=0
                if is_rule:
                    if program:r['seen']=(r['seen']+[str(program.get('id') or program.get('url'))])[-200:]
                    r['state']='waiting'
                else:r['state']='started'
            else:
                r['state']='waiting';r['retry_at']=now+60
                if not is_rule:r['seen']=[]
    def search_due(self,now=None):
        now=time.time() if now is None else now
        return any((r.get('keyword') or r.get('official_tag')) and r['state']=='waiting'
                   and r['search_next_at']<=now for r in self.snapshot())

    def defer_searches(self,now):
        """Back off remaining due searches if setup/processing itself failed."""
        with self.lock:
            for rule in self.records:
                if (rule.get('keyword') or rule.get('official_tag')) and rule['state']=='waiting' and rule['search_next_at']<=now:
                    rule['search_next_at']=now+3600
                    rule['message']='公式検索に失敗しました。1時間後に再試行します。'

    def expand_rules(self,catalog,now):
        rules=[r for r in self.snapshot() if (r.get('keyword') or r.get('official_tag'))
               and r['state']=='waiting' and r['search_next_at']<=now]
        fetched={}
        for saved in rules:
            # Share a single timetable/tag request between rules due in this run.
            # A failed search is cached too; successful searches are not retried.
            key=('tag',saved['official_tag']) if saved.get('official_tag') else ('week','')
            if key not in fetched:
                try:
                    result=(catalog.search(key[1],is_tag=True,provider='official',statuses=('onair','reserved'))
                            if key[0]=='tag' else catalog.official_week())
                    if not isinstance(result,list):raise ValueError('Invalid catalog response')
                    fetched[key]=result
                except Exception:
                    fetched[key]=None
            programs=fetched[key]
            with self.lock:
                rule=next((r for r in self.records if r['id']==saved['id']),None)
                if not rule or rule['state']!='waiting' or rule['search_next_at']>now:continue
                if programs is None:
                    rule['search_next_at']=now+3600
                    rule['message']=('公式タグ検索に失敗しました。1時間後に再試行します。' if key[0]=='tag' else
                                     '公式番組表を取得できません。1時間後に再試行します。')
                    continue
                added=0;full=False
                for program in programs:
                    if not isinstance(program,dict) or program.get('provider')!='official' or program.get('status')=='ENDED':continue
                    if rule.get('keyword') and keyword_text(rule['keyword']) not in keyword_text(program.get('title','')):continue
                    try:url=normalize_url(program.get('url',''))
                    except ValueError:continue
                    if url in rule['seen']:continue
                    wanted={'play','record'} if rule['mode']=='both' else {rule['mode']}
                    occupied=set()
                    for existing in self.records:
                        if existing['url']==url:occupied.update({'play','record'} if existing['mode']=='both' else {existing['mode']})
                    missing=wanted-occupied
                    if missing:
                        mode='both' if len(missing)==2 else next(iter(missing))
                        try:self.add(dict(program,source=rule['title']),mode)
                        except ValueError:
                            full=True;break
                        added+=1
                    rule['seen']=(rule['seen']+[url])[-2000:]
                rule['search_next_at']=next_official_search(now)
                rule['message']='予約が上限です。不要な予約を削除してください。' if full else f'定期検索完了・今回{added}件を追加'

    def expand_keywords(self,catalog,now):
        self.expand_rules(catalog,now)

    def tick(self,catalog,launch=None,now=None,preflight=None):
        """Scheduled discovery only. A catalog response never starts playback."""
        self.expand_rules(catalog,time.time() if now is None else now)

    def notify_live(self, program_data, launch, preflight=None, now=None):
        """Called when a live broadcast starts via WebPush notification."""
        now=time.time() if now is None else now
        url=str(program_data.get('url') or '')
        try:
            clean=normalize_url(url or str(program_data.get('id') or ''))
        except ValueError:return
        lv_id=clean.rsplit('/',1)[-1]
        if program_data.get('id') and program_data['id']!=lv_id:return
        clean_url=f"https://live.nicovideo.jp/watch/{lv_id}"
        user_id=str(program_data.get('user_id') or '')
        channel_id=str(program_data.get('channel_id') or '')
        matched=[]
        with self.lock:
            for r in self.records:
                if r['state'] not in ('waiting','queued'):continue
                if r.get('url'):
                    if reservation_key(r)==lv_id and r['state']=='waiting':matched.append(r)
                elif user_id and r.get('user_id')==user_id:
                    if clean_url not in r['seen'] and lv_id not in r['seen']:
                        matched.append(r)
                elif channel_id and (r.get('channel_id')==channel_id or r.get('channel_id')==channel_id.lstrip('ch') or r.get('user_id')==channel_id):
                    if clean_url not in r['seen'] and lv_id not in r['seen']:
                        matched.append(r)
        for r in matched:
            p={
                'id': lv_id,
                'url': clean_url,
                'title': program_data.get('title') or r.get('title') or clean_url,
                'status': 'ON_AIR',
                'start': program_data.get('start',now),
                'user_id': user_id,
                'channel_id': channel_id,
                'author': program_data.get('author') or program_data.get('user_name') or ''
            }
            with self.lock:
                if p['start']<r.get('last_push_at',0):continue
                r['last_push_at']=p['start']
                previous=self.pending_push.get(r['id'])
                if previous and previous['program']['id']!=lv_id:self.pending_push.pop(r['id'],None)
                pending=self.pending_push.setdefault(r['id'],{'program':p,'expires':min(now+600,program_data.get('expires_at',now+600))})
                if pending['expires']<=now:
                    self.pending_push.pop(r['id'],None);continue
                if r['state']!='waiting':continue
                if r.get('retry_at',0)>now:continue
            if preflight:
                ok,reason=preflight(p,r['mode'])
                if not ok:self.ack(r['id'],False,reason,now=now);continue
            with self.lock:
                rec=next((x for x in self.records if x['id']==r['id']),None)
                if not rec or rec['state']!='waiting':continue
                rec['state']='queued';rec['message']='起動待ち（プッシュ通知）'
            launch(r['id'],p,r['mode'])

    def retry_push(self,launch,preflight=None,now=None):
        """Retry resource-blocked pushes locally; never query a catalog."""
        now=time.time() if now is None else now
        with self.lock:
            live={r['id'] for r in self.records}
            self.pending_push={k:v for k,v in self.pending_push.items() if k in live and v['expires']>now}
            pending=list(self.pending_push.values())
        for item in pending:
            self.notify_live(dict(item['program'],expires_at=item['expires']),launch,preflight,now)
