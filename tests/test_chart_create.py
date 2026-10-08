"""A new chart from data (``ooxml_edit.charts.create``), and the formatting edits LP8 needs.

Each kind is created in the synthetic package behind a test frame, then held to the same
gates as an edited chart: it reads back as given, the embedded workbook -- opened with the
independent reader in ``tests/xlsx.py`` -- holds exactly what every cache holds, the chart
part is in schema order, the package is valid, and the existing edits keep working on it.
"""

from __future__ import annotations

import pytest
from lxml import etree

import charts_synthetic as cs
from ooxml_edit.charts import Chart, ChartDataError, GraphicHost
from ooxml_edit.charts.create import (CHART_KINDS, POWERPOINT_LOOK, WORD_LOOK, add_chart,
                                      chart_space, chart_workbook, series_color)
from test_chart import chart_order_violations
from xlsx import check_chart_against_workbook

C = "{%s}" % cs.C
A = "{%s}" % cs.A
REGIONS = [{"name": "North", "values": [12.4, 13.1, 14.0, 15.2]},
           {"name": "South", "values": [9.8, 10.2, None, 11.9]},
           {"name": "East", "values": [7.1, 7.4, 8.0, 8.3]}]
QUARTERS = ["Q1", "Q2", "Q3", "Q4"]


def data_for(kind: str):
    if kind == "pie":
        return QUARTERS, REGIONS[:1]
    if kind == "scatter":
        return [1, 2, 3.5, 5], REGIONS[:2]
    return QUARTERS, REGIONS


def new(kind: str, **options) -> tuple[cs.Opened, Chart]:
    """A fresh synthetic package with a chart of ``kind`` added in a frame named ``new``."""
    document = cs.Opened(cs.package())
    categories, series = data_for(kind)
    with document.history.batch():
        made = add_chart(document.package, cs.MAIN, kind, categories, series, **options)
        root = document.package.tree(cs.MAIN)
        frame = etree.SubElement(root, "{%s}frame" % cs.NS, name="new")
        frame.append(made.graphic())
        document.package.mark_dirty(cs.MAIN)
    return document, document.chart("new")


@pytest.mark.parametrize("kind", list(CHART_KINDS))
def test_a_new_chart_reads_back_and_its_workbook_matches(kind):
    titles = {} if kind == "pie" else {"category": "Quarter", "value": "€m"}
    document, chart = new(kind, title="Revenue by region", axis_titles=titles,
                          number_format="#,##0.0")
    categories, series = data_for(kind)
    assert chart.categories == categories
    assert [s.name for s in chart.series] == [s["name"] for s in series]
    assert [s.values for s in chart.series] == [s["values"] for s in series]
    assert chart.title == "Revenue by region"
    assert chart.number_format == "#,##0.0"
    for axis, text in titles.items():
        assert chart.axis_title(axis) == text
    assert chart.has_legend

    book = chart.workbook_values()
    assert book["sheet"] == "Sheet1"
    assert book["categories"]["values"] == categories
    assert [s["values"]["values"] for s in book["series"]] == [s["values"] for s in series]
    assert [s["name"]["value"] for s in book["series"]] == [s["name"] for s in series]

    data = document.to_bytes()
    cs.assert_valid(data)
    reopened = cs.Opened(data)
    part = reopened.chart("new").part
    workbook = reopened.chart("new").workbook_part
    assert check_chart_against_workbook(reopened.package.read(part),
                                        reopened.package.read(workbook)) > 0
    assert not chart_order_violations(reopened.package.read(part))


def test_the_parts_are_named_as_office_names_them_with_the_next_free_number():
    document, chart = new("column")
    taken = [name for name in cs.parts(cs.package()) if name.startswith("doc/charts/chart")]
    assert chart.part == f"doc/charts/chart{len(taken) + 1}.xml"
    assert chart.workbook_part.startswith("doc/embeddings/Microsoft_Excel_Worksheet")
    with document.history.batch():
        made = add_chart(document.package, cs.MAIN, "line", QUARTERS, REGIONS)
    assert made.part == f"doc/charts/chart{len(taken) + 2}.xml"
    assert made.workbook != chart.workbook_part
    assert document.package.content_type(made.part) == \
        "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"


def test_the_same_chart_is_the_same_bytes():
    first = new("column", title="T")[0].to_bytes()
    assert first == new("column", title="T")[0].to_bytes()


def test_adding_a_chart_is_undone_to_the_original_bytes():
    document, _ = new("column", title="T")
    document.undo_all()
    assert document.to_bytes() == cs.package()


