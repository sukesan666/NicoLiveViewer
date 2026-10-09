"""Offline dedicated Edge/CDP plumbing test using a synthetic session cookie.

No real account, password, personal browser profile, or site login is accessed.
"""
import json
import threading
import time
import websocket
from nicoview.browser_login import BrowserLogin
from nicoview.credentials import protect, unprotect

# Edge's cookie store itself requires a usable Windows user encryption profile.
try:
    assert unprotect(protect(b'browser-test'))==b'browser-test'
except OSError:
    print('SKIP: Windows user encryption unavailable; Edge cookie integration cannot run in this account')
    raise SystemExit(0)

browser=BrowserLogin(threading.Event())
try:
    browser.open('about:blank',headless=True)
    tabs=browser.http.get(browser.base+'/json/list',timeout=3).json()
    tab=next(t for t in tabs if t.get('type')=='page')
    ws=websocket.create_connection(tab['webSocketDebuggerUrl'],timeout=3,suppress_origin=True,
                                  http_no_proxy=['127.0.0.1','localhost'])
    try:
        ws.send(json.dumps({'id':10,'method':'Network.setCookie','params':{
            'name':'user_session','value':'synthetic-test-only','domain':'.nicovideo.jp',
            'path':'/','secure':True,'httpOnly':True}}))
        while True:
            response=json.loads(ws.recv())
            if response.get('id')==10:
                assert response.get('result',{}).get('success'), 'Synthetic cookie setup failed'
                break
    finally:ws.close()
    cookies=browser.cookies()
    assert len(cookies)==1 and cookies[0]['value']=='synthetic-test-only'
    # Graceful shutdown must preserve a session cookie for the subsequent handoff.
    ws=websocket.create_connection(tab['webSocketDebuggerUrl'],timeout=3,suppress_origin=True,
                                  http_no_proxy=['127.0.0.1','localhost'])
    try:
        ws.send(json.dumps({'id':11,'method':'Browser.close'}))
    finally:ws.close()
    deadline=time.monotonic()+15
    while browser.is_running() and time.monotonic()<deadline:time.sleep(.1)
    assert not browser.is_running(), 'Owned browser children did not finish flushing'
    assert browser.proc.poll()==0
    browser.job.close();browser.job=None;browser.proc=None
    browser.http.close();browser.http=None
    browser.open('about:blank',headless=True)
    cookies=browser.cookies()
    assert len(cookies)==1 and cookies[0]['value']=='synthetic-test-only', 'Session cookie lost during restart'
    profile=browser.profile
finally:browser.close()
assert not profile.exists(),'Temporary browser profile not cleaned'
print('Dedicated Edge + HttpOnly session cookie restart handoff + owned profile cleanup: PASS')
