"""ooxml_edit.sheets: an existing template's cells written losslessly.

Against :mod:`sheets_synthetic`'s made-up stock list: one cell edited keeps everything else
in its sheet and every other part as it was, a no-op leaves the package's bytes, every kind
of value is stored as its kind, and the bookkeeping (shared strings, styles, the calculation
chain, ``calcPr``) is exactly the footprint :mod:`ooxml_edit.sheets.footprint` allows.
"""

from __future__ import annotations

import datetime as dt
import io
import re
import zipfile

import pytest
from lxml import etree

from sheets_synthetic import FIRST_INPUT, SHOP_URI, Shop
from ooxml_edit.history import History
from ooxml_edit.opc import OpcPackage
from ooxml_edit.sheets import (MAX_TEXT, Cell, Content, Formula, SharedFormulaError, Workbook,
                               date_serial, outside_footprint)

X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
STOCK = "xl/worksheets/sheet1.xml"


def _open(**options) -> tuple[bytes, Workbook]:
    data = Shop(**options).to_bytes()
    return data, Workbook(OpcPackage.open(data))


def _parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _cell(data: bytes, ref: str, part: str = STOCK) -> etree._Element:
    root = etree.fromstring(_parts(data)[part])
    for node in root.iter(X + "c"):
        if node.get("r") == ref:
            return node
    raise AssertionError(f"no cell {ref}")


# -- lossless ----------------------------------------------------------------------------------


@pytest.mark.parametrize("options", [{}, {"shared": False}, {"rless": True}],
                         ids=["shared", "inline", "rless"])
def test_a_no_op_leaves_the_package_bytes(options):
    data, book = _open(**options)
    stock = book.sheet("Stock")
    assert stock.write(Cell(FIRST_INPUT, 4), 120) is False       # the quantity it holds
    assert stock.write(Cell(FIRST_INPUT, 2), "Pencil") is False  # the text it holds
    assert stock.write(Cell(FIRST_INPUT, 7), True) is False
    assert stock.write(Cell(30, 30), None) is False             # a blank where there is none
    assert stock.write(Cell(FIRST_INPUT + 1, 1),
                       Formula('"P-"&TEXT(ROW()-9,"000")')) is False
    book.finish()
    assert not book.changed and book.to_bytes() == data


def test_one_cell_edited_keeps_the_rest_of_its_sheet_and_every_other_part():
    data, book = _open()
    assert book.sheet("Stock").write(Cell(FIRST_INPUT + 2, 4), 7) is True
    edited = book.to_bytes()
    assert outside_footprint(data, edited, cells={"Stock": ["D12"]}) == []
    before, after = _parts(data), _parts(edited)
    assert [name for name in before if before[name] != after.get(name)] == [STOCK]
    # The sheet's own bytes, between the root's start tag and extLst: only the one cell
    # differs.  (lxml writes a start tag's namespace declarations before its attributes,
    # so the root's and each ext's start tags are the same declarations and attributes in
    # another order: the footprint check above compares those as XML.)
    old, new = before[STOCK].decode(), after[STOCK].decode()

    def body(text: str) -> str:
        return text[text.index(">", text.index("<worksheet")) + 1:text.index("<extLst>")]

    assert body(new) == body(old).replace(
        '<c r="D12" s="5"><v>3</v></c>', '<c r="D12" s="5"><v>7</v></c>')
    # What no application knows is still there, as it was.
    assert '<shop:opaque>keep me as I am</shop:opaque>' in new and SHOP_URI in new
    assert "<x14:dataValidations" in new and "<mc:AlternateContent>" in new


