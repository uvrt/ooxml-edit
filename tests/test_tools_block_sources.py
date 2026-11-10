"""A call on a draft that reads another document's table (round 50): ``t:`` sources and
``{{t:...}}`` references.

The toy text format of ``test_tools_block_edits`` admits ``toy_block_quote`` on a draft: it
appends an item whose text may hold references (``Tool.facts``), and a cell list whose
``from`` is a source (``Tool.sources``).  A grid (``t:<table>``) is the other document; its
``toy_grid_set`` changes a value.  The quote is read at the grid's committed version while the
draft is prepared, and applied only while the grid is at that version still: else it is
prepared again, so what it wrote is the grid's value when its change was made, as under both
documents' locks.  Every value is made up.
"""

from __future__ import annotations

import dataclasses
import random
import threading

from ooxml_edit.tools import Dataset, DocumentFormat, Result, Toolbox, ToolError, array, integer, obj, shared, \
    string, tool

import synthetic
import tools_toys
from test_tools_block_edits import ToyBlocks, _read_copy, _shape


class Grid:
    """A made-up workbook: ``tables[name] = (header, rows)``."""

    def __init__(self, tables: dict) -> None:
        self.tables = tables


def _table(grid: Grid, address: str) -> Dataset:
    name, _, key = address[len("t:"):].partition("/")
    if name not in grid.tables:
        raise ToolError("not_found", f"no table {name!r}", valid_options=sorted(grid.tables))
    header, rows = grid.tables[name]
    rows = [tuple(row) for row in rows]
    if key:
        rows = [row for i, row in enumerate(rows) if f"#{i + 1}" == key or row[0] == key]
    return Dataset(header, rows, keys=1)


GRID = DocumentFormat(kind="xlsx", open=lambda data: Grid({}), detect=lambda data, name: False,
                      datasets={"t:": _table})

#: ``grid version -> the count it holds`` (the setter's, under the grid's lock).
HELD: dict[int, int] = {}
#: ``text written -> the grid's version it was read at`` (the last preparation's).
READ: dict[str, int] = {}


@tool("toy_grid_set", "Test only: set a stock count.",
      {"doc": string("Document id."), "count": integer("The count.")}, kind="xlsx", mutates=True)
def grid_set(call, doc, count):
    call.document.tables["stock"][1][0][1] = count
    HELD[call.entry.version + 1] = count            # the version this call makes
    return Result(summary=f"count {count}")


@tool("toy_block_quote", "Test only: append an item, its text by reference, or a source's first value.",
      {"doc": string("Document id."), "text": string("The item.", optional=True),
       "cells": array(obj({"from": string("Source.")}), "Sources.", optional=True)},
      kind="docx", mutates=True, facts=("text",), sources=("cells[].from",))
def block_quote(call, doc, text=None, cells=None):
    grid = call.entries.get("d2")
    if grid is not None and text:
        READ[text] = grid.version                   # the version its references were read at
    for cell in cells or []:
        rows = call.source(cell["from"])
        text = (text or "") + str(rows.dataset.rows[0][1])
    call.document.insert(text, "end")
    return Result(summary=f"added {text}", created=[f"item:{text}"])


class QuoteBlocks(ToyBlocks):
    def admits(self, tool, arguments):
        return tool in ("toy_block_quote", "toy_block_add") or super().admits(tool, arguments)


def _box(mode: str = "always"):
    hook = QuoteBlocks()
    fmt = dataclasses.replace(tools_toys.TEXT, read_copy=_read_copy, blocks=hook)
    box = Toolbox(tools_toys.TOOLS + [block_quote, grid_set, *shared.SESSION_TOOLS],
                  formats=[tools_toys.DECK, fmt, GRID], groups=tools_toys.GROUPS, workers=0, block_edits=mode)
    return box, hook


def _open(box, hook):
    session = box.session()
    doc = session.open(synthetic.outer_package(), "notes.docx")                          # d1
    session.adopt(Grid({"stock": (("item", "count"), [["bolts", 12], ["nuts", 7]])}), "xlsx", "book.xlsx")  # d2
    HELD.clear()
    READ.clear()
    HELD[session.entry("d2").version] = 12
    return session, doc


def test_a_reference_to_another_documents_table_runs_on_a_draft():
    box, hook = _box()
    with box:
        session, doc = _open(box, hook)
        quoted = box.dispatch(session, "toy_block_quote", {"doc": doc, "text": "bolts: {{t:stock/#1/count}}"})
        sourced = box.dispatch(session, "toy_block_quote", {"doc": doc, "cells": [{"from": "t:stock"}]})
        assert quoted.ok and _shape(quoted.block) == {"tries": 1, "conflicts": []}, quoted.to_json()
        assert sourced.ok and _shape(sourced.block) == {"tries": 1, "conflicts": []}, sourced.to_json()
        assert session.entry(doc).document.items()[-2:] == ["bolts: 12", "12"]
        assert quoted.data["facts"][0]["from"] == "t:stock/#1/count"


