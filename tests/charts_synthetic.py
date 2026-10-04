"""Charts, workbooks and diagrams built in the tests, so the charts suite needs no real document.

The host is format-neutral on purpose: a main part in the test namespace whose ``tst:frame``
elements hold a ``c:chart`` or a ``dgm:relIds`` the way any document's graphic frame does.
What it points at is ordinary DrawingML, written the way Office writes it:

* chart parts for a bar, line, pie, doughnut, scatter, radar and bubble chart and a chart
  with two plots (bars and a line over the same categories), each with an embedded workbook;
* workbooks with shared or inline strings, a table over the data, a calculation chain and a
  formula cell, laid out with series in columns or in rows;
* charts whose data is linked from outside the package, embedded as an OLE object, or
  missing, which can only be edited in their caches;
* a SmartArt data model with nodes, an assistant, transitions, presentation points and
  ``presOf`` connections, and the cached ``dsp`` drawing that shows it.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

NS = "urn:ooxml-edit:test"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DGM = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
DSP = "http://schemas.microsoft.com/office/drawing/2008/diagram"
X = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PKG_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

REL = R + "/"
REL_CHART = REL + "chart"
REL_PACKAGE = REL + "package"
REL_OLE = REL + "oleObject"
REL_DGM_DATA = REL + "diagramData"
REL_DGM_LAYOUT = REL + "diagramLayout"
REL_DGM_STYLE = REL + "diagramQuickStyle"
REL_DGM_COLORS = REL + "diagramColors"
REL_DGM_DRAWING = "http://schemas.microsoft.com/office/2007/relationships/diagramDrawing"

CT_MAIN = "application/vnd.ooxml-edit.test.main+xml"
CT_CHART = "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
CT_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CT_RELS = "application/vnd.openxmlformats-package.relationships+xml"
_SML = "application/vnd.openxmlformats-officedocument.spreadsheetml."
_DML = "application/vnd.openxmlformats-officedocument.drawingml."

MAIN = "doc/main.xml"
MAIN_RELS = "doc/_rels/main.xml.rels"
DATA = "doc/diagrams/data1.xml"
DRAWING = "doc/diagrams/drawing1.xml"
FLAT_DATA = "doc/diagrams/data2.xml"
FLAT_DRAWING = "doc/diagrams/drawing2.xml"

STAMP = (2024, 5, 6, 7, 8, 10)


def xml(body: str) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' + body).encode()


def rels(*relationships: tuple) -> bytes:
    nodes = []
    for rel_id, rel_type, target, *mode in relationships:
        extra = f' TargetMode="{mode[0]}"' if mode else ""
        nodes.append(f'<Relationship Id="{rel_id}" Type="{rel_type}" Target="{target}"{extra}/>')
    return xml(f'<Relationships xmlns="{PKG_RELS}">' + "".join(nodes) + "</Relationships>")


def zip_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()


def column(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def number(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return repr(value) if isinstance(value, float) else str(value)


# ------------------------------------------------------------------------------------------
# Workbooks
# ------------------------------------------------------------------------------------------


@dataclass
class Book:
    """A one-sheet workbook: cells by ``(row, column)``, written as Office writes them."""

    cells: dict[tuple[int, int], object] = field(default_factory=dict)
    shared: bool = True
    table: tuple[int, int, int, int] | None = None  # first row, first col, last row, last col
    formulas: dict[tuple[int, int], str] = field(default_factory=dict)
    sheet: str = "Sheet1"

    def to_bytes(self) -> bytes:
        strings: list[str] = []
        string_uses = 0
        rows: dict[int, list[str]] = {}
        for (row, col), value in sorted(self.cells.items()):
            ref = f"{column(col)}{row}"
            style = ' s="1"' if (row, col) not in self.formulas and row > 1 and col > 1 else ""
            if value is None:
                continue
            if isinstance(value, str):
                if self.shared:
                    if value not in strings:
                        strings.append(value)
                    string_uses += 1
                    cell = f'<c r="{ref}" t="s"><v>{strings.index(value)}</v></c>'
                else:
                    cell = f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
            elif (row, col) in self.formulas:
                cell = (f'<c r="{ref}"><f>{escape(self.formulas[(row, col)])}</f>'
                        f'<v>{number(value)}</v></c>')
            else:
                cell = f'<c r="{ref}"{style}><v>{number(value)}</v></c>'
            rows.setdefault(row, []).append(cell)
        last_row = max((r for r, _ in self.cells), default=1)
        last_col = max((c for _, c in self.cells), default=1)
        sheet_rows = "".join(
            f'<row r="{row}" spans="1:{last_col}">' + "".join(cells) + "</row>"
            for row, cells in sorted(rows.items()))
        table_part = '<tableParts count="1"><tablePart r:id="rId1"/></tableParts>' \
            if self.table else ""
        sheet = xml(
            f'<worksheet xmlns="{X}" xmlns:r="{R}">'
            f'<dimension ref="A1:{column(last_col)}{last_row}"/>'
            '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
            '<sheetFormatPr defaultRowHeight="15"/>'
            f"<sheetData>{sheet_rows}</sheetData>"
            '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" '
            'footer="0.3"/>' + table_part + "</worksheet>")
        overrides = [
            ("/xl/workbook.xml", _SML + "sheet.main+xml"),
            ("/xl/worksheets/sheet1.xml", _SML + "worksheet+xml"),
            ("/xl/styles.xml", _SML + "styles+xml"),
        ]
        workbook_rels = [("rId1", REL + "worksheet", "worksheets/sheet1.xml"),
                         ("rId2", REL + "styles", "styles.xml")]
        entries = []
        if self.shared:
            overrides.append(("/xl/sharedStrings.xml", _SML + "sharedStrings+xml"))
            workbook_rels.append(("rId3", REL + "sharedStrings", "sharedStrings.xml"))
            items = "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
            entries.append(("xl/sharedStrings.xml", xml(
                f'<sst xmlns="{X}" count="{string_uses}" uniqueCount="{len(strings)}">'
                f"{items}</sst>")))
        if self.formulas:
            overrides.append(("/xl/calcChain.xml", _SML + "calcChain+xml"))
            workbook_rels.append(("rId4", REL + "calcChain", "calcChain.xml"))
            chain = "".join(f'<c r="{column(c)}{r}" i="1"/>' for r, c in sorted(self.formulas))
            entries.append(("xl/calcChain.xml", xml(f'<calcChain xmlns="{X}">{chain}</calcChain>')))
        if self.table:
            r1, c1, r2, c2 = self.table
            overrides.append(("/xl/tables/table1.xml", _SML + "table+xml"))
            names = []
            for col in range(c1, c2 + 1):
                header = self.cells.get((r1, col))
                names.append(header if isinstance(header, str) and header.strip()
                             else f"Column{col - c1 + 1}")
            columns = "".join(f'<tableColumn id="{i + 1}" name="{escape(n)}"/>'
                              for i, n in enumerate(names))
            ref = f"{column(c1)}{r1}:{column(c2)}{r2}"
            entries.append(("xl/tables/table1.xml", xml(
                f'<table xmlns="{X}" id="1" name="Table1" displayName="Table1" ref="{ref}" '
                f'totalsRowShown="0"><autoFilter ref="{ref}"/>'
                f'<tableColumns count="{len(names)}">{columns}</tableColumns>'
                '<tableStyleInfo name="TableStyleMedium2" showFirstColumn="0" '
                'showLastColumn="0" showRowStripes="1" showColumnStripes="0"/></table>')))
            entries.append(("xl/worksheets/_rels/sheet1.xml.rels",
                            rels(("rId1", REL + "table", "../tables/table1.xml"))))
        types = xml(
            f'<Types xmlns="{CT_NS}"><Default Extension="rels" ContentType="{CT_RELS}"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            + "".join(f'<Override PartName="{name}" ContentType="{kind}"/>'
                      for name, kind in overrides) + "</Types>")
        workbook = xml(
            f'<workbook xmlns="{X}" xmlns:r="{R}"><bookViews><workbookView/></bookViews>'
            f'<sheets><sheet name="{escape(self.sheet)}" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>")
        styles = xml(
            f'<styleSheet xmlns="{X}"><numFmts count="1"><numFmt numFmtId="164" '
            'formatCode="0.0"/></numFmts><fonts count="1"><font><sz val="11"/></font></fonts>'
            '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
            '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" '
            'fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf '
            'numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="164" '
            'fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
            "</styleSheet>")
        return zip_bytes([
            ("[Content_Types].xml", types),
            ("_rels/.rels", rels(("rId1", REL + "officeDocument", "xl/workbook.xml"))),
            ("xl/workbook.xml", workbook),
            ("xl/_rels/workbook.xml.rels", rels(*workbook_rels)),
            ("xl/worksheets/sheet1.xml", sheet),
            ("xl/styles.xml", styles),
        ] + entries)


# ------------------------------------------------------------------------------------------
# Charts
# ------------------------------------------------------------------------------------------


@dataclass
class ChartSpec:
    """One chart: its plots, its data, how the data sits in its workbook."""

    name: str
    plots: list[str]                      # "bar", "line", "pie", ... ; series split evenly
    categories: list
    series: list[tuple[str, list]]        # (name, values)
    sizes: list[list] | None = None       # bubble sizes, one list per series
    down: bool = True                     # series in columns (True) or in rows
    shared: bool = True
    table: bool = False
    formula_cell: bool = False            # the second value of the first series is computed
    workbook: str = "embedded"            # "embedded", "linked", "ole", "missing", "none"
    title: str | None = None
    legend: bool = True
    plot_series: list[int] | None = None  # how many series each plot holds

    @property
    def numeric_categories(self) -> bool:
        return any(plot in {"scatter", "bubble"} for plot in self.plots)


def _str_cache(values: list) -> str:
    points = "".join(f'<c:pt idx="{i}"><c:v>{escape(str(v))}</c:v></c:pt>'
                     for i, v in enumerate(values) if v is not None)
    return f'<c:ptCount val="{len(values)}"/>{points}'


def _num_cache(values: list, code: str = "General") -> str:
    points = "".join(f'<c:pt idx="{i}"><c:v>{number(v)}</c:v></c:pt>'
                     for i, v in enumerate(values) if v is not None)
    return f"<c:formatCode>{code}</c:formatCode>" f'<c:ptCount val="{len(values)}"/>{points}'


def _range(sheet: str, first: tuple[int, int], last: tuple[int, int]) -> str:
    a = f"${column(first[1])}${first[0]}"
    b = f"${column(last[1])}${last[0]}"
    return f"{sheet}!{a}" if first == last else f"{sheet}!{a}:{b}"


class _Layout:
    """Where each line of a chart's data sits in its sheet."""

    def __init__(self, spec: ChartSpec) -> None:
        self.spec = spec
        self.count = len(spec.categories)
        bubble = "bubble" in spec.plots
        self.lines: list[tuple[int, int | None]] = []  # (values line, sizes line) per series
        line = 2
        for _ in spec.series:
            sizes = None
            if bubble:
                sizes = line + 1
            self.lines.append((line, sizes))
            line += 2 if bubble else 1

    def cell(self, across: int, position: int) -> tuple[int, int]:
        """``across`` is the line (column when down), ``position`` the point's place."""
        return (position, across) if self.spec.down else (across, position)

    def span(self, across: int) -> tuple[tuple[int, int], tuple[int, int]]:
        return self.cell(across, 2), self.cell(across, 1 + self.count)

    def book(self) -> Book:
        spec = self.spec
        cells: dict[tuple[int, int], object] = {}
        for offset, label in enumerate(spec.categories):
            cells[self.cell(1, 2 + offset)] = label
        for (name, values), (line, sizes_line), index in zip(
                spec.series, self.lines, range(len(spec.series))):
            cells[self.cell(line, 1)] = name
            for offset, value in enumerate(values):
                cells[self.cell(line, 2 + offset)] = value
            if sizes_line is not None:
                cells[self.cell(sizes_line, 1)] = "Size"
                for offset, value in enumerate(spec.sizes[index]):
                    cells[self.cell(sizes_line, 2 + offset)] = value
        formulas = {}
        if spec.formula_cell:
            formulas[self.cell(self.lines[0][0], 3)] = "1+1"
        table = None
        if spec.table:
            last = max(max(line, sizes or 0) for line, sizes in self.lines)
            first_cell = (1, 1)
            last_cell = self.cell(last, 1 + self.count)
            table = (first_cell[0], first_cell[1], last_cell[0], last_cell[1])
        return Book(cells, shared=spec.shared, table=table, formulas=formulas)


