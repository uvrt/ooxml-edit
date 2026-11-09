"""Reads of the committed version (round 45): a reading call never waits for an edit in flight.

A format that copies its documents (``DocumentFormat.read_copy``) has every changing call keep
the version it leaves as committed, under the document's lock (``DocumentEntry.commit``); a
reading call works on a copy of that version (``Session.read_lock``), under the copy's own
lock, so an edit in flight -- which changes the document, never the copy -- does not hold it
up, nor it the edit.  A caller's own edit is committed before its call returns, so its next
read sees it.
"""

from __future__ import annotations

import dataclasses
import threading
import time

import pytest

from ooxml_edit.tools import Result, Toolbox, integer, shared, string, tool

import synthetic
import tools_toys

#: How long the slow edit holds the document: long enough that waiting for it would show.
STALL = 2
MADE: list[int] = []


def _read_copy(document):
    def make(data):
        MADE.append(1)
        return tools_toys.ToyDocument(data)
    return make


TEXT = dataclasses.replace(tools_toys.TEXT, read_copy=_read_copy)


@tool("toy_word_slow_insert", "Test only: insert an item, slowly, holding the document.",
      {"doc": string("Document id."), "text": string("The item's text."),
       "stall": integer("Seconds the edit holds the document.", minimum=0)},
      kind="docx", mutates=True)
def slow_insert(call, doc, text, stall):
    call.document.insert(text, "end")
    time.sleep(stall)
    return Result(summary="inserted", created=[f"item:{len(call.document.items())}"])


@tool("toy_word_slow_read", "Test only: read the items, slowly.",
      {"doc": string("Document id."), "stall": integer("Seconds the read takes.", minimum=0)},
      kind="docx")
def slow_read(call, doc, stall):
    items = call.document.items()
    time.sleep(stall)
    return Result(summary="read", data={"items": items, "version": call.entry.version})


def _box(fmt=TEXT):
    return Toolbox(tools_toys.TOOLS + [slow_insert, slow_read, *shared.SESSION_TOOLS],
                   formats=[tools_toys.DECK, fmt], groups=tools_toys.GROUPS, workers=0)


def _open(box):
    session = box.session()
    return session, session.open(synthetic.outer_package(), "notes.docx")


def _in_thread(fn):
    out: dict = {}
    thread = threading.Thread(target=lambda: out.setdefault("result", fn()))
    thread.start()
    return thread, out


def test_a_read_does_not_wait_for_an_edit_in_flight_and_reads_the_committed_version():
    with _box() as box:
        session, doc = _open(box)
        box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "first", "at": "end"})
        before = session.entry(doc).version
        thread, edit = _in_thread(lambda: box.dispatch(
            session, "toy_word_slow_insert", {"doc": doc, "text": "second", "stall": STALL}))
        time.sleep(0.3)                                   # the edit holds the document
        started = time.perf_counter()
        read = box.dispatch(session, "toy_word_read", {"doc": doc})
        took = time.perf_counter() - started
        thread.join()
        assert read.ok, read.to_json()
        assert took < STALL - 0.5, f"the read waited {took:.1f} s for the edit"
        assert read.version == before and "second" not in read.data and "first" in read.data
        assert edit["result"].ok and edit["result"].version == before + 1
        after = box.dispatch(session, "toy_word_read", {"doc": doc})
        assert "second" in after.data and after.version == before + 1


def test_a_callers_own_edit_is_committed_before_its_next_read():
    with _box() as box:
        session, doc = _open(box)
        view = session.view(context={"agent": "one"})
        for n in range(3):
            edit = box.dispatch(view, "toy_word_insert", {"doc": doc, "text": f"item {n}", "at": "end"})
            read = box.dispatch(view, "toy_word_read", {"doc": doc})
            assert f"item {n}" in read.data and read.version == edit.version


def test_an_edit_does_not_wait_for_a_slow_read_of_the_committed_version():
    with _box() as box:
        session, doc = _open(box)
        box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "first", "at": "end"})
        before = session.entry(doc).version
        thread, read = _in_thread(lambda: box.dispatch(
            session, "toy_word_slow_read", {"doc": doc, "stall": STALL}))
        time.sleep(0.3)
        started = time.perf_counter()
        edit = box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "second", "at": "end"})
        took = time.perf_counter() - started
        thread.join()
        assert edit.ok and took < STALL - 0.5, f"the edit waited {took:.1f} s for the read"
        result = read["result"]
        assert result.ok and result.version == before and "second" not in result.data["items"]
        assert result.data["version"] == before          # the handler's entry is pinned
        assert result.warnings == []                     # it held its copy throughout


def test_one_copy_per_committed_version_shared_by_its_reads():
    with _box() as box:
        session, doc = _open(box)
        box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "first", "at": "end"})
        MADE.clear()
        for _ in range(3):
            assert box.dispatch(session, "toy_word_read", {"doc": doc}).ok
        assert len(MADE) == 1
        entry = session.entry(doc)
        reader = entry.reader()
        assert reader is not None and reader.live is entry and reader.version == entry.version
        assert reader.document is not entry.document
        assert reader.render_cache is entry.render_cache and reader.log is entry.log
        box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "second", "at": "end"})
        assert entry.reader() is not reader               # a new version, a new copy
        assert box.dispatch(session, "toy_word_read", {"doc": doc}).ok
        assert len(MADE) == 2


