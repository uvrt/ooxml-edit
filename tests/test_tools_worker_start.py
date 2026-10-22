"""A worker that exits as it starts fails the call at once, naming the cause (round 13, R6).

The case that cost minutes: a script with no ``if __name__ == "__main__":`` guard.  Under
``spawn`` each worker imports the main module again before its loop runs, so the script's
work ran once more in every worker -- a render there failing, the work after it going on --
and the parent waited ``start_timeout`` (120 s; 240 s for the first call, which waited for
the warming workers first and then started its own).  Now the worker stops at its first
render (or dispatch) with :data:`~ooxml_edit.tools.worker.UNGUARDED_EXIT`, the parent sees
the exit at once, and the pool falls back to subprocess workers -- or, with
``fallback=False``, every call fails at once with the same plain error.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from ooxml_edit.tools import SubprocessPool, ToolError, WorkerPool
from ooxml_edit.tools import worker as worker_module

import tools_toys

HERE = Path(__file__).resolve().parent

# A script with no __main__ guard: every line runs again in each worker spawn starts.  The
# sleep stands for the work a script goes on to after its renders; a worker that got past
# its first call would sit in it, and the parent would wait out start_timeout (60 s here).
GUARDLESS = """
import json, os, sys, time
sys.path[:0] = [{tests!r}]
from ooxml_edit.tools import ToolError, WorkerPool
import tools_toys
{prelude}
pool = WorkerPool(1, fallback={fallback!r}, start_timeout=60)
if {warm!r}:
    pool.warm()
outcomes = []
for _ in range(3):
    began = time.monotonic()
    try:
        outcome = ["ok", pool.run(tools_toys.worker_pid, timeout=30) != os.getpid()]
    except ToolError as exc:
        outcome = [exc.code, exc.message]
    outcomes.append([round(time.monotonic() - began, 2)] + outcome)
if __name__ == "__main__":
    print(json.dumps({{"outcomes": outcomes, "runner": pool.runner,
                      "remembered": pool.failed is not None}}))
else:
    time.sleep(600)
pool.close()
"""


def _guardless(tmp_path: Path, *, fallback=False, warm=False, prelude="") -> tuple[dict, str]:
    script = tmp_path / "guardless.py"
    script.write_text(GUARDLESS.format(tests=str(HERE), fallback=fallback, warm=warm,
                                       prelude=prelude), encoding="utf-8")
    done = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                          cwd=str(tmp_path), timeout=300)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1]), done.stderr


@pytest.mark.parametrize("warm", [False, True], ids=["cold", "warmed-first"])
def test_a_guardless_script_fails_at_once_and_says_why(tmp_path, warm):
    report, stderr = _guardless(tmp_path, warm=warm)
    (first, *later) = report["outcomes"]
    assert first[0] < 5, report                   # was start_timeout: 120 s, 240 s warmed
    assert first[1] == "internal"
    assert "exit code 86" in first[2] and 'if __name__ == "__main__":' in first[2]
    assert "workers=0" in first[2] and "SubprocessPool" in first[2]
    assert report["remembered"]
    for seconds, code, message in later:          # not tried again: as fast, the same words
        assert seconds < 0.5 and (code, message) == (first[1], first[2])
    assert "this worker process stops as it starts" in stderr     # the worker said why


def test_a_guardless_script_falls_back_to_subprocess_workers(tmp_path):
    report, stderr = _guardless(tmp_path, fallback=True)
    assert report["runner"] == "subprocesses"
    assert [outcome[1:] for outcome in report["outcomes"]] == [["ok", True]] * 3
    assert report["outcomes"][0][0] < 5
    assert "run their work in subprocess workers" in stderr and "exit code 86" in stderr


def test_a_main_module_that_exits_as_a_worker_imports_it(tmp_path):
    prelude = 'if __name__ == "__mp_main__":\n    sys.exit(3)'
    report, _ = _guardless(tmp_path, prelude=prelude)
    (first, *later) = report["outcomes"]
    assert first[0] < 5 and first[1] == "internal" and "exit code 3" in first[2], report
    assert report["remembered"] and all(seconds < 0.5 for seconds, *_ in later)


def test_a_main_module_that_fails_to_import_in_a_worker(tmp_path):
    prelude = 'if __name__ == "__mp_main__":\n    import a_module_no_worker_has'
    report, stderr = _guardless(tmp_path, prelude=prelude)
    first = report["outcomes"][0]
    assert first[0] < 5 and "exit code 1" in first[2], report
    assert "a_module_no_worker_has" in stderr     # the worker's own error, on stderr


def _kill_the_starting_worker(stop: threading.Event) -> None:
    while not stop.is_set():
        for child in multiprocessing.active_children():
            if child.name == worker_module.WORKER_NAME:
                child.kill()
                return
        time.sleep(0.01)


def test_a_worker_killed_as_it_starts_fails_at_once_and_the_next_call_starts_another():
    with WorkerPool(1, warm_up=[tools_toys.slow_warm_up], fallback=False,
                    start_timeout=60) as pool:
        stop = threading.Event()
        killer = threading.Thread(target=_kill_the_starting_worker, args=(stop,))
        killer.start()
        began = time.monotonic()
        try:
            with pytest.raises(ToolError, match="stopped by signal") as caught:
                pool.run(tools_toys.worker_pid, timeout=30)
        finally:
            stop.set()
            killer.join()
        assert time.monotonic() - began < 10 and caught.value.code == "internal"
        assert pool.failed is None                 # a kill is not the next start's fate
        assert pool.run(tools_toys.worker_pid, timeout=30) != os.getpid()


@pytest.mark.parametrize("make", [WorkerPool, SubprocessPool], ids=["processes", "subprocesses"])
def test_a_slow_start_that_is_alive_keeps_its_whole_start_timeout(make):
    # The warm-up takes 2 s, the task's deadline is 1 s: waiting on the process as well as
    # the pipe ends the wait for an exit only, never for a worker that is still starting.
    with make(1, warm_up=[tools_toys.slow_warm_up], start_timeout=30) as pool:
        began = time.monotonic()
        assert pool.run(tools_toys.warmed, timeout=1.0) == "1"
        assert time.monotonic() - began >= 1.9 and pool.failed is None


def test_a_subprocess_worker_that_exits_before_it_is_ready(monkeypatch):
    monkeypatch.setattr(worker_module, "ENTRY_POINT", "ooxml_edit.tools.no_such_entry_point")
    started = []
    popen = subprocess.Popen

    def counted(*args, **kwargs):
        started.append(args)
        return popen(*args, **kwargs)

    monkeypatch.setattr(worker_module.subprocess, "Popen", counted)
    with SubprocessPool(1, start_timeout=60) as pool:
        began = time.monotonic()
        with pytest.raises(worker_module._CannotStart, match=r"exit code 1\b"):
            pool.run(tools_toys.worker_pid, timeout=30)
        assert time.monotonic() - began < 10
        with pytest.raises(worker_module._CannotStart, match=r"exit code 1\b"):
            pool.run(tools_toys.worker_pid, timeout=30)
        assert len(started) == 1                   # remembered: not started again
        pool.warm(wait=True)
        assert len(started) == 1


def test_exit_causes_say_what_happened():
    cause = worker_module.exit_cause
    assert "__main__" in cause(worker_module.UNGUARDED_EXIT)
    assert "signal 9" in cause(-9)
    assert "exit code 2" in cause(2) and "stderr" in cause(2)
    assert "stderr" in cause(None)


def test_this_process_is_no_starting_worker():
    assert not worker_module.bootstrapping_worker()
    worker_module.refuse_while_bootstrapping()     # nothing happens here
