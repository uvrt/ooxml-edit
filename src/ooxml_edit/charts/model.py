"""A chart's and a diagram's content as plain data: read, validated, and applied back.

:func:`chart_model` and :func:`diagram_model` describe a chart's data and labels and a
diagram's nodes as JSON-ready dictionaries.  A format layer that lets something outside edit
that data -- an agent, a serialised view of the document -- takes it back through
:func:`canonical_chart`/:func:`canonical_diagram`, which validate untrusted input field by
field and rewrite it in exactly the form the readers produce (so comparing two models
compares meaning, not spelling), and :func:`apply_chart_model`/:func:`apply_diagram_model`,
which bring the document there through the ordinary edits -- so the workbook moves with the
cache, and a diagram's cached drawing with its data model.

Every refusal is a :class:`ChartModelError` whose message starts with the ``what`` the caller
passed, so a format layer can re-raise it as its own error with the message unchanged.
"""

from __future__ import annotations

import difflib
import math
import re
import warnings
from typing import Any

from .chart import PLOT_TYPES, Chart, ChartDataError, chart_model
from .diagram import Diagram, diagram_model

#: The keys of :func:`chart_model`, in order.
CHART_KEYS = ("types", "title", "axis_titles", "legend", "format", "categories", "series")
#: Chart fields that describe and cannot be changed through the model.
CHART_READ_ONLY = ("types", "format")

_INT = re.compile(r"-?\d{1,15}")
_MODEL_ID = re.compile(r"\{[0-9A-Fa-f-]{1,64}\}|[0-9A-Za-z_-]{1,64}")


class ChartModelError(ValueError):
    """A chart or diagram model that cannot be accepted or applied: malformed, or asking for
    something the edits cannot do.  Raised before anything changes, or rolled back."""


# ------------------------------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------------------------------


def _parse_int(text: Any, what: str, low: int = -(2 ** 40), high: int = 2 ** 40) -> int:
    if isinstance(text, bool):
        raise ChartModelError(f"{what}: expected an integer, got {text!r}")
    if isinstance(text, int):
        value = text
    elif isinstance(text, str) and _INT.fullmatch(text.strip()):
        value = int(text)
    else:
        raise ChartModelError(f"{what}: {text!r} is not an integer")
    if not low <= value <= high:
        raise ChartModelError(f"{what}: {value} is out of range {low}..{high}")
    return value


def _text(value: Any, what: str, limit: int = 10_000) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ChartModelError(f"{what}: expected a string")
    return value


def _number(value: Any, what: str) -> "int | float | None":
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value):
        raise ChartModelError(f"{what}: {value!r} is not a finite number or null")
    return value


def _optional_text(value: Any, what: str, limit: int = 10_000) -> str | None:
    return None if value is None else _text(value, what, limit)


def canonical_chart(model: Any, what: str, max_points: int = 1_000_000) -> dict[str, Any]:
    """Validate a chart's JSON: ``{"types", "title", "axis_titles", "legend", "format",
    "categories", "series": [{"name", "values"}]}``, one value per category per series."""
    if not isinstance(model, dict) or set(model) != set(CHART_KEYS):
        raise ChartModelError(f"{what}: chart data is {{{', '.join(CHART_KEYS)}}}")
    types = model["types"]
    if not isinstance(types, list) or any(t not in PLOT_TYPES for t in types):
        raise ChartModelError(f"{what}: types are chart types such as 'bar' or 'line'")
    titles = model["axis_titles"]
    if not isinstance(titles, dict) or set(titles) - {"category", "value"}:
        raise ChartModelError(f"{what}: axis_titles has 'category' and 'value' at most")
    if not isinstance(model["legend"], bool):
        raise ChartModelError(f"{what}: legend is true or false")
    categories, series = model["categories"], model["series"]
    if not isinstance(categories, list) or not isinstance(series, list):
        raise ChartModelError(f"{what}: categories and series are lists")
    labels = []
    for index, label in enumerate(categories):
        where = f"{what} category {index}"
        labels.append(_text(label, where) if isinstance(label, str) else _number(label, where))
    canonical_series = []
    for index, entry in enumerate(series):
        where = f"{what} series {index}"
        if not isinstance(entry, dict) or set(entry) != {"name", "values"} \
                or not isinstance(entry["values"], list):
            raise ChartModelError(f"{where}: a series is {{\"name\", \"values\": [...]}}")
        values = [_number(v, where) for v in entry["values"]]
        canonical_series.append({"name": _optional_text(entry["name"], where),
                                 "values": values})
    lengths = {len(entry["values"]) for entry in canonical_series}
    if labels and lengths - {len(labels)}:
        raise ChartModelError(f"{what}: every series needs one value per category "
                              f"({len(labels)})")
    if len(lengths) > 1:
        raise ChartModelError(f"{what}: the series have different numbers of values")
    if (len(labels) + 1) * max(len(canonical_series), 1) > max_points:
        raise ChartModelError(f"{what}: more than {max_points} chart values")
    return {
        "types": list(types),
        "title": _optional_text(model["title"], what),
        "axis_titles": {key: _optional_text(titles[key], what) for key in ("category", "value")
                        if key in titles},
        "legend": model["legend"],
        "format": _optional_text(model["format"], what, 256),
        "categories": labels,
        "series": canonical_series,
    }


