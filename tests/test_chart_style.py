"""A chart's look (``ooxml_edit.charts.styling``, ``.format``, ``.actions``) and the new chart
kinds and options of ``add_chart``: axis scales, gridlines, series and point colours and
markers, combination charts, chart text, point labels (Value From Cells), filled radar and
bubble charts.

Every edit is held to the gates the data edits are held to: schema order in the chart part,
a valid package, the workbook equal to the caches, one undo step back to the same bytes, and
the same bytes for the same edit.  The Office facts the writing follows are measured on
Office for Mac 16 (see the docstrings of ``create`` and ``format``).
"""

from __future__ import annotations

import pytest
from lxml import etree

import charts_synthetic as cs
from ooxml_edit.charts import ChartDataError
from ooxml_edit.charts.actions import (add_style_actions, apply_add_style, apply_style_action,
                                       chart_style_json, gridline_arguments, scale_arguments)
from ooxml_edit.charts.format import FormatError, color_element
from test_chart import chart_order_violations
from test_chart_create import QUARTERS, new
from xlsx import Book, check_chart_against_workbook

C = "{%s}" % cs.C
A = "{%s}" % cs.A
C15 = "{http://schemas.microsoft.com/office/drawing/2012/chart}"
MATURITY = ["Governance", "People", "Data", "Technology", "Procure"]
CURRENT = [2.13, 2.10, 2.17, 2.33, 2.08]
TARGET = [4, 3, 4, 3, 4]
X = [1.0, 2.0, 4.0, 1.5, 3.5, 4.5]
Y = [4.5, 3.0, 4.0, 1.5, 2.0, 1.0]
R = ["R1", "R2", "R3", "R4", "R5", "R6"]


def gap_chart(**options):
    document = cs.Opened(cs.package())
    from ooxml_edit.charts.create import add_chart

    with document.history.batch():
        made = add_chart(document.package, cs.MAIN, "column", MATURITY,
                         [{"name": "Current", "values": CURRENT},
                          {"name": "Target", "values": TARGET}], **options)
        frame = etree.SubElement(document.package.tree(cs.MAIN), "{%s}frame" % cs.NS,
                                 name="new")
        frame.append(made.graphic())
        document.package.mark_dirty(cs.MAIN)
    return document, document.chart("new")


def xy_chart(kind="scatter", **options):
    document = cs.Opened(cs.package())
    from ooxml_edit.charts.create import add_chart

    series = [{"name": "Recs", "values": Y}]
    if kind == "bubble":
        series[0]["size"] = [3, 2, 3, 1, 2, 1]
    with document.history.batch():
        made = add_chart(document.package, cs.MAIN, kind, X, series, **options)
        frame = etree.SubElement(document.package.tree(cs.MAIN), "{%s}frame" % cs.NS,
                                 name="new")
        frame.append(made.graphic())
        document.package.mark_dirty(cs.MAIN)
    return document, document.chart("new")


def gates(document, chart) -> None:
    """Schema order, a valid package, and the workbook equal to the caches."""
    data = document.to_bytes()
    cs.assert_valid(data)
    reopened = cs.Opened(data)
    part = reopened.package.read(chart.part)
    assert not chart_order_violations(part), chart_order_violations(part)
    assert check_chart_against_workbook(part, reopened.package.read(chart.workbook_part)) > 0


def root(chart):
    return chart._root()


def axis(chart, tag):
    return root(chart).find(f"{C}chart/{C}plotArea/{C}{tag}")


# -- colours ---------------------------------------------------------------------------------


@pytest.mark.parametrize("spec, expected", [
    ("tx2", ("schemeClr", "tx2", [])),
    ("accent1 lumMod=60% lumOff=40%",
     ("schemeClr", "accent1", [("lumMod", "60000"), ("lumOff", "40000")])),
    ("#1f4e79", ("srgbClr", "1F4E79", [])),
    ("1F4E79 alpha=50000", ("srgbClr", "1F4E79", [("alpha", "50000")])),
])
def test_colours_are_written_as_the_deck_takes_them(spec, expected):
    element = color_element(spec)
    assert etree.QName(element).namespace == cs.A
    assert (local(element), element.get("val"),
            [(local(n), n.get("val")) for n in element]) == expected