def _ser(spec: ChartSpec, layout: _Layout, index: int, plot: str) -> str:
    name, values = spec.series[index]
    line, sizes_line = layout.lines[index]
    sheet = "Sheet1"
    tx = (f"<c:tx><c:strRef><c:f>{_range(sheet, layout.cell(line, 1), layout.cell(line, 1))}"
          f"</c:f><c:strCache>{_str_cache([name])}</c:strCache></c:strRef></c:tx>")
    cat_first, cat_last = layout.span(1)
    val_first, val_last = layout.span(line)
    if spec.numeric_categories:
        categories = (f"<c:xVal><c:numRef><c:f>{_range(sheet, cat_first, cat_last)}</c:f>"
                      f"<c:numCache>{_num_cache(spec.categories)}</c:numCache></c:numRef>"
                      "</c:xVal>")
        value_tag = "c:yVal"
    else:
        categories = (f"<c:cat><c:strRef><c:f>{_range(sheet, cat_first, cat_last)}</c:f>"
                      f"<c:strCache>{_str_cache(spec.categories)}</c:strCache></c:strRef>"
                      "</c:cat>")
        value_tag = "c:val"
    vals = (f"<{value_tag}><c:numRef><c:f>{_range(sheet, val_first, val_last)}</c:f>"
            f"<c:numCache>{_num_cache(values)}</c:numCache></c:numRef></{value_tag}>")
    colour = ["4472C4", "ED7D31", "A5A5A5", "FFC000", "5B9BD5"][index % 5]
    look = f'<c:spPr><a:solidFill><a:srgbClr val="{colour}"/></a:solidFill></c:spPr>'
    head = f'<c:idx val="{index}"/><c:order val="{index}"/>{tx}{look}'
    marker = '<c:marker><c:symbol val="circle"/><c:size val="5"/></c:marker>'
    if plot == "bar":
        return f'<c:ser>{head}<c:invertIfNegative val="0"/>{categories}{vals}</c:ser>'
    if plot in {"line", "radar"}:
        smooth = '<c:smooth val="0"/>' if plot == "line" else ""
        return f"<c:ser>{head}{marker}{categories}{vals}{smooth}</c:ser>"
    if plot in {"pie", "doughnut"}:
        points = "".join(
            f'<c:dPt><c:idx val="{i}"/><c:bubble3D val="0"/><c:spPr><a:solidFill>'
            f'<a:srgbClr val="{["4472C4", "ED7D31", "A5A5A5", "FFC000"][i % 4]}"/>'
            "</a:solidFill></c:spPr></c:dPt>" for i in range(len(values)))
        labels = ('<c:dLbls><c:dLbl><c:idx val="1"/><c:showLegendKey val="0"/>'
                  '<c:showVal val="1"/><c:showCatName val="0"/><c:showSerName val="0"/>'
                  '<c:showPercent val="0"/><c:showBubbleSize val="0"/></c:dLbl>'
                  '<c:showLegendKey val="0"/><c:showVal val="0"/><c:showCatName val="0"/>'
                  '<c:showSerName val="0"/><c:showPercent val="0"/>'
                  '<c:showBubbleSize val="0"/></c:dLbls>')
        return f"<c:ser>{head}{points}{labels}{categories}{vals}</c:ser>"
    if plot == "scatter":
        return f'<c:ser>{head}{marker}{categories}{vals}<c:smooth val="0"/></c:ser>'
    if plot == "bubble":
        size_first, size_last = layout.span(sizes_line)
        sizes = (f"<c:bubbleSize><c:numRef><c:f>{_range(sheet, size_first, size_last)}</c:f>"
                 f"<c:numCache>{_num_cache(spec.sizes[index])}</c:numCache></c:numRef>"
                 "</c:bubbleSize>")
        return (f'<c:ser>{head}<c:invertIfNegative val="0"/>{categories}{vals}{sizes}'
                '<c:bubble3D val="0"/></c:ser>')
    raise ValueError(plot)