def canonical_diagram(model: Any, what: str, max_nodes: int = 10_000) -> dict[str, Any]:
    """Validate a diagram's JSON: ``{"layout"?, "nodes": [{"id"?, "lvl", "t"}]}``.  A node
    without an ``id`` is a new one."""
    if not isinstance(model, dict) or not {"nodes"} <= set(model) <= {"layout", "nodes"} \
            or not isinstance(model["nodes"], list):
        raise ChartModelError(f"{what}: a diagram is {{\"layout\", \"nodes\": [...]}}")
    if len(model["nodes"]) > max_nodes:
        raise ChartModelError(f"{what}: more than {max_nodes} diagram nodes")
    nodes, seen, previous = [], set(), -1
    for index, node in enumerate(model["nodes"]):
        where = f"{what} node {index}"
        if not isinstance(node, dict) or not {"lvl", "t"} <= set(node) <= {"id", "lvl", "t"}:
            raise ChartModelError(f"{where}: a node is {{\"id\", \"lvl\", \"t\"}}")
        level = _parse_int(node["lvl"], where, 0, 64)
        if level > previous + 1:
            raise ChartModelError(f"{where}: level {level} follows level {previous}")
        previous = level
        result: dict[str, Any] = {}
        if "id" in node:
            identifier = _text(node["id"], where, 80)
            if not _MODEL_ID.fullmatch(identifier) or identifier in seen:
                raise ChartModelError(f"{where}: {identifier!r} is not a fresh model id")
            seen.add(identifier)
            result["id"] = identifier
        result["lvl"] = level
        result["t"] = _text(node["t"], where, 1_000_000)
        nodes.append(result)
    result = {"nodes": nodes}
    if "layout" in model:
        result = {"layout": _optional_text(model["layout"], what, 1024), **result}
    return result


# ------------------------------------------------------------------------------------------
# Applying
# ------------------------------------------------------------------------------------------


def _tolerant(canonical, value: Any, what: str) -> Any:
    """A document's value in canonical form -- or as it is, if the file is unusual."""
    try:
        return canonical(value, what)
    except ChartModelError:
        return value


def _realign(count_live: int, count_wanted: int, live_keys: list, wanted_keys: list,
             delete, insert) -> None:
    """Insert and delete entries so the live sequence lines up with the wanted one."""
    matcher = difflib.SequenceMatcher(None, live_keys, wanted_keys, autojunk=False)
    for tag, i1, i2, j1, j2 in reversed(matcher.get_opcodes()):
        if tag == "equal":
            continue
        common = min(i2 - i1, j2 - j1)
        for index in reversed(range(i1 + common, i2)):
            delete(index)
        for offset in range(j2 - j1 - common):
            insert(i1 + common + offset)


def apply_chart_model(chart: Chart, wanted: dict[str, Any], what: str, *,
                      read_only_hint: str = "edit the chart part instead") -> None:
    """Bring a chart's data, titles and legend to ``wanted`` (a :func:`canonical_chart`
    result) through the chart edits: series and categories are lined up by name and label,
    then renamed, relabelled and revalued where they still differ.  A chart whose workbook
    cannot be reached is still edited, in its cache, without a warning per edit.

    ``read_only_hint`` ends the refusal of a change to a read-only field: what to do
    instead."""
    live = _tolerant(canonical_chart, chart.data, what)
    if live == wanted:
        return
    for key in CHART_READ_ONLY:
        if live.get(key) != wanted[key]:
            raise ChartModelError(f"{what}: {key} is read-only (it is {live.get(key)!r}); "
                                  f"{read_only_hint}")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # a cache-only chart is still edited; say so once
            _apply_chart_edits(chart, wanted, live, what)
    except ChartDataError as error:
        raise ChartModelError(f"{what}: {error}") from None


