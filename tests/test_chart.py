"""Charts: data, titles and legend, with the embedded workbook kept in step.

Every edit runs on every synthetic chart -- bar, line, pie, doughnut, scatter, radar, bubble,
a chart with two plots, series in columns and in rows, shared and inline strings, a table, a
formula cell -- and goes through the same gates:

* **round trip** -- edit, save, reopen, read back;
* **the workbook agrees** -- the saved package's embedded workbook is opened independently
  (``tests/xlsx.py``, not the library's own reader) and every formula's cells must hold what
  its cache holds, tables must cover the data and be named after their headers, and the
  shared-string counts must be right;
* **undo** -- back to the original bytes, workbook included, and redo to the edited ones;
* **validity** -- every part well-formed and typed, every relationship resolving, and the
  chart part in schema order.
"""

from __future__ import annotations

import random
import re
import warnings

import pytest
from lxml import etree

import charts_synthetic as cs
from ooxml_edit.charts import ChartDataError, ChartDataWarning
from ooxml_edit.charts.workbook import Cell, Workbook
from ooxml_edit.xml import _ranks, prefixed_name
from xlsx import Book, check_chart_against_workbook

C = "{%s}" % cs.C
X = "{%s}" % cs.X
DATA = cs.package()
SPECS = {spec.name: spec for spec in cs.charts()}
CHARTS = list(SPECS)


def opened(**options) -> cs.Opened:
    return cs.Opened(DATA, **options)


def check_charts(data: bytes) -> int:
    """Every chart in a saved package agrees with its workbook; returns formulas checked."""
    document = cs.Opened(data)
    checked = 0
    for name in CHARTS:
        chart = document.chart(name)
        workbook = chart.workbook_part
        checked += check_chart_against_workbook(
            document.package.read(chart.part),
            None if workbook is None else document.package.read(workbook))
        assert not chart_order_violations(document.package.read(chart.part)), name
    return checked


def chart_order_violations(data: bytes) -> set[tuple[str, str]]:
    """``(parent, child)`` pairs out of their schema order in a chart part."""
    found = set()
    for parent in etree.fromstring(data).iter():
        ranks = _ranks(prefixed_name(parent))
        if not ranks:
            continue
        last = -1
        for child in parent:
            rank = ranks.get(prefixed_name(child), len(ranks) + 1)
            if rank < last:
                found.add((prefixed_name(parent), prefixed_name(child)))
            last = max(last, rank)
    return found


def test_the_synthetic_charts_are_consistent_before_any_edit():
    assert check_charts(DATA) > 40
    cs.assert_valid(DATA)


def test_reading():
    document = opened()
    chart = document.chart("bar")
    assert chart.chart_types == ["bar"] and chart.chart_type == "bar"
    assert chart.categories == ["Q1", "Q2", "Q3", "Q4"]
    assert [s.name for s in chart.series] == ["North", "South"]
    assert chart.series[0].values == [10, 12.5, 9, 14]
    assert chart.series_named("South").values == [7, 8, 11, 6]
    assert chart.title == "Sales" and chart.has_legend
    assert chart.axes == ["category", "value"] and chart.axis_title("value") is None
    assert chart.number_format == "General"
    assert chart.part == "doc/charts/chart1.xml"
    assert chart.workbook_part == "doc/embeddings/book1.xlsx"
    assert document.chart("combo").chart_types == ["bar", "line"]
    assert document.chart("pie").axes == []
    assert document.chart("scatter").categories == [1, 2, 3, 5]
    assert document.chart("line").data == {
        "types": ["line"], "title": None, "axis_titles": {"category": None, "value": None},
        "legend": True, "format": "General", "categories": ["Q1", "Q2", "Q3", "Q4"],
        "series": [{"name": "Revenue", "values": [100, 120, 90, 130]},
                   {"name": "Cost", "values": [80, 85, 70, 95]},
                   {"name": "Margin", "values": [20, 35, 20, 35]}],
    }


def test_the_chart_is_found_anywhere_under_the_frame():
    """The test host nests ``c:chart`` two levels down; a format's frame may nest it deeper."""
    document = opened()
    assert document.chart("radar").part == "doc/charts/chart7.xml"


# ------------------------------------------------------------------------------------------
# The E4 edits, each through every gate
# ------------------------------------------------------------------------------------------


def _label(chart, text: str, number: float):
    """A new category label: text, or a number where the categories are numbers."""
    numeric = any(isinstance(c, (int, float)) for c in chart.categories)
    return number if numeric else text