_AXES = {
    "category": ('<c:catAx><c:axId val="{a}"/><c:scaling><c:orientation val="minMax"/>'
                 '</c:scaling><c:delete val="0"/><c:axPos val="b"/><c:numFmt '
                 'formatCode="General" sourceLinked="1"/><c:majorTickMark val="out"/>'
                 '<c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/><c:crossAx '
                 'val="{b}"/><c:crosses val="autoZero"/><c:auto val="1"/><c:lblAlgn '
                 'val="ctr"/><c:lblOffset val="100"/><c:noMultiLvlLbl val="0"/></c:catAx>'),
    "value": ('<c:valAx><c:axId val="{a}"/><c:scaling><c:orientation val="minMax"/>'
              '</c:scaling><c:delete val="0"/><c:axPos val="{pos}"/><c:majorGridlines/>'
              '<c:numFmt formatCode="General" sourceLinked="1"/><c:majorTickMark val="out"/>'
              '<c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/><c:crossAx '
              'val="{b}"/><c:crosses val="autoZero"/><c:crossBetween val="{between}"/>'
              "</c:valAx>"),
}


def _plot(spec: ChartSpec, layout: _Layout, plot: str, indexes: list[int]) -> str:
    series = "".join(_ser(spec, layout, i, plot) for i in indexes)
    axes = '<c:axId val="500"/><c:axId val="501"/>'
    if plot == "bar":
        return (f'<c:barChart><c:barDir val="col"/><c:grouping val="clustered"/>'
                f'<c:varyColors val="0"/>{series}<c:gapWidth val="219"/>{axes}</c:barChart>')
    if plot == "line":
        return (f'<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>{series}'
                f'<c:marker val="1"/>{axes}</c:lineChart>')
    if plot == "radar":
        return (f'<c:radarChart><c:radarStyle val="marker"/><c:varyColors val="0"/>{series}'
                f"{axes}</c:radarChart>")
    if plot == "pie":
        return (f'<c:pieChart><c:varyColors val="1"/>{series}<c:firstSliceAng val="0"/>'
                "</c:pieChart>")
    if plot == "doughnut":
        return (f'<c:doughnutChart><c:varyColors val="1"/>{series}<c:firstSliceAng val="0"/>'
                '<c:holeSize val="50"/></c:doughnutChart>')
    if plot == "scatter":
        return (f'<c:scatterChart><c:scatterStyle val="lineMarker"/><c:varyColors val="0"/>'
                f"{series}{axes}</c:scatterChart>")
    if plot == "bubble":
        return (f'<c:bubbleChart><c:varyColors val="0"/>{series}<c:bubbleScale val="100"/>'
                f'<c:showNegBubbles val="0"/>{axes}</c:bubbleChart>')
    raise ValueError(plot)


