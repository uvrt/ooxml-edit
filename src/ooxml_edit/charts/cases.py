"""What the shared ``edit_chart`` and ``edit_smartart`` tools do, as one table every format
replays (review, S10).

The handlers of both agents are this package's actions (:mod:`.actions`); what each format
adds is how it finds a chart or diagram and what it calls it in a result (``address`` or
``id``).  This table is the calls a model makes and what they must give, the same in every
format, so a format's tests run it through its own tools and hold it to :func:`problems`::

    for case in CHART_CASES:
        made = dispatch("edit_chart", {"target": anchor, "action": "add", **CHART, "ref": "c"})
        for arguments in case["before"]:
            dispatch("edit_chart", {"target": "$c", **arguments})
        result = dispatch("edit_chart", {"target": "$c", **case["call"]})
        assert problems(case, result, key="address") == []

Every chart case starts from a new chart made from :data:`CHART`; every SmartArt case from
the format's own diagram, whose first node it changes.  An expectation is either
``{"error": code, "field": name, "says": words}`` or ``{"data": {path: value}, "keys":
[...], "no_keys": [...]}``, a path as :func:`pick` reads it.
"""

from __future__ import annotations

from typing import Any

#: The chart every chart case starts from: ``edit_chart``'s ``add`` arguments, less the
#: format's own (a ``box`` or ``width``, the ``target``).
CHART: dict[str, Any] = {
    "chart_type": "column", "categories": ["Q1", "Q2", "Q3"],
    "data": [{"name": "North", "values": [1, 2, 3]}, {"name": "South", "values": [4, 5, 6]}]}

#: The keys every chart result has, beside the format's ``address`` or ``id``.
CHART_KEYS = ("type", "title", "categories", "series", "legend", "number_formats")

#: ``{"name", "before": [arguments], "call": arguments, "expect"}``.
CHART_CASES: list[dict[str, Any]] = [
    {"name": "add_category takes its label from text, a value per series",
     "before": [], "call": {"action": "add_category", "text": "Q4", "values": [7, 8]},
     "expect": {"data": {"categories": ["Q1", "Q2", "Q3", "Q4"],
                         "series[0].values": [1, 2, 3, 7], "series[1].values": [4, 5, 6, 8]}}},
    {"name": "add_category without values: blanks",
     "before": [], "call": {"action": "add_category", "text": "Q4"},
     "expect": {"data": {"categories[3]": "Q4", "series[0].values": [1, 2, 3, None],
                         "series[1].values": [4, 5, 6, None]}}},
    {"name": "add_category's label is not category",
     "before": [], "call": {"action": "add_category", "category": "Q4", "values": [7, 8]},
     "expect": {"error": "invalid_arguments", "field": "text",
                "says": "add_category needs text: the new category's label"}},
    {"name": "add_series without values: blanks",
     "before": [], "call": {"action": "add_series", "text": "East"},
     "expect": {"data": {"series[2]": {"name": "East", "values": [None, None, None]}}}},
    {"name": "add_series with values",
     "before": [], "call": {"action": "add_series", "text": "East", "values": [7, 8, 9]},
     "expect": {"data": {"series[2]": {"name": "East", "values": [7, 8, 9]}}}},
    {"name": "add_series needs its name",
     "before": [], "call": {"action": "add_series", "values": [7, 8, 9]},
     "expect": {"error": "invalid_arguments", "field": "text",
                "says": "add_series needs text: the new series' name"}},
    {"name": "set_legend without a position: on the right",
     "before": [{"action": "set_legend", "position": "none"}],
     "call": {"action": "set_legend"}, "expect": {"data": {"legend": True}}},
    {"name": "set_legend none hides it",
     "before": [], "call": {"action": "set_legend", "position": "none"},
     "expect": {"data": {"legend": False}}},
    {"name": "set_legend refuses a label position",
     "before": [], "call": {"action": "set_legend", "position": "center"},
     "expect": {"error": "invalid_arguments", "field": "position",
                "says": "center is a label position; a legend is right, left, top, bottom"}},
    {"name": "show_data_labels in a number format",
     "before": [], "call": {"action": "show_data_labels", "number_format": "0.0"},
     "expect": {"data": {"number_formats.data_labels": "0.0"}}},
    {"name": "hide_data_labels takes number_format as show does, and refuses it",
     "before": [{"action": "show_data_labels"}],
     "call": {"action": "hide_data_labels", "number_format": "0.0"},
     "expect": {"error": "invalid_arguments", "says": "a number format is for labels shown"}},
    {"name": "hide_data_labels",
     "before": [{"action": "show_data_labels", "number_format": "0.0"}],
     "call": {"action": "hide_data_labels"},
     "expect": {"data": {"number_formats.data_labels": None}}},
    {"name": "set_values needs values",
     "before": [], "call": {"action": "set_values", "series": "South"},
     "expect": {"error": "invalid_arguments", "field": "values",
                "says": "set_values needs values: one number per category (3)"}},
    {"name": "set_value by names",
     "before": [], "call": {"action": "set_value", "series": "South", "category": "Q2",
                            "value": 9},
     "expect": {"data": {"series[1].values": [4, 9, 6]}}},
    {"name": "set_value needs value",
     "before": [], "call": {"action": "set_value", "series": "South", "category": "Q2"},
     "expect": {"error": "invalid_arguments", "field": "value",
                "says": "set_value needs value: the number"}},
    {"name": "rename_category",
     "before": [], "call": {"action": "rename_category", "category": "Q2", "text": "Mid"},
     "expect": {"data": {"categories": ["Q1", "Mid", "Q3"]}}},
    {"name": "remove_series",
     "before": [], "call": {"action": "remove_series", "series": "North"},
     "expect": {"data": {"series": [{"name": "South", "values": [4, 5, 6]}]}}},
    {"name": "rename_series",
     "before": [], "call": {"action": "rename_series", "series": "0", "text": "West"},
     "expect": {"data": {"series[0].name": "West"}}},
    {"name": "set_title",
     "before": [], "call": {"action": "set_title", "text": "Sales"},
     "expect": {"data": {"title": "Sales"}}},
    {"name": "set_axis_title needs axis",
     "before": [], "call": {"action": "set_axis_title", "text": "Units"},
     "expect": {"error": "invalid_arguments", "field": "axis",
                "says": "set_axis_title needs axis: category or value"}},
    {"name": "set_gap_width",
     "before": [], "call": {"action": "set_gap_width", "value": 80},
     "expect": {"data": {"gap_width": 80}}},
    {"name": "set_gap_width is a whole percentage",
     "before": [], "call": {"action": "set_gap_width", "value": 80.5},
     "expect": {"error": "invalid_arguments", "field": "value",
                "says": "a gap width is a whole percentage, 0-500"}},
    {"name": "read: the result's keys and the look",
     "before": [], "call": {"action": "read"},
     "expect": {"data": {"legend": True, "axes": {"category": None, "value": None},
                         "number_formats.values": "General"},
                "keys": ["axes", "workbook", "scales", "types", "gap_width"],
                "no_keys": ["number_format"]}},
    {"name": "a formatting action returns the look too",
     "before": [], "call": {"action": "format_series", "series": "North",
                            "style": {"fill": "accent2"}},
     "expect": {"data": {"series_format.North.fill": "accent2"}, "keys": ["scales"]}},
]

