"""Round 44 (xlsx-agent lane P): the cell writer on a sheet of many rows, each shortcut
against the long way round.

A cell's column is looked up rather than matched, the ``dimension`` is not rewritten for a
cell inside it, the sheet is measured in line, and the shared strings' index is checked at
its end rather than by counting every item (which made each new string cost a pass over a
table of many thousand): each gives what the long way gives.
"""

from __future__ import annotations

import io
import random
import zipfile

import pytest
from lxml import etree

from sheets_synthetic import Shop
from ooxml_edit.errors import ArgumentError
from ooxml_edit.opc import OpcPackage
from ooxml_edit.sheets import Area, Cell, Workbook
from ooxml_edit.sheets.workbook import _column_of

X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
STOCK = "xl/worksheets/sheet1.xml"


def _open(**options) -> tuple[bytes, Workbook]:
    data = Shop(**options).to_bytes()
    return data, Workbook(OpcPackage.open(data))


def _parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


@pytest.mark.parametrize("ref", ["A1", "B10", "XFD1048576", "AA7", "$A$1", "a1", "$c5",
                                 " D4 ", "A0", "AAAA1", "A12345678", "1", "A", "", "B-1",
                                 "Z9", "ZZ99", "XFE1"])
def test_a_column_looked_up_is_the_column_parsed(ref):
    for _ in range(2):                  # once to learn the letters, once to look them up
        try:
            expected = Cell.parse(ref).column
        except ArgumentError:
            with pytest.raises(ArgumentError):
                _column_of(ref)
            continue
        assert _column_of(ref) == expected


def _naive_bounds(sheet) -> tuple[int, int, int, int] | None:
    cells = [(number, column) for number, row in sheet._index()
             for column, _ in sheet._cells(row)]
    if not cells:
        return None
    rows = [r for r, _ in cells]
    columns = [c for _, c in cells]
    return min(rows), min(columns), max(rows), max(columns)


@pytest.mark.parametrize("seed", range(8))
def test_the_sheet_measured_in_line_is_every_cell_placed(seed):
    rng = random.Random(seed)
    _, book = _open(rless=bool(seed % 2))
    sheet = book.sheet("Stock")
    for _ in range(rng.randint(0, 6)):
        sheet.write(Cell(rng.randint(1, 60), rng.randint(1, 30)), rng.choice(["x", 1, None]))
    data = sheet._sheet_data
    rows = list(data.iterchildren(X + "row"))
    if rows and seed % 3 == 0:          # a cell out of order in its row
        row = rows[len(rows) // 2]
        cells = list(row.iterchildren(X + "c"))
        if len(cells) > 1:
            row.remove(cells[-1])
            cells[0].addprevious(cells[-1])
    sheet._order = None
    assert sheet._measure() == _naive_bounds(sheet)


def test_the_dimension_follows_every_write_and_a_change_made_under_it():
    _, book = _open()
    sheet = book.sheet("Stock")
    dimension = sheet._root.find(X + "dimension")
    sheet.write(Cell(12, 2), "inside")
    first = dimension.get("ref")
    assert Area.parse(first).contains(Cell(12, 2))
    sheet.write(Cell(11, 3), "inside again")
    assert dimension.get("ref") == first
    sheet.write(Cell(40, 20), "outside")
    grown = Area.parse(dimension.get("ref"))
    assert grown.last == Cell(40, 20)
    dimension.set("ref", "A1")          # changed under the writer: written right again
    sheet.write(Cell(12, 2), "inside, once more")
    assert dimension.get("ref") == grown.ref


def test_shared_strings_are_appended_before_the_tables_extensions():
    data, book = _open()
    parts = _parts(data)
    sst = parts["xl/sharedStrings.xml"].replace(
        b"</sst>", b'<extLst><ext uri="{00000000-0000-4000-8000-000000000001}"/></extLst></sst>')
    package = OpcPackage.open(data)
    package.replace_part("xl/sharedStrings.xml", sst)
    book = Workbook(package)
    sheet = book.sheet("Stock")
    for n in range(5):
        sheet.write(Cell(30 + n, 2), f"new {n}")
    root = etree.fromstring(_parts(book.to_bytes())["xl/sharedStrings.xml"])
    tags = [node.tag for node in root]
    assert tags[-1] == X + "extLst" and tags.count(X + "extLst") == 1
    texts = [node.findtext(X + "t") for node in root.iterchildren(X + "si")]
    assert texts[-5:] == [f"new {n}" for n in range(5)]
    assert int(root.get("uniqueCount")) == len(texts)


def test_the_strings_index_is_read_again_when_its_end_changes_under_it():
    _, book = _open()
    sheet = book.sheet("Stock")
    sheet.write(Cell(30, 2), "first")
    table = book._string_table()
    root = table[0]
    # an item appended under the index: indexed again, and found
    item = etree.SubElement(root, X + "si")
    etree.SubElement(item, X + "t").text = "added under"
    book.package.mark_dirty(book._shared_strings_part())
    assert book.add_shared_string("added under") == len(list(root.iterchildren(X + "si"))) - 1
    # the last item taken away under the index: indexed again
    root.remove(item)
    index = book.add_shared_string("after the removal")
    assert root[index].findtext(X + "t") == "after the removal"
    assert book.shared_string(index) == "after the removal"
