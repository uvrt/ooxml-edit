"""Refs at the committed version, and refs by caller (round 47).

``Session.read_refs`` and ``Session.read_resolve_ref`` read a document's refs at its committed
version, as ``Session.read_lock`` reads the document: an edit in flight, which holds the
document's lock, does not hold them up.  A call that only names something (its version as it
was) is committed too, so refs read at the committed version are never behind.

A ref is the caller's that defined it: another caller pointing it elsewhere is refused, naming
the ref, its address and another name to give (never the owner's context); using it works.  A ref a call with no context
defined is shared.  Undo and redo bring back each ref with its owner.  A call on a draft whose
refs were changed before its change is applied is prepared again on what is committed then.
"""

from __future__ import annotations

import dataclasses
import json
import threading
import time

import pytest

from ooxml_edit.tools import Result, Toolbox, ToolError, integer, shared, string, tool

import synthetic
import tools_toys
from test_tools_block_edits import ToyBlocks

#: How long a slow edit holds the document: long enough that waiting for it would show.
STALL = 2
#: What ``toy_ref_after`` made, by its target.
MADE: list[str] = []


@tool("toy_ref_make", "Test only: append an item and name it; stall seconds holding the document.",
      {"doc": string("Document id."), "text": string("The item."), "ref": string("Ref name."),
       "stall": integer("Seconds.", minimum=0, optional=True)},
      kind="docx", mutates=True)
def ref_make(call, doc, text, ref, stall=0):
    call.document.insert(text, "end")
    call.define_ref(ref, f"item:{len(call.document.items())}")
    if stall:
        time.sleep(stall)
    return Result(summary=f"added {text}", created=[f"item:{len(call.document.items())}"])


@tool("toy_ref_name", "Test only: name an address, changing nothing (the name not in a ``ref``).",
      {"doc": string("Document id."), "name": string("Ref name."), "address": string("Address.")},
      kind="docx", mutates=True)
def ref_name(call, doc, name, address):
    call.define_ref(name, address)
    return Result(summary=f"named {address}")


@tool("toy_ref_after", "Test only: append an item saying the target it was given; stall first.",
      {"doc": string("Document id."), "target": string("Where."),
       "ref": string("Ref name.", optional=True),
       "stall": integer("Seconds.", minimum=0, optional=True)},
      kind="docx", mutates=True, refs=("target",))
def ref_after(call, doc, target, ref=None, stall=0):
    if stall:
        time.sleep(stall)
    call.document.insert(f"after {target}", "end")
    if ref:
        call.define_ref(ref, f"item:{len(call.document.items())}")
    MADE.append(target)
    return Result(summary=f"added after {target}")


@tool("toy_ref_refuse", "Test only: refuse the target it was given.",
      {"doc": string("Document id."), "target": string("Where.")},
      kind="docx", mutates=True, refs=("target",))
def ref_refuse(call, doc, target):
    raise ToolError("invalid_arguments", f"{target} is not for this", field="target",
                    valid_options=[target], details={"target": target})


TOOLS = [ref_make, ref_name, ref_after, ref_refuse]


class RefBlocks(ToyBlocks):
    """The toy block edits, admitting ``toy_ref_after`` and ``toy_ref_refuse`` on a draft too;
    ``renames``: what applying a patch says it renamed (ids drawn meanwhile)."""

    renames: dict = {}

    def admits(self, tool, arguments):
        return tool in ("toy_ref_after", "toy_ref_refuse") or super().admits(tool, arguments)

    def apply(self, document, plan):
        super().apply(document, plan)
        return dict(self.renames)


def _read_copy(document):
    return lambda data: tools_toys.ToyDocument(data)


def _box(*, copies: bool = True, blocks: str = "off") -> Toolbox:
    fmt = tools_toys.TEXT
    if copies:
        fmt = dataclasses.replace(fmt, read_copy=_read_copy,
                                  blocks=RefBlocks() if blocks != "off" else None)
    return Toolbox(tools_toys.TOOLS + TOOLS + list(shared.SESSION_TOOLS), formats=[tools_toys.DECK, fmt],
                   groups=tools_toys.GROUPS, workers=0, block_edits=blocks)


def _open(box):
    session = box.session()
    return session, session.open(synthetic.outer_package(), "notes.docx")


def _thread(fn):
    out: dict = {}
    thread = threading.Thread(target=lambda: out.setdefault("result", fn()))
    thread.start()
    return thread, out


def _ok(result):
    assert result.ok, result.to_json()
    return result


# -- R79: refs at the committed version -----------------------------------------------------------