def test_rows_without_r_keep_their_spelling():
    data, book = _open(rless=True)
    stock = book.sheet("Stock")
    assert stock.value(Cell(FIRST_INPUT + 3, 2)) == "Mug"
    stock.write(Cell(FIRST_INPUT + 3, 4), 20)          # an existing cell in an r-less row
    stock.write(Cell(FIRST_INPUT + 3, 10), "new")      # a new cell at that row's end
    stock.write(Cell(FIRST_INPUT + 5, 2), "Lamp")      # a new row after them
    stock.write(Cell(6, 1), "Note")                    # a new row between explicit ones
    edited = book.to_bytes()
    assert outside_footprint(data, edited, cells={"Stock": [
        f"D{FIRST_INPUT + 3}", f"J{FIRST_INPUT + 3}", f"B{FIRST_INPUT + 5}", "A6"]}) == []
    root = etree.fromstring(_parts(edited)[STOCK])
    rows = root.find(X + "sheetData").findall(X + "row")
    assert [row.get("r") for row in rows] == ["1", "2", "4", "6", "9"] + [None] * 5 + ["15"]
    mug = rows[8]
    assert [c.get("r") for c in mug.findall(X + "c")] == [None] * 9 + [f"J{FIRST_INPUT + 3}"]
    reread = Workbook(OpcPackage.open(edited)).sheet("Stock")
    assert reread.value(Cell(FIRST_INPUT + 3, 4)) == 20
    assert reread.value(Cell(FIRST_INPUT + 5, 2)) == "Lamp"
    assert root.find(X + "dimension").get("ref") == f"A1:J{FIRST_INPUT + 5}"


def test_the_footprint_check_catches_what_an_edit_may_not_do():
    data = Shop().to_bytes()
    parts = _parts(data)

    def tampered(part: str, old: str, new: str) -> bytes:
        changed = dict(parts)
        changed[part] = parts[part].replace(old.encode(), new.encode())
        assert changed[part] != parts[part]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, body in changed.items():
                archive.writestr(name, body)
        return buffer.getvalue()

    assert outside_footprint(data, data) == []
    found = outside_footprint(data, tampered(STOCK, "keep me as I am", "changed"))
    assert found == [f"{STOCK}: extLst changed"]
    assert outside_footprint(data, tampered(STOCK, '<c r="D12" s="5"><v>3</v>',
                                                        '<c r="D12" s="5"><v>4</v>'),
                             cells={"Stock": ["D10"]}) == [
        f"{STOCK}: cell D12 changed but was not written"]
    assert outside_footprint(data, tampered("xl/workbook.xml", 'state="hidden"', "")) == [
        "xl/workbook.xml: changed beyond calcPr"]
    assert outside_footprint(data, tampered("xl/sharedStrings.xml", "Pencil<", "Pen<"))
    assert outside_footprint(data, tampered("xl/styles.xml", 'fontId="1"', 'fontId="0"'))
    assert outside_footprint(data, tampered(STOCK, 'x14ac:dyDescent="0.35"/>', '/>'))
    assert outside_footprint(data, tampered(STOCK, 'notes" shop:template', 'other" shop:template'))


# -- values ------------------------------------------------------------------------------------


def test_text_that_starts_with_an_equals_sign_stays_text():
    data, book = _open()
    stock = book.sheet("Stock")
    stock.write(Cell(4, 2), "=SUM(D10:D14)")
    edited = book.to_bytes()
    node = _cell(edited, "B4")
    assert node.get("t") == "s" and node.find(X + "f") is None
    assert Workbook(OpcPackage.open(edited)).sheet("Stock").value(Cell(4, 2)) == "=SUM(D10:D14)"
    assert node.get("s") == "5"  # the format kept
    assert "xl/calcChain.xml" in _parts(edited)  # no formula touched


