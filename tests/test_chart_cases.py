"""S10 (review): the actions both agents' ``edit_chart`` and ``edit_smartart`` handlers are
(``ooxml_edit.charts.actions``), and the table of what they do that each agent replays
through its own tools (``ooxml_edit.charts.cases``).  Here the table runs through the
actions themselves, as a handler calls them, on a made-up package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from lxml import etree

import charts_synthetic as cs
from ooxml_edit.charts import ChartDataError
from ooxml_edit.charts.actions import (DATA_ACTIONS, STYLE_ACTIONS, add_plan, apply_data_action,
                                       apply_smartart_action, apply_style_action, chart_json,
                                       chart_read_json, chart_style_json, finish_add,
                                       legend_position, need, resolve_category, resolve_series,
                                       smartart_json)
from ooxml_edit.charts.cases import CHART, CHART_CASES, SMARTART_CASES, pick, problems
from ooxml_edit.tools import ToolError


@dataclass
class Outcome:
    """What a handler answers, as far as :func:`problems` reads it."""

    ok: bool
    data: Any = None
    error: Any = None


def _answer(run) -> Outcome:
    try:
        return Outcome(True, data=run())
    except ToolError as error:
        return Outcome(False, error=error)
    except (ChartDataError, IndexError) as error:  # what the handlers answer for these
        return Outcome(False, error=ToolError("invalid_arguments", str(error).strip("'\"")))


def _new_chart(**look):
    from ooxml_edit.charts.create import add_chart

    document = cs.Opened(cs.package())
    plan = add_plan(CHART["chart_type"], CHART["categories"], CHART["data"], **look)
    with document.history.batch():
        made = add_chart(document.package, cs.MAIN, CHART["chart_type"], plan["labels"],
                         [dict(entry) for entry in CHART["data"]], legend=plan["legend"],
                         category_order=plan["category_order"])
        frame = etree.SubElement(document.package.tree(cs.MAIN), "{%s}frame" % cs.NS,
                                 name="new")
        frame.append(made.graphic())
        document.package.mark_dirty(cs.MAIN)
        finish_add(document.chart("new"), CHART["chart_type"], plan)
    return document, document.chart("new")


def _edit_chart(chart, action, **arguments) -> dict:
    """What a handler does with a call on an existing chart."""
    if action == "read":
        return chart_read_json(chart, "address")
    if action in STYLE_ACTIONS:
        series, category = arguments.pop("series", None), arguments.pop("category", None)
        chosen = None if series is None else resolve_series(chart, series, action).index
        point = None if category is None else resolve_category(chart, category, action)
        apply_style_action(chart, action, series=chosen, category=point, **arguments)
        return {**chart_json(chart), **chart_style_json(chart)}
    assert action in DATA_ACTIONS
    apply_data_action(chart, action, **arguments)
    return chart_json(chart)


@pytest.mark.parametrize("case", CHART_CASES, ids=[case["name"] for case in CHART_CASES])
def test_a_chart_case_through_the_actions(case):
    document, chart = _new_chart()
    for arguments in case["before"]:
        _edit_chart(chart, **arguments)
    before = document.to_bytes()
    outcome = _answer(lambda: _edit_chart(chart, **case["call"]))
    assert problems(case, outcome, key="address") == []
    if not outcome.ok:
        assert document.to_bytes() == before          # a refusal changes nothing


@pytest.mark.parametrize("case", SMARTART_CASES, ids=[case["name"] for case in SMARTART_CASES])
def test_a_smartart_case_through_the_actions(case):
    document = cs.Opened(cs.package())
    diagram = document.diagram("blocks")

    def run():
        apply_smartart_action(diagram, **case["call"])
        return smartart_json(document.diagram("blocks"))

    assert problems(case, _answer(run)) == []


def test_what_a_missing_argument_is_said_to_be():
    _, chart = _new_chart()
    with pytest.raises(ToolError) as caught:
        need(chart, "set_gap_width", "value", None)
    assert (caught.value.code, caught.value.field) == ("invalid_arguments", "value")
    assert "set_gap_width needs value: the gap as a whole % of a bar's width" in caught.value.message
    with pytest.raises(ToolError, match="add needs chart_type: one of column, stacked_column"):
        need(None, "add", "chart_type", None)
    with pytest.raises(ToolError, match=r"add needs data: each series as \{name, values\}"):
        need(None, "add", "data", None)
    assert need(chart, "set_value", "value", 0) == 0          # given: itself, even 0


def test_a_legend_position():
    assert [legend_position(p) for p in (None, "right", "left", "top", "bottom", "none")] == [
        "r", "r", "l", "t", "b", None]
    with pytest.raises(ToolError, match="inside_end is a label position"):
        legend_position("inside_end")


@pytest.mark.parametrize("arguments, field, words", [
    ({"chart_type": None}, "chart_type", "add needs chart_type"),
    ({"data": None}, "data", "add needs data"),
    ({"categories": None}, "categories", "add needs categories"),
    ({"values": [1, 2, 3]}, "values", "values are a scatter or bubble chart's x values"),
    ({"chart_type": "scatter"}, "values", "a scatter chart takes its x values in values"),
    ({"position": "center"}, "position", "center is a label position"),
])
def test_add_is_checked_before_anything_is_made(arguments, field, words):
    given = {"chart_type": "column", "categories": ["A", "B", "C"],
             "data": [{"name": "S", "values": [1, 2, 3]}], **arguments}
    chart_type, categories, data = given.pop("chart_type"), given.pop("categories"), given.pop("data")
    with pytest.raises(ToolError) as caught:
        add_plan(chart_type, categories, data, given.pop("values", None), **given)
    assert caught.value.field == field and words in caught.value.message


def test_add_names_what_the_format_needs_after_the_type_and_data():
    with pytest.raises(ToolError) as caught:
        add_plan("column", ["A"], [{"name": "S", "values": [1]}],
                 required={"frame": (None, "where it goes")})
    assert (caught.value.field, caught.value.message) == ("frame", "add needs frame: where it goes")
    with pytest.raises(ToolError) as caught:
        add_plan("column", ["A"] * 10_001, [{"name": "S", "values": [1] * 10_001}])
    assert caught.value.code == "limit"


def test_add_plans_the_order_legend_and_scale():
    plan = add_plan("column", ["A"], [{"name": "S", "values": [1]}], axis="category",
                    scale={"reverse": True, "max": 4}, position="none")
    assert (plan["legend"], plan["category_order"], plan["scale"]) == (None, "reversed", {"max": 4})
    plan = add_plan("column", ["A"], [{"name": "S", "values": [1]}])
    assert (plan["legend"], plan["category_order"], plan["scale"], plan["look"]) == (
        "default", "normal", {}, [])


def test_a_new_charts_look_and_scale_through_finish_add():
    _, chart = _new_chart(scale={"max": 9}, data_labels=True)
    assert chart.scales["value"]["maximum"] == 9
    assert all(entry["shown"] for entry in chart.data_labels)


def test_the_number_formats_a_chart_shows():
    _, chart = _new_chart()
    assert chart.number_formats == {"values": "General", "data_labels": None,
                                    "value_axis": "General"}
    chart.set_data_labels(True, number_format="0.0")
    assert chart.number_formats["data_labels"] == "0.0"
    apply_style_action(chart, "set_axis_scale", axis="value", scale={"number_format": "0%"})
    assert chart.number_formats["value_axis"] == "0%"


def test_a_result_names_the_chart_by_the_formats_key():
    _, chart = _new_chart()
    assert chart_json(chart, "id")["id"] == chart.address
    assert "address" not in chart_json(chart, "id")
    read = chart_read_json(chart)
    assert read["workbook"]["series"][0]["values"]["values"] == [1, 2, 3]
    assert pick(read, "series[-1].name") == "South"
