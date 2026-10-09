"""Bounded local recording inventory and optional local thumbnail extraction."""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess

MEDIA_SUFFIXES=('.ts','.mp4','.mkv','.webm','.mov')

def _number(value):
    try:
        n=float(value); return max(0,n) if math.isfinite(n) else 0
    except (TypeError,ValueError): return 0

def scan_library(recording_dir,history=(),limit=2000):
    root=Path(recording_dir or Path.home()/'Videos'/'NicoLive Studio')
    rows={}; visited=0
    if root.is_dir():
        for folder,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(folder)/d).is_symlink()]
            for name in files:
                visited+=1
                if visited>20000 or len(rows)>=limit: break
                if not name.endswith('.metadata.json'): continue
                metadata=Path(folder)/name
                try:
                    if metadata.is_symlink() or metadata.stat().st_size>256*1024: continue
                    data=json.loads(metadata.read_text(encoding='utf-8'))
                    if not isinstance(data,dict): continue
                    base=name[:-len('.metadata.json')]
                    candidates=[Path(folder)/(base+suffix) for suffix in MEDIA_SUFFIXES]
                    path=next((p for p in candidates if p.is_file()),candidates[0])
                    if path.is_symlink(): continue
                    path.resolve().relative_to(root.resolve())
                    state=str(data.get('state','unknown'))
                    if not path.is_file(): state='missing'
                    elif state=='recording': state='interrupted'
                    rows[str(path.resolve()).casefold()]={'path':str(path),'title':str(data.get('title',base))[:500],'author':str(data.get('author',''))[:200],'state':state,'created':str(data.get('created',''))[:80],'duration':_number(data.get('elapsed',0)),'position':0,'watched':False,'comments':str(path.with_suffix('.comments.jsonl'))}
                except (OSError,ValueError): continue
            if visited>20000 or len(rows)>=limit: break
    for item in list(history)[:2000]:
        if not isinstance(item,dict) or not isinstance(item.get('local_path'),str) or not item['local_path']: continue
        path=Path(item['local_path'])
        if path.suffix.lower() not in MEDIA_SUFFIXES: continue
        key=str(path.resolve()).casefold()
        if key not in rows:
            rows[key]={'path':str(path),'title':str(item.get('title',path.stem))[:500],'author':'','state':'complete' if path.is_file() else 'missing','created':str(item.get('seen_at','')),'duration':_number(item.get('duration',0)),'comments':str(path.with_suffix('.comments.jsonl'))}
        rows[key].update(position=_number(item.get('position',0)),watched=item.get('watched') is True)
    return sorted(rows.values(),key=lambda r:r['created'],reverse=True)[:limit]

def make_thumbnail(video,ffmpeg_path,cache_dir):
    path=Path(video)
    if not ffmpeg_path or not Path(ffmpeg_path).is_file() or not path.is_file(): return None
    stat=path.stat(); key=hashlib.sha256(f'{path.resolve()}:{stat.st_mtime_ns}:{stat.st_size}'.encode()).hexdigest()
    cache=Path(cache_dir); cache.mkdir(parents=True,exist_ok=True); target=cache/(key+'.png')
    if target.is_file(): return target
    # Input is a local absolute file; protocol allowlist prevents nested network access.
    args=[str(ffmpeg_path),'-nostdin','-hide_banner','-loglevel','error','-protocol_whitelist','file,pipe','-ss','1','-i',str(path.resolve()),'-frames:v','1','-vf','scale=256:144:force_original_aspect_ratio=decrease,pad=256:144:(ow-iw)/2:(oh-ih)/2','-y',str(target)]
    try:
        result=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=12,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode==0 and target.is_file(): return target
    except (OSError,subprocess.TimeoutExpired): pass
    target.unlink(missing_ok=True); return None