@pytest.mark.parametrize("kind", ["column", "line", "stacked_bar", "radar"])
def test_the_existing_edits_work_on_a_new_chart(kind):
    document, chart = new(kind)
    chart.add_category("Q5", [16.0, 12.5, 9.1])
    chart.add_series("West", [1, 2, 3, 4, 5])
    chart.remove_series("South")
    chart.series[0].set_value(1, 99)
    chart.set_title("Changed")
    book = chart.workbook_values()
    assert book["categories"]["values"] == QUARTERS + ["Q5"]
    assert [s["values"]["values"] for s in book["series"]] == [s.values for s in chart.series]
    data = document.to_bytes()
    cs.assert_valid(data)
    reopened = cs.Opened(data).chart("new")
    assert check_chart_against_workbook(cs.Opened(data).package.read(reopened.part),
                                        cs.Opened(data).package.read(reopened.workbook_part))


def test_office_s_measured_look():
    space = chart_space("column", QUARTERS, REGIONS, title="T", look=POWERPOINT_LOOK)
    xml = etree.tostring(space).decode()
    assert '<c:gapWidth val="219"/>' in xml and '<c:overlap val="-27"/>' in xml
    assert '<c:legendPos val="b"/>' in xml and '<c:roundedCorners val="0"/>' in xml
    assert 'sz="1862"' in xml and 'sz="1197"' in xml
    fills = [f.get("val") for f in space.iter(A + "schemeClr")
             if f.getparent().getparent().getparent().tag == C + "ser"]
    assert fills == ["accent1", "accent2", "accent3"]
    word = etree.tostring(chart_space("column", QUARTERS, REGIONS, title="T",
                                      look=WORD_LOOK)).decode()
    assert 'sz="1400"' in word and 'sz="900"' in word and '<a:schemeClr val="bg1"/>' in word
    bars = etree.tostring(chart_space("bar", QUARTERS, REGIONS)).decode()
    assert '<c:gapWidth val="182"/>' in bars and "overlap" not in bars
    stacked = etree.tostring(chart_space("stacked_column", QUARTERS, REGIONS)).decode()
    assert '<c:grouping val="stacked"/>' in stacked and '<c:overlap val="100"/>' in stacked


def test_office_s_measured_radar():
    """PowerPoint's and Word's Insert > Chart > Radar (Office for Mac 16): lines in the
    "marker" style with the markers off, the column chart's axes, the legend at the top."""
    space = chart_space("radar", QUARTERS, REGIONS, look=POWERPOINT_LOOK)
    plot = space.find(f"{C}chart/{C}plotArea/{C}radarChart")
    assert [etree.QName(child).localname for child in plot] == [
        "radarStyle", "varyColors", "ser", "ser", "ser", "dLbls", "axId", "axId"]
    assert plot.find(f"{C}radarStyle").get("val") == "marker"
    for index, series in enumerate(plot.findall(f"{C}ser")):
        line = series.find(f"{C}spPr/{A}ln")
        assert (line.get("w"), line.get("cap")) == ("28575", "rnd")
        assert line.find(f"{A}solidFill/{A}schemeClr").get("val") == f"accent{index + 1}"
        assert series.find(f"{C}spPr/{A}solidFill") is None
        assert series.find(f"{C}marker/{C}symbol").get("val") == "none"
        assert series.find(f"{C}smooth") is None
    category, value = space.find(f"{C}chart/{C}plotArea/{C}catAx"), \
        space.find(f"{C}chart/{C}plotArea/{C}valAx")
    assert category.find(f"{C}majorGridlines") is None
    assert category.find(f"{C}spPr/{A}ln").get("w") == "9525"
    assert value.find(f"{C}majorGridlines") is not None
    assert value.find(f"{C}spPr/{A}ln/{A}noFill") is not None
    assert value.find(f"{C}crossBetween").get("val") == "between"
    assert space.find(f"{C}chart/{C}legend/{C}legendPos").get("val") == "t"
    word = chart_space("radar", QUARTERS, REGIONS, look=WORD_LOOK)
    assert word.find(f"{C}spPr/{A}solidFill/{A}schemeClr").get("val") == "bg1"


@pytest.mark.parametrize("kind, position", [("radar", "t"), ("line", "b"), ("pie", "b")])
def test_the_default_legend_is_office_s_for_the_kind(kind, position):
    categories, series = data_for(kind)
    space = chart_space(kind, categories, series)
    assert space.find(f"{C}chart/{C}legend/{C}legendPos").get("val") == position
    placed = chart_space(kind, categories, series, legend="right")
    assert placed.find(f"{C}chart/{C}legend/{C}legendPos").get("val") == "r"
    assert chart_space(kind, categories, series, legend=None).find(
        f"{C}chart/{C}legend") is None


def test_data_labels_on_a_radar_chart():
    document, chart = new("radar")
    chart.set_data_labels(True, number_format="0.0")
    assert all(entry["shown"] for entry in chart.data_labels)
    assert not chart_order_violations(document.package.read(chart.part))
    cs.assert_valid(document.to_bytes())
    assert chart.chart_type == "radar" and chart.gap_width is None


def test_the_colour_cycle_follows_office_after_six_series():
    assert series_color(0).get("val") == "accent1"
    seventh = series_color(6)
    assert seventh.get("val") == "accent1"
    assert [(etree.QName(m).localname, m.get("val")) for m in seventh] == [("lumMod", "60000")]


