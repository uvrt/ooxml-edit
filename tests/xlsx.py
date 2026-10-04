"""An independent look at a chart's embedded workbook, for checking what the library wrote.

Deliberately not :mod:`ooxml_edit.charts.workbook`: a check that reads the workbook with the
code that wrote it proves only that the code agrees with itself.  This is the zipfile, lxml
and the format, nothing else.
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile

from lxml import etree

X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _cell(ref: str) -> tuple[int, int]:
    letters, digits = re.fullmatch(r"\$?([A-Z]+)\$?(\d+)", ref).groups()
    column = 0
    for letter in letters:
        column = column * 26 + ord(letter) - 64
    return int(digits), column


def _rels(archive: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    directory, name = posixpath.split(part)
    path = posixpath.join(directory, "_rels", name + ".rels")
    if path not in archive.namelist():
        return {}
    result = {}
    for node in etree.fromstring(archive.read(path)):
        target = node.get("Target")
        resolved = target.lstrip("/") if target.startswith("/") else \
            posixpath.normpath(posixpath.join(directory, target))
        result[node.get("Id")] = (node.get("Type").rsplit("/", 1)[-1], resolved)
    return result


class Sheet:
    def __init__(self, cells: dict[tuple[int, int], object], tables: list[etree._Element],
                 dimension: str | None) -> None:
        self.cells = cells
        self.tables = tables
        self.dimension = dimension

    def value(self, ref: str):
        return self.cells.get(_cell(ref))

    def range(self, first: str, last: str) -> list:
        (r1, c1), (r2, c2) = _cell(first), _cell(last)
        return [self.cells.get((r, c)) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]


class Book:
    """Sheets by name, cells by reference, shared strings resolved."""

    def __init__(self, data: bytes) -> None:
        self.archive = zipfile.ZipFile(io.BytesIO(data))
        assert self.archive.testzip() is None
        names = self.archive.namelist()
        for name in names:
            if name.endswith((".xml", ".rels")):
                etree.fromstring(self.archive.read(name))  # well-formed, every part
        root_rels = _rels(self.archive, "")
        workbook = next(t for k, t in root_rels.values() if k == "officeDocument")
        self.workbook_part = workbook
        relationships = _rels(self.archive, workbook)
        self.strings: list[str] = []
        self.string_counts = None
        for kind, target in relationships.values():
            if kind == "sharedStrings":
                root = etree.fromstring(self.archive.read(target))
                for item in root.findall(X + "si"):
                    self.strings.append("".join(t.text or "" for t in item.iter(X + "t")))
                self.string_counts = (root.get("count"), root.get("uniqueCount"))
        self.sheet_parts = {}
        for node in etree.fromstring(self.archive.read(workbook)).iter(X + "sheet"):
            self.sheet_parts[node.get("name")] = relationships[node.get(R + "id")][1]
        self.relationships = relationships

    def sheet(self, name: str) -> Sheet:
        part = self.sheet_parts[name]
        root = etree.fromstring(self.archive.read(part))
        cells = {}
        rows = []
        for row in root.iter(X + "row"):
            rows.append(int(row.get("r")))
            columns = []
            for cell in row.findall(X + "c"):
                position = _cell(cell.get("r"))
                assert position[0] == int(row.get("r")), cell.get("r")
                columns.append(position[1])
                kind = cell.get("t", "n")
                value_node = cell.find(X + "v")
                if kind == "inlineStr":
                    value = "".join(t.text or "" for t in cell.iter(X + "t"))
                elif value_node is None:
                    value = None
                elif kind == "s":
                    value = self.strings[int(value_node.text)]
                elif kind in {"str", "e"}:
                    value = value_node.text
                else:
                    value = float(value_node.text)
                cells[position] = value
            assert columns == sorted(columns) and len(set(columns)) == len(columns), \
                f"cells out of order in row {row.get('r')}"
        assert rows == sorted(rows) and len(set(rows)) == len(rows), "rows out of order"
        tables = [etree.fromstring(self.archive.read(target))
                  for kind, target in _rels(self.archive, part).values() if kind == "table"]
        dimension = root.find(X + "dimension")
        return Sheet(cells, tables, None if dimension is None else dimension.get("ref"))

    def count_shared(self) -> int:
        total = 0
        for part in self.sheet_parts.values():
            total += sum(1 for cell in etree.fromstring(self.archive.read(part)).iter(X + "c")
                         if cell.get("t") == "s")
        return total


_FORMULA = re.compile(r"('?)(.+?)\1!(\$?[A-Z]+\$?\d+)(?::(\$?[A-Z]+\$?\d+))?")


def cache_points(cache: etree._Element) -> list:
    """A cache's points by ``idx`` (``None`` for a gap), checking ``ptCount`` covers them."""
    count = int(cache.find(C + "ptCount").get("val"))
    holder = cache.find(C + "lvl") if cache.find(C + "lvl") is not None else cache
    values = [None] * count
    seen = set()
    for pt in holder.findall(C + "pt"):
        index = int(pt.get("idx"))
        assert 0 <= index < count, f"pt idx {index} outside ptCount {count}"
        assert index not in seen, f"pt idx {index} twice"
        seen.add(index)
        values[index] = pt.find(C + "v").text
    indexes = [int(pt.get("idx")) for pt in holder.findall(C + "pt")]
    assert indexes == sorted(indexes), "points out of order"
    return values