def _apply_chart_edits(chart: Chart, wanted: dict[str, Any], live: dict[str, Any],
                       what: str) -> None:
    if wanted["title"] != live["title"]:
        chart.set_title(wanted["title"])
    for axis, title in wanted["axis_titles"].items():
        if axis not in live["axis_titles"]:
            raise ChartModelError(f"{what}: the chart has no {axis} axis to title")
        if title != live["axis_titles"][axis]:
            chart.set_axis_title(axis, title)
    if wanted["legend"] != live["legend"]:
        chart.set_legend(wanted["legend"])

    # Series, lined up by name; then categories, by label.
    names = [entry["name"] for entry in live["series"]]
    wanted_names = [entry["name"] for entry in wanted["series"]]
    if not wanted_names:
        raise ChartModelError(f"{what}: a chart keeps at least one series")
    _realign(len(names), len(wanted_names), names, wanted_names,
             chart.remove_series, lambda index: chart.add_series(None, index=index))
    labels, wanted_labels = chart.categories, wanted["categories"]
    if labels or wanted_labels:
        if not wanted_labels:
            raise ChartModelError(f"{what}: a chart with categories keeps at least one")
        _realign(len(labels), len(wanted_labels), labels, wanted_labels,
                 chart.remove_category, lambda index: chart.add_category(None, index=index))
    else:  # no category labels at all: line the points up by count
        have = chart.point_count
        need = len(wanted["series"][0]["values"])
        for index in range(have, need):
            chart.add_category(None, index=index)
        for index in reversed(range(need, have)):
            chart.remove_category(index)

    for index, label in enumerate(wanted_labels):
        if chart.categories[index] != label:
            chart.set_category(index, label)
    for series, entry in zip(chart.series, wanted["series"]):
        if series.name != entry["name"]:
            if entry["name"] is None:
                raise ChartModelError(f"{what}: a series name cannot be taken away")
            series.set_name(entry["name"])
        if series.values != entry["values"]:
            series.set_values(entry["values"])


def apply_diagram_model(diagram: Diagram, wanted: dict[str, Any], what: str, *,
                        through: str = "the model") -> None:
    """Bring a diagram's nodes to ``wanted`` (a :func:`canonical_diagram` result): nodes
    missing from it are removed, nodes without an ``id`` are added where the list puts them,
    and texts are set.  Existing nodes keep their order and level -- moving one is refused.

    ``through`` names where ``wanted`` came from, in refusals ("cannot be reordered through
    the model")."""
    live = diagram.model
    if "layout" in wanted and wanted["layout"] != live.get("layout"):
        raise ChartModelError(f"{what}: the layout is read-only")
    live_nodes = {node["id"]: node for node in live["nodes"]}
    kept = [node["id"] for node in wanted["nodes"] if "id" in node]
    unknown = [identifier for identifier in kept if identifier not in live_nodes]
    if unknown:
        raise ChartModelError(f"{what}: no node {unknown[0]!r} in the diagram; a new node "
                              f"has no id")
    if kept != [node["id"] for node in live["nodes"] if node["id"] in set(kept)]:
        raise ChartModelError(f"{what}: nodes cannot be reordered through {through}")
    for node in wanted["nodes"]:
        if "id" in node and node["lvl"] != live_nodes[node["id"]]["lvl"]:
            raise ChartModelError(f"{what}: node {node['id']} cannot change level")
    if not wanted["nodes"]:
        raise ChartModelError(f"{what}: a diagram keeps at least one node")

    # Removals, deepest first, so a removed parent does not take a kept child with it.
    going = [node for node in live["nodes"] if node["id"] not in set(kept)]
    parents = {}
    stack: list[str] = []
    for node in live["nodes"]:
        del stack[node["lvl"]:]
        parents[node["id"]] = stack[-1] if stack else None
        stack.append(node["id"])
    for node in going:
        children_kept = [k for k in kept if _descends(k, node["id"], parents)]
        if children_kept:
            raise ChartModelError(f"{what}: removing {node['id']} would remove "
                                  f"{children_kept[0]}, which {through} keeps")
    for node in sorted(going, key=lambda n: -n["lvl"]):
        if node["id"] in {n.id for n in diagram.nodes}:
            diagram.remove_node(node["id"])

    # Additions and texts, in list order.
    stack = []
    counts: dict[str | None, int] = {}
    for node in wanted["nodes"]:
        del stack[node["lvl"]:]
        parent = stack[-1] if stack else None
        position = counts.get(parent, 0)
        counts[parent] = position + 1
        if "id" in node:
            identifier = node["id"]
            if diagram.node(identifier).text != node["t"]:
                diagram.set_text(identifier, node["t"])
        else:
            identifier = diagram.add_node(node["t"], parent=parent, index=position).id
        stack.append(identifier)


def _descends(node: str, ancestor: str, parents: dict[str, str | None]) -> bool:
    current = parents.get(node)
    while current is not None:
        if current == ancestor:
            return True
        current = parents.get(current)
    return False


__all__ = ["CHART_KEYS", "CHART_READ_ONLY", "ChartModelError", "apply_chart_model",
           "apply_diagram_model", "canonical_chart", "canonical_diagram", "chart_model",
           "diagram_model"]
