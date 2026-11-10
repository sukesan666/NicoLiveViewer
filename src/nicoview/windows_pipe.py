"""Full-duplex Windows named-pipe I/O with cancellable overlapped operations."""
import ctypes
from ctypes import wintypes as W
import threading
import time


class _Overlapped(ctypes.Structure):
    _fields_=[('Internal',ctypes.c_size_t),('InternalHigh',ctypes.c_size_t),
              ('Offset',W.DWORD),('OffsetHigh',W.DWORD),('hEvent',W.HANDLE)]


_kernel=ctypes.WinDLL('kernel32',use_last_error=True)
_kernel.CreateFileW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,ctypes.c_void_p,W.DWORD,W.DWORD,W.HANDLE]
_kernel.CreateFileW.restype=W.HANDLE
_kernel.CreateEventW.argtypes=[ctypes.c_void_p,W.BOOL,W.BOOL,W.LPCWSTR]
_kernel.CreateEventW.restype=W.HANDLE
for _name in ('ReadFile','WriteFile'):
    _function=getattr(_kernel,_name)
    _function.argtypes=[W.HANDLE,ctypes.c_void_p,W.DWORD,ctypes.POINTER(W.DWORD),ctypes.POINTER(_Overlapped)]
    _function.restype=W.BOOL
_kernel.GetOverlappedResult.argtypes=[W.HANDLE,ctypes.POINTER(_Overlapped),ctypes.POINTER(W.DWORD),W.BOOL]
_kernel.GetOverlappedResult.restype=W.BOOL
_kernel.CancelIoEx.argtypes=[W.HANDLE,ctypes.POINTER(_Overlapped)]
_kernel.CancelIoEx.restype=W.BOOL
_kernel.PeekNamedPipe.argtypes=[W.HANDLE,ctypes.c_void_p,W.DWORD,ctypes.c_void_p,ctypes.POINTER(W.DWORD),ctypes.c_void_p]
_kernel.PeekNamedPipe.restype=W.BOOL
_kernel.GetNamedPipeServerProcessId.argtypes=[W.HANDLE,ctypes.POINTER(W.ULONG)]
_kernel.GetNamedPipeServerProcessId.restype=W.BOOL
_kernel.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD]
_kernel.WaitForSingleObject.restype=W.DWORD
_kernel.CloseHandle.argtypes=[W.HANDLE]
_kernel.CloseHandle.restype=W.BOOL

_INVALID_HANDLE=ctypes.c_void_p(-1).value
_GENERIC_READ_WRITE=0xC0000000
_OPEN_EXISTING=3
_FILE_FLAG_OVERLAPPED=0x40000000
_ERROR_IO_PENDING=997
_WAIT_TIMEOUT=258


class WindowsPipe:
    def __init__(self,path,write_timeout=5.0):
        self._lock=threading.Lock()
        self._closed=False
        self._active=0
        self.write_timeout=write_timeout
        # A duplicated synchronous handle would still share kernel I/O locks.
        self._handle=_kernel.CreateFileW(str(path),_GENERIC_READ_WRITE,0,None,_OPEN_EXISTING,_FILE_FLAG_OVERLAPPED,None)
        if self._handle==_INVALID_HANDLE:
            raise ctypes.WinError(ctypes.get_last_error())

    def _release(self):
        with self._lock:
            self._active-=1
            if self._closed and not self._active:
                _kernel.CloseHandle(self._handle)
                self._handle=None

    def _transfer(self,function,buffer,size,timeout=None):
        event=_kernel.CreateEventW(None,True,False,None)
        if not event:raise ctypes.WinError(ctypes.get_last_error())
        overlapped=_Overlapped(hEvent=event)
        transferred=W.DWORD()
        active=False
        try:
            # Issuing and cancellation share this lock. close() cannot miss an
            # operation that was admitted but has not reached the kernel yet.
            with self._lock:
                if self._closed:raise OSError('Player pipe is closed')
                handle=self._handle
                self._active+=1;active=True
                completed=function(handle,buffer,size,ctypes.byref(transferred),ctypes.byref(overlapped))
                error=ctypes.get_last_error() if not completed else 0
            if not completed and error!=_ERROR_IO_PENDING:
                raise ctypes.WinError(error)
            if not completed:
                milliseconds=0xFFFFFFFF if timeout is None else max(0,min(0xFFFFFFFE,int(timeout*1000)))
                wait=_kernel.WaitForSingleObject(event,milliseconds)
                if wait!=0:
                    failure=TimeoutError('Player pipe write timed out') if wait==_WAIT_TIMEOUT else ctypes.WinError(ctypes.get_last_error())
                    self.close()
                    # CancelIoEx only requests cancellation. Keep the event,
                    # OVERLAPPED and data buffer alive until completion arrives.
                    _kernel.GetOverlappedResult(handle,ctypes.byref(overlapped),ctypes.byref(transferred),True)
                    raise failure
            if not _kernel.GetOverlappedResult(handle,ctypes.byref(overlapped),ctypes.byref(transferred),False):
                raise ctypes.WinError(ctypes.get_last_error())
            return transferred.value
        finally:
            if active:self._release()
            _kernel.CloseHandle(event)

    def read_available(self):
        available=W.DWORD()
        with self._lock:
            if self._closed:raise OSError('Player pipe is closed')
            if not _kernel.PeekNamedPipe(self._handle,None,0,None,ctypes.byref(available),None):
                raise ctypes.WinError(ctypes.get_last_error())
        if not available.value:return b''
        # Bound each drain so snapshots and sends can acquire the receive lock.
        size=min(available.value,65536)
        buffer=ctypes.create_string_buffer(size)
        count=self._transfer(_kernel.ReadFile,buffer,size)
        return buffer.raw[:count]

    def server_process_id(self):
        actual=W.ULONG()
        with self._lock:
            if self._closed:raise OSError('Player pipe is closed')
            if not _kernel.GetNamedPipeServerProcessId(self._handle,ctypes.byref(actual)):
                raise ctypes.WinError(ctypes.get_last_error())
        return actual.value

    def write(self,data):
        deadline=time.monotonic()+self.write_timeout
        offset=0
        try:
            while offset<len(data):
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('Player pipe write timed out')
                buffer=ctypes.create_string_buffer(data[offset:])
                count=self._transfer(_kernel.WriteFile,buffer,len(data)-offset,remaining)
                if not count:raise OSError('Player pipe write made no progress')
                offset+=count
            return offset
        except OSError:
            # A partial JSON command cannot safely be followed by another one.
            self.close()
            raise

    def close(self):
        with self._lock:
            if self._closed:return
            self._closed=True
            if self._active:
                # Completion may race cancellation; every operation still reaps
                # its completion before releasing the last handle reference.
                _kernel.CancelIoEx(self._handle,None)
            else:
                _kernel.CloseHandle(self._handle)
                self._handle=None