def test_reading_refs_at_the_committed_version_does_not_wait_for_an_edit_in_flight():
    with _box() as box:
        session, doc = _open(box)
        _ok(box.dispatch(session, "toy_ref_make", {"doc": doc, "text": "one", "ref": "one"}))
        thread, edit = _thread(lambda: box.dispatch(
            session, "toy_ref_make", {"doc": doc, "text": "two", "ref": "two", "stall": STALL}))
        time.sleep(0.3)                                      # the edit holds the document
        started = time.perf_counter()
        refs = session.read_refs(doc)
        resolved = session.read_resolve_ref(doc, "$one/x")
        took = time.perf_counter() - started
        with pytest.raises(ToolError) as missing:
            session.read_resolve_ref(doc, "$two")            # not committed yet
        thread.join()
        assert took < STALL - 0.5, f"reading refs waited {took:.1f} s for the edit"
        assert refs == {"one": "item:1"} and resolved == "item:1/x"
        assert missing.value.code == "not_found" and missing.value.valid_options == ["$one"]
        _ok(edit["result"])
        assert session.read_refs(doc) == session.refs(doc) == {"one": "item:1", "two": "item:2"}


def test_a_call_that_only_names_something_is_committed_at_once():
    with _box() as box:
        session, doc = _open(box)
        _ok(box.dispatch(session, "toy_ref_make", {"doc": doc, "text": "one", "ref": "one"}))
        entry = session.entry(doc)
        _ok(box.dispatch(session, "toy_word_read", {"doc": doc}))      # a reader of this version
        reader = entry.reader()
        version = entry.version
        named = _ok(box.dispatch(session, "toy_ref_name", {"doc": doc, "name": "intro", "address": "item:1"}))
        assert named.version == version == entry.committed.version     # nothing else changed
        assert session.read_refs(doc) == {"one": "item:1", "intro": "item:1"}
        assert session.read_resolve_ref(doc, "$intro") == "item:1"
        # The reads after it see it too, on the copy already made of this version.
        again = entry.reader()
        assert again is not reader and again.document is reader.document and again.lock is reader.lock
        assert again.refs == {"one": "item:1", "intro": "item:1"} and reader.refs == {"one": "item:1"}
        used = _ok(box.dispatch(session, "toy_ref_after", {"doc": doc, "target": "$intro"}))
        assert used.summary == "added after item:1"


def test_reading_refs_of_a_document_changed_outside_a_call_reads_the_version_now():
    with _box() as box:
        session, doc = _open(box)
        _ok(box.dispatch(session, "toy_ref_make", {"doc": doc, "text": "one", "ref": "one"}))
        entry = session.entry(doc)
        with entry.lock:                                     # the application's own change
            entry.document.insert("two", "end")
            entry.keep_refs({"two": "item:2"})
        assert session.read_refs(doc) == session.refs(doc) == {"one": "item:1", "two": "item:2"}
        assert session.entry(doc).committed.version == session.entry(doc).version


def test_without_copies_refs_are_read_under_the_lock_as_before():
    with _box(copies=False) as box:
        session, doc = _open(box)
        _ok(box.dispatch(session, "toy_ref_make", {"doc": doc, "text": "one", "ref": "one"}))
        assert session.entry(doc).committed is None
        assert session.read_refs(doc) == {"one": "item:1"}
        assert session.read_resolve_ref(doc, "$one") == "item:1"
        assert session.read_resolve_ref(doc, "item:7") == "item:7"


# -- R80: refs by caller ----------------------------------------------------------------------------

def test_pointing_another_callers_ref_elsewhere_is_refused_with_the_way_out():
    with _box() as box:
        session, doc = _open(box)
        one, two = session.view(context={"agent": "one"}), session.view(context={"agent": "two"})
        _ok(box.dispatch(one, "toy_ref_make", {"doc": doc, "text": "a", "ref": "table"}))
        version = session.entry(doc).version
        refused = box.dispatch(two, "toy_ref_make", {"doc": doc, "text": "b", "ref": "table"})
        assert not refused.ok and refused.error.code == "invalid_arguments"
        assert refused.error.field == "ref" and refused.error.valid_options == ["table_2"]
        assert "another caller" in refused.error.message and "'table_2'" in refused.error.message
        assert "agent" not in json.dumps(refused.to_json())                 # no caller context
        assert refused.error.details == {"ref": "table", "address": "item:1"}
        assert session.entry(doc).version == version and session.entry(doc).document.items() == ["a"]
        # The way out works; using the other caller's ref works.
        _ok(box.dispatch(two, "toy_ref_make", {"doc": doc, "text": "b", "ref": "table_2"}))
        used = _ok(box.dispatch(two, "toy_ref_after", {"doc": doc, "target": "$table"}))
        assert used.summary == "added after item:1"
        # Naming the address it has is no change of it; its owner points it elsewhere.
        _ok(box.dispatch(two, "toy_ref_name", {"doc": doc, "name": "table", "address": "item:1"}))
        assert session.entry(doc).ref_owner("table") == {"agent": "one"}
        _ok(box.dispatch(one, "toy_ref_name", {"doc": doc, "name": "table", "address": "item:3"}))
        assert session.refs(doc)["table"] == "item:3"


