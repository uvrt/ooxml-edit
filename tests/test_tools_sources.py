"""Sources (O3): ``edit_chart`` takes its categories and series from a dataset (``d:<id>``) or
from a table of another open document (``t:<name>``) by column name, and reports where every
placed value came from.

The toys: a chart handler for the toy deck kind that keeps charts in a dict and records the
literal arguments it was given, and a toy grid format whose documents are named tables of
cells, resolving ``t:<name>`` to rows with a source per cell (``G!B2``) and merging runs of
cells in one column into ranges (``G!B2:B4``).
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from ooxml_edit.tools import (Dataset, DefaultLocale, DocumentFormat, Limits, Result, ToolError,
                              Toolbox, shared, string, strict_problems, tool)
from ooxml_edit.tools.sources import label, merged, runs

import synthetic
import tools_toys

CLOCK = lambda: dt.datetime(2026, 10, 13, 12, 0, tzinfo=dt.timezone.utc)  # noqa: E731

#: What the toy chart handler was given, call by call: (action, arguments).
SEEN: list[tuple[str, dict]] = []


def _chart_json(chart: dict) -> dict:
    return {"categories": list(chart["categories"]),
            "series": [{"name": s["name"], "values": list(s["values"])} for s in chart["series"]]}


@shared.handler("edit_chart", kind="pptx")
def toy_chart(call, doc, target, action, **arguments):
    SEEN.append((action, dict(arguments)))
    charts = call.document.__dict__.setdefault("charts", {})
    if action == "add":
        labels = arguments.get("categories") or arguments.get("values")
        charts["c1"] = {"categories": list(labels),
                        "series": [{"name": d["name"], "values": list(d["values"])}
                                   for d in arguments["data"]]}
        return Result(summary="Added c1", created=["c1"], data=_chart_json(charts["c1"]))
    chart = charts[target]
    if action == "read":
        return Result(summary="Read", data=_chart_json(chart))
    if action == "set_values":
        series = next(s for s in chart["series"] if s["name"] == arguments["series"])
        assert len(arguments["values"]) == len(series["values"])
        series["values"] = list(arguments["values"])
    elif action == "add_series":
        chart["series"].append({"name": arguments["text"], "values": list(arguments["values"])})
    return Result(summary=action, changed=[target], data=_chart_json(chart))


# -- the toy grid: tables of cells ---------------------------------------------------------------


class Grid:
    """Named tables, each a header and rows; cell ``B2`` is the second column's first row."""

    def __init__(self, tables: dict) -> None:
        self.tables = tables
        self.reads = 0


def _column_letter(index: int) -> str:
    return chr(ord("A") + index)


def _table(grid: Grid, address: str) -> Dataset:
    name = address[len("t:"):]
    if name not in grid.tables:
        raise ToolError("not_found", f"no table {name!r}", valid_options=sorted(grid.tables))
    grid.reads += 1
    header, rows = grid.tables[name]
    sources = [[(f"G!{_column_letter(c)}{r + 2}",) for c in range(len(header))]
               for r in range(len(rows))]
    return Dataset(header, rows, sources=sources, keys=1)


def _merge(addresses):
    """Runs of cells in one column, rows in a row, as ranges: G!B2, G!B3 -> G!B2:B3."""
    out: list[list] = []
    for address in addresses:
        sheet, cell = address.split("!")
        column, row = cell[0], int(cell[1:])
        last = out[-1] if out else None
        if last and last[0] == sheet and last[1] == column and last[3] == row - 1:
            last[3] = row
        else:
            out.append([sheet, column, row, row])
    return [f"{s}!{c}{a}" if a == b else f"{s}!{c}{a}:{c}{b}" for s, c, a, b in out]


GRID = DocumentFormat(kind="xlsx", open=lambda data: Grid({}), detect=lambda data, name: False,
                      datasets={"t:": _table}, merge_sources=_merge)

SALES = (("region", "units", "note", "year"),
         [("North", 3, "ok", 2024), ("South", 5, "late", 2025), ("East", 2.5, "", 2026)])


