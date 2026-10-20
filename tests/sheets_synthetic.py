"""A made-up workbook template, built in code: a corner shop's stock list.

What :mod:`ooxml_edit.sheets` is tested against when it edits an existing template.  Every
part is written here, nothing comes from an application: a ``Stock`` sheet with title rows,
headers on row 9 and typed input rows below (item ids made by a formula, a shared formula
group for the line values, a lookup into a hidden sheet), a hidden ``Lists`` sheet, a
``Totals`` sheet of cross-sheet formulas, defined names, standard and ``x14`` data
validations, conditional formats, merges, print settings with their printer-settings part,
``mc:AlternateContent``, attributes of a later version (``x14ac:dyDescent``) and a made-up
extension in ``extLst`` that no application knows.

``Shop(rless=True)`` leaves ``r`` off the input rows and their cells, as the format allows;
``Shop(shared=False)`` writes inline strings and no shared-string table; ``date1904`` uses
the 1904 date system.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from xml.sax.saxutils import escape

X = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
X14AC = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac"
X14 = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"
XM = "http://schemas.microsoft.com/office/excel/2006/main"
#: A made-up extension namespace: what no application knows, and must keep.
SHOP = "urn:example:corner-shop:notes"
SHOP_URI = "{6F1C3E2A-0000-4000-8000-5E0D5E0D5E0D}"

_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

#: The input rows: id formula, item, category, quantity, unit price, in stock, restocked.
ITEMS = [
    ("Pencil", "Stationery", 120, 0.35, True, 46023),
    ("Notebook", "Stationery", 40, 2.5, True, 46030),
    ("Kettle", "Kitchen", 3, 24.0, False, 45990),
    ("Mug", "Kitchen", 18, 4.75, True, 46001),
    ("Torch", "Hardware", 0, 9.99, False, 45975),
]
CATEGORIES = [("Stationery", 0.06), ("Kitchen", 0.21), ("Hardware", 0.21), ("Garden", 0.21)]
FIRST_INPUT = 10  # headers on row 9


@dataclass
class Shop:
    shared: bool = True
    rless: bool = False
    date1904: bool = False
    calc_chain: bool = True

    # -- strings ---------------------------------------------------------------------------

    def __post_init__(self) -> None:
        self._strings: list[str] = []
        self._uses = 0

    def _text(self, ref: str | None, text: str, style: int | None = None) -> str:
        r = f' r="{ref}"' if ref else ""
        s = f' s="{style}"' if style is not None else ""
        if not self.shared:
            return f'<c{r}{s} t="inlineStr"><is><t>{escape(text)}</t></is></c>'
        if text not in self._strings:
            self._strings.append(text)
        self._uses += 1
        return f'<c{r}{s} t="s"><v>{self._strings.index(text)}</v></c>'

    # -- parts -----------------------------------------------------------------------------

    def stock(self) -> str:
        rows = []
        rows.append(f'<row r="1" spans="1:9" ht="21" customHeight="1" x14ac:dyDescent="0.35">'
                    f'{self._text("A1", "Corner shop stock list", 1)}</row>')
        rows.append(f'<row r="2" spans="1:9" x14ac:dyDescent="0.25">'
                    f'{self._text("A2", "Counted at closing; prices without tax")}</row>')
        rows.append('<row r="4" spans="1:9" x14ac:dyDescent="0.25">'
                    f'{self._text("A4", "Counted by")}<c r="B4" s="5"/></row>')
        headers = ["Id", "Item", "Category", "Quantity", "Unit price", "Value", "In stock",
                   "Restocked", "Tax rate"]
        cells = "".join(self._text(f"{chr(65 + i)}9", h, 4) for i, h in enumerate(headers))
        rows.append(f'<row r="9" spans="1:9" x14ac:dyDescent="0.25">{cells}</row>')
        last = FIRST_INPUT + len(ITEMS) - 1
        for offset, (item, category, quantity, price, in_stock, restocked) in enumerate(ITEMS):
            n = FIRST_INPUT + offset

            def ref(column: str) -> str | None:
                return None if self.rless else f"{column}{n}"

            ident = (f'<c{_r(ref("A"))} s="6" t="str"><f>"P-"&amp;TEXT(ROW()-{FIRST_INPUT - 1},'
                     f'"000")</f><v>P-{offset + 1:03d}</v></c>')
            if offset == 0:
                value = (f'<c{_r(ref("F"))} s="3"><f t="shared" ref="F{n}:F{last}" si="0">'
                         f'D{n}*E{n}</f><v>{_num(quantity * price)}</v></c>')
            else:
                value = (f'<c{_r(ref("F"))} s="3"><f t="shared" si="0"/>'
                         f'<v>{_num(quantity * price)}</v></c>')
            rate = dict(CATEGORIES)[category]
            row = (ident + self._text(ref("B"), item, 5) + self._text(ref("C"), category, 5)
                   + f'<c{_r(ref("D"))} s="5"><v>{quantity}</v></c>'
                   + f'<c{_r(ref("E"))} s="3"><v>{_num(price)}</v></c>' + value
                   + f'<c{_r(ref("G"))} s="5" t="b"><v>{int(in_stock)}</v></c>'
                   + f'<c{_r(ref("H"))} s="2"><v>{restocked - (1462 if self.date1904 else 0)}'
                   f'</v></c>'
                   + f'<c{_r(ref("I"))} s="7"><f>VLOOKUP(C{n},Lists!$A$2:$B$5,2,FALSE)</f>'
                   f'<v>{_num(rate)}</v></c>')
            r_attr = "" if self.rless else f' r="{n}" spans="1:9"'
            rows.append(f'<row{r_attr} x14ac:dyDescent="0.25">{row}</row>')
        data = "".join(rows)
        return (
            _DECL
            + f'<worksheet xmlns="{X}" xmlns:r="{R}" xmlns:mc="{MC}" mc:Ignorable="x14ac shop" '
            f'xmlns:x14ac="{X14AC}" xmlns:shop="{SHOP}" shop:template="stock-2">'
            '<sheetPr><tabColor rgb="FF2E7D32"/><pageSetUpPr fitToPage="1"/></sheetPr>'
            f'<dimension ref="A1:I{last}"/>'
            '<sheetViews><sheetView tabSelected="1" workbookViewId="0">'
            '<pane ySplit="9" topLeftCell="A10" activePane="bottomLeft" state="frozen"/>'
            '<selection pane="bottomLeft" activeCell="D10" sqref="D10"/></sheetView></sheetViews>'
            '<sheetFormatPr defaultRowHeight="14.5" x14ac:dyDescent="0.35"/>'
            '<cols><col min="1" max="1" width="9" customWidth="1"/>'
            '<col min="2" max="3" width="16" customWidth="1"/></cols>'
            f'<sheetData>{data}</sheetData>'
            '<mergeCells count="2"><mergeCell ref="A1:I1"/><mergeCell ref="A2:I2"/></mergeCells>'
            f'<conditionalFormatting sqref="D{FIRST_INPUT}:D{last}">'
            '<cfRule type="cellIs" dxfId="0" priority="2" operator="lessThan"><formula>5</formula>'
            '</cfRule></conditionalFormatting>'
            f'<conditionalFormatting sqref="G{FIRST_INPUT}:G{last}">'
            '<cfRule type="expression" dxfId="1" priority="1">'
            f'<formula>NOT(G{FIRST_INPUT})</formula></cfRule></conditionalFormatting>'
            '<dataValidations count="2">'
            f'<dataValidation type="whole" operator="greaterThanOrEqual" allowBlank="1" '
            f'showErrorMessage="1" errorTitle="Quantity" error="Whole numbers from 0" '
            f'sqref="D{FIRST_INPUT}:D200"><formula1>0</formula1></dataValidation>'
            f'<dataValidation type="date" allowBlank="1" showInputMessage="1" '
            f'sqref="H{FIRST_INPUT}:H200"><formula1>36526</formula1></dataValidation>'
            '</dataValidations>'
            '<printOptions gridLines="1"/>'
            '<pageMargins left="0.5" right="0.5" top="0.75" bottom="0.75" header="0.3" '
            'footer="0.3"/>'
            '<pageSetup paperSize="9" orientation="landscape" fitToHeight="0" r:id="rId1"/>'
            '<headerFooter><oddHeader>&amp;CStock list</oddHeader>'
            '<oddFooter>&amp;RPage &amp;P of &amp;N</oddFooter></headerFooter>'
            '<rowBreaks count="1" manualBreakCount="1"><brk id="40" max="16383" man="1"/>'
            '</rowBreaks>'
            '<mc:AlternateContent><mc:Choice Requires="shop">'
            '<shop:panel anchor="K2" size="small"/></mc:Choice><mc:Fallback/>'
            '</mc:AlternateContent>'
            '<extLst>'
            f'<ext uri="{{CCE6A557-97BC-4b89-ADB6-D9C93CAAB3DF}}" xmlns:x14="{X14}">'
            f'<x14:dataValidations count="1" xmlns:xm="{XM}">'
            '<x14:dataValidation type="list" allowBlank="1" showErrorMessage="1">'
            '<x14:formula1><xm:f>Lists!$A$2:$A$5</xm:f></x14:formula1>'
            f'<xm:sqref>C{FIRST_INPUT}:C200</xm:sqref></x14:dataValidation>'
            '</x14:dataValidations></ext>'
            f'<ext uri="{SHOP_URI}" xmlns:shop="{SHOP}">'
            '<shop:notes version="2"><shop:note cell="B4">who counted</shop:note>'
            '<shop:opaque>keep me as I am</shop:opaque></shop:notes></ext>'
            '</extLst></worksheet>')

    def lists(self) -> str:
        rows = [f'<row r="1">{self._text("A1", "Category")}{self._text("B1", "Tax")}</row>']
        for index, (name, rate) in enumerate(CATEGORIES, start=2):
            rows.append(f'<row r="{index}">{self._text(f"A{index}", name)}'
                        f'<c r="B{index}" s="7"><v>{_num(rate)}</v></c></row>')
        return (_DECL + f'<worksheet xmlns="{X}" xmlns:r="{R}">'
                f'<dimension ref="A1:B{len(CATEGORIES) + 1}"/>'
                f'<sheetData>{"".join(rows)}</sheetData>'
                '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" '
                'footer="0.3"/></worksheet>')

    def totals(self) -> str:
        last = FIRST_INPUT + len(ITEMS) - 1
        total = sum(q * p for _, _, q, p, _, _ in ITEMS)
        rows = (f'<row r="1">{self._text("A1", "Stock value")}'
                f'<c r="B1" s="3"><f>SUM(Stock!F{FIRST_INPUT}:F{last})</f>'
                f'<v>{_num(total)}</v></c></row>'
                f'<row r="2">{self._text("A2", "Items out")}'
                f'<c r="B2"><f>COUNTIF(Stock!G{FIRST_INPUT}:G{last},FALSE)</f>'
                f'<v>{sum(1 for i in ITEMS if not i[4])}</v></c></row>'
                f'<row r="3">{self._text("A3", "Kitchen rate")}'
                f'<c r="B3" s="7"><f>INDEX(TaxRates,2)</f><v>0.21</v></c></row>')
        return (_DECL + f'<worksheet xmlns="{X}" xmlns:r="{R}"><dimension ref="A1:B3"/>'
                f'<sheetData>{rows}</sheetData>'
                '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" '
                'footer="0.3"/></worksheet>')

    def workbook(self) -> str:
        last = FIRST_INPUT + len(ITEMS) - 1
        pr = ' date1904="1"' if self.date1904 else ""
        return (
            _DECL + f'<workbook xmlns="{X}" xmlns:r="{R}" xmlns:mc="{MC}" mc:Ignorable="shop" '
            f'xmlns:shop="{SHOP}">'
            '<fileVersion appName="xl" lastEdited="7" lowestEdited="7" rupBuild="10000"/>'
            f'<workbookPr{pr} defaultThemeVersion="166925"/>'
            '<mc:AlternateContent><mc:Choice Requires="shop"><shop:origin path="templates"/>'
            '</mc:Choice></mc:AlternateContent>'
            '<bookViews><workbookView xWindow="0" yWindow="0" windowWidth="28800" '
            'windowHeight="12300"/></bookViews>'
            '<sheets><sheet name="Stock" sheetId="1" r:id="rId1"/>'
            '<sheet name="Lists" sheetId="2" state="hidden" r:id="rId2"/>'
            '<sheet name="Totals" sheetId="3" r:id="rId3"/></sheets>'
            '<definedNames>'
            '<definedName name="_xlnm.Print_Area" localSheetId="0">'
            f'Stock!$A$1:$I${last}</definedName>'
            '<definedName name="_xlnm.Print_Titles" localSheetId="0">Stock!$9:$9</definedName>'
            '<definedName name="Categories">Lists!$A$2:$A$5</definedName>'
            '<definedName name="TaxRates">Lists!$B$2:$B$5</definedName>'
            '<definedName name="Markup">1.4</definedName>'
            '</definedNames>'
            '<calcPr calcId="191029"/>'
            f'<extLst><ext uri="{SHOP_URI}" xmlns:shop="{SHOP}"><shop:owner team="front"/>'
            '</ext></extLst></workbook>')

    def styles(self) -> str:
        return (
            _DECL + f'<styleSheet xmlns="{X}" xmlns:mc="{MC}" mc:Ignorable="x14ac" '
            f'xmlns:x14ac="{X14AC}">'
            '<numFmts count="2"><numFmt numFmtId="164" formatCode="yyyy\\-mm\\-dd"/>'
            '<numFmt numFmtId="165" formatCode="&quot;€&quot;\\ #,##0.00"/></numFmts>'
            '<fonts count="2" x14ac:knownFonts="1"><font><sz val="11"/><name val="Aptos"/>'
            '</font><font><b/><sz val="14"/><name val="Aptos"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border>'
            '</borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
            '</cellStyleXfs>'
            '<cellXfs count="8">'
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
            '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" '
            'applyNumberFormat="1"><protection locked="0"/></xf>'
            '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" '
            'applyNumberFormat="1"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1">'
            '<alignment horizontal="center"/></xf>'
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" '
            'applyProtection="1"><protection locked="0"/></xf>'
            '<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" '
            'applyNumberFormat="1"/>'
            '<xf numFmtId="10" fontId="0" fillId="0" borderId="0" xfId="0" '
            'applyNumberFormat="1"/>'
            '</cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            '<dxfs count="2"><dxf><font><color rgb="FF9C0006"/></font></dxf>'
            '<dxf><fill><patternFill><bgColor rgb="FFFFEB9C"/></patternFill></fill></dxf></dxfs>'
            '<tableStyles count="0" defaultTableStyle="TableStyleMedium2"/>'
            f'<extLst><ext uri="{SHOP_URI}" xmlns:shop="{SHOP}"><shop:palette name="shop"/>'
            '</ext></extLst></styleSheet>')

    def shared_strings(self) -> str:
        items = "".join(f"<si><t>{escape(text)}</t></si>" for text in self._strings)
        # A rich item: what a plain string never reuses.
        items += ('<si><r><rPr><b/></rPr><t>Pencil</t></r><r><t xml:space="preserve"> (box)'
                  '</t></r></si>')
        return (_DECL + f'<sst xmlns="{X}" count="{self._uses}" '
                f'uniqueCount="{len(self._strings) + 1}">{items}</sst>')

    def calc_chain_xml(self) -> str:
        last = FIRST_INPUT + len(ITEMS) - 1
        cells = "".join(f'<c r="{c}{n}" i="1"/>' for n in range(FIRST_INPUT, last + 1)
                        for c in "AFI")
        return (_DECL + f'<calcChain xmlns="{X}">{cells}<c r="B1" i="3"/><c r="B2"/>'
                '<c r="B3"/></calcChain>')

    # -- the package -----------------------------------------------------------------------

    def parts(self) -> dict[str, bytes]:
        self._strings, self._uses = [], 0
        sheets = {"xl/worksheets/sheet1.xml": self.stock(),
                  "xl/worksheets/sheet2.xml": self.lists(),
                  "xl/worksheets/sheet3.xml": self.totals()}
        rels = [("rId1", "worksheet", "worksheets/sheet1.xml"),
                ("rId2", "worksheet", "worksheets/sheet2.xml"),
                ("rId3", "worksheet", "worksheets/sheet3.xml"),
                ("rId4", "styles", "styles.xml")]
        overrides = [("/xl/workbook.xml", "spreadsheetml.sheet.main+xml"),
                     ("/xl/styles.xml", "spreadsheetml.styles+xml")]
        overrides += [(f"/{p}", "spreadsheetml.worksheet+xml") for p in sheets]
        parts: dict[str, str | bytes] = {}
        if self.shared:
            rels.append(("rId5", "sharedStrings", "sharedStrings.xml"))
            overrides.append(("/xl/sharedStrings.xml", "spreadsheetml.sharedStrings+xml"))
        if self.calc_chain:
            rels.append(("rId6", "calcChain", "calcChain.xml"))
            overrides.append(("/xl/calcChain.xml", "spreadsheetml.calcChain+xml"))
        ct = (_DECL + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
              '<Default Extension="bin" ContentType="application/vnd.openxmlformats-'
              'officedocument.spreadsheetml.printerSettings"/>'
              '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
              'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
              + "".join(f'<Override PartName="{name}" ContentType="application/vnd.'
                        f'openxmlformats-officedocument.{kind}"/>' for name, kind in overrides)
              + "</Types>")
        parts["[Content_Types].xml"] = ct
        parts["_rels/.rels"] = _rels([("rId1", "officeDocument", "xl/workbook.xml")])
        parts["xl/workbook.xml"] = self.workbook()
        parts["xl/_rels/workbook.xml.rels"] = _rels(rels)
        parts.update(sheets)
        parts["xl/worksheets/_rels/sheet1.xml.rels"] = _rels(
            [("rId1", "printerSettings", "../printerSettings/printerSettings1.bin")])
        # Made-up bytes: a part nobody parses, kept as it is.
        parts["xl/printerSettings/printerSettings1.bin"] = bytes(range(256)) * 2
        parts["xl/styles.xml"] = self.styles()
        if self.shared:
            parts["xl/sharedStrings.xml"] = self.shared_strings()
        if self.calc_chain:
            parts["xl/calcChain.xml"] = self.calc_chain_xml()
        return {name: data.encode("utf-8") if isinstance(data, str) else data
                for name, data in parts.items()}

    def to_bytes(self) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in self.parts().items():
                archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 2, 3, 4, 6)), data,
                                 compress_type=zipfile.ZIP_DEFLATED)
        return buffer.getvalue()


def _r(ref: str | None) -> str:
    return f' r="{ref}"' if ref else ""


def _num(value: float) -> str:
    value = round(value, 10)
    return str(int(value)) if float(value).is_integer() else repr(value)


def _rels(items: list[tuple[str, str, str]]) -> str:
    body = "".join(f'<Relationship Id="{rid}" Type="{_REL}{kind}" Target="{target}"/>'
                   for rid, kind, target in items)
    return (_DECL + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'relationships">{body}</Relationships>')
