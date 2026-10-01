"""The workbook behind a chart: an embedded ``.xlsx`` edited cell by cell, with lxml only.

A chart's numbers live twice: in the chart part's caches, which is what every application
draws from, and in the embedded workbook, which is what PowerPoint's "Edit Data" opens.  An
edit that updates one and not the other is invisible until someone clicks Edit Data and
PowerPoint shows -- and then writes back -- the old numbers.  So every chart edit in
:mod:`.chart` comes here as well.

The workbook is a package inside the package.  It is opened with the format-neutral core
(:meth:`~pptx_agent.core.opc.OpcPackage.open_embedded`), so the same losslessness holds one
level down: sheets, styles, themes and anything else nobody touched are written back as the
bytes that were read.  What is SpreadsheetML -- cell references, shared strings, tables -- is
here, outside the core.

What this module keeps in step, because Excel (which PowerPoint's Edit Data *is*) reports a
repair otherwise:

* cells in row and column order, rows with their ``spans`` hint and the sheet's
  ``dimension``;
* shared strings (``count``/``uniqueCount``) when the workbook uses them, inline strings
  when it does not;
* a formula a value replaces is removed, and so is the calculation chain, which Excel
  rebuilds (a chain naming a cell with no formula is a repair);
* tables (``xl/tables``) over the data: their ``ref`` and ``autoFilter`` grow and shrink with
  the lines inserted and deleted, a column added or removed inside one gets or loses its
  ``tableColumn``, and every column's name follows its header cell, unique as Excel insists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Iterator

from lxml import etree

from ..core.opc import OpcPackage, normalize_part_path
from ..core.xml import Element, qn, register_namespaces, remove

SML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
register_namespaces({"x": SML_NS})

_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
REL_WORKSHEET = _REL + "worksheet"
REL_SHARED_STRINGS = _REL + "sharedStrings"
REL_TABLE = _REL + "table"
REL_CALC_CHAIN = _REL + "calcChain"
CT_SHARED_STRINGS = "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"

_X = "{%s}" % SML_NS
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

#: Excel's grid.
MAX_ROW = 1_048_576
MAX_COLUMN = 16_384

CellValue = "str | float | int | bool | None"


# ------------------------------------------------------------------------------------------
# References
# ------------------------------------------------------------------------------------------


def column_letters(index: int) -> str:
    """1 -> ``A``, 27 -> ``AA``."""
    if not 1 <= index <= MAX_COLUMN:
        raise ValueError(f"column {index} is outside the sheet")
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def column_index(letters: str) -> int:
    """``A`` -> 1, ``AA`` -> 27."""
    value = 0
    for letter in letters.upper():
        value = value * 26 + ord(letter) - 64
    return value


_CELL = re.compile(r"(\$?)([A-Za-z]{1,3})(\$?)(\d{1,7})")


@dataclass(frozen=True, order=True)
class Cell:
    """A cell position, 1-based: ``Cell(2, 1)`` is ``A2``."""

    row: int
    column: int

    @property
    def ref(self) -> str:
        return f"{column_letters(self.column)}{self.row}"

    @classmethod
    def parse(cls, text: str) -> "Cell":
        match = _CELL.fullmatch(text.strip())
        if match is None:
            raise ValueError(f"{text!r} is not a cell reference")
        return cls(int(match.group(4)), column_index(match.group(2)))

    def moved(self, rows: int = 0, columns: int = 0) -> "Cell":
        return Cell(self.row + rows, self.column + columns)


@dataclass(frozen=True)
class Area:
    """A rectangle of cells, inclusive."""

    first: Cell
    last: Cell

    @property
    def ref(self) -> str:
        if self.first == self.last:
            return self.first.ref
        return f"{self.first.ref}:{self.last.ref}"

    @property
    def rows(self) -> int:
        return self.last.row - self.first.row + 1

    @property
    def columns(self) -> int:
        return self.last.column - self.first.column + 1

    def contains(self, cell: Cell) -> bool:
        return (self.first.row <= cell.row <= self.last.row
                and self.first.column <= cell.column <= self.last.column)

    def cells(self) -> Iterator[Cell]:
        for row in range(self.first.row, self.last.row + 1):
            for column in range(self.first.column, self.last.column + 1):
                yield Cell(row, column)

    @classmethod
    def parse(cls, text: str, *, lenient: bool = False) -> "Area":
        """``A1:D4`` or ``B2``.  ``lenient`` accepts trailing junk after a valid range -- a
        table ``ref`` some generators write as ``A1:D4'`` -- so it can be written back clean."""
        pattern = r"\$?[A-Za-z]{1,3}\$?\d{1,7}(?::\$?[A-Za-z]{1,3}\$?\d{1,7})?"
        match = (re.match if lenient else re.fullmatch)(pattern, text.strip())
        if match is None:
            raise ValueError(f"{text!r} is not a range")
        head, _, tail = match.group(0).partition(":")
        first = Cell.parse(head)
        last = Cell.parse(tail) if tail else first
        return cls(Cell(min(first.row, last.row), min(first.column, last.column)),
                   Cell(max(first.row, last.row), max(first.column, last.column)))