@pytest.mark.parametrize("spec", ["", "blue", "accent9", "accent1 lumMod", "accent1 glow=3"])
def test_a_bad_colour_is_refused(spec):
    with pytest.raises(FormatError):
        color_element(spec)


# -- G1: axis scales -------------------------------------------------------------------------


def test_a_fixed_value_axis_reads_back_and_goes_back_to_automatic():
    document, chart = gap_chart()
    before = document.to_bytes()
    chart.set_axis_scale("value", minimum=0, maximum=5, major_unit=1)
    scale = chart.axis_scale("value")
    assert (scale["minimum"], scale["maximum"], scale["major_unit"]) == (0, 5, 1)
    assert scale["minor_unit"] is None and scale["reverse"] is False
    value = axis(chart, "valAx")
    assert [local(n) for n in value.find(f"{C}scaling")] == ["orientation", "max", "min"]
    assert value.find(f"{C}majorUnit").get("val") == "1"
    gates(document, chart)
    chart.set_axis_scale("value", maximum=None, major_unit=None)
    scale = chart.axis_scale("value")
    assert scale["maximum"] is None and scale["major_unit"] is None and scale["minimum"] == 0
    chart.set_axis_scale("value", minimum=None)
    document.history.undo()
    document.history.undo()
    document.history.undo()
    assert document.to_bytes() == before


def local(node):
    return etree.QName(node).localname


def test_a_scale_is_checked_before_anything_changes():
    document, chart = gap_chart()
    before = document.to_bytes()
    with pytest.raises(FormatError, match="below the maximum"):
        chart.set_axis_scale("value", minimum=5, maximum=1)
    with pytest.raises(FormatError, match="no numeric bounds"):
        chart.set_axis_scale("category", minimum=0)
    with pytest.raises(FormatError, match="greater than 0"):
        chart.set_axis_scale("value", major_unit=0)
    with pytest.raises(FormatError, match="minor unit"):
        chart.set_axis_scale("value", major_unit=1, minor_unit=2)
    with pytest.raises(FormatError, match="no numeric bounds"):
        chart.set_axis_scale("x", maximum=3)  # a column chart's x axis is its categories
    with pytest.raises(FormatError, match="has no value axis"):
        new("pie")[1].set_axis_scale("value", maximum=3)
    assert document.to_bytes() == before



def test_x_and_y_name_the_horizontal_and_vertical_axes():
    document, chart = xy_chart()
    assert chart.axis_names() == ["x", "y"]
    chart.set_axis_scale("x", minimum=0, maximum=5)
    chart.set_axis_scale("y", minimum=0, maximum=5, number_format="0.0")
    axes = root(chart).findall(f"{C}chart/{C}plotArea/{C}valAx")
    horizontal = next(a for a in axes if a.find(f"{C}axPos").get("val") == "b")
    assert horizontal.find(f"{C}scaling/{C}max").get("val") == "5"
    assert chart.scales["y"]["number_format"] == "0.0"
    gates(document, chart)
    # On a bar chart x is the value axis: the horizontal one.
    document, chart = new("bar")
    chart.set_axis_scale("x", maximum=40)
    assert chart.axis_scale("value")["maximum"] == 40


def test_reversed_categories_keep_the_value_axis_where_it_was():
    """PowerPoint, measured: maxMin alone moves the value axis to the top (and rewrites
    axPos t); with the value axis crossing at the maximum category it stays at the bottom."""
    document, chart = new("bar")
    chart.set_axis_scale("category", reverse=True)
    assert axis(chart, "catAx").find(f"{C}scaling/{C}orientation").get("val") == "maxMin"
    value = axis(chart, "valAx")
    assert value.find(f"{C}crosses").get("val") == "max"
    assert value.find(f"{C}axPos").get("val") == "b"
    assert chart.axis_scale("category")["reverse"] is True
    gates(document, chart)
    chart.set_axis_scale("category", reverse=False)
    assert axis(chart, "valAx").find(f"{C}crosses").get("val") == "autoZero"