@tool("ds_put", "Keep a made-up dataset.",
      {"id": string("Its id."), "plain": string("Without sources.", optional=True)},
      documents=())
def ds_put(call, id, plain=None):
    header, rows = SALES
    sources = None if plain else [[(f"G!{_column_letter(c)}{r + 2}",) for c in range(4)]
                                  for r in range(3)]
    # Derived from the grid d2, so its sources are merged as the grid's format merges them.
    call.define_dataset(id, Dataset(header, rows, sources=sources, keys=1),
                        source=None if plain else "d2")
    return Result(summary=f"Kept d:{id}")


TOOLS = [toy_chart, ds_put, tools_toys.toy_ppt_set_title, *shared.SESSION_TOOLS]


@pytest.fixture(autouse=True)
def _clear():
    SEEN.clear()


@pytest.fixture
def toolbox():
    with Toolbox(TOOLS, formats=[*tools_toys.FORMATS, GRID], groups=shared.GROUPS,
                 workers=0) as box:
        yield box


def _session(toolbox, **options):
    session = toolbox.session(clock=CLOCK, **options)
    session.open(synthetic.outer_package(), "deck.pptx")                 # d1
    session.adopt(Grid({"sales": SALES, "words": (("k", "v"), [("a", "x")])}), "xlsx",
                  "book.xlsx")                                           # d2
    return session


@pytest.fixture
def session(toolbox):
    return _session(toolbox)


def call(toolbox, session, name, **arguments):
    return toolbox.dispatch(session, name, arguments)


def ok(result):
    assert result.ok, result.to_json()
    return result


def failed(result, code):
    assert not result.ok, result.to_json()
    assert result.error.code == code, result.to_json()
    return result.error


def add(toolbox, session, **arguments):
    arguments = {"doc": "d1", "target": "1", "action": "add", "chart_type": "column",
                 **arguments}
    return call(toolbox, session, "edit_chart", **arguments)


# -- the definition ------------------------------------------------------------------------------


def test_edit_chart_declares_from_and_columns():
    chart = shared.definition("edit_chart")
    properties = chart.schema["properties"]
    assert properties["from"]["type"] == "string"
    assert properties["columns"] == {"type": "array", "description": properties["columns"][
        "description"], "items": {"type": "string"}}
    assert "from" not in chart.schema.get("required", [])
    assert tuple(chart.sources) == ("from",) and not chart.datasets
    assert strict_problems([chart]) == []


def test_every_kinds_handler_is_wrapped_once():
    made = shared.SPECS["edit_chart"].make({"pptx": toy_chart.handlers["pptx"]})
    assert made.handlers["pptx"] is not toy_chart.handlers["pptx"]
    assert made.handlers["pptx"].__wrapped__ is toy_chart.handlers["pptx"]


# -- add -----------------------------------------------------------------------------------------


def test_add_from_a_dataset_passes_literal_values_and_reports_their_sources(toolbox, session):
    ok(call(toolbox, session, "ds_put", id="sales"))
    result = ok(add(toolbox, session, **{"from": "d:sales", "columns": ["region", "units"]}))
    action, given = SEEN[-1]
    assert action == "add" and "from" not in given and "columns" not in given
    assert given["categories"] == ["North", "South", "East"]
    assert given["data"] == [{"name": "units", "values": [3, 5, 2.5]}]
    placed = result.data["from"]
    assert placed["source"] == "d:sales" and placed["doc"] == "d2"
    assert placed["categories"] == {"column": "region", "rows": "1-3", "sources": ["G!A2:A4"]}
    assert placed["series"] == [{"name": "units", "column": "units", "rows": "1-3",
                                 "sources": ["G!B2:B4"]}]
    assert result.data["series"] == [{"name": "units", "values": [3, 5, 2.5]}]


