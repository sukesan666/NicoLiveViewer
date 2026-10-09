import copy
import json
import math
from pathlib import Path

DEFAULTS = {'font':'Meiryo','size':28,'delay':0.0,'enabled':True,'player_path':'','ffmpeg_path':'',
 'svp_enabled':False,'svp_player_path':'','fps':120,'theme':'dark','ui_scale':100,'volume':100,'muted':False,
 'quality':'best','ng_words':[],'ng_users':[],'comment_offset':0.0,'recording_dir':'',
 'max_jobs':4,'reconnect':True,'inhibit_sleep':False,'startup':False,'tray':False,
 'sound_play':'','sound_record':'','sound_volume':80,'quiet_enabled':False,'quiet_start':'22:00',
 'quiet_end':'07:00','notification_group_seconds':5,'first_run_complete':False}

def clean_settings(values, fonts=None):
    result=copy.deepcopy(DEFAULTS)
    if not isinstance(values,dict): return result
    for key in ('player_path','ffmpeg_path','svp_player_path','recording_dir','sound_play','sound_record'):
        value=values.get(key,values.get('record_dir','') if key=='recording_dir' else '')
        if isinstance(value,str) and '\x00' not in value: result[key]=value[:4096]
    name=values.get('font','Meiryo')
    if isinstance(name,str) and 0<len(name)<100 and not any(c in name for c in '{}\\\r\n'): result['font']=name
    if fonts and result['font'] not in fonts: result['font']='Meiryo' if 'Meiryo' in fonts else fonts[0]
    for key,lo,hi in [('size',12,96),('delay',0,30),('fps',1,240),('volume',0,100),('comment_offset',-3600,3600),('max_jobs',1,16),('sound_volume',0,100),('notification_group_seconds',0,300)]:
        try:
            v=float(values.get(key,result[key]))
            if math.isfinite(v): result[key]=max(lo,min(hi,v))
        except (TypeError,ValueError,OverflowError): pass
    for key in ('size','fps','volume','max_jobs','sound_volume','notification_group_seconds'): result[key]=int(result[key])
    for key in ('enabled','svp_enabled','muted','reconnect','inhibit_sleep','startup','tray','quiet_enabled','first_run_complete'):
        if isinstance(values.get(key),bool): result[key]=values[key]
    if values.get('theme') in ('dark','light'): result['theme']=values['theme']
    if str(values.get('ui_scale')) in tuple(str(n) for n in range(100,251,10)): result['ui_scale']=int(values['ui_scale'])
    quality=values.get('quality')
    if quality in ('best','worst','high','normal','low','audio_only','1080p','720p','480p','360p'): result['quality']=quality
    for key in ('ng_words','ng_users'):
        items=values.get(key,[])
        if isinstance(items,list): result[key]=list(dict.fromkeys(x[:200] for x in items[:1000] if isinstance(x,str) and x.strip()))
    for key in ('quiet_start','quiet_end'):
        v=values.get(key)
        if isinstance(v,str) and len(v)==5:
            try:
                h,m=map(int,v.split(':'))
                if 0<=h<24 and 0<=m<60: result[key]=f'{h:02}:{m:02}'
            except ValueError: pass
    return result

def load_settings(path):
    try: return clean_settings(json.loads(Path(path).read_text(encoding='utf-8')))
    except (OSError,ValueError): return clean_settings({})

def save_settings(path,values):
    from .preferences import atomic_json
    atomic_json(Path(path),clean_settings(values))
