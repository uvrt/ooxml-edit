"""A chart's and a diagram's content as data: read, validated, and applied back."""

from __future__ import annotations

import copy
import math

import pytest

import charts_synthetic as cs
from ooxml_edit.charts import (
    ChartModelError,
    apply_chart_model,
    apply_diagram_model,
    canonical_chart,
    canonical_diagram,
    chart_model,
    diagram_model,
)
from xlsx import Book, check_chart_against_workbook

DATA = cs.package()


def opened() -> cs.Opened:
    return cs.Opened(DATA)


def test_the_readers_are_the_charts_and_diagrams_own():
    document = opened()
    chart = document.chart("combo")
    assert chart_model(document.package.tree(chart.part)) == chart.data
    diagram = document.diagram("bullets")
    assert diagram_model(document.package.tree(diagram.part), diagram.layout) == diagram.model
    assert "layout" not in diagram_model(document.package.tree(diagram.part))


@pytest.mark.parametrize("name", [spec.name for spec in cs.charts()])
def test_a_charts_model_is_canonical_as_read(name):
    data = opened().chart(name).data
    assert canonical_chart(copy.deepcopy(data), "x") == data


# ------------------------------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------------------------------


def _chart() -> dict:
    return opened().chart("bar").data


@pytest.mark.parametrize("change, message", [
    (lambda d: d["series"][0]["values"].__setitem__(0, "12"),
     "w series 0: '12' is not a finite number or null"),
    (lambda d: d["series"][0]["values"].__setitem__(0, True),
     "w series 0: True is not a finite number or null"),
    (lambda d: d["series"][0]["values"].__setitem__(0, math.nan),
     "w series 0: nan is not a finite number or null"),
    (lambda d: d["series"][0]["values"].append(1),
     "w: every series needs one value per category (4)"),
    (lambda d: d["series"].append({"name": "x"}),
     'w series 2: a series is {"name", "values": [...]}'),
    (lambda d: d.__setitem__("legend", "yes"), "w: legend is true or false"),
    (lambda d: d.__setitem__("types", ["pie3D", "teapot"]),
     "w: types are chart types such as 'bar' or 'line'"),
    (lambda d: d["axis_titles"].__setitem__("depth", "z"),
     "w: axis_titles has 'category' and 'value' at most"),
    (lambda d: d.pop("format"),
     "w: chart data is {types, title, axis_titles, legend, format, categories, series}"),
    (lambda d: d.__setitem__("categories", "Q1"), "w: categories and series are lists"),
    (lambda d: d.__setitem__("title", 5), "w: expected a string"),
    (lambda d: d["series"][1].__setitem__("name", ["x"]), "w series 1: expected a string"),
], ids=["string", "bool", "nan", "length", "shape", "legend", "type", "axis", "keys",
        "lists", "title", "name"])
def test_chart_data_is_validated(change, message):
    data = _chart()
    change(data)
    with pytest.raises(ChartModelError) as caught:
        canonical_chart(data, "w")
    assert str(caught.value) == message


def test_chart_size_is_bounded():
    with pytest.raises(ChartModelError, match="w: more than 8 chart values"):
        canonical_chart(_chart(), "w", max_points=8)
    assert canonical_chart(_chart(), "w", max_points=10)


