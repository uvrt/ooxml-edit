"""Edits of different blocks of one document at the same time (round 45, lane B; blocks.py).

A toy format admits two tools on a draft: ``toy_block_add`` appends an item (its result names
the item, not its place: appends of different items commute), ``toy_block_first`` inserts one
first and says how many items there are (it reads them all: any change since its draft's
version is a conflict).  The dispatcher runs an admitted call on a draft of the committed
version, then checks and applies its patch under the document's lock; a conflict prepares it
again, at most ``Limits.block_tries`` times, then the call takes the lock for its whole run.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from types import SimpleNamespace

import pytest

from ooxml_edit.tools import Dataset, Result, Toolbox, ToolError, array, integer, number, obj, shared, string, tool
from ooxml_edit.tools import dispatch
from ooxml_edit.tools.blocks import BlockConflict, token_renamer, renamed
from ooxml_edit.tools.limits import Limits
from ooxml_edit.xml import make

import synthetic
import tools_toys

STALL = 2
PROBES: dict[str, threading.Event] = {}


@tool("toy_block_add", "Test only: append an item; stall seconds on the way.",
      {"doc": string("Document id."), "text": string("The item."),
       "stall": integer("Seconds.", minimum=0, optional=True),
       "key": string("Retry key.", optional=True)},
      kind="docx", mutates=True)
def block_add(call, doc, text, stall=0, key=None):
    call.document.insert(text, "end")
    if stall:
        time.sleep(stall)
    return Result(summary=f"added {text}", created=[f"item:{text}"])


@tool("toy_block_first", "Test only: put an item first; say how many there are.",
      {"doc": string("Document id."), "text": string("The item."),
       "stall": integer("Seconds.", minimum=0, optional=True),
       "burn": number("Seconds of CPU.", minimum=0, optional=True)},
      kind="docx", mutates=True)
def block_first(call, doc, text, stall=0, burn=0):
    started = time.thread_time()
    while time.thread_time() - started < burn:
        sum(range(1000))
    if stall:
        time.sleep(stall)
    call.document.insert(text, "start")
    return Result(summary=f"{len(call.document.items())} items", created=[f"item:{text}"])


@tool("toy_block_refuse", "Test only: refuse.", {"doc": string("Document id.")}, kind="docx",
      mutates=True)
def block_refuse(call, doc):
    raise ToolError("refused", f"not on version {call.entry.version}")


@tool("toy_block_dataset", "Test only: append an item and keep a dataset.",
      {"doc": string("Document id."), "text": string("The item.")}, kind="docx", mutates=True)
def block_dataset(call, doc, text):
    call.document.insert(text, "end")
    call.define_dataset("made", Dataset(("a",), [(1,)]), source=None)
    return Result(summary="added")


@tool("toy_block_cells", "Test only: cells with a source each.",
      {"doc": string("Document id."),
       "cells": array(obj({"text": string("Text.", optional=True), "from": string("Source.", optional=True)}),
                      "The cells.")},
      kind="docx", mutates=True, sources=("cells[].from",))
def block_cells(call, doc, cells):
    return Result(summary=f"{len(cells)} cell(s): " + ", ".join(repr(c.get('from')) for c in cells))


@tool("toy_block_track", "Test only: switch tracking (no change to the document).",
      {"doc": string("Document id."), "on": integer("1 on, 0 off.", minimum=0)}, kind="docx", mutates=True)
def block_track(call, doc, on):
    call.entry.tracking = {"on": True, "author": "Writer"} if on else None
    return Result(summary="tracking switched")


class ToyBlocks:
    """The toy format's :class:`~ooxml_edit.tools.blocks.BlockEdits`: items in the main part."""

    def __init__(self) -> None:
        self.released: list = []
        self.drafts = 0

    def admits(self, tool, arguments):
        return tool in ("toy_block_add", "toy_block_first", "toy_block_refuse", "toy_block_dataset")

    def draft(self, document, committed):
        self.drafts += 1
        copy = tools_toys.ToyDocument(committed.data)
        copy.history.continue_from(committed.version, committed.version)
        return SimpleNamespace(document=copy, base=list(copy.items()))

    def prepared(self, draft, result, arguments):
        after, base = draft.document.items(), draft.base
        if result.summary.endswith(" items"):          # toy_block_first: before the others
            return ("start", base, after[:len(after) - len(base)])
        return ("end", base, after[len(base):])

    def check(self, document, patch, changed):
        at, base, new = patch
        if changed is None or changed - {synthetic.MAIN}:
            raise BlockConflict("parts changed")
        if at == "start" and document.items() != base:
            raise BlockConflict("the items changed")
        return patch

    def apply(self, document, plan):
        at, _base, new = plan
        root = document.package.tree(synthetic.MAIN)
        for text in (new if at == "end" else reversed(new)):
            item = make("tst:item", val=text)
            if at == "end":
                root.append(item)
            else:
                root.insert(0, item)
        document.package.mark_dirty(synthetic.MAIN)
        return {}

    def settle(self, document, plan, *, touched, before):
        pass

    def rename(self, value, renames):
        return renamed(value, token_renamer(renames))

    def release(self, draft, *, current=None):
        self.released.append(current)