def _edit_value(chart):
    series = chart.series[-1]
    index = len(series.values) - 1
    series.set_value(index, 1234.5)
    return lambda c: c.series[-1].values[index] == 1234.5


def _edit_blank(chart):
    chart.series[0].set_value(0, None)
    return lambda c: c.series[0].values[0] is None


def _edit_values(chart):
    count = len(chart.categories) or chart.point_count
    wanted = [float(10 * (i + 1)) + 0.25 for i in range(count)]
    chart.series[0].set_values(wanted)
    return lambda c: c.series[0].values == wanted


def _edit_category(chart):
    label = _label(chart, "E4 label", 0.5)
    chart.set_category(0, label)
    return lambda c: c.categories[0] == label


def _edit_name(chart):
    chart.series[0].name = "E4 series"
    return lambda c: c.series[0].name == "E4 series"


def _add_category_last(chart):
    count = len(chart.series)
    label = _label(chart, "E4 new", 99)
    chart.add_category(label, list(range(7, 7 + count)))
    return lambda c: c.categories[-1] == label and c.series[-1].values[-1] == 6 + count


def _add_category_first(chart):
    before = chart.categories
    label = _label(chart, "E4 first", -1)
    chart.add_category(label, {chart.series[0].name: 99}, index=0)
    return lambda c: c.categories == [label] + before and c.series[0].values[0] == 99 \
        and all(s.values[0] is None for s in c.series[1:])


def _remove_category(chart):
    before = chart.categories
    values = chart.series[0].values
    chart.remove_category(1)
    return lambda c: c.categories == before[:1] + before[2:] \
        and c.series[0].values == values[:1] + values[2:]


def _add_series(chart):
    count = chart.point_count
    names = [s.name for s in chart.series]
    chart.add_series("E4 added", [float(i) + 0.5 for i in range(count)])
    return lambda c: [s.name for s in c.series] == names + ["E4 added"] \
        and c.series[-1].values == [float(i) + 0.5 for i in range(count)]


def _add_series_first(chart):
    names = [s.name for s in chart.series]
    chart.add_series("E4 front", [1] * chart.point_count, index=0)
    return lambda c: [s.name for s in c.series] == ["E4 front"] + names


def _remove_series(chart):
    if len(chart.series) < 2:
        chart.add_series("E4 spare", [1] * chart.point_count)
    names = [s.name for s in chart.series]
    values = [s.values for s in chart.series]
    chart.remove_series(0)
    return lambda c: [s.name for s in c.series] == names[1:] \
        and [s.values for s in c.series] == values[1:]


def _titles(chart):
    chart.set_title("E4 title")
    axes = chart.axes
    for axis in axes:
        chart.set_axis_title(axis, f"E4 {axis}")
    return lambda c: c.title == "E4 title" and all(c.axis_title(a) == f"E4 {a}" for a in axes)


def _legend(chart):
    shown = chart.has_legend
    chart.set_legend(not shown)
    return lambda c: c.has_legend is (not shown)


EDITS = {
    "value": _edit_value, "blank": _edit_blank, "values": _edit_values,
    "category": _edit_category, "name": _edit_name, "add_category_last": _add_category_last,
    "add_category_first": _add_category_first, "remove_category": _remove_category,
    "add_series": _add_series, "add_series_first": _add_series_first,
    "remove_series": _remove_series, "titles": _titles, "legend": _legend,
}


def test_there_are_thirteen_edits():
    assert len(EDITS) == 13


@pytest.mark.parametrize("edit", list(EDITS))
@pytest.mark.parametrize("name", CHARTS)
def test_each_edit_round_trips_with_the_workbook_in_step(name, edit):
    document = opened()
    with warnings.catch_warnings():
        warnings.simplefilter("error", ChartDataWarning)  # every one of these has a workbook
        holds = EDITS[edit](document.chart(name))
    edited = document.to_bytes()

    reopened = cs.Opened(edited)
    assert holds(reopened.chart(name)), reopened.chart(name).data
    assert check_charts(edited) > 0
    cs.assert_valid(edited)

    document.undo_all()
    assert cs.parts(document.to_bytes()) == cs.parts(DATA)
    document.redo_all()
    assert document.to_bytes() == edited