def test_a_draft_reading_a_table_answers_and_writes_as_the_call_under_both_locks():
    out = {}
    for mode in ("off", "always"):
        box, hook = _box(mode)
        with box:
            session, doc = _open(box, hook)
            results = [box.dispatch(session, "toy_block_quote", {"doc": doc, "text": "{{t:stock/nuts/count}} nuts"}),
                       box.dispatch(session, "toy_grid_set", {"doc": "d2", "count": 30}),
                       box.dispatch(session, "toy_block_quote", {"doc": doc, "cells": [{"from": "t:stock/#1"}]})]
            out[mode] = ([(r.ok, r.summary, r.data) for r in results],
                         session.entry(doc).document.items(), session.entry(doc).version)
    assert out["off"] == out["always"]


def test_a_table_changed_while_the_draft_is_prepared_makes_it_prepare_again(monkeypatch):
    """The grid changes after the draft read it, before its change is applied: the change
    is not applied with the value read; prepared again, it writes the value now."""
    box, hook = _box()
    ready, go = threading.Event(), threading.Event()
    original = QuoteBlocks.prepared
    taken = []

    def prepared(self, draft, result, arguments):
        patch = original(self, draft, result, arguments)
        if not taken:
            taken.append(1)
            ready.set()
            assert go.wait(60)
        return patch

    monkeypatch.setattr(QuoteBlocks, "prepared", prepared)
    with box:
        session, doc = _open(box, hook)
        out: dict = {}
        thread = threading.Thread(target=lambda: out.setdefault("r", box.dispatch(
            session, "toy_block_quote", {"doc": doc, "text": "bolts: {{t:stock/#1/count}}"})))
        thread.start()
        assert ready.wait(60)
        assert box.dispatch(session, "toy_grid_set", {"doc": "d2", "count": 40}).ok
        go.set()
        thread.join()
        result = out["r"]
        assert result.ok and _shape(result.block) == {"tries": 2, "conflicts": ["d2 changed"]}, result.block
        assert session.entry(doc).document.items()[-1] == "bolts: 40"
        assert result.data["facts"][0]["text"] == "40"


def test_a_closed_table_is_refused_when_prepared_again(monkeypatch):
    box, hook = _box()
    ready, go = threading.Event(), threading.Event()
    original = QuoteBlocks.prepared

    def prepared(self, draft, result, arguments):
        patch = original(self, draft, result, arguments)
        if not ready.is_set():
            ready.set()
            assert go.wait(60)
        return patch

    monkeypatch.setattr(QuoteBlocks, "prepared", prepared)
    with box:
        session, doc = _open(box, hook)
        out: dict = {}
        thread = threading.Thread(target=lambda: out.setdefault("r", box.dispatch(
            session, "toy_block_quote", {"doc": doc, "text": "{{t:stock/#1/count}}"})))
        thread.start()
        assert ready.wait(60)
        assert box.dispatch(session, "close_document", {"doc": "d2"}).ok
        go.set()
        thread.join()
        result = out["r"]
        assert not result.ok and result.error.code == "not_found", result.to_json()
        assert "12" not in session.entry(doc).document.items()


def test_quotes_and_table_changes_from_threads_replay_serially(monkeypatch):
    """Quotes and grid changes from several threads at once: each quote's change is applied
    only when the grid is at the version it read (checked under the document's lock, as
    the dispatcher checks it), its text the count of that version; replayed one after
    another in the order applied, with the grid as each read it, they make the same
    document."""
    from ooxml_edit.tools import dispatch

    checked: list[tuple[dict, int, str | None]] = []
    original = dispatch.Toolbox._block_reads_changed

    def reads_changed(self, session, entry, committed, context, clean, made_keys, read=None):
        reason = original(self, session, entry, committed, context, clean, made_keys, read)
        checked.append(({doc: version for doc, (_live, version) in (read or {}).items()},
                        session.entry("d2").version, reason))
        return reason

    monkeypatch.setattr(dispatch.Toolbox, "_block_reads_changed", reads_changed)
    box, hook = _box()
    with box:
        session, doc = _open(box, hook)
        rng = random.Random(50)
        calls = [("toy_block_quote", {"doc": doc, "text": f"q{k}={{{{t:stock/#1/count}}}}"}) for k in range(12)]
        calls += [("toy_grid_set", {"doc": "d2", "count": 100 + k}) for k in range(8)]
        rng.shuffle(calls)
        results: dict = {}
        threads = [threading.Thread(target=lambda k=k, c=c: results.setdefault(k, box.dispatch(session, *c)))
                   for k, c in enumerate(calls)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert all(result.ok for result in results.values()), [r.to_json() for r in results.values() if not r.ok]
        quotes = sorted((results[k] for k, c in enumerate(calls) if c[0] == "toy_block_quote"),
                        key=lambda r: r.version)
        assert any(r.block is not None and "lock" not in r.block for r in quotes)    # drafted
        for read, now, reason in checked:
            assert (reason is None) is (read.get("d2") == now) or (reason is not None and "d2" not in reason)
        written = [r.summary.removeprefix("added ") for r in quotes]
        for text in written:                                   # each the count it read
            assert text.split("=")[1] == str(HELD[READ[text]]), (text, READ, HELD)
        items = session.entry(doc).document.items()
        assert items == written                                # the serial replay, in order applied
