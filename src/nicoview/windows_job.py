"""Kill only this app's worker and descendants if the UI exits unexpectedly."""
import ctypes
from ctypes import wintypes as W
import os

class Job:
    def __init__(self, process):
        self.handle=None
        if os.name!='nt': return
        k=ctypes.WinDLL('kernel32',use_last_error=True); self.k=k
        class Basic(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',W.DWORD),
                ('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',W.DWORD),
                ('affinity',ctypes.c_size_t),('priority',W.DWORD),('scheduling',W.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(n,ctypes.c_uint64) for n in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Extended(ctypes.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),
                ('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        k.CreateJobObjectW.argtypes=[ctypes.c_void_p,W.LPCWSTR];k.CreateJobObjectW.restype=W.HANDLE
        k.SetInformationJobObject.argtypes=[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD]
        k.AssignProcessToJobObject.argtypes=[W.HANDLE,W.HANDLE]
        k.CloseHandle.argtypes=[W.HANDLE]
        handle=k.CreateJobObjectW(None,None)
        info=Extended(); info.basic.flags=0x2000 # KILL_ON_JOB_CLOSE
        if not handle or not k.SetInformationJobObject(handle,9,ctypes.byref(info),ctypes.sizeof(info)):
            if handle:k.CloseHandle(handle)
            raise OSError('Cannot create worker job')
        if not k.AssignProcessToJobObject(handle,W.HANDLE(int(process._handle))):
            k.CloseHandle(handle);raise OSError('Cannot assign worker job')
        self.handle=handle
    @property
    def active_process_count(self):
        """Count owned descendants, including replacements of an exited launcher."""
        if not self.handle:return 0
        class Accounting(ctypes.Structure):
            _fields_=[('user',ctypes.c_int64),('kernel',ctypes.c_int64),
                      ('period_user',ctypes.c_int64),('period_kernel',ctypes.c_int64),
                      ('faults',W.DWORD),('total',W.DWORD),('active',W.DWORD),('terminated',W.DWORD)]
        query=self.k.QueryInformationJobObject
        query.argtypes=[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD,ctypes.c_void_p]
        query.restype=W.BOOL
        info=Accounting()
        if not query(self.handle,1,ctypes.byref(info),ctypes.sizeof(info),None):
            raise OSError('Cannot query browser process group')
        return info.active
    def close(self):
        if self.handle: self.k.CloseHandle(self.handle);self.handle=None