@dataclass(frozen=True)
class SheetRange:
    """A chart formula such as ``Sheet1!$B$2:$B$4``: one rectangle on one named sheet.

    The spelling is kept -- the sheet name's quoting and which coordinates were absolute --
    so a formula that is moved or resized is written back the way it was written.
    """

    sheet: str
    area: Area
    #: The sheet as written, quotes included: ``'My data'``.
    sheet_text: str
    #: ``(column absolute, row absolute)`` for the first and the last corner.
    absolute: tuple[tuple[bool, bool], tuple[bool, bool]] = ((True, True), (True, True))

    @property
    def formula(self) -> str:
        def spell(cell: Cell, flags: tuple[bool, bool]) -> str:
            return (("$" if flags[0] else "") + column_letters(cell.column)
                    + ("$" if flags[1] else "") + str(cell.row))

        first = spell(self.area.first, self.absolute[0])
        if self.area.first == self.area.last:
            return f"{self.sheet_text}!{first}"
        return f"{self.sheet_text}!{first}:{spell(self.area.last, self.absolute[1])}"

    def with_area(self, area: Area) -> "SheetRange":
        return replace(self, area=area)

    @property
    def is_line(self) -> bool:
        return self.area.rows == 1 or self.area.columns == 1

    @property
    def length(self) -> int:
        return max(self.area.rows, self.area.columns)

    def cell(self, index: int) -> Cell:
        """The ``index``-th cell of a one-row or one-column range."""
        if not self.is_line:
            raise ValueError(f"{self.formula} is not a single row or column")
        if not 0 <= index < self.length:
            raise IndexError(f"{self.formula} has no cell {index}")
        if self.area.columns == 1:
            return self.area.first.moved(rows=index)
        return self.area.first.moved(columns=index)


_SHEET_PART = r"(?:'(?:[^']|'')+'|[^'!:()\s,]+)"
_FORMULA = re.compile(rf"\s*({_SHEET_PART})!(\$?[A-Za-z]{{1,3}}\$?\d{{1,7}})"
                      rf"(?::(\$?[A-Za-z]{{1,3}}\$?\d{{1,7}}))?\s*")


def parse_formula(text: str | None) -> SheetRange | None:
    """A chart's ``c:f`` as one rectangle, or ``None`` for anything else (several areas, a
    defined name, an external workbook) -- which a caller then edits in the cache only."""
    if not text:
        return None
    match = _FORMULA.fullmatch(text)
    if match is None:
        return None
    sheet_text, head, tail = match.group(1), match.group(2), match.group(3) or match.group(2)
    if sheet_text.startswith("["):
        return None  # another workbook
    sheet = sheet_text[1:-1].replace("''", "'") if sheet_text.startswith("'") else sheet_text
    first_match, last_match = _CELL.fullmatch(head), _CELL.fullmatch(tail)
    flags = ((bool(first_match.group(1)), bool(first_match.group(3))),
             (bool(last_match.group(1)), bool(last_match.group(3))))
    first, last = Cell.parse(head), Cell.parse(tail)
    if first.row > last.row or first.column > last.column:
        return None
    return SheetRange(sheet, Area(first, last), sheet_text, flags)


# ------------------------------------------------------------------------------------------
# The workbook
# ------------------------------------------------------------------------------------------