def _read_copy(document):
    return lambda data: tools_toys.ToyDocument(data)


def _box(mode: str = "always", hook=None, limits=None) -> tuple[Toolbox, ToyBlocks]:
    hook = hook or ToyBlocks()
    fmt = dataclasses.replace(tools_toys.TEXT, read_copy=_read_copy, blocks=hook)
    box = Toolbox(tools_toys.TOOLS + [block_add, block_first, block_refuse, block_dataset, block_cells,
                                      block_track, *shared.SESSION_TOOLS],
                  formats=[tools_toys.DECK, fmt], groups=tools_toys.GROUPS, workers=0, block_edits=mode,
                  limits=limits)
    return box, hook


def _open(box):
    session = box.session()
    return session, session.open(synthetic.outer_package(), "notes.docx")


def _shape(block):
    """``Result.block`` less ``cpu`` (the drafts' own preparation time)."""
    return {key: value for key, value in (block or {}).items() if key != "cpu"}


def _thread(fn):
    out: dict = {}
    thread = threading.Thread(target=lambda: out.setdefault("result", fn()))
    thread.start()
    return thread, out


def test_one_caller_alone_runs_every_call_as_before():
    box, hook = _box("auto")
    with box:
        session, doc = _open(box)
        result = box.dispatch(session, "toy_block_add", {"doc": doc, "text": "one"})
        assert result.ok and result.block is None and hook.drafts == 0
        assert session.entry(doc).document.items()[-1] == "one"


def test_auto_drafts_beside_another_edit_never_beside_reads_alone():
    """``auto``: a draft only while another call edits the document (its change not yet
    made); calls in flight that only read, or are past their change (at their checks), never
    wait for an edit, so a draft would spare nothing."""
    box, hook = _box("auto")
    with box:
        session, doc = _open(box)
        entry = session.entry(doc)
        entry.enter()                               # a read in flight
        try:
            result = box.dispatch(session, "toy_block_add", {"doc": doc, "text": "one"})
        finally:
            entry.leave()
        assert result.ok and result.block is None and hook.drafts == 0
        assert entry.editing == 0
        entry.begin_edit()                          # another edit in flight
        try:
            result = box.dispatch(session, "toy_block_add", {"doc": doc, "text": "two"})
        finally:
            entry.end_edit()
        assert result.ok and _shape(result.block) == {"tries": 1, "conflicts": []} and hook.drafts == 1
        assert entry.editing == 0 and entry.document.items()[-2:] == ["one", "two"]


def test_an_edit_counts_as_editing_until_its_change_is_made_not_through_its_checks(monkeypatch):
    box, _hook = _box("auto")
    with box:
        session, doc = _open(box)
        entry = session.entry(doc)
        seen: list[int] = []
        original = dispatch.After.run

        def run(self, result):
            seen.append(entry.editing)
            return original(self, result)

        monkeypatch.setattr(dispatch.After, "run", run)
        thread, slow = _thread(lambda: box.dispatch(session, "toy_block_add",
                                                    {"doc": doc, "text": "slow", "stall": STALL}))
        time.sleep(0.3)
        assert entry.editing == 1                    # its change in the making
        result = box.dispatch(session, "toy_block_add", {"doc": doc, "text": "beside"})
        thread.join()
        assert slow["result"].ok and slow["result"].block is None
        assert result.ok and result.block is not None    # drafted beside it
        assert entry.editing == 0
        seen.clear()
        assert box.dispatch(session, "toy_block_add", {"doc": doc, "text": "alone"}).ok
        assert seen == [0]                          # at its checks after, no longer editing


def test_a_call_the_format_says_is_worth_a_draft_alone_drafts_alone():
    class Alone(ToyBlocks):
        def alone(self, tool, arguments):
            return arguments.get("text") == "long"

    box, hook = _box("auto", Alone())
    with box:
        session, doc = _open(box)
        short = box.dispatch(session, "toy_block_add", {"doc": doc, "text": "short"})
        long = box.dispatch(session, "batch", {"ops": [
            {"tool": "toy_block_add", "arguments": {"doc": doc, "text": "long"}}]})
        assert short.ok and short.block is None
        assert long.ok and _shape(long.block) == {"tries": 1, "conflicts": []} and hook.drafts == 1