def test_every_kind_of_value_is_stored_as_its_kind():
    data, book = _open()
    stock = book.sheet("Stock")
    stock.write(Cell(4, 2), True)
    stock.write(Cell(4, 3), 12.5)
    stock.write(Cell(4, 4), "0012")                      # text that looks like a number
    stock.write(Cell(FIRST_INPUT, 8), dt.date(2026, 3, 1))
    stock.write(Cell(FIRST_INPUT, 2), None)              # a blank that keeps its style
    edited = book.to_bytes()
    assert outside_footprint(data, edited, cells={"Stock": [
        "B4", "C4", "D4", "H10", "B10"]}) == []
    assert (_cell(edited, "B4").get("t"), _cell(edited, "B4").findtext(X + "v")) == ("b", "1")
    assert _cell(edited, "C4").get("t") is None and _cell(edited, "C4").findtext(X + "v") == "12.5"
    assert _cell(edited, "D4").get("t") == "s"
    assert _cell(edited, "H10").findtext(X + "v") == "46082" and _cell(edited, "H10").get("s") == "2"
    blank = _cell(edited, "B10")
    assert blank.get("s") == "5" and len(blank) == 0 and blank.get("t") is None
    reread = Workbook(OpcPackage.open(edited)).sheet("Stock")
    assert reread.content(Cell(4, 2)) == Content("bool", True, style=5)
    assert reread.content(Cell(4, 4)).kind == "text" and reread.value(Cell(4, 4)) == "0012"


def test_dates_follow_the_workbooks_date_system():
    assert date_serial(dt.date(1900, 1, 1)) == 1
    assert date_serial(dt.date(1900, 2, 28)) == 59
    assert date_serial(dt.date(1900, 3, 1)) == 61
    assert date_serial(dt.date(2026, 3, 1)) == 46082
    assert date_serial(dt.date(2026, 3, 1), date1904=True) == 46082 - 1462
    assert date_serial(dt.datetime(2026, 3, 1, 18)) == 46082.75
    assert date_serial(dt.time(6)) == 0.25
    with pytest.raises(ValueError, match="1 January 1904"):
        date_serial(dt.date(1903, 12, 31), date1904=True)
    with pytest.raises(ValueError, match="time zone"):
        date_serial(dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc))
    _, book = _open(date1904=True)
    assert book.date1904
    book.sheet("Stock").write(Cell(FIRST_INPUT, 8), dt.date(2026, 3, 1))
    assert book.sheet("Stock").value(Cell(FIRST_INPUT, 8)) == 46082 - 1462


def test_a_formula_is_written_only_when_given_as_one():
    data, book = _open()
    stock = book.sheet("Stock")
    stock.write(Cell(4, 2), Formula("=Markup*2", 2.8))
    stock.write(Cell(4, 3), Formula('IF(B4>2,"high","")', ""))
    stock.write(Cell(4, 4), Formula("TODAY()"))
    assert book.request_full_calculation() is True
    edited = book.to_bytes()
    assert outside_footprint(data, edited, cells={"Stock": ["B4", "C4", "D4"]}) == []
    assert "xl/calcChain.xml" not in _parts(edited)
    assert b"calcChain" not in _parts(edited)["[Content_Types].xml"]
    assert b"calcChain" not in _parts(edited)["xl/_rels/workbook.xml.rels"]
    b4, c4, d4 = (_cell(edited, ref) for ref in ("B4", "C4", "D4"))
    assert b4.findtext(X + "f") == "Markup*2" and b4.findtext(X + "v") == "2.8"
    assert c4.get("t") == "str" and c4.findtext(X + "v") == ""
    assert d4.find(X + "v") is None and d4.get("t") is None
    workbook = etree.fromstring(_parts(edited)["xl/workbook.xml"])
    assert workbook.find(X + "calcPr").attrib == {"calcId": "191029", "fullCalcOnLoad": "1"}
    reread = Workbook(OpcPackage.open(edited)).sheet("Stock")
    # A formula whose result is "" is not empty: a later allocation must not reuse it.
    assert not reread.is_empty(Cell(4, 3)) and not reread.is_empty(Cell(4, 4))
    assert reread.content(Cell(4, 3)) == Content("text", "", 'IF(B4>2,"high","")', "normal", 0)