def test_a_reversed_category_order_on_a_new_chart():
    document, chart = new("bar", category_order="reversed")
    assert chart.axis_scale("category")["reverse"]
    assert axis(chart, "valAx").find(f"{C}crosses").get("val") == "max"
    assert chart.categories == QUARTERS  # the data and the workbook keep their order
    assert chart.workbook_values()["categories"]["values"] == QUARTERS
    gates(document, chart)


def test_a_number_format_on_an_axis_and_back_to_the_values():
    document, chart = gap_chart(number_format="0.00")
    chart.set_axis_scale("value", number_format="0")
    fmt = axis(chart, "valAx").find(f"{C}numFmt")
    assert (fmt.get("formatCode"), fmt.get("sourceLinked")) == ("0", "0")
    chart.set_axis_scale("value", number_format=None)
    fmt = axis(chart, "valAx").find(f"{C}numFmt")
    assert (fmt.get("formatCode"), fmt.get("sourceLinked")) == ("0.00", "1")


# -- gridlines -------------------------------------------------------------------------------


def test_gridlines_on_and_off_in_offices_style():
    document, chart = gap_chart()
    chart.set_gridlines("value", major=False, minor=True)
    value = axis(chart, "valAx")
    assert value.find(f"{C}majorGridlines") is None
    minor = value.find(f"{C}minorGridlines")
    assert minor.find(f".//{A}lumMod").get("val") == "5000"  # measured
    chart.set_gridlines("category", major=True)
    assert axis(chart, "catAx").find(f"{C}majorGridlines//{A}lumMod").get("val") == "15000"
    assert chart.axis_scale("category")["major_gridlines"]
    gates(document, chart)


def test_a_new_chart_without_gridlines():
    for kind in ("column", "scatter"):
        document, chart = new(kind, gridlines=False)
        assert not list(root(chart).iter(f"{C}majorGridlines"))
        gates(document, chart)


# -- G2: series and point formatting ---------------------------------------------------------


def test_a_series_fill_and_one_highlighted_point():
    document, chart = gap_chart()
    chart.format_series("Current", fill="tx2")
    chart.format_point("Current", "Procure", fill="accent2")
    ser = root(chart).find(f".//{C}ser")
    assert ser.find(f"{C}spPr/{A}solidFill/{A}schemeClr").get("val") == "tx2"
    point = ser.find(f"{C}dPt")
    assert [local(n) for n in point] == ["idx", "invertIfNegative", "bubble3D", "spPr"]
    assert point.find(f"{C}idx").get("val") == "4"
    assert point.find(f"{C}spPr/{A}solidFill/{A}schemeClr").get("val") == "accent2"
    assert point.find(f"{C}spPr/{A}ln/{A}noFill") is not None  # from the series' look
    look = chart.series_format("Current")
    assert look["fill"] == "tx2" and look["points"] == {4: {"fill": "accent2", "line": None}}
    gates(document, chart)
    # The point keeps its format when a category is inserted before it.
    chart.add_category("New", [1, 2], index=0)
    assert root(chart).find(f".//{C}dPt/{C}idx").get("val") == "5"
    chart.format_point("Current", 5, fill="accent3")
    assert len(root(chart).findall(f".//{C}dPt")) == 1
    gates(document, chart)


def test_lines_dashes_and_markers():
    document, chart = new("line")
    chart.format_series("North", line="accent2 lumMod=75%", line_width=1.5, dash="dash",
                        marker="diamond", marker_size=8, marker_fill="FF0000")
    look = chart.series_format("North")
    assert look == {"line": "accent2 lumMod=75%", "line_width": 1.5, "dash": "dash",
                    "marker": "diamond", "marker_size": 8, "marker_fill": "FF0000",
                    "marker_line": "FF0000"}
    ser = root(chart).find(f".//{C}ser")
    assert [local(n) for n in ser.find(f"{C}spPr/{A}ln")] == ["solidFill", "prstDash", "round"]
    assert [local(n) for n in ser.find(f"{C}marker")] == ["symbol", "size", "spPr"]
    chart.format_series("North", line=None, marker_fill=None)
    assert chart.series_format("North")["line"] is None
    assert chart.series_format("North")["marker_fill"] is None
    chart.format_series("North", line_width=3)  # the colour stays as it is
    assert chart.series_format("North")["line_width"] == 3
    gates(document, chart)


