"""Round 33 (R16c): no slow reading work under a document's lock where it can be helped.

* A batch's reads after its last change -- a ``check`` that lays the document out -- run
  once the batch's undo step is closed, each as the same call alone: they let the lock go
  while they work, so another call on the document does not wait for them, and they answer
  of the batch's version.  A read before the last change runs inside the batch, as before.
  A failing read after the last change cannot take the batch back, atomic or not: it is
  reported as its op's error.
* A changing call may do slow reading work ahead of its change (``Tool.aheads``): before
  its undo step, with the lock let go; the handler runs after, under the lock, on the
  document as it finds it.
"""

from __future__ import annotations

import threading
import time

import pytest

from ooxml_edit.tools import Limits, Result, ToolError, Toolbox, shared, string, tool

import synthetic
import tools_toys

#: How long the slow toy work stalls: long enough that a call waiting for it would show.
STALL = 1.5

SEEN: list[dict] = []


def _held_elsewhere(lock) -> bool:
    out = {}

    def attempt():
        out["got"] = lock.acquire(timeout=0.05)
        if out["got"]:
            lock.release()

    thread = threading.Thread(target=attempt)
    thread.start()
    thread.join()
    return not out["got"]


@tool("toy_slow_read", "Test only: a slow read of a page's title, the lock let go if it may.",
      {"doc": string("Document id."), "page": string("Page number."),
       "mode": string("ok or fail.", enum=["ok", "fail"], optional=True),
       "target": string("Test only: a ref.", optional=True)},
      kind="pptx", refs=("target",))
def toy_slow_read(call, doc, page, mode="ok", target=None):
    title = call.document.title(int(page))
    version = call.entry.version
    may = call.may_unlock
    with call.unlocked() as released:
        held = _held_elsewhere(call.entry.lock)
        time.sleep(STALL)
    SEEN.append({"may_unlock": may, "released": released, "held_inside": held,
                 "in_batch": call.in_batch})
    if mode == "fail":
        raise ToolError("not_found", "the slow read failed", field="mode")
    return Result(summary=f"Title {title!r}", data={"title": title, "version": version})


def _lay_out_ahead(call, doc, page, text, fail=False):
    """The ahead work of toy_set_after_reading: a slow read of the deck, the lock let go."""
    may = call.may_unlock
    with call.unlocked() as released:
        held = _held_elsewhere(call.entry.lock)
        time.sleep(STALL)
    SEEN.append({"ahead": True, "may_unlock": may, "released": released, "held_inside": held,
                 "changing": call.changing})
    call.warn("read ahead")
    if fail:
        raise ToolError("timeout", "the reading ahead took too long")


@tool("toy_set_after_reading", "Test only: set a page's title after a slow read of the deck.",
      {"doc": string("Document id."), "page": string("Page number."),
       "text": string("The title."), "fail": string("Test only.", optional=True)},
      kind="pptx", mutates=True, ahead=_lay_out_ahead)
def toy_set_after_reading(call, doc, page, text, fail=None):
    SEEN.append({"handler": True, "may_unlock": call.may_unlock,
                 "held": _held_elsewhere(call.entry.lock), "in_batch": call.in_batch})
    call.document.set_title(int(page), text)
    return Result(summary=f"Set the title of page {page}", changed=[f"page:{page}"])


@pytest.fixture(params=[0, 1], ids=["in-process", "worker"])
def toolbox(request):
    with Toolbox(tools_toys.TOOLS + [toy_slow_read, toy_set_after_reading,
                                     *shared.SESSION_TOOLS],
                 formats=tools_toys.FORMATS, groups=tools_toys.GROUPS,
                 workers=request.param) as box:
        yield box


@pytest.fixture(autouse=True)
def _clear():
    SEEN.clear()


def _open(box):
    session = box.session()
    return session, session.open(synthetic.outer_package(), "deck.pptx")


def _op(name, **arguments):
    return {"tool": name, "arguments": arguments}


def _set(doc, page, text):
    return _op("toy_ppt_set_title", doc=doc, page=page, text=text)


def _edit_meanwhile(box, session, doc, text="Meanwhile"):
    """An edit from another thread while the slow work runs: how long it waited."""
    took = {}

    def edit():
        started = time.perf_counter()
        took["result"] = box.dispatch(session, "toy_ppt_set_title",
                                      {"doc": doc, "page": 2, "text": text})
        took["seconds"] = time.perf_counter() - started

    thread = threading.Thread(target=edit)
    return thread, took


# -- a batch's reads after its last change ------------------------------------------------------


