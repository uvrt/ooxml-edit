"""Slow reading work runs on a snapshot with the document's lock let go (``Call.unlocked``).

A render rasterises for seconds; it used to hold the document's lock throughout, so an edit
to another page of the same document waited for it.  Now the handler takes the bytes and the
version under the lock, lets the lock go while the worker draws, and takes it back to cache
the image under the snapshot's version -- which the result reports.  In a worker process and
in the in-process fallback alike; inside a batch, or in a changing call, the lock stays held.
"""

from __future__ import annotations

import threading
import time

import pytest

from ooxml_edit.tools import Result, Toolbox, shared, string, tool

import synthetic
import tools_toys

#: How long the toy renderer stalls: long enough that an edit waiting for it would show.
STALL = 2


@pytest.fixture(params=[0, 1], ids=["in-process", "worker"])
def toolbox(request):
    with Toolbox(tools_toys.TOOLS + [probe, *shared.SESSION_TOOLS], formats=tools_toys.FORMATS, groups=tools_toys.GROUPS,
                 workers=request.param) as box:
        yield box


def _open(box):
    session = box.session()
    doc = session.open(synthetic.outer_package(), "deck.pptx")
    return session, doc


def test_an_edit_to_page_2_does_not_wait_for_a_render_of_page_1(toolbox):
    session, doc = _open(toolbox)
    toolbox.dispatch(session, "toy_ppt_render", {"doc": doc, "page": 1, "width": 64})   # warm-up
    before = session.entry(doc).version
    rendered: dict = {}

    def render():
        rendered["result"] = toolbox.dispatch(
            session, "toy_ppt_render", {"doc": doc, "page": 1, "width": 96, "stall": STALL})
        rendered["done"] = time.perf_counter()

    thread = threading.Thread(target=render)
    started = time.perf_counter()
    thread.start()
    time.sleep(0.3)                                      # the render has its snapshot
    edit = toolbox.dispatch(session, "toy_ppt_set_title", {"doc": doc, "page": 2, "text": "Edited"})
    edited = time.perf_counter()
    thread.join()

    assert edit.ok and edit.version == before + 1
    assert edited - started < STALL - 0.5, "the edit waited for the render"
    assert rendered["done"] > edited                     # it really ran while the render did
    result = rendered["result"]
    assert result.ok and len(result.images) == 1
    assert result.version == before                      # what was drawn: the snapshot
    entry = session.entry(doc)
    assert entry.version == before + 1
    assert entry.document.title(2) == "Edited"
    # The image is cached under the snapshot's version; the edited version renders afresh.
    assert entry.render_cache.get((before, ("page", 1, 96))) is not None
    assert entry.render_cache.get((before + 1, ("page", 1, 96))) is None

    # Undo and history: the render made no step; one undo takes the edit back.
    undone = toolbox.dispatch(session, "undo", {"doc": doc})
    assert undone.ok, undone.to_json()
    assert entry.document.title(2) != "Edited"
    assert entry.version == before
    again = toolbox.dispatch(session, "toy_ppt_render", {"doc": doc, "page": 1, "width": 96})
    assert again.ok and again.version == before          # back at the drawn state: a cache hit


def test_the_lock_is_held_again_after_the_work(toolbox):
    session, doc = _open(toolbox)
    result = toolbox.dispatch(session, "toy_probe", {"doc": doc, "mode": "plain"})
    assert result.ok and result.data == {"may_unlock": True, "released": True,
                                         "held_inside": False, "held_after": True}


def test_the_lock_comes_back_when_the_work_fails(toolbox):
    session, doc = _open(toolbox)
    failed = toolbox.dispatch(session, "toy_probe", {"doc": doc, "mode": "fail"})
    assert not failed.ok
    # Released exactly once by dispatch: another thread can take it now.
    taken = threading.Event()

    def take():
        with session.entry(doc).lock:
            taken.set()

    thread = threading.Thread(target=take)
    thread.start()
    thread.join(5)
    assert taken.is_set()
    assert toolbox.dispatch(session, "toy_ppt_set_title", {"doc": doc, "page": 1, "text": "x"}).ok


def test_inside_a_batch_the_lock_stays_held(toolbox):
    session, doc = _open(toolbox)
    result = toolbox.dispatch(session, "batch", {"ops": [
        {"tool": "toy_probe", "arguments": {"doc": doc, "mode": "plain"}}]})
    assert result.ok, result.to_json()
    assert PROBED[-1] == {"may_unlock": False, "released": False, "held_inside": True,
                          "held_after": True}


@tool("toy_probe", "Test only: say whether the call could let its lock go.",
      {"doc": string("Document id."), "mode": string("plain or fail.", enum=["plain", "fail"])},
      kind="pptx")
def probe(call, doc, mode):
    lock = call.entry.lock
    may = call.may_unlock
    with call.unlocked() as released:
        held_inside = _held_elsewhere(lock)
        if mode == "fail":
            raise ValueError("the work failed")
    data = {"may_unlock": may, "released": released, "held_inside": held_inside,
            "held_after": _held_elsewhere(lock)}
    PROBED.append(data)
    return Result(summary="probed", data=data)


PROBED: list[dict] = []


def _held_elsewhere(lock) -> bool:
    """Whether another thread finds ``lock`` taken."""
    out = {}

    def attempt():
        out["got"] = lock.acquire(timeout=0.05)
        if out["got"]:
            lock.release()

    thread = threading.Thread(target=attempt)
    thread.start()
    thread.join()
    return not out["got"]