@pytest.mark.parametrize("name", CHARTS)
def test_all_edits_together(name):
    document = opened()
    for edit in EDITS.values():
        holds = edit(document.chart(name))
    edited = document.to_bytes()
    check_charts(edited)
    cs.assert_valid(edited)
    assert holds(cs.Opened(edited).chart(name))  # the last edit, at least
    document.undo_all()
    assert cs.parts(document.to_bytes()) == cs.parts(DATA)


def test_an_unchanged_value_changes_nothing():
    document = opened()
    chart = document.chart("bar")
    chart.series[1].set_value(1, 8)
    chart.set_category(0, "Q1")
    chart.series[0].name = "North"
    chart.set_title("Sales")
    chart.set_legend(True)
    assert not document.history.can_undo()
    assert document.package.dirty_parts == frozenset()


# ------------------------------------------------------------------------------------------
# The workbook, specifically
# ------------------------------------------------------------------------------------------


def _book(document: cs.Opened, name: str) -> Book:
    return Book(document.package.read(document.chart(name).workbook_part))


def test_a_value_reaches_its_cell():
    document = opened()
    document.chart("bar").series[1].set_value(2, 8.25)
    assert _book(document, "bar").sheet("Sheet1").value("C4") == 8.25
    document.chart("bar-rows").series[1].set_value(0, 9.5)  # series in rows
    assert _book(document, "bar-rows").sheet("Sheet1").value("B3") == 9.5


def test_shared_strings_are_reused_and_counted():
    document = opened()
    document.chart("bar").set_category(2, "Q1")  # a string the workbook already has
    book = _book(document, "bar")
    assert book.strings.count("Q1") == 1
    assert book.sheet("Sheet1").value("A4") == "Q1"
    assert int(book.string_counts[1]) == len(book.strings)
    assert int(book.string_counts[0]) == book.count_shared()


def test_a_workbook_without_shared_strings_gets_inline_strings():
    document = opened()
    document.chart("bar-rows").set_category(1, "Inline")
    book = _book(document, "bar-rows")
    assert book.strings == [] and book.string_counts is None
    assert book.sheet("Sheet1").value("C1") == "Inline"


def test_a_table_follows_the_data():
    document = opened()
    chart = document.chart("bar")
    chart.add_series("Extra", [1, 2, 3, 4])
    chart.add_category("Q5", [1, 2, 3])
    table = _book(document, "bar").sheet("Sheet1").tables[0]
    assert table.get("ref") == "A1:D6"
    assert [c.get("name") for c in table.iter(X + "tableColumn")][1:] == [
        "North", "South", "Extra"]
    chart.remove_series(0)
    chart.remove_category(0)
    table = _book(document, "bar").sheet("Sheet1").tables[0]
    assert table.get("ref") == "A1:C5"
    assert [c.get("name") for c in table.iter(X + "tableColumn")][1:] == ["South", "Extra"]


def test_formulas_follow_the_data():
    document = opened()
    chart = document.chart("line")
    chart.remove_series(0)
    chart.add_category("Q5", [1, 2])
    formulas = re.findall(r"<c:f>([^<]*)</c:f>", document.package.read(chart.part).decode())
    assert formulas == ["Sheet1!$B$1", "Sheet1!$A$2:$A$6", "Sheet1!$B$2:$B$6",
                        "Sheet1!$C$1", "Sheet1!$A$2:$A$6", "Sheet1!$C$2:$C$6"]
    sheet = _book(document, "line").sheet("Sheet1")
    assert sheet.range("B1", "C1") == ["Cost", "Margin"]
    assert sheet.value("D1") is None and sheet.value("D2") is None


def test_formulas_follow_the_data_in_rows():
    document = opened()
    chart = document.chart("radar")  # series in rows
    chart.add_category("Fun", [1, 2], index=2)
    formulas = re.findall(r"<c:f>([^<]*)</c:f>", document.package.read(chart.part).decode())
    assert formulas[:3] == ["Sheet1!$A$2", "Sheet1!$B$1:$G$1", "Sheet1!$B$2:$G$2"]
    sheet = _book(document, "radar").sheet("Sheet1")
    assert sheet.range("B1", "G1") == ["Speed", "Cost", "Fun", "Risk", "Fit", "Ease"]
    assert sheet.range("B3", "G3") == [4, 2, 2, 4, 3, 5]


