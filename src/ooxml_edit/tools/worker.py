"""Worker processes for CPU-bound work -- rendering, layout -- with deadlines that are kept.

Rendering and text layout are pure Python, or native code that holds the GIL (resvg-py's
rasterise does, for the whole of it), so threads would serialize on the GIL and a runaway
render could not be stopped.  Each task runs in a worker process instead; the caller waits
at most its deadline, and a worker still busy when the deadline passes is killed and
replaced, and the call reports ``timeout``.  The document is passed as bytes
(``document.to_bytes()``): the libraries render from bytes, and nothing in a worker can
touch the live document.

Workers are few and persistent -- a worker serves task after task -- because starting a
process and importing a renderer costs more than most renders (measured for one page: 0.14 s
for the interpreter, 0.25-0.4 s for the imports and 0.35 s more for the first render's font
scan, against 0.33 s for a warm render).

**Three runners.**

* :class:`WorkerPool` -- :mod:`multiprocessing` workers, the default.  ``spawn`` is the
  start method on every platform: it behaves the same on Linux, macOS and Windows, and
  never forks a process holding locks.  A task must be a module-level function, and its
  arguments and result must pickle.
* :class:`SubprocessPool` -- workers started with :mod:`subprocess`
  (``python -m ooxml_edit.tools.subworker``), which speak length-prefixed pickles over
  private pipes.  A daemonic process -- a Celery prefork worker's child, where
  ``Process.start`` fails with "daemonic processes are not allowed to have children" -- may
  still start these, and a :class:`WorkerPool` there uses one by itself (it logs a warning
  once).  Deadlines are kept the same way: the worker is killed and replaced.  A task must
  also be *registered* (:func:`worker_task`): the worker runs nothing else.
* :class:`InProcess` -- threads of this process: ``WorkerPool(0)`` (the toolbox's
  ``workers=0``) asks for it, and a :class:`WorkerPool` falls back to it when no worker
  process can be started at all.  The caller still waits at most its deadline, then gets
  ``timeout`` -- but a thread cannot be killed: the task runs on to its end in the
  background.  At most ``size`` tasks (at least one) run at a time, abandoned ones included,
  so a runaway task delays later ones -- each still bounded by its own deadline -- rather
  than piling up CPU work.  The deadline bounds the *wait*, not the work; and work that
  holds the GIL stops the threads serving other calls while it runs.
"""

from __future__ import annotations

import importlib
import io
import logging
import multiprocessing
import os
import pickle
import queue
import struct
import subprocess
import sys
import threading
import time
import weakref
from typing import Any, Callable

from .results import ToolError

logger = logging.getLogger("ooxml_edit.tools.worker")

#: What :func:`worker_task` sets, in the function's own ``__dict__``.
TASK_MARK = "__ooxml_edit_task__"

#: The module a :class:`SubprocessPool` worker runs: its fixed entry point.
ENTRY_POINT = "ooxml_edit.tools.subworker"


def daemonic() -> bool:
    """Whether this process is daemonic, so may not start multiprocessing children."""
    try:
        return bool(multiprocessing.current_process().daemon)
    except Exception:  # noqa: BLE001 -- an unusual process object: assume it may
        return False


# -- registered tasks ----------------------------------------------------------------------------


