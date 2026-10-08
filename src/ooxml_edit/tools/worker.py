"""A process pool for CPU-bound work -- rendering, layout -- with deadlines that are kept.

Rendering and text layout are pure Python, so threads would serialize on the GIL and a
runaway render could not be stopped.  Each task runs in a worker process instead; the
caller waits at most its deadline, and a worker still busy when the deadline passes is
killed and replaced, and the call reports ``timeout``.  The document is passed as bytes
(``document.to_bytes()``): the libraries render from bytes, and nothing in a worker can
touch the live document.

The pool is small and persistent -- a worker serves task after task -- because starting a
process and importing a renderer costs more than most renders.  ``spawn`` is the default
start method on every platform: it behaves the same on Linux, macOS and Windows, and never
forks a process holding locks.  A task must be a module-level function, and its arguments
and result must pickle.

**In-process.**  A process that may not start children -- a daemonic one, such as a Celery
prefork worker's child, where ``Process.start`` fails with "daemonic processes are not
allowed to have children" -- runs the work in-process instead.  ``WorkerPool(0)`` (the
toolbox's ``workers=0``) asks for that; a pool that finds itself in a daemonic process
(``multiprocessing.current_process().daemon``), or whose first start fails that way, falls
back to it by itself and logs a warning once.  In-process, each task runs in a thread of
this process and the caller still waits at most its deadline, then gets ``timeout`` -- but
a thread cannot be killed: the task runs on to its end in the background.  At most ``size``
tasks (at least one) run at a time, abandoned ones included, so a runaway task delays later
ones -- each still bounded by its own deadline -- rather than piling up CPU work.  The
deadline bounds the *wait*, not the work; and pure-Python work shares the GIL with the
threads serving other calls.
"""

from __future__ import annotations

import logging
import multiprocessing
import threading
import time
from typing import Any, Callable

from .results import ToolError

logger = logging.getLogger("ooxml_edit.tools.worker")


def daemonic() -> bool:
    """Whether this process is daemonic, so may not start child processes."""
    try:
        return bool(multiprocessing.current_process().daemon)
    except Exception:  # noqa: BLE001 -- an unusual process object: assume it may
        return False


class _NoChildren(Exception):
    """Starting a worker process failed because this process may not have children."""


def _serve(conn: Any) -> None:
    """The worker's loop: run each task it is sent, and send back its result or error."""
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


class WorkerPool:
    """At most ``size`` worker processes, started when first needed.

    ``run(fn, *args, timeout=...)`` returns ``fn(*args)`` from a worker, re-raises what it
    raised, and raises :class:`~.results.ToolError` ``timeout`` when the deadline passes --
    waiting for a free worker counts against the deadline too -- after killing the worker.
    A worker that dies mid-task is replaced and the call reports ``internal``.

    ``size=0`` runs every task in-process (:class:`InProcess`, one at a time).  So does a
    pool in a daemonic process, by itself, at its ``size``; :attr:`in_process` then says so.
    ``fallback=False`` turns that off: starting a worker then fails as multiprocessing makes
    it fail.
    """

    def __init__(self, size: int = 2, *, start_method: str = "spawn",
                 fallback: bool = True) -> None:
        if size < 0:
            raise ValueError("a pool's size is 0 (in-process) or more")
        self.size = size
        self._context = multiprocessing.get_context(start_method)
        self._idle: list[_Worker] = []
        self._count = 0
        self._closed = False
        self._condition = threading.Condition()
        self._fallback = fallback
        self._inline: InProcess | None = None
        #: Workers killed at a deadline, for the record (and the tests).
        self.killed: list[int] = []
        if size == 0:
            self._go_in_process(None)
        elif fallback and daemonic():
            self._go_in_process("this process is daemonic, so it may not start workers")

    @property
    def in_process(self) -> bool:
        """Whether tasks run in this process: asked for (``size=0``), or fallen back to."""
        return self._inline is not None

    @property
    def abandoned(self) -> int:
        """In-process tasks given up at their deadline (they ran on); 0 with processes."""
        return self._inline.abandoned if self._inline is not None else 0

    def _go_in_process(self, why: str | None) -> InProcess:
        with self._condition:
            if self._inline is None:
                self._inline = InProcess(max(1, self.size))
                if why:
                    logger.warning("ooxml-edit tools run their work in-process: %s; a "
                                   "deadline bounds the wait, not the work", why)
            return self._inline

    def run(self, fn: Callable[..., Any], *args: Any, timeout: float, **kwargs: Any) -> Any:
        if self._inline is not None:
            if self._closed:
                raise RuntimeError("the worker pool is closed")
            return self._inline.run(fn, *args, timeout=timeout, **kwargs)
        deadline = time.monotonic() + timeout
        try:
            worker = self._acquire(deadline, timeout)
        except _NoChildren as exc:
            if not self._fallback:
                raise AssertionError(str(exc)) from None
            inline = self._go_in_process(str(exc))
            return inline.run(fn, *args, timeout=max(0.0, deadline - time.monotonic()),
                              **kwargs)
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
        with self._condition:
            return [worker.pid or 0 for worker in self._idle]

    def close(self) -> None:
        if self._inline is not None:
            self._inline.close()
        with self._condition:
            self._closed = True
            idle, self._idle = self._idle, []
            self._count -= len(idle)
            self._condition.notify_all()
        for worker in idle:
            worker.stop()

    def __enter__(self) -> "WorkerPool":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- internals ---------------------------------------------------------------------------

    def _acquire(self, deadline: float, timeout: float) -> _Worker:
        with self._condition:
            while True:
                if self._closed:
                    raise RuntimeError("the worker pool is closed")
                if self._idle:
                    worker = self._idle.pop()
                    if worker.process.is_alive():
                        return worker
                    self._count -= 1
                    worker.conn.close()
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
            return _Worker(self._context)
        except BaseException:
            with self._condition:
                self._count -= 1
                self._condition.notify()
            raise

    def _release(self, worker: _Worker) -> None:
        with self._condition:
            if self._closed:
                self._count -= 1
            else:
                self._idle.append(worker)
                self._condition.notify()
                return
        worker.stop()

    def _discard(self, worker: _Worker) -> None:
        worker.kill()
        with self._condition:
            self._count -= 1
            self._condition.notify()
