"""ooxml_edit.sheets: rich text (runs with their marks) written into a cell.

Against :mod:`sheets_synthetic`'s stock list: a run carries the cell's whole font with its
own marks (Excel for Mac shows a run with only ``<b/>`` in its default font: measured,
:class:`~ooxml_edit.sheets.RichText`), an identical item is reused, plain text over rich
text makes it plain, the inline form is used without a shared-string table, and the
footprint is the shared strings' and styles' appends.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from lxml import etree

from sheets_synthetic import FIRST_INPUT, Shop
from ooxml_edit.history import History
from ooxml_edit.markdown import Run
from ooxml_edit.opc import OpcPackage
from ooxml_edit.sheets import Cell, RichText, Workbook, outside_footprint, rich_runs

X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RICH = RichText((Run("Total", bold=True), Run(" due "), Run("now", italic=True),
                 Run(" gone", strike=True)))


def _open(**options) -> tuple[bytes, Workbook]:
    data = Shop(**options).to_bytes()
    return data, Workbook(OpcPackage.open(data))


def _part(data: bytes, name: str) -> etree._Element:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return etree.fromstring(archive.read(name))


def _local(node) -> str:
    return etree.QName(node).localname


def test_rich_text_is_a_shared_string_of_runs_with_the_cells_font():
    before, wb = _open()
    stock = wb.sheet("Stock")
    cell = Cell(FIRST_INPUT, 2)                     # B10: format 5, font 0 (Aptos 11)
    assert stock.write(cell, RICH)
    assert stock.value(cell) == "Total due now gone"
    assert stock.runs(cell) == RICH.runs
    after = wb.to_bytes()
    item = _part(after, "xl/sharedStrings.xml").findall(X + "si")[-1]
    shapes = [[_local(p) for p in run.find(X + "rPr")] for run in item.findall(X + "r")]
    assert shapes == [["b", "sz", "rFont"], ["sz", "rFont"], ["i", "sz", "rFont"],
                      ["strike", "sz", "rFont"]]
    assert item.find(X + "r/" + X + "rPr/" + X + "rFont").get("val") == "Aptos"
    assert [t.text for t in item.iter(X + "t")] == ["Total", " due ", "now", " gone"]
    assert item.findall(X + "r/" + X + "t")[1].get(
        "{http://www.w3.org/XML/1998/namespace}space") == "preserve"
    assert outside_footprint(before, after, cells={"Stock": ["B10"]}) == []


def test_a_bold_cells_runs_stay_bold_and_take_its_size():
    _, wb = _open()
    stock = wb.sheet("Stock")
    stock.write(Cell(1, 1), RICH)                   # A1: format 1, bold Aptos 14
    item = wb.shared_item(int(stock._find(Cell(1, 1)).find(X + "v").text))
    for run in item.findall(X + "r"):
        props = run.find(X + "rPr")
        assert props.find(X + "b") is not None
        assert props.find(X + "sz").get("val") == "14"
    assert all(run.bold for run in rich_runs(item))


def test_the_same_rich_text_reuses_its_item_and_writes_nothing_twice():
    _, wb = _open()
    stock = wb.sheet("Stock")
    stock.write(Cell(FIRST_INPUT, 2), RICH)
    count = len(wb.package.tree("xl/sharedStrings.xml"))
    assert stock.write(Cell(FIRST_INPUT + 1, 2), RICH)
    assert len(wb.package.tree("xl/sharedStrings.xml")) == count
    assert not stock.write(Cell(FIRST_INPUT + 1, 2), RICH)
    reopened = Workbook.open(wb.to_bytes())
    assert not reopened.sheet("Stock").write(Cell(FIRST_INPUT + 1, 2), RICH)
    assert not reopened.changed


def test_plain_text_over_rich_text_is_plain_and_other_marks_are_another_item():
    _, wb = _open()
    stock = wb.sheet("Stock")
    cell = Cell(FIRST_INPUT, 2)
    stock.write(cell, RICH)
    assert stock.write(cell, RICH.text)             # same characters, no runs: a change
    assert stock.runs(cell) is None
    assert stock.write(cell, RichText((Run("Total due now gone", italic=True),)))
    assert stock.runs(cell) == (Run("Total due now gone", italic=True),)


def test_without_a_shared_string_table_rich_text_is_inline():
    before, wb = _open(shared=False)
    stock = wb.sheet("Stock")
    cell = Cell(FIRST_INPUT, 2)
    assert stock.write(cell, RICH)
    node = stock._find(cell)
    assert node.get("t") == "inlineStr"
    assert [_local(c) for c in node.find(X + "is")] == ["r", "r", "r", "r"]
    assert stock.runs(cell) == RICH.runs
    assert not stock.write(cell, RICH)


def test_rich_text_is_checked_before_anything_changes():
    _, wb = _open()
    with pytest.raises(ValueError):
        RichText((Run(""),))
    with pytest.raises(TypeError):
        RichText(("text",))
    with pytest.raises(ValueError):
        wb.sheet("Stock").write(Cell(FIRST_INPUT, 2), RichText((Run("x" * 40_000),)))
    assert not wb.changed


def test_undo_takes_the_rich_item_back():
    before, wb = _open()
    history = History(wb.package)
    with history.batch():
        wb.sheet("Stock").write(Cell(FIRST_INPUT, 2), RICH)
        wb.finish()
    history.undo()
    assert wb.to_bytes() == before
    assert wb.sheet("Stock").runs(Cell(FIRST_INPUT, 2)) is None


def test_rich_runs_reads_marks_and_leaves_phonetic_runs_out():
    item = etree.fromstring(
        f'<si xmlns="{X[1:-1]}"><r><rPr><b val="0"/><i/></rPr><t>a</t></r>'
        '<r><rPr><strike val="true"/></rPr><t>b</t></r><r><t>c</t></r>'
        '<rPh sb="0" eb="1"><t>x</t></rPh></si>')
    assert rich_runs(item) == (Run("a", italic=True), Run("b", strike=True), Run("c"))
    assert rich_runs(etree.fromstring(f'<si xmlns="{X[1:-1]}"><t>plain</t></si>')) is None


def test_wrapped_text_format_and_font_and_custom_height():
    before, wb = _open()
    styles = wb.styles
    assert styles.font(1).find(X + "b") is not None
    assert styles.font(999) is styles.font(0)
    wrapped = styles.with_wrap_text(5)
    assert wrapped == styles.count - 1 and styles.wraps_text(wrapped)
    assert not styles.wraps_text(5)
    assert styles.with_wrap_text(5) == wrapped     # reused
    assert styles.with_wrap_text(wrapped) == wrapped
    xf = wb.package.tree(styles.part).find(X + "cellXfs")[wrapped]
    assert xf[0].tag == X + "alignment" and xf.get("applyAlignment") == "1"
    stock = wb.sheet("Stock")
    assert stock.custom_height(1) and not stock.custom_height(2)
    assert not stock.custom_height(500)
    stock.write(Cell(FIRST_INPUT, 2), "two\nlines", style=wrapped)
    assert outside_footprint(before, wb.to_bytes(), cells={"Stock": ["B10"]}) == []
