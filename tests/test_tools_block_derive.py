"""A batch that derives a dataset from another document, then writes from it, on a draft
(round 51, R105).

The toy text format of ``test_tools_block_edits`` drafts ``toy_block_quote`` (an item whose text
is a source's first value); the grid of ``test_tools_block_sources`` is the other document,
``toy_grid_set`` its change.  ``toy_grid_derive`` only reads the grid and keeps what it found as a
dataset (``d:<id>``), as a workbook's derive does.  A batch deriving from the grid and quoting the
dataset runs on a draft of the text: the grid read at its committed version, op by op; the quote
applied, and the dataset kept, under both documents' locks only while the grid is at the version
read -- else prepared again.  Its answer, its change and the dataset are those of the same batch
under both locks.  Every value is made up.
"""

from __future__ import annotations

import threading

from ooxml_edit.tools import Dataset, Result, integer, string, tool

import test_tools_block_sources as sources
from test_tools_block_edits import _shape
from test_tools_block_sources import HELD, _box, _open

#: ``dataset id -> the grid's version it was derived at`` (the last preparation's).
DERIVED: dict[str, int] = {}
#: Called inside a derive, when set: what a test does while a batch is between its ops.
PAUSE: list = []


@tool("toy_grid_derive", "Test only: keep the stock rows with at least min items as a dataset.",
      {"doc": string("Document id."), "id": string("Dataset id."),
       "min": integer("The least count kept.", optional=True)},
      kind="xlsx")
def grid_derive(call, doc, id, min=0):
    header, rows = call.document.tables["stock"]
    kept = [tuple(row) for row in rows if row[1] >= min]
    DERIVED[id] = call.entry.version
    name = call.define_dataset(id, Dataset(header, kept, keys=1))
    return Result(summary=f"derived {name}: {len(kept)} rows")


@tool("toy_pause", "Test only: a read of no document, calling the test's hooks.", {})
def pause(call):
    for hook in PAUSE:
        hook("pause")
    return Result(summary="paused")


def _derive_box(mode: str = "always"):
    box, hook = _box(mode)
    box.tools[grid_derive.name] = grid_derive
    box.tools[pause.name] = pause
    return box, hook


def _batch(doc: str, id: str, *, min: int = 0, atomic: bool = False, more: list | None = None) -> dict:
    ops = [{"tool": "toy_grid_derive", "arguments": {"doc": "d2", "id": id, "min": min}},
           {"tool": "toy_block_quote", "arguments": {"doc": doc, "cells": [{"from": f"d:{id}"}]}},
           *(more or [])]
    return {"ops": ops, **({"atomic": True} if atomic else {})}


def _said(result) -> dict:
    out = result.to_json()
    for op in (out.get("data") or {}).get("ops") or []:
        (op.get("data") or {}).pop("timing", None)
    return out


def _datasets(session) -> dict:
    return {id_: (entry.doc, entry.version, entry.stale, list(entry.dataset.rows))
            for id_, entry in session.datasets.items()}


def test_a_batch_deriving_from_another_document_then_writing_runs_on_a_draft():
    box, hook = _derive_box()
    with box:
        session, doc = _open(box, hook)
        result = box.dispatch(session, "batch", _batch(doc, "low"))
        assert result.ok and _shape(result.block) == {"tries": 1, "conflicts": []}, result.to_json()
        assert session.entry(doc).document.items()[-1] == "12"
        kept = session.datasets["low"]
        assert (kept.doc, kept.version, kept.stale) == ("d2", session.entry("d2").version, False)
        assert result.doc == "d2" and result.datasets == ["d:low"]      # as under both locks
        assert box.dispatch(session, "toy_grid_set", {"doc": "d2", "count": 9}).ok
        assert session.datasets["low"].stale                            # follows the grid itself


def test_a_derive_and_write_batch_answers_and_changes_as_under_both_locks():
    """The same calls with drafts off and always: the answers, the document, the datasets."""
    out = {}
    for mode in ("off", "always"):
        box, hook = _derive_box(mode)
        with box:
            session, doc = _open(box, hook)
            results = [box.dispatch(session, "batch", _batch(doc, "low")),
                       box.dispatch(session, "toy_grid_set", {"doc": "d2", "count": 30}),
                       box.dispatch(session, "batch", _batch(doc, "high", min=10, atomic=True)),
                       # the quote fails (a dataset with no rows): it changes nothing, its
                       # dataset is kept all the same
                       box.dispatch(session, "batch", _batch(doc, "none", min=99)),
                       box.dispatch(session, "batch", _batch(doc, "low", more=[
                           {"tool": "toy_block_quote", "arguments": {"doc": doc, "cells": [{"from": "d:high"}]}}]))]
            out[mode] = ([_said(r) for r in results], session.entry(doc).document.items(),
                         session.entry(doc).version, _datasets(session))
            if mode == "always":
                blocks = [r.block for r in results]
    assert out["off"] == out["always"]
    assert blocks[1] is None                                    # the grid's own change: no draft
    assert all(_shape(block) == {"tries": 1, "conflicts": []} for k, block in enumerate(blocks) if k != 1), blocks
    assert out["always"][1][-3:] == ["30", "30", "30"]
    assert "none" in out["always"][3]