def worker_task(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Register a module-level function as work a :class:`SubprocessPool` worker may run.

    A subprocess worker imports the function's module and runs the function only if it
    carries this mark: the parent names a task, it never sends code.  The function must be
    reachable by its module and qualified name (not nested, not in ``__main__``).  Its
    arguments and result cross by pickling, as with :class:`WorkerPool`.  The mark changes
    nothing else: the other runners run any module-level function.

    The mark is the function's attribute ``__ooxml_edit_task__ = True`` (:data:`TASK_MARK`);
    a module that should not import the tool layer may set it itself."""
    setattr(fn, TASK_MARK, True)
    return fn


def _lookup(owner: Any, qualname: str) -> Any:
    for name in qualname.split("."):
        if owner is None:
            return None
        owner = getattr(owner, name, None)
    return owner


def _marked(fn: Any) -> bool:
    try:
        return vars(fn).get(TASK_MARK) is True
    except TypeError:  # no __dict__
        return False


def task_reference(fn: Any) -> str | None:
    """``"module:qualname"`` of a registered task the worker can import, else ``None``."""
    module = getattr(fn, "__module__", None)
    qualname = getattr(fn, "__qualname__", None)
    if (not _marked(fn) or not isinstance(module, str) or not isinstance(qualname, str)
            or module == "__main__" or "<" in qualname):
        return None
    if _lookup(sys.modules.get(module), qualname) is not fn:
        return None
    return f"{module}:{qualname}"


def resolve_task(reference: str) -> Callable[..., Any]:
    """The worker's side of :func:`task_reference`: import, look up, check the mark."""
    module, _, qualname = reference.partition(":")
    if not module or not qualname or module == "__main__" or "<" in qualname:
        raise PermissionError(f"{reference!r} is not a registered worker task")
    fn = _lookup(importlib.import_module(module), qualname)
    if fn is None or not callable(fn) or not _marked(fn):
        raise PermissionError(f"{reference!r} is not a registered worker task "
                              "(ooxml_edit.tools.worker_task)")
    return fn


# -- the subprocess protocol ---------------------------------------------------------------------
#
# A frame is an 8-byte big-endian length and that many bytes of pickle.  The envelope of a
# message -- a tuple of strings, numbers, bytes, lists and dicts -- is read with an unpickler
# that refuses every global, so reading one runs nothing; a task's arguments and a reply's
# value travel inside it as bytes, unpickled only once the task is known to be registered.
#
#   parent -> worker   ("hello", {"sys_path": [...], "parent": pid})
#                      ("task", "module:qualname", pickle of (args, kwargs))
#                      ("bye",)
#   worker -> parent   ("ready", pid)
#                      ("ok", pickle of the result)
#                      ("error", pickle of the exception or None, "Type: message")

_HEADER = struct.Struct(">Q")
_PROTOCOL = pickle.HIGHEST_PROTOCOL


class _PlainUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> Any:
        raise pickle.UnpicklingError(f"a protocol message may not name {module}.{name}")


def loads_plain(data: bytes) -> Any:
    """Unpickle a protocol envelope: plain data only, no globals."""
    return _PlainUnpickler(io.BytesIO(data)).load()


def write_frame(stream: Any, message: Any) -> None:
    data = pickle.dumps(message, protocol=_PROTOCOL)
    stream.write(_HEADER.pack(len(data)))
    stream.write(data)
    stream.flush()


def _read_exact(stream: Any, size: int) -> bytes | None:
    chunks: list[bytes] = []
    got = 0
    while got < size:
        chunk = stream.read(size - got)
        if not chunk:
            if got == 0:
                return None
            raise EOFError("the stream ended inside a frame")
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def read_frame(stream: Any) -> Any:
    """The next message's envelope, or ``None`` at the end of the stream."""
    header = _read_exact(stream, _HEADER.size)
    if header is None:
        return None
    size = _HEADER.unpack(header)[0]
    body = _read_exact(stream, size) if size else b""
    if body is None:
        raise EOFError("the stream ended inside a frame")
    return loads_plain(body)


# -- a pool's bookkeeping, whatever its workers are ----------------------------------------------


class _Pool:
    """At most ``size`` workers, started when first needed, each serving one task at a time."""

    def __init__(self, size: int) -> None:
        self.size = size
        self._idle: list[Any] = []
        self._count = 0
        self._closed = False
        self._condition = threading.Condition()
        #: Workers killed at a deadline, for the record (and the tests).
        self.killed: list[int] = []

    def _start(self, deadline: float, timeout: float) -> Any:
        raise NotImplementedError

    def pids(self) -> list[int]:
        """The idle workers' process ids."""
        with self._condition:
            return [worker.pid or 0 for worker in self._idle]

    def _acquire(self, deadline: float, timeout: float) -> Any:
        with self._condition:
            while True:
                if self._closed:
                    raise RuntimeError("the worker pool is closed")
                if self._idle:
                    worker = self._idle.pop()
                    if worker.alive():
                        return worker
                    self._count -= 1
                    self._forget(worker)
                    worker.dispose()
                    continue
                if self._count < self.size:
                    self._count += 1
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ToolError("timeout", f"no worker was free within {timeout:g} s",
                                    details={"timeout": timeout})
                self._condition.wait(remaining)
        try:
            return self._start(deadline, timeout)
        except BaseException:
            with self._condition:
                self._count -= 1
                self._condition.notify()
            raise

    def _forget(self, worker: Any) -> None:
        """A worker leaves the pool for good (called with the condition held)."""

    def _release(self, worker: Any) -> None:
        with self._condition:
            if self._closed:
                self._count -= 1
                self._forget(worker)
            else:
                self._idle.append(worker)
                self._condition.notify()
                return
        worker.stop()

    def _discard(self, worker: Any) -> None:
        with self._condition:
            self._forget(worker)
        worker.kill()
        with self._condition:
            self._count -= 1
            self._condition.notify()

    def _close_idle(self) -> list[Any]:
        with self._condition:
            self._closed = True
            idle, self._idle = self._idle, []
            self._count -= len(idle)
            for worker in idle:
                self._forget(worker)
            self._condition.notify_all()
        return idle


# -- multiprocessing workers -------------------------------------------------------------------


class _NoChildren(Exception):
    """Starting a worker process failed because this process may not have children."""


class _CannotStart(RuntimeError):
    """A subprocess worker could not be started (or exited before it was ready)."""


def _serve(conn: Any) -> None:
    """The multiprocessing worker's loop: run each task it is sent, send back the outcome."""
    while True:
        try:
            task = conn.recv()
        except (EOFError, OSError):
            return
        if task is None:
            return
        fn, args, kwargs = task
        try:
            reply = ("ok", fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001 -- everything goes back to the caller
            reply = ("error", exc)
        try:
            conn.send(reply)
        except Exception as exc:  # the result or the exception did not pickle
            conn.send(("error", RuntimeError(f"{type(exc).__name__}: {exc}")))


class _Worker:
    def __init__(self, context: Any) -> None:
        self.conn, child = context.Pipe(duplex=True)
        self.process = context.Process(target=_serve, args=(child,), daemon=True,
                                       name="ooxml-edit-tools-worker")
        try:
            self.process.start()
        except AssertionError as exc:
            # multiprocessing's own check, in a daemonic process (a Celery prefork child).
            self.conn.close()
            child.close()
            if "daemonic" in str(exc):
                raise _NoChildren(str(exc)) from None
            raise
        child.close()

    @property
    def pid(self) -> int | None:
        return self.process.pid

    def alive(self) -> bool:
        return self.process.is_alive()

    def dispose(self) -> None:
        self.conn.close()

    def kill(self) -> None:
        try:
            self.process.terminate()
            self.process.join(2)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(2)
        finally:
            self.conn.close()

    def stop(self) -> None:
        try:
            self.conn.send(None)
            self.process.join(2)
        except (OSError, ValueError):
            pass
        if self.process.is_alive():
            self.kill()
        else:
            self.conn.close()


# -- subprocess workers ----------------------------------------------------------------------


def _package_root() -> str:
    """The folder ``ooxml_edit`` is imported from, so the worker finds its entry point."""
    import ooxml_edit
    return os.path.dirname(os.path.dirname(os.path.abspath(ooxml_edit.__file__)))


class _Gone(Exception):
    """The subprocess worker exited, or its pipes broke."""


class _Subprocess:
    """One subprocess worker: the process, its pipes, and a thread reading its replies."""

    def __init__(self, executable: str, deadline: float, timeout: float) -> None:
        env = dict(os.environ)
        root = _package_root()
        paths = [root] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep)
                          if p and p != root]
        env["PYTHONPATH"] = os.pathsep.join(paths)
        # The current folder is not put first on the worker's path (3.11+); its path is the
        # parent's anyway, sent in the hello.
        env["PYTHONSAFEPATH"] = "1"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self.process = subprocess.Popen(
                [executable, "-m", ENTRY_POINT], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=None, env=env, close_fds=True,
                creationflags=flags)
        except OSError as exc:
            raise _CannotStart(f"could not start {executable!r}: {exc}") from None
        self.replies: queue.Queue[Any] = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True,
                                        name="ooxml-edit-tools-subprocess-reader")
        self._reader.start()
        try:
            self.send(("hello", {"sys_path": list(sys.path), "parent": os.getpid()}))
            reply = self.wait(deadline - time.monotonic())
        except _Gone:
            code = self._exit_code()
            self.kill()
            raise _CannotStart(f"the worker process exited as it started (exit code {code}); "
                               "its error is on stderr") from None
        if reply is None:
            self.kill()
            raise ToolError("timeout", f"the worker process did not start within {timeout:g} s",
                            details={"timeout": timeout})
        if not (isinstance(reply, tuple) and reply and reply[0] == "ready"):
            self.kill()
            raise _CannotStart(f"the worker process answered {reply!r} to its hello")

    @property
    def pid(self) -> int:
        return self.process.pid

    def _exit_code(self) -> int | None:
        try:
            return self.process.wait(2)
        except subprocess.TimeoutExpired:
            return None

    def _read(self) -> None:
        stream = self.process.stdout
        try:
            while True:
                message = read_frame(stream)
                if message is None:
                    break
                self.replies.put(message)
        except Exception:  # noqa: BLE001 -- a broken stream: the worker is gone either way
            pass
        self.replies.put(None)

    def send(self, message: Any) -> None:
        try:
            write_frame(self.process.stdin, message)
        except (OSError, ValueError) as exc:  # a broken pipe, or a closed one
            raise _Gone(str(exc) or "it exited") from None

    def wait(self, timeout: float) -> Any:
        """The next reply, or ``None`` when ``timeout`` passes first."""
        try:
            reply = self.replies.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None
        if reply is None:
            raise _Gone("it exited")
        return reply

    def alive(self) -> bool:
        return self.process.poll() is None

    def dispose(self) -> None:
        self._close_pipes()

    def _close_pipes(self) -> None:
        for stream in (self.process.stdin, self.process.stdout):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass

    def kill(self) -> None:
        try:
            self.process.kill()
        except OSError:
            pass
        try:
            self.process.wait(5)
        except subprocess.TimeoutExpired:
            pass
        self._reader.join(1)
        self._close_pipes()

    def ask_to_stop(self) -> None:
        try:
            self.send(("bye",))
            self.process.stdin.close()
        except (_Gone, OSError, ValueError):
            pass

    def finish(self, grace: float = 2.0) -> None:
        try:
            self.process.wait(grace)
        except subprocess.TimeoutExpired:
            self.kill()
            return
        self._reader.join(1)
        self._close_pipes()

    def stop(self) -> None:
        self.ask_to_stop()
        self.finish()