class Workbook:
    """An ``.xlsx`` package, opened for editing.  Sheets are found by name."""

    def __init__(self, package: OpcPackage) -> None:
        self.package = package
        main = package.main_document_part()
        if main is None or package.tree(main) is None:
            raise ValueError("not a workbook: no main part")
        self.part = main
        self._sheets: dict[str, Worksheet] = {}
        #: Cells whose formula was replaced by a value; the calculation chain goes with them.
        self._dropped_formula = False

    @classmethod
    def open(cls, data: bytes) -> "Workbook":
        return cls(OpcPackage.open(data))

    @property
    def changed(self) -> bool:
        return bool(self.package.dirty_parts or self.package._raw_changes)

    def to_bytes(self) -> bytes:
        self._finish()
        return self.package.to_bytes()

    # -- sheets ------------------------------------------------------------------------------

    @property
    def sheet_names(self) -> list[str]:
        root = self.package.tree(self.part)
        return [node.get("name", "") for node in root.iter(_X + "sheet")]

    def sheet(self, name: str) -> "Worksheet | None":
        if name in self._sheets:
            return self._sheets[name]
        root = self.package.tree(self.part)
        for node in root.iter(_X + "sheet"):
            if node.get("name") == name:
                part = self.package.related_part(self.part, node.get(qn("r:id")))
                if part is None or self.package.tree(part) is None:
                    return None
                sheet = Worksheet(self, part, name)
                self._sheets[name] = sheet
                return sheet
        return None

    def _worksheet_parts(self) -> list[str]:
        return [part for part in self.package.related_parts_of_type(self.part, REL_WORKSHEET)
                if self.package.has_part(part)]

    # -- shared strings ----------------------------------------------------------------------

    def _shared_strings_part(self) -> str | None:
        parts = self.package.related_parts_of_type(self.part, REL_SHARED_STRINGS)
        return parts[0] if parts and self.package.has_part(parts[0]) else None

    def shared_string(self, index: int) -> str:
        part = self._shared_strings_part()
        root = self.package.tree(part) if part else None
        items = [] if root is None else root.findall(_X + "si")
        if not 0 <= index < len(items):
            raise IndexError(f"shared string {index} does not exist")
        return _rich_text(items[index])

    def add_shared_string(self, text: str) -> int | None:
        """Index of ``text`` in the shared-string table, added if new; ``None`` when the
        workbook has no shared-string table (its strings are then written inline)."""
        part = self._shared_strings_part()
        if part is None:
            return None
        root = self.package.tree(part)
        items = root.findall(_X + "si")
        for index, item in enumerate(items):
            if item.find(_X + "r") is None and _rich_text(item) == text:
                return index
        item = etree.SubElement(root, _X + "si")
        node = etree.SubElement(item, _X + "t")
        _set_text(node, text)
        self.package.mark_dirty(part)
        return len(items)

    def _finish(self) -> None:
        """Bring the workbook-wide bookkeeping up to date before it is written."""
        part = self._shared_strings_part()
        if part is not None and (part in self.package.dirty_parts or self._touched_sheets()):
            root = self.package.tree(part)
            unique = str(len(root.findall(_X + "si")))
            total = 0
            for sheet in self._worksheet_parts():
                tree = self.package.tree(sheet)
                total += sum(1 for cell in tree.iter(_X + "c") if cell.get("t") == "s")
            if root.get("count") is not None and root.get("count") != str(total):
                root.set("count", str(total))
                self.package.mark_dirty(part)
            if root.get("uniqueCount") is not None and root.get("uniqueCount") != unique:
                root.set("uniqueCount", unique)
                self.package.mark_dirty(part)
        if self._dropped_formula:
            for chain in self.package.related_parts_of_type(self.part, REL_CALC_CHAIN):
                for rel in list(self.package.relationships(self.part).values()):
                    if rel.target_part == chain:
                        self.package.remove_relationship(self.part, rel.id)
                if self.package.has_part(chain):
                    self.package.remove_part(chain)
            self._dropped_formula = False

    def _touched_sheets(self) -> set[str]:
        return {sheet.part for sheet in self._sheets.values()
                if sheet.part in self.package.dirty_parts}