def test_add_from_a_table_of_the_one_open_grid(toolbox, session):
    result = ok(add(toolbox, session, **{"from": "t:sales", "columns": ["Region", "units"]}))
    placed = result.data["from"]
    assert placed["source"] == "d2/t:sales" and placed["doc"] == "d2"
    assert placed["version"] == session.entry("d2").version
    assert placed["series"][0]["sources"] == ["G!B2:B4"]
    assert SEEN[-1][1]["categories"] == ["North", "South", "East"]
    log = session.entry("d2").log
    assert log and log[-1].tool == "edit_chart"          # the grid was held for the call


def test_a_qualified_table_names_its_document(toolbox, session):
    session.adopt(Grid({"sales": SALES}), "xlsx", "other.xlsx")         # d3
    error = failed(add(toolbox, session, **{"from": "t:sales", "columns": ["region", "units"]}),
                   "ambiguous")
    assert error.valid_options == ["d2/t:sales", "d3/t:sales"] and error.field == "from"
    ok(add(toolbox, session, **{"from": "d3/t:sales", "columns": ["region", "units"]}))
    assert session.entry("d3").document.reads == 1 and session.entry("d2").document.reads == 0


@pytest.mark.parametrize("source, code", [
    ("t:nothing", "not_found"),           # the format's own refusal
    ("Sales!A1:B4", "invalid_arguments"),  # no format takes it
    ("d9/t:sales", "not_found"),           # no such document
    ("d1/t:sales", "invalid_arguments"),   # a document whose format takes no t:
    ("d:none", "not_found"),               # no such dataset
])
def test_a_source_that_names_nothing_is_refused(toolbox, session, source, code):
    failed(add(toolbox, session, **{"from": source, "columns": ["region", "units"]}), code)
    assert not SEEN


def test_no_open_grid_means_no_table(toolbox):
    session = toolbox.session(clock=CLOCK)
    session.open(synthetic.outer_package(), "deck.pptx")
    error = failed(add(toolbox, session, **{"from": "t:sales", "columns": ["a", "b"]}),
                   "not_found")
    assert "t:" in error.message


def test_part_of_a_dataset_is_not_a_source(toolbox, session):
    ok(call(toolbox, session, "ds_put", id="sales"))
    failed(add(toolbox, session, **{"from": "d:sales[units]", "columns": ["region", "units"]}),
           "invalid_arguments")


def test_a_dataset_without_sources_reports_its_rows(toolbox, session):
    ok(call(toolbox, session, "ds_put", id="plain", plain="yes"))
    placed = ok(add(toolbox, session, **{"from": "d:plain", "columns": ["region", "units"]})
                ).data["from"]
    assert placed["series"] == [{"name": "units", "column": "units", "rows": "1-3"}]


def test_several_series_and_a_scatter_charts_x_column(toolbox, session):
    ok(add(toolbox, session, **{"from": "t:sales", "columns": ["region", "units", "year"]}))
    assert [d["name"] for d in SEEN[-1][1]["data"]] == ["units", "year"]
    result = ok(add(toolbox, session, chart_type="scatter",
                    **{"from": "t:sales", "columns": ["year", "units"]}))
    given = SEEN[-1][1]
    assert given["values"] == [2024, 2025, 2026] and "categories" not in given
    assert result.data["from"]["categories"]["sources"] == ["G!D2:D4"]


def test_number_categories_are_written_in_the_sessions_locale(toolbox):
    nl = DefaultLocale(decimal_separator=",", thousands_separator=".", date_order="dmy",
                       language="nl-NL", list_separator=";")
    session = _session(toolbox, locale=nl)
    ok(add(toolbox, session, **{"from": "t:sales", "columns": ["units", "year"]}))
    assert SEEN[-1][1]["categories"] == ["3", "5", "2,5"]
    ok(add(toolbox, session, **{"from": "t:sales", "columns": ["year", "units"]}))
    assert SEEN[-1][1]["categories"] == ["2024", "2025", "2026"]


# -- refusals ------------------------------------------------------------------------------------


