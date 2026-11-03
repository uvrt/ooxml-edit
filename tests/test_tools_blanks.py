"""Blank chart points (round 29, C1): a blank source cell is a blank point, values by position
may hold ``null``, ``edit_chart``'s ``blanks`` sets how they draw (``c:dispBlanksAs``), and
the results say where the blanks are without a value.

The toys are test_tools_provenance's: a chart document over the synthetic chart package whose
handler edits real chart parts, and a grid of made-up tables.
"""

from __future__ import annotations

import pytest
from lxml import etree

from ooxml_edit.charts import Chart, ChartDataError
from ooxml_edit.charts import actions
from ooxml_edit.charts.chart import BLANKS as CHART_BLANKS
from ooxml_edit.tools import ToolError, Toolbox, shared
from ooxml_edit.tools.adapters import openai_strict_schema
from ooxml_edit.tools.schema import CallError, canonical, validate_call
from ooxml_edit.tools.sources import blank_text
from ooxml_edit.xml import qn

import charts_synthetic as cs
from test_tools_provenance import BOOK, CLOCK, GRID, PLAIN, TOOLS, ChartBook, Grid, book

#: Made-up quarters, a blank (None) and a cell of spaces in the series columns.
GAPPY = (("quarter", "north", "east"),
         [("Q1", 1, 5), ("Q2", None, 6), ("Q3", 3, "  "), ("Q4", 4, 8)])


@pytest.fixture
def toolbox():
    with Toolbox(TOOLS, formats=[BOOK, PLAIN, GRID], groups=shared.GROUPS, workers=0) as box:
        yield box


@pytest.fixture
def session(toolbox):
    session = toolbox.session(clock=CLOCK)
    session.adopt(ChartBook(), "pptx", "charts.toy")                          # d1
    session.adopt(Grid({"gappy": GAPPY, "texty": (("quarter", "north"),
                                                  [("Q1", 1), ("Q2", "n/a"), ("Q3", 3),
                                                   ("Q4", 4)])}), "xlsx", "made-up.ods")
    return session


def call(toolbox, session, **arguments):
    return toolbox.dispatch(session, "edit_chart", {"doc": "d1", **arguments})


def ok(result):
    assert result.ok, result.to_json()
    return result


def _dispositions(chart: Chart) -> list[str | None]:
    node = chart._root().find(f"{qn('c:chart')}/{qn('c:dispBlanksAs')}")
    return [None if node is None else node.get("val")]


# -- from a source ------------------------------------------------------------------------------


def test_a_blank_cell_is_a_blank_point_and_the_result_names_its_rows(toolbox, session):
    result = ok(call(toolbox, session, target="p:x", action="add", chart_type="line",
                     **{"from": "t:gappy"}))
    chart = book(session).chart("new1")
    assert chart.series[0].values == [1, None, 3, 4]
    assert chart.series[1].values == [5, 6, None, 8]
    assert result.data["blank_points"] == {"north": "1 blank point (row 2)",
                                           "east": "1 blank point (row 3)"}
    assert "n/a" not in str(result.to_json())
    assert chart.blanks == "gap"                       # a new chart's, as Office makes it


def test_text_in_a_series_is_still_refused(toolbox, session):
    result = call(toolbox, session, target="p:x", action="add", chart_type="line",
                  **{"from": "t:texty"})
    assert not result.ok and result.error.code == "invalid_arguments"
    assert "not a number" in result.error.message and "n/a" not in result.error.message


def test_set_values_from_rows_by_position_names_the_series(toolbox, session):
    result = ok(call(toolbox, session, target="bar", action="set_values", series="0",
                     columns=["quarter", "north"], **{"from": "t:gappy"}))
    assert book(session).chart("bar").series[0].values == [1, None, 3, 4]
    assert result.data["blank_points"] == {"North": "1 blank point (row 2)"}


def test_blanks_with_from_is_set_in_the_calls_one_undo_step(toolbox, session):
    before = session.entry("d1").version
    was = _dispositions(book(session).chart("bar"))
    result = ok(call(toolbox, session, target="bar", action="set_values", series="North",
                     columns=["quarter", "north"], blanks="span", **{"from": "t:gappy"}))
    chart = book(session).chart("bar")
    assert chart.blanks == "span" and result.data["blanks"] == "span"
    assert session.entry("d1").version == before + 1
    session.undo("d1")
    chart = book(session).chart("bar")
    assert chart.series[0].values == [10, 12.5, 9, 14] and _dispositions(chart) == was


# -- by position --------------------------------------------------------------------------------


def test_null_values_by_position_are_blank_points(toolbox, session):
    result = ok(call(toolbox, session, target="bar", action="set_values", series="South",
                     values=[7, None, None, 6], blanks="zero"))
    chart = book(session).chart("bar")
    assert chart.series[1].values == [7, None, None, 6]
    assert chart.blanks == "zero" and _dispositions(chart) == ["zero"]
    assert result.data["blanks"] == "zero"