class Worksheet:
    """One sheet's cells, read and written by position."""

    def __init__(self, workbook: Workbook, part: str, name: str) -> None:
        self.workbook = workbook
        self.part = normalize_part_path(part)
        self.name = name
        self._root = workbook.package.tree(self.part)
        self._data = self._root.find(_X + "sheetData")
        if self._data is None:
            raise ValueError(f"sheet {name!r} has no sheetData")
        self._normalised = False

    # -- reading -----------------------------------------------------------------------------

    def value(self, cell: Cell) -> "str | float | bool | None":
        """What the cell holds: text, a number, a boolean, an error's text, or ``None``."""
        node = self._find(cell)
        return None if node is None else self._read(node)

    def is_empty(self, cell: Cell) -> bool:
        """No value at all -- a cell that only carries a style counts as empty."""
        node = self._find(cell)
        return node is None or self._read(node) is None and node.find(_X + "f") is None

    def _read(self, node: Element):
        kind = node.get("t", "n")
        if kind == "inlineStr":
            inline = node.find(_X + "is")
            return None if inline is None else _rich_text(inline)
        raw = node.find(_X + "v")
        if raw is None or raw.text is None:
            return None
        text = raw.text
        if kind == "s":
            return self.workbook.shared_string(int(text))
        if kind in {"str", "e"}:
            return text
        if kind == "b":
            return text.strip() in {"1", "true"}
        try:
            number = float(text)
        except ValueError:
            return text
        return int(number) if number.is_integer() and "e" not in text.lower() \
            and "." not in text else number

    # -- writing -----------------------------------------------------------------------------

    def set_value(self, cell: Cell, value: "str | float | int | None", *,
                  style_from: Cell | None = None) -> None:
        """Write a number, a string or nothing (``None`` empties the cell, keeping its style).

        A new cell takes its style from ``style_from`` when given (the neighbour whose line it
        continues), so an added point is formatted like its series.
        """
        if isinstance(value, bool):
            raise TypeError("booleans are not chart values")
        node = self._find(cell)
        if node is None:
            if value is None:
                return
            node = self._create(cell)
            if style_from is not None:
                template = self._find(style_from)
                if template is not None and template.get("s") is not None:
                    node.set("s", template.get("s"))
        elif node.find(_X + "f") is None and self._holds(node, value):
            return
        self._drop_formula(node)
        for child in list(node):
            if child.tag in {_X + "v", _X + "is"}:
                node.remove(child)
        if value is None:
            node.attrib.pop("t", None)
            if node.get("s") is None and len(node) == 0:
                self._remove_cell(node)
        elif isinstance(value, str):
            index = self.workbook.add_shared_string(value)
            if index is None:
                node.set("t", "inlineStr")
                inline = etree.SubElement(node, _X + "is")
                _set_text(etree.SubElement(inline, _X + "t"), value)
            else:
                node.set("t", "s")
                etree.SubElement(node, _X + "v").text = str(index)
        else:
            node.attrib.pop("t", None)
            etree.SubElement(node, _X + "v").text = number_text(value)
        self._touch()

    def _holds(self, node: Element, value) -> bool:
        """Whether the cell already holds exactly ``value``, stored as the same kind."""
        current = self._read(node)
        if value is None:
            return current is None
        if isinstance(value, str):
            return node.get("t") in {"s", "inlineStr", "str"} and current == value
        return node.get("t", "n") == "n" and not isinstance(current, str) \
            and current is not None and float(current) == float(value)

    def move(self, source: Cell, target: Cell) -> None:
        """Move a cell -- value, formula and style -- onto an empty position."""
        node = self._find(source)
        existing = self._find(target)
        if existing is not None:
            self._remove_cell(existing)
        if node is None:
            self._touch()
            return
        self._remove_cell(node)
        node.set("r", target.ref)
        self._place(node, target)
        self._touch()

    def clear(self, cell: Cell) -> None:
        """Remove a cell entirely, style and all."""
        node = self._find(cell)
        if node is not None:
            self._drop_formula(node)
            self._remove_cell(node)
            self._touch()

    # -- tables ------------------------------------------------------------------------------

    def tables(self) -> list["Table"]:
        package = self.workbook.package
        found = []
        for part in package.related_parts_of_type(self.part, REL_TABLE):
            root = package.tree(part)
            if root is not None:
                found.append(Table(self, part, root))
        return found

    def shift_tables(self, axis: str, position: int, delta: int, span: tuple[int, int]) -> None:
        """Keep tables in step with a line of cells inserted (``delta=1``) before ``position``
        or deleted (``delta=-1``) at it, along ``axis`` (``"row"`` or ``"column"``), in the
        lines ``span`` of the other axis.  A table the change touches grows, shrinks or moves
        the way Excel's own insert and delete would move it -- and an insertion just past a
        table's end grows it, which is how a chart's data range is extended."""
        for table in self.tables():
            table.shift(axis, position, delta, span)

    def sync_tables(self) -> None:
        """Every table column named after its header cell, uniquely, as Excel requires."""
        for table in self.tables():
            table.sync_names()

    # -- internals ---------------------------------------------------------------------------

    def _touch(self) -> None:
        self.workbook.package.mark_dirty(self.part)
        self._update_dimension()

    def _normalise(self) -> None:
        """Give every row and cell an explicit ``r`` -- optional in the format, needed to
        address them -- the first time the sheet is written."""
        if self._normalised:
            return
        row_number = 0
        changed = False
        for row in self._data.findall(_X + "row"):
            row_number = int(row.get("r")) if row.get("r") else row_number + 1
            if row.get("r") is None:
                row.set("r", str(row_number))
                changed = True
            column = 0
            for node in row.findall(_X + "c"):
                column = Cell.parse(node.get("r")).column if node.get("r") else column + 1
                if node.get("r") is None:
                    node.set("r", Cell(row_number, column).ref)
                    changed = True
        if changed:
            self.workbook.package.mark_dirty(self.part)
        self._normalised = True

    def _rows(self) -> Iterator[tuple[int, Element]]:
        row_number = 0
        for row in self._data.findall(_X + "row"):
            row_number = int(row.get("r")) if row.get("r") else row_number + 1
            yield row_number, row

    def _find(self, cell: Cell) -> Element | None:
        for number, row in self._rows():
            if number != cell.row:
                continue
            column = 0
            for node in row.findall(_X + "c"):
                column = Cell.parse(node.get("r")).column if node.get("r") else column + 1
                if column == cell.column:
                    return node
            return None
        return None

    def _row(self, number: int, *, create: bool) -> Element | None:
        self._normalise()
        before = None
        for existing, row in self._rows():
            if existing == number:
                return row
            if existing > number:
                before = row
                break
        if not create:
            return None
        row = etree.Element(_X + "row")
        row.set("r", str(number))
        if before is not None:
            before.addprevious(row)
        else:
            self._data.append(row)
        return row

    def _create(self, cell: Cell) -> Element:
        node = etree.Element(_X + "c")
        node.set("r", cell.ref)
        self._place(node, cell)
        return node

    def _place(self, node: Element, cell: Cell) -> None:
        self._normalise()
        row = self._row(cell.row, create=True)
        for sibling in row.findall(_X + "c"):
            if Cell.parse(sibling.get("r")).column > cell.column:
                sibling.addprevious(node)
                break
        else:
            extensions = row.find(_X + "extLst")
            if extensions is not None:
                extensions.addprevious(node)
            else:
                row.append(node)
        _update_spans(row)

    def _remove_cell(self, node: Element) -> None:
        row = node.getparent()
        remove(node)
        if row is not None:
            if len(row) == 0 and not set(row.attrib.keys()) - {"r", "spans"}:
                remove(row)
            else:
                _update_spans(row)

    def _drop_formula(self, node: Element) -> None:
        formula = node.find(_X + "f")
        if formula is None:
            return
        if formula.get("t") == "shared" and formula.get("ref"):
            raise ValueError(f"{node.get('r')} holds a shared formula other cells use; "
                             f"edit the workbook in Excel")
        node.remove(formula)
        self.workbook._dropped_formula = True

    def _update_dimension(self) -> None:
        cells = [Cell.parse(node.get("r")) for node in self._data.iter(_X + "c")
                 if node.get("r")]
        dimension = self._root.find(_X + "dimension")
        if dimension is None:
            return
        if not cells:
            wanted = "A1"
        else:
            area = Area(Cell(min(c.row for c in cells), min(c.column for c in cells)),
                        Cell(max(c.row for c in cells), max(c.column for c in cells)))
            wanted = area.ref
        if dimension.get("ref") != wanted:
            dimension.set("ref", wanted)