def test_a_name_another_caller_has_is_refused_before_anything_is_made():
    with _box() as box:
        session, doc = _open(box)
        one, two = session.view(context={"agent": "one"}), session.view(context={"agent": "two"})
        _ok(box.dispatch(one, "toy_ref_after", {"doc": doc, "target": "item:0", "ref": "spot"}))
        MADE.clear()
        for name in ("spot", "Spot"):                       # folded as the handler would
            refused = box.dispatch(two, "toy_ref_after", {"doc": doc, "target": "item:0", "ref": name})
            assert not refused.ok and refused.error.valid_options == ["spot_2"]
        assert MADE == [] and session.entry(doc).document.items() == ["after item:0"]
        _ok(box.dispatch(one, "toy_ref_after", {"doc": doc, "target": "item:0", "ref": "spot"}))
        assert MADE == ["item:0"] and session.refs(doc) == {"spot": "item:2"}


def test_the_suggested_name_is_free_and_a_ref_name():
    with _box() as box:
        session, doc = _open(box)
        one, two = session.view(context={"agent": "one"}), session.view(context={"agent": "two"})
        long = "a" * 32
        for name in ("x", "x_2", long):
            _ok(box.dispatch(one, "toy_ref_name", {"doc": doc, "name": name, "address": "item:1"}))
        refused = box.dispatch(two, "toy_ref_name", {"doc": doc, "name": "x", "address": "item:2"})
        assert refused.error.valid_options == ["x_3"]
        refused = box.dispatch(two, "toy_ref_name", {"doc": doc, "name": long, "address": "item:2"})
        assert refused.error.valid_options == ["a" * 28 + "_2"]


def test_a_ref_with_no_context_is_shared_and_a_call_with_none_points_any():
    with _box() as box:
        session, doc = _open(box)
        one, two = session.view(context={"agent": "one"}), session.view(context={"agent": "two"})
        _ok(box.dispatch(session, "toy_ref_name", {"doc": doc, "name": "intro", "address": "item:1"}))
        assert session.entry(doc).ref_owner("intro") is None
        _ok(box.dispatch(one, "toy_ref_name", {"doc": doc, "name": "intro", "address": "item:2"}))
        assert session.entry(doc).ref_owner("intro") == {"agent": "one"}      # now one's
        assert not box.dispatch(two, "toy_ref_name", {"doc": doc, "name": "intro", "address": "item:3"}).ok
        _ok(box.dispatch(session, "toy_ref_name", {"doc": doc, "name": "intro", "address": "item:4"}))
        assert session.entry(doc).ref_owner("intro") is None                  # shared again
        _ok(box.dispatch(two, "toy_ref_name", {"doc": doc, "name": "intro", "address": "item:5"}))


def test_a_caller_points_its_own_refs_again_within_one_batch():
    with _box() as box:
        session, doc = _open(box)
        one = session.view(context={"agent": "one"})
        two = session.view(context={"agent": "two"})
        _ok(box.dispatch(two, "toy_ref_name", {"doc": doc, "name": "other", "address": "item:9"}))
        result = box.dispatch(one, "batch", {"ops": [
            {"tool": "toy_ref_make", "arguments": {"doc": doc, "text": "a", "ref": "mine"}},
            {"tool": "toy_ref_make", "arguments": {"doc": doc, "text": "b", "ref": "mine"}},
            {"tool": "toy_ref_name", "arguments": {"doc": doc, "name": "other", "address": "item:1"}}]})
        assert result.ok and result.data["failed"] == [2], result.to_json()
        assert result.data["ops"][2]["error"]["valid_options"] == ["other_2"]
        assert session.refs(doc)["mine"] == "item:2" and session.refs(doc)["other"] == "item:9"


