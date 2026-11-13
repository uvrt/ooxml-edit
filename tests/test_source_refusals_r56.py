"""Round 56 (R120): a source that names no rows is refused with what there is.

* A value naming a thing of the call's own document (``t:own``, the toy deck's table, bare
  or as ``d1/t:own``) is said to be one -- the format's ``own_source`` -- with what a source
  is, the datasets and tables there are as the options, and an example call; with one
  source alone, ``details.call`` is the call that works.
* Every option a source refusal offers resolves (tool design rule 2): ``d1/t:sales`` (a
  document taking no ``t:``) never offers ``d2/t:sales`` when the grid has no such table; a
  bare option is written with its handle when two grids are open.
* A ``not_found`` of a whole table offers the datasets beside the tables.

Made-up toys only: the deck and grid of ``test_tools_sources``.
"""

from __future__ import annotations

import dataclasses
import threading
import time

import pytest

from ooxml_edit.tools import DocumentFormat, ToolError, Toolbox, shared
from ooxml_edit.tools import sources

import synthetic
import test_tools_sources as base
import tools_toys

ASKED: list[str] = []


def _own(document, address):
    ASKED.append(address)
    if address == "t:raise":
        raise RuntimeError("a hook that fails says nothing")
    return "a table" if address.split("/")[0] == "t:own" else None


DECK = dataclasses.replace(tools_toys.DECK, own_source=_own)
def _table(grid, address):
    """The toy grid's tables, its refusal offering them as a workbook's does: ``t:sales``."""
    try:
        return base._table(grid, address)
    except ToolError as error:
        raise ToolError(error.code, error.message, valid_options=[f"t:{name}" for name in error.valid_options])


GRID = dataclasses.replace(base.GRID, datasets={"t:": _table},
                           value_sources=lambda grid: [f"t:{name}" for name in grid.tables])


@pytest.fixture
def toolbox():
    with Toolbox(base.TOOLS, formats=[DECK, tools_toys.TEXT, GRID], groups=shared.GROUPS,
                 workers=0) as box:
        yield box


@pytest.fixture(autouse=True)
def _clear():
    ASKED.clear()
    base.SEEN.clear()


def _session(toolbox, grids=1):
    session = toolbox.session(clock=base.CLOCK)
    session.open(synthetic.outer_package(), "deck.pptx")                          # d1
    for n in range(grids):
        session.adopt(base.Grid({"sales": base.SALES}), "xlsx", f"book{n}.xlsx")  # d2, d3
    return session


def _add(toolbox, session, value):
    return base.add(toolbox, session, **{"from": value, "columns": ["region", "units"]})


def _resolves(toolbox, session, option):
    """Whether ``option`` given as from places rows (the toy chart is added)."""
    result = _add(toolbox, session, option)
    return result.ok


@pytest.mark.parametrize("value", ["t:own", "d1/t:own", "t:own/#2"])
def test_the_calls_own_table_is_said_to_be_one(toolbox, value):
    session = _session(toolbox)
    error = base.failed(_add(toolbox, session, value), "invalid_arguments")
    address = value.removeprefix("d1/")
    assert error.message.startswith(f"{address} is a table of d1, "
                                    "the document this call edits, not rows to take: from takes a "
                                    "dataset (d:<id>) or rows of an open xlsx document (t:<name>)")
    assert "there are tables t:sales" in error.message and 'e.g. from: "t:sales"' in error.message
    assert error.field == "from" and error.valid_options == ["t:sales"]
    assert error.details["names"] == {"doc": "d1", "address": address, "is": error.details["names"]["is"]}
    # One source alone: the call that works.
    meant = error.details["call"]
    assert meant["tool"] == "edit_chart" and meant["arguments"]["from"] == "t:sales"
    assert toolbox.dispatch(session, meant["tool"], meant["arguments"]).ok