def test_the_master_of_a_shared_formula_is_refused_and_nothing_changes():
    data, book = _open()
    stock = book.sheet("Stock")
    with pytest.raises(SharedFormulaError) as caught:
        stock.write(Cell(FIRST_INPUT, 6), 1, style=0)
    assert (caught.value.cell, caught.value.ref, caught.value.si) == (
        "F10", f"F10:F{FIRST_INPUT + 4}", "0")
    assert not book.changed and book.to_bytes() == data
    # A member leaves its group: its own formula goes, the master's range stays.
    stock.write(Cell(FIRST_INPUT + 1, 6), 100)
    assert _cell(book.to_bytes(), "F11").find(X + "f") is None


def test_values_are_checked_before_anything_is_touched():
    data, book = _open()
    stock = book.sheet("Stock")
    for value, error in [("x" * (MAX_TEXT + 1), ValueError), (float("nan"), ValueError),
                         (object(), TypeError), (b"bytes", TypeError)]:
        with pytest.raises(error):
            stock.write(Cell(FIRST_INPUT, 2), value)
    with pytest.raises(ValueError):
        stock.write(Cell(1, 16_385), 1)
    with pytest.raises(ValueError):
        stock.write(Cell(4, 2), 1, style=-1)
    assert not book.changed and book.to_bytes() == data
    stock.write(Cell(4, 2), "x" * MAX_TEXT)  # at the limit: written whole
    assert len(Workbook(OpcPackage.open(book.to_bytes())).sheet("Stock").value(Cell(4, 2))) \
        == MAX_TEXT


# -- shared strings ----------------------------------------------------------------------------


def test_shared_strings_are_indexed_once_and_appended():
    data, book = _open()
    stock = book.sheet("Stock")
    stock.write(Cell(4, 2), "Kitchen")        # already there: reused
    stock.write(Cell(4, 3), "Pencil (box)")   # only as rich text: added as plain
    stock.write(Cell(5, 2), "Pencil (box)")
    stock.write(Cell(5, 3), "  spaced ")
    edited = book.to_bytes()
    assert outside_footprint(data, edited, cells={"Stock": ["B4", "C4", "B5", "C5"]}) == []
    sst = etree.fromstring(_parts(edited)["xl/sharedStrings.xml"])
    items = sst.findall(X + "si")
    before = etree.fromstring(_parts(data)["xl/sharedStrings.xml"])
    assert len(items) == len(before) + 2 == int(sst.get("uniqueCount"))
    used = sum(1 for part in ("xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml",
                              "xl/worksheets/sheet3.xml")
               for c in etree.fromstring(_parts(edited)[part]).iter(X + "c") if c.get("t") == "s")
    assert int(sst.get("count")) == used
    assert _cell(edited, "B5").findtext(X + "v") == _cell(edited, "C4").findtext(X + "v")
    assert items[-1].find(X + "t").get("{http://www.w3.org/XML/1998/namespace}space") == \
        "preserve"


def test_strings_are_inline_without_a_shared_table():
    data, book = _open(shared=False)
    book.sheet("Stock").write(Cell(4, 2), "Lamp")
    edited = book.to_bytes()
    assert _cell(edited, "B4").get("t") == "inlineStr"
    assert outside_footprint(data, edited, cells={"Stock": ["B4"]}) == []


# -- styles ------------------------------------------------------------------------------------


