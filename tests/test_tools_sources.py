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

from ooxml_edit.tools import (Dataset, DatasetEntry, DefaultLocale, DocumentFormat, Limits,
                              Result, ToolError, Toolbox, shared, string, strict_problems,
                              tool)
from ooxml_edit.tools.datasets import row_range
from ooxml_edit.tools.sources import first_rows, label, merged, moment_of, runs

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


@pytest.mark.parametrize("source, address, categories, rows, cells", [
    ("d:sales/#2:#3", "d:sales/#2:#3", ["South", "East"], "1-2", ["G!A3:A4"]),
    ("d:sales/#3:#2", "d:sales/#2:#3", ["South", "East"], "1-2", ["G!A3:A4"]),
    ("d:sales/#2:", "d:sales/#2:", ["South", "East"], "1-2", ["G!A3:A4"]),
    ("d:sales/#1", "d:sales/#1", ["North"], "1", ["G!A2"]),
    ("d:sales/#2:#2", "d:sales/#2", ["South"], "1", ["G!A3"]),
])
def test_a_run_of_a_datasets_rows_is_a_source(toolbox, session, source, address, categories,
                                              rows, cells):
    ok(call(toolbox, session, "ds_put", id="sales"))
    placed = ok(add(toolbox, session, **{"from": source, "columns": ["region", "units"]})
                ).data["from"]
    assert SEEN[-1][1]["categories"] == categories
    assert placed["source"] == address and placed["doc"] == "d2"
    assert placed["categories"] == {"column": "region", "rows": rows, "sources": cells}


@pytest.mark.parametrize("source, options", [
    ("d:sales/#2:#4", ["d:sales/#2:#3"]),
    ("d:sales/#4:", ["d:sales/#3"]),
    ("d:sales/#9", ["d:sales/#3"]),
])
def test_a_row_past_a_datasets_last_is_refused_not_cut(toolbox, session, source, options):
    ok(call(toolbox, session, "ds_put", id="sales"))
    error = failed(add(toolbox, session, **{"from": source, "columns": ["region", "units"]}),
                   "invalid_arguments")
    assert "has 3 rows" in error.message and "nothing is cut" in error.message
    assert error.valid_options == options and error.field == "from"
    assert not SEEN


@pytest.mark.parametrize("address, found", [
    ("d:x/#9:#16", ("x", 9, 16)), ("d:x/#16:#9", ("x", 9, 16)), ("d:x/#9:", ("x", 9, None)),
    ("d:x/#9", ("x", 9, 9)), ("d:x", None), ("d:x[a]", None), ("d:x/#0:#2", None),
    ("d:x/#2:3", None), ("d:x/North", None), ("d:x/#2/a", None), ("t:x/#2:#3", None),
])
def test_row_range_reads_a_run_of_rows(address, found):
    assert row_range(address) == found


@pytest.mark.parametrize("address, count, found", [
    ("t:sales", 9, "t:sales/#1:#9"), ("d2/t:sales", 1, "d2/t:sales/#1"),
    ("d:by_region/#10:", 5, "d:by_region/#10:#14"), ("t:sales/#9:#40", 2, "t:sales/#9:#10"),
    ("t:Stock!A1", 3, "t:Stock!A1/#1:#3"), ("t:sales[units]", 3, None),
    ("t:sales/{a,b}", 1, None), ("d:by_region/North", 2, None), ("t:sales", 0, None),
])
def test_first_rows_offers_a_row_range_of_a_source(address, count, found):
    assert first_rows(address, count) == found