def test_a_chart_with_no_title_says_so():
    _, chart = new("column")
    assert chart.title is None
    root = chart._root()
    assert root.find(f"{C}chart/{C}autoTitleDeleted").get("val") == "1"


@pytest.mark.parametrize("kind, categories, series, message", [
    ("donut", QUARTERS, REGIONS, "chart type"),
    ("radar_filled", QUARTERS, REGIONS, "chart type"),
    ("column", [], REGIONS, "at least one category"),
    ("column", QUARTERS, [], "at least one series"),
    ("column", QUARTERS, [{"name": "A", "values": [1, 2]}], "2 values for 4"),
    ("column", QUARTERS, [{"name": "A", "values": [1, 2, float("nan"), 4]}], "finite"),
    ("pie", QUARTERS, REGIONS, "one series"),
    ("scatter", QUARTERS, REGIONS, "x value"),
])
def test_data_that_cannot_be_charted_is_refused(kind, categories, series, message):
    document = cs.Opened(cs.package())
    with pytest.raises(ChartDataError, match=message):
        add_chart(document.package, cs.MAIN, kind, categories, series)
    assert document.to_bytes() == cs.package()


def test_the_workbook_carries_the_number_format():
    book = chart_workbook("column", QUARTERS, REGIONS, number_format='"€"#,##0.0"m"')
    from xlsx import Book

    assert Book(book).sheet("Sheet1").range("B2", "B2") == [12.4]
    import io
    import zipfile

    styles = etree.fromstring(zipfile.ZipFile(io.BytesIO(book)).read("xl/styles.xml"))
    (code,) = [n.get("formatCode") for n in styles.iter("{%s}numFmt" % cs.X)]
    assert code == '"€"#,##0.0"m"'
    cells = etree.fromstring(zipfile.ZipFile(io.BytesIO(book)).read("xl/worksheets/sheet1.xml"))
    assert {c.get("s") for c in cells.iter("{%s}c" % cs.X) if c.get("r")[0] != "A"
            and c.get("r")[1:] != "1"} == {"1"}


# -- formatting ------------------------------------------------------------------------------


def test_data_labels_show_values_in_a_number_format_and_undo():
    document, chart = new("column")
    before = document.to_bytes()
    chart.set_data_labels(True, number_format='"€"#,##0.0"m"')
    assert chart.data_labels == [{"series": i, "shown": True, "format": '"€"#,##0.0"m"'}
                                 for i in range(3)]
    labels = chart._root().find(f"{C}chart/{C}plotArea/{C}barChart/{C}ser/{C}dLbls")
    assert labels.find(f"{C}txPr//{A}defRPr").get("sz") == "1197"
    assert not chart_order_violations(document.package.read(chart.part))
    chart.set_data_labels(False, series=[1])
    assert [entry["shown"] for entry in chart.data_labels] == [True, False, True]
    cs.assert_valid(document.to_bytes())
    document.history.undo()
    document.history.undo()
    assert document.to_bytes() == before


def test_data_labels_are_refused_a_format_when_hidden():
    _, chart = new("column")
    with pytest.raises(ChartDataError):
        chart.set_data_labels(False, number_format="0.0")
    with pytest.raises(IndexError):
        chart.set_data_labels(True, series=[7])


def test_gap_width():
    document, chart = new("column")
    assert chart.gap_width == 219
    chart.set_gap_width(60)
    assert chart.gap_width == 60
    assert not chart_order_violations(document.package.read(chart.part))
    with pytest.raises(ChartDataError):
        chart.set_gap_width(600)
    _, line = new("line")
    assert line.gap_width is None
    with pytest.raises(ChartDataError, match="only bar and column"):
        line.set_gap_width(50)


def test_the_host_hook_needs_nothing_format_specific():
    document, chart = new("pie", title="Share")
    host = document.host("new")
    assert isinstance(host, GraphicHost)
    assert chart.chart_type == "pie" and chart.axes == []


def test_a_host_s_look_sizes_new_titles_and_data_labels():
    document, _ = new("bar")
    chart = document.chart("new", look=WORD_LOOK)
    chart.set_title("Chart")
    chart.set_axis_title("category", "Region")    # vertical on a bar chart
    chart.set_axis_title("value", "EUR m")        # horizontal
    chart.set_data_labels(True)
    root = chart._root()
    sizes = {etree.QName(t.getparent()).localname if t.getparent().tag != C + "chart" else "chart":
             t.find(f"{C}tx//{A}defRPr").get("sz") for t in root.iter(C + "title")}
    assert sizes == {"chart": "1400", "catAx": "1000", "valAx": "1000"}
    assert {n.get("sz") for n in root.iter(A + "defRPr")
            if n.getparent().getparent().getparent().getparent().tag == C + "dLbls"} == {"900"}
