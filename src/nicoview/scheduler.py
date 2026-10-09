"""Persistent reservation state machine. Network calls run off the Tk thread."""
import copy
import math
import threading
import time
import uuid
import unicodedata
from .url import normalize_url

def keyword_text(value):
    return unicodedata.normalize('NFKC',str(value)).casefold().strip()

class Scheduler:
    def __init__(self,records=None):
        self.lock=threading.RLock();self.records=[];self.keyword_next=0
        used=set()
        for r in (records if isinstance(records,list) else [])[:500]:
            try:
                ident=self.add(r,r.get('mode','play'))
                q=self.records[-1]
                saved_id=str(r.get('id',ident))[:100]
                q['id']=saved_id if saved_id and saved_id not in used else ident
                used.add(q['id'])
                q['state']=r.get('state','waiting') if r.get('state') in ('waiting','started','ended','disabled') else 'waiting'
                seen=r.get('seen',[])
                q['seen']=[str(x)[:100] for x in seen if isinstance(x,str)][-2000:] if isinstance(seen,list) else []
            except (ValueError,TypeError,AttributeError):pass
    def add(self,program,mode):
        if mode not in ('play','record','both'):raise ValueError('実行方法を選択してください。')
        url=program.get('url','');user=str(program.get('user_id',''))[:100]
        keyword=str(program.get('keyword','')).strip()
        if 'keyword' in program and (not keyword or len(keyword)>100 or url or user):
            raise ValueError('キーワードは1～100文字で入力してください。')
        if url:url=normalize_url(url)
        if not url and not user and not keyword:raise ValueError('番組またはユーザーを選択してください。')
        if not url and not keyword and not user.isdecimal():
            # tests and alternate provider IDs may use alphanumerics; never form URLs from it.
            if not user.isalnum():raise ValueError('ユーザーIDを確認してください。')
        start=program.get('start',0)
        try:start=max(0,float(start or 0))
        except (ValueError,TypeError):start=0
        if not math.isfinite(start) or start>4102444800:start=0
        item={'id':uuid.uuid4().hex,'url':url,'user_id':user if not url else '',
              'title':str(program.get('title') or program.get('author') or url or user)[:300],
              'start':start,'mode':mode,'state':'waiting','seen':[],'message':'待機中','retry_at':0}
        if keyword:item.update(keyword=keyword,title='公式キーワード：'+keyword)
        with self.lock:
            if keyword:
                if any(keyword_text(r.get('keyword',''))==keyword_text(keyword) and r['mode']==mode for r in self.records):
                    raise ValueError('このキーワードは同じ動作で登録済みです。')
                self.keyword_next=0
            if len(self.records)>=500:raise ValueError('予約は500件までです。')
            self.records.append(item)
        return item['id']
    def snapshot(self):
        with self.lock:return copy.deepcopy(self.records)
    def remove(self,ident):
        with self.lock:self.records=[r for r in self.records if r['id']!=ident]
    def ack(self,ident,ok,message,program=None,now=None):
        now=time.time() if now is None else now
        with self.lock:
            r=next((r for r in self.records if r['id']==ident),None)
            if not r:return
            r['message']=str(message)[:300]
            if ok:
                if r['user_id']:
                    if program:r['seen']=(r['seen']+[str(program.get('id') or program.get('url'))])[-200:]
                    r['state']='waiting'
                else:r['state']='started'
            else:r['state']='waiting';r['retry_at']=now+60
    def expand_keywords(self,catalog,now):
        rules=[r for r in self.snapshot() if r.get('keyword') and r['state']=='waiting']
        if not rules or now<self.keyword_next:return
        self.keyword_next=now+600
        try:
            programs=catalog.official_week()
        except Exception:
            self.keyword_next=now+60
            for rule in rules:self.ack(rule['id'],False,'公式番組表を取得できません。次回再試行します。',now=now)
            return
        for saved in rules:
            with self.lock:
                rule=next((r for r in self.records if r['id']==saved['id']),None)
                if not rule or rule['state']!='waiting':continue
                added=0;full=False
                for program in programs:
                    if program.get('provider')!='official' or program.get('status')=='ENDED':continue
                    if keyword_text(rule['keyword']) not in keyword_text(program.get('title','')):continue
                    try:url=normalize_url(program.get('url',''))
                    except ValueError:continue
                    if url in rule['seen']:continue
                    wanted={'play','record'} if rule['mode']=='both' else {rule['mode']}
                    occupied=set()
                    for existing in self.records:
                        if existing['url']==url:
                            occupied.update({'play','record'} if existing['mode']=='both' else {existing['mode']})
                    missing=wanted-occupied
                    if missing:
                        mode='both' if len(missing)==2 else next(iter(missing))
                        try:self.add(program,mode)
                        except ValueError:
                            full=True;break
                        added+=1
                    rule['seen']=(rule['seen']+[url])[-2000:]
                rule['message']='予約が上限です。不要な予約を削除してください。' if full else f'10分ごとに確認・今回{added}件を追加'

    def tick(self,catalog,launch,now=None,preflight=None):
        now=time.time() if now is None else now
        self.expand_keywords(catalog,now)
        following=None
        for saved in self.snapshot():
            if saved.get('keyword'):continue
            if saved['state']!='waiting' or saved.get('retry_at',0)>now:continue
            try:
                if saved['user_id']:
                    if following is None:following=catalog.following()
                    candidates=[p for p in following if str(p.get('user_id',''))==saved['user_id'] and str(p.get('id') or p.get('url')) not in saved['seen']]
                else:candidates=[catalog.program(saved['url'])]
                for p in candidates:
                    status=p.get('status','');start=p.get('start') or saved['start']
                    if not isinstance(start,(int,float)) or not math.isfinite(start) or start>4102444800:start=0
                    with self.lock:
                        r=next((r for r in self.records if r['id']==saved['id']),None)
                        if not r or r['state']!='waiting':break
                        if start:r['start']=start
                        if p.get('title') and not r['user_id']:r['title']=p['title'][:300]
                        if status=='ENDED' and not r['user_id']:
                            r['state']='ended';r['message']='放送終了・予約未実行';continue
                    if preflight and (status=='ON_AIR' or start and start-now<300):
                        ok,reason=preflight(p,saved['mode'])
                        if not ok:self.ack(saved['id'],False,reason,now=now);continue
                    if status!='ON_AIR':continue
                    with self.lock:
                        r=next((r for r in self.records if r['id']==saved['id']),None)
                        if not r or r['state']!='waiting':break
                        r['state']='queued';r['message']='起動待ち'
                    launch(saved['id'],p,saved['mode'])
                    break
            except Exception:
                self.ack(saved['id'],False,'番組情報を取得できません。次回再試行します。',now=now)