#: ``{"name", "call": arguments, "expect"}``; ``node`` 0 is the diagram's first node.
SMARTART_CASES: list[dict[str, Any]] = [
    {"name": "set_text takes empty text",
     "call": {"action": "set_text", "node": 0, "text": ""},
     "expect": {"data": {"nodes[0].n": 0, "nodes[0].text": ""}}},
    {"name": "set_text without text is empty text",
     "call": {"action": "set_text", "node": 0},
     "expect": {"data": {"nodes[0].text": ""}}},
    {"name": "set_text: the nodes as {n, text, level}",
     "call": {"action": "set_text", "node": 0, "text": "First"},
     "expect": {"data": {"nodes[0].n": 0, "nodes[0].text": "First", "nodes[0].level": 0}}},
    {"name": "add_node: last, at the top level",
     "call": {"action": "add_node", "text": "New"},
     "expect": {"data": {"nodes[-1].text": "New", "nodes[-1].level": 0}}},
    {"name": "set_text needs node",
     "call": {"action": "set_text", "text": "x"},
     "expect": {"error": "invalid_arguments", "field": "node", "says": "set_text needs node"}},
    {"name": "a node past the end",
     "call": {"action": "remove_node", "node": 99},
     "expect": {"error": "not_found", "field": "node", "says": "no node 99"}},
]


def pick(data: Any, path: str) -> Any:
    """The value at ``path`` in ``data``: keys by dots, list positions in brackets (``-1``
    the last)::

        pick({"series": [{"name": "A"}]}, "series[0].name")   # 'A'
    """
    value = data
    for step in path.replace("[", ".[").split("."):
        if not step:
            continue
        if step.startswith("["):
            value = value[int(step[1:-1])]
        else:
            value = value[step]
    return value


def problems(case: dict[str, Any], result: Any, key: str | None = None) -> list[str]:
    """What in ``result`` (the tool's ``Result``) is not what ``case`` expects; ``key`` is
    the format's name for the chart in a result (``address`` or ``id``), which a chart
    result must have, and the other must not."""
    expect = case["expect"]
    found: list[str] = []
    if "error" in expect:
        if result.ok:
            return [f"{case['name']}: ok, expected {expect['error']}"]
        error = result.error
        if error.code != expect["error"]:
            found.append(f"{case['name']}: code {error.code}, expected {expect['error']}")
        if "field" in expect and error.field != expect["field"]:
            found.append(f"{case['name']}: field {error.field}, expected {expect['field']}")
        if expect.get("says", "") not in error.message:
            found.append(f"{case['name']}: {error.message!r} does not say {expect['says']!r}")
        return found
    if not result.ok:
        return [f"{case['name']}: {result.error.code}: {result.error.message}"]
    data = result.data
    for path, wanted in expect.get("data", {}).items():
        try:
            got = pick(data, path)
        except (KeyError, IndexError, TypeError):
            found.append(f"{case['name']}: no {path}")
            continue
        if got != wanted:
            found.append(f"{case['name']}: {path} is {got!r}, expected {wanted!r}")
    for name in expect.get("keys", []):
        if name not in data:
            found.append(f"{case['name']}: no key {name}")
    for name in expect.get("no_keys", []):
        if name in data:
            found.append(f"{case['name']}: key {name} should not be there")
    if key is not None and "series" in data:
        other = {"address": "id", "id": "address"}.get(key)
        for name in CHART_KEYS + (key,):
            if name not in data:
                found.append(f"{case['name']}: no key {name}")
        if other in data:
            found.append(f"{case['name']}: key {other} is the other format's")
    return found


__all__ = ["CHART", "CHART_CASES", "CHART_KEYS", "SMARTART_CASES", "pick", "problems"]