def _shut_down(workers: set) -> None:
    """Stop every worker still running: at :meth:`SubprocessPool.close` or interpreter exit."""
    live = list(workers)
    workers.clear()
    for worker in live:
        worker.ask_to_stop()
    for worker in live:
        worker.finish()


def _outcome(reply: Any) -> Any:
    kind = reply[0] if isinstance(reply, tuple) and reply else None
    if kind == "ok":
        try:
            return pickle.loads(reply[1])
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"the worker's result did not unpickle: "
                               f"{type(exc).__name__}: {exc}") from None
    if kind == "error":
        _, blob, text = reply
        error: Any = None
        if blob is not None:
            try:
                error = pickle.loads(blob)
            except Exception:  # noqa: BLE001 -- an exception that does not round-trip
                error = None
        if not isinstance(error, BaseException):
            error = RuntimeError(text)
        raise error
    raise ToolError("internal", f"the worker process answered {reply!r}")


class SubprocessPool(_Pool):
    """At most ``size`` subprocess workers (``python -m ooxml_edit.tools.subworker``), started
    when first needed and kept.

    ``run(fn, *args, timeout=...)`` returns ``fn(*args)`` from a worker, re-raises what it
    raised, and raises :class:`~.results.ToolError` ``timeout`` when the deadline passes --
    waiting for a free worker, and starting one, count against it -- after killing the
    worker; the next call starts another.  A worker that dies mid-task is replaced and the
    call reports ``internal``.  Usable where multiprocessing is not: in a daemonic process.

    ``fn`` must be registered (:func:`worker_task`).  One that is not -- an application's
    own function, a lambda, one in ``__main__`` -- runs in-process instead (an
    :class:`InProcess` of the same size; a warning is logged once per function), as it did
    before this pool existed.

    The worker gets this process's environment and ``sys.path`` as it starts, and nothing
    later: whatever a task needs that may differ -- font folders above all -- goes in its
    arguments.  Its stdin and stdout carry the protocol only: it moves them to private file
    descriptors first, so what a library prints goes to stderr (shared with this process).
    It stops when this pool is closed, at this interpreter's exit, or when this process is
    gone (its pipe closes; a busy worker notices within half a second on POSIX, at once on
    Windows).
    """

    def __init__(self, size: int = 2, *, executable: str | None = None) -> None:
        if size < 1:
            raise ValueError("a subprocess pool has at least one worker")
        super().__init__(size)
        self.executable = executable or sys.executable
        self._inline: InProcess | None = None
        self._warned: set[str] = set()
        #: Every worker started and not yet stopped, idle or busy: what exit stops.
        self._all: set[_Subprocess] = set()
        self._finalizer = weakref.finalize(self, _shut_down, self._all)

    @property
    def in_process(self) -> bool:
        return False

    @property
    def abandoned(self) -> int:
        """Unregistered tasks run in-process and given up at their deadline."""
        return self._inline.abandoned if self._inline is not None else 0

    def run(self, fn: Callable[..., Any], *args: Any, timeout: float, **kwargs: Any) -> Any:
        if self._closed:
            raise RuntimeError("the worker pool is closed")
        reference = task_reference(fn)
        if reference is None:
            return self._run_inline(fn, args, kwargs, timeout)
        payload = pickle.dumps((args, kwargs), protocol=_PROTOCOL)
        deadline = time.monotonic() + timeout
        worker = self._acquire(deadline, timeout)
        try:
            worker.send(("task", reference, payload))
            reply = worker.wait(deadline - time.monotonic())
        except _Gone as exc:
            self._discard(worker)
            raise ToolError("internal", f"the worker process failed: {exc}") from None
        except BaseException:
            self._discard(worker)  # a task may be in flight: the worker cannot be reused
            raise
        if reply is None:
            self.killed.append(worker.pid or 0)
            self._discard(worker)
            raise ToolError("timeout", f"the work did not finish within {timeout:g} s; "
                            "it was stopped", details={"timeout": timeout})
        self._release(worker)
        return _outcome(reply)

    def _run_inline(self, fn: Callable[..., Any], args: tuple, kwargs: dict,
                    timeout: float) -> Any:
        name = f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__qualname__', repr(fn))}"
        with self._condition:
            if self._inline is None:
                self._inline = InProcess(self.size)
            first = name not in self._warned
            self._warned.add(name)
        if first:
            logger.warning("%s is not a registered worker task (ooxml_edit.tools.worker_task), "
                           "so it runs in-process: a deadline bounds the wait, not the work",
                           name)
        return self._inline.run(fn, *args, timeout=timeout, **kwargs)

    def _start(self, deadline: float, timeout: float) -> _Subprocess:
        worker = _Subprocess(self.executable, deadline, timeout)
        with self._condition:
            self._all.add(worker)
        return worker

    def _forget(self, worker: Any) -> None:
        self._all.discard(worker)

    def close(self) -> None:
        if self._inline is not None:
            self._inline.close()
        _shut_down(set(self._close_idle()))

    def __enter__(self) -> "SubprocessPool":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# -- in-process --------------------------------------------------------------------------------


