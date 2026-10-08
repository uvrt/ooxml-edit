"""The worker pool: results from another process, and deadlines kept by killing it."""

from __future__ import annotations

import os
import time

import pytest

from ooxml_edit.tools import Limits, ToolError, Toolbox, WorkerPool

import synthetic
import tools_toys


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a zombie child is gone for our purposes
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return True


@pytest.fixture(scope="module")
def pool():
    with WorkerPool(1) as pool:
        yield pool


def test_a_task_runs_in_another_process(pool):
    pid = pool.run(tools_toys.worker_pid, timeout=60)
    assert pid != os.getpid()
    assert pool.run(tools_toys.worker_pid, timeout=60) == pid   # the worker is kept


def test_an_exception_comes_back_as_itself(pool):
    with pytest.raises(IndexError, match="no page 9"):
        pool.run(tools_toys.render_page, synthetic.outer_package(), 9, 32, timeout=60)
    with pytest.raises(ToolError) as caught:
        pool.run(tools_toys.raise_tool_error, timeout=60)
    assert caught.value.code == "refused" and caught.value.valid_options == ["a", "b"]


def test_a_task_past_its_deadline_is_killed_and_reports_timeout(pool):
    pool.run(tools_toys.worker_pid, timeout=60)        # a warm worker
    victim = pool.pids()[0]
    started = time.monotonic()
    with pytest.raises(ToolError) as caught:
        pool.run(tools_toys.render_page, synthetic.outer_package(), 1, 32, 30, timeout=1.5)
    assert caught.value.code == "timeout"
    assert time.monotonic() - started < 10
    assert pool.killed[-1] == victim
    deadline = time.monotonic() + 10
    while _alive(victim) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(victim)
    assert pool.run(tools_toys.worker_pid, timeout=60) != victim   # replaced, and working


def test_a_killed_render_returns_a_timeout_result():
    limits = Limits(render_timeout=1.5)
    with Toolbox(tools_toys.TOOLS, formats=tools_toys.FORMATS, limits=limits, workers=1) as box:
        session = box.session(limits=limits)
        session.open(synthetic.outer_package(), "deck.pptx")
        warm = box.dispatch(session, "toy_ppt_render", {"doc": "d1", "page": 1, "width": 32})
        assert warm.ok, warm.to_json()
        result = box.dispatch(session, "toy_ppt_render",
                              {"doc": "d1", "page": 2, "width": 32, "stall": 30})
        assert not result.ok and result.error.code == "timeout"
        assert result.images == [] and session.images_used == 1
        assert box.pool.killed
        after = box.dispatch(session, "toy_ppt_render", {"doc": "d1", "page": 2, "width": 32})
        assert after.ok


def test_waiting_for_a_busy_pool_counts_against_the_deadline():
    import threading
    with WorkerPool(1) as pool:
        pool.run(tools_toys.worker_pid, timeout=60)
        blocker = threading.Thread(target=lambda: pytest.raises(
            ToolError, pool.run, tools_toys.render_page, synthetic.outer_package(), 1, 32, 5,
            timeout=3))
        blocker.start()
        time.sleep(0.5)
        with pytest.raises(ToolError) as caught:
            pool.run(tools_toys.worker_pid, timeout=0.5)
        assert caught.value.code == "timeout"
        blocker.join(30)


# -- in-process: a daemonic parent, workers=0, an injected runner -------------------------------


def _in_a_daemonic_process(fallback: bool, stall: int = 0):
    import multiprocessing
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=tools_toys.render_in_a_daemonic_process,
                              args=(queue, fallback, stall), daemon=True)
    process.start()
    try:
        return queue.get(timeout=120)
    finally:
        process.join(30)


def test_in_a_daemonic_process_the_old_pool_fails_as_production_saw():
    ok, code, _, message = _in_a_daemonic_process(fallback=False)
    assert not ok and code == "internal" and "daemonic" in message


def test_in_a_daemonic_process_the_pool_renders_in_process():
    ok, code, in_process, _ = _in_a_daemonic_process(fallback=True)
    assert (ok, code, in_process) == (True, None, True)


def test_in_a_daemonic_process_a_deadline_still_answers_timeout():
    ok, code, in_process, message = _in_a_daemonic_process(fallback=True, stall=30)
    assert (ok, code, in_process) == (False, "timeout", True)
    assert "abandoned" in message


def test_workers_zero_runs_in_this_process_with_its_deadline():
    with WorkerPool(0) as pool:
        assert pool.in_process
        assert pool.run(tools_toys.worker_pid, timeout=60) == os.getpid()
        with pytest.raises(IndexError, match="no page 9"):
            pool.run(tools_toys.render_page, synthetic.outer_package(), 9, 32, timeout=60)
        started = time.monotonic()
        with pytest.raises(ToolError) as caught:
            pool.run(tools_toys.render_page, synthetic.outer_package(), 1, 32, 2, timeout=0.3)
        assert caught.value.code == "timeout" and caught.value.details["in_process"]
        assert time.monotonic() - started < 1.5 and pool.abandoned == 1
        # The abandoned task holds the only slot until it ends: the next one waits for it.
        assert pool.run(tools_toys.worker_pid, timeout=10) == os.getpid()


def test_a_toolbox_with_workers_zero_or_a_runner_renders_in_process():
    from ooxml_edit.tools import InProcess

    for options in ({"workers": 0}, {"runner": InProcess(2)}):
        with Toolbox(tools_toys.TOOLS, formats=tools_toys.FORMATS, **options) as box:
            session = box.session()
            session.open(synthetic.outer_package(), "deck.pptx")
            result = box.dispatch(session, "toy_ppt_render", {"doc": "d1", "page": 1, "width": 32})
            assert result.ok and result.images, options
            assert box.pool.pids() == []
