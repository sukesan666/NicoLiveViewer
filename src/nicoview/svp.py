"""Use the user's installed SVP runtime; never redistribute it or its license."""
import os
from pathlib import Path

PIPE = r'\\.\pipe\mpvpipe'


def find_player():
    for root in (os.environ.get('ProgramFiles','C:/Program Files'),
                 os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)')):
        candidate=Path(root)/'SVP 4/mpv64/mpv.exe'
        if candidate.is_file():
            return str(candidate)
    return ''


def player_path(configured):
    value=configured or find_player()
    if not value or not Path(value).is_file():
        raise ValueError('SVP付属のmpv.exeが見つかりません。SVPの追加コンポーネントでmpvを導入し、場所を指定してください。')
    if not (Path(value).parent/'VSScript.dll').is_file():
        raise ValueError('VapourSynthが見つかりません。SVP付属のmpv64/mpv.exeを指定してください。')
    return value


def player_environment():
    env=os.environ.copy()
    # The worker's Python paths must not contaminate SVP's embedded Python.
    env.pop('PYTHONPATH',None)
    env.pop('PYTHONHOME',None)
    return env


def check_pipe_available():
    if os.name != 'nt':
        raise ValueError('SVP連携はWindows専用です。')
    import ctypes
    from ctypes import wintypes as W
    dll=ctypes.WinDLL('kernel32',use_last_error=True)
    wait=dll.WaitNamedPipeW
    wait.argtypes=[W.LPCWSTR,W.DWORD];wait.restype=W.BOOL
    # Do not open or send commands to another player's server.
    if wait(PIPE,1) or ctypes.get_last_error() != 2:
        raise ValueError('SVP接続が使用中です。他のSVP対応mpvを閉じて再生し直してください。')


def verify_server(ipc, pid):
    """Close a startup race safely: never control an unrelated mpv process."""
    import ctypes
    import msvcrt
    from ctypes import wintypes as W
    dll=ctypes.WinDLL('kernel32',use_last_error=True)
    get_pid=dll.GetNamedPipeServerProcessId
    get_pid.argtypes=[W.HANDLE,ctypes.POINTER(W.ULONG)];get_pid.restype=W.BOOL
    actual=W.ULONG()
    handle=W.HANDLE(msvcrt.get_osfhandle(ipc.file.fileno()))
    if not get_pid(handle,ctypes.byref(actual)) or actual.value != pid:
        raise ValueError('SVP接続が競合しました。他のSVP対応mpvを閉じて再生し直してください。')