def test_a_point_moves_its_formatting_with_it():
    """``c:dPt`` and ``c:dLbl`` follow their point when a category is inserted before it."""
    document = opened()
    chart = document.chart("pie")
    before = re.findall(rb'<c:dPt><c:idx val="(\d+)"', document.package.read(chart.part))
    label = re.findall(rb'<c:dLbl><c:idx val="(\d+)"', document.package.read(chart.part))
    chart.add_category("New", [5], index=0)
    after = re.findall(rb'<c:dPt><c:idx val="(\d+)"', document.package.read(chart.part))
    assert [int(v) for v in after] == [int(v) + 1 for v in before]
    assert re.findall(rb'<c:dLbl><c:idx val="(\d+)"', document.package.read(chart.part)) == [
        str(int(label[0]) + 1).encode()]
    chart.remove_category(2)  # the labelled point goes, and its label with it
    assert b"<c:dLbl>" not in document.package.read(chart.part)


def test_a_formula_a_value_replaces_goes_with_the_calculation_chain():
    """A cell computed by a formula becomes a plain value, and the calculation chain that
    named it is removed (Excel rebuilds it; a stale one is a repair)."""
    document = opened()
    chart = document.chart("line")
    assert "xl/calcChain.xml" in _book(document, "line").archive.namelist()
    chart.series[0].set_value(1, 40)
    book = _book(document, "line")
    assert "xl/calcChain.xml" not in book.archive.namelist()
    assert b"<f>" not in book.archive.read("xl/worksheets/sheet1.xml")
    assert book.sheet("Sheet1").value("B3") == 40
    assert "calcChain" not in book.archive.read("[Content_Types].xml").decode()
    assert "calcChain" not in book.archive.read("xl/_rels/workbook.xml.rels").decode()


def test_untouched_workbook_parts_keep_their_bytes():
    original = Book(cs.Opened(DATA).package.read("doc/embeddings/book1.xlsx"))
    document = opened()
    document.chart("bar").series[0].set_value(0, 1)
    edited = _book(document, "bar")
    changed = {"xl/worksheets/sheet1.xml", "xl/tables/table1.xml"}
    for name in original.archive.namelist():
        if name not in changed and not name.endswith("/"):
            assert edited.archive.read(name) == original.archive.read(name), name
    assert [i.compress_type for i in edited.archive.infolist()] == [
        i.compress_type for i in original.archive.infolist()]


def test_a_new_cell_takes_its_neighbours_style():
    document = opened()
    document.chart("bar").add_category("Q5", [1, 2])
    sheet = etree.fromstring(_book(document, "bar").archive.read("xl/worksheets/sheet1.xml"))
    cell = next(c for c in sheet.iter(X + "c") if c.get("r") == "B6")
    assert cell.get("s") == "1"


def test_a_bubble_chart_keeps_its_sizes_in_step():
    document = opened()
    chart = document.chart("bubble")
    chart.add_category(4, [40, 45])
    root = etree.fromstring(document.package.read(chart.part))
    sizes = [s.find(C + "bubbleSize") for s in root.iter(C + "ser")]
    assert [s.find(f"{C}numRef/{C}f").text for s in sizes] == ["Sheet1!$C$2:$C$5",
                                                               "Sheet1!$E$2:$E$5"]
    added = chart.add_series("New", [1, 2, 3, 4])
    assert added.values == [1, 2, 3, 4]
    # The new series' sizes are not in the workbook: they are a literal, never a reference
    # without a formula, which the schema does not allow.
    root = etree.fromstring(document.package.read(chart.part))
    new_sizes = list(root.iter(C + "ser"))[-1].find(C + "bubbleSize")
    assert new_sizes.find(C + "numLit") is not None and new_sizes.find(C + "numRef") is None
    check_charts(document.to_bytes())


# ------------------------------------------------------------------------------------------
# Titles: the host's language, or its own template
# ------------------------------------------------------------------------------------------


def test_a_new_title_is_rich_text_that_inherits_its_formatting():
    document = opened()
    chart = document.chart("line")
    chart.set_title("Plain")
    chart.set_axis_title("value", "Up")
    root = etree.fromstring(document.package.read(chart.part))
    title = root.find(f"{C}chart/{C}title")
    assert title.find(f"{C}tx/{C}rich") is not None
    assert list(title.iter("{%s}rPr" % cs.A)) == []  # the chart's text properties apply
    body = root.find(f"{C}chart/{C}plotArea/{C}valAx/{C}title/{C}tx/{C}rich/"
                     "{%s}bodyPr" % cs.A)
    assert body.get("rot") == "-5400000"  # the value axis is vertical
    assert chart.title == "Plain" and chart.axis_title("value") == "Up"
    deleted = root.find(f"{C}chart/{C}autoTitleDeleted")
    assert deleted.get("val") == "0"