def test_text_in_a_series_is_refused_naming_its_cell(toolbox, session):
    error = failed(add(toolbox, session, **{"from": "t:sales", "columns": ["region", "note"]}),
                   "invalid_arguments")
    assert "G!C2" in error.message and "not a number" in error.message
    assert "ok" not in error.message.split()             # never the cell's text
    assert error.field == "columns[1]" and not SEEN


@pytest.mark.parametrize("value, word", [("4", "text"), (True, "logical"), (None, "blank"),
                                         (float("nan"), "finite")])
def test_nothing_is_coerced_into_a_number(toolbox, value, word):
    session = toolbox.session(clock=CLOCK)
    session.open(synthetic.outer_package(), "deck.pptx")
    session.adopt(Grid({"t": (("k", "v"), [("a", 1), ("b", value)])}), "xlsx", "b.xlsx")
    error = failed(add(toolbox, session, **{"from": "t:t", "columns": ["k", "v"]}),
                   "invalid_arguments")
    assert "G!B3" in error.message and word in error.message


def test_a_blank_category_is_refused(toolbox):
    session = toolbox.session(clock=CLOCK)
    session.open(synthetic.outer_package(), "deck.pptx")
    session.adopt(Grid({"t": (("k", "v"), [("a", 1), (None, 2)])}), "xlsx", "b.xlsx")
    error = failed(add(toolbox, session, **{"from": "t:t", "columns": ["k", "v"]}),
                   "invalid_arguments")
    assert "G!A3" in error.message and error.field == "columns[0]"


@pytest.mark.parametrize("arguments, field", [
    ({"from": "t:sales", "columns": ["region", "units"], "data": [{"name": "x", "values": [1]}]},
     "data"),
    ({"from": "t:sales", "columns": ["region", "units"], "categories": ["a"]}, "categories"),
    ({"from": "t:sales"}, "from"),               # the note column is text
    ({"from": "t:sales", "columns": ["region"]}, "columns"),
    ({"from": "t:sales", "columns": ["region", "units", "units"]}, "columns"),
    ({"from": "t:sales", "columns": ["region", "price"]}, "columns[1]"),
    ({"columns": ["region", "units"], "categories": ["a"], "data": []}, "columns"),
    ({"from": "t:sales", "columns": ["region", "units"], "chart_type": "bubble"}, "from"),
])
def test_from_and_columns_are_checked(toolbox, session, arguments, field):
    result = add(toolbox, session, **arguments)
    assert not result.ok and result.error.field == field, result.to_json()
    assert not SEEN


def test_add_without_columns_takes_every_column_the_first_as_categories(toolbox):
    session = toolbox.session(clock=CLOCK)
    session.open(synthetic.outer_package(), "deck.pptx")
    session.adopt(Grid({"t": (("k", "v", "w"), [("a", 1, 4), ("b", 2, 5)])}), "xlsx", "b.xlsx")
    result = ok(add(toolbox, session, **{"from": "t:t"}))
    assert SEEN[-1] == ("add", {"chart_type": "column", "categories": ["a", "b"],
                                "data": [{"name": "v", "values": [1, 2]},
                                         {"name": "w", "values": [4, 5]}]})
    placed = result.data["from"]
    assert placed["categories"]["column"] == "k"
    assert [s["column"] for s in placed["series"]] == ["v", "w"]


def test_a_text_column_without_columns_is_refused_suggesting_columns(toolbox, session):
    error = failed(add(toolbox, session, **{"from": "t:sales"}), "invalid_arguments")
    assert "G!C2" in error.message and "columns" in error.message
    assert error.field == "from" and not SEEN


def test_from_is_for_three_actions(toolbox, session):
    ok(add(toolbox, session, categories=["a"], data=[{"name": "s", "values": [1]}]))
    error = failed(call(toolbox, session, "edit_chart", doc="d1", target="c1",
                        action="set_title", text="T",
                        **{"from": "t:sales", "columns": ["region", "units"]}),
                   "invalid_arguments")
    assert error.valid_options == list(shared.FROM_ACTIONS)