class InProcess:
    """Tasks run in threads of this process, at most ``size`` at a time, each waited for at
    most its deadline.  A task past its deadline is abandoned, not stopped: it runs on, in its
    slot, until it ends (see the module's notes).  The same ``run`` and ``close`` as
    :class:`WorkerPool`, so an application may pass one as a toolbox's ``runner``."""

    def __init__(self, size: int = 1) -> None:
        self.size = max(1, size)
        self._slots = threading.BoundedSemaphore(self.size)
        self._lock = threading.Lock()
        self._closed = False
        #: Tasks given up at a deadline: all so far, and those still running.
        self.abandoned = 0
        self.running_abandoned = 0

    def run(self, fn: Callable[..., Any], *args: Any, timeout: float, **kwargs: Any) -> Any:
        if self._closed:
            raise RuntimeError("the worker pool is closed")
        deadline = time.monotonic() + timeout
        if not self._slots.acquire(timeout=max(0.0, timeout)):
            raise ToolError("timeout", f"no worker was free within {timeout:g} s",
                            details={"timeout": timeout, "in_process": True})
        outcome: dict[str, Any] = {}
        done = threading.Event()
        state = {"abandoned": False}

        def work() -> None:
            try:
                outcome["value"] = fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 -- everything goes back to the caller
                outcome["error"] = exc
            finally:
                with self._lock:
                    if state["abandoned"]:
                        self.running_abandoned -= 1
                    done.set()
                self._slots.release()

        thread = threading.Thread(target=work, name="ooxml-edit-tools-inprocess", daemon=True)
        try:
            thread.start()
        except BaseException:
            self._slots.release()
            raise
        if not done.wait(max(0.0, deadline - time.monotonic())):
            with self._lock:
                if not done.is_set():
                    state["abandoned"] = True
                    self.abandoned += 1
                    self.running_abandoned += 1
            if state["abandoned"]:
                raise ToolError("timeout", f"the work did not finish within {timeout:g} s; "
                                "it was abandoned (in-process work cannot be stopped)",
                                details={"timeout": timeout, "in_process": True})
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("value")

    def pids(self) -> list[int]:
        return []

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> "InProcess":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# -- the default pool --------------------------------------------------------------------------

