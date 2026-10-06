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
"""

from __future__ import annotations

import multiprocessing
import threading
import time
from typing import Any, Callable

from .results import ToolError


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
        self.process.start()
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


class WorkerPool:
    """At most ``size`` worker processes, started when first needed.

    ``run(fn, *args, timeout=...)`` returns ``fn(*args)`` from a worker, re-raises what it
    raised, and raises :class:`~.results.ToolError` ``timeout`` when the deadline passes --
    waiting for a free worker counts against the deadline too -- after killing the worker.
    A worker that dies mid-task is replaced and the call reports ``internal``.
    """

    def __init__(self, size: int = 2, *, start_method: str = "spawn") -> None:
        if size < 1:
            raise ValueError("a pool needs at least one worker")
        self.size = size
        self._context = multiprocessing.get_context(start_method)
        self._idle: list[_Worker] = []
        self._count = 0
        self._closed = False
        self._condition = threading.Condition()
        #: Workers killed at a deadline, for the record (and the tests).
        self.killed: list[int] = []

    def run(self, fn: Callable[..., Any], *args: Any, timeout: float, **kwargs: Any) -> Any:
        deadline = time.monotonic() + timeout
        worker = self._acquire(deadline, timeout)
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