# -- set_values and add_series: matched to the chart's categories --------------------------------


def _chart(toolbox, session, categories):
    ok(add(toolbox, session, categories=categories,
           data=[{"name": "units", "values": [0] * len(categories)}]))


def test_set_values_matches_rows_to_categories_by_label(toolbox, session):
    _chart(toolbox, session, ["East", "North", "South"])
    result = ok(call(toolbox, session, "edit_chart", doc="d1", target="c1", action="set_values",
                     series="units", **{"from": "t:sales", "columns": ["region", "units"]}))
    assert SEEN[-1] == ("set_values", {"series": "units", "values": [2.5, 3, 5]})
    assert SEEN[-2][0] == "read"
    placed = result.data["from"]
    assert placed["categories"] == {"column": "region", "rows": "3,1-2",
                                    "sources": ["G!A4", "G!A2:A3"]}
    assert placed["series"] == [{"name": "units", "column": "units", "rows": "3,1-2",
                                 "sources": ["G!B4", "G!B2:B3"]}]
    assert result.data["series"][0]["values"] == [2.5, 3, 5]


def test_number_categories_match_by_value(toolbox, session):
    _chart(toolbox, session, ["2026", "2024", "2025"])
    ok(call(toolbox, session, "edit_chart", doc="d1", target="c1", action="set_values",
            series="units", **{"from": "t:sales", "columns": ["year", "units"]}))
    assert SEEN[-1][1]["values"] == [2.5, 3, 5]


def test_add_series_is_named_by_its_column_unless_text_says(toolbox, session):
    _chart(toolbox, session, ["North", "South", "East"])
    result = ok(call(toolbox, session, "edit_chart", doc="d1", target="c1", action="add_series",
                     **{"from": "t:sales", "columns": ["region", "year"]}))
    assert SEEN[-1] == ("add_series", {"text": "year", "values": [2024, 2025, 2026]})
    assert result.data["from"]["series"][0]["name"] == "year"
    ok(call(toolbox, session, "edit_chart", doc="d1", target="c1", action="add_series",
            text="Years", **{"from": "t:sales", "columns": ["region", "year"]}))
    assert SEEN[-1][1]["text"] == "Years"


@pytest.mark.parametrize("categories, code", [
    (["North", "South", "West"], "not_found"),   # a category with no row
    (["North", "South"], "refused"),             # a row with no category
    (["North", "North", "South"], "ambiguous"),  # two categories alike
])
def test_rows_and_categories_must_pair_up(toolbox, session, categories, code):
    _chart(toolbox, session, categories)
    failed(call(toolbox, session, "edit_chart", doc="d1", target="c1", action="set_values",
                series="units", **{"from": "t:sales", "columns": ["region", "units"]}), code)
    assert SEEN[-1][0] == "read"


def test_two_rows_with_one_category_are_ambiguous(toolbox):
    session = toolbox.session(clock=CLOCK)
    session.open(synthetic.outer_package(), "deck.pptx")
    session.adopt(Grid({"t": (("k", "v"), [("a", 1), ("a", 2)])}), "xlsx", "b.xlsx")
    _chart(toolbox, session, ["a"])
    failed(call(toolbox, session, "edit_chart", doc="d1", target="c1", action="set_values",
                series="units", **{"from": "t:t", "columns": ["k", "v"]}), "ambiguous")


def test_set_values_takes_two_columns(toolbox, session):
    _chart(toolbox, session, ["North", "South", "East"])
    failed(call(toolbox, session, "edit_chart", doc="d1", target="c1", action="set_values",
                series="units", **{"from": "t:sales", "columns": ["region", "units", "year"]}),
           "invalid_arguments")