#: :class:`WorkerPool`'s ``fallback`` values.
FALLBACKS = (True, False, "subprocess", "in-process")


class WorkerPool(_Pool):
    """At most ``size`` worker processes, started when first needed.

    ``run(fn, *args, timeout=...)`` returns ``fn(*args)`` from a worker, re-raises what it
    raised, and raises :class:`~.results.ToolError` ``timeout`` when the deadline passes --
    waiting for a free worker counts against the deadline too -- after killing the worker.
    A worker that dies mid-task is replaced and the call reports ``internal``.

    ``size=0`` runs every task in-process (:class:`InProcess`, one at a time).

    ``fallback`` says what a pool does where multiprocessing may not start workers -- a
    daemonic process (``multiprocessing.current_process().daemon``), or one whose first
    start fails that way:

    * ``True`` (the default) or ``"subprocess"``: a :class:`SubprocessPool` of the same size
      runs the work (:attr:`runner` is ``"subprocesses"``) -- deadlines still kill.  Where a
      subprocess cannot be started either, the work runs in-process.
    * ``"in-process"``: an :class:`InProcess` of the same size runs it (:attr:`in_process`).
    * ``False``: no fallback; starting a worker fails as multiprocessing makes it fail.

    Each fallback logs a warning once.
    """

    def __init__(self, size: int = 2, *, start_method: str = "spawn",
                 fallback: bool | str = True) -> None:
        if size < 0:
            raise ValueError("a pool's size is 0 (in-process) or more")
        if not any(fallback is value or fallback == value and type(fallback) is type(value)
                   for value in FALLBACKS):
            raise ValueError(f"fallback is one of {FALLBACKS}, not {fallback!r}")
        super().__init__(size)
        self._context = multiprocessing.get_context(start_method)
        self._fallback = fallback
        self._inline: InProcess | None = None
        self._subprocesses: SubprocessPool | None = None
        if size == 0:
            self._go_in_process(None)
        elif fallback and daemonic():
            self._go_elsewhere("this process is daemonic, so it may not start "
                               "multiprocessing workers")

    @property
    def in_process(self) -> bool:
        """Whether tasks run in this process: asked for (``size=0``), or fallen back to."""
        return self._inline is not None

    @property
    def runner(self) -> str:
        """What runs the work: ``"processes"``, ``"subprocesses"`` or ``"in-process"``."""
        if self._inline is not None:
            return "in-process"
        return "subprocesses" if self._subprocesses is not None else "processes"

    @property
    def abandoned(self) -> int:
        """In-process tasks given up at their deadline (they ran on); 0 with processes."""
        if self._inline is not None:
            return self._inline.abandoned
        return self._subprocesses.abandoned if self._subprocesses is not None else 0

    def _go_in_process(self, why: str | None) -> InProcess:
        with self._condition:
            if self._inline is None:
                self._inline = InProcess(max(1, self.size))
                if why:
                    logger.warning("ooxml-edit tools run their work in-process: %s; a "
                                   "deadline bounds the wait, not the work", why)
            return self._inline

    def _go_elsewhere(self, why: str) -> Any:
        if self._fallback == "in-process":
            return self._go_in_process(why)
        with self._condition:
            if self._inline is not None:
                return self._inline
            if self._subprocesses is None:
                self._subprocesses = SubprocessPool(max(1, self.size))
                # One record of the workers killed at a deadline, whichever ran them.
                self._subprocesses.killed = self.killed
                logger.warning("ooxml-edit tools run their work in subprocess workers: %s",
                               why)
            return self._subprocesses

    def _run_elsewhere(self, runner: Any, fn: Callable[..., Any], args: tuple, kwargs: dict,
                       deadline: float) -> Any:
        try:
            return runner.run(fn, *args, timeout=max(0.0, deadline - time.monotonic()),
                              **kwargs)
        except _CannotStart as exc:
            with self._condition:
                if self._subprocesses is runner:
                    self._subprocesses = None
            runner.close()
            inline = self._go_in_process(f"no subprocess worker either: {exc}")
            return inline.run(fn, *args, timeout=max(0.0, deadline - time.monotonic()),
                              **kwargs)

    def run(self, fn: Callable[..., Any], *args: Any, timeout: float, **kwargs: Any) -> Any:
        if self._closed:
            raise RuntimeError("the worker pool is closed")
        deadline = time.monotonic() + timeout
        if self._inline is not None:
            return self._inline.run(fn, *args, timeout=timeout, **kwargs)
        if self._subprocesses is not None:
            return self._run_elsewhere(self._subprocesses, fn, args, kwargs, deadline)
        try:
            worker = self._acquire(deadline, timeout)
        except _NoChildren as exc:
            if not self._fallback:
                raise AssertionError(str(exc)) from None
            return self._run_elsewhere(self._go_elsewhere(str(exc)), fn, args, kwargs,
                                       deadline)
        try:
            worker.conn.send((fn, args, kwargs))
            if not worker.conn.poll(max(0.0, deadline - time.monotonic())):
                self.killed.append(worker.pid or 0)
                self._discard(worker)
                worker = None
                raise ToolError("timeout", f"the work did not finish within {timeout:g} s; "
                                "it was stopped", details={"timeout": timeout})
            status, value = worker.conn.recv()
        except (EOFError, ConnectionError, BrokenPipeError) as exc:
            if worker is not None:
                self._discard(worker)
                worker = None
            raise ToolError("internal", f"the worker process failed: {exc or 'it exited'}") from None
        except BaseException:
            if worker is not None:
                self._release(worker)
                worker = None
            raise
        else:
            self._release(worker)
            worker = None
        if status == "error":
            raise value
        return value

    def pids(self) -> list[int]:
        if self._subprocesses is not None:
            return self._subprocesses.pids()
        return super().pids()

    def close(self) -> None:
        if self._inline is not None:
            self._inline.close()
        if self._subprocesses is not None:
            self._subprocesses.close()
        for worker in self._close_idle():
            worker.stop()

    def __enter__(self) -> "WorkerPool":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _start(self, deadline: float, timeout: float) -> _Worker:
        return _Worker(self._context)
