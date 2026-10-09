"""Per-user durable preferences. Exports never include the separate DPAPI session."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
from urllib.parse import urlsplit,urlunsplit
from .settings import clean_settings

_LOCK=threading.RLock()
_SECRET=('cookie','token','password','authorization','credential','session','secret')

def safe_state(value,depth=0):
    if depth>12: return None
    if isinstance(value,dict):
        return {str(k)[:100]:safe_state(v,depth+1) for k,v in list(value.items())[:2000] if not any(s in str(k).lower() for s in _SECRET)}
    if isinstance(value,list): return [safe_state(v,depth+1) for v in value[:10000]]
    if isinstance(value,str):
        if any(s in value.lower() for s in ('cookie=','token=','bearer ','user_session','password=')): return '[redacted]'
        if value.startswith(('http://','https://')):
            p=urlsplit(value)
            return urlunsplit((p.scheme,p.hostname or '',p.path,'',''))
        return value[:10000]
    if value is None or isinstance(value,(bool,int,float)): return value
    return None

def atomic_json(path,value,backup=True):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    data=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)
    with _LOCK:
        fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as f:
                f.write(data); f.flush(); os.fsync(f.fileno())
            if backup and path.exists():
                try:
                    json.loads(path.read_text(encoding='utf-8'))
                    shutil.copy2(path,path.with_suffix(path.suffix+'.bak'))
                except (ValueError,OSError): pass
            os.replace(name,path)
        finally:
            if os.path.exists(name): os.unlink(name)

class PreferenceStore:
    def __init__(self,path=None):
        self.directory=Path(path) if path is not None else Path(os.getenv('APPDATA',str(Path.home()/'AppData'/'Roaming')))/'NicoLiveViewer'
        self.path=self.directory
        self.settings_path=self.directory/'settings.json'
        self.state_path=self.directory/'state.json'
    def _load(self,path,default):
        for candidate in (path,path.with_suffix(path.suffix+'.bak')):
            try:
                if candidate.stat().st_size>8*1024*1024: continue
                value=json.loads(candidate.read_text(encoding='utf-8'))
                if isinstance(value,dict): return value
            except (OSError,ValueError): pass
        return default
    def load_settings(self): return clean_settings(self._load(self.settings_path,{}))
    def save_settings(self,values):
        result=clean_settings(values); atomic_json(self.settings_path,result); return result
    def load_state(self): return safe_state(self._load(self.state_path,{}))
    def save_state(self,values):
        if not isinstance(values,dict): raise ValueError('State must be an object')
        atomic_json(self.state_path,safe_state(values))
    def export_settings(self,path): atomic_json(Path(path),{'format':'NicoLiveViewer settings','version':1,'settings':self.load_settings()},backup=False)
    def import_settings(self,path):
        path=Path(path)
        if path.stat().st_size>1024*1024: raise ValueError('設定ファイルが大きすぎます')
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data,dict): raise ValueError('設定形式が不正です')
        values=data.get('settings',data)
        if not isinstance(values,dict): raise ValueError('設定形式が不正です')
        values=clean_settings(values)
        # Imports cannot opt the machine into OS side effects.
        values['startup']=False; values['tray']=False; values['inhibit_sleep']=False
        return self.save_settings(values)
    def restore_backup(self,kind='settings'):
        if kind not in ('settings','state'): raise ValueError('Unknown backup')
        path=self.settings_path if kind=='settings' else self.state_path
        data=json.loads(path.with_suffix(path.suffix+'.bak').read_text(encoding='utf-8'))
        if not isinstance(data,dict): raise ValueError('Invalid backup')
        atomic_json(path,clean_settings(data) if kind=='settings' else safe_state(data),backup=False)
    def migrate(self,legacy_directory):
        legacy=Path(legacy_directory)
        self.directory.mkdir(parents=True,exist_ok=True)
        if not self.settings_path.exists() and (legacy/'settings.json').is_file():
            self.save_settings(self._load(legacy/'settings.json',{}))
        for name in ('session.dat','session.bin','credentials.dat'):
            old=legacy/name; new=self.directory/name
            if old.is_file() and not new.exists() and old.stat().st_size<1024*1024: shutil.copy2(old,new)
    def export_diagnostics(self,path,details=None):
        # Explicit non-sensitive counters only: never export arbitrary logs or paths.
        allowed=('job_count','running_count','failed_count','version','platform')
        info={k:v for k,v in (details or {}).items() if k in allowed and isinstance(v,(int,bool))}
        atomic_json(Path(path),{'format':'NicoLiveViewer diagnostics','settings':{k:v for k,v in self.load_settings().items() if k in ('fps','theme','quality','max_jobs','reconnect')},'details':info},backup=False)

