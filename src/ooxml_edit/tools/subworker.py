"""A subprocess worker: ``python -m ooxml_edit.tools.subworker``, started by
:class:`~ooxml_edit.tools.worker.SubprocessPool` and by nothing else.

The entry point is fixed: the worker takes no code and no arguments.  It reads messages
from its stdin and answers on its stdout (the protocol is described in :mod:`.worker`), and
runs only functions registered with :func:`~ooxml_edit.tools.worker.worker_task`.

Before anything else it moves the protocol off file descriptors 0 and 1: the pipes are
duplicated to private descriptors, fd 0 becomes the null device and fd 1 a copy of stderr.
So a library that prints -- from Python or from native code -- writes to stderr, and one
that reads stdin reads nothing; neither can touch the protocol.

It exits at the end of its input (the parent closed the pipe, or died), when told to, or
when the parent process is gone while it works (a watchdog thread: the parent pid changes
on POSIX, the parent's handle is signalled on Windows).
"""

from __future__ import annotations

import os
import pickle
import signal
import sys
import threading
import time


def _claim_stdio() -> tuple[int, int]:
    """Move the protocol pipes to private descriptors; fd 0 reads nothing, fd 1 is stderr."""
    proto_in = os.dup(0)
    proto_out = os.dup(1)
    null = os.open(os.devnull, os.O_RDWR)
    try:
        os.dup2(null, 0)
        try:
            os.dup2(2, 1)
        except OSError:  # no stderr (a GUI parent): printing goes nowhere
            os.dup2(null, 1)
    finally:
        os.close(null)
    if sys.platform == "win32":  # (os.name == "nt"; the spelling type checkers follow)
        import msvcrt
        msvcrt.setmode(proto_in, os.O_BINARY)
        msvcrt.setmode(proto_out, os.O_BINARY)
    return proto_in, proto_out


def _watch_parent(parent: int | None, original_ppid: int) -> None:
    """Exit when the parent is gone, even mid-task (an idle worker sees its input end)."""
    if sys.platform == "win32":  # (os.name == "nt"; the spelling type checkers follow)
        if not parent:
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        handle = kernel32.OpenProcess(0x00100000, False, parent)  # SYNCHRONIZE
        if not handle:
            return

        def watch() -> None:
            kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)  # INFINITE
            os._exit(0)
    else:
        def watch() -> None:
            while os.getppid() == original_ppid:
                time.sleep(0.5)
            os._exit(0)

    threading.Thread(target=watch, daemon=True, name="ooxml-edit-tools-parent-watch").start()


def _error(exc: BaseException) -> tuple:
    text = f"{type(exc).__name__}: {exc}"
    try:
        blob: bytes | None = pickle.dumps(exc, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:  # noqa: BLE001 -- the parent gets the text instead
        blob = None
    return ("error", blob, text)


def serve(reader, writer, original_ppid: int) -> None:
    from ooxml_edit.tools.worker import read_frame, resolve_task, write_frame

    hello = read_frame(reader)
    if not (isinstance(hello, tuple) and len(hello) == 2 and hello[0] == "hello"):
        return
    options = hello[1]
    # The parent's import path first, as multiprocessing's spawn does, then the worker's own.
    parent_path = [str(entry) for entry in options.get("sys_path", [])]
    sys.path[:] = parent_path + [entry for entry in sys.path if entry not in parent_path]
    _watch_parent(options.get("parent"), original_ppid)
    write_frame(writer, ("ready", os.getpid()))
    while True:
        message = read_frame(reader)
        if not isinstance(message, tuple) or not message or message[0] != "task":
            return  # the end of the input, "bye", or something this worker does not speak
        _, reference, payload = message
        try:
            fn = resolve_task(reference)
            args, kwargs = pickle.loads(payload)
            result = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 -- everything goes back to the caller
            reply = _error(exc)
        else:
            try:
                reply = ("ok", pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL))
            except Exception as exc:  # noqa: BLE001 -- the result did not pickle
                reply = ("error", None, f"the result did not pickle: {type(exc).__name__}: {exc}")
        write_frame(writer, reply)


def main() -> None:
    original_ppid = os.getppid()
    proto_in, proto_out = _claim_stdio()
    sys.stdin = open(0, closefd=False)
    # The parent decides when this worker stops; a Ctrl-C at the terminal reaches the whole
    # process group, and the parent's own handling closes the pipe.
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):
        pass
    with open(proto_in, "rb") as reader, open(proto_out, "wb") as writer:
        try:
            serve(reader, writer, original_ppid)
        except (BrokenPipeError, EOFError, OSError):
            pass  # the parent is gone


if __name__ == "__main__":
    main()