def test_set_values_and_add_series_default_to_a_two_column_source(toolbox, session):
    _chart(toolbox, session, ["b", "a"])
    session.adopt(Grid({"t": (("k", "v"), [("a", 1), ("b", 2)])}), "xlsx", "c.xlsx")   # d3
    result = ok(call(toolbox, session, "edit_chart", doc="d1", target="c1", action="set_values",
                     series="units", **{"from": "d3/t:t"}))
    assert SEEN[-1] == ("set_values", {"series": "units", "values": [2, 1]})
    assert result.data["from"]["categories"]["column"] == "k"
    ok(call(toolbox, session, "edit_chart", doc="d1", target="c1", action="add_series",
            **{"from": "d3/t:t"}))
    assert SEEN[-1] == ("add_series", {"text": "v", "values": [2, 1]})


def test_set_values_without_columns_needs_a_two_column_source(toolbox, session):
    _chart(toolbox, session, ["North", "South", "East"])
    error = failed(call(toolbox, session, "edit_chart", doc="d1", target="c1",
                        action="set_values", series="units", **{"from": "t:sales"}),
                   "invalid_arguments")
    assert error.field == "columns" and "4 columns" in error.message
    assert SEEN[-1][0] == "add"


# -- the session: stale datasets, batches, limits, parallel calls --------------------------------


def test_a_stale_dataset_warns(toolbox, session):
    ok(call(toolbox, session, "ds_put", id="sales"))
    entry = session.dataset("d:sales")
    entry.mark_stale()
    result = ok(add(toolbox, session, **{"from": "d:sales", "columns": ["region", "units"]}))
    assert result.data["from"]["stale"] is True
    assert any("d:sales is stale" in warning for warning in result.warnings)


def test_a_batch_reads_the_grid_without_changing_it(toolbox, session):
    before = session.entry("d2").version
    result = ok(call(toolbox, session, "batch", ops=[
        {"tool": "edit_chart", "arguments": {"doc": "d1", "target": "1", "action": "add",
                                             "chart_type": "column", "from": "t:sales",
                                             "columns": ["region", "units"]}},
        {"tool": "edit_chart", "arguments": {"doc": "d1", "target": "c1",
                                             "action": "add_series", "from": "d2/t:sales",
                                             "columns": ["region", "year"]}}]))
    assert [op["data"]["from"]["source"] for op in result.data["ops"]] == ["d2/t:sales"] * 2
    assert session.entry("d2").version == before


def test_a_resolved_table_is_held_to_the_dataset_limit(toolbox):
    session = toolbox.session(clock=CLOCK, limits=Limits(max_dataset_cells=5))
    session.open(synthetic.outer_package(), "deck.pptx")
    session.adopt(Grid({"sales": SALES}), "xlsx", "book.xlsx")
    failed(add(toolbox, session, **{"from": "t:sales", "columns": ["region", "units"]}), "limit")


def test_parallel_calls_order_by_the_source_document_too(toolbox, session):
    named = toolbox._documents_named(session, "edit_chart", {
        "doc": "d1", "target": "1", "action": "add", "from": "t:sales"})
    assert named == frozenset({"d1", "d2"})
    results = toolbox.dispatch_many(session, [
        ("edit_chart", {"doc": "d1", "target": "1", "action": "add", "chart_type": "column",
                        "from": "t:sales", "columns": ["region", "units"]}),
        ("close_document", {"doc": "d2"})])
    assert results[0].ok and results[1].ok


# -- the format's hint for a source that names nothing -------------------------------------------

#: What the hinting deck format was asked: (document, value).
ASKED: list[tuple] = []


def _deck_hint(document, value):
    ASKED.append((document, value))
    if value.startswith("d:") and value[2:].isdigit():
        return f"{value} is one of the deck's drawings, not a dataset"
    if value == "t:raise":
        raise RuntimeError("a broken hint")
    return "" if value == "t:blank" else None


@pytest.fixture
def hinting():
    ASKED.clear()
    deck = dataclasses.replace(tools_toys.DECK, source_hint=_deck_hint)
    with Toolbox(TOOLS, formats=[deck, tools_toys.TEXT, GRID], groups=shared.GROUPS,
                 workers=0) as box:
        yield box


def _set_from(value):
    return {"doc": "d1", "target": "c1", "action": "set_values", "series": "units",
            "from": value, "columns": ["region", "units"]}


