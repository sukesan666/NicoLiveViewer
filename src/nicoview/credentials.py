"""Opt-in credential storage, encrypted with Windows DPAPI CurrentUser or POSIX per-user AES-GCM."""
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile

MAGIC=b'NICOVIEW-DPAPI-1\x00'

def _posix_key(create=False) -> bytes:
    from Crypto.Protocol.KDF import HKDF
    from Crypto.Hash import SHA256
    if sys.platform=='darwin' and not os.getenv('XDG_CONFIG_HOME'):
        base=Path.home()/'Library'/'Application Support'/'NicoLiveViewer'
    else:
        base=Path(os.getenv('XDG_CONFIG_HOME') or (Path.home()/'.config'))/'NicoLiveViewer'
    key_file=base/'.master.key'
    if not key_file.is_file():
        if not create:raise OSError('Windowsの暗号化・復号に失敗しました。')
        base.mkdir(parents=True,exist_ok=True)
        try:os.chmod(base,0o700)
        except OSError:pass
        secret=os.urandom(32)
        tmp=None
        try:
            with tempfile.NamedTemporaryFile(dir=base,prefix='.key-',delete=False) as f:
                tmp=Path(f.name)
                try:os.chmod(tmp,0o600)
                except OSError:pass
                f.write(secret);f.flush();os.fsync(f.fileno())
            tmp.replace(key_file)
        finally:
            if tmp:tmp.unlink(missing_ok=True)
    raw=key_file.read_bytes()
    if len(raw)!=32:raise OSError('Windowsの暗号化・復号に失敗しました。')
    uid=str(os.getuid()).encode('ascii') if hasattr(os,'getuid') else b'0'
    return HKDF(raw,32,b'nicoview-salt:'+uid,SHA256,context=b'nicoview-credentials-v1')

def _posix_crypt(data: bytes, decrypt=False) -> bytes:
    from Crypto.Cipher import AES
    try:
        key=_posix_key(create=not decrypt)
        if not decrypt:
            nonce=os.urandom(12)
            cipher=AES.new(key,AES.MODE_GCM,nonce=nonce)
            ct,tag=cipher.encrypt_and_digest(data)
            return nonce+tag+ct
        if len(data)<28:raise ValueError('Truncated ciphertext')
        nonce,tag,ct=data[:12],data[12:28],data[28:]
        cipher=AES.new(key,AES.MODE_GCM,nonce=nonce)
        return cipher.decrypt_and_verify(ct,tag)
    except (OSError,ValueError,KeyError) as exc:
        raise OSError('Windowsの暗号化・復号に失敗しました。') from exc

def _crypt(data: bytes, decrypt=False) -> bytes:
    if os.name!='nt':return _posix_crypt(data,decrypt)
    from ctypes import wintypes as W
    class Blob(ctypes.Structure):
        _fields_=[('size',W.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
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