def test_datasets_and_tables_are_both_offered(toolbox):
    session = _session(toolbox, grids=2)
    base.ok(base.call(toolbox, session, "ds_put", id="sales"))
    error = base.failed(_add(toolbox, session, "t:own"), "invalid_arguments")
    assert error.valid_options == ["d:sales", "d2/t:sales", "d3/t:sales"]
    assert "there are datasets d:sales and tables d2/t:sales, d3/t:sales" in error.message
    assert "call" not in error.details              # three sources: none is the obvious one
    for option in error.valid_options:
        assert _resolves(toolbox, session, option), option


def test_none_there_says_how_to_get_one(toolbox):
    session = toolbox.session(clock=base.CLOCK)
    session.open(synthetic.outer_package(), "deck.pptx")
    error = base.failed(_add(toolbox, session, "t:own"), "invalid_arguments")
    assert error.message.endswith("there is none: define a dataset or open a document with tables first")
    assert error.valid_options == []


@pytest.mark.parametrize("grids", [1, 2])
@pytest.mark.parametrize("value", ["d1/t:sales", "d1/t:nothing", "d9/t:sales", "d9/t:nothing", "t:nothing",
                                   "Sales!A1:B4", "d2/t:nothing", "t:raise"])
def test_every_option_offered_resolves(toolbox, grids, value):
    session = _session(toolbox, grids)
    base.ok(base.call(toolbox, session, "ds_put", id="sales"))
    result = _add(toolbox, session, value)
    assert not result.ok
    options = result.error.valid_options
    assert options, result.to_json()
    for option in options:
        assert _resolves(toolbox, session, option), (value, option, result.to_json())


def test_a_document_taking_no_t_never_offers_a_table_that_is_not_there(toolbox):
    session = _session(toolbox)
    error = base.failed(_add(toolbox, session, "d1/t:missing"), "invalid_arguments")
    assert error.message.startswith("d1 (pptx) takes no t: addresses")
    assert "d2/t:missing" not in error.valid_options and error.valid_options == ["t:sales"]
    # What is there, a bare address while one grid takes t:.
    error = base.failed(_add(toolbox, session, "d1/t:sales"), "invalid_arguments")
    assert error.valid_options == ["t:sales"]
    two = _session(toolbox, grids=2)
    error = base.failed(_add(toolbox, two, "d1/t:sales"), "invalid_arguments")
    assert error.valid_options == ["d2/t:sales", "d3/t:sales"]


def test_a_missing_table_offers_the_datasets_too(toolbox):
    session = _session(toolbox)
    base.ok(base.call(toolbox, session, "ds_put", id="sales"))
    error = base.failed(_add(toolbox, session, "t:nothing"), "not_found")
    assert error.message == "no table 'nothing'; or a dataset: d:sales"
    assert error.valid_options == ["t:sales", "d:sales"]


def test_a_format_without_the_hook_keeps_its_words(toolbox):
    with Toolbox(base.TOOLS, formats=[*tools_toys.FORMATS, GRID], groups=shared.GROUPS, workers=0) as plain:
        session = _session(plain)
        error = base.failed(_add(plain, session, "t:own"), "not_found")
        assert error.message == "no table 'own'" and error.valid_options == ["t:sales"]


def test_a_failing_hook_is_ignored(toolbox):
    session = _session(toolbox)
    error = base.failed(_add(toolbox, session, "t:raise"), "not_found")
    assert error.message == "no table 'raise'" and "t:raise" in ASKED


def test_a_document_another_call_holds_is_not_waited_for():
    entry = type("Entry", (), {})()
    entry.lock = threading.RLock()
    entry.document = object()
    entry.reader = lambda: None
    held, release = threading.Event(), threading.Event()

    def hold():
        with entry.lock:
            held.set()
            release.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    held.wait(5)
    began = time.monotonic()
    with sources.reading(entry, wait=0.05) as document:
        assert document is None
    assert time.monotonic() - began < 1
    release.set()
    thread.join()
    with sources.reading(entry) as document:
        assert document is entry.document


def test_limit_and_other_codes_stand():
    error = ToolError("limit", "too many")
    assert sources.refusal(error, "t:x", object()) is error


def test_the_hook_is_declared():
    assert DocumentFormat.__dataclass_fields__["own_source"].default is None