def chart_xml(spec: ChartSpec, workbook_rel: str | None = "rId1") -> bytes:
    layout = _Layout(spec)
    counts = spec.plot_series or [len(spec.series)]
    plots, start = [], 0
    for plot, count in zip(spec.plots, counts):
        plots.append(_plot(spec, layout, plot, list(range(start, start + count))))
        start += count
    axes = ""
    if not set(spec.plots) <= {"pie", "doughnut"}:
        if spec.numeric_categories:
            axes = (_AXES["value"].format(a=500, b=501, pos="b", between="midCat")
                    + _AXES["value"].format(a=501, b=500, pos="l", between="midCat"))
        else:
            axes = (_AXES["category"].format(a=500, b=501)
                    + _AXES["value"].format(a=501, b=500, pos="l", between="between"))
    title = ""
    if spec.title is not None:
        title = ('<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr/>'
                 f'</a:pPr><a:r><a:rPr lang="en-GB" b="1"/><a:t>{escape(spec.title)}</a:t>'
                 '</a:r></a:p></c:rich></c:tx><c:overlay val="0"/></c:title>')
    legend = ('<c:legend><c:legendPos val="r"/><c:overlay val="0"/></c:legend>'
              if spec.legend else "")
    external = (f'<c:externalData r:id="{workbook_rel}"><c:autoUpdate val="0"/>'
                "</c:externalData>") if workbook_rel else ""
    return xml(
        f'<c:chartSpace xmlns:c="{C}" xmlns:a="{A}" xmlns:r="{R}">'
        '<c:date1904 val="0"/><c:roundedCorners val="0"/>'
        f'<c:chart>{title}<c:autoTitleDeleted val="{0 if spec.title else 1}"/><c:plotArea>'
        f'<c:layout/>{"".join(plots)}{axes}</c:plotArea>{legend}<c:plotVisOnly val="1"/>'
        f'<c:dispBlanksAs val="gap"/></c:chart>{external}</c:chartSpace>')


