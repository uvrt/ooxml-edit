"""Slow reads on a snapshot in the worker pool, side by side (round 28, P2).

``Call.on_snapshot`` takes the document's bytes under its lock (once per version), lets the
lock go and runs the work in a worker on them; the result reports the version it read, and
warns when the document moved on meanwhile.  ``dispatch_many`` runs one turn's reads of a
document side by side, an edit after a read once the read holds its locks -- so a read that
works on a snapshot does not hold the edit up, and its result is still of the document
before the edit.
"""

from __future__ import annotations

import threading
import time

import pytest

from ooxml_edit.tools import InProcess, Limits, Result, ToolError, Toolbox, integer, shared, string, tool
from ooxml_edit.tools.worker import WorkerPool
from ooxml_edit.tools.dispatch import moved_on

import synthetic
import tools_toys

#: How long a slow read stalls: long enough that waiting for it would show.
STALL = 2


@tool("toy_ppt_slow_read", "Test only: read a page's title, slowly, on a snapshot.",
      {"doc": string("Document id."), "page": integer("Page number.", minimum=1),
       "stall": integer("Seconds the read stalls.", minimum=0, optional=True)},
      kind="pptx")
def slow_read(call, doc, page, stall=0):
    title, size, pid = call.on_snapshot(tools_toys.read_title, page, stall, timeout=30)
    return Result(summary=f"read page {page}", data={"title": title, "size": size, "pid": pid,
                                                     "may_unlock": call.may_unlock})


@pytest.fixture(params=[0, 3], ids=["in-process", "workers"])
def toolbox(request):
    with Toolbox(tools_toys.TOOLS + [slow_read, *shared.SESSION_TOOLS],
                 formats=tools_toys.FORMATS, groups=tools_toys.GROUPS,
                 workers=request.param) as box:
        if request.param:
            box.warm(wait=True)
        yield box


def _open(box):
    session = box.session()
    return session, session.open(synthetic.outer_package(), "deck.pptx")


def test_the_work_gets_this_versions_bytes_shared_by_its_readers(toolbox):
    session, doc = _open(toolbox)
    entry = session.entry(doc)
    first = toolbox.dispatch(session, "toy_ppt_slow_read", {"doc": doc, "page": 1})
    assert first.ok, first.to_json()
    assert first.data["size"] == len(entry.snapshot()) and first.version == entry.version
    assert first.warnings == []
    kept = entry.snapshot()
    toolbox.dispatch(session, "toy_ppt_slow_read", {"doc": doc, "page": 2})
    assert entry.snapshot() is kept                  # one snapshot per version, not per call
    if toolbox.parallelism > 1:
        assert first.data["pid"] != __import__("os").getpid()   # in a worker process


def test_an_edit_does_not_wait_and_the_read_says_the_document_moved_on(toolbox):
    session, doc = _open(toolbox)
    before = session.entry(doc).version
    title = session.entry(doc).document.title(1)
    read: dict = {}

    def run():
        read["result"] = toolbox.dispatch(session, "toy_ppt_slow_read",
                                          {"doc": doc, "page": 1, "stall": STALL})

    thread = threading.Thread(target=run)
    started = time.perf_counter()
    thread.start()
    time.sleep(0.5)                                  # the read has its snapshot
    edit = toolbox.dispatch(session, "toy_ppt_set_title", {"doc": doc, "page": 1, "text": "New"})
    edited = time.perf_counter() - started
    thread.join()
    result = read["result"]
    assert edit.ok and edited < STALL - 0.5, "the edit waited for the read"
    assert result.ok and result.data["title"] == title     # the snapshot's, not the edit's
    assert result.version == before
    assert result.warnings == [moved_on(doc, before, before + 1)]


def test_inside_a_batch_the_work_runs_on_the_bytes_under_the_lock(toolbox):
    session, doc = _open(toolbox)
    result = toolbox.dispatch(session, "batch", {"ops": [
        {"tool": "toy_ppt_slow_read", "arguments": {"doc": doc, "page": 2}}]})
    assert result.ok, result.to_json()
    assert result.warnings == []


def test_one_turns_reads_of_a_document_run_side_by_side(toolbox):
    session, doc = _open(toolbox)
    started = time.perf_counter()
    results = toolbox.dispatch_many(session, [
        ("toy_ppt_slow_read", {"doc": doc, "page": 1, "stall": STALL}),
        ("toy_ppt_slow_read", {"doc": doc, "page": 2, "stall": STALL})])
    took = time.perf_counter() - started
    assert all(r.ok for r in results)
    assert took < 2 * STALL - 0.5, f"the reads queued: {took:.1f} s"


def test_snapshot_work_leaves_a_worker_for_the_rest(toolbox):
    if toolbox.parallelism < 2:
        pytest.skip("in process: the work runs in the calling thread")
    assert toolbox.snapshot_slots._initial_value == toolbox.parallelism - 1