def test_undo_and_redo_bring_back_each_ref_with_its_owner():
    with _box() as box:
        session, doc = _open(box)
        one, two = session.view(context={"agent": "one"}), session.view(context={"agent": "two"})
        _ok(box.dispatch(session, "toy_ref_make", {"doc": doc, "text": "a", "ref": "x"}))      # shared
        _ok(box.dispatch(one, "toy_ref_make", {"doc": doc, "text": "b", "ref": "x"}))          # one's
        assert not box.dispatch(two, "toy_ref_make", {"doc": doc, "text": "c", "ref": "x"}).ok
        _ok(box.dispatch(session, "undo", {"doc": doc}))
        assert session.refs(doc) == {"x": "item:1"} and session.entry(doc).ref_owner("x") is None
        _ok(box.dispatch(session, "undo", {"doc": doc, "redo": True}))
        assert session.refs(doc) == {"x": "item:2"} and session.entry(doc).ref_owner("x") == {"agent": "one"}
        assert session.read_refs(doc) == {"x": "item:2"}
        assert not box.dispatch(two, "toy_ref_name", {"doc": doc, "name": "x", "address": "item:1"}).ok


# -- drafts ----------------------------------------------------------------------------------------

def test_a_ref_pointed_elsewhere_before_a_drafts_change_is_applied_sends_it_there():
    with _box(blocks="always") as box:
        session, doc = _open(box)
        one, two = session.view(context={"agent": "one"}), session.view(context={"agent": "two"})
        _ok(box.dispatch(one, "toy_ref_name", {"doc": doc, "name": "spot", "address": "item:1"}))
        thread, slow = _thread(lambda: box.dispatch(
            two, "toy_ref_after", {"doc": doc, "target": "$spot", "stall": STALL}))
        time.sleep(0.3)                          # its draft resolved $spot to item:1
        _ok(box.dispatch(one, "toy_ref_name", {"doc": doc, "name": "spot", "address": "item:7"}))
        thread.join()
        result = _ok(slow["result"])
        assert result.block["conflicts"] == ["the document's refs changed"] and result.block["tries"] == 2
        assert result.summary == "added after item:7"
        assert session.entry(doc).document.items()[-1] == "after item:7"


def test_a_ref_another_caller_took_before_a_drafts_change_is_applied_is_refused():
    with _box(blocks="always") as box:
        session, doc = _open(box)
        one, two = session.view(context={"agent": "one"}), session.view(context={"agent": "two"})
        version = session.entry(doc).version
        thread, slow = _thread(lambda: box.dispatch(
            two, "toy_ref_after", {"doc": doc, "target": "item:1", "ref": "spot", "stall": STALL}))
        time.sleep(0.3)                          # its draft names spot, which nobody has yet
        _ok(box.dispatch(one, "toy_ref_name", {"doc": doc, "name": "spot", "address": "item:9"}))
        thread.join()
        result = slow["result"]
        assert not result.ok and result.error.field == "ref" and "another caller" in result.error.message
        assert session.entry(doc).version == version and session.refs(doc) == {"spot": "item:9"}


def test_a_refusal_for_another_callers_ref_is_said_of_the_refs_the_change_is_applied_to():
    with _box(blocks="always") as box:
        session, doc = _open(box)
        one, two = session.view(context={"agent": "one"}), session.view(context={"agent": "two"})
        _ok(box.dispatch(one, "toy_ref_name", {"doc": doc, "name": "spot", "address": "item:5"}))
        thread, slow = _thread(lambda: box.dispatch(two, "batch", {"ops": [
            {"tool": "toy_ref_after", "arguments": {"doc": doc, "target": "item:0", "ref": "spot"}},
            {"tool": "toy_ref_after", "arguments": {"doc": doc, "target": "item:0", "stall": STALL}}]}))
        time.sleep(0.3)                          # its draft: op 0 refused, spot naming item:5
        _ok(box.dispatch(one, "toy_ref_name", {"doc": doc, "name": "spot", "address": "item:9"}))
        thread.join()
        result = _ok(slow["result"])
        assert result.block["conflicts"] == ["the document's refs changed"]
        assert result.data["failed"] == [0]
        assert result.data["ops"][0]["error"]["details"]["address"] == "item:9"


def test_a_failed_ops_error_names_the_ids_its_batch_made_as_applied(monkeypatch):
    monkeypatch.setattr(RefBlocks, "renames", {"item:1": "item:71"})
    with _box(blocks="always") as box:
        session, doc = _open(box)
        one = session.view(context={"agent": "one"})
        result = _ok(box.dispatch(one, "batch", {"ops": [
            {"tool": "toy_ref_after", "arguments": {"doc": doc, "target": "item:0", "ref": "made"}},
            {"tool": "toy_ref_refuse", "arguments": {"doc": doc, "target": "$made"}}]}))
        assert result.block is not None and result.data["failed"] == [1]
        assert result.refs == {"made": "item:71"}
        assert "item:71 is not for this" in result.summary and "item:1 " not in result.summary
        assert result.data["ops"][1]["error"]["message"] == "item:71 is not for this"
