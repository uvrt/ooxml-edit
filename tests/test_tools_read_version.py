"""0.116.0: a read may wait for its format's own work and read the version that work is of.

``DocumentFormat.read_version(toolbox, session, entry, tool, arguments)`` runs before a
reading call takes its locks; ``(committed, at_call)`` makes the call read that committed
version (``Session.read_lock``'s ``pinned``), with a warning when it is newer than the
version at the call; ``None`` -- or a hook that raises -- reads the committed version as
before.  A ``read_batch`` and a changing call never ask.
"""

from __future__ import annotations

import dataclasses
import threading

from ooxml_edit.tools import Result, Toolbox, shared, string, tool

import synthetic
import tools_toys

ASKED: list[tuple] = []


def _read_copy(document):
    def make(data):
        return tools_toys.ToyDocument(data)
    return make


@tool("toy_word_versioned_read", "Test only: read the items and the version read.",
      {"doc": string("Document id.")}, kind="docx")
def versioned_read(call, doc):
    return Result(summary="read", data={"items": call.document.items(), "version": call.entry.version})


def _box(hook):
    fmt = dataclasses.replace(tools_toys.TEXT, read_copy=_read_copy, read_version=hook)
    return Toolbox(tools_toys.TOOLS + [versioned_read, *shared.SESSION_TOOLS],
                   formats=[tools_toys.DECK, fmt], groups=tools_toys.GROUPS, workers=0)


def _open(box):
    session = box.session()
    return session, session.open(synthetic.outer_package(), "notes.docx")


def test_a_read_reads_the_version_the_hook_names_and_says_when_it_is_newer():
    kept: dict = {}

    def hook(toolbox, session, entry, tool, arguments):
        ASKED.append((tool.name, entry.doc_id))
        if tool.name != "toy_word_versioned_read":
            return None
        committed = entry.committed
        if "first" in kept:
            return committed, kept["first"].version     # as if the call came at the earlier version
        kept["first"] = committed
        return committed, committed.version

    with _box(hook) as box:
        session, doc = _open(box)
        box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "first", "at": "end"})
        read = box.dispatch(session, "toy_word_versioned_read", {"doc": doc})
        assert read.ok and read.data["version"] == read.version
        assert not [w for w in read.warnings if "newer than at the call" in w]
        box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "second", "at": "end"})
        again = box.dispatch(session, "toy_word_versioned_read", {"doc": doc})
        assert again.ok and again.version == read.version + 1 and "second" in str(again.data["items"])
        said = [w for w in again.warnings if "newer than at the call" in w]
        assert said == [f"of version {again.version}: 1 commit(s) newer than at the call (version "
                        f"{read.version}), laid out for every check waiting meanwhile"]


def test_a_read_reads_an_older_committed_version_the_hook_hands():
    held: dict = {}

    def hook(toolbox, session, entry, tool, arguments):
        if tool.name != "toy_word_versioned_read":
            return None
        if "old" not in held:
            held["old"] = entry.committed
        return held["old"], held["old"].version

    with _box(hook) as box:
        session, doc = _open(box)
        box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "first", "at": "end"})
        first = box.dispatch(session, "toy_word_versioned_read", {"doc": doc})
        box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "second", "at": "end"})
        pinned = box.dispatch(session, "toy_word_versioned_read", {"doc": doc})
        assert pinned.ok and pinned.version == first.version
        assert "second" not in str(pinned.data["items"]) and pinned.data["version"] == first.version


def test_a_hook_that_answers_none_or_raises_leaves_the_read_as_it_was():
    def none(toolbox, session, entry, tool, arguments):
        return None

    def broken(toolbox, session, entry, tool, arguments):
        raise RuntimeError("the format's work failed")

    for hook in (none, broken):
        with _box(hook) as box:
            session, doc = _open(box)
            box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "first", "at": "end"})
            read = box.dispatch(session, "toy_word_versioned_read", {"doc": doc})
            assert read.ok and read.version == session.entry(doc).version
            assert "first" in str(read.data["items"]) and not read.warnings


def test_changing_calls_and_read_batches_never_ask():
    asked: list[str] = []

    def hook(toolbox, session, entry, tool, arguments):
        asked.append(tool.name)
        return None

    with _box(hook) as box:
        session, doc = _open(box)
        box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "first", "at": "end"})
        box.dispatch(session, "read_batch", {"ops": [{"tool": "toy_word_versioned_read",
                                                        "arguments": {"doc": doc}}]})
        assert asked == []
        box.dispatch(session, "toy_word_versioned_read", {"doc": doc})
        assert asked == ["toy_word_versioned_read"]


def test_reads_waiting_in_the_hook_do_not_hold_an_edit_up():
    gate = threading.Event()
    entered = threading.Event()

    def hook(toolbox, session, entry, tool, arguments):
        if tool.name == "toy_word_versioned_read":
            entered.set()
            gate.wait(5)
        return None

    with _box(hook) as box:
        session, doc = _open(box)
        out: dict = {}
        thread = threading.Thread(target=lambda: out.setdefault(
            "read", box.dispatch(session, "toy_word_versioned_read", {"doc": doc})))
        thread.start()
        assert entered.wait(5)
        edit = box.dispatch(session, "toy_word_insert", {"doc": doc, "text": "meanwhile", "at": "end"})
        assert edit.ok
        gate.set()
        thread.join()
        assert out["read"].ok and "meanwhile" in str(out["read"].data["items"])