@pytest.mark.parametrize("model, message", [
    ({"nodes": [{"lvl": 1, "t": "a"}]}, "w node 0: level 1 follows level -1"),
    ({"nodes": [{"lvl": "a", "t": "a"}]}, "w node 0: 'a' is not an integer"),
    ({"nodes": [{"lvl": True, "t": "a"}]}, "w node 0: expected an integer, got True"),
    ({"nodes": [{"lvl": 65, "t": "a"}]}, "w node 0: 65 is out of range 0..64"),
    ({"nodes": [{"lvl": 0, "t": 5}]}, "w node 0: expected a string"),
    ({"nodes": [{"id": "<script>", "lvl": 0, "t": "a"}]},
     "w node 0: '<script>' is not a fresh model id"),
    ({"nodes": [{"id": "a1", "lvl": 0, "t": "a"}, {"id": "a1", "lvl": 0, "t": "b"}]},
     "w node 1: 'a1' is not a fresh model id"),
    ({"nodes": [{"lvl": 0}]}, 'w node 0: a node is {"id", "lvl", "t"}'),
    ({"nodes": {}}, 'w: a diagram is {"layout", "nodes": [...]}'),
    ({"nodes": [], "extra": 1}, 'w: a diagram is {"layout", "nodes": [...]}'),
])
def test_diagram_nodes_are_validated(model, message):
    with pytest.raises(ChartModelError) as caught:
        canonical_diagram(model, "w")
    assert str(caught.value) == message


def test_diagram_size_is_bounded():
    model = opened().diagram("bullets").model
    assert canonical_diagram(model, "w") == model
    with pytest.raises(ChartModelError, match="w: more than 3 diagram nodes"):
        canonical_diagram(model, "w", max_nodes=3)


# ------------------------------------------------------------------------------------------
# Applying a chart model
# ------------------------------------------------------------------------------------------


def _apply(document: cs.Opened, name: str, change, **options) -> dict:
    chart = document.chart(name)
    wanted = chart.data
    change(wanted)
    apply_chart_model(chart, canonical_chart(wanted, name), name, **options)
    return wanted


def test_a_chart_model_arrives_with_the_workbook():
    document = opened()

    def change(data):
        data["title"] = "Regions"
        data["legend"] = False
        data["axis_titles"]["value"] = "Units"
        data["categories"] = ["Q0"] + data["categories"][1:] + ["Q5"]
        data["series"][0]["name"] = "Noord"
        data["series"][1]["values"] = [1, 2, 3, 4]
        data["series"].append({"name": "East", "values": [5, 6, 7, 8]})
        for series in data["series"]:
            series["values"] = series["values"] + [9]

    wanted = _apply(document, "bar", change)
    chart = document.chart("bar")
    assert chart.data == wanted
    check_chart_against_workbook(document.package.read(chart.part),
                                 document.package.read(chart.workbook_part))
    assert Book(document.package.read(chart.workbook_part)).sheet("Sheet1").value("D1") == "East"


def test_series_and_categories_are_lined_up_by_name_and_label():
    document = opened()

    def change(data):
        data["series"] = data["series"][1:]          # the first series goes
        data["categories"] = data["categories"][:2]  # the last two categories go
        data["series"][0]["values"] = data["series"][0]["values"][:2]
        for series in data["series"][1:]:
            series["values"] = series["values"][:2]

    wanted = _apply(document, "line", change)
    assert document.chart("line").data == wanted


def test_an_unchanged_model_changes_nothing():
    document = opened()
    _apply(document, "combo", lambda data: None)
    assert not document.history.can_undo()


@pytest.mark.parametrize("change, message", [
    (lambda d: d.__setitem__("types", ["line"]),
     "bar: types is read-only (it is ['bar']); edit the chart part instead"),
    (lambda d: d.__setitem__("format", "0.0%"),
     "bar: format is read-only (it is 'General'); edit the chart part instead"),
    (lambda d: d["series"][0].__setitem__("name", None),
     "bar: a series name cannot be taken away"),
    (lambda d: (d.__setitem__("series", []), d.__setitem__("categories", [])),
     "bar: a chart keeps at least one series"),
])
def test_a_chart_model_is_refused_where_the_edits_cannot_follow(change, message):
    document = opened()
    with pytest.raises(ChartModelError) as caught:
        with document.history.batch():
            _apply(document, "bar", change)
    assert str(caught.value) == message
    assert document.to_bytes() == DATA or cs.parts(document.to_bytes()) == cs.parts(DATA)