def charts() -> list[ChartSpec]:
    """Every synthetic chart: each plot type, both directions, every workbook feature."""
    quarters = ["Q1", "Q2", "Q3", "Q4"]
    return [
        ChartSpec("bar", ["bar"], quarters, [("North", [10, 12.5, 9, 14]),
                                             ("South", [7, 8, 11, 6])], table=True,
                  title="Sales"),
        ChartSpec("bar-rows", ["bar"], ["Ann", "Bo", "Cy"],
                  [("2023", [3, 4, 5]), ("2024", [6, 7, 8])], down=False, shared=False),
        ChartSpec("line", ["line"], quarters, [("Revenue", [100, 120, 90, 130]),
                                               ("Cost", [80, 85, 70, 95]),
                                               ("Margin", [20, 35, 20, 35])],
                  formula_cell=True, table=True),
        ChartSpec("pie", ["pie"], ["Red", "Green", "Blue"], [("Share", [50, 30, 20])],
                  legend=True),
        ChartSpec("doughnut", ["doughnut"], ["A", "B", "C", "D"], [("Mix", [1, 2, 3, 4])],
                  shared=False, table=True),
        ChartSpec("scatter", ["scatter"], [1, 2, 3, 5], [("Fit", [2.5, 4, 6.5, 10]),
                                                         ("Raw", [2, 5, 6, 11])]),
        ChartSpec("radar", ["radar"], ["Speed", "Cost", "Risk", "Fit", "Ease"],
                  [("Plan A", [3, 4, 2, 5, 4]), ("Plan B", [4, 2, 4, 3, 5])], down=False),
        ChartSpec("bubble", ["bubble"], [1, 2, 3], [("Small", [10, 20, 30]),
                                                    ("Large", [15, 25, 35])],
                  sizes=[[1, 2, 3], [4, 5, 6]]),
        ChartSpec("combo", ["bar", "line"], quarters,
                  [("Units", [5, 6, 7, 8]), ("Returns", [1, 1, 2, 1]),
                   ("Trend", [4, 6, 7, 9])], plot_series=[2, 1], table=True),
    ]


def cache_only_charts() -> list[ChartSpec]:
    """Charts whose workbook cannot be reached."""
    base = dict(plots=["bar"], categories=["X", "Y", "Z"],
                series=[("One", [1, 2, 3]), ("Two", [4, 5, 6])])
    return [ChartSpec(f"cache-{kind}", workbook=kind, **base)
            for kind in ("linked", "ole", "missing", "none")]


# ------------------------------------------------------------------------------------------
# Diagrams
# ------------------------------------------------------------------------------------------

#: Model ids, fixed so tests can name them.
DOC = "{5A000000-0000-4000-8000-000000000001}"
GOALS = "{5A000000-0000-4000-8000-000000000010}"
FASTER = "{5A000000-0000-4000-8000-000000000011}"
FEWER = "{5A000000-0000-4000-8000-000000000012}"
RISKS = "{5A000000-0000-4000-8000-000000000020}"
STALE = "{5A000000-0000-4000-8000-000000000021}"
HELPER = "{5A000000-0000-4000-8000-000000000022}"
#: Presentation points: a parent's text shape, and the shape its children share.
GOALS_PRES, GOALS_KIDS = "{5A000000-0000-4000-8000-000000000110}", \
    "{5A000000-0000-4000-8000-000000000111}"
RISKS_PRES, RISKS_KIDS = "{5A000000-0000-4000-8000-000000000120}", \
    "{5A000000-0000-4000-8000-000000000121}"
ROOT_PRES = "{5A000000-0000-4000-8000-000000000100}"