@pytest.mark.parametrize("kind, arguments, message", [
    ("line", {"fill": "accent1"}, "is a line"),
    ("column", {"marker": "circle"}, "markers"),
    ("column", {"fill": "nonsense"}, "neither"),
    ("line", {"marker": "picture"}, "marker"),
    ("line", {"marker_size": 100}, "2-72"),
    ("column", {"dash": "wavy"}, "dash"),
    ("column", {}, "give something"),
])
def test_series_formatting_is_checked_before_anything_changes(kind, arguments, message):
    document, chart = new(kind)
    before = document.to_bytes()
    with pytest.raises(FormatError, match=message):
        chart.format_series("North", **arguments)
    assert document.to_bytes() == before


def test_a_point_by_a_label_no_category_has():
    document, chart = gap_chart()
    with pytest.raises(KeyError, match="no category"):
        chart.format_point("Current", "Nowhere", fill="accent1")


# -- G3: combination charts ------------------------------------------------------------------


def test_a_series_drawn_as_a_line_shares_the_axes_and_keeps_its_colour_and_cells():
    document, chart = gap_chart()
    book = chart.workbook_values()
    chart.set_series_type("Target", "line")
    assert chart.chart_types == ["bar", "line"]
    plot_area = root(chart).find(f"{C}chart/{C}plotArea")
    bars, lines = plot_area.find(f"{C}barChart"), plot_area.find(f"{C}lineChart")
    assert [n.get("val") for n in lines.findall(f"{C}axId")] == \
        [n.get("val") for n in bars.findall(f"{C}axId")]
    ser = lines.find(f"{C}ser")
    assert ser.find(f"{C}spPr/{A}ln/{A}solidFill/{A}schemeClr").get("val") == "accent2"
    assert ser.find(f"{C}marker/{C}symbol").get("val") == "none"
    assert ser.find(f"{C}invertIfNegative") is None
    assert chart.workbook_values() == book
    assert [s.name for s in chart.series] == ["Current", "Target"]
    chart.format_series("Target", line=None, marker="diamond", marker_size=10,
                        marker_fill="accent6")
    gates(document, chart)
    chart.set_series_type("Target", "column")
    assert chart.chart_types == ["bar"]
    assert plot_area.find(f"{C}lineChart") is None
    gates(document, chart)


def test_a_line_on_a_secondary_axis_as_powerpoint_builds_it():
    document, chart = gap_chart()
    chart.set_series_type("Target", "line", secondary_axis=True)
    plot_area = root(chart).find(f"{C}chart/{C}plotArea")
    axes = [local(a) for a in plot_area if local(a).endswith("Ax")]
    assert axes == ["catAx", "valAx", "valAx", "catAx"]  # measured order
    secondary = plot_area.findall(f"{C}valAx")[1]
    assert secondary.find(f"{C}axPos").get("val") == "r"
    assert secondary.find(f"{C}crosses").get("val") == "max"
    assert plot_area.findall(f"{C}catAx")[1].find(f"{C}delete").get("val") == "1"
    gates(document, chart)
    chart.set_series_type("Target", "column")
    assert [local(a) for a in plot_area if local(a).endswith("Ax")] == ["catAx", "valAx"]
    gates(document, chart)


@pytest.mark.parametrize("kind, message", [("bar", "horizontal bars"), ("pie", "column chart"),
                                           ("radar", "column chart")])
def test_only_a_column_chart_takes_a_line(kind, message):
    document, chart = new(kind)
    with pytest.raises(ChartDataError, match=message):
        chart.set_series_type(0, "line")