@pytest.mark.parametrize("atomic", [False, True])
def test_a_read_after_the_last_change_does_not_hold_the_document(toolbox, atomic):
    session, doc = _open(toolbox)
    before = session.entry(doc).version
    thread, took = _edit_meanwhile(toolbox, session, doc)
    out = {}

    def batch():
        out["result"] = toolbox.dispatch(session, "batch", {"atomic": atomic, "ops": [
            _set(doc, 1, "Changed"), _op("toy_slow_read", doc=doc, page="1")]})

    runner = threading.Thread(target=batch)
    runner.start()
    time.sleep(0.4)                           # the batch applied; its read is working
    thread.start()
    thread.join()
    runner.join()
    result = out["result"]
    assert result.ok, result.to_json()
    assert took["result"].ok and took["seconds"] < STALL - 0.5, "the edit waited for the read"
    assert SEEN[-1] == {"may_unlock": True, "released": True, "held_inside": False,
                        "in_batch": False}
    # The read answered of the batch's version, which the batch's result reports; the edit
    # meanwhile is its own (warned about: the document moved on).
    read = result.data["ops"][1]
    assert read["data"] == {"title": "Changed", "version": before + 1}
    assert result.version == before + 1 and took["result"].version == before + 2
    assert any(w.startswith("ops[1]: d1 changed while this call read it") for w in result.warnings), \
        result.warnings
    assert result.summary == "Ran 2 op(s) as one step: Set the title of page 1; Title 'Changed'"
    # One undo step for the batch; the read made none.
    assert toolbox.dispatch(session, "undo", {"doc": doc, "steps": 2}).ok
    assert session.entry(doc).document.title(1) != "Changed"


def test_a_read_before_the_last_change_runs_inside_the_batch(toolbox):
    session, doc = _open(toolbox)
    result = toolbox.dispatch(session, "batch", {"ops": [
        _set(doc, 1, "First"), _op("toy_slow_read", doc=doc, page="1"), _set(doc, 2, "Second")]})
    assert result.ok, result.to_json()
    assert SEEN[-1] == {"may_unlock": False, "released": False, "held_inside": True,
                        "in_batch": True}
    assert result.data["ops"][1]["data"]["title"] == "First"      # the state between


def test_a_failing_read_after_the_last_change_does_not_undo_an_atomic_batch(toolbox):
    session, doc = _open(toolbox)
    before = session.entry(doc).version
    result = toolbox.dispatch(session, "batch", {"atomic": True, "ops": [
        _set(doc, 1, "Kept"), _op("toy_slow_read", doc=doc, page="1", mode="fail"),
        _op("toy_slow_read", doc=doc, page="1")]})
    assert result.ok, result.to_json()
    assert session.entry(doc).document.title(1) == "Kept"
    assert session.entry(doc).version == before + 1
    assert result.data["applied"] == 1 and result.data["failed"] == [1]
    ops = result.data["ops"]
    assert ops[1] == {"op": 1, "tool": "toy_slow_read",
                      "error": {"code": "not_found", "message": "the slow read failed",
                                "field": "mode"}}
    assert ops[2]["data"]["title"] == "Kept"
    assert "Not answered, the batch applied -- resend only these on their own: " \
           "ops[1] (toy_slow_read): the slow read failed" in result.summary


def test_a_failing_read_before_the_last_change_still_undoes_an_atomic_batch(toolbox):
    session, doc = _open(toolbox)
    result = toolbox.dispatch(session, "batch", {"atomic": True, "ops": [
        _set(doc, 1, "Gone"), _op("toy_slow_read", doc=doc, page="1", mode="fail"),
        _set(doc, 2, "Gone too")]})
    assert not result.ok and result.error.details["op"] == 1
    assert session.entry(doc).document.title(1) != "Gone"


def test_a_failing_read_after_the_last_change_in_a_partial_batch(toolbox):
    session, doc = _open(toolbox)
    result = toolbox.dispatch(session, "batch", {"ops": [
        _set(doc, 1, "Kept"), _op("toy_slow_read", doc=doc, page="9", mode="ok")]})
    assert result.ok, result.to_json()
    assert result.data["applied"] == 1 and result.data["failed"] == [1]
    assert result.data["ops"][1]["error"]["code"] == "not_found"
    assert session.entry(doc).document.title(1) == "Kept"


def test_a_batch_of_reads_only_answers_each_as_alone(toolbox):
    session, doc = _open(toolbox)
    version = session.entry(doc).version
    result = toolbox.dispatch(session, "batch", {"ops": [
        _op("toy_slow_read", doc=doc, page="1"), _op("toy_slow_read", doc=doc, page="2")]})
    assert result.ok, result.to_json()
    assert [seen["released"] for seen in SEEN] == [True, True]
    assert result.summary.startswith("Read: ")
    assert session.entry(doc).version == version and result.version == version