def test_a_host_language_goes_into_the_end_of_a_new_titles_paragraph():
    document = opened(lang="nl-NL")
    document.chart("line").set_title("")
    data = document.package.read(document.chart("line").part)
    assert b'<a:p><a:pPr><a:defRPr/></a:pPr><a:endParaRPr lang="nl-NL"/></a:p>' in data
    assert b"<a:r>" not in data.split(b"<c:title>")[1].split(b"</c:title>")[0]
    plain = opened()
    plain.chart("line").set_title("")
    assert b'<a:p><a:pPr><a:defRPr/></a:pPr></a:p>' in plain.package.read(plain.chart("line").part)


def test_the_text_of_a_new_title_keeps_the_host_language():
    # The text written into a new title takes its run's properties from the paragraph's
    # end, so a chart title and an axis title alike carry the language, before and after
    # the document is saved and opened again.
    document = opened(lang="nl-NL")
    chart = document.chart("line")
    chart.set_title("Omzet")
    chart.set_axis_title("value", "Euro")
    chart.set_axis_title("category", "Maand")
    for reopened in (document, cs.Opened(document.to_bytes())):
        root = etree.fromstring(reopened.package.read(chart.part))
        titles = [root.find(f"{C}chart/{C}title")] + [
            root.find(f"{C}chart/{C}plotArea/{C}{axis}/{C}title") for axis in ("catAx", "valAx")]
        for title in titles:
            runs = list(title.iter("{%s}r" % cs.A))
            assert len(runs) == 1
            assert runs[0].find("{%s}rPr" % cs.A).get("lang") == "nl-NL"
            end = title.find(f"{C}tx/{C}rich/{{{cs.A}}}p/{{{cs.A}}}endParaRPr")
            assert end is not None and end.get("lang") == "nl-NL"
    assert chart.title == "Omzet" and chart.axis_title("value") == "Euro"
    check_charts(document.to_bytes())


def test_a_host_template_builds_new_titles():
    from ooxml_edit.xml import make

    def template(vertical: bool):
        tx = make("c:tx")
        rich = make("c:rich")
        rich.append(make("a:bodyPr", vert="vert" if vertical else "horz"))
        paragraph = make("a:p")
        paragraph.append(make("a:endParaRPr", lang="de-DE", b="1"))
        rich.append(paragraph)
        tx.append(rich)
        return tx

    document = opened(title_template=template)
    chart = document.chart("line")
    chart.set_title("Umsatz")
    data = document.package.read(chart.part)
    assert b'<a:rPr lang="de-DE" b="1"/><a:t>Umsatz</a:t>' in data
    assert chart.title == "Umsatz"


def test_an_existing_title_keeps_its_formatting():
    document = opened()
    chart = document.chart("bar")
    chart.set_title("Sales by region")
    assert b'<a:rPr lang="en-GB" b="1"/><a:t>Sales by region</a:t>' in \
        document.package.read(chart.part)


def test_removing_the_title_stops_an_automatic_one():
    document = opened()
    chart = document.chart("bar")
    chart.set_title(None)
    root = etree.fromstring(document.package.read(chart.part))
    assert root.find(f"{C}chart/{C}title") is None
    assert root.find(f"{C}chart/{C}autoTitleDeleted").get("val") == "1"


# ------------------------------------------------------------------------------------------
# Without a workbook
# ------------------------------------------------------------------------------------------

REASONS = {
    "cache-linked": "its workbook is linked from outside the document "
                    "(file:///elsewhere/book.xlsx)",
    "cache-ole": "its data is embedded as an OLE object, not as a workbook",
    "cache-missing": "its workbook relationship is missing",
    "cache-none": "the chart has no workbook",
}


@pytest.mark.parametrize("name", list(REASONS))
def test_without_a_workbook_the_cache_is_edited_with_a_warning(name):
    document = opened()
    chart = document.chart(name)
    assert chart.workbook_part is None
    before = cs.parts(DATA)
    with pytest.warns(ChartDataWarning) as caught:
        chart.series[0].set_value(0, 77)
    assert str(caught[0].message) == (
        f"{chart.part}: only the chart's cached data was changed, because {REASONS[name]}; "
        "the application's Edit Data will show the old values")
    assert chart.series[0].values[0] == 77
    with pytest.warns(ChartDataWarning):
        added = chart.add_series("Cache only", [1, 2, 3])
    assert added.values == [1, 2, 3]
    # The new series cannot point at cells nobody wrote: its data is literal.
    assert b"<c:numLit>" in document.package.read(chart.part)
    with pytest.warns(ChartDataWarning):
        chart.add_category("W", [1, 2, 3])
    assert chart.series[-1].values == [1, 2, 3, 3] or chart.series[-1].values[-1] == 3
    after = cs.parts(document.to_bytes())
    assert {p for p in after if after[p] != before.get(p)} == {chart.part}
    document.undo_all()
    assert cs.parts(document.to_bytes()) == before