def check_chart_against_workbook(chart_xml: bytes, workbook: bytes | None, *,
                                 edited: bool = True) -> int:
    """Every formula's cells hold exactly what its cache says; tables cover the data and are
    named after their headers.  Returns the number of formulas checked.

    ``edited=False`` is for a workbook exactly as its generator wrote it, whose table ``ref``
    may carry a stray character (``A1:D4'``) the library cleans up only when it edits one."""
    root = etree.fromstring(chart_xml)
    book = Book(workbook) if workbook is not None else None
    checked = 0
    for ref in root.iter(C + "strRef", C + "numRef", C + "multiLvlStrRef"):
        formula = ref.find(C + "f").text
        cache = next((child for child in ref if child.tag in {
            C + "strCache", C + "numCache", C + "multiLvlStrCache"}), None)
        if cache is None or book is None:
            continue
        match = _FORMULA.fullmatch(formula)
        assert match, formula
        sheet = book.sheet(match.group(2).replace("''", "'"))
        first, last = match.group(3), match.group(4) or match.group(3)
        cells = sheet.range(first.replace("$", ""), last.replace("$", ""))
        points = cache_points(cache)
        assert len(cells) == len(points), f"{formula}: {len(cells)} cells, {len(points)} points"
        for index, (cell, point) in enumerate(zip(cells, points)):
            if ref.tag == C + "numRef":
                expected = None if point is None else float(point)
                assert cell == expected, f"{formula}[{index}]: cell {cell!r}, cache {point!r}"
            else:
                shown = cell if not isinstance(cell, float) else (
                    str(int(cell)) if cell.is_integer() else repr(cell))
                assert (shown or "") == (point or ""), \
                    f"{formula}[{index}]: cell {cell!r}, cache {point!r}"
        checked += 1
    if book is not None:
        for name in book.sheet_parts:
            check_sheet_tables(book, name, edited=edited)
        if book.string_counts is not None:
            count, unique = book.string_counts
            assert unique is None or int(unique) == len(book.strings)
            assert count is None or int(count) == book.count_shared()
    return checked


def check_sheet_tables(book: Book, name: str, *, edited: bool = True) -> None:
    sheet = book.sheet(name)
    if sheet.cells and sheet.dimension:
        rows = [r for r, _ in sheet.cells]
        columns = [c for _, c in sheet.cells]
        first, _, last = sheet.dimension.partition(":")
        (r1, c1), (r2, c2) = _cell(first), _cell(last or first)
        assert r1 <= min(rows) and c1 <= min(columns) and r2 >= max(rows) and c2 >= max(columns)
    for table in sheet.tables:
        ref = table.get("ref")
        if not edited:
            ref = re.match(r"[A-Z]+\d+:[A-Z]+\d+", ref).group(0)
        assert re.fullmatch(r"[A-Z]+\d+:[A-Z]+\d+", ref), f"table ref {ref!r}"
        first, last = ref.split(":")
        (r1, c1), (r2, c2) = _cell(first), _cell(last)
        columns = table.find(X + "tableColumns")
        names = [c.get("name") for c in columns.findall(X + "tableColumn")]
        assert int(columns.get("count")) == len(names) == c2 - c1 + 1, (ref, names)
        assert len({n.lower() for n in names}) == len(names), f"duplicate names {names}"
        ids = [c.get("id") for c in columns.findall(X + "tableColumn")]
        assert len(set(ids)) == len(ids)
        if table.get("headerRowCount", "1") != "0":
            for offset, column_name in enumerate(names):
                header = sheet.cells.get((r1, c1 + offset))
                if isinstance(header, str) and header.strip():
                    assert column_name == header, f"{column_name!r} under header {header!r}"
        auto = table.find(X + "autoFilter")
        if auto is not None and edited:
            assert auto.get("ref") == ref
