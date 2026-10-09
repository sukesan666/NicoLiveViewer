"""Opt-in credential storage, encrypted with Windows DPAPI CurrentUser."""
import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import tempfile

MAGIC=b'NICOVIEW-DPAPI-1\x00'

class Blob(ctypes.Structure):
    _fields_=[('size',W.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]

def _crypt(data: bytes, decrypt=False) -> bytes:
    if os.name!='nt':raise OSError('Windowsの暗号化機能が必要です。')
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    function=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),
                       ctypes.c_void_p,ctypes.c_void_p,W.DWORD,ctypes.POINTER(Blob)]
    function.restype=W.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    output=Blob()
    try:
        # UI_FORBIDDEN only; deliberately do not use CRYPTPROTECT_LOCAL_MACHINE.
        if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output)):
            raise OSError('Windowsの暗号化・復号に失敗しました。')
        return ctypes.string_at(output.data,output.size)
    finally:
        ctypes.memset(buffer,0,len(buffer))
        if output.data:
            ctypes.memset(output.data,0,output.size)
            kernel.LocalFree(output.data)

def protect(data):return _crypt(data)
def unprotect(data):return _crypt(data,True)

class CredentialStore:
    def __init__(self,path):self.path=Path(path)

    def save(self,email,password):
        if not isinstance(email,str) or not email or not isinstance(password,str) or not password:
            raise ValueError('メールアドレスとパスワードが必要です。')
        payload=json.dumps({'email':email,'password':password},ensure_ascii=False).encode('utf-8')
        if len(payload)>16384:raise ValueError('ログイン情報が長すぎます。')
        encrypted=MAGIC+protect(payload)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent,prefix='.credentials-',suffix='.tmp',delete=False) as f:
                temporary=Path(f.name);f.write(encrypted);f.flush();os.fsync(f.fileno())
            temporary.replace(self.path)
        finally:
            if temporary:temporary.unlink(missing_ok=True)

    def load(self):
        try:
            if self.path.stat().st_size>65536:raise ValueError('Invalid size')
            raw=self.path.read_bytes()
        except FileNotFoundError:return None
        try:
            if not raw.startswith(MAGIC):raise ValueError('Invalid header')
            result=json.loads(unprotect(raw[len(MAGIC):]).decode('utf-8'))
            if not isinstance(result,dict) or not all(isinstance(result.get(k),str) and result[k] for k in ('email','password')):
                raise ValueError('Invalid credentials')
            return {'email':result['email'],'password':result['password']}
        except (OSError,ValueError,UnicodeError) as exc:
            raise ValueError('保存したログイン情報を読み込めません。同じWindowsユーザーで再保存してください。') from None

    def delete(self):self.path.unlink(missing_ok=True)