def test_a_new_combination_chart_as_office_inserts_one():
    """Measured (Insert > Chart > Combo > Clustered Column - Line): gap width 150 and no
    overlap; the line plot on the same axes, markers on, no c:crosses on either axis."""
    document = cs.Opened(cs.package())
    from ooxml_edit.charts.create import add_chart

    with document.history.batch():
        made = add_chart(document.package, cs.MAIN, "column", MATURITY,
                         [{"name": "Current", "values": CURRENT},
                          {"name": "Target", "values": TARGET, "type": "line"}])
        frame = etree.SubElement(document.package.tree(cs.MAIN), "{%s}frame" % cs.NS,
                                 name="new")
        frame.append(made.graphic())
        document.package.mark_dirty(cs.MAIN)
    chart = document.chart("new")
    assert chart.chart_types == ["bar", "line"]
    bars = root(chart).find(f".//{C}barChart")
    assert bars.find(f"{C}gapWidth").get("val") == "150" and bars.find(f"{C}overlap") is None
    lines = root(chart).find(f".//{C}lineChart")
    assert lines.find(f"{C}marker").get("val") == "1"
    assert not list(root(chart).iter(f"{C}crosses"))
    assert [s.name for s in chart.series] == ["Current", "Target"]
    gates(document, chart)


# -- G3: text --------------------------------------------------------------------------------


def test_all_chart_text_at_one_size():
    document, chart = new("column", title="T", axis_titles={"value": "€m"})
    chart.set_data_labels(True)
    chart.set_text_style(size=10, font="+mn-lt", color="tx1")
    sizes = {n.get("sz") for n in root(chart).iter(f"{A}defRPr", f"{A}rPr")
             if n.get("sz") is not None}
    assert sizes == {"1000"}
    space_defaults = root(chart).find(f"{C}txPr/{A}p/{A}pPr/{A}defRPr")
    assert space_defaults.get("sz") == "1000"
    for node in root(chart).iter(f"{A}defRPr"):
        children = [local(n) for n in node]
        assert children.index("solidFill") < children.index("latin")
    assert chart.text_style("legend") == {"font": "+mn-lt", "size": 10.0, "bold": False,
                                          "color": "tx1"}
    gates(document, chart)


def test_one_part_of_the_text():
    document, chart = new("column", title="T")
    chart.set_text_style("title", size=14, bold=True)
    title = root(chart).find(f"{C}chart/{C}title")
    assert {n.get("sz") for n in title.iter(f"{A}defRPr", f"{A}rPr")} == {"1400"}
    assert chart.text_style("axis_labels").get("size") != 14
    chart.set_text_style("legend", color="accent1")
    with pytest.raises(FormatError, match="no axis titles"):
        chart.set_text_style("axis_titles", size=9)
    with pytest.raises(FormatError, match="1-400"):
        chart.set_text_style(size=0)


# -- G4: point labels ------------------------------------------------------------------------


def test_points_labelled_with_their_own_text():
    document, chart = xy_chart()
    chart.set_point_labels("Recs", R, position="right")
    assert chart.point_labels("Recs") == R
    labels = root(chart).findall(f".//{C}ser/{C}dLbls/{C}dLbl")
    assert len(labels) == 6
    first = labels[0]
    assert [local(n) for n in first][:3] == ["idx", "tx", "spPr"]
    assert first.find(f"{C}dLblPos").get("val") == "r"
    assert first.find(f".//{A}t").text == "R1"
    gates(document, chart)
    chart.set_point_labels("Recs", None)
    assert chart.point_labels("Recs") == [None] * 6
    assert root(chart).find(f".//{C}ser/{C}dLbls") is None