def test_the_style_index_reuses_and_appends_cell_formats():
    data, book = _open()
    styles = book.styles
    assert styles.count == 8
    assert styles.number_format(2).code == "yyyy\\-mm\\-dd" and styles.is_date(2) is True
    assert styles.is_date(5) is False and styles.is_date(0) is False
    assert styles.number_format(6).id == 49 and styles.number_format(6).code is None
    # 5 is unlocked plain; with the date format of 2 it is not 2 (that one has no
    # applyProtection), so it is appended -- once.
    dated = styles.with_number_format(5, "yyyy\\-mm\\-dd")
    assert dated == 8 and styles.with_number_format(5, "yyyy\\-mm\\-dd") == 8
    assert styles.with_number_format(2, 164) == 2                  # already has it
    assert styles.with_number_format(0, 14) == 9 and styles.is_date(9) is True
    coded = styles.with_number_format(0, "0.000")
    assert styles.number_format(coded).code == "0.000" and styles.number_format(coded).id == 166
    assert styles.is_date(styles.with_number_format(0, 31)) is None  # language-dependent
    book.sheet("Stock").write(Cell(4, 2), dt.date(2026, 3, 2), style=dated)
    edited = book.to_bytes()
    assert outside_footprint(data, edited, cells={"Stock": ["B4"]}) == []
    root = etree.fromstring(_parts(edited)["xl/styles.xml"])
    assert root.find(X + "cellXfs").get("count") == str(len(root.find(X + "cellXfs")))
    assert root.find(X + "numFmts").get("count") == "3"
    with pytest.raises(IndexError):
        styles.with_number_format(99, 14)


# -- history -----------------------------------------------------------------------------------


def test_undo_gives_the_original_bytes_and_redo_the_edited_ones():
    data, book = _open()
    history = History(book.package)
    with history.batch():
        book.sheet("Stock").write(Cell(FIRST_INPUT, 4), 99)
        book.sheet("Stock").write(Cell(4, 2), Formula("Markup"))
        book.request_full_calculation()
        book.finish()
    edited = book.package.to_bytes()
    assert history.undo() and book.package.to_bytes() == data
    # The objects follow the restored trees: a read after undo sees the original.
    assert book.sheet("Stock").value(Cell(FIRST_INPUT, 4)) == 120
    assert history.redo() and book.package.to_bytes() == edited
    assert book.sheet("Stock").value(Cell(FIRST_INPUT, 4)) == 99


def test_a_failed_batch_rolls_back_the_calculation_metadata_too():
    data, book = _open()
    history = History(book.package)
    with pytest.raises(SharedFormulaError):
        with history.batch():
            book.sheet("Stock").write(Cell(4, 2), Formula("Markup"))   # drops the chain
            book.request_full_calculation()
            book.sheet("Stock").write(Cell(FIRST_INPUT, 6), 1)          # refused
    book.finish()
    assert book.package.to_bytes() == data
    book.sheet("Stock").write(Cell(4, 3), 5)  # still writable, into the live tree
    assert outside_footprint(data, book.to_bytes(), cells={"Stock": ["C4"]}) == []


# -- the rest of the template ------------------------------------------------------------------


def test_a_hidden_sheet_and_the_names_are_kept():
    data, book = _open()
    lists = book.sheet("Lists")
    lists.write(Cell(6, 1), "Toys")
    lists.write(Cell(6, 2), 0.21, style=7)
    edited = book.to_bytes()
    assert outside_footprint(data, edited, cells={"Lists": ["A6", "B6"]}) == []
    assert _parts(edited)["xl/workbook.xml"] == _parts(data)["xl/workbook.xml"]
    assert etree.fromstring(_parts(edited)["xl/worksheets/sheet2.xml"]).find(
        X + "dimension").get("ref") == "A1:B6"


def test_many_writes_into_one_sheet():
    data, book = _open()
    stock = book.sheet("Stock")
    for row in range(20, 2020):
        stock.write(Cell(row, 2), f"item {row % 50}")
        stock.write(Cell(row, 4), row)
    edited = book.to_bytes()
    root = etree.fromstring(_parts(edited)[STOCK])
    numbers = [int(row.get("r")) for row in root.find(X + "sheetData") if row.get("r")]
    assert numbers == sorted(numbers) and root.find(X + "dimension").get("ref") == "A1:I2019"
    assert outside_footprint(data, edited) == []