def test_without_a_hint_the_message_is_unchanged(toolbox, session):
    error = failed(call(toolbox, session, "edit_chart", **_set_from("d:3")), "not_found")
    assert error.message == "no dataset d:3" and error.field == "from"
    alone = toolbox.session(clock=CLOCK)
    alone.open(synthetic.outer_package(), "deck.pptx")
    error = failed(call(toolbox, alone, "edit_chart", **_set_from("t:sales")), "not_found")
    assert error.message == "no open document takes t: addresses: open one first"


def test_the_call_documents_format_adds_its_hint_to_a_missing_dataset(hinting):
    session = _session(hinting)
    ok(call(hinting, session, "ds_put", id="sales"))
    error = failed(call(hinting, session, "edit_chart", **_set_from("d:3")), "not_found")
    assert error.message == "no dataset d:3; d:3 is one of the deck's drawings, not a dataset"
    assert error.field == "from" and error.valid_options == ["d:sales"]
    assert ASKED == [(session.entry("d1").document, "d:3")]


def test_the_hint_reaches_a_t_address_no_document_takes(hinting):
    session = hinting.session(clock=CLOCK)
    session.open(synthetic.outer_package(), "deck.pptx")
    error = failed(call(hinting, session, "edit_chart", **_set_from("t:sales")), "not_found")
    assert error.message.startswith("no open document takes t: addresses: open one first")
    assert ASKED == [(session.entry("d1").document, "t:sales")]
    for value in ("t:blank", "t:raise"):    # nothing to say, or a hint that fails: as it was
        error = failed(call(hinting, session, "edit_chart", **_set_from(value)), "not_found")
        assert error.message == "no open document takes t: addresses: open one first"


def test_a_hint_never_changes_what_resolves(hinting):
    session = _session(hinting)
    _chart(hinting, session, ["North", "South", "East"])
    ok(call(hinting, session, "ds_put", id="sales"))
    for value in ("d:sales", "t:sales"):
        ok(call(hinting, session, "edit_chart", **_set_from(value)))
    failed(call(hinting, session, "edit_chart", **_set_from("d2/t:none")), "not_found")
    failed(call(hinting, session, "edit_chart", **_set_from("x:1")), "invalid_arguments")
    assert ASKED == []      # asked only when the dispatcher finds nothing to resolve


def test_the_hint_reaches_a_batch(hinting):
    session = _session(hinting)
    error = failed(call(hinting, session, "batch", ops=[
        {"tool": "edit_chart", "arguments": {"doc": "d1", "target": "1", "action": "add",
                                             "chart_type": "column", "from": "t:sales",
                                             "columns": ["region", "units"]}},
        {"tool": "edit_chart", "arguments": _set_from("d:3")}]), "not_found")
    assert error.message.endswith("no dataset d:3; d:3 is one of the deck's drawings, "
                                  "not a dataset")
    assert error.field == "ops[1].arguments.from"
    assert error.details["error"]["message"] == (
        "no dataset d:3; d:3 is one of the deck's drawings, not a dataset")
    alone = hinting.session(clock=CLOCK)
    alone.open(synthetic.outer_package(), "deck.pptx")
    error = failed(call(hinting, alone, "batch", ops=[
        {"tool": "edit_chart", "arguments": _set_from("t:sales")}]), "not_found")
    assert [value for _, value in ASKED][-1] == "t:sales"


# -- the helpers ---------------------------------------------------------------------------------


def test_runs_merging_and_labels():
    assert runs([0, 1, 2, 4, 6, 7]) == "1-3,5,7-8" and runs([]) == "" and runs([3]) == "4"
    assert merged(["a", "b", "a"]) == ["a", "b"]
    assert merged(["G!A2", "G!A3"], _merge) == ["G!A2:A3"]
    assert label(2024.0) == "2024" and label(0.1 + 0.2) == "0.3" and label("") is None
    assert label(True) == "TRUE" and label(None) is None and label(float("inf")) is None
