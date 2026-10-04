"""The vocabulary charts, diagrams and their text are written in, registered with the core.

Importing this module teaches :func:`ooxml_edit.xml.qn` the DrawingML prefixes a chart or a
SmartArt diagram uses -- ``a:`` (DrawingML), ``c:`` (charts), ``dgm:`` (diagram data),
``dsp:`` (the cached diagram drawing) and ``x:`` (SpreadsheetML, for a chart's embedded
workbook) -- and the child sequences of every element this subpackage inserts into.

These parts and this markup are the same whichever document holds them, so the subpackage
owns their order tables.  The registry is last-wins: a format layer that also registers one
of these parents replaces the entry for everyone, so it should register the same sequence
or none at all.
"""

from __future__ import annotations

from ..xml import register_child_order, register_namespaces

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
DGM_NS = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
DSP_NS = "http://schemas.microsoft.com/office/drawing/2008/diagram"
SML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

NAMESPACES: dict[str, str] = {
    "a": A_NS,
    "c": C_NS,
    "dgm": DGM_NS,
    "dsp": DSP_NS,
    "x": SML_NS,
}
register_namespaces(NAMESPACES)

#: A text body: ``c:rich``, ``dgm:t`` and ``dsp:txBody`` are all ``CT_TextBody``.
_TEXT_ORDER = ("a:bodyPr", "a:lstStyle", "a:p")

#: DrawingML text, which every text body above holds.
TEXT_CHILD_ORDER: dict[str, tuple] = {
    "a:r": ("a:rPr", "a:t"),
    "a:fld": ("a:rPr", "a:pPr", "a:t"),
    "a:br": ("a:rPr",),
    # A paragraph's body is a repeating choice of runs, breaks and fields whose order carries
    # meaning -- "text<br>more text".  They share one rank, so inserting a run never reorders
    # the others; it lands after them and before a:endParaRPr, and callers that want it
    # elsewhere position it themselves.
    "a:p": ("a:pPr", ("a:r", "a:br", "a:fld", "mc:AlternateContent"), "a:endParaRPr"),
}

_AXIS_HEAD = ("c:axId", "c:scaling", "c:delete", "c:axPos", "c:majorGridlines",
              "c:minorGridlines", "c:title", "c:numFmt", "c:majorTickMark", "c:minorTickMark",
              "c:tickLblPos", "c:spPr", "c:txPr", "c:crossAx", ("c:crosses", "c:crossesAt"))

#: The chart part (``CT_ChartSpace`` and below).
CHART_CHILD_ORDER: dict[str, tuple] = {
    "c:chart": ("c:title", "c:autoTitleDeleted", "c:pivotFmts", "c:view3D", "c:floor",
                "c:sideWall", "c:backWall", "c:plotArea", "c:legend", "c:plotVisOnly",
                "c:dispBlanksAs", "c:showDLblsOverMax", "c:extLst"),
    "c:title": ("c:tx", "c:layout", "c:overlay", "c:spPr", "c:txPr", "c:extLst"),
    "c:legend": ("c:legendPos", "c:legendEntry", "c:layout", "c:overlay", "c:spPr",
                 "c:txPr", "c:extLst"),
    "c:catAx": _AXIS_HEAD + ("c:auto", "c:lblAlgn", "c:lblOffset", "c:tickLblSkip",
                             "c:tickMarkSkip", "c:noMultiLvlLbl", "c:extLst"),
    "c:valAx": _AXIS_HEAD + ("c:crossBetween", "c:majorUnit", "c:minorUnit", "c:dispUnits",
                             "c:extLst"),
    "c:dateAx": _AXIS_HEAD + ("c:auto", "c:lblOffset", "c:baseTimeUnit", "c:majorUnit",
                              "c:majorTimeUnit", "c:minorUnit", "c:minorTimeUnit", "c:extLst"),
    "c:serAx": _AXIS_HEAD + ("c:tickLblSkip", "c:tickMarkSkip", "c:extLst"),
    # Every chart type's series in one sequence: each type's own order is a subsequence.
    "c:ser": ("c:idx", "c:order", "c:tx", "c:spPr", "c:invertIfNegative", "c:pictureOptions",
              "c:marker", "c:explosion", "c:dPt", "c:dLbls", "c:trendline", "c:errBars",
              "c:cat", "c:xVal", "c:val", "c:yVal", "c:bubbleSize", "c:bubble3D", "c:shape",
              "c:smooth", "c:extLst"),
    "c:strRef": ("c:f", "c:strCache", "c:extLst"),
    "c:numRef": ("c:f", "c:numCache", "c:extLst"),
    "c:multiLvlStrRef": ("c:f", "c:multiLvlStrCache", "c:extLst"),
    "c:strCache": ("c:ptCount", "c:pt", "c:extLst"),
    "c:strLit": ("c:ptCount", "c:pt", "c:extLst"),
    "c:numCache": ("c:formatCode", "c:ptCount", "c:pt", "c:extLst"),
    "c:numLit": ("c:formatCode", "c:ptCount", "c:pt", "c:extLst"),
    "c:multiLvlStrCache": ("c:ptCount", "c:lvl", "c:extLst"),
    "c:lvl": ("c:pt", "c:extLst"),
    "c:rich": _TEXT_ORDER,
}

#: The diagram data model and the cached drawing's text.
DIAGRAM_CHILD_ORDER: dict[str, tuple] = {
    "dgm:pt": ("dgm:prSet", "dgm:spPr", "dgm:t", "dgm:extLst"),
    "dgm:t": _TEXT_ORDER,
    "dsp:txBody": _TEXT_ORDER,
    "dgm:dataModel": ("dgm:ptLst", "dgm:cxnLst", "dgm:bg", "dgm:whole", "dgm:extLst"),
}

register_child_order(TEXT_CHILD_ORDER)
register_child_order(CHART_CHILD_ORDER)
register_child_order(DIAGRAM_CHILD_ORDER)

__all__ = ["A_NS", "C_NS", "CHART_CHILD_ORDER", "DGM_NS", "DIAGRAM_CHILD_ORDER", "DSP_NS",
           "NAMESPACES", "SML_NS", "TEXT_CHILD_ORDER"]