def test_a_call_that_takes_the_lock_waits_for_a_long_draft_in_flight_to_apply():
    """A draft prepared for longer than ``block_redo_cpu`` is not made to run again by a
    call that takes the document's lock: that call waits until the draft is applied, then
    runs on the version it made.  The draft runs once."""
    box, _hook = _box("always", limits=Limits(block_redo_cpu=0.3))
    with box:
        session, doc = _open(box)
        thread, slow = _thread(lambda: box.dispatch(session, "toy_block_first",
                                                    {"doc": doc, "text": "first", "stall": STALL}))
        time.sleep(0.8)
        late = box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "late", "at": "end"})
        thread.join()
        first = slow["result"]
        assert first.ok and _shape(first.block) == {"tries": 1, "conflicts": []}
        assert late.ok and late.block is not None and late.block["waited"] > 0.5
        assert late.version == first.version + 1
        items = session.entry(doc).document.items()
        assert items[0] == "first" and items[-1] == "late"
        assert session.entry(doc).drafts_in_flight == {}


def test_a_draft_not_yet_long_is_not_waited_for():
    box, _hook = _box("always", limits=Limits(block_redo_cpu=30))
    with box:
        session, doc = _open(box)
        thread, slow = _thread(lambda: box.dispatch(session, "toy_block_first",
                                                    {"doc": doc, "text": "first", "stall": STALL}))
        time.sleep(0.5)
        late = box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "late", "at": "end"})
        thread.join()
        assert late.ok and late.block is None
        assert slow["result"].ok and slow["result"].block["tries"] == 2   # prepared again


def test_a_long_draft_that_cannot_apply_does_not_hold_up_a_call_that_takes_the_lock(monkeypatch):
    """A long draft whose change cannot be a patch takes the lock: a call waiting for it
    goes on then (each in turn under the lock), never waiting for its rerun."""
    box, hook = _box("always", limits=Limits(block_redo_cpu=0.3))
    monkeypatch.setattr(hook, "prepared", lambda draft, result, arguments: None)
    with box:
        session, doc = _open(box)
        thread, slow = _thread(lambda: box.dispatch(session, "toy_block_add",
                                                    {"doc": doc, "text": "first", "stall": STALL}))
        time.sleep(0.8)
        late = box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "late", "at": "end"})
        thread.join()
        assert late.ok and slow["result"].ok and slow["result"].block["lock"] == "not a patch"
        assert session.entry(doc).drafts_in_flight == {}


def test_an_admitted_call_runs_on_a_draft_and_its_change_is_one_step_of_its_own():
    box, hook = _box("always")
    with box:
        session, doc = _open(box)
        view = session.view(context={"agent": "a"})
        before = session.entry(doc).version
        result = box.dispatch(view, "toy_block_add", {"doc": doc, "text": "one"})
        assert result.ok and _shape(result.block) == {"tries": 1, "conflicts": []}
        entry = session.entry(doc)
        assert entry.document.items()[-1] == "one" and result.version == entry.version == before + 1
        assert entry.owners[entry.version].tool == "toy_block_add"
        assert entry.owners[entry.version].caller == {"agent": "a"}
        assert result.touched == [{"doc": doc, "part": synthetic.MAIN}]
        assert entry.committed.version == entry.version          # committed before it returned
        assert hook.released == [entry.committed]                 # the draft is that version
        undone = box.dispatch(view, "undo", {"doc": doc})
        assert undone.ok and "one" not in entry.document.items()
        assert entry.log[-2].tool == "toy_block_add"              # the call log is the document's


def test_an_edit_on_a_draft_does_not_hold_up_another_edit():
    box, _hook = _box("always")
    with box:
        session, doc = _open(box)
        thread, slow = _thread(lambda: box.dispatch(session, "toy_block_add",
                                                    {"doc": doc, "text": "slow", "stall": STALL}))
        time.sleep(0.3)
        started = time.perf_counter()
        fast = box.dispatch(session, "toy_block_add", {"doc": doc, "text": "fast"})
        took = time.perf_counter() - started
        thread.join()
        assert fast.ok and took < STALL - 0.5, f"the edit waited {took:.1f} s"
        result = slow["result"]
        assert result.ok and _shape(result.block) == {"tries": 1, "conflicts": []}
        items = session.entry(doc).document.items()
        assert items[-2:] == ["fast", "slow"]                     # the commit order
        assert result.version == fast.version + 1