def test_the_warning_names_the_hosts_application_and_document():
    document = opened(application="Writer", document="report")
    with pytest.warns(ChartDataWarning, match="linked from outside the report .*; "
                                              "Writer's Edit Data will show"):
        document.chart("cache-linked").series[0].set_value(0, 5)


# ------------------------------------------------------------------------------------------
# Refusals: before anything changes
# ------------------------------------------------------------------------------------------


def test_a_layout_insertion_cannot_follow_is_refused():
    document = opened()
    chart = document.chart("line")
    root = document.package.tree(chart.part)
    formulas = [f for f in root.iter(C + "f") if f.text == "Sheet1!$C$2:$C$5"]
    formulas[0].text = "Sheet1!$C$3:$C$6"  # one series a row lower than the rest
    document.package.mark_dirty(chart.part)
    before = document.to_bytes()
    with pytest.raises(ChartDataError, match="do not line up"):
        chart.add_category("Q5")
    assert document.to_bytes() == before


def test_a_cell_in_the_way_is_refused():
    document = opened()
    chart = document.chart("bar")
    package = document.package
    nested = package.open_embedded(chart.workbook_part)
    Workbook(nested).sheet("Sheet1").set_value(Cell(6, 2), "note")
    package.replace_embedded(chart.workbook_part, nested)
    before = document.to_bytes()
    with pytest.raises(ChartDataError, match="B6 is in the way"):
        chart.add_category("Q5")
    assert document.to_bytes() == before


def test_bad_values_are_refused():
    chart = opened().chart("bar")
    for bad in (float("nan"), float("inf"), "12", True):
        with pytest.raises(ChartDataError, match="is not a finite number"):
            chart.series[0].set_value(0, bad)
    with pytest.raises(ChartDataError, match="2 values for 4 categories"):
        chart.series[0].set_values([1, 2])
    with pytest.raises(ChartDataError):
        chart.add_series("Short", [1])
    with pytest.raises(IndexError, match="bar: no category 4"):
        chart.series[0].set_value(4, 1)
    with pytest.raises(KeyError):
        chart.add_category("x", {"Nobody": 1})
    with pytest.raises(ChartDataError, match="is not a number"):
        opened().chart("scatter").set_category(0, "text")


def test_the_last_series_and_category_stay():
    chart = opened().chart("pie")
    with pytest.raises(ChartDataError, match="pie: a chart keeps at least one series"):
        chart.remove_series(0)
    for _ in range(2):
        chart.remove_category(0)
    with pytest.raises(ChartDataError, match="at least one category"):
        chart.remove_category(0)


def test_a_refused_edit_leaves_one_undo_step_less():
    document = opened()
    chart = document.chart("bar")
    chart.series[0].set_value(0, 1)
    with pytest.raises(ChartDataError):
        chart.series[0].set_values([1])
    assert document.history.undo() and not document.history.can_undo()


# ------------------------------------------------------------------------------------------
# A random walk: cache and workbook agree after every step, and undo goes all the way back
# ------------------------------------------------------------------------------------------


def _random_step(chart, rng: random.Random, step: int) -> None:
    count, series = chart.point_count, len(chart.series)
    numeric = any(isinstance(c, (int, float)) for c in chart.categories)
    action = rng.choice(["add_category", "remove_category", "add_series", "remove_series",
                         "value", "category", "name"])
    if action == "add_category":
        label = 100 + step if numeric else f"C{step}"
        chart.add_category(label, [rng.randint(0, 50) for _ in range(series)],
                           index=rng.randint(0, count))
    elif action == "remove_category" and count > 1:
        chart.remove_category(rng.randrange(count))
    elif action == "add_series":
        chart.add_series(f"S{step}", [rng.randint(0, 50) for _ in range(count)],
                         index=rng.randint(0, series))
    elif action == "remove_series" and series > 1:
        chart.remove_series(rng.randrange(series))
    elif action == "value":
        chart.series[rng.randrange(series)].set_value(rng.randrange(count),
                                                      rng.choice([None, 1.5, 42]))
    elif action == "category":
        chart.set_category(rng.randrange(count), 200 + step if numeric else f"L{step}")
    elif action == "name":
        chart.series[rng.randrange(series)].name = f"N{step}"


