"""Subprocess workers: what a daemonic process (a Celery prefork child) renders with.

Such a process may not start multiprocessing children, but may start subprocesses.  Work
that holds the GIL -- resvg-py's rasterise does -- then runs in parallel and does not stop
the threads serving edits, and a deadline kills the worker.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import pickle
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from ooxml_edit.tools import SubprocessPool, ToolError, WorkerPool, worker_task
from ooxml_edit.tools import worker as worker_module

import synthetic
import tools_toys
from test_tools_worker import _alive

HERE = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def pool():
    with SubprocessPool(1) as pool:
        yield pool


def _gone(pid: int, within: float = 15.0) -> bool:
    deadline = time.monotonic() + within
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _alive(pid)


# -- running work --------------------------------------------------------------------------------


def test_a_registered_task_runs_in_a_kept_subprocess(pool):
    pid = pool.run(tools_toys.worker_pid, timeout=60)
    assert pid != os.getpid()
    assert pool.run(tools_toys.worker_pid, timeout=60) == pid
    image, width, height = pool.run(tools_toys.render_page, synthetic.outer_package(), 1, 32,
                                    timeout=60)
    assert image.startswith(b"\x89PNG") and (width, height) == (32, 18)


def test_arguments_cross_explicitly(pool, tmp_path):
    pid, seen = pool.run(tools_toys.fonts_seen, (str(tmp_path), "b"), timeout=60)
    assert pid != os.getpid() and seen == (str(tmp_path), "b")
    assert pool.run(tools_toys.fonts_seen, font_dirs=None, timeout=60)[1] is None


def test_exceptions_come_back_to_the_caller(pool):
    with pytest.raises(IndexError, match="no page 9"):
        pool.run(tools_toys.render_page, synthetic.outer_package(), 9, 32, timeout=60)
    with pytest.raises(ToolError) as caught:
        pool.run(tools_toys.raise_tool_error, timeout=60)
    assert caught.value.code == "refused" and caught.value.valid_options == ["a", "b"]
    # One that does not unpickle comes back as its text.
    with pytest.raises(RuntimeError, match="Unpicklable: one and two"):
        pool.run(tools_toys.raise_unpicklable, timeout=60)


def test_what_a_task_prints_does_not_reach_the_protocol(capfd):
    with SubprocessPool(1) as pool:      # started under capfd, so its stderr is captured
        assert pool.run(tools_toys.noisy, "x", timeout=60) == "x|''"
        assert pool.run(tools_toys.noisy, "y", timeout=60) == "y|''"
    err = capfd.readouterr().err
    assert all(f"noise from {how}" in err for how in ("print y", "fd 1", "__stdout__")), err


def test_a_task_past_its_deadline_is_killed_and_the_next_call_works(pool):
    victim = pool.run(tools_toys.worker_pid, timeout=60)
    started = time.monotonic()
    with pytest.raises(ToolError) as caught:
        pool.run(tools_toys.render_page, synthetic.outer_package(), 1, 32, 30, timeout=1.5)
    assert caught.value.code == "timeout" and "stopped" in caught.value.message
    assert time.monotonic() - started < 10
    assert pool.killed[-1] == victim and _gone(victim)
    after = pool.run(tools_toys.worker_pid, timeout=60)
    assert after not in (victim, os.getpid())


def test_a_worker_that_dies_mid_task_is_replaced(pool):
    victim = pool.run(tools_toys.worker_pid, timeout=60)
    with pytest.raises(ToolError) as caught:
        pool.run(tools_toys.exit_hard, timeout=60)
    assert caught.value.code == "internal"
    assert _gone(victim)
    assert pool.run(tools_toys.worker_pid, timeout=60) != victim


def test_an_unregistered_function_runs_in_process_with_a_warning(caplog):
    with SubprocessPool(1) as pool, caplog.at_level(logging.WARNING, "ooxml_edit.tools.worker"):
        assert pool.run(tools_toys.not_registered, timeout=60) == os.getpid()
        assert pool.run(tools_toys.not_registered, timeout=60) == os.getpid()
        assert pool.run(lambda: 7, timeout=60) == 7
    warned = [record.getMessage() for record in caplog.records]
    assert sum("not_registered is not a registered worker task" in m for m in warned) == 1


def test_the_size_bounds_the_workers():
    import threading
    with SubprocessPool(2) as pool:
        pids: list[int] = []
        threads = [threading.Thread(target=lambda: pids.append(
            pool.run(tools_toys.render_page, synthetic.outer_package(), 1, 16, 0.5, timeout=60)
            and pool.run(tools_toys.worker_pid, timeout=60))) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
        assert len(pids) == 5 and len(set(pids)) <= 2
        assert pool._count <= 2


# -- what a worker runs: a registered task, nothing else --------------------------------------


class _Touch:
    """Unpickling this creates a file: what a payload must not get to do."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __reduce__(self):
        return (Path.touch, (self.path,))


def test_the_worker_refuses_an_unregistered_reference_before_reading_its_arguments(tmp_path):
    marker = tmp_path / "touched"
    with SubprocessPool(1) as pool:
        deadline = time.monotonic() + 60
        worker = pool._acquire(deadline, 60)
        try:
            for reference in ("os:system", "tools_toys:not_registered", "builtins:eval",
                              "__main__:anything", "tools_toys:<locals>.f"):
                worker.send(("task", reference,
                             pickle.dumps(((_Touch(marker),), {}), protocol=4)))
                kind, blob, text = worker.wait(60)
                assert kind == "error" and "PermissionError" in text, (reference, text)
        finally:
            pool._release(worker)
        assert not marker.exists()
        assert pool.run(tools_toys.worker_pid, timeout=60) != os.getpid()