def test_a_conflict_prepares_the_call_again_on_what_is_committed_then():
    box, hook = _box("always")
    with box:
        session, doc = _open(box)
        thread, slow = _thread(lambda: box.dispatch(session, "toy_block_first",
                                                    {"doc": doc, "text": "first", "stall": STALL}))
        time.sleep(0.3)
        assert box.dispatch(session, "toy_block_add", {"doc": doc, "text": "meanwhile"}).ok
        thread.join()
        result = slow["result"]
        assert result.ok and result.block["tries"] == 2
        assert result.block["conflicts"] == ["the items changed"]
        items = session.entry(doc).document.items()
        assert items[0] == "first" and items[-1] == "meanwhile"
        assert result.summary == f"{len(items)} items"            # read on the version it changed


def test_past_its_tries_a_call_runs_on_the_document_under_its_lock():
    box, _hook = _box("always", limits=Limits(block_tries=1))
    with box:
        session, doc = _open(box)
        thread, slow = _thread(lambda: box.dispatch(session, "toy_block_first",
                                                    {"doc": doc, "text": "first", "stall": STALL}))
        time.sleep(0.3)
        assert box.dispatch(session, "toy_block_add", {"doc": doc, "text": "meanwhile"}).ok
        thread.join()
        result = slow["result"]
        assert result.ok and _shape(result.block) == {"tries": 1, "conflicts": ["the items changed"],
                                              "lock": "conflicts"}
        assert session.entry(doc).document.items()[0] == "first"


def test_a_conflict_after_a_long_preparation_takes_the_lock_at_once():
    """Its preparation past ``Limits.block_redo_cpu`` seconds of its thread's CPU, a conflict
    sends the call to the document's lock instead of preparing it again: once more, not
    ``block_tries`` times.  A short one is prepared again as before."""
    box, _hook = _box("always", limits=Limits(block_redo_cpu=0.3))
    with box:
        session, doc = _open(box)
        thread, slow = _thread(lambda: box.dispatch(session, "toy_block_first",
                                                    {"doc": doc, "text": "first", "burn": 0.6, "stall": STALL}))
        time.sleep(0.3 + 0.6)
        assert box.dispatch(session, "toy_block_add", {"doc": doc, "text": "meanwhile"}).ok
        thread.join()
        result = slow["result"]
        assert result.ok and _shape(result.block) == {"tries": 1, "conflicts": ["the items changed"],
                                                      "lock": "a conflict after a long preparation"}
        assert result.block["cpu"] >= 0.6
        assert session.entry(doc).document.items()[0] == "first"


def test_a_call_the_format_does_not_admit_keeps_the_lock():
    box, hook = _box("always")
    with box:
        session, doc = _open(box)
        result = box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "x", "at": "end"})
        assert result.ok and result.block is None and hook.drafts == 0


def test_a_refusal_on_a_draft_is_of_the_committed_version():
    box, _hook = _box("always")
    with box:
        session, doc = _open(box)
        version = session.entry(doc).version
        result = box.dispatch(session, "toy_block_refuse", {"doc": doc})
        assert not result.ok and result.error.code == "refused"
        assert result.version == version and result.error.message == f"not on version {version}"


def test_datasets_made_on_a_draft_are_never_the_sessions():
    box, _hook = _box("always")
    with box:
        session, doc = _open(box)
        result = box.dispatch(session, "toy_block_dataset", {"doc": doc, "text": "x"})
        assert result.ok and result.block["lock"].startswith("not a patch")
        assert session.dataset_ids() == ["d:made"]
        assert session.entry(doc).document.items().count("x") == 1


def test_a_batchs_reads_after_its_last_change_read_the_version_it_made():
    box, _hook = _box("always")
    with box:
        session, doc = _open(box)
        result = box.dispatch(session, "batch", {"ops": [
            {"tool": "toy_block_add", "arguments": {"doc": doc, "text": "batched"}},
            {"tool": "toy_word_read", "arguments": {"doc": doc}}]})
        assert result.ok and _shape(result.block) == {"tries": 1, "conflicts": []}, result.to_json()
        read = result.data["ops"][1]
        assert "batched" in read["data"] and "version" not in read
        assert result.version == session.entry(doc).version


def test_a_retry_key_is_kept_and_a_repeat_makes_nothing_new():
    box, _hook = _box("always")
    with box:
        session, doc = _open(box)
        first = box.dispatch(session, "toy_block_add", {"doc": doc, "text": "keyed", "key": "k1"})
        again = box.dispatch(session, "toy_block_add", {"doc": doc, "text": "keyed", "key": "k1"})
        assert first.ok and again.ok and again.summary.startswith("already done")
        assert session.entry(doc).document.items().count("keyed") == 1