def test_protection_is_reported():
    data = Shop().to_bytes()
    book = Workbook(OpcPackage.open(data))
    assert book.sheet("Stock").protected is False
    root = book.package.tree(STOCK)
    node = etree.SubElement(root, X + "sheetProtection")
    node.set("sheet", "1")
    assert book.sheet("Stock").protected is True


def test_the_charts_path_is_the_same_writer():
    import ooxml_edit.charts.workbook as old
    import ooxml_edit.sheets as sheets

    assert old.Workbook is sheets.Workbook and old.Worksheet is sheets.Worksheet
    assert old.Cell is sheets.Cell and old.number_text is sheets.number_text
    assert re.search(r"booleans are not chart values", _set_value_error())


def _set_value_error() -> str:
    _, book = _open()
    try:
        book.sheet("Stock").set_value(Cell(4, 2), True)
    except TypeError as error:
        return str(error)
    return ""


def test_full_calculation_is_asked_for_where_calcpr_belongs():
    data = Shop().to_bytes()
    parts = _parts(data)
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(b'<calcPr calcId="191029"/>', b"")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in parts.items():
            archive.writestr(name, body)
    without = buffer.getvalue()
    book = Workbook(OpcPackage.open(without))
    assert book.request_full_calculation() is True and book.request_full_calculation() is False
    edited = book.to_bytes()
    root = etree.fromstring(_parts(edited)["xl/workbook.xml"])
    names = [etree.QName(child).localname for child in root]
    assert names[names.index("definedNames") + 1] == "calcPr" and names[-1] == "extLst"
    assert outside_footprint(without, edited) == []
    book = Workbook(OpcPackage.open(edited))
    book.package.tree("xl/workbook.xml").find(X + "calcPr").set("calcMode", "manual")
    book.package.mark_dirty("xl/workbook.xml")
    assert outside_footprint(edited, book.to_bytes()) == [
        "xl/workbook.xml: calcPr changed beyond fullCalcOnLoad"]


def test_the_dimension_follows_each_write_without_finish():
    _, book = _open()
    book.sheet("Stock").write(Cell(40, 12), 1)
    root = etree.fromstring(book.package.read(STOCK))  # no finish: the tree as it is
    assert root.find(X + "dimension").get("ref") == "A1:L40"
    book.sheet("Stock").write(Cell(40, 12), None)
    root = etree.fromstring(book.package.read(STOCK))
    assert root.find(X + "dimension").get("ref") == f"A1:I{FIRST_INPUT + 4}"


# -- shared formula groups -------------------------------------------------------------------------


def _moved(text: str, rows: int, columns: int) -> str:
    """A stand-in for a formula lexer, enough for the stock list's ``D10*E10``: each relative
    reference moved by the offset."""
    def move(match: re.Match) -> str:
        column = chr(ord(match.group(1)) + columns)
        return f"{column}{int(match.group(2)) + rows}"
    return re.sub(r"([A-Z])(\d+)", move, text)


def test_a_shared_group_is_found_from_any_member():
    _, book = _open()
    stock = book.sheet("Stock")
    group = stock.shared_group(Cell(FIRST_INPUT + 2, 6))
    assert group is not None and group.si == "0" and group.master == Cell(FIRST_INPUT, 6)
    assert group.text == f"D{FIRST_INPUT}*E{FIRST_INPUT}"
    assert group.ref.ref == f"F{FIRST_INPUT}:F{FIRST_INPUT + 4}"
    assert group.members == tuple(Cell(FIRST_INPUT + i, 6) for i in range(5))
    assert stock.shared_group(Cell(FIRST_INPUT, 9)) is None      # a normal formula
    assert stock.shared_group(Cell(FIRST_INPUT, 4)) is None      # a value


