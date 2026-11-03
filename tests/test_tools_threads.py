"""The opt-in thread runner (``Toolbox(runner="threads")``, :class:`ThreadRunner`).

A thread cannot be stopped: a task past its deadline is reported as ``timeout``, its result
dropped, and it keeps its slot until it ends; past ``max_abandoned`` of them, new work is
refused.  These hold on any build: with the GIL on the tasks take turns, but every rule
here is the same."""

from __future__ import annotations

import logging
import os
import threading
import time

import pytest

from ooxml_edit.errors import ArgumentError
from ooxml_edit.tools import Limits, ThreadRunner, ToolError, Toolbox, WorkerPool, gil_enabled
from ooxml_edit.tools import worker
from ooxml_edit.tools.dispatch import RUNNERS

import synthetic
import tools_toys


def _until(condition, seconds: float = 10.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def test_a_task_runs_in_a_thread_of_this_process():
    with ThreadRunner(2) as runner:
        assert runner.run(tools_toys.worker_pid, timeout=10) == os.getpid()
        assert runner.runner == "threads" and not runner.in_process and runner.pids() == []
        assert runner.run(tools_toys.sleep_then, 0, "value", timeout=10) == "value"


def test_an_exception_comes_back_as_itself():
    with ThreadRunner(1) as runner:
        with pytest.raises(IndexError, match="no page 9"):
            runner.run(tools_toys.render_page, synthetic.outer_package(), 9, 32, timeout=10)
        with pytest.raises(ToolError) as caught:
            runner.run(tools_toys.raise_tool_error, timeout=10)
        assert caught.value.code == "refused"


def test_a_task_past_its_deadline_is_abandoned_and_keeps_its_slot_until_it_ends():
    with ThreadRunner(1, max_abandoned=2) as runner:
        started = time.monotonic()
        with pytest.raises(ToolError) as caught:
            runner.run(tools_toys.sleep_then, 1.5, "dropped", timeout=0.2)
        assert caught.value.code == "timeout" and time.monotonic() - started < 1.0
        assert "cannot be stopped" in caught.value.message
        assert caught.value.details == {"timeout": 0.2, "threads": True}
        assert runner.abandoned == 1 and runner.running_abandoned == 1
        # Its slot is not handed on while it runs: the next task waits, and times out.
        with pytest.raises(ToolError) as busy:
            runner.run(tools_toys.sleep_then, 0, "late", timeout=5, queue_timeout=0.2)
        assert busy.value.code == "timeout" and "no thread was free" in busy.value.message
        assert _until(lambda: runner.running_abandoned == 0)
        assert runner.run(tools_toys.sleep_then, 0, "next", timeout=5) == "next"
        assert runner.abandoned == 1


def test_past_the_cap_of_abandoned_tasks_new_work_is_refused_at_once():
    with ThreadRunner(3, max_abandoned=1) as runner:
        with pytest.raises(ToolError):
            runner.run(tools_toys.sleep_then, 1.5, None, timeout=0.1)
        started = time.monotonic()
        with pytest.raises(ToolError) as caught:
            runner.run(tools_toys.sleep_then, 0, "refused", timeout=5)
        assert time.monotonic() - started < 0.5                 # not queued
        assert caught.value.code == "limit"
        assert "past their deadline are still running" in caught.value.message
        assert caught.value.details["abandoned_running"] == 1
        assert _until(lambda: runner.running_abandoned == 0)
        assert runner.run(tools_toys.sleep_then, 0, "again", timeout=5) == "again"


def test_the_default_cap_is_the_size():
    assert ThreadRunner(4).max_abandoned == 4
    assert ThreadRunner(4, max_abandoned=0).max_abandoned == 0
    with pytest.raises(ArgumentError):
        ThreadRunner(0)
    with pytest.raises(ArgumentError):
        ThreadRunner(2, max_abandoned=-1)


def test_the_warm_up_runs_once_per_process_not_per_thread(monkeypatch):
    calls = []

    def warm():
        calls.append(threading.get_ident())
        time.sleep(0.3)

    monkeypatch.setattr(worker, "_WARMED", {})
    with ThreadRunner(4, warm_up=[warm]) as one, ThreadRunner(2, warm_up=[warm]) as two:
        threads = [threading.Thread(target=runner.run, args=(tools_toys.worker_pid,),
                                    kwargs={"timeout": 0.1})
                   for runner in (one, one, one, two, two)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        one.warm(wait=True)
    # Each waited for the one warm-up, which was not their time (0.3 s against 0.1 s).
    assert len(calls) == 1


def test_a_failed_warm_up_is_logged_and_the_work_still_runs(monkeypatch, caplog):
    monkeypatch.setattr(worker, "_WARMED", {})
    with ThreadRunner(1, warm_up=[tools_toys.failing_warm_up]) as runner:
        with caplog.at_level(logging.WARNING, logger="ooxml_edit.tools.worker"):
            assert runner.run(tools_toys.worker_pid, timeout=10) == os.getpid()
            assert runner.run(tools_toys.worker_pid, timeout=10) == os.getpid()
    assert sum("this warm-up fails" in record.getMessage() for record in caplog.records) == 1


def test_each_task_gets_its_own_copy_of_what_could_change():
    data = synthetic.outer_package()
    shared = {"pages": [1, 2]}
    seen = []

    def keep(*args, **kwargs):
        seen.append((args, kwargs))
        return None

    with ThreadRunner(2) as runner:
        runner._run = lambda fn, args, kwargs, timeout, queue: keep(*args, **kwargs)
        runner.run(tools_toys.sleep_then, data, shared, timeout=5, extra=[3])
    (args, kwargs), = seen
    assert args[0] is data                         # bytes as they are
    assert args[1] == shared and args[1] is not shared
    assert args[1]["pages"] is not shared["pages"] and kwargs["extra"] == [3]


def test_results_are_those_of_the_worker_processes():
    data = synthetic.outer_package()
    with ThreadRunner(2) as threads, WorkerPool(1) as processes:
        for task in [(tools_toys.render_page, data, 1, 48), (tools_toys.read_title, data, 2)]:
            fn, *args = task
            by_thread = threads.run(fn, *args, timeout=60)
            by_process = processes.run(fn, *args, timeout=60)
            assert by_thread[:-1] == by_process[:-1]          # the last is the pid


def _box(**options):
    return Toolbox([tools_toys.toy_draw], formats=tools_toys.FORMATS,
                   groups=tools_toys.GROUPS, prewarm=False, **options)


def test_a_toolbox_runs_threads_when_asked_and_the_gil_is_off(monkeypatch):
    monkeypatch.setattr("ooxml_edit.tools.dispatch.gil_enabled", lambda: False)
    with _box(runner="threads", workers=3) as box:
        assert isinstance(box.pool, ThreadRunner) and box.pool.size == 3
        assert box.parallelism == 3 and box.snapshot_slots._value == 2
        session = box.session()
        doc = session.open(synthetic.outer_package(), "deck.pptx")
        for shape in ("alone", "whole"):
            result = box.dispatch(session, "toy_draw", {"doc": doc, "pages": [1, 2],
                                                        "shape": shape})
            assert result.ok and result.data["drawn"] == [1, 2], result.to_json()


def test_limits_set_the_cap_of_a_toolboxs_thread_runner(monkeypatch):
    monkeypatch.setattr("ooxml_edit.tools.dispatch.gil_enabled", lambda: False)
    with _box(runner="threads", workers=3) as box:
        assert box.pool.max_abandoned == 3
    with _box(runner="threads", workers=3, limits=Limits(max_abandoned_threads=1)) as box:
        assert box.pool.max_abandoned == 1


def test_with_the_gil_on_a_toolbox_asked_for_threads_uses_worker_processes(monkeypatch, caplog):
    monkeypatch.setattr("ooxml_edit.tools.dispatch.gil_enabled", lambda: True)
    monkeypatch.setattr("ooxml_edit.tools.dispatch._THREADS_GIL_WARNED", [])
    with caplog.at_level(logging.WARNING, logger="ooxml_edit.tools"):
        with _box(runner="threads", workers=2) as box:
            assert isinstance(box.pool, WorkerPool) and box.pool.runner == "processes"
    assert any("the GIL on" in record.getMessage() for record in caplog.records)


def test_a_runner_by_name_is_one_of_these():
    assert RUNNERS == ("processes", "threads")
    with _box(runner="processes", workers=1) as box:
        assert isinstance(box.pool, WorkerPool)
    with pytest.raises(ArgumentError, match="runner is one of"):
        _box(runner="fibres")


@pytest.mark.skipif(gil_enabled(), reason="needs a free-threaded Python with the GIL off")
def test_with_the_gil_off_work_that_would_hold_it_runs_side_by_side():
    data = synthetic.outer_package()
    count = 20_000_000

    def timed(tasks: int) -> float:
        started = time.perf_counter()
        threads = [threading.Thread(target=runner.run,
                                    args=(tools_toys.hold_the_gil, data, 1, count),
                                    kwargs={"timeout": 60}) for _ in range(tasks)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return time.perf_counter() - started

    with ThreadRunner(2) as runner:
        runner.run(tools_toys.hold_the_gil, data, 1, 1000, timeout=60)
        # Taking turns, two would take twice as long as one; the best of three tries, as
        # a loaded machine (other tests at once) slows any one of them.
        ratios = []
        for _ in range(3):
            ratios.append(timed(2) / timed(1))
            if ratios[-1] < 1.6:
                break
    assert min(ratios) < 1.6, ratios