def test_value_from_cells_writes_the_labels_into_the_workbook_and_keeps_them_in_step():
    """PowerPoint's own form (measured): a CELLRANGE field per point, c15:xForSave, and the
    series' c15:datalabelsRange over a workbook column."""
    document, chart = xy_chart("bubble")
    chart.set_point_labels("Recs", R, position="right", cells=True)
    ser = root(chart).find(f".//{C}ser")
    rng = ser.find(f"{C}extLst/{C}ext/{C15}datalabelsRange")
    assert rng.find(f"{C15}f").text == "Sheet1!$D$2:$D$7"
    field = ser.find(f"{C}dLbls/{C}dLbl/{C}tx/{C}rich/{A}p/{A}fld")
    assert field.get("type") == "CELLRANGE" and field.find(f"{A}t").text == "[CELLRANGE]"
    assert ser.find(f"{C}dLbls/{C}dLbl//{C15}xForSave").get("val") == "1"
    assert chart.point_labels("Recs") == R
    book = Book(document.package.read(chart.workbook_part)).sheet("Sheet1")
    assert book.range("D2", "D7") == R and book.value("D1") == "Labels"
    gates(document, chart)
    chart.add_category(5.0, [2.5], index=1)
    assert chart.point_labels("Recs") == ["R1", None, "R2", "R3", "R4", "R5", "R6"]
    assert rng.find(f"{C15}f").text == "Sheet1!$D$2:$D$8"
    book = Book(document.package.read(chart.workbook_part)).sheet("Sheet1")
    assert book.range("D2", "D8") == ["R1", None, "R2", "R3", "R4", "R5", "R6"]
    chart.remove_category(0)
    book = Book(document.package.read(chart.workbook_part)).sheet("Sheet1")
    assert book.range("D2", "D7") == [None, "R2", "R3", "R4", "R5", "R6"]
    gates(document, chart)


def test_a_point_label_position_must_suit_the_chart():
    document, chart = new("stacked_column")
    with pytest.raises(ChartDataError, match="center, inside_end, inside_base"):
        chart.set_point_labels("North", ["a", "b", "c", "d"], position="outside_end")
    with pytest.raises(ChartDataError, match="none"):
        new("radar")[1].set_point_labels("North", ["a", "b", "c", "d"], position="right")
    with pytest.raises(ChartDataError, match="3 labels for 4"):
        chart.set_point_labels("North", ["a", "b", "c"])


def test_a_label_and_its_value():
    document, chart = gap_chart()
    chart.set_point_labels("Current", [None, None, None, None, "lowest"], show_value=True,
                           position="outside_end")
    label = root(chart).find(f".//{C}dLbl")
    paragraph = label.find(f"{C}tx/{C}rich/{A}p")
    assert [local(n) for n in paragraph] == ["pPr", "r", "r", "fld", "endParaRPr"]
    assert paragraph.find(f"{A}fld").get("type") == "VALUE"
    assert paragraph.find(f"{A}fld/{A}t").text == "2.08"
    assert chart.point_labels("Current") == [None, None, None, None, "lowest"]
    gates(document, chart)


# -- new kinds -------------------------------------------------------------------------------


def test_a_filled_radar_as_office_inserts_one():
    """Measured: radarStyle filled; each series filled in its colour, no line, no marker."""
    document, chart = new("radar_filled")
    plot = root(chart).find(f".//{C}radarChart")
    assert plot.find(f"{C}radarStyle").get("val") == "filled"
    ser = plot.find(f"{C}ser")
    assert ser.find(f"{C}spPr/{A}solidFill/{A}schemeClr").get("val") == "accent1"
    assert ser.find(f"{C}spPr/{A}ln/{A}noFill") is not None
    assert ser.find(f"{C}marker") is None
    assert root(chart).find(f"{C}chart/{C}legend/{C}legendPos").get("val") == "t"
    chart.format_series("North", fill="accent1 alpha=50%")
    gates(document, chart)


def test_a_bubble_chart_as_office_inserts_one():
    """Measured: 75% opaque series colour, no line, bubble3D 0, bubbleScale 100,
    showNegBubbles 0, the scatter's axes; the workbook x | y | size."""
    document, chart = xy_chart("bubble", bubble_scale=60)
    plot = root(chart).find(f".//{C}bubbleChart")
    ser = plot.find(f"{C}ser")
    assert ser.find(f"{C}spPr/{A}solidFill/{A}schemeClr/{A}alpha").get("val") == "75000"
    assert [local(n) for n in ser][-5:] == ["xVal", "yVal", "bubbleSize", "bubble3D", "extLst"]
    assert plot.find(f"{C}bubbleScale").get("val") == "60"
    assert ser.find(f"{C}bubbleSize/{C}numRef/{C}f").text == "Sheet1!$C$2:$C$7"
    sheet = Book(document.package.read(chart.workbook_part)).sheet("Sheet1")
    assert sheet.range("A1", "C1") == [None, "Recs", "Size"]
    assert sheet.range("C2", "C7") == [3, 2, 3, 1, 2, 1]
    assert chart.categories == X and chart.series[0].values == Y
    gates(document, chart)
    chart.format_point("Recs", 0, fill="accent2")
    chart.set_point_labels("Recs", R, position="right")
    gates(document, chart)