#: The bullet list: (id, type, text, parent, order).
BULLETS = [
    (GOALS, None, "Goals", DOC, 0),
    (FASTER, None, "Faster edits", GOALS, 0),
    (FEWER, None, "Fewer prompts", GOALS, 1),
    (RISKS, None, "Risks", DOC, 1),
    (STALE, None, "Stale caches", RISKS, 0),
    (HELPER, "asst", "Helper", RISKS, 1),
]
#: Which presentation point shows which node's text, in destOrd order.
SHOWN_BY = {GOALS_PRES: [GOALS], GOALS_KIDS: [FASTER, FEWER],
            RISKS_PRES: [RISKS], RISKS_KIDS: [STALE, HELPER]}

FLAT = [
    ("{5B000000-0000-4000-8000-000000000010}", None, "Plan", "{5B000000-0000-4000-8000-000000000001}", 0),
    ("{5B000000-0000-4000-8000-000000000020}", None, "Build", "{5B000000-0000-4000-8000-000000000001}", 1),
    ("{5B000000-0000-4000-8000-000000000030}", None, "Ship", "{5B000000-0000-4000-8000-000000000001}", 2),
]


def _text_body(tag: str, paragraphs: list[str], size: str | None = None) -> str:
    size_attr = f' sz="{size}"' if size else ""
    body = "".join(
        f'<a:p><a:r><a:rPr lang="en-US"{size_attr} b="1"/><a:t>{escape(p)}</a:t></a:r></a:p>'
        if p else '<a:p><a:endParaRPr lang="en-US"/></a:p>' for p in paragraphs)
    return f"<{tag}><a:bodyPr/><a:lstStyle/>{body}</{tag}>"


def data_model(nodes: list[tuple], shown_by: dict[str, list[str]], document: str,
               drawing_rel: str | None) -> bytes:
    points = [f'<dgm:pt modelId="{document}" type="doc"><dgm:prSet loTypeId="urn:test/layout"/>'
              f'<dgm:spPr/><dgm:t><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="en-US"/>'
              "</a:p></dgm:t></dgm:pt>"]
    connections = []
    for number_, (model_id, kind, text, parent, order) in enumerate(nodes):
        type_attr = f' type="{kind}"' if kind else ""
        points.append(f'<dgm:pt modelId="{model_id}"{type_attr}><dgm:prSet phldrT="[Text]"/>'
                      f'<dgm:spPr/>{_text_body("dgm:t", [text])}</dgm:pt>')
        par, sib, cxn = (f"{{5C{number_:06d}-0000-4000-8000-00000000000{k}}}" for k in (1, 2, 3))
        for transition, kind_ in ((par, "parTrans"), (sib, "sibTrans")):
            points.append(f'<dgm:pt modelId="{transition}" type="{kind_}" cxnId="{cxn}">'
                          '<dgm:prSet/><dgm:spPr/><dgm:t><a:bodyPr/><a:lstStyle/><a:p>'
                          '<a:endParaRPr lang="en-US"/></a:p></dgm:t></dgm:pt>')
        connections.append(f'<dgm:cxn modelId="{cxn}" srcId="{parent}" destId="{model_id}" '
                           f'srcOrd="{order}" destOrd="0" parTransId="{par}" '
                           f'sibTransId="{sib}"/>')
    root = f"{{5D000000-0000-4000-8000-{document[-13:-1]}}}"
    points.append(f'<dgm:pt modelId="{root}" type="pres"><dgm:prSet presAssocID="{document}" '
                  'presName="root" presStyleCnt="0"/><dgm:spPr/></dgm:pt>')
    connections.append(f'<dgm:cxn modelId="{{5E000000-0000-4000-8000-{document[-13:-1]}}}" '
                       f'type="presOf" srcId="{document}" destId="{root}" srcOrd="0" '
                       'destOrd="0"/>')
    for index, (shape, shown) in enumerate(shown_by.items()):
        owner = shown[0] if len(shown) == 1 else next(
            parent for model_id, _, _, parent, _ in nodes if model_id == shown[0])
        points.append(f'<dgm:pt modelId="{shape}" type="pres"><dgm:prSet presAssocID="{owner}" '
                      f'presName="text{index}" presStyleIdx="{index}" presStyleCnt="4"/>'
                      "<dgm:spPr/></dgm:pt>")
        connections.append(f'<dgm:cxn modelId="{{5F{index:06d}-0000-4000-8000-000000000000}}" '
                           f'type="presParOf" srcId="{root}" destId="{shape}" '
                           f'srcOrd="{index}" destOrd="0"/>')
        for order, model_id in enumerate(shown):
            connections.append(
                f'<dgm:cxn modelId="{{5F{index:06d}-{order:04d}-4000-8000-000000000001}}" '
                f'type="presOf" srcId="{model_id}" destId="{shape}" srcOrd="0" '
                f'destOrd="{order}"/>')
    extension = ""
    if drawing_rel:
        extension = (f'<dgm:extLst><a:ext uri="{DSP}"><dsp:dataModelExt '
                     f'xmlns:dsp="{DSP}" relId="{drawing_rel}" minVer="{DGM}"/></a:ext>'
                     "</dgm:extLst>")
    return xml(
        f'<dgm:dataModel xmlns:dgm="{DGM}" xmlns:a="{A}" xmlns:r="{R}">'
        f'<dgm:ptLst>{"".join(points)}</dgm:ptLst><dgm:cxnLst>{"".join(connections)}'
        f"</dgm:cxnLst><dgm:bg/><dgm:whole/>{extension}</dgm:dataModel>")


