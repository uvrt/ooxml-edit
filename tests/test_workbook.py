"""The SpreadsheetML pieces charts lean on: references, chart formulas, cells and tables."""

from __future__ import annotations

import pytest

from conftest import FIXTURE_DIR
from pptx_agent import Document
from pptx_agent.edit.workbook import (
    Area,
    Cell,
    Workbook,
    column_index,
    column_letters,
    number_text,
    parse_formula,
)


def test_columns_and_cells():
    assert [column_letters(i) for i in (1, 26, 27, 52, 703, 16384)] == [
        "A", "Z", "AA", "AZ", "AAA", "XFD"]
    assert all(column_index(column_letters(i)) == i for i in range(1, 2000))
    assert Cell.parse("$C$12") == Cell(12, 3) and Cell(12, 3).ref == "C12"
    with pytest.raises(ValueError):
        column_letters(16385)


def test_chart_formulas():
    simple = parse_formula("Sheet1!$B$2:$B$4")
    assert (simple.sheet, simple.area.ref, simple.length) == ("Sheet1", "B2:B4", 3)
    assert simple.cell(2) == Cell(4, 2)
    quoted = parse_formula("'Q3 ''data'''!$A$2:$D$2")
    assert quoted.sheet == "Q3 'data'" and quoted.cell(3) == Cell(2, 4)
    moved = quoted.with_area(Area(Cell(2, 1), Cell(2, 5)))
    assert moved.formula == "'Q3 ''data'''!$A$2:$E$2"
    relative = parse_formula("Sheet1!B1")
    assert relative.formula == "Sheet1!B1"
    for unusual in ("(Sheet1!$A$1,Sheet1!$A$3)", "[1]Sheet1!$A$1", "MyName", "", None,
                    "Sheet1!$B$4:$B$2"):
        assert parse_formula(unusual) is None, unusual


def test_numbers_are_written_like_excel():
    assert [number_text(v) for v in (3, 3.0, 11.7, -0.5, 1e20)] == [
        "3", "3", "11.7", "-0.5", "1e+20"]
    with pytest.raises(ValueError):
        number_text(float("nan"))


def test_lenient_table_ref():
    assert Area.parse("A1:D4'", lenient=True).ref == "A1:D4"
    with pytest.raises(ValueError):
        Area.parse("A1:D4'")


def test_cells_stay_in_order_and_the_dimension_follows(financial_report):
    document = Document.open(str(financial_report))
    book = Workbook(document.package.open_embedded(
        "ppt/embeddings/Microsoft_Excel_Worksheet2.xlsx"))
    sheet = book.sheet("Sheet1")
    sheet.set_value(Cell(7, 6), 5)        # below and right of everything
    sheet.set_value(Cell(1, 5), "Header")  # into an existing row, at its end
    sheet.set_value(Cell(6, 1), "Lone")    # a new row between others
    from xlsx import Book

    reread = Book(book.to_bytes()).sheet("Sheet1")  # asserts row and cell order itself
    assert reread.value("F7") == 5.0 and reread.value("E1") == "Header"
    assert reread.dimension == "A1:F7"
    sheet.clear(Cell(7, 6))
    assert Book(book.to_bytes()).sheet("Sheet1").dimension == "A1:E6"


def test_a_missing_sheet_is_none():
    document = Document.open(str(FIXTURE_DIR / "authoring-integration.pptx"))
    book = Workbook(document.package.open_embedded(
        "ppt/embeddings/Microsoft_Excel_Worksheet1.xlsx"))
    assert book.sheet_names == ["Sheet1"] and book.sheet("Nope") is None
