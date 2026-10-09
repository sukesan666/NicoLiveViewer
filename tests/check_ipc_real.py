"""Run explicitly: a real mpv integration regression; never part of the unit suite."""
import faulthandler
import json
from pathlib import Path
import subprocess
import threading
import time
import uuid
from nicoview.player_ipc import PlayerIPC
from nicoview.playback import asset_path

pipe=r'\\.\pipe\nicoview-test-'+uuid.uuid4().hex
player=Path('../player/mpv.exe').resolve()
p=subprocess.Popen([str(player),'--no-config','--vo=null','--ao=null','--idle=yes',
                    '--input-ipc-server='+pipe],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
    ipc=PlayerIPC(pipe);ipc.connect()
    done=threading.Event();errors=[]
    def send():
        try:ipc.send(['get_property','mpv-version'])
        except Exception as e:errors.append(repr(e))
        finally:done.set()
    threading.Thread(target=send,daemon=True).start()
    assert done.wait(3),'IPC write deadlocked behind blocking reader'
    assert not errors,errors
    print('Real mpv IPC write PASS',flush=True)
finally:
    p.terminate();p.wait(timeout=5)