def test_a_protocol_envelope_names_no_globals():
    with pytest.raises(pickle.UnpicklingError, match="may not name"):
        worker_module.loads_plain(pickle.dumps(("task", os.system)))
    assert worker_module.loads_plain(pickle.dumps(("ok", b"x", [1], {"a": None}))) == (
        "ok", b"x", [1], {"a": None})


def test_only_a_marked_module_level_function_is_a_task():
    def local() -> None:
        pass

    assert worker_module.task_reference(tools_toys.worker_pid) == "tools_toys:worker_pid"
    assert worker_module.task_reference(tools_toys.not_registered) is None
    assert worker_module.task_reference(worker_task(local)) is None       # nested
    assert worker_module.task_reference(len) is None
    with pytest.raises(PermissionError):
        worker_module.resolve_task("tools_toys:not_registered")
    assert worker_module.resolve_task("tools_toys:worker_pid") is tools_toys.worker_pid


# -- stopping: close, interpreter exit, the parent killed ---------------------------------------


def test_close_stops_the_workers():
    pool = SubprocessPool(2)
    pid = pool.run(tools_toys.worker_pid, timeout=60)
    assert _alive(pid)
    pool.close()
    assert _gone(pid, 5)
    with pytest.raises(RuntimeError, match="closed"):
        pool.run(tools_toys.worker_pid, timeout=60)


def _script(body: str) -> subprocess.Popen:
    code = textwrap.dedent(f"""
        import os, sys, threading, time
        sys.path[:0] = [{str(HERE)!r}]
        import synthetic, tools_toys
        from ooxml_edit.tools import SubprocessPool
        pool = SubprocessPool(2)
    """) + textwrap.dedent(body)
    return subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)


def test_no_worker_outlives_an_interpreter_that_did_not_close_its_pool():
    parent = _script("""
        print(pool.run(tools_toys.worker_pid, timeout=60), flush=True)
    """)
    pid = int(parent.stdout.readline())
    assert parent.wait(60) == 0
    assert _gone(pid, 10)


@pytest.mark.parametrize("busy", [False, True], ids=["idle", "busy"])
def test_no_worker_outlives_a_parent_killed_outright(busy):
    parent = _script("""
        print(pool.run(tools_toys.worker_pid, timeout=60), flush=True)
        if %r:
            threading.Thread(target=pool.run, args=(tools_toys.render_page,
                             synthetic.outer_package(), 1, 16, 120), kwargs={"timeout": 300},
                             daemon=True).start()
            time.sleep(1)
        print("ready", flush=True)
        time.sleep(300)
    """ % busy)
    pid = int(parent.stdout.readline())
    assert parent.stdout.readline().strip() == "ready"
    assert _alive(pid)
    parent.kill()
    parent.wait(30)
    assert _gone(pid, 15)


# -- a daemonic parent ---------------------------------------------------------------------------


def _count_for(seconds: float) -> int:
    """Additions ``sum(range(n))`` makes in about ``seconds`` here."""
    count = 1_000_000
    while True:
        started = time.perf_counter()
        sum(range(count))
        took = time.perf_counter() - started
        if took > 0.1:
            return int(count * seconds / took)
        count *= 4


def _daemonic(target, *args):
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=target, args=(queue, *args), daemon=True)
    process.start()
    try:
        return queue.get(timeout=240)
    finally:
        process.join(30)


def _measured(workers: int, holds) -> dict:
    """The daemonic measurement, taken again (up to three times) while ``holds`` does not
    hold of it: a busy test machine can slow one run's timings, never fake the effect."""
    for _ in range(3):
        got = _daemonic(tools_toys.concurrency_in_a_daemonic_process, workers, _count_for(1.0))
        assert "error" not in got, got
        if holds(got):
            break
    return got


def test_in_a_daemonic_process_renders_run_in_parallel_and_edits_do_not_wait():
    got = _measured(2, lambda got: got["two"] < 1.5 * got["one"]
                    and got["edit"] < 0.4 * got["one"])
    assert got["runner"] == "subprocesses" and got["ok"], got
    assert len(got["pids"]) == 2 and os.getpid() not in got["pids"]
    one, two = got["one"], got["two"]
    assert two < 1.5 * one, got        # two renders at once take about as long as one
    assert got["edit"] < 0.4 * one, got  # an edit does not wait for the render


def test_in_a_daemonic_process_in_process_work_serialises_as_measured():
    """The control: the same work in-process (``workers=0``) holds the GIL, so two renders
    take twice as long and an edit waits for the render."""
    got = _measured(0, lambda got: got["two"] > 1.6 * got["one"]
                    and got["edit"] > 0.4 * got["one"])
    assert got["runner"] == "in-process" and got["ok"], got
    one, two = got["one"], got["two"]
    assert two > 1.6 * one, got
    assert got["edit"] > 0.4 * one, got


def test_a_stalled_worker_in_a_daemonic_process_is_killed_and_the_next_render_works():
    ok, code, in_process, message, runner, then_ok = _daemonic(
        tools_toys.render_in_a_daemonic_process, True, 30)
    assert (ok, code, in_process, runner, then_ok) == (False, "timeout", False, "subprocesses",
                                                       True), message
    assert "stopped" in message


def test_where_no_subprocess_starts_either_the_work_runs_in_process(caplog):
    with WorkerPool(1) as pool, caplog.at_level(logging.WARNING, "ooxml_edit.tools.worker"):
        sub = pool._go_elsewhere("a test")
        sub.executable = str(HERE / "no-such-python")
        assert pool.run(tools_toys.worker_pid, timeout=60) == os.getpid()
        assert pool.runner == "in-process"
    assert any("no subprocess worker either" in r.getMessage() for r in caplog.records)