@pytest.mark.parametrize("seed", range(3))
@pytest.mark.parametrize("name", CHARTS)
def test_a_random_walk_keeps_cache_and_workbook_in_step(name, seed):
    document = opened()
    rng = random.Random(f"{name}/{seed}")
    for step in range(30):
        chart = document.chart(name)
        before = document.to_bytes()
        try:
            _random_step(chart, rng, step)
        except ChartDataError:
            assert document.to_bytes() == before  # refused before anything changed
            continue
        data = document.to_bytes()
        chart = document.chart(name)
        for series in chart.series:
            assert len(series.values) == chart.point_count
        assert len(chart.categories) == chart.point_count
        workbook = chart.workbook_part
        check_chart_against_workbook(document.package.read(chart.part),
                                     document.package.read(workbook))
        assert not chart_order_violations(document.package.read(chart.part))
        assert cs.Opened(data).chart(name).data == chart.data
    cs.assert_valid(document.to_bytes())
    document.undo_all()
    assert cs.parts(document.to_bytes()) == cs.parts(DATA)


# ------------------------------------------------------------------------------------------
# A new series' c16:uniqueId, as Word and PowerPoint give one on saving
# ------------------------------------------------------------------------------------------

C16 = "http://schemas.microsoft.com/office/drawing/2014/chart"
UNIQUE_ID = re.compile(r"\{00000000-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}")


def _ids(document, chart) -> list[str | None]:
    root = etree.fromstring(document.package.read(chart.part))
    out = []
    for ser in root.iter(C + "ser"):
        node = next(ser.iter("{%s}uniqueId" % C16), None)
        out.append(None if node is None else node.get("val"))
    return out


@pytest.mark.parametrize("name", CHARTS)
def test_a_new_series_gets_a_unique_id_and_the_others_keep_theirs(name):
    document = opened()
    chart = document.chart(name)
    before = _ids(document, chart)
    count = chart.point_count
    chart.add_series("Added", [1] * count)
    chart.add_series("Front", [2] * count, index=0)
    after = _ids(document, chart)
    added = [value for value in after if value not in before]
    assert len(added) == 2 and len(set(added)) == 2
    assert all(UNIQUE_ID.fullmatch(value) for value in added)
    # The series that were there are left as they were, with an id or without one.
    assert [value for value in after if value not in added] == before
    root = etree.fromstring(document.package.read(chart.part))
    for ser in root.iter(C + "ser"):
        node = next(ser.iter("{%s}uniqueId" % C16), None)
        if node is None or node.get("val") not in added:
            continue
        ext = node.getparent()
        assert etree.QName(ext).localname == "ext" and ext.get("uri") == \
            "{C3380CC4-5D6E-409C-BE32-E72D297353CC}"
        assert ext.getparent() is ser[-1]  # c:extLst, the series' last child
    check_charts(document.to_bytes())


def test_the_ids_are_deterministic_and_step_past_one_already_taken():
    first, second = opened(), opened()
    for document in (first, second):
        chart = document.chart("bar")
        chart.add_series("Added", [1] * chart.point_count)
    assert first.to_bytes() == second.to_bytes()
    value = _ids(first, first.chart("bar"))[-1]
    # The same edit on a chart whose first series already holds that id writes another.
    third = opened()
    chart = third.chart("bar")
    root = third.package.tree(chart.part)
    ser = next(root.iter(C + "ser"))
    extensions = etree.SubElement(ser, C + "extLst")
    ext = etree.SubElement(extensions, C + "ext", nsmap={"c16": C16})
    ext.set("uri", "{C3380CC4-5D6E-409C-BE32-E72D297353CC}")
    etree.SubElement(ext, "{%s}uniqueId" % C16).set("val", value)
    chart.add_series("Added", [1] * chart.point_count)
    ids = _ids(third, chart)
    assert ids[0] == value and ids[-1] != value and UNIQUE_ID.fullmatch(ids[-1])


