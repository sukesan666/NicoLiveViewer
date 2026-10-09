"""Explicit portable-package integration check (opens only its own test player)."""
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from nicoview.windows_job import Job

exe=Path(sys.argv[1]).resolve()
use_svp='--svp' in sys.argv[2:]
flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
result=subprocess.run([str(exe),'--self-test'],cwd=exe.parent,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                      timeout=30,encoding='utf-8',creationflags=flags)
assert result.returncode==0,result.stderr
assert json.loads(result.stdout)['gui'] is True
events=queue.Queue()
p=subprocess.Popen([str(exe),'--worker'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                   text=True,encoding='utf-8',cwd=exe.parent,creationflags=flags)
job=Job(p)
def read():
    for line in p.stdout:events.put(json.loads(line))
threading.Thread(target=read,daemon=True).start()
def send(data):p.stdin.write(json.dumps(data)+'\n');p.stdin.flush()
def wait(kind,ident,timeout=15):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        e=events.get(timeout=max(.01,deadline-time.monotonic()))
        assert e['type']!='error',e
        if e['type']==kind and e['request_id']==ident:return e
    raise AssertionError('Timed out: '+kind)
try:
    for i in range(2):
        ident='preview-'+str(i)
        send({'type':'start','request_id':ident,'preview':True,'player_path':str(exe.parent/'player/mpv.exe'),
              'settings':{'font':'Meiryo','size':28,'svp_enabled':use_svp}})
        wait('status',ident)
        send({'type':'configure','request_id':ident,'settings':{'font':'Yu Mincho','size':48,'enabled':True,'svp_enabled':use_svp}})
        time.sleep(2)
        send({'type':'stop','request_id':ident});wait('stopped',ident)
        time.sleep(.2)
    send({'type':'shutdown','request_id':''});p.wait(timeout=10)
    assert p.returncode==0,p.stderr.read()
    print('Packaged GUI + assets + two preview/start/stop cycles: PASS; SVP mode='+str(use_svp))
finally:
    job.close()
    if p.poll() is None:p.kill()
    p.wait(timeout=5)