def test_a_read_after_the_last_change_names_the_failed_op_that_would_have_made_its_ref(toolbox):
    session, doc = _open(toolbox)
    result = toolbox.dispatch(session, "batch", {"ops": [
        _set(doc, 1, "Kept"),
        _op("toy_ppt_set_title", doc=doc, page=99, text="x", ref="later"),
        _op("toy_slow_read", doc=doc, page="1", target="$later")]})
    assert result.ok and result.data["failed"] == [1, 2], result.to_json()
    error = result.data["ops"][2]["error"]
    assert error["code"] in ("not_found", "invalid_arguments"), error


def test_a_read_after_the_last_change_past_the_deadline_is_not_run(toolbox):
    session = toolbox.session(limits=Limits(batch_timeout=STALL / 2))
    doc = session.open(synthetic.outer_package(), "deck.pptx")
    result = toolbox.dispatch(session, "batch", {"atomic": True, "ops": [
        _set(doc, 1, "Kept"), _op("toy_slow_read", doc=doc, page="1"),
        _op("toy_slow_read", doc=doc, page="2")]})
    assert result.ok, result.to_json()
    assert session.entry(doc).document.title(1) == "Kept"
    assert result.data["not_run"]["first"] == 2 and "Not run: " in result.summary


# -- work ahead of a change ------------------------------------------------------------------


def test_a_changing_call_s_work_ahead_lets_the_lock_go(toolbox):
    session, doc = _open(toolbox)
    before = session.entry(doc).version
    thread, took = _edit_meanwhile(toolbox, session, doc)
    out = {}

    def change():
        out["result"] = toolbox.dispatch(session, "toy_set_after_reading",
                                         {"doc": doc, "page": "1", "text": "After"})

    runner = threading.Thread(target=change)
    runner.start()
    time.sleep(0.4)                           # working ahead, the lock let go
    thread.start()
    thread.join()
    runner.join()
    result = out["result"]
    assert result.ok, result.to_json()
    assert took["result"].ok and took["seconds"] < STALL - 0.5, "the edit waited for the work"
    ahead, handler = SEEN
    assert ahead == {"ahead": True, "may_unlock": True, "released": True, "held_inside": False,
                     "changing": True}
    assert handler == {"handler": True, "may_unlock": False, "held": True, "in_batch": False}
    # The change came after the edit meanwhile, each its own step and version.
    assert took["result"].version == before + 1 and result.version == before + 2
    assert result.warnings == ["read ahead"]
    assert result.touched == [{"doc": doc, "part": synthetic.PAGE1}]
    entry = session.entry(doc)
    assert entry.document.title(1) == "After" and entry.document.title(2) == "Meanwhile"
    assert toolbox.dispatch(session, "undo", {"doc": doc}).ok
    assert entry.document.title(1) != "After" and entry.document.title(2) == "Meanwhile"


def test_work_ahead_that_fails_ends_the_call_with_nothing_changed(toolbox):
    session, doc = _open(toolbox)
    before = session.entry(doc).version
    result = toolbox.dispatch(session, "toy_set_after_reading",
                              {"doc": doc, "page": "1", "text": "Never", "fail": "yes"})
    assert not result.ok and result.error.code == "timeout"
    assert session.entry(doc).version == before
    assert session.entry(doc).document.title(1) != "Never"
    assert not _held_elsewhere(session.entry(doc).lock)


def test_inside_a_batch_there_is_no_work_ahead(toolbox):
    session, doc = _open(toolbox)
    result = toolbox.dispatch(session, "batch", {"ops": [
        _op("toy_set_after_reading", doc=doc, page="1", text="In a batch")]})
    assert result.ok, result.to_json()
    assert SEEN == [{"handler": True, "may_unlock": False, "held": True, "in_batch": True}]


def test_concurrent_changes_with_work_ahead_each_apply_once(toolbox):
    session, doc = _open(toolbox)
    before = session.entry(doc).version
    results = []

    def change(n):
        results.append(toolbox.dispatch(session, "toy_set_after_reading",
                                        {"doc": doc, "page": str(1 + n % 2), "text": f"T{n}"}))

    threads = [threading.Thread(target=change, args=(n,)) for n in range(4)]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(result.ok for result in results), [r.to_json() for r in results]
    # Their slow work ran side by side, not one after another.
    assert time.perf_counter() - started < 4 * STALL - 1
    assert sorted(result.version for result in results) == [before + n for n in range(1, 5)]
    assert session.entry(doc).version == before + 4