def test_the_call_log_and_results_name_the_document_and_its_version():
    with _box() as box:
        session, doc = _open(box)
        edit = box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "first", "at": "end"})
        read = box.dispatch(session, "toy_word_read", {"doc": doc})
        assert read.doc == doc and read.version == edit.version
        assert session.entry(doc).log[-1].tool == "toy_word_read"
        assert session.entry(doc).log[-1].version == edit.version


def test_refs_of_the_committed_version_resolve_in_its_reads():
    with _box() as box:
        session, doc = _open(box)
        entry = session.entry(doc)
        with entry.lock:
            entry.keep_refs({"first": "item:1"})
        box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "first", "at": "end"})
        reader = (box.dispatch(session, "toy_word_read", {"doc": doc}), entry.reader())[1]
        assert reader.refs == entry.refs == {"first": "item:1"}


def test_a_format_that_does_not_copy_reads_under_the_lock_as_before():
    with _box(tools_toys.TEXT) as box:
        session, doc = _open(box)
        box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "first", "at": "end"})
        assert session.entry(doc).committed is None
        thread, edit = _in_thread(lambda: box.dispatch(
            session, "toy_word_slow_insert", {"doc": doc, "text": "second", "stall": STALL}))
        time.sleep(0.3)
        read = box.dispatch(session, "toy_word_read", {"doc": doc})
        thread.join()
        assert read.ok and "second" in read.data          # it waited: the edit's version


def test_a_copy_that_cannot_be_made_reads_under_the_lock():
    def broken(document):
        def make(data):
            raise RuntimeError("no copy")
        return make

    with _box(dataclasses.replace(tools_toys.TEXT, read_copy=broken)) as box:
        session, doc = _open(box)
        box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "first", "at": "end"})
        read = box.dispatch(session, "toy_word_read", {"doc": doc})
        assert read.ok and "first" in read.data


def test_session_tools_still_act_on_the_document():
    with _box() as box:
        session, doc = _open(box)
        box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "first", "at": "end"})
        undone = box.dispatch(session, "undo", {"doc": doc})
        assert undone.ok, undone.to_json()
        read = box.dispatch(session, "toy_word_read", {"doc": doc})
        assert "first" not in (read.data or "") and read.version == session.entry(doc).version


@pytest.mark.parametrize("calls", [3, 8])
def test_reads_and_edits_from_many_threads_keep_every_version_consistent(calls):
    with _box() as box:
        session, doc = _open(box)
        errors: list[str] = []

        def writer(n):
            for k in range(calls):
                result = box.dispatch(session, "toy_word_insert", {"doc": doc, "text": f"w{n}-{k}", "at": "end"})
                if not result.ok:
                    errors.append(str(result.to_json()))

        def reader():
            for _ in range(calls * 2):
                result = box.dispatch(session, "toy_word_slow_read", {"doc": doc, "stall": 0})
                items = result.data["items"]
                # A version's items are what the edits up to it wrote: no half-applied edit.
                if result.version != len(items) or result.data["version"] != result.version:
                    errors.append(f"version {result.version} with {len(items)} items")

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(3)]
        threads += [threading.Thread(target=reader) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert len(box.dispatch(session, "toy_word_slow_read", {"doc": doc, "stall": 0}).data["items"]) == 3 * calls


# -- stage 2: a change's checks run on the version it made, its lock let go -------------------

CHECKED: list[tuple[int, list[str]]] = []


def _slow_checks(entry, touched):
    items = entry.document.items()
    time.sleep(STALL)
    CHECKED.append((entry.version, items))
    return {"items": len(items), "version": entry.version}


CHECKING = dataclasses.replace(TEXT, checks=_slow_checks)


def test_an_edit_does_not_wait_for_the_checks_of_the_edit_before_it():
    with _box(CHECKING) as box:
        session, doc = _open(box)
        CHECKED.clear()
        thread, first = _in_thread(lambda: box.dispatch(
            session, "toy_word_insert", {"doc": doc, "text": "first", "at": "end"}))
        time.sleep(0.5)                                   # its checks are running
        started = time.perf_counter()
        # A concurrent edit in between: it changes the document while the checks read.
        second = box.dispatch(session, "batch", {"ops": [
            {"tool": "toy_word_insert", "arguments": {"doc": doc, "text": "second", "at": "end"}}]})
        edited = time.perf_counter() - started
        thread.join()
        first = first["result"]
        assert first.ok and second.ok
        assert edited < 2 * STALL - 0.5, f"the second edit waited {edited:.1f} s"
        # Each change's checks are of the version it made, whatever came after.
        assert first.checks == {"items": 1, "version": first.version}
        assert second.checks == {"items": 2, "version": second.version}
        assert sorted(CHECKED) == [(first.version, ["first"]), (second.version, ["first", "second"])]


def test_without_copies_the_checks_run_under_the_lock_as_before():
    plain = dataclasses.replace(tools_toys.TEXT, checks=_slow_checks)
    with _box(plain) as box:
        session, doc = _open(box)
        thread, first = _in_thread(lambda: box.dispatch(
            session, "toy_word_insert", {"doc": doc, "text": "first", "at": "end"}))
        time.sleep(0.5)
        started = time.perf_counter()
        second = box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "second", "at": "end"})
        edited = time.perf_counter() - started
        thread.join()
        assert second.ok and edited > STALL, "the checks did not hold the lock"
        assert first["result"].checks == {"items": 1, "version": first["result"].version}