def drawing(nodes: list[tuple], shown_by: dict[str, list[str]]) -> bytes:
    texts = {model_id: text for model_id, _, text, _, _ in nodes}
    shapes = []
    for index, (shape, shown) in enumerate(shown_by.items()):
        shapes.append(
            f'<dsp:sp modelId="{shape}"><dsp:nvSpPr><dsp:cNvPr id="0" name=""/><dsp:cNvSpPr/>'
            f'</dsp:nvSpPr><dsp:spPr><a:xfrm><a:off x="0" y="{index * 100000}"/><a:ext '
            'cx="100000" cy="100000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
            "</dsp:spPr>" + _text_body("dsp:txBody", [texts[m] for m in shown], "6500")
            + "</dsp:sp>")
    return xml(f'<dsp:drawing xmlns:dsp="{DSP}" xmlns:dgm="{DGM}" xmlns:a="{A}"><dsp:spTree>'
               '<dsp:nvGrpSpPr><dsp:cNvPr id="0" name=""/><dsp:cNvGrpSpPr/></dsp:nvGrpSpPr>'
               f'<dsp:grpSpPr/>{"".join(shapes)}</dsp:spTree></dsp:drawing>')


def _layout_part(unique: str) -> bytes:
    return xml(f'<dgm:layoutDef xmlns:dgm="{DGM}" uniqueId="{unique}"><dgm:title val=""/>'
               '<dgm:layoutNode name="root"/></dgm:layoutDef>')


# ------------------------------------------------------------------------------------------
# The host package
# ------------------------------------------------------------------------------------------


def package(specs: list[ChartSpec] | None = None, *, diagrams: bool = True) -> bytes:
    """A package whose main part holds a frame per chart (named after it) and, when
    ``diagrams``, two SmartArt frames: ``bullets`` (with an assistant) and ``blocks``."""
    specs = charts() + cache_only_charts() if specs is None else specs
    entries: list[tuple[str, bytes]] = []
    overrides = [("/" + MAIN, CT_MAIN)]
    defaults = {"rels": CT_RELS, "xml": "application/xml"}
    main_rels: list[tuple] = []
    frames = []
    for number_, spec in enumerate(specs, start=1):
        chart_name = f"doc/charts/chart{number_}.xml"
        rel_id = f"rId{number_}"
        main_rels.append((rel_id, REL_CHART, f"charts/chart{number_}.xml"))
        overrides.append(("/" + chart_name, CT_CHART))
        frames.append(f'<tst:frame name="{spec.name}"><tst:graphic><tst:data>'
                      f'<c:chart r:id="{rel_id}"/></tst:data></tst:graphic></tst:frame>')
        book = _Layout(spec).book()
        chart_rels = []
        workbook_rel: str | None = "rId1"
        if spec.workbook == "embedded":
            embedded = f"doc/embeddings/book{number_}.xlsx"
            entries.append((embedded, book.to_bytes()))
            defaults["xlsx"] = CT_XLSX
            chart_rels.append(("rId1", REL_PACKAGE, f"../embeddings/book{number_}.xlsx"))
        elif spec.workbook == "linked":
            chart_rels.append(("rId1", REL_OLE, "file:///elsewhere/book.xlsx", "External"))
        elif spec.workbook == "ole":
            embedded = f"doc/embeddings/oleObject{number_}.bin"
            entries.append((embedded, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 56))
            defaults["bin"] = "application/vnd.openxmlformats-officedocument.oleObject"
            chart_rels.append(("rId1", REL_OLE, f"../embeddings/oleObject{number_}.bin"))
        elif spec.workbook == "missing":
            pass  # the chart names rId1, which no relationship has
        else:
            workbook_rel = None
        entries.append((chart_name, chart_xml(spec, workbook_rel)))
        if chart_rels:
            entries.append((f"doc/charts/_rels/chart{number_}.xml.rels", rels(*chart_rels)))
    if diagrams:
        for name, data_part, drawing_part, nodes, shown, document, base in (
                ("bullets", DATA, DRAWING, BULLETS, SHOWN_BY, DOC, 100),
                ("blocks", FLAT_DATA, FLAT_DRAWING, FLAT, {
                    f"{{5B000000-0000-4000-8000-0000000001{i}0}}": [FLAT[i][0]]
                    for i in range(3)}, "{5B000000-0000-4000-8000-000000000001}", 200)):
            number_ = 1 if name == "bullets" else 2
            ids = [f"rId{base + k}" for k in range(5)]
            main_rels += [
                (ids[0], REL_DGM_DATA, f"diagrams/data{number_}.xml"),
                (ids[1], REL_DGM_LAYOUT, f"diagrams/layout{number_}.xml"),
                (ids[2], REL_DGM_STYLE, f"diagrams/quickStyle{number_}.xml"),
                (ids[3], REL_DGM_COLORS, f"diagrams/colors{number_}.xml"),
                (ids[4], REL_DGM_DRAWING, f"diagrams/drawing{number_}.xml"),
            ]
            frames.append(
                f'<tst:frame name="{name}"><tst:graphic><tst:data><dgm:relIds '
                f'r:dm="{ids[0]}" r:lo="{ids[1]}" r:qs="{ids[2]}" r:cs="{ids[3]}"/>'
                "</tst:data></tst:graphic></tst:frame>")
            entries += [
                (data_part, data_model(nodes, shown, document, ids[4])),
                (f"doc/diagrams/layout{number_}.xml",
                 _layout_part(f"urn:test/layout/{'vList' if number_ == 1 else 'blocks'}")),
                (f"doc/diagrams/quickStyle{number_}.xml",
                 xml(f'<dgm:styleDef xmlns:dgm="{DGM}" uniqueId="urn:test/style"/>')),
                (f"doc/diagrams/colors{number_}.xml",
                 xml(f'<dgm:colorsDef xmlns:dgm="{DGM}" uniqueId="urn:test/colors"/>')),
                (drawing_part, drawing(nodes, shown)),
            ]
            overrides += [
                ("/" + data_part, _DML + "diagramData+xml"),
                (f"/doc/diagrams/layout{number_}.xml", _DML + "diagramLayout+xml"),
                (f"/doc/diagrams/quickStyle{number_}.xml", _DML + "diagramStyle+xml"),
                (f"/doc/diagrams/colors{number_}.xml", _DML + "diagramColors+xml"),
                ("/" + drawing_part, "application/vnd.ms-office.drawingml.diagramDrawing+xml"),
            ]
    main = xml(f'<tst:doc xmlns:tst="{NS}" xmlns:r="{R}" xmlns:c="{C}" xmlns:dgm="{DGM}">'
               + "".join(frames) + "</tst:doc>")
    types = xml(
        f'<Types xmlns="{CT_NS}">'
        + "".join(f'<Default Extension="{e}" ContentType="{t}"/>' for e, t in defaults.items())
        + "".join(f'<Override PartName="{p}" ContentType="{t}"/>' for p, t in overrides)
        + "</Types>")
    return zip_bytes([
        ("[Content_Types].xml", types),
        ("_rels/.rels", rels(("rId1", REL + "officeDocument", MAIN))),
        (MAIN, main),
        (MAIN_RELS, rels(*main_rels)),
    ] + entries)