@pytest.mark.parametrize("options", [{}, {"rless": True}], ids=["r", "rless"])
def test_the_master_released_hands_the_group_to_the_next_member(options):
    data, book = _open(**options)
    stock = book.sheet("Stock")
    assert stock.release_shared(Cell(FIRST_INPUT + 1, 6), _moved) == []   # a member: nothing
    changed = stock.release_shared(Cell(FIRST_INPUT, 6), _moved)
    assert changed == [Cell(FIRST_INPUT, 6), Cell(FIRST_INPUT + 1, 6)]
    assert stock.content(Cell(FIRST_INPUT, 6)).formula_type == "normal"
    master = stock.shared_group(Cell(FIRST_INPUT + 3, 6))
    assert master.master == Cell(FIRST_INPUT + 1, 6)
    assert master.text == f"D{FIRST_INPUT + 1}*E{FIRST_INPUT + 1}"
    assert master.ref.ref == f"F{FIRST_INPUT + 1}:F{FIRST_INPUT + 4}"
    stock.write(Cell(FIRST_INPUT, 6), 1)                    # now the old master can be written
    book.finish()
    edited = book.to_bytes()
    cells = {"Stock": [f"F{FIRST_INPUT}", f"F{FIRST_INPUT + 1}"]}
    assert outside_footprint(data, edited, cells=cells) == []
    assert "xl/calcChain.xml" not in _parts(edited)


def test_an_irregular_group_gives_every_member_its_own_formula():
    data, book = _open()
    stock = book.sheet("Stock")
    # F11 out of the group and a member added at J11: the members after the master are J11,
    # F12, F13, F14, whose rectangle (F11:J14) does not start at J11, the next master.
    part = book.package.tree(stock.part)
    for node in part.iter(X + "c"):
        if node.get("r") == f"F{FIRST_INPUT + 1}":
            node.remove(node.find(X + "f"))
    extra = etree.SubElement(part.find(X + "sheetData").findall(X + "row")[5], X + "c")
    extra.set("r", f"J{FIRST_INPUT + 1}")
    etree.SubElement(extra, X + "f", t="shared", si="0")
    book.package.mark_dirty(stock.part)
    stock._order = None
    changed = stock.release_shared(Cell(FIRST_INPUT, 6), _moved)
    assert Cell(FIRST_INPUT + 1, 10) in changed
    assert stock.content(Cell(FIRST_INPUT + 1, 10)) == Content(
        "blank", None, f"H{FIRST_INPUT + 1}*I{FIRST_INPUT + 1}", "normal", 0)
    assert stock.content(Cell(FIRST_INPUT + 4, 6)).formula == f"D{FIRST_INPUT + 4}*E{FIRST_INPUT + 4}"
    assert stock.shared_group(Cell(FIRST_INPUT + 4, 6)) is None


def test_a_translation_refused_changes_nothing():
    data, book = _open()
    stock = book.sheet("Stock")

    def refuse(text, rows, columns):
        raise ValueError("cannot move it")

    with pytest.raises(ValueError, match="cannot move it"):
        stock.release_shared(Cell(FIRST_INPUT, 6), refuse)
    book.finish()
    assert not book.changed and book.to_bytes() == data


def test_a_new_cell_s_inherited_format_is_its_row_s_then_its_column_s():
    _, book = _open()
    stock = book.sheet("Stock")
    root = book.package.tree(stock.part)
    columns = root.find(X + "cols")
    etree.SubElement(columns, X + "col", min="8", max="8", width="12", style="2")
    rows = {node.get("r"): node for node in root.iter(X + "row")}
    rows["4"].set("customFormat", "1")
    rows["4"].set("s", "3")
    assert stock.style(Cell(30, 8)) == 0                      # no cell there
    assert stock.style(Cell(30, 8), inherited=True) == 2      # its column's
    assert stock.style(Cell(4, 8), inherited=True) == 3       # its row's comes first
    assert stock.style(Cell(30, 2), inherited=True) == 0      # a column without a style
    assert stock.style(Cell(30, 20), inherited=True) == 0     # past every col
    assert stock.style(Cell(FIRST_INPUT, 8), inherited=True) == 2   # a cell's own