def test_the_read_only_hint_is_the_callers():
    document = opened()
    with pytest.raises(ChartModelError, match=r"\(it is 'General'\); open it elsewhere$"):
        _apply(document, "bar", lambda d: d.__setitem__("format", "0%"),
               read_only_hint="open it elsewhere")


def test_a_chart_data_error_becomes_a_model_error_with_the_callers_prefix():
    document = opened()
    with pytest.raises(ChartModelError) as caught:
        _apply(document, "scatter", lambda d: d["categories"].__setitem__(0, "one"))
    assert str(caught.value) == "scatter: 'one' is not a number, and this data is numeric"


def test_a_cache_only_chart_takes_a_model_without_warnings(recwarn):
    document = opened()
    wanted = _apply(document, "cache-ole", lambda d: d["series"][0].__setitem__(
        "values", [9, 9, 9]))
    assert document.chart("cache-ole").data == wanted
    assert not recwarn.list


# ------------------------------------------------------------------------------------------
# Applying a diagram model
# ------------------------------------------------------------------------------------------


def test_a_diagram_model_arrives():
    document = opened()
    diagram = document.diagram("bullets")
    model = diagram.model
    nodes = model["nodes"]
    nodes[1]["t"] = "Quicker edits"
    model["nodes"] = [node for node in nodes if node["t"] != "Stale caches"]
    position = next(i for i, node in enumerate(model["nodes"]) if node["t"] == "Risks")
    model["nodes"].insert(position + 1, {"lvl": 1, "t": "New risk"})
    apply_diagram_model(diagram, canonical_diagram(model, "d"), "d")
    assert [(n.level, n.text) for n in diagram.nodes] == [
        (0, "Goals"), (1, "Quicker edits"), (1, "Fewer prompts"), (0, "Risks"),
        (1, "New risk"), (1, "Helper")]
    assert diagram.drawing_part is None  # nodes came and went: the layout moved


def test_a_text_only_diagram_model_keeps_the_drawing():
    document = opened()
    diagram = document.diagram("bullets")
    model = diagram.model
    model["nodes"][4]["t"] = "Fresh caches"
    apply_diagram_model(diagram, canonical_diagram(model, "d"), "d")
    assert diagram.drawing_part == cs.DRAWING
    assert b"Fresh caches" in document.package.read(cs.DRAWING)


@pytest.mark.parametrize("change, message", [
    (lambda m: m["nodes"].insert(0, m["nodes"].pop(3)),
     "d: nodes cannot be reordered through the model"),
    (lambda m: m["nodes"][1].__setitem__("lvl", 0), None),
    (lambda m: m.__setitem__("layout", "urn:other"), "d: the layout is read-only"),
    (lambda m: m["nodes"].__delitem__(3),
     f"d: removing {cs.RISKS} would remove {cs.STALE}, which the model keeps"),
    (lambda m: m.__setitem__("nodes", []), "d: a diagram keeps at least one node"),
    (lambda m: m["nodes"][0].__setitem__("id", "{00000000-0000-0000-0000-000000000000}"),
     "d: no node '{00000000-0000-0000-0000-000000000000}' in the diagram; a new node has "
     "no id"),
], ids=["reorder", "level", "layout", "orphans", "empty", "unknown"])
def test_a_diagram_model_is_refused_where_the_edits_cannot_follow(change, message):
    document = opened()
    diagram = document.diagram("bullets")
    model = diagram.model
    change(model)
    with pytest.raises(ChartModelError) as caught:
        apply_diagram_model(diagram, canonical_diagram(model, "d"), "d")
    if message is not None:
        assert str(caught.value) == message
    assert document.to_bytes() == cs.Opened(DATA).to_bytes()


def test_the_source_named_in_refusals_is_the_callers():
    document = opened()
    diagram = document.diagram("bullets")
    model = diagram.model
    del model["nodes"][3]
    with pytest.raises(ChartModelError, match="which the outline keeps$"):
        apply_diagram_model(diagram, canonical_diagram(model, "d"), "d", through="the outline")