def test_bubble_series_may_carry_their_own_x():
    from ooxml_edit.charts.create import chart_data

    labels, data = chart_data("bubble", None, [{"name": "A", "x": [1, 2], "y": [3, 4],
                                                "size": [1, 1]}])
    assert labels == [1, 2] and data[0]["values"] == [3, 4]
    with pytest.raises(ChartDataError, match="share"):
        chart_data("bubble", None, [{"x": [1, 2], "y": [3, 4], "size": [1, 1]},
                                    {"x": [1, 3], "y": [3, 4], "size": [1, 1]}])
    with pytest.raises(ChartDataError, match="0 or more"):
        chart_data("bubble", [1, 2], [{"values": [3, 4], "size": [1, -1]}])


@pytest.mark.parametrize("kind", ["radar_filled", "bubble", "column"])
def test_the_new_kinds_are_the_same_bytes_and_undo(kind):
    make = (lambda: xy_chart("bubble")) if kind == "bubble" else (lambda: new(kind))
    first, chart = make()
    second, _ = make()
    assert first.to_bytes() == second.to_bytes()
    first.undo_all()
    assert first.to_bytes() == cs.package()


def test_the_style_edits_are_deterministic():
    def build():
        document, chart = gap_chart()
        chart.format_point("Current", 4, fill="accent2")
        chart.set_series_type("Target", "line", secondary_axis=True)
        chart.set_point_labels("Current", ["a", "b", "c", "d", "e"], cells=True)
        return document.to_bytes()

    assert build() == build()


# -- the tool's arguments --------------------------------------------------------------------


def test_scale_and_gridline_arguments():
    assert scale_arguments({"min": 0, "max": 5, "major_unit": 1}) == \
        {"minimum": 0, "maximum": 5, "major_unit": 1}
    assert scale_arguments({"auto": ["max"], "reverse": True}) == {"maximum": None,
                                                                  "reverse": True}
    with pytest.raises(ChartDataError, match="listed in auto"):
        scale_arguments({"max": 4, "auto": ["max"]})
    with pytest.raises(ChartDataError, match="empty"):
        scale_arguments({})
    assert gridline_arguments("none") == {"major": False, "minor": False}


def test_the_style_actions_and_what_read_adds():
    document, chart = gap_chart()
    apply_style_action(chart, "set_axis_scale", axis="value", scale={"min": 0, "max": 5,
                                                                     "major_unit": 1})
    apply_style_action(chart, "format_series", series="Current", style={"fill": "tx2"})
    apply_style_action(chart, "format_point", series="Current", category=4,
                       style={"fill": "accent2"})
    apply_style_action(chart, "set_series_type", series="Target", chart_type="line")
    apply_style_action(chart, "format_series", series="Target",
                       style={"line": "none", "marker": "diamond", "marker_size": 10,
                              "marker_fill": "accent6"})
    apply_style_action(chart, "set_text_style", style={"size": 10})
    apply_style_action(chart, "set_gridlines", axis="value", gridlines="none")
    with pytest.raises(ChartDataError, match="not for set_text_style"):
        apply_style_action(chart, "set_text_style", style={"fill": "tx2"})
    with pytest.raises(ChartDataError, match="needs axis"):
        apply_style_action(chart, "set_axis_scale", scale={"min": 0})
    seen = chart_style_json(chart)
    assert seen["types"] == ["bar", "line"]
    assert seen["scales"]["value"]["maximum"] == 5
    assert seen["series_format"]["Target"]["marker"] == "diamond"
    assert seen["series_format"]["Current"]["points"] == {4: {"fill": "accent2", "line": None}}
    gates(document, chart)