def test_a_series_added_by_a_model_gets_an_id_and_undo_takes_it_away():
    document = opened()
    chart = document.chart("line")
    from ooxml_edit.charts.model import apply_chart_model, canonical_chart

    model = chart.data
    model["series"].append({"name": "Modelled", "values": [1] * len(model["categories"])})
    apply_chart_model(chart, canonical_chart(model, "line"), "line")
    assert UNIQUE_ID.fullmatch(_ids(document, chart)[-1])
    edited = document.to_bytes()
    document.undo_all()
    assert cs.parts(document.to_bytes()) == cs.parts(DATA)
    document.redo_all()
    assert document.to_bytes() == edited


# ------------------------------------------------------------------------------------------
# workbook_values: what Edit Data shows, read back
# ------------------------------------------------------------------------------------------


def _agrees(chart) -> bool:
    book = chart.workbook_values()
    categories = book["categories"]
    return (categories is None or categories["values"] == chart.categories) and \
        [s["values"]["values"] for s in book["series"]] == [s.values for s in chart.series] and \
        [s["name"]["value"] for s in book["series"]] == [s.name for s in chart.series]


@pytest.mark.parametrize("name", CHARTS)
def test_the_workbook_values_agree_with_the_cache(name):
    document = opened()
    chart = document.chart(name)
    book = chart.workbook_values()
    assert book["workbook"] == chart.workbook_part and "missing" not in book
    assert book["sheet"] == "Sheet1"
    assert all(s["values"]["ref"].startswith("Sheet1!") for s in book["series"])
    assert _agrees(chart)


def test_the_workbook_values_name_their_formulas():
    book = opened().chart("bar").workbook_values()
    assert book["categories"] == {"ref": "Sheet1!$A$2:$A$5", "values": ["Q1", "Q2", "Q3", "Q4"]}
    assert book["series"][0] == {"name": {"ref": "Sheet1!$B$1", "value": "North"},
                                 "values": {"ref": "Sheet1!$B$2:$B$5", "values": [10, 12.5, 9, 14]}}


def test_reading_the_workbook_changes_nothing_and_follows_every_edit():
    document = opened()
    chart = document.chart("bar")
    before = document.to_bytes()
    chart.workbook_values()
    assert document.to_bytes() == before
    chart.series[0].set_value(1, 999)
    chart.add_category("Q5", [1, 2])
    chart.series[1].name = "Southeast"
    book = chart.workbook_values()
    assert book["categories"]["ref"] == "Sheet1!$A$2:$A$6"
    assert book["series"][0]["values"]["values"] == [10, 999, 9, 14, 1]
    assert book["series"][1]["name"]["value"] == "Southeast"
    assert _agrees(chart)
    document.undo_all()
    assert chart.workbook_values()["series"][1]["name"]["value"] == "South"


def test_scatter_and_bubble_values_are_read_through_their_x_and_y():
    for name in ("scatter", "bubble"):
        assert _agrees(opened().chart(name))


@pytest.mark.parametrize("name", list(REASONS))
def test_without_a_workbook_the_values_say_why(name):
    book = opened().chart(name).workbook_values()
    assert book == {"workbook": None, "sheet": None, "categories": None, "series": [],
                    "missing": REASONS[name]}


def test_what_cannot_be_read_is_none_not_a_guess():
    document = opened()
    chart = document.chart("bar")
    root = document.package.tree(chart.part)
    formulas = list(root.iter(f"{C}f"))
    formulas[0].text = "Sheet1!$B$1"                       # still one range: kept
    by_text = {f.text: f for f in formulas}
    by_text["Sheet1!$B$2:$B$5"].text = "Nowhere!$B$2:$B$5"  # a sheet the workbook lacks
    by_text["Sheet1!$C$2:$C$5"].text = "(Sheet1!$C$2,Sheet1!$C$4)"  # not one range
    book = chart.workbook_values()
    assert book["series"][0]["values"] == {"ref": "Nowhere!$B$2:$B$5", "values": None}
    assert book["series"][1]["values"] == {"ref": "(Sheet1!$C$2,Sheet1!$C$4)", "values": None}
    assert book["sheet"] == "Nowhere"


def test_a_rectangle_reads_as_rows():
    document = opened()
    chart = document.chart("bar")
    root = document.package.tree(chart.part)
    category = next(f for f in root.iter(f"{C}f") if f.text == "Sheet1!$A$2:$A$5")
    category.text = "Sheet1!$A$2:$B$3"
    assert chart.workbook_values()["categories"] == {
        "ref": "Sheet1!$A$2:$B$3", "values": [["Q1", 10], ["Q2", 12.5]]}
