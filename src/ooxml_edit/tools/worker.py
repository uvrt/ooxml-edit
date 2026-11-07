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

**Starting is not the call's time.**  A new worker first runs the pool's ``warm_up`` tasks
-- a format's imports and font index -- and only then takes work; starting and warming are
bounded by ``start_timeout``, apart from any task's deadline, and waiting for a worker that
is starting does not count against a deadline either.  So a cold pool, or a worker replaced
after a kill, cannot make a call time out, nor a slow start kill a worker again and again.
:meth:`WorkerPool.warm` starts the workers ahead of the first call, in the background.

**A worker that exits as it starts fails the call at once.**  A starting worker is waited
for on its pipe and on its process together, so one that exits before it is ready is seen
at once, not at ``start_timeout``; the error names the cause -- its exit code, or the
signal that stopped it.  The usual cause under ``spawn`` is the program's own main module:
a worker imports it again before its loop runs, and a script with no
``if __name__ == "__main__":`` guard runs its work there once more -- a render (and the
script's work after it) in every worker, the parent waiting the while.  A worker that gets
that far -- a :class:`WorkerPool`'s ``run`` or a toolbox's ``dispatch``, while the worker
is still being started -- stops at once instead, saying so on stderr, with exit code
:data:`UNGUARDED_EXIT`, and the parent's error says plainly what to do: put the work under
the guard, or use ``workers=0`` or a :class:`SubprocessPool`, whose workers never import
the main module.  An exit with a code as a worker starts (that one, an import error, a
``sys.exit`` in the main module) happens again for every worker, so the pool does not try
again: a :class:`WorkerPool` falls back as it does where multiprocessing may not start
children (below; the warning names the cause), and with ``fallback=False`` every later call
fails at once with the same error.  A worker stopped by a signal as it starts (killed) is
not remembered: the next call starts another.  A worker that is alive keeps its whole
``start_timeout``, however slowly it starts.  A failure met by :meth:`WorkerPool.warm`, in
the background, is the one the call that needs a worker then gets.

**How many.**  :func:`default_workers` is what this process may use -- the CPUs it may run
on and its container's CPU quota (cgroup v2 ``cpu.max``, v1 ``cpu.cfs_quota_us``), never
``os.cpu_count()`` alone, which counts the host's -- capped at
:data:`MAX_DEFAULT_WORKERS`.  :func:`run_all` runs several tasks at once over a pool's
workers, results in order: one call's views, in chunks.

**Affinity: a task to the worker that holds its state.**  A worker serves task after task,
so what one task leaves in its process -- a format's layout of a document, what its text
measured -- is there for the next task in that worker, and nowhere else.  ``run(...,
affinity=key)`` names such state: the pool remembers which worker took the last task with
``key`` (:data:`AFFINITY_KEYS` keys, the oldest forgotten first; a worker that is killed,
dies or is stopped takes its keys with it) and sends the next task with ``key`` there --
at once when it is idle; when it is busy, the task waits for it at most ``affinity_wait``
seconds (the caller's rule: the time the work would take done afresh, without the state,
so waiting never costs more than going elsewhere would; ``None``, the default, waits as
long as the task's deadline allows, ``0`` not at all), then takes any idle worker, or a
new one.  Tasks waiting for one holder take it in the order they came, and no other task
takes a worker that tasks are waiting for.  A key no worker holds goes to any idle worker.  Work without a key prefers the
idle workers that hold no key, or held one longest ago, so that the holders stay free for
their keys.  Affinity only chooses the worker: a task must give the same result on any
worker (state it does not find, it makes again), and the pool counts where its keyed
tasks went (:attr:`_Pool.affinity_counts`).  :class:`InProcess` and
:class:`ThreadRunner` take ``affinity`` and need it not: their tasks share one process.

**Lanes: rendering yields to the rest.**  ``run(..., lane="render")`` marks a task as
drawing images (:data:`RENDER_LANE`): rasterising a view of a document, which takes long and
which no other call waits on.  Such a task takes a worker only while no other task is
waiting for one -- a layout, a check -- so a turn of images never starves the layouts
behind them, and at most :attr:`_Pool.render_workers` of them hold a worker at once
(``None``, the default: all of them; the CPUs used are the pool's either way).  Among idle
workers it prefers those holding no affinity key, as other work without a key does.  And it
is not stopped at its deadline: the caller gets ``timeout`` then, and the worker goes on
with it for at most as long again (:data:`OVERRUN`; ``run(..., overrun=True)`` asks the same
for other work, a render's layout) -- an image that is slow, not stuck,
is finished, handed to ``on_late`` (the caller keeps it for the next call) and the worker
kept, with its warm state and its layouts; only one still busy then is stopped.  Starting
a worker again costs more than most images (its imports, its fonts, its first layout).

**Three runners** (and a fourth, below).

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

**A fourth, opt-in: threads with the GIL off.**  On a free-threaded Python with the GIL off
(``python3.15t``, ``PYTHON_GIL=0``, lxml 7: :func:`gil_enabled`), threads of one process
run side by side, so :class:`ThreadRunner` (``Toolbox(runner="threads")``) runs the work
there: no worker to start, warm-up once per process (:func:`warm_once`), nothing pickled
across but the arguments that could change.  Its deadlines are :class:`InProcess`'s -- a
task past one is abandoned, not stopped, and keeps its slot until it ends -- with a cap on
how many may be running before new work is refused.  The process pool stays the default:
only a process can be stopped at a deadline.
"""

from __future__ import annotations

import importlib
import io
import math
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
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.connection import wait as wait_for
from typing import Any, Callable, Sequence

from .results import ToolError
from ..errors import ArgumentError

logger = logging.getLogger("ooxml_edit.tools.worker")

#: What :func:`worker_task` sets, in the function's own ``__dict__``.
TASK_MARK = "__ooxml_edit_task__"

#: The module a :class:`SubprocessPool` worker runs: its fixed entry point.
ENTRY_POINT = "ooxml_edit.tools.subworker"


#: The most workers :func:`default_workers` gives: each is a process with its own renderer
#: and font index (a few hundred MB), and a call's chunks rarely gain past eight.
MAX_DEFAULT_WORKERS = 8
#: How many affinity keys a pool remembers (each, the worker that took its last task):
#: the oldest is forgotten first, and then goes to any idle worker.
AFFINITY_KEYS = 256
#: What :attr:`_Pool.affinity_counts` counts, for each task given a key: ``hit``, its
#: holder was idle; ``waited``, its holder became idle within ``affinity_wait``;
#: ``elsewhere``, it was busy longer and another worker took the task; ``unheld``, no worker
#: held the key (a first task, or one whose holder was killed, died or forgot it).
AFFINITY_OUTCOMES = ("hit", "waited", "elsewhere", "unheld")
#: The lane of a task that draws images (the module's notes): it yields to other work, and
#: its worker is not stopped at its deadline unless it is still busy :data:`OVERRUN` times
#: the deadline later.
RENDER_LANE = "render"
#: The lanes ``run`` takes: ``None`` for the rest of the work.
LANES = (None, RENDER_LANE)
#: How much longer than its deadline a :data:`RENDER_LANE` task's worker may go on with it
#: (as a share of the deadline) before it is stopped.
OVERRUN = 1.0
#: :attr:`_Pool.start_timeout`'s default: seconds to start one worker and warm it.
START_TIMEOUT = 120.0
#: A :class:`WorkerPool` worker's process name, which spawn sets before the main module
#: is imported again in it.
WORKER_NAME = "ooxml-edit-tools-worker"
#: The exit code of a worker that stopped because the program's main module, imported
#: again as the worker started, reached work outside its ``__main__`` guard.
UNGUARDED_EXIT = 86


def _cgroup_paths() -> tuple[str | None, str | None]:
    """This process's cgroup: its v2 path, and its v1 ``cpu`` controller's path."""
    v2 = v1 = None
    try:
        with open("/proc/self/cgroup", encoding="utf-8") as handle:
            for line in handle:
                number, _, rest = line.strip().partition(":")
                controllers, _, path = rest.partition(":")
                if number == "0" and controllers == "":
                    v2 = path or "/"
                elif "cpu" in controllers.split(","):
                    v1 = path or "/"
    except OSError:
        pass
    return v2, v1


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _ancestors(path: str) -> list[str]:
    """``path`` and every cgroup above it, the root last: a limit anywhere above holds."""
    parts = [part for part in path.split("/") if part]
    return ["/" + "/".join(parts[:end]) for end in range(len(parts), -1, -1)]


def cgroup_cpu_quota(root: str = "/sys/fs/cgroup") -> float | None:
    """The CPUs this process's container may use, as its cgroup's quota says -- v2
    ``cpu.max`` (``"200000 100000"``: 2), else v1 ``cpu.cfs_quota_us`` over
    ``cpu.cfs_period_us`` -- the smallest along the cgroup and those above it; ``None``
    where none is set (``max``, ``-1``) or there is no cgroup file system (macOS, Windows)."""
    v2, v1 = _cgroup_paths()
    quotas: list[float] = []
    for path in _ancestors(v2 or "/"):
        text = _read(os.path.join(root, path.lstrip("/"), "cpu.max"))
        if text:
            fields = text.split()
            if fields[0] != "max" and len(fields) > 1:
                try:
                    quota, period = float(fields[0]), float(fields[1])
                except ValueError:
                    continue
                if quota > 0 and period > 0:
                    quotas.append(quota / period)
    if not quotas:
        bases = [os.path.join(root, name) for name in ("cpu", "cpu,cpuacct", "cpuacct,cpu")]
        for base in bases:
            for path in _ancestors(v1 or "/"):
                quota_text = _read(os.path.join(base, path.lstrip("/"), "cpu.cfs_quota_us"))
                period_text = _read(os.path.join(base, path.lstrip("/"), "cpu.cfs_period_us"))
                try:
                    q, p = float(quota_text or "-1"), float(period_text or "0")
                except ValueError:
                    continue
                if q > 0 and p > 0:
                    quotas.append(q / p)
            if quotas:
                break
    return min(quotas) if quotas else None


def available_cpus() -> int:
    """The CPUs this process may use: the fewest of those it may run on
    (``os.process_cpu_count``, ``os.sched_getaffinity``) and its container's CPU quota
    (:func:`cgroup_cpu_quota`, rounded up); ``os.cpu_count()`` only where neither says.
    At least 1."""
    counts: list[int] = []
    process_count = getattr(os, "process_cpu_count", None)            # 3.13+
    affinity = getattr(os, "sched_getaffinity", None)                  # Linux
    try:
        if process_count is not None:
            found = process_count()
            if found:
                counts.append(found)
        elif affinity is not None:
            counts.append(len(affinity(0)))
    except OSError:
        pass
    if not counts:
        counts.append(os.cpu_count() or 1)
    quota = cgroup_cpu_quota()
    if quota is not None:
        counts.append(math.ceil(quota))
    return max(1, min(counts))


def default_workers() -> int:
    """The workers a toolbox starts by default: :func:`available_cpus`, at most
    :data:`MAX_DEFAULT_WORKERS`."""
    return max(1, min(available_cpus(), MAX_DEFAULT_WORKERS))


def run_all(runner: Any, fn: Callable[..., Any], calls: Sequence[tuple],
            timeouts: Sequence[float], options: "Sequence[dict] | None" = None) -> list[Any]:
    """``fn(*args)`` for each ``args`` of ``calls``, at once over ``runner``'s workers (a
    thread per call waits on ``runner.run``), each with its own deadline; the results in
    order, a call's exception in its place (raised by none: the caller decides).  One call
    runs in this thread.  ``options``: each call's further keywords for ``runner.run``
    (``lane``, ``queue_timeout``, ``on_late``), what the runner takes."""
    def one(index: int) -> Any:
        try:
            return runner.run(fn, *calls[index], timeout=timeouts[index],
                              **(options[index] if options else {}))
        except BaseException as exc:  # noqa: BLE001 -- reported in its place
            return exc

    if len(calls) <= 1:
        return [one(index) for index in range(len(calls))]
    with ThreadPoolExecutor(max_workers=len(calls),
                            thread_name_prefix="ooxml-edit-tools-run-all") as executor:
        return list(executor.map(one, range(len(calls))))


def takes_affinity(runner: Any) -> bool:
    """Whether ``runner.run`` takes ``affinity`` and ``affinity_wait`` (the module's notes):
    this module's runners do; an application's own runner says so with a true
    ``takes_affinity`` attribute, else they are left out of what it is handed."""
    return isinstance(runner, (_Pool, _Threads)) or getattr(runner, "takes_affinity", False) is True


def takes_lane(runner: Any) -> bool:
    """Whether ``runner.run`` takes ``lane`` and ``on_late`` (the module's notes): this
    module's runners do; an application's own runner says so with a true ``takes_lane``
    attribute, else they are left out of what it is handed."""
    return isinstance(runner, (_Pool, _Threads)) or getattr(runner, "takes_lane", False) is True


def bootstrapping_worker() -> bool:
    """Whether this process is a :class:`WorkerPool` worker that is still starting: spawn
    (or forkserver) importing the parent's main module in it, before the worker's loop."""
    try:
        current = multiprocessing.current_process()
        return bool(getattr(current, "_inheriting", False)) and current.name == WORKER_NAME
    except Exception:  # noqa: BLE001 -- an unusual process object: not a starting worker
        return False


#: What a worker that stops for :data:`UNGUARDED_EXIT` writes to stderr.
UNGUARDED_TEXT = (
    "ooxml-edit: this worker process stops as it starts: the program's main module, "
    "imported again in every worker process (spawn), reached work that is not under "
    "`if __name__ == \"__main__\":`. Put that work under the guard, or use Toolbox(workers=0) "
    "or a SubprocessPool, whose workers do not import the main module.\n")


def refuse_while_bootstrapping() -> None:
    """In a :class:`WorkerPool` worker still starting (:func:`bootstrapping_worker`), stop
    the process at once with :data:`UNGUARDED_EXIT`, saying why on stderr: the main module
    is running the program's work again here.  Elsewhere, nothing."""
    if not bootstrapping_worker():
        return
    try:
        sys.stderr.write(UNGUARDED_TEXT)
        sys.stderr.flush()
    except Exception:  # noqa: BLE001 -- no stderr: the exit code says it
        pass
    os._exit(UNGUARDED_EXIT)


def exit_cause(code: int | None) -> str:
    """Why a worker process ended before it was ready, from its exit code."""
    if code == UNGUARDED_EXIT:
        return (f"it stopped (exit code {code}) because this program's main module, "
                "imported again in every worker process (spawn), reached work that is not "
                "under `if __name__ == \"__main__\":` -- put that work under the guard, or use "
                "workers=0 or a SubprocessPool, whose workers do not import the main module")
    if code is None:
        return "it exited; its error, if any, is on stderr"
    if code < 0:
        return f"it was stopped by signal {-code}"
    return (f"it exited with exit code {code}; its error is on stderr (under spawn a "
            "worker first imports this program's main module: an error or sys.exit there "
            "ends it)")


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


@worker_task
def _ready() -> int:
    """What a new worker is asked first: it answers once it is running."""
    return os.getpid()


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
    """At most ``size`` workers, started when first needed, each serving one task at a time.

    A new worker runs ``warm_up`` -- functions of no arguments, registered tasks for a
    subprocess worker -- before its first task; starting and warming one is bounded by
    ``start_timeout`` and is no task's time (see the module's notes)."""

    def __init__(self, size: int, *, warm_up: Sequence[Callable[[], Any]] = (),
                 start_timeout: float = START_TIMEOUT) -> None:
        self.size = size
        #: What each new worker runs before its first task (a failure is logged, not fatal).
        self.warm_up: list[Callable[[], Any]] = list(warm_up)
        self.start_timeout = start_timeout
        self._idle: list[Any] = []
        self._count = 0
        self._starting = 0
        self._closed = False
        self._condition = threading.Condition()
        #: Workers killed at a deadline, for the record (and the tests).
        self.killed: list[int] = []
        #: A start failure that would happen again (:attr:`_StartFailed.lasting`): every
        #: later start raises it at once instead of trying.
        self.failed: BaseException | None = None
        #: Affinity key -> the worker that took the last task with it (module notes).
        self._holders: OrderedDict[Any, Any] = OrderedDict()
        #: ``id(worker)`` -> when it last took a task with a key.
        self._keyed_at: dict[int, float] = {}
        self._counts = dict.fromkeys(AFFINITY_OUTCOMES, 0)
        #: ``id(worker)`` -> the tasks waiting for it as their holder, in the order they came.
        self._waiting: dict[int, list] = {}
        #: How many :data:`RENDER_LANE` tasks may hold a worker at once; ``None``: all.
        self.render_workers: int | None = None
        #: Tasks of the other lane waiting for any worker: a render task yields to them.
        self._wanting = 0
        #: ``id(worker)`` of the workers running a :data:`RENDER_LANE` task.
        self._rendering: set[int] = set()
        #: Render tasks past their deadline whose worker finished them (kept), and those
        #: stopped after all (also in :attr:`killed`).
        self.overruns = {"finished": 0, "stopped": 0}

    def _failure(self, exc: BaseException) -> None:
        """Remember ``exc`` if it is lasting (called with the condition held)."""
        if getattr(exc, "lasting", False) and self.failed is None:
            self.failed = exc

    def _start(self) -> Any:
        raise NotImplementedError

    def pids(self) -> list[int]:
        """The idle workers' process ids."""
        with self._condition:
            return [worker.pid or 0 for worker in self._idle]

    @property
    def starting(self) -> int:
        """Workers being started (and warmed) now."""
        with self._condition:
            return self._starting

    @property
    def affinity_counts(self) -> dict[str, int]:
        """Where the tasks given an ``affinity`` key went, so far (:data:`AFFINITY_OUTCOMES`)."""
        with self._condition:
            return dict(self._counts)

    def holder(self, key: Any) -> int | None:
        """The process id of the worker holding ``key`` (that took its last task), or
        ``None``."""
        with self._condition:
            worker = self._holders.get(key)
            return None if worker is None else (worker.pid or 0)

    def _unhold(self, worker: Any) -> None:
        """``worker`` leaves the pool: its keys go with it (called with the condition held)."""
        for key in [key for key, held in self._holders.items() if held is worker]:
            del self._holders[key]
        self._keyed_at.pop(id(worker), None)
        self._waiting.pop(id(worker), None)
        self._rendering.discard(id(worker))

    def _yields(self) -> bool:
        """Whether a :data:`RENDER_LANE` task waits now rather than take a worker: another
        task waits for one, or :attr:`render_workers` hold one already (condition held)."""
        limit = self.render_workers
        return self._wanting > 0 or (limit is not None and len(self._rendering) >= max(1, limit))

    def _dequeue(self, worker: Any, ticket: object) -> None:
        """A task no longer waits for ``worker`` (condition held): the next may take it."""
        queue = self._waiting.get(id(worker))
        if queue is not None:
            if ticket in queue:
                queue.remove(ticket)
            if not queue:
                del self._waiting[id(worker)]
        self._condition.notify_all()

    def _choose(self, holder: Any, wait_over: bool, ticket: object = None) -> tuple[Any, str]:
        """The idle worker a task takes, and how (:data:`AFFINITY_OUTCOMES`; ``""`` for a
        task without a key), or ``(None, "")`` to wait for the busy ``holder``.  Tasks
        waiting for a holder take it in the order they came (``ticket``: this one's place),
        and no other task takes a worker that tasks are waiting for."""
        if holder is not None:
            queue = self._waiting.get(id(holder))
            first = not queue or queue[0] is ticket
            if holder in self._idle and first:
                return holder, "hit"
            if not wait_over:
                return None, ""
        free = [worker for worker in self._idle
                if worker is not holder and not self._waiting.get(id(worker))]
        if not free:
            return None, ""
        held = {id(worker) for worker in self._holders.values()}
        if not held:
            return free[-1], ""                  # the last one back, as before keys
        # Workers holding no key first, then the one keyed longest ago; the last back of equals.
        best = max(range(len(free)), key=lambda k: (
            id(free[k]) not in held, -self._keyed_at.get(id(free[k]), 0.0), k))
        return free[best], ""

    def _took(self, worker: Any, affinity: Any, how: str, holder: Any) -> None:
        """Remember that ``worker`` took a task with ``affinity`` (condition held)."""
        if affinity is None:
            return
        if not how:
            how = "elsewhere" if holder is not None else "unheld"
        self._counts[how] += 1
        self._holders.pop(affinity, None)
        self._holders[affinity] = worker
        while len(self._holders) > AFFINITY_KEYS:
            self._holders.popitem(last=False)
        self._keyed_at[id(worker)] = time.monotonic()

    def _acquire(self, deadline: float, timeout: float, affinity: Any = None,
                 affinity_wait: float | None = None, lane: Any = None) -> tuple[Any, float]:
        """An idle worker, or a new one, and the seconds of the wait that do not count
        against ``deadline``: starting one, or waiting while workers start.  With
        ``affinity``, the worker holding it, waited for at most ``affinity_wait`` s while
        busy (the module's notes); a new worker takes it only after that, too.  A
        :data:`RENDER_LANE` task waits while another task waits for a worker, or while
        :attr:`render_workers` render already."""
        if lane not in LANES:
            raise ArgumentError(f"lane is one of {LANES}, not {lane!r}")
        rendering = lane == RENDER_LANE
        uncounted = 0.0
        holding_since: float | None = None      # when the wait for a busy holder began
        waited = False
        wanting = False                          # counted in self._wanting
        ticket = object()                        # this task's place among a holder's waiters
        queued: Any = None                       # the holder it is queued for
        with self._condition:
            try:
                while True:
                    if self._closed:
                        raise RuntimeError("the worker pool is closed")
                    if self.failed is not None:
                        raise _again(self.failed)
                    holder = self._holders.get(affinity) if affinity is not None else None
                    if queued is not None and queued is not holder:
                        self._dequeue(queued, ticket)
                        queued = None
                    now = time.monotonic()
                    wait_over = True
                    if holder is not None and (holder not in self._idle or self._waiting.get(id(holder))):
                        if holding_since is None:
                            holding_since = now
                        wait_over = affinity_wait is not None and now - holding_since >= affinity_wait
                        if not wait_over and queued is None:
                            self._waiting.setdefault(id(holder), []).append(ticket)
                            queued = holder
                    yields = rendering and self._yields()
                    worker, how = (None, "") if yields else self._choose(holder, wait_over, ticket)
                    if worker is not None:
                        self._idle.remove(worker)
                        if worker.alive():
                            if how == "hit" and waited:
                                how = "waited"
                            self._took(worker, affinity, how, holder)
                            if rendering:
                                self._rendering.add(id(worker))
                            return worker, uncounted
                        self._count -= 1
                        self._forget(worker)
                        self._unhold(worker)
                        worker.dispose()
                        continue
                    if self._count < self.size and wait_over and not yields:
                        self._count += 1
                        self._starting += 1
                        break
                    if not rendering and wait_over and not wanting:
                        # Any worker would do: render tasks wait until this one has one.
                        wanting = True
                        self._wanting += 1
                    began = time.monotonic()
                    if self._starting:
                        # A worker is starting (warm-up, or a replacement): not the call's time.
                        self._condition.wait(self.start_timeout)
                        uncounted += time.monotonic() - began
                        waited = waited or holder is not None
                        continue
                    remaining = deadline + uncounted - began
                    if remaining <= 0:
                        raise ToolError("timeout", f"no worker was free within {timeout:g} s",
                                        details={"timeout": timeout})
                    if not wait_over and holding_since is not None and affinity_wait is not None:
                        # Wake when the wait for the holder is over, to go elsewhere then.
                        remaining = min(remaining, max(0.0, holding_since + affinity_wait - began) + 0.001)
                    self._condition.wait(remaining)
                    waited = waited or holder is not None
            finally:
                if queued is not None:
                    self._dequeue(queued, ticket)
                if wanting:
                    self._wanting -= 1
                    self._condition.notify_all()
        began = time.monotonic()
        try:
            worker = self._start()
        except BaseException as exc:
            with self._condition:
                self._count -= 1
                self._starting -= 1
                self._failure(exc)
                self._condition.notify_all()
            raise
        with self._condition:
            self._starting -= 1
            holder = self._holders.get(affinity) if affinity is not None else None
            self._took(worker, affinity, "", holder)
            if rendering:
                self._rendering.add(id(worker))
            self._condition.notify_all()
        return worker, uncounted + time.monotonic() - began

    def warm(self, count: int | None = None, *, wait: bool = False) -> None:
        """Start workers up to ``count`` (``None``: :attr:`size`) and warm them, in
        background threads; ``wait`` returns when they are ready (or failed: logged)."""
        want = self.size if count is None else max(0, min(count, self.size))
        with self._condition:
            if self._closed or self.failed is not None:
                return
            new = max(0, want - self._count)
            self._count += new
            self._starting += new
        threads = [threading.Thread(target=self._warm_one, daemon=True,
                                    name="ooxml-edit-tools-warm") for _ in range(new)]
        for thread in threads:
            thread.start()
        if wait:
            for thread in threads:
                thread.join()
            with self._condition:            # and those another call is starting
                self._condition.wait_for(lambda: not self._starting or self._closed,
                                         self.start_timeout)

    def _warm_one(self) -> None:
        try:
            worker = self._start()
        except BaseException as exc:  # noqa: BLE001 -- a call that needs a worker meets it again
            logger.debug("a worker did not start ahead of use: %s", exc)
            with self._condition:
                self._count -= 1
                self._starting -= 1
                self._failure(exc)          # the call that needs a worker gets it
                self._condition.notify_all()
            return
        with self._condition:
            self._starting -= 1
        self._release(worker)

    def _warm_tasks(self, run: Callable[[Callable[[], Any]], Any]) -> None:
        """Run each warm-up task with ``run`` (the worker's own way); a failure is logged."""
        for fn in [_ready, *self.warm_up]:
            try:
                run(fn)
            except _StartFailed:
                raise
            except Exception as exc:  # noqa: BLE001 -- the worker still serves
                logger.warning("a worker's warm-up %s failed: %s: %s",
                               getattr(fn, "__qualname__", fn), type(exc).__name__, exc)

    def _forget(self, worker: Any) -> None:
        """A worker leaves the pool for good (called with the condition held)."""

    def _release(self, worker: Any) -> None:
        with self._condition:
            self._rendering.discard(id(worker))
            if self._closed:
                self._count -= 1
                self._forget(worker)
                self._unhold(worker)
            else:
                self._idle.append(worker)
                self._condition.notify_all()
                return
        worker.stop()

    def _discard(self, worker: Any) -> None:
        with self._condition:
            self._forget(worker)
            self._unhold(worker)
        worker.kill()
        with self._condition:
            self._count -= 1
            self._condition.notify_all()

    def _overrun(self, worker: Any, grace: float, finished: Callable[[Any, float], Any],
                 on_late: "Callable[[Any], Any] | None") -> None:
        """A :data:`RENDER_LANE` task's deadline passed: ``worker`` goes on with it, in the
        background, for at most ``grace`` s -- ``finished(worker, seconds)`` waits for its
        reply, ``None`` when there is none in time -- then is kept (its result handed to
        ``on_late``) or stopped.  It stays busy the while: the pool's size holds."""
        def wait() -> None:
            ends = time.monotonic() + grace
            reply = None
            try:
                while True:
                    left = ends - time.monotonic()
                    if self._closed or left <= 0:
                        break
                    reply = finished(worker, min(left, 0.5))
                    if reply is not None:
                        break
            except Exception:  # noqa: BLE001 -- a worker that broke is stopped below
                reply = None
            if reply is None:
                with self._condition:
                    self.overruns["stopped"] += 1
                self.killed.append(worker.pid or 0)
                self._discard(worker)
                return
            with self._condition:
                self.overruns["finished"] += 1
            self._release(worker)
            ok = isinstance(reply, tuple) and len(reply) >= 2 and reply[0] == "ok"
            if ok and on_late is not None:
                value = reply[1]
                try:
                    on_late(value)
                except Exception as exc:  # noqa: BLE001 -- the result was nobody's by then
                    logger.debug("a late render's result was not kept: %s", exc)

        threading.Thread(target=wait, daemon=True, name="ooxml-edit-tools-overrun").start()

    def _close_idle(self) -> list[Any]:
        with self._condition:
            self._closed = True
            idle, self._idle = self._idle, []
            self._count -= len(idle)
            for worker in idle:
                self._forget(worker)
            self._holders.clear()
            self._keyed_at.clear()
            self._waiting.clear()
            self._condition.notify_all()
        return idle


# -- multiprocessing workers -------------------------------------------------------------------


def _queue(deadline: float, timeout: float, queue_timeout: float | None) -> tuple[float, float]:
    """What :meth:`_Pool._acquire` waits for a busy worker against: the task's deadline, or
    its own ``queue_timeout`` from now when one is given."""
    if queue_timeout is None:
        return deadline, timeout
    return time.monotonic() + queue_timeout, queue_timeout


class _StartFailed(ToolError):
    """A worker did not start, or not in time: raised from its warm-up, never a task's.
    ``lasting``: it exited with a code as it started, so the next would too."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None,
                 lasting: bool = False) -> None:
        super().__init__(code, message, details=details)
        self.lasting = lasting


class _NoChildren(Exception):
    """Starting a worker process failed because this process may not have children."""


class _CannotStart(RuntimeError):
    """A subprocess worker could not be started (or exited before it was ready);
    ``lasting`` as for :class:`_StartFailed`."""

    def __init__(self, message: str, *, lasting: bool = False) -> None:
        super().__init__(message)
        self.lasting = lasting


def _again(exc: BaseException) -> BaseException:
    """A fresh copy of a remembered start failure, to raise once more."""
    if isinstance(exc, _StartFailed):
        return _StartFailed(exc.code, exc.message, details=exc.details, lasting=True)
    if isinstance(exc, _CannotStart):
        return _CannotStart(str(exc), lasting=True)
    return exc


def _exited(code: int | None) -> _StartFailed:
    return _StartFailed("internal", f"the worker process exited as it started: "
                        f"{exit_cause(code)}", details={"exit_code": code},
                        lasting=code is not None and code >= 0)


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
                                       name=WORKER_NAME)
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

    def ask(self, fn: Callable[[], Any], timeout: float) -> Any:
        """``fn()`` in this worker, waited for at most ``timeout`` (its start, its warm-up)
        -- on its pipe and its process at once, so an exit ends the wait (see the module's
        notes)."""
        try:
            self.conn.send((fn, (), {}))
            ready = wait_for([self.conn, self.process.sentinel], max(0.0, timeout))
            if not ready:
                raise _StartFailed("timeout", f"the worker process did not start within "
                                   f"{timeout:g} s", details={"timeout": timeout})
            if self.conn not in ready and not self.conn.poll(0):
                raise EOFError
            status, value = self.conn.recv()
        except (EOFError, ConnectionError, BrokenPipeError, OSError):
            raise _exited(self._exit_code()) from None
        if status == "error":
            raise value
        return value

    def _exit_code(self) -> int | None:
        self.process.join(2)
        return self.process.exitcode

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
            raise _CannotStart(f"could not start {executable!r}: {exc}", lasting=True) from None
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
                               "its error is on stderr",
                               lasting=code is not None and code >= 0) from None
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
            stdin = self.process.stdin
            assert stdin is not None  # started with stdin=PIPE
            stdin.close()
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


def _overran(timeout: float) -> ToolError:
    """A :data:`RENDER_LANE` task's ``timeout``: its worker goes on with it a while."""
    return ToolError("timeout", f"the work did not finish within {timeout:g} s; its worker "
                     f"finishes it in the background (kept for the next call) or is stopped "
                     f"{timeout * OVERRUN:g} s later", details={"timeout": timeout})


def _late(on_late: "Callable[[Any], Any] | None") -> "Callable[[Any], Any] | None":
    """``on_late`` handed a subprocess worker's late result as its value (``_outcome``)."""
    if on_late is None:
        return None
    return lambda value: on_late(_outcome(("ok", value)))


class SubprocessPool(_Pool):
    """At most ``size`` subprocess workers (``python -m ooxml_edit.tools.subworker``), started
    when first needed and kept.

    ``run(fn, *args, timeout=...)`` returns ``fn(*args)`` from a worker, re-raises what it
    raised, and raises :class:`~.results.ToolError` ``timeout`` when the deadline passes --
    waiting for a busy worker counts against it, starting one and warming it
    (``warm_up``, bounded by ``start_timeout``) does not -- after killing the worker; the
    next call starts another.  A worker that dies mid-task is replaced and the call reports
    ``internal``.  Usable where multiprocessing is not: in a daemonic process.

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

    def __init__(self, size: int = 2, *, executable: str | None = None,
                 warm_up: Sequence[Callable[[], Any]] = (),
                 start_timeout: float = START_TIMEOUT) -> None:
        if size < 1:
            raise ArgumentError("a subprocess pool has at least one worker")
        super().__init__(size, warm_up=warm_up, start_timeout=start_timeout)
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

    def run(self, fn: Callable[..., Any], *args: Any, timeout: float,
            queue_timeout: float | None = None, affinity: Any = None,
            affinity_wait: float | None = None, lane: Any = None,
            on_late: "Callable[[Any], Any] | None" = None, overrun: bool = False,
            **kwargs: Any) -> Any:
        """``fn(*args, **kwargs)`` in a worker, as :meth:`WorkerPool.run` (``affinity``,
        ``lane``, ``on_late``: the module's notes)."""
        if self._closed:
            raise RuntimeError("the worker pool is closed")
        reference = task_reference(fn)
        if reference is None:
            return self._run_inline(fn, args, kwargs, timeout, queue_timeout)
        payload = pickle.dumps((args, kwargs), protocol=_PROTOCOL)
        deadline = time.monotonic() + timeout
        worker, uncounted = self._acquire(*_queue(deadline, timeout, queue_timeout), affinity,
                                          affinity_wait, lane)
        deadline = (deadline + uncounted if queue_timeout is None
                    else time.monotonic() + timeout)
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
            if lane == RENDER_LANE or overrun:
                self._overrun(worker, timeout * OVERRUN, lambda w, wait: w.wait(wait),
                              _late(on_late))
                raise _overran(timeout)
            self.killed.append(worker.pid or 0)
            self._discard(worker)
            raise ToolError("timeout", f"the work did not finish within {timeout:g} s; "
                            "it was stopped", details={"timeout": timeout})
        self._release(worker)
        return _outcome(reply)

    def _run_inline(self, fn: Callable[..., Any], args: tuple, kwargs: dict,
                    timeout: float, queue_timeout: float | None = None) -> Any:
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
        return self._inline.run(fn, *args, timeout=timeout, queue_timeout=queue_timeout,
                                **kwargs)

    def _start(self) -> _Subprocess:
        timeout = self.start_timeout
        worker = _Subprocess(self.executable, time.monotonic() + timeout, timeout)
        with self._condition:
            self._all.add(worker)

        def ask(fn: Callable[[], Any]) -> Any:
            reference = task_reference(fn)
            if reference is None:
                raise RuntimeError("not a registered worker task (ooxml_edit.tools.worker_task)")
            try:
                worker.send(("task", reference, pickle.dumps(((), {}), protocol=_PROTOCOL)))
                reply = worker.wait(timeout)
            except _Gone:
                raise _exited(worker._exit_code()) from None
            if reply is None:
                raise _StartFailed("timeout", f"the worker process did not start within "
                                   f"{timeout:g} s", details={"timeout": timeout})
            return _outcome(reply)

        try:
            self._warm_tasks(ask)
        except BaseException:
            with self._condition:
                self._all.discard(worker)
            worker.kill()
            raise
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


class _Threads:
    """Tasks in threads of this process, at most ``size`` at a time, each waited for at most
    its deadline; a task past it is abandoned, not stopped: it runs on, in its slot, until
    it ends.  What :class:`InProcess` and :class:`ThreadRunner` share."""

    def __init__(self, size: int = 1) -> None:
        self.size = max(1, size)
        self._slots = threading.BoundedSemaphore(self.size)
        self._lock = threading.Lock()
        self._closed = False
        #: Tasks given up at a deadline: all so far, and those still running.
        self.abandoned = 0
        self.running_abandoned = 0

    #: What a thread serving a task is called.
    _thread_name = "ooxml-edit-tools-inprocess"

    def _timed_out(self, timeout: float) -> ToolError:
        return ToolError("timeout", f"the work did not finish within {timeout:g} s; "
                         "it was abandoned (in-process work cannot be stopped)",
                         details={"timeout": timeout, "in_process": True})

    def _busy(self, wait: float) -> ToolError:
        return ToolError("timeout", f"no worker was free within {wait:g} s",
                         details={"timeout": wait, "in_process": True})

    def _run(self, fn: Callable[..., Any], args: tuple, kwargs: dict, timeout: float,
             queue_timeout: float | None) -> Any:
        if self._closed:
            raise RuntimeError("the worker pool is closed")
        wait = timeout if queue_timeout is None else queue_timeout
        deadline = time.monotonic() + timeout
        if not self._slots.acquire(timeout=max(0.0, wait)):
            raise self._busy(wait)
        if queue_timeout is not None:
            deadline = time.monotonic() + timeout     # from when a slot took it
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

        thread = threading.Thread(target=work, name=self._thread_name, daemon=True)
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
                raise self._timed_out(timeout)
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("value")

    def pids(self) -> list[int]:
        return []

    def close(self) -> None:
        self._closed = True


class InProcess(_Threads):
    """Tasks run in threads of this process, at most ``size`` at a time, each waited for at
    most its deadline.  A task past its deadline is abandoned, not stopped: it runs on, in its
    slot, until it ends (see the module's notes).  The same ``run`` and ``close`` as
    :class:`WorkerPool`, so an application may pass one as a toolbox's ``runner``."""

    def run(self, fn: Callable[..., Any], *args: Any, timeout: float,
            queue_timeout: float | None = None, affinity: Any = None,
            affinity_wait: float | None = None, lane: Any = None,
            on_late: "Callable[[Any], Any] | None" = None, overrun: bool = False,
            **kwargs: Any) -> Any:
        """``fn(*args, **kwargs)`` in a thread of this process; ``affinity`` and ``lane`` are
        taken and not needed: every task runs in this one process, and a task past its
        deadline runs on there anyway (``on_late`` is not called)."""
        return self._run(fn, args, kwargs, timeout, queue_timeout)

    def warm(self, count: int | None = None, *, wait: bool = False) -> None:
        """Nothing to start: the work runs in this process."""

    def __enter__(self) -> "InProcess":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# -- threads, with the GIL off -----------------------------------------------------------------


def gil_enabled() -> bool:
    """Whether this interpreter runs with the GIL: always on a regular build; on a
    free-threaded one (``python3.15t``) unless it was turned back on -- ``PYTHON_GIL=1``,
    or an extension module imported that needs it (lxml before 7 does), which Python
    announces with a ``RuntimeWarning``."""
    enabled = getattr(sys, "_is_gil_enabled", None)
    return True if enabled is None else bool(enabled())


#: Warm-up tasks already run in this process, each with the event set once it ended (a
#: failure included: it is logged, and not tried again).
_WARMED: dict[Any, threading.Event] = {}
_WARMED_LOCK = threading.Lock()


def warm_once(tasks: Sequence[Callable[[], Any]], timeout: float = START_TIMEOUT) -> None:
    """Run each of ``tasks`` once in this process, whichever thread asks first; another
    thread asking meanwhile waits for that run, at most ``timeout`` s each, then goes on
    without it (logged).  A failure is logged, not raised, and not tried again: the work
    still runs, as a worker process's does after a failed warm-up."""
    for fn in tasks:
        with _WARMED_LOCK:
            event = _WARMED.get(fn)
            mine = event is None
            if mine:
                event = _WARMED[fn] = threading.Event()
        assert event is not None
        if not mine:
            if not event.wait(timeout):
                logger.warning("the warm-up %s did not end within %g s; the work goes on "
                               "without waiting for it", getattr(fn, "__qualname__", fn),
                               timeout)
            continue
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- the work still runs
            logger.warning("a warm-up %s failed: %s: %s",
                           getattr(fn, "__qualname__", fn), type(exc).__name__, exc)
        finally:
            event.set()


#: Types a :class:`ThreadRunner` hands a task as they are: nothing in them can change.
_IMMUTABLE = (bytes, str, int, float, complex, bool, type(None), range)


def _own_copy(value: Any) -> Any:
    """``value`` for one task alone: as it is when nothing in it can change, else a copy
    made as a worker process gets its arguments (a pickle round trip)."""
    if isinstance(value, _IMMUTABLE):
        return value
    if type(value) is tuple and all(isinstance(item, _IMMUTABLE) for item in value):
        return value
    return pickle.loads(pickle.dumps(value, protocol=_PROTOCOL))


class ThreadRunner(_Threads):
    """Tasks in threads of this process, side by side, for a free-threaded Python with the
    GIL off (``python3.15t``, lxml 7): opt in with ``Toolbox(runner="threads")``.

    The same ``run``, ``warm`` and ``close`` as :class:`WorkerPool`: at most ``size`` tasks
    run at once, each waited for at most its deadline; ``queue_timeout`` bounds the wait for
    a slot as there.  What changes is what a deadline does.  A thread cannot be stopped,
    so a task past its deadline is reported as ``timeout`` and its result is thrown away
    when it ends, but it keeps its slot until then: a runaway task makes the runner
    smaller, never busier.  At most ``max_abandoned`` (default ``size``) such tasks may be
    running: past that, ``run`` refuses new work at once (:class:`ToolError` ``limit``,
    saying why) rather than queueing it behind work nothing can stop.  Where deadlines
    must stop the work, use the default :class:`WorkerPool`.

    Warm-up runs once per process, not per worker (:func:`warm_once`): the formats'
    imports and font scans are shared by every task, and there is no worker to start, no
    module to import again and nothing to pickle across.  Each task still gets its own
    arguments: those that could change (a list, a dict, an object) are copied for it as a
    worker process would get them (pickled), so no two tasks share one; bytes, strings and
    numbers are handed over as they are.  Results are not copied.

    With the GIL on, threads take turns: the runner works, but no faster than one thread,
    and a task that holds the GIL (resvg's rasterise) stops every other.  It warns once
    when it finds the GIL on; ``Toolbox(runner="threads")`` does not use it then."""

    _thread_name = "ooxml-edit-tools-thread"

    def __init__(self, size: int = 2, *, warm_up: Sequence[Callable[[], Any]] = (),
                 start_timeout: float = START_TIMEOUT, max_abandoned: int | None = None) -> None:
        if size < 1:
            raise ArgumentError("a thread runner has at least one thread")
        if max_abandoned is not None and max_abandoned < 0:
            raise ArgumentError("max_abandoned is 0 or more (None: the runner's size)")
        super().__init__(size)
        #: What runs once in this process before the first task (:func:`warm_once`).
        self.warm_up: list[Callable[[], Any]] = list(warm_up)
        self.start_timeout = start_timeout
        #: Tasks past their deadline that may still be running before new work is refused.
        self.max_abandoned = self.size if max_abandoned is None else max_abandoned
        self._warned_gil = False

    @property
    def in_process(self) -> bool:
        """``False``: tasks run side by side, each bounded by its deadline, as in worker
        processes -- not one at a time in the calling thread (:class:`InProcess`)."""
        return False

    @property
    def runner(self) -> str:
        return "threads"

    def _timed_out(self, timeout: float) -> ToolError:
        return ToolError("timeout", f"the work did not finish within {timeout:g} s; it was "
                         "abandoned: a thread cannot be stopped, so it runs on and keeps its "
                         "slot until it ends, and its result is thrown away",
                         details={"timeout": timeout, "threads": True})

    def _busy(self, wait: float) -> ToolError:
        return ToolError("timeout", f"no thread was free within {wait:g} s",
                         details={"timeout": wait, "threads": True})

    def _check_gil(self) -> None:
        if self._warned_gil or not gil_enabled():
            return
        self._warned_gil = True
        logger.warning("ooxml-edit's thread runner finds the GIL on: its tasks take turns "
                       "(a free-threaded Python, python3.15t with PYTHON_GIL=0 and lxml 7, "
                       "runs them side by side; worker processes do on any Python)")

    def run(self, fn: Callable[..., Any], *args: Any, timeout: float,
            queue_timeout: float | None = None, affinity: Any = None,
            affinity_wait: float | None = None, lane: Any = None,
            on_late: "Callable[[Any], Any] | None" = None, overrun: bool = False,
            **kwargs: Any) -> Any:
        """``fn(*args, **kwargs)`` in a thread, waited for at most ``timeout`` s (see the
        class's notes); warming up first is not the call's time.  ``affinity`` and ``lane``
        are taken and not needed: every task runs in this one process (``on_late`` is not
        called)."""
        if self._closed:
            raise RuntimeError("the worker pool is closed")
        self._check_gil()
        warm_once(self.warm_up, self.start_timeout)
        with self._lock:
            stuck = self.running_abandoned
        if stuck and stuck >= self.max_abandoned:
            raise ToolError(
                "limit", f"{stuck} task(s) past their deadline are still running in this "
                "process's threads, which cannot stop them, so new work is refused until "
                "they end; try again later. The application may allow more "
                "(ThreadRunner(max_abandoned=...)) or run the work in worker processes, the "
                "default runner, whose deadlines stop it",
                details={"abandoned_running": stuck, "max_abandoned": self.max_abandoned,
                         "threads": True})
        args = tuple(_own_copy(arg) for arg in args)
        kwargs = {name: _own_copy(value) for name, value in kwargs.items()}
        return self._run(fn, args, kwargs, timeout, queue_timeout)

    def warm(self, count: int | None = None, *, wait: bool = False) -> None:
        """Run the warm-up once in this process (:func:`warm_once`), in a background thread;
        ``wait`` returns when it has (a failure is logged).  ``count`` is not used: there
        is no worker to start."""
        if self._closed:
            return
        thread = threading.Thread(target=warm_once, args=(self.warm_up, self.start_timeout),
                                  daemon=True, name="ooxml-edit-tools-warm")
        thread.start()
        if wait:
            thread.join()

    def __enter__(self) -> "ThreadRunner":
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
    waiting for a busy worker counts against the deadline too; starting a worker and
    running its ``warm_up`` does not (``start_timeout`` bounds it) -- after killing the
    worker.  A worker that dies mid-task is replaced and the call reports ``internal``.

    ``size=0`` runs every task in-process (:class:`InProcess`, one at a time).

    ``fallback`` says what a pool does where multiprocessing may not start workers -- a
    daemonic process (``multiprocessing.current_process().daemon``), or one whose first
    start fails that way -- or its workers exit as they start, with a code (a main module
    with no ``__main__`` guard; see the module's notes):

    * ``True`` (the default) or ``"subprocess"``: a :class:`SubprocessPool` of the same size
      runs the work (:attr:`runner` is ``"subprocesses"``) -- deadlines still kill.  Where a
      subprocess cannot be started either, the work runs in-process.
    * ``"in-process"``: an :class:`InProcess` of the same size runs it (:attr:`in_process`).
    * ``False``: no fallback; starting a worker fails as multiprocessing makes it fail.

    Each fallback logs a warning once.
    """

    def __init__(self, size: int = 2, *, start_method: str = "spawn",
                 fallback: bool | str = True, warm_up: Sequence[Callable[[], Any]] = (),
                 start_timeout: float = START_TIMEOUT) -> None:
        if size < 0:
            raise ArgumentError("a pool's size is 0 (in-process) or more")
        if not any(fallback is value or fallback == value and type(fallback) is type(value)
                   for value in FALLBACKS):
            raise ArgumentError(f"fallback is one of {FALLBACKS}, not {fallback!r}")
        super().__init__(size, warm_up=warm_up, start_timeout=start_timeout)
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
    def render_workers(self) -> int | None:
        """How many :data:`RENDER_LANE` tasks may hold a worker at once (``None``: all),
        here and in the :class:`SubprocessPool` it may fall back to."""
        return self._render_workers

    @render_workers.setter
    def render_workers(self, value: int | None) -> None:
        self._render_workers = value
        others = getattr(self, "_subprocesses", None)
        if others is not None:
            others.render_workers = value

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
                self._subprocesses = SubprocessPool(max(1, self.size), warm_up=self.warm_up,
                                                    start_timeout=self.start_timeout)
                self._subprocesses.render_workers = self.render_workers
                # One record of the workers killed at a deadline, whichever ran them.
                self._subprocesses.killed = self.killed
                logger.warning("ooxml-edit tools run their work in subprocess workers: %s",
                               why)
            return self._subprocesses

    def _run_elsewhere(self, runner: Any, fn: Callable[..., Any], args: tuple, kwargs: dict,
                       deadline: float, queue_timeout: float | None = None,
                       affinity: Any = None, affinity_wait: float | None = None,
                       lane: Any = None, on_late: "Callable[[Any], Any] | None" = None,
                       overrun: bool = False) -> Any:
        if queue_timeout is not None:
            kwargs = {**kwargs, "queue_timeout": queue_timeout}
        if affinity is not None:
            kwargs = {**kwargs, "affinity": affinity, "affinity_wait": affinity_wait}
        if lane is not None or on_late is not None or overrun:
            kwargs = {**kwargs, "lane": lane, "on_late": on_late, "overrun": overrun}
        try:
            return runner.run(fn, *args, timeout=max(0.0, deadline - time.monotonic()),
                              **kwargs)
        except (_CannotStart, _StartFailed) as exc:
            if isinstance(exc, _StartFailed) and not exc.lasting:
                raise
            why = f"no subprocess worker either: {exc}"
        with self._condition:
            if self._subprocesses is runner:
                self._subprocesses = None
        runner.close()
        inline = self._go_in_process(why)
        return inline.run(fn, *args, timeout=max(0.0, deadline - time.monotonic()), **kwargs)

    def run(self, fn: Callable[..., Any], *args: Any, timeout: float,
            queue_timeout: float | None = None, affinity: Any = None,
            affinity_wait: float | None = None, lane: Any = None,
            on_late: "Callable[[Any], Any] | None" = None, overrun: bool = False,
            **kwargs: Any) -> Any:
        """``fn(*args, **kwargs)`` in a worker, waited for at most ``timeout`` s.  Waiting for
        a busy worker counts against it, unless ``queue_timeout`` is given: then that bounds
        the wait for a worker, and ``timeout`` counts from when one takes the task.
        ``affinity`` (hashable) sends the task to the worker that took the last task with
        the same key, waiting for it at most ``affinity_wait`` s while it is busy (``None``:
        as long as the deadline allows; the module's notes); ``None``, any worker.
        ``lane``: :data:`RENDER_LANE` for a task that draws images -- it yields to other
        work, and past its deadline its worker goes on with it a while, its result then
        handed to ``on_late`` (the module's notes).  ``overrun``: that last for a task of
        the other lane too (a render's layout: its worker holds layouts worth keeping)."""
        if self._closed:
            raise RuntimeError("the worker pool is closed")
        refuse_while_bootstrapping()
        deadline = time.monotonic() + timeout
        if self._inline is not None:
            return self._inline.run(fn, *args, timeout=timeout, queue_timeout=queue_timeout,
                                    **kwargs)
        if self._subprocesses is not None:
            return self._run_elsewhere(self._subprocesses, fn, args, kwargs, deadline,
                                       queue_timeout, affinity, affinity_wait, lane, on_late,
                                       overrun)
        why = None
        try:
            worker, uncounted = self._acquire(*_queue(deadline, timeout, queue_timeout),
                                              affinity, affinity_wait, lane)
            deadline = (deadline + uncounted if queue_timeout is None
                        else time.monotonic() + timeout)
        except _NoChildren as exc:
            if not self._fallback:
                raise AssertionError(str(exc)) from None
            why = str(exc)
        except _StartFailed as exc:
            if not (exc.lasting and self._fallback):
                raise
            why = exc.message
        if why is not None:         # outside the handler: the work's own error unchained
            return self._run_elsewhere(self._go_elsewhere(why), fn, args, kwargs, deadline,
                                       queue_timeout, affinity, affinity_wait, lane, on_late,
                                       overrun)
        try:
            worker.conn.send((fn, args, kwargs))
            if not worker.conn.poll(max(0.0, deadline - time.monotonic())):
                if lane == RENDER_LANE or overrun:
                    late, worker = worker, None
                    self._overrun(late, timeout * OVERRUN,
                                  lambda w, wait: w.conn.recv() if w.conn.poll(wait) else None,
                                  on_late)
                    raise _overran(timeout)
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

    @property
    def affinity_counts(self) -> dict[str, int]:
        if self._subprocesses is not None:
            return self._subprocesses.affinity_counts
        return super().affinity_counts

    def holder(self, key: Any) -> int | None:
        if self._subprocesses is not None:
            return self._subprocesses.holder(key)
        return super().holder(key)

    def warm(self, count: int | None = None, *, wait: bool = False) -> None:
        """Start and warm workers ahead of use (:meth:`_Pool.warm`); in-process, or in a
        worker still starting (:func:`bootstrapping_worker`), nothing."""
        if self._inline is not None or bootstrapping_worker():
            return
        if self._subprocesses is not None:
            self._subprocesses.warm(count, wait=wait)
            return
        super().warm(count, wait=wait)

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

    def _start(self) -> _Worker:
        worker = _Worker(self._context)
        try:
            self._warm_tasks(lambda fn: worker.ask(fn, self.start_timeout))
        except BaseException:
            worker.kill()
            raise
        return worker