def test_add_takes_null_in_a_series_and_blanks(toolbox, session):
    ok(call(toolbox, session, target="p:x", action="add", chart_type="line",
            categories=["a", "b", "c"], data=[{"name": "s", "values": [1, None, 2]}],
            blanks="span"))
    chart = book(session).chart("new1")
    assert chart.series[0].values == [1, None, 2] and chart.blanks == "span"


@pytest.mark.parametrize("action", ["set_title", "read", "remove_series"])
def test_blanks_is_refused_where_no_values_are_given(toolbox, session, action):
    result = call(toolbox, session, target="bar", action=action, series="0", blanks="gap")
    assert not result.ok and result.error.field == "blanks"
    assert result.error.valid_options == list(shared.BLANKS_ACTIONS)


def test_a_blank_is_null_only_where_the_schema_says_so():
    spec = shared.SPECS["edit_chart"]
    tool_schema = {"type": "object", "properties": {name: param.schema
                                                    for name, param in spec.params.items()},
                   "additionalProperties": False}
    found = validate_call(tool_schema, {"doc": "d1", "target": "c", "action": "set_values",
                                        "values": [1, None]})
    assert found["values"] == [1, None]
    with pytest.raises(CallError, match="must be a string"):
        validate_call(tool_schema, {"doc": "d1", "target": "c", "action": "set_point_labels",
                                    "labels": ["a", None]})


def test_the_provider_schemas_say_null_for_a_blank(toolbox):
    tool = toolbox.tools["edit_chart"]
    assert "orNull" not in str(tool.canonical)                    # validation only
    assert tool.canonical["properties"]["values"]["items"] == {"type": "number"}
    assert "null for a blank" in tool.canonical["properties"]["values"]["description"]
    strict = openai_strict_schema(tool.canonical, tool.schema)
    assert strict["properties"]["values"]["items"]["type"] == ["number", "null"]
    data = strict["properties"]["data"]["items"]["properties"]["values"]["items"]
    assert data["type"] == ["number", "null"]
    assert strict["properties"]["box"]["properties"]["x"]["type"] == "number"
    assert canonical(tool.schema)["properties"]["blanks"]["enum"] == list(shared.BLANKS)


def test_the_tool_layers_blanks_are_the_charts():
    assert shared.BLANKS == CHART_BLANKS
    assert set(shared.BLANKS_ACTIONS) <= set(shared.SPECS["edit_chart"].params["action"]
                                             .schema["enum"])


# -- the chart and its results ------------------------------------------------------------------


def _chart(xml_tail: str = "") -> Chart:
    opened = cs.Opened(cs.package(cs.charts()[:1], diagrams=False))
    chart = opened.chart("bar")
    root = chart._root()
    plot = root.find(qn("c:chart"))
    for node in plot.findall(qn("c:dispBlanksAs")):
        plot.remove(node)
    if xml_tail:
        plot.append(etree.fromstring(xml_tail))
    return chart


@pytest.mark.parametrize("tail, expected", [
    ("", "zero"),                                    # measured: no element draws zero
    (f'<c:dispBlanksAs xmlns:c="{cs.C}"/>', "zero"),  # nor one without val
    (f'<c:dispBlanksAs xmlns:c="{cs.C}" val="gap"/>', "gap"),
    (f'<c:dispBlanksAs xmlns:c="{cs.C}" val="span"/>', "span"),
    (f'<c:dispBlanksAs xmlns:c="{cs.C}" val="other"/>', "zero")])
def test_blanks_reads_what_the_chart_draws(tail, expected):
    assert _chart(tail).blanks == expected


def test_set_blanks_writes_the_element_in_schema_order_once():
    chart = _chart()
    chart.set_blanks("span")
    names = [etree.QName(child).localname for child in chart._root().find(qn("c:chart"))]
    assert names.count("dispBlanksAs") == 1
    if "plotVisOnly" in names:
        assert names.index("dispBlanksAs") == names.index("plotVisOnly") + 1
    chart.set_blanks("gap")
    assert chart.blanks == "gap"
    with pytest.raises(ChartDataError, match="gap, zero or span"):
        chart.set_blanks("none")


def test_chart_json_lists_blank_points_by_category_and_read_always_says_blanks():
    chart = _chart()
    assert "blanks" not in actions.chart_json(chart)
    assert actions.chart_read_json(chart)["blanks"] == "zero"
    chart.series[1].set_values([7, None, None, 6])
    data = actions.chart_json(chart)
    assert data["blanks"] == "zero"
    assert data["blank_points"] == {"South": "2 blank points (categories 1-2)"}
    assert chart.blank_points() == {1: [1, 2]}


def test_blank_text_runs_and_counts():
    assert blank_text([13], "row", "rows", start=1) == "1 blank point (row 14)"
    assert blank_text([3, 4, 8], "row", "rows", start=1) == "3 blank points (rows 4-5, 9)"
    assert blank_text([0, 1, 2], "category", "categories", start=0) == \
        "3 blank points (categories 0-2)"


def test_a_scatter_x_value_is_never_blank():
    with pytest.raises(ToolError, match="x values are numbers") as caught:
        actions.add_plan("scatter", None, [{"name": "s", "values": [1, 2]}], [1, None])
    assert caught.value.field == "values"