def test_a_row_slice_keeps_sources_and_refuses_past_the_end():
    header, rows = SALES
    sources = [[(f"G!{_column_letter(c)}{r + 2}",) for c in range(4)] for r in range(3)]
    dataset = Dataset(header, rows, sources=sources, keys=1, types=("text",) * 4)
    part = dataset.row_slice(2)
    assert part.rows == tuple(tuple(row) for row in rows[1:])
    assert part.sources[0][0] == ("G!A3",) and part.keys == 1 and part.types == dataset.types
    assert dataset.row_slice(1, 1).rows == (tuple(rows[0]),)
    with pytest.raises(ValueError, match="has 3 rows"):
        dataset.row_slice(2, 4)
    with pytest.raises(ValueError):
        dataset.row_slice(3, 2)


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
        {"tool": "edit_chart", "arguments": _set_from("d:3")}], atomic=True), "not_found")
    assert error.message.endswith("no dataset d:3; d:3 is one of the deck's drawings, "
                                  "not a dataset")
    assert error.field == "ops[1].arguments.from"
    assert error.details["error"]["message"] == (
        "no dataset d:3; d:3 is one of the deck's drawings, not a dataset")
    result = ok(call(hinting, session, "batch", ops=[
        {"tool": "edit_chart", "arguments": {"doc": "d1", "target": "1", "action": "add",
                                             "chart_type": "column", "from": "t:sales",
                                             "columns": ["region", "units"]}},
        {"tool": "edit_chart", "arguments": _set_from("d:3")}]))
    assert result.data["failed"] == [1] and result.data["ops"][1]["error"]["message"] == (
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


# -- dates -----------------------------------------------------------------------------------------

DUTCH = DefaultLocale(decimal_separator=",", thousands_separator=".", date_order="dmy",
                      language="nl-BE", list_separator=";")
#: Made-up days: March the first, and a day whose day number is over 12.
DAYS = (dt.date(2026, 3, 1), dt.date(2026, 4, 15))


@pytest.mark.parametrize("locale, first, second", [
    (None, "3/1/2026", "4/15/2026"),             # en-US, the default
    (DUTCH, "1/3/2026", "15/4/2026"),
    (dataclasses.replace(DUTCH, date_order="ymd"), "2026-03-01", "2026-04-15"),
])
def test_a_date_is_written_in_the_locales_day_order(locale, first, second):
    assert [label(day, locale) for day in DAYS] == [first, second]


def test_a_datetime_and_a_time():
    when = dt.datetime(2026, 3, 1, 14, 5)
    assert label(when) == "3/1/2026 14:05" and label(when, DUTCH) == "1/3/2026 14:05"
    assert label(dt.time(9, 5, 30)) == "09:05:30" and label(dt.time(23, 0)) == "23:00"
    aware = dt.datetime(2026, 3, 1, 14, 5, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    assert label(aware) == "3/1/2026 14:05"              # the wall clock, no zone


def test_date_categories_from_a_dataset_in_the_sessions_locale(toolbox):
    session = _session(toolbox, locale=DUTCH)
    session.keep_datasets([DatasetEntry("days", Dataset.from_records(
        [{"day": DAYS[0], "units": 3}, {"day": DAYS[1], "units": 5}]))])
    ok(add(toolbox, session, **{"from": "d:days"}))
    assert SEEN[-1][1]["categories"] == ["1/3/2026", "15/4/2026"]
    _chart(toolbox, session, ["15/4/2026", "1/3/2026"])
    ok(call(toolbox, session, "edit_chart", doc="d1", target="c1", action="set_values",
            series="units", **{"from": "d:days"}))
    assert SEEN[-1][1]["values"] == [5, 3]               # matched by the date's label


def test_a_date_in_a_series_is_refused_as_a_date(toolbox, session):
    session.keep_datasets([DatasetEntry("days", Dataset.from_records(
        [{"region": "North", "day": DAYS[0]}]))])
    error = failed(add(toolbox, session, **{"from": "d:days"}), "invalid_arguments")
    assert "a date, not a number" in error.message


# -- ISO text in a date column (T18b) --------------------------------------------------------------

#: A workbook's date column, as a ``t:`` address or a derive gives it: ISO 8601 text, typed.
ISO_DAYS = Dataset(("day", "units"), (("2026-03-01", 3), ("2026-04-15", 5)),
                   types=("date", "number"))


@pytest.mark.parametrize("locale, first, second", [
    (None, "3/1/2026", "4/15/2026"),
    (DUTCH, "1/3/2026", "15/4/2026"),
    (dataclasses.replace(DUTCH, date_order="ymd"), "2026-03-01", "2026-04-15"),
])
def test_iso_text_in_a_date_column_is_written_as_a_date_is(locale, first, second):
    assert [label(day, locale, type="date") for day in ISO_DAYS.rows[0][:1] +
            ISO_DAYS.rows[1][:1]] == [first, second]
    assert label("2026-03-01", locale) == "2026-03-01"          # unlabelled: text
    assert label("2026-03-01", locale, type="text") == "2026-03-01"


def test_iso_datetime_and_time_text_by_their_types():
    assert label("2026-03-01T14:05:00", type="datetime") == "3/1/2026 14:05"
    assert label("2026-03-01T14:05:30.250", DUTCH, type="datetime") == "1/3/2026 14:05:30"
    assert label("14:05:00", type="time") == "14:05"
    assert label("09:05:30.500", DUTCH, type="time") == "09:05:30"
    # Not ISO 8601, or no such day: as written.
    assert label("PT25H30M0S", type="time") == "PT25H30M0S"
    assert label("2026-02-30", type="date") == "2026-02-30"
    assert label("soon", type="date") == "soon" and label("", type="date") is None


def test_moment_of_reads_iso_and_with_written_the_locales_text():
    assert moment_of("2026-03-01") == dt.date(2026, 3, 1)
    assert moment_of("2026-03-01 14:05") == dt.datetime(2026, 3, 1, 14, 5)
    assert moment_of("1/3/2026") is None                         # not without written
    assert moment_of("1/3/2026", DUTCH, written=True) == dt.date(2026, 3, 1)
    assert moment_of("1/3/2026", written=True) == dt.date(2026, 1, 3)          # en-US
    assert moment_of("1/3/2026 14:05", DUTCH, written=True) == dt.datetime(2026, 3, 1, 14, 5)
    assert moment_of("15/4/2026", written=True) is None          # no month 15
    assert moment_of(3) is None and moment_of("2026-03-01+02:00") is None


@pytest.mark.parametrize("locale, written", [
    (None, ["3/1/2026", "4/15/2026"]), (DUTCH, ["1/3/2026", "15/4/2026"])])
def test_a_date_columns_iso_categories_are_written_in_the_locale(toolbox, locale, written):
    session = _session(toolbox, locale=locale)
    session.keep_datasets([DatasetEntry("iso", ISO_DAYS)])
    ok(add(toolbox, session, **{"from": "d:iso"}))
    assert SEEN[-1][1]["categories"] == written


@pytest.mark.parametrize("locale, categories", [
    (None, ["4/15/2026", "3/1/2026"]),               # written in the locale
    (DUTCH, ["15/4/2026", "1/3/2026"]),
    (None, ["2026-04-15", "2026-03-01"]),            # written ISO, by an older deck
    (DUTCH, ["2026-04-15", "2026-03-01"]),
])
def test_set_values_matches_a_date_column_however_its_categories_were_written(
        toolbox, locale, categories):
    for rows in (ISO_DAYS, Dataset.from_records(
            [{"day": DAYS[0], "units": 3}, {"day": DAYS[1], "units": 5}])):
        session = _session(toolbox, locale=locale)
        session.keep_datasets([DatasetEntry("days", rows)])
        _chart(toolbox, session, categories)
        ok(call(toolbox, session, "edit_chart", doc="d1", target="c1", action="set_values",
                series="units", **{"from": "d:days"}))
        assert SEEN[-1][1]["values"] == [5, 3]


@pytest.mark.parametrize("categories, found", [
    (["2026-04-15", "2026-03-01"], [5, 3]), (["4/15/2026", "3/1/2026"], None)])
def test_iso_categories_of_a_text_column_still_match_as_text(toolbox, categories, found):
    session = _session(toolbox)
    session.keep_datasets([DatasetEntry("codes", Dataset(
        ("code", "units"), (("2026-03-01", 3), ("2026-04-15", 5)), types=("text", None)))])
    _chart(toolbox, session, categories)
    if found:
        ok(call(toolbox, session, "edit_chart", doc="d1", target="c1", action="set_values",
                series="units", **{"from": "d:codes"}))
        assert SEEN[-1][1]["values"] == found
        return
    error = failed(call(toolbox, session, "edit_chart", doc="d1", target="c1",
                        action="set_values", series="units", **{"from": "d:codes"}),
                   "not_found")
    assert "no row of d:codes" in error.message


# -- date_format (round 6, R9) ------------------------------------------------------------------


def _in(language):
    return dataclasses.replace(DUTCH, language=language)


def test_label_writes_a_date_in_a_date_format_and_leaves_the_rest():
    assert label(DAYS[0], date_format="d mmmm yyyy") == "1 March 2026"
    assert label(DAYS[0], _in("de-DE"), date_format="d. mmmm yyyy") == "1. März 2026"
    assert label("2026-04-15", DUTCH, type="date", date_format="dd/mm/yyyy") == "15/04/2026"
    assert label("2026-04-15T09:30:00", type="datetime", date_format="d mmm") == "15 Apr"
    assert label("2026-04-15", date_format="dd/mm/yyyy") == "2026-04-15"   # unlabelled text
    assert label(dt.time(9, 30), date_format="dd/mm/yyyy") == "09:30"      # no date
    assert label(2.5, DUTCH, date_format="dd/mm/yyyy") == "2,5"


@pytest.mark.parametrize("language, code", [
    ("en-US", r"[$-409]d\ mmmm\ yyyy"), ("nl-NL", r"[$-413]d\ mmmm\ yyyy"),
    ("de-DE", r"[$-407]d\ mmmm\ yyyy"), ("it-IT", r"[$-410]d\ mmmm\ yyyy"),
    # A region: its own measured id, else that of the tag whose names it shows.
    ("fr-BE", r"[$-80C]d\ mmmm\ yyyy"), ("en-BE", r"[$-409]d\ mmmm\ yyyy"),
])
def test_add_from_with_a_date_format_passes_dates_in_the_locales_code(toolbox, language, code):
    session = _session(toolbox, locale=_in(language))
    session.keep_datasets([DatasetEntry("days", Dataset.from_records(
        [{"day": DAYS[0], "units": 3}, {"day": DAYS[1], "units": 5}]))])
    ok(add(toolbox, session, date_format="d mmmm yyyy", **{"from": "d:days"}))
    categories = SEEN[-1][1]["categories"]
    assert categories == list(DAYS)
    assert [type(day).__name__ for day in categories] == ["DateCategory"] * 2
    assert {day.format_code for day in categories} == {code}
    assert "date_format" not in SEEN[-1][1]                  # the handler never sees it


def test_a_date_columns_iso_text_takes_a_date_format_too(toolbox, session):
    session.keep_datasets([DatasetEntry("iso", ISO_DAYS)])
    ok(add(toolbox, session, date_format="dd/mm/yyyy", **{"from": "d:iso"}))
    assert SEEN[-1][1]["categories"] == list(DAYS)
    assert SEEN[-1][1]["categories"][0].format_code == "[$-409]dd/mm/yyyy"


@pytest.mark.parametrize("arguments, message", [
    ({"date_format": "d mmmm yyyy"}, "for the dates of add from"),
    ({"date_format": "d mmmm yyyy", "from": "d:days", "action": "set_values",
      "target": "c1", "series": "units"}, "date_format is for add"),
    ({"date_format": "d mmmm yyyy", "from": "d:days", "chart_type": "scatter"},
     "x values are numbers"),
    ({"date_format": "hh:mm", "from": "d:days"}, "'h' is no date token"),
    ({"date_format": "d mmmm yyyy", "from": "d:words"}, "not a date"),
])
def test_a_date_format_is_checked(toolbox, arguments, message):
    session = _session(toolbox)
    session.keep_datasets([
        DatasetEntry("days", Dataset.from_records([{"day": DAYS[0], "units": 3}])),
        DatasetEntry("words", Dataset.from_records([{"k": "a", "units": 3}]))])
    _chart(toolbox, session, ["1/3/2026"])
    arguments = {"doc": "d1", "target": "1", "action": "add", "chart_type": "column",
                 **arguments}
    error = failed(call(toolbox, session, "edit_chart", **arguments), "invalid_arguments")
    assert message in error.message
    assert error.field == "date_format" or message == "not a date"


def test_a_name_token_in_a_locale_without_names_is_refused_numbers_are_not(toolbox):
    session = _session(toolbox, locale=_in("pt-BR"))
    session.keep_datasets([DatasetEntry("days", Dataset.from_records(
        [{"day": DAYS[0], "units": 3}]))])
    error = failed(add(toolbox, session, date_format="d mmmm yyyy", **{"from": "d:days"}),
                   "invalid_arguments")
    assert "no month or day names are measured for pt-BR" in error.message
    ok(add(toolbox, session, date_format="dd/mm/yyyy", **{"from": "d:days"}))
    assert SEEN[-1][1]["categories"][0].format_code == "dd/mm/yyyy"     # no measured id


def test_set_values_matches_a_date_column_to_date_serial_categories(toolbox, session):
    session.keep_datasets([DatasetEntry("days", Dataset.from_records(
        [{"day": DAYS[0], "units": 3}, {"day": DAYS[1], "units": 5}]))])
    _chart(toolbox, session, ["x", "y"])
    # A chart whose date categories are kept as serials: 15 April and 1 March 2026.
    session.documents["d1"].document.charts["c1"]["categories"] = [46127, 46082]
    ok(call(toolbox, session, "edit_chart", doc="d1", target="c1", action="set_values",
            series="units", **{"from": "d:days"}))
    assert SEEN[-1][1]["values"] == [5, 3]