class Table:
    """An Excel table (``xl/tables/tableN.xml``) on a sheet."""

    def __init__(self, sheet: Worksheet, part: str, root: Element) -> None:
        self.sheet = sheet
        self.part = part
        self.root = root

    @property
    def area(self) -> Area | None:
        try:
            return Area.parse(self.root.get("ref", ""), lenient=True)
        except ValueError:
            return None

    @property
    def header_rows(self) -> int:
        raw = self.root.get("headerRowCount")
        return 1 if raw is None else int(raw)

    def _columns(self) -> tuple[Element | None, list[Element]]:
        container = self.root.find(_X + "tableColumns")
        return container, ([] if container is None else container.findall(_X + "tableColumn"))

    def _set_area(self, area: Area) -> None:
        if self.root.get("ref") != area.ref:
            self.root.set("ref", area.ref)
            self._mark()
        auto = self.root.find(_X + "autoFilter")
        if auto is not None and auto.get("ref") != area.ref:
            auto.set("ref", area.ref)
            self._mark()

    def _mark(self) -> None:
        self.sheet.workbook.package.mark_dirty(self.part)

    def shift(self, axis: str, position: int, delta: int, span: tuple[int, int]) -> None:
        area = self.area
        if area is None:
            return
        if axis == "row":
            low, high, across = area.first.row, area.last.row, (area.first.column, area.last.column)
        else:
            low, high, across = area.first.column, area.last.column, (area.first.row, area.last.row)
        if across[1] < span[0] or across[0] > span[1]:
            return  # beside the change, not in its lines
        if delta > 0:
            if position < low:
                low, high = low + delta, high + delta
            elif position <= high + 1:
                high += delta
            else:
                return
        else:
            if position < low:
                low, high = low + delta, high + delta
            elif position <= high:
                high += delta
            else:
                return
        if high < low:
            return
        if axis == "row":
            new = Area(Cell(low, area.first.column), Cell(high, area.last.column))
        else:
            new = Area(Cell(area.first.row, low), Cell(area.last.row, high))
            self._shift_columns(position - area.first.column, delta)
        self._set_area(new)

    def _shift_columns(self, offset: int, delta: int) -> None:
        container, columns = self._columns()
        if container is None:
            return
        if delta > 0:
            used = [int(c.get("id", "0")) for c in columns if (c.get("id") or "").isdigit()]
            node = etree.Element(_X + "tableColumn")
            node.set("id", str(max(used, default=0) + 1))
            node.set("name", f"Column{max(used, default=0) + 1}")
            if offset < len(columns):
                columns[max(offset, 0)].addprevious(node)
            else:
                (columns[-1].addnext(node) if columns else container.append(node))
        elif 0 <= offset < len(columns):
            remove(columns[offset])
        container.set("count", str(len(container.findall(_X + "tableColumn"))))
        self._mark()

    def sync_names(self) -> None:
        """Name each column after its header cell, as Excel requires of a table."""
        area = self.area
        if area is not None and self.root.get("ref") != area.ref:
            self._set_area(area)  # a ref with junk after it (``A1:D4'``), written back clean
        container, columns = self._columns()
        if area is None or container is None or not self.header_rows:
            return
        taken: set[str] = set()
        for offset, column in enumerate(columns):
            header = self.sheet.value(Cell(area.first.row, area.first.column + offset))
            if isinstance(header, float) or isinstance(header, int):
                header = number_text(header)
            name = header if isinstance(header, str) and header.strip() else column.get("name")
            if not name or name.lower() in taken:
                base, number = (name or "Column"), 1
                while f"{base}{number}".lower() in taken:
                    number += 1
                name = f"{base}{number}"
            taken.add(name.lower())
            if column.get("name") != name:
                column.set("name", name)
                self._mark()