TWO = [{"name": "Now", "values": [1, 2]}, {"name": "Goal", "values": [2, 2], "type": "line"}]


@pytest.mark.parametrize("kind, data, key", [
    ("column", TWO, "fill"), ("stacked_bar", TWO[:1], "fill"), ("bubble", TWO[:1], "fill"),
    ("radar_filled", TWO[:1], "fill"), ("line", TWO[:1], "line"), ("radar", TWO[:1], "line"),
    ("scatter", TWO[:1], "marker_fill")])
def test_add_s_look_is_the_separate_calls_in_order(kind, data, key):
    actions = add_style_actions(kind, data, data_labels=True, colors=["tx2"],
                                style={"size": 9}, part="legend")
    assert actions == [("show_data_labels", {}),
                       ("format_series", {"series": "0", "style": {key: "tx2"}}),
                       ("set_text_style", {"style": {"size": 9}, "part": "legend"})]


def test_add_s_colours_line_a_column_chart_s_line_series_and_fill_a_pie_s_slices():
    assert add_style_actions("column", TWO, colors=["tx2", "accent2"])[1] == (
        "format_series", {"series": "1", "style": {"line": "accent2"}})
    assert add_style_actions("pie", TWO[:1], colors=["tx2", "accent2"]) == [
        ("format_point", {"series": "0", "category": str(k), "style": {"fill": colour}})
        for k, colour in enumerate(["tx2", "accent2"])]
    assert add_style_actions("column", TWO) == []


@pytest.mark.parametrize("given, words", [
    ({"colors": ["a", "b", "c"]}, "3 colours for 2 series"),
    ({"colors": "tx2"}, "a list of colours"),
    ({"colors": [""]}, r"colors\[0\] is a colour"),
    ({"colors": ["tx2", "no such colour"]}, r"colors\[1\]: "),
    ({"style": {"fill": "tx2"}}, "colour series with colors"),
    ({"style": {}}, "style is empty"),
    ({"part": "title"}, "give style too"),
    ({"data_labels": "yes"}, "true or false")])
def test_add_s_look_is_refused_before_anything_is_made(given, words):
    with pytest.raises(ChartDataError, match=words):
        add_style_actions("column", TWO, **given)


def test_add_s_look_applied_after_the_chart_is_made():
    document, chart = new("column")
    apply_add_style(chart, add_style_actions("column", [{}, {}], data_labels=True,
                                             colors=["tx2", "accent2"], style={"size": 9}))
    seen = chart_style_json(chart)
    assert seen["series_format"]["North"]["fill"] == "tx2"
    assert chart.text_style("data_labels")["size"] == 9
    gates(document, chart)


def test_a_chart_without_a_workbook_refuses_value_from_cells():
    document = cs.Opened(cs.package())
    chart = document.chart("cache-missing")
    before = document.to_bytes()
    with pytest.raises(ChartDataError, match="Value From Cells needs"):
        chart.set_point_labels(0, ["x"] * chart.point_count, cells=True)
    assert document.to_bytes() == before


@pytest.mark.parametrize("spec, scheme, modifiers", [
    ("text1", "tx1", []), ("Text 2", "tx2", []), ("background1", "bg1", []),
    ("BACKGROUND2", "bg2", []), ("Accent 1 lumMod=75%", "accent1", ["lumMod"]),
    ("dark1", "dk1", []), ("followedHyperlink", "folHlink", []), ("TX1", "tx1", [])])
def test_office_theme_colour_names_are_read_as_scheme_names(spec, scheme, modifiers):
    element = color_element(spec)
    assert element.tag.endswith("}schemeClr") and element.get("val") == scheme
    assert [child.tag.split("}")[1] for child in element] == modifiers


def test_an_unknown_colour_lists_the_scheme_names_and_mentions_offices():
    with pytest.raises(FormatError, match=r"accent1, .*tx2; Office's names such as text1"):
        color_element("text3")