# ------------------------------------------------------------------------------------------
# Opening one, the way a format layer would
# ------------------------------------------------------------------------------------------


class Opened:
    """A synthetic package opened for editing, with an undo history and hosts by frame name."""

    def __init__(self, data: bytes, **host_options) -> None:
        from ooxml_edit.history import History
        from ooxml_edit.opc import OpcPackage

        self.data = data
        self.package = OpcPackage.open(data)
        self.history = History(self.package)
        self.host_options = host_options

    def host(self, name: str, **options):
        from ooxml_edit.charts import GraphicHost

        root = self.package.tree(MAIN)
        frame = next(f for f in root.iter("{%s}frame" % NS) if f.get("name") == name)
        return GraphicHost(self.package, MAIN, frame, self.history.batch, name,
                           **{**self.host_options, **options})

    def chart(self, name: str, **options):
        from ooxml_edit.charts import Chart

        return Chart(lambda: self.host(name, **options))

    def diagram(self, name: str, *, on_inexact_drawing: str = "drop", notify=None, **options):
        from ooxml_edit.charts import Diagram

        return Diagram(lambda: self.host(name, **options), on_inexact_drawing, notify)

    def to_bytes(self) -> bytes:
        return self.package.to_bytes()

    def undo_all(self) -> None:
        while self.history.undo():
            pass

    def redo_all(self) -> None:
        while self.history.redo():
            pass


def parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()
                if not info.is_dir()}


def assert_valid(data: bytes) -> None:
    """Every part well-formed, typed, and every internal relationship resolving."""
    import posixpath

    from lxml import etree

    found = parts(data)
    types = etree.fromstring(found["[Content_Types].xml"])
    defaults = {n.get("Extension").lower() for n in types if n.tag.endswith("Default")}
    overrides = {n.get("PartName").lstrip("/") for n in types if n.tag.endswith("Override")}
    for name, content in found.items():
        if name == "[Content_Types].xml":
            continue
        assert name in overrides or name.rsplit(".", 1)[-1].lower() in defaults, name
        if name.endswith((".xml", ".rels")):
            etree.fromstring(content)
        if name.endswith(".rels"):
            directory = posixpath.dirname(posixpath.dirname(name))
            for node in etree.fromstring(content):
                if node.get("TargetMode") == "External":
                    continue
                target = node.get("Target")
                resolved = target.lstrip("/") if target.startswith("/") else \
                    posixpath.normpath(posixpath.join(directory, target))
                assert resolved in found, f"{name}: {target} is missing"
    for name in overrides:
        assert name in found, f"an Override for the missing part {name}"