def test_the_parts_each_change_touched_are_counted():
    box, _hook = _box("off")
    with box:
        session, doc = _open(box)
        entry = session.entry(doc)
        seq = entry.change_seq
        assert entry.parts_changed_since(seq) == frozenset()
        assert box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "x", "at": "end"}).ok
        assert entry.parts_changed_since(seq) == frozenset({synthetic.MAIN})
        later = entry.change_seq
        assert entry.parts_changed_since(later) == frozenset()
        with entry.lock:                                   # the application's own change
            entry.document.insert("by hand", "end")
        assert entry.parts_changed_since(later) is None
        assert box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "y", "at": "end"}).ok
        assert entry.parts_changed_since(later) is None    # it came before a counted one
        assert entry.parts_changed_since(entry.change_seq) == frozenset()


def test_a_cells_from_left_empty_by_a_strict_decoder_is_no_source():
    box, _hook = _box("off")
    with box:
        session, doc = _open(box)
        result = box.dispatch(session, "toy_block_cells", {"doc": doc, "cells": [{"text": "a", "from": ""}]})
        assert result.ok, result.to_json()
        assert result.summary == "1 cell(s): ''"


def test_many_callers_on_drafts_keep_every_change_once():
    box, _hook = _box("auto")
    with box:
        session, doc = _open(box)
        before = session.entry(doc).version

        def caller(k):
            view = session.view(context={"agent": f"c{k}"})
            return [box.dispatch(view, "toy_block_add", {"doc": doc, "text": f"{k}-{n}"}) for n in range(15)]

        results: dict = {}
        threads = [threading.Thread(target=lambda k=k: results.setdefault(k, caller(k))) for k in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        made = [r for found in results.values() for r in found]
        assert all(r.ok for r in made)
        entry = session.entry(doc)
        items = entry.document.items()
        assert sorted(i for i in items if "-" in i) == sorted(f"{k}-{n}" for k in range(6) for n in range(15))
        versions = sorted(r.version for r in made)
        assert versions == list(range(before + 1, before + 1 + len(made)))   # one each, in order
        for r in made:                                          # each its own version's owner
            assert entry.owners[r.version].tool == "toy_block_add"
        assert any(r.block for r in made)                       # some ran beside another call


def test_renamed_replaces_whole_tokens_only():
    rename = token_renamer({"0A1B2C3D": "11112222"})
    assert renamed({"p:0A1B2C3D": ["t:0A1B2C3D/c1,2", "a0A1B2C3D", "0A1B2C3DE", "0A1B2C3D"]}, rename) == \
        {"p:11112222": ["t:11112222/c1,2", "a0A1B2C3D", "0A1B2C3DE", "11112222"]}


def test_block_edits_takes_auto_always_or_off():
    with pytest.raises(Exception):
        Toolbox(tools_toys.TOOLS, formats=tools_toys.FORMATS, block_edits="sometimes")


def test_a_call_that_drew_ids_and_failed_runs_under_the_lock():
    """Round 47: a call that drew from the document's sequences on its draft, then failed or
    changed nothing, runs under the lock -- where, as before, what it drew is spent."""
    class Drawing(ToyBlocks):
        def drew(self, draft):
            return True

    box, hook = _box("always", Drawing())
    with box:
        session, doc = _open(box)
        refused = box.dispatch(session, "toy_block_refuse", {"doc": doc})
        assert not refused.ok and refused.error.code == "refused"
        assert refused.block["lock"] == "drew ids, then failed" and hook.drafts == 1
    box, hook = _box("always")                                  # one that cannot tell: as before
    with box:
        session, doc = _open(box)
        refused = box.dispatch(session, "toy_block_refuse", {"doc": doc})
        assert not refused.ok and "lock" not in (refused.block or {})


def test_tracking_switched_without_a_new_version_is_the_committed_versions():
    """Round 51: switching tracking makes no new version; the committed version a draft is
    made from carries the tracking now, so the next call drafts on its first try (before:
    every try found "tracking was switched", and the call took the lock)."""
    box, hook = _box("always")
    with box:
        session, doc = _open(box)
        assert box.dispatch(session, "toy_block_add", {"doc": doc, "text": "one"}).ok
        for on in (1, 0, 1):
            assert box.dispatch(session, "toy_block_track", {"doc": doc, "on": on}).ok
            result = box.dispatch(session, "toy_block_add", {"doc": doc, "text": f"after {on}"})
            assert result.ok and _shape(result.block) == {"tries": 1, "conflicts": []}, result.block
            assert session.entry(doc).committed.tracking == session.entry(doc).tracking