def test_the_grid_changed_while_the_batch_is_prepared_makes_it_prepare_again(monkeypatch):
    """The grid changes after the batch derived from it, before its change is applied: the
    quote is not applied with the value read, nor the dataset kept; prepared again, both are
    of the grid now."""
    box, hook = _derive_box()
    ready, go = threading.Event(), threading.Event()
    original = sources.QuoteBlocks.prepared

    def prepared(self, draft, result, arguments):
        patch = original(self, draft, result, arguments)
        if not ready.is_set():
            ready.set()
            assert go.wait(60)
        return patch

    monkeypatch.setattr(sources.QuoteBlocks, "prepared", prepared)
    with box:
        session, doc = _open(box, hook)
        out: dict = {}
        thread = threading.Thread(target=lambda: out.setdefault("r", box.dispatch(
            session, "batch", _batch(doc, "low"))))
        thread.start()
        assert ready.wait(60)
        assert box.dispatch(session, "toy_grid_set", {"doc": "d2", "count": 40}).ok
        go.set()
        thread.join()
        result = out["r"]
        assert result.ok and _shape(result.block) == {"tries": 2, "conflicts": ["d2 changed"]}, result.block
        assert session.entry(doc).document.items()[-1] == "40"
        assert session.datasets["low"].dataset.rows[0][1] == 40 and not session.datasets["low"].stale
        assert result.version == session.entry("d2").version


def test_a_grid_changed_between_two_of_its_reads_makes_the_batch_prepare_again():
    """Each op holds the grid's lock only while it runs: a change between two derives of one
    batch (made while a read of no document runs between them) is two states of the grid,
    never one; the batch is prepared again."""
    box, hook = _derive_box()
    with box:
        session, doc = _open(box, hook)
        changed: list = []

        def between(_name):
            if not changed:
                changed.append(1)
                done = threading.Thread(target=lambda: box.dispatch(
                    session, "toy_grid_set", {"doc": "d2", "count": 50}))
                done.start()
                done.join()

        PAUSE.append(between)
        try:
            result = box.dispatch(session, "batch", _batch(doc, "first", more=[
                {"tool": "toy_pause", "arguments": {}},
                {"tool": "toy_grid_derive", "arguments": {"doc": "d2", "id": "second"}},
                {"tool": "toy_block_quote", "arguments": {"doc": doc, "cells": [{"from": "d:second"}]}}]))
        finally:
            PAUSE.clear()
        assert result.ok and _shape(result.block) == {"tries": 2, "conflicts": ["d2 changed"]}, result.block
        assert session.entry(doc).document.items()[-2:] == ["50", "50"]


def test_what_keeps_the_locks():
    """Changes of two documents, a read after the last change of another document, a read of
    the document drafted before the change, and a dataset of the document drafted: under the
    locks, as before."""
    box, hook = _derive_box()
    with box:
        session, doc = _open(box, hook)
        quote = {"tool": "toy_block_quote", "arguments": {"doc": doc, "text": "q"}}
        two = box.dispatch(session, "batch", {"ops": [
            {"tool": "toy_grid_set", "arguments": {"doc": "d2", "count": 3}}, quote]})
        late = box.dispatch(session, "batch", {"ops": [
            quote, {"tool": "toy_grid_derive", "arguments": {"doc": "d2", "id": "late"}}]})
        own = box.dispatch(session, "batch", {"ops": [
            {"tool": "toy_word_read", "arguments": {"doc": doc}}, quote]})
        for result in (two, late, own):
            assert result.ok and result.block is None, result.to_json()
        assert "late" in session.datasets


def test_derive_and_write_batches_from_threads_replay_serially():
    """A short replay: three threads each derive from the grid and quote what they derived,
    four times; a fourth changes the grid.  Every quote is the count of the grid version its
    batch derived at, and applied only at that version (else prepared again): along the
    document, in the order applied, the versions read never go back -- the order of a serial
    run of the same batches and changes."""
    box, hook = _derive_box()
    with box:
        session, doc = _open(box, hook)
        results: dict = {}
        lock = threading.Lock()

        def writer(k: int) -> None:
            for c in range(4):
                id_ = f"t{k}_{c}"
                batch = _batch(doc, id_)
                batch["ops"][1]["arguments"]["text"] = f"{id_}="
                found = box.dispatch(session, "batch", batch)
                with lock:
                    results[id_] = found

        def changer() -> None:
            for c in range(4):
                assert box.dispatch(session, "toy_grid_set", {"doc": "d2", "count": 100 + c}).ok

        threads = [threading.Thread(target=writer, args=(k,)) for k in range(3)]
        threads.append(threading.Thread(target=changer))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert all(result.ok for result in results.values()), [r.to_json() for r in results.values() if not r.ok]
        assert any(result.block is not None and "lock" not in result.block for result in results.values())
        read = []
        for item in session.entry(doc).document.items():
            id_, _, count = item.partition("=")
            if id_ not in results:
                continue
            kept = session.datasets[id_]
            # the dataset, the quote and the batch's answer: of one version of the grid
            assert kept.version == DERIVED[id_] == results[id_].version, (id_, kept.version, DERIVED[id_])
            assert int(count) == kept.dataset.rows[0][1] == HELD[DERIVED[id_]], (item, HELD)
            read.append(DERIVED[id_])
        assert len(read) == 12 and read == sorted(read), read