def test_an_edit_after_a_read_runs_while_the_read_works_and_the_order_holds(toolbox):
    session, doc = _open(toolbox)
    entry = session.entry(doc)
    before, title = entry.version, entry.document.title(1)
    read, edit, after = toolbox.dispatch_many(session, [
        ("toy_ppt_slow_read", {"doc": doc, "page": 1, "stall": STALL}),
        ("toy_ppt_set_title", {"doc": doc, "page": 1, "text": "New"}),
        ("toy_ppt_slow_read", {"doc": doc, "page": 1})])
    assert read.ok and edit.ok and after.ok
    # The first read is of the document before the edit, the last after it: as in order.
    assert read.data["title"] == title and read.version == before
    assert edit.version == before + 1
    assert after.data["title"] == "New" and after.version == before + 1
    # The turn's own later edit moved it: its order already says so, no warning.
    assert read.warnings == [] and after.warnings == []
    assert edit.duration < STALL - 0.5, "the edit waited for the read's work"


def test_session_tools_and_edits_still_keep_the_turns_order(toolbox):
    session, doc = _open(toolbox)
    entry = session.entry(doc)
    before = entry.version
    results = toolbox.dispatch_many(session, [
        ("toy_ppt_set_title", {"doc": doc, "page": 1, "text": "One"}),
        ("toy_ppt_slow_read", {"doc": doc, "page": 1}),
        ("undo", {"doc": doc}),
        ("toy_ppt_slow_read", {"doc": doc, "page": 1})])
    assert all(r.ok for r in results), [r.to_json() for r in results]
    assert results[1].data["title"] == "One"
    assert results[3].version == before and results[3].data["title"] != "One"


def test_side_by_side_is_for_plain_reads_only(toolbox):
    side = toolbox._side_by_side
    assert side("toy_ppt_slow_read", {"doc": "d1", "page": 1})
    assert side("toy_ppt_slow_read", '{"doc": "d1", "page": 1}')
    assert not side("toy_ppt_set_title", {"doc": "d1", "page": 1, "text": "x"})
    for name in ("undo", "close_document", "save_document", "batch"):
        assert not side(name, {"doc": "d1"}), name
    assert not side("no_such_tool", {"doc": "d1"})
    assert not side("toy_ppt_slow_read", "not json")


def test_a_turns_read_still_says_so_when_another_caller_moved_the_document(toolbox):
    session, doc = _open(toolbox)
    before = session.entry(doc).version
    other: dict = {}

    def edit_elsewhere():
        time.sleep(0.5)                              # the turn's read has its snapshot
        other["result"] = toolbox.dispatch(session, "toy_ppt_set_title",
                                           {"doc": doc, "page": 2, "text": "Elsewhere"})

    thread = threading.Thread(target=edit_elsewhere)
    thread.start()
    read, edit = toolbox.dispatch_many(session, [
        ("toy_ppt_slow_read", {"doc": doc, "page": 1, "stall": STALL}),
        ("toy_ppt_set_title", {"doc": doc, "page": 1, "text": "In the turn"})])
    thread.join()
    assert read.ok and edit.ok and other["result"].ok
    now = session.entry(doc).version
    assert read.version == before and now == before + 2
    assert read.warnings == [moved_on(doc, before, now)]


def test_snapshot_workers_sets_the_slots():
    with Toolbox(tools_toys.TOOLS, formats=tools_toys.FORMATS, groups=tools_toys.GROUPS,
                 workers=3, limits=Limits(snapshot_workers=3)) as box:
        assert box.snapshot_slots._value == 3


@pytest.mark.parametrize("runner", ["workers", "in-process"])
def test_a_queue_timeout_keeps_the_wait_for_a_worker_out_of_the_deadline(runner):
    pool = WorkerPool(1) if runner == "workers" else InProcess(1)
    with pool:
        if runner == "workers":
            pool.warm(wait=True)
        busy = threading.Thread(target=pool.run, args=(tools_toys.read_title,
                                                       synthetic.outer_package(), 1, 3),
                                kwargs={"timeout": 30})
        busy.start()
        time.sleep(0.5)                               # the one worker is busy for ~2.5 s
        # Waits ~2.5 s for the worker, then runs in well under its 2 s: no timeout.
        pool.run(tools_toys.read_title, synthetic.outer_package(), 1, timeout=2,
                 queue_timeout=30)
        busy.join()
        busy = threading.Thread(target=pool.run, args=(tools_toys.read_title,
                                                       synthetic.outer_package(), 1, 3),
                                kwargs={"timeout": 30})
        busy.start()
        time.sleep(0.5)
        with pytest.raises(ToolError) as caught:      # the wait counts: as before
            pool.run(tools_toys.read_title, synthetic.outer_package(), 1, timeout=1)
        assert caught.value.code == "timeout"
        busy.join()