# ------------------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------------------


def number_text(value: float | int) -> str:
    """A number the way Excel writes it: ``3``, not ``3.0``; shortest exact decimal otherwise."""
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{value!r} is not a finite number")
    if number.is_integer() and abs(number) < 1e15:
        return str(int(number))
    return repr(number)


def _rich_text(item: Element) -> str:
    """The text of a shared string or inline string: plain ``t``, or runs' ``t`` joined."""
    plain = item.find(_X + "t")
    if plain is not None and item.find(_X + "r") is None:
        return plain.text or ""
    return "".join(node.text or "" for node in item.iter(_X + "t")
                   if node.getparent().tag == _X + "r")


def _set_text(node: Element, text: str) -> None:
    node.text = text
    if text != text.strip() or "\n" in text:
        node.set(_XML_SPACE, "preserve")
    else:
        node.attrib.pop(_XML_SPACE, None)


def _update_spans(row: Element) -> None:
    """Keep a row's optional ``spans`` hint (its first and last used column) true."""
    if row.get("spans") is None:
        return
    columns = [Cell.parse(node.get("r")).column for node in row.findall(_X + "c")
               if node.get("r")]
    if not columns:
        return
    wanted = f"{min(columns)}:{max(columns)}"
    if row.get("spans") != wanted:
        row.set("spans", wanted)
