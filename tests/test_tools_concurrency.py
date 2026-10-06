"""Calls on one document run serially in order; on different documents, concurrently; and
calls that lock several documents cannot deadlock."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ooxml_edit.tools import DocumentFormat, Toolbox, doc_order, integer, string, tool


class FakeDocument:
    """A document with no history: the session counts its versions itself."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.active = 0
        self.most_active = 0


FAKE = DocumentFormat(kind="fake", open=lambda data: FakeDocument(),
                      detect=lambda data, name: name.endswith(".fake"))
BARRIER: dict[str, threading.Barrier] = {}


@tool("fake_mark", "Record a label on a document, slowly.",
      {"doc": string("Document id."), "label": string("The label."),
       "delay_ms": integer("Milliseconds to take.", minimum=0, optional=True)}, mutates=True)
def fake_mark(call, doc, label, delay_ms=0):
    document = call.document
    document.active += 1
    document.most_active = max(document.most_active, document.active)
    try:
        if label in BARRIER:
            BARRIER[label].wait(timeout=10)  # both calls must be inside at once
        time.sleep(delay_ms / 1000)
        if label == "fail":
            raise ValueError("this one fails")
        document.events.append(label)
    finally:
        document.active -= 1
    return {"label": label}


@tool("fake_link", "Record a link between two documents.",
      {"doc": string("The document edited."), "other": string("The other document.")},
      mutates=True, documents=("doc", "other"))
def fake_link(call, doc, other):
    call.entries[other].document.events  # read the other, under its lock
    call.document.events.append(other)


@pytest.fixture
def setup():
    with Toolbox([fake_mark, fake_link], formats=[FAKE]) as box:
        session = box.session()
        for name in ("one.fake", "two.fake", "three.fake"):
            session.adopt(FakeDocument(), "fake", name)
        yield box, session


def test_calls_on_one_document_run_serially_in_the_order_given(setup):
    box, session = setup
    calls = [("fake_mark", {"doc": "d1", "label": f"m{n}", "delay_ms": 30 - 5 * n})
             for n in range(6)]
    results = box.dispatch_many(session, calls)
    document = session.entry("d1").document
    assert document.events == [f"m{n}" for n in range(6)]
    assert document.most_active == 1
    assert [r.data["label"] for r in results] == [f"m{n}" for n in range(6)]
    assert [r.version for r in results] == [1, 2, 3, 4, 5, 6]


def test_calls_on_two_documents_run_at_the_same_time(setup):
    box, session = setup
    BARRIER.clear()
    BARRIER.update({"meet-a": (barrier := threading.Barrier(2)), "meet-b": barrier})
    try:
        results = box.dispatch_many(session, [("fake_mark", {"doc": "d1", "label": "meet-a"}),
                                              ("fake_mark", {"doc": "d2", "label": "meet-b"})])
    finally:
        BARRIER.clear()
    assert all(r.ok for r in results), [r.to_json() for r in results]


def test_interleaved_calls_keep_each_documents_order_and_the_results_order(setup):
    box, session = setup
    calls = [("fake_mark", {"doc": doc, "label": label, "delay_ms": delay})
             for doc, label, delay in [("d1", "a1", 40), ("d2", "b1", 5), ("d1", "a2", 0),
                                       ("d2", "b2", 30), ("d1", "a3", 0)]]
    results = box.dispatch_many(session, calls)
    assert [r.data["label"] for r in results] == ["a1", "b1", "a2", "b2", "a3"]
    assert session.entry("d1").document.events == ["a1", "a2", "a3"]
    assert session.entry("d2").document.events == ["b1", "b2"]


def test_a_failed_call_does_not_stop_the_later_ones(setup):
    box, session = setup
    results = box.dispatch_many(session, [("fake_mark", {"doc": "d1", "label": "x"}),
                                          ("fake_mark", {"doc": "d1", "label": "fail"}),
                                          ("fake_mark", {"doc": "d1", "label": "y"})])
    assert [r.ok for r in results] == [True, False, True]
    assert results[1].error.code == "unit" and results[1].version == 1
    assert results[2].version == 2
    assert session.entry("d1").document.events == ["x", "y"]


def test_calls_naming_no_document_or_an_invalid_one_still_come_back_in_order(setup):
    box, session = setup
    results = box.dispatch_many(session, [("fake_mark", "{oops"), ("nope", {}),
                                          ("fake_mark", {"doc": "d1", "label": "z"})])
    assert [r.ok for r in results] == [False, False, True]


def test_cross_document_calls_in_opposite_directions_cannot_deadlock(setup):
    box, session = setup
    errors: list[str] = []

    def hammer(doc: str, other: str) -> None:
        for _ in range(300):
            result = box.dispatch(session, "fake_link", {"doc": doc, "other": other})
            if not result.ok:
                errors.append(result.error.message)

    threads = [threading.Thread(target=hammer, args=pair, daemon=True)
               for pair in [("d1", "d2"), ("d2", "d1"), ("d2", "d3"), ("d3", "d1")]]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "deadlocked"
    assert errors == []
    assert len(session.entry("d1").document.events) == 300
    assert session.entry("d2").document.events.count("d1") == 300


def test_locks_are_taken_in_numeric_order():
    assert sorted(["d10", "d2", "d1"], key=doc_order) == ["d1", "d2", "d10"]


def test_the_registries_are_the_same_imported_in_threads_or_in_order():
    """Module-level registries are written at import and only read afterwards."""
    code = (
        "import json, sys, threading\n"
        "mods = ['ooxml_edit.charts', 'ooxml_edit.tools', 'ooxml_edit.opc', 'ooxml_edit.stamp']\n"
        "if sys.argv[1] == 'threads':\n"
        "    import importlib\n"
        "    ts = [threading.Thread(target=importlib.import_module, args=(m,)) for m in mods]\n"
        "    [t.start() for t in ts]; [t.join() for t in ts]\n"
        "else:\n"
        "    import importlib\n"
        "    [importlib.import_module(m) for m in mods]\n"
        "from ooxml_edit.xml import NAMESPACES, CHILD_ORDER\n"
        "print(json.dumps([sorted(NAMESPACES.items()), sorted(map(str, CHILD_ORDER.items()))]))\n"
    )
    source = str(Path(__file__).parents[1] / "src")
    outputs = [subprocess.run([sys.executable, "-c", code, mode], capture_output=True, text=True,
                              cwd=source, check=True).stdout for mode in ("threads", "order")]
    assert json.loads(outputs[0]) == json.loads(outputs[1])
