"""Exercise Windows pipe backpressure in bounded, isolated child processes."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
import uuid

from nicoview.player_ipc import PlayerIPC


class NamedPipeServer:
    def __init__(self):
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateNamedPipeW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        ]
        self.api.CreateNamedPipeW.restype = wintypes.HANDLE
        self.api.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        self.api.ConnectNamedPipe.restype = wintypes.BOOL
        for name in ('ReadFile', 'WriteFile'):
            operation = getattr(self.api, name)
            operation.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            operation.restype = wintypes.BOOL
        self.api.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.path = r'\\.\pipe\nicoview-regression-' + uuid.uuid4().hex
        self.handle = self.api.CreateNamedPipeW(self.path, 3, 0, 1, 4096, 4096, 0, None)
        if self.handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())

    def accept(self):
        if not self.api.ConnectNamedPipe(self.handle, None) and ctypes.get_last_error() != 535:
            raise ctypes.WinError(ctypes.get_last_error())

    def write_property(self):
        message = b'{"event":"property-change","name":"time-pos","data":4.5}\n'
        buffer = ctypes.create_string_buffer(message)
        written = wintypes.DWORD()
        if not self.api.WriteFile(self.handle, buffer, len(message), ctypes.byref(written), None):
            raise ctypes.WinError(ctypes.get_last_error())
        assert written.value == len(message)

    def read_command(self):
        buffer = ctypes.create_string_buffer(65536)
        received = wintypes.DWORD()
        while True:
            if not self.api.ReadFile(self.handle, buffer, len(buffer), ctypes.byref(received), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not received.value:
                raise AssertionError('The pipe closed before the command completed')
            if b'\n' in buffer.raw[:received.value]:
                return

    def close(self):
        self.api.DisconnectNamedPipe(self.handle)
        self.api.CloseHandle(self.handle)


def exercise_pipe(case):
    server = NamedPipeServer()
    ipc = PlayerIPC(server.path)
    connected = threading.Event()
    sending = threading.Event()
    property_written = threading.Event()
    property_seen = threading.Event()
    release_peer = threading.Event()
    sender_done = threading.Event()
    write_timed_out = threading.Event()
    errors = []

    def peer():
        try:
            server.accept()
            connected.set()
            assert sending.wait(3), 'The client did not start its write'
            if case == 'parallel-read':
                # The command is larger than the peer's input quota. Keep it
                # pending while sending a reply in the opposite direction.
                time.sleep(.1)
                server.write_property()
                property_written.set()
            if release_peer.wait(8) and case == 'parallel-read':
                server.read_command()
        except Exception as error:
            errors.append(repr(error))

    def send_command():
        try:
            sending.set()
            ipc.send(['script-message', 'regression', 'x' * (2 * 1024 * 1024)])
        except OSError as error:
            if case == 'timeout' and isinstance(error, TimeoutError):
                write_timed_out.set()
            elif case != 'close':
                errors.append(repr(error))
        finally:
            sender_done.set()

    def observe():
        while not ipc.closed:
            if ipc.snapshot().get('time-pos') == 4.5:
                property_seen.set()
                return
            time.sleep(.01)

    peer_thread = threading.Thread(target=peer, daemon=True)
    sender = threading.Thread(target=send_command, daemon=True)
    observer = threading.Thread(target=observe, daemon=True)
    peer_thread.start()
    try:
        ipc.connect(timeout=2)
        assert connected.wait(1), 'The server did not accept the client'
        if case == 'timeout':
            ipc.file.write_timeout = .15
        started = time.monotonic()
        sender.start()
        assert sending.wait(1), 'The client writer did not start'
        if case == 'parallel-read':
            assert property_written.wait(1), 'The server could not send its reply'
            observer.start()
            assert property_seen.wait(1), 'Receiving stopped while the outgoing write was pending'
            assert not sender_done.is_set(), 'The command must remain pending until the peer reads'
            release_peer.set()
            assert sender_done.wait(2), 'The write did not finish after the peer read'
        elif case == 'close':
            time.sleep(.1)
            assert not sender_done.is_set(), 'The peer must hold the write pending'
            started = time.monotonic()
            ipc.close()
            assert time.monotonic() - started < 2, 'Closing waited on a blocked pipe operation'
            assert sender_done.wait(1), 'Closing did not cancel the pending write'
        elif case == 'timeout':
            assert sender_done.wait(1), 'The blocked write exceeded its deadline'
            assert write_timed_out.is_set(), 'The blocked write must raise TimeoutError'
            assert time.monotonic() - started < 1, 'The write timeout was not prompt'
            try:
                ipc.send(['get_property', 'time-pos'])
            except OSError:
                pass
            else:
                raise AssertionError('A timed-out partial command must invalidate the pipe')
            ipc.close()
        else:
            raise AssertionError('Unknown pipe test case')
    finally:
        release_peer.set()
        if sender.ident is not None:
            sender.join(2)
        ipc.close()
        peer_thread.join(2)
        server.close()
        if observer.ident is not None:
            observer.join(1)
    assert not errors, errors
    print('PIPE PASS ' + case)


@unittest.skipUnless(os.name == 'nt', 'Windows named-pipe regression')
class WindowsPipeRegressions(unittest.TestCase):
    def run_case(self, case):
        command = [sys.executable, '-B', str(Path(__file__).resolve()), '--pipe-case', case]
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8', errors='replace', timeout=10)
        except subprocess.TimeoutExpired as error:
            self.fail(f'The pipe child hung in {case}: {error.stdout!r}')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('PIPE PASS ' + case, result.stdout)

    def test_receiving_continues_while_a_write_is_pending(self):
        self.run_case('parallel-read')

    def test_close_cancels_pending_write(self):
        self.run_case('close')

    def test_write_timeout_invalidates_partial_command_connection(self):
        self.run_case('timeout')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--pipe-case':
        exercise_pipe(sys.argv[2])
    else:
        unittest.main()
