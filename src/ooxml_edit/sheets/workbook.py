"""SpreadsheetML cells, edited losslessly: a workbook's sheets written cell by cell, with lxml only.

A workbook is a package of parts, and an edit touches few of them: the sheets written to, the
shared strings, perhaps the styles.  The package is opened with the format-neutral core
(:class:`~ooxml_edit.opc.OpcPackage`), so every part nobody touched is written back as the
bytes that were read, and a part that was touched is the tree that was read with only the
edited cells changed: whatever this module does not know -- ``extLst`` and the validations,
conditional formats and sparklines in it, ``mc:AlternateContent``, attributes of later
versions, namespace declarations, print settings, merges -- stays as it was.  An edit that
writes what a cell already holds changes nothing, and a workbook with nothing changed is
written back as the bytes it was read from.

This is the writer behind both a chart's embedded workbook (:mod:`ooxml_edit.charts`, which
re-exports it from ``charts.workbook``) and a whole workbook edited for its own sake.  What
it keeps in step, because Excel reports a repair otherwise:

* cells in row and column order, rows with their ``spans`` hint and the sheet's
  ``dimension`` (computed once, then grown with each cell written);
* rows and cells without an ``r`` attribute, which the format allows (each is the one after
  the one before): positions are computed, and only what is written gets an ``r``;
* shared strings (``count``/``uniqueCount``) when the workbook uses them, inline strings
  when it does not, found through an index built once;
* a formula a value replaces, and a formula written, take the calculation chain with them:
  Excel rebuilds it, and a chain naming a cell with no formula is a repair;
* tables (``xl/tables``) over the data: their ``ref`` and ``autoFilter`` grow and shrink with
  the lines inserted and deleted, a column added or removed inside one gets or loses its
  ``tableColumn``, and every column's name follows its header cell, unique as Excel insists.

Values (:meth:`Worksheet.write`): text, numbers, booleans, dates (as the serial number of the
workbook's date system), a :class:`Formula`, or ``None`` for a blank that keeps its style.
Text is always text: ``"=A1"`` is written as those three characters, and only a
:class:`Formula` is a formula.  :class:`Styles` finds or adds the cell format a value needs
(a date's number format), reusing an identical one.

Every edit is a change to the package's trees, so a :class:`~ooxml_edit.history.History`
over the package undoes it exactly; the objects here re-read the live trees after an undo.
Call :meth:`Workbook.finish` at the end of each step, inside it, so the step's snapshot
holds the bookkeeping too.
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from dataclasses import dataclass, replace
from typing import Iterable, Iterator

from lxml import etree
from ..opc import OpcPackage, normalize_part_path
from ..xml import Element, qn, remove

SML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
REL_WORKSHEET = _REL + "worksheet"
REL_SHARED_STRINGS = _REL + "sharedStrings"
REL_STYLES = _REL + "styles"
REL_TABLE = _REL + "table"
REL_CALC_CHAIN = _REL + "calcChain"
CT_SHARED_STRINGS = "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"

_X = "{%s}" % SML_NS
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

#: Excel's grid.
MAX_ROW = 1_048_576
MAX_COLUMN = 16_384
#: The most characters a cell holds; longer text is refused, never cut.
MAX_TEXT = 32_767

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
# Values
# ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Formula:
    """A formula to write, as opposed to text that starts with ``=``.

    ``text`` is the formula as a cell stores it, without the ``=`` (one leading ``=`` is
    taken off); ``value`` is the cached result to store with it -- text, a number or a
    boolean -- or ``None`` for none, which Excel computes when the workbook is calculated
    (:meth:`Workbook.request_full_calculation`).
    """

    text: str
    value: "str | float | int | bool | None" = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError(f"a formula's text is a string, not {type(self.text).__name__}")
        text = self.text[1:] if self.text.startswith("=") else self.text
        if not text.strip():
            raise ValueError("a formula needs text; write None for a blank cell")
        object.__setattr__(self, "text", text)
        if self.value is not None and not isinstance(self.value, (str, bool, int, float)):
            raise TypeError("a formula's cached value is text, a number, a boolean or None, "
                            f"not {type(self.value).__name__}")


@dataclass(frozen=True)
class Content:
    """What a cell holds, as stored: what an edit compares with before it writes.

    ``kind`` is ``"text"``, ``"number"``, ``"bool"``, ``"error"`` (``value`` the code) or
    ``"blank"``; for a formula cell it is the cached result's, and ``"blank"`` when there is
    none.  ``formula`` is the cell's formula text (``""`` for a member of a shared group,
    which holds none of its own) and ``formula_type`` its ``t`` -- ``"normal"``,
    ``"shared"``, ``"array"`` or ``"dataTable"`` -- both ``None`` for a cell without one.
    ``style`` is the cell's format, an index into :class:`Styles` (0 when it has none).
    """

    kind: str
    value: "str | float | int | bool | None"
    formula: str | None = None
    formula_type: str | None = None
    style: int = 0


class SharedFormulaError(ValueError):
    """A cell holds the master of a shared formula other cells use: writing over it would
    leave them without a formula.  ``cell`` is the master's reference, ``ref`` the group's
    range and ``si`` its index.  Nothing was changed."""

    def __init__(self, cell: str, ref: str, si: str | None) -> None:
        super().__init__(f"{cell} holds a shared formula other cells use; "
                         f"edit the workbook in Excel")
        self.cell = cell
        self.ref = ref
        self.si = si


@dataclass(frozen=True)
class SharedGroup:
    """A shared formula group of one sheet (ECMA-376 Part 1, 18.3.1.40 ``f``, ``t="shared"``):
    ``si`` its index, ``master`` the cell that holds its text (``text``, the master's own
    formula) and its range (``ref``, ``None`` when the file gives none), and ``members``
    every cell of the group, the master included, in the order of the sheet.  A member's
    formula is the master's with its relative references moved by the member's offset from
    the master."""

    si: str
    master: Cell
    text: str
    ref: Area | None
    members: tuple[Cell, ...]


def date_serial(value: "_dt.date | _dt.time", *, date1904: bool = False) -> "int | float":
    """``value``'s serial number in a workbook's date system (ECMA-376 Part 1, 18.17.4.1):
    whole days for a ``date``, the time of day as the fraction for a ``datetime``, and the
    fraction alone for a ``time``.

    The 1900 system counts 1 January 1900 as 1 and a 29 February 1900 as 60, so every date
    from 1 March 1900 is one more than the days since 31 December 1899; the 1904 system
    counts 1 January 1904 as 0.  A date before a system's first day is refused.
    """
    if isinstance(value, _dt.time):
        if value.tzinfo is not None:
            raise ValueError("a cell's time has no time zone; drop its tzinfo")
        return _fraction(value)
    if not isinstance(value, _dt.date):
        raise TypeError(f"{type(value).__name__} is not a date")
    if isinstance(value, _dt.datetime) and value.tzinfo is not None:
        raise ValueError("a cell's date has no time zone; convert the time to the one the "
                         "workbook means and drop its tzinfo")
    day = value.date() if isinstance(value, _dt.datetime) else value
    if date1904:
        if day < _dt.date(1904, 1, 1):
            raise ValueError(f"{day.isoformat()} is before 1 January 1904, the first day "
                             "of this workbook's date system; write it as text")
        whole = (day - _dt.date(1904, 1, 1)).days
    else:
        if day < _dt.date(1900, 1, 1):
            raise ValueError(f"{day.isoformat()} is before 1 January 1900, the first day "
                             "of this workbook's date system; write it as text")
        whole = (day - _dt.date(1899, 12, 31)).days
        if day >= _dt.date(1900, 3, 1):
            whole += 1  # the 29 February 1900 the system counts
    if isinstance(value, _dt.datetime):
        part = _fraction(value.time())
        return whole + part if part else whole
    return whole


def _fraction(moment: _dt.time) -> "int | float":
    seconds = (moment.hour * 3600 + moment.minute * 60 + moment.second
               + moment.microsecond / 1e6)
    return seconds / 86400 if seconds else 0


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
        #: The shared strings as read: the tree they were read from, its length then, each
        #: item's text, and the first index of each plain (not rich) text.
        self._strings: tuple[Element, int, list[str], dict[str, int]] | None = None
        self._styles: Styles | None = None
        #: Cells counted ``t="s"`` per sheet, by the tree they were counted in.
        self._string_uses: dict[str, tuple[Element, int]] = {}

    @classmethod
    def open(cls, data: bytes) -> "Workbook":
        return cls(OpcPackage.open(data))

    @property
    def changed(self) -> bool:
        return bool(self.package.dirty_parts or self.package._raw_changes)

    def to_bytes(self) -> bytes:
        self.finish()
        return self.package.to_bytes()

    # -- sheets ------------------------------------------------------------------------------

    @property
    def sheet_names(self) -> list[str]:
        root = self.package.tree(self.part)
        return [node.get("name", "") for node in root.iter(_X + "sheet")]

    def sheet(self, name: str) -> "Worksheet | None":
        if name in self._sheets and self.package.has_part(self._sheets[name].part):
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

    @property
    def date1904(self) -> bool:
        """Whether dates count from 1904 (``workbookPr date1904``) rather than 1900."""
        node = self.package.tree(self.part).find(_X + "workbookPr")
        return node is not None and node.get("date1904", "").strip().lower() in {"1", "true"}

    # -- shared strings ----------------------------------------------------------------------

    def _shared_strings_part(self) -> str | None:
        parts = self.package.related_parts_of_type(self.part, REL_SHARED_STRINGS)
        return parts[0] if parts and self.package.has_part(parts[0]) else None

    def _string_table(self) -> tuple[Element, int, list[str], dict[str, int]] | None:
        """The shared strings, indexed once -- again only when the tree was replaced (an
        undo) or changed under this index."""
        part = self._shared_strings_part()
        root = self.package.tree(part) if part else None
        if root is None:
            return None
        cached = self._strings
        if cached is None or cached[0] is not root or cached[1] != len(root):
            texts: list[str] = []
            plain: dict[str, int] = {}
            for item in root.iterchildren(_X + "si"):
                text = _rich_text(item)
                if item.find(_X + "r") is None:
                    plain.setdefault(text, len(texts))
                texts.append(text)
            cached = self._strings = (root, len(root), texts, plain)
        return cached

    def shared_string(self, index: int) -> str:
        table = self._string_table()
        texts = [] if table is None else table[2]
        if not 0 <= index < len(texts):
            raise IndexError(f"shared string {index} does not exist")
        return texts[index]

    def add_shared_string(self, text: str) -> int | None:
        """Index of ``text`` in the shared-string table, added if new; ``None`` when the
        workbook has no shared-string table (its strings are then written inline)."""
        table = self._string_table()
        if table is None:
            return None
        root, _, texts, plain = table
        if text in plain:
            return plain[text]
        item = etree.Element(_X + "si")
        node = etree.SubElement(item, _X + "t")
        _set_text(node, text)
        extensions = root.find(_X + "extLst")
        if extensions is not None:
            extensions.addprevious(item)
        else:
            root.append(item)
        self.package.mark_dirty(self._shared_strings_part())
        plain[text] = len(texts)
        texts.append(text)
        self._strings = (root, len(root), texts, plain)
        return len(texts) - 1

    # -- styles ------------------------------------------------------------------------------

    @property
    def styles(self) -> "Styles | None":
        """The cell formats, or ``None`` for a workbook without a styles part."""
        parts = self.package.related_parts_of_type(self.part, REL_STYLES)
        part = parts[0] if parts and self.package.has_part(parts[0]) else None
        if part is None or self.package.tree(part) is None:
            return None
        if self._styles is None or self._styles.part != part:
            self._styles = Styles(self.package, part)
        return self._styles

    # -- calculation -------------------------------------------------------------------------

    def drop_calc_chain(self) -> bool:
        """Remove the calculation chain, which Excel rebuilds on load; whether there was one.
        A chain that names a cell without a formula, or misses one with, is a repair."""
        dropped = False
        for chain in self.package.related_parts_of_type(self.part, REL_CALC_CHAIN):
            for rel in list(self.package.relationships(self.part).values()):
                if rel.target_part == chain:
                    self.package.remove_relationship(self.part, rel.id)
            if self.package.has_part(chain):
                self.package.remove_part(chain)
            dropped = True
        return dropped

    def request_full_calculation(self) -> bool:
        """Ask the application to recalculate every formula when it opens the workbook
        (``calcPr fullCalcOnLoad="1"``), keeping the rest of ``calcPr``; whether it changed.
        Cached values written before then are what readers without a calculator see."""
        root = self.package.tree(self.part)
        node = root.find(_X + "calcPr")
        if node is None:
            node = etree.Element(_X + "calcPr")
            _insert_ranked(root, node, _WORKBOOK_ORDER)
        elif node.get("fullCalcOnLoad") in {"1", "true"}:
            return False
        node.set("fullCalcOnLoad", "1")
        self.package.mark_dirty(self.part)
        return True

    # -- finishing ---------------------------------------------------------------------------

    def finish(self) -> None:
        """Bring the workbook-wide bookkeeping up to date: the shared strings' ``count``
        and ``uniqueCount``.  Call it once at the end of a step (inside a history batch, so
        the step holds it); it changes nothing when nothing was written, and twice in a row
        is the same as once."""
        dirty = self.package.dirty_parts
        part = self._shared_strings_part()
        if part is not None and (part in dirty or self._touched_sheets()):
            root = self.package.tree(part)
            unique = str(sum(1 for _ in root.iterchildren(_X + "si")))
            total = sum(self._uses(sheet) for sheet in self._worksheet_parts())
            if root.get("count") is not None and root.get("count") != str(total):
                root.set("count", str(total))
                self.package.mark_dirty(part)
            if root.get("uniqueCount") is not None and root.get("uniqueCount") != unique:
                root.set("uniqueCount", unique)
                self.package.mark_dirty(part)

    #: The name charts have always called; the same.
    _finish = finish

    def _uses(self, part: str) -> int:
        """How many cells of a sheet use a shared string, counted again only when the sheet
        was written or its tree replaced."""
        tree = self.package.tree(part)
        cached = self._string_uses.get(part)
        if cached is not None and cached[0] is tree and part not in self.package.dirty_parts:
            return cached[1]
        count = sum(1 for cell in tree.iter(_X + "c") if cell.get("t") == "s")
        self._string_uses[part] = (tree, count)
        return count

    def _touched_sheets(self) -> set[str]:
        return {sheet.part for sheet in self._sheets.values()
                if sheet.part in self.package.dirty_parts}


class Worksheet:
    """One sheet's cells, read and written by position."""

    def __init__(self, workbook: Workbook, part: str, name: str) -> None:
        self.workbook = workbook
        self.part = normalize_part_path(part)
        self.name = name
        self._tree: Element | None = None
        self._sheet_data: Element | None = None
        #: The rows in document order, with their numbers, and the row of each number; built
        #: once per tree.
        self._order: list[tuple[int, Element]] | None = None
        self._by_number: dict[int, Element] = {}
        self._ascending = True
        #: ``(first row, first column, last row, last column)`` of the cells, or ``None``
        #: until computed and after a cell is removed.
        self._bounds: tuple[int, int, int, int] | None = None
        if self._data is None:
            raise ValueError(f"sheet {name!r} has no sheetData")

    @property
    def _root(self) -> Element:
        """The live tree: the same object until an undo replaces it, when the row index goes
        with it."""
        root = self.workbook.package.tree(self.part)
        if root is not self._tree:
            self._tree = root
            self._sheet_data = root.find(_X + "sheetData")
            self._order = None
            self._bounds = None
        return root

    @property
    def _data(self) -> Element | None:
        self._root
        return self._sheet_data

    @property
    def protected(self) -> bool:
        """Whether the sheet is protected (``sheetProtection sheet="1"``): its locked cells
        are not to be written."""
        node = self._root.find(_X + "sheetProtection")
        return node is not None and node.get("sheet", "").strip().lower() in {"1", "true"}

    # -- reading -----------------------------------------------------------------------------

    def value(self, cell: Cell) -> "str | float | bool | None":
        """What the cell holds: text, a number, a boolean, an error's text, or ``None``."""
        node = self._find(cell)
        return None if node is None else self._read(node)

    def is_empty(self, cell: Cell) -> bool:
        """No value at all -- a cell that only carries a style counts as empty."""
        node = self._find(cell)
        return node is None or self._read(node) is None and node.find(_X + "f") is None

    def content(self, cell: Cell) -> Content:
        """The cell as stored -- its value and kind, formula and style (:class:`Content`)."""
        node = self._find(cell)
        if node is None:
            return Content("blank", None)
        value = self._read(node)
        kind = node.get("t", "n")
        if value is None and kind == "str" and node.find(_X + "v") is not None:
            value = ""  # a formula's empty text result: text, not blank
        if value is None:
            kind = "blank"
        elif kind in {"s", "inlineStr", "str"} or isinstance(value, str) and kind != "e":
            kind = "text"
        elif kind == "b":
            kind = "bool"
        elif kind == "e":
            kind = "error"
        else:
            kind = "number"
        formula = node.find(_X + "f")
        return Content(kind, value,
                       None if formula is None else (formula.text or ""),
                       None if formula is None else formula.get("t", "normal"),
                       _style_of(node))

    def shared_group(self, cell: Cell) -> SharedGroup | None:
        """The shared formula group the cell is a member of, or ``None`` (no formula, or not
        a shared one).  Found by walking the sheet's cells once; ``None`` too for a group
        whose master is not there (no cell holds its text)."""
        node = self._find(cell)
        formula = None if node is None else node.find(_X + "f")
        if formula is None or formula.get("t") != "shared" or formula.get("si") is None:
            return None
        si = formula.get("si")
        members: list[Cell] = []
        master: tuple[Cell, Element] | None = None
        for number, row in self._index():
            for column, other in self._cells(row):
                found = other.find(_X + "f")
                if found is None or found.get("t") != "shared" or found.get("si") != si:
                    continue
                position = Cell(number, column)
                members.append(position)
                if master is None and found.text:
                    master = (position, found)
        if master is None:
            return None
        ref = master[1].get("ref")
        try:
            area = Area.parse(ref) if ref else None
        except ValueError:
            area = None
        return SharedGroup(si, master[0], master[1].text, area, tuple(members))

    def release_shared(self, cell: Cell, translate, *,
                       written: "Iterable[Cell]" = ()) -> list[Cell]:
        """Take the master of a shared formula group out of its group, so that it can be
        written (:meth:`write` refuses a master other cells use), every other member keeping
        its formula; the cells whose formula changed.  Nothing to do -- ``[]`` -- for a cell
        that is no master: writing a member leaves the others' formulas as they are.

        ``translate(text, rows, columns)`` is the master's formula with its relative
        references moved by ``rows`` and ``columns`` (a member's offset from the master): a
        formula lexer's, which this module does not have.  It may raise to refuse a formula
        it cannot move safely; it is called for every cell that needs it before anything
        changes, so a refusal leaves the sheet as it was.

        The first other member, in the order of the sheet, becomes the master: its formula
        moved there, the group's ``ref`` the rectangle the remaining members span -- when
        that rectangle starts at the new master.  Otherwise (an irregular group) every other
        member is given its own formula, moved from the master, and the group is gone.  The
        old master keeps its formula as a plain one, for the write that follows to replace.
        The calculation chain is dropped, as for any formula changed."""
        group = self.shared_group(cell)
        if group is None or group.master != cell:
            return []
        skipped = set(written)
        others = [member for member in group.members if member != cell
                  and member not in skipped]
        moved = {member: translate(group.text, member.row - cell.row,
                                   member.column - cell.column) for member in others[:1]}
        if others:
            top = Cell(min(m.row for m in others), min(m.column for m in others))
            bottom = Cell(max(m.row for m in others), max(m.column for m in others))
            regular = top == others[0]
            if not regular:
                moved = {member: translate(group.text, member.row - cell.row,
                                           member.column - cell.column) for member in others}
        changed = [cell]
        self._plain(self._find(cell).find(_X + "f"), group.text)
        if others and regular:
            formula = self._find(others[0]).find(_X + "f")
            formula.text = moved[others[0]]
            formula.set("ref", Area(top, bottom).ref)
            changed.append(others[0])
        else:
            for member, text in moved.items():
                self._plain(self._find(member).find(_X + "f"), text)
                changed.append(member)
        self.workbook.drop_calc_chain()
        self.workbook.package.mark_dirty(self.part)
        return changed

    @staticmethod
    def _plain(formula: Element, text: str) -> None:
        """A shared formula's ``f`` made a normal one holding ``text``."""
        for name in ("t", "ref", "si"):
            formula.attrib.pop(name, None)
        formula.text = text

    def style(self, cell: Cell, *, inherited: bool = False) -> int:
        """The cell's format, an index into the workbook's :class:`Styles`; 0 when none.

        ``inherited``: for a position that holds no cell, the format a cell made there
        takes -- its row's (``s``, when the row has ``customFormat``), else its column's
        (``col style``), else 0 (ECMA-376 Part 1, 18.3.1.73 ``row``, 18.3.1.13 ``col``).
        :meth:`write` gives a new cell no format of its own unless asked (``style``), so
        a caller that wants a template's formatted column to show a value as typed into it
        passes this."""
        node = self._find(cell)
        if node is not None or not inherited:
            return 0 if node is None else _style_of(node)
        row = self._by_number.get(cell.row)
        if row is not None and row.get("customFormat", "").strip().lower() in {"1", "true"}:
            given = row.get("s", "")
            if given.isdigit():
                return int(given)
        columns = self._root.find(_X + "cols")
        if columns is not None:
            for column in columns.iterchildren(_X + "col"):
                low, high, given = column.get("min", ""), column.get("max", ""), \
                    column.get("style", "")
                if low.isdigit() and high.isdigit() and int(low) <= cell.column <= int(high):
                    return int(given) if given.isdigit() else 0
        return 0

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

    def write(self, cell: Cell, value, *, style: int | None = None) -> bool:
        """Write one cell; whether anything changed.

        ``value`` is text (always text, ``"=A1"`` included), a number, a boolean, a
        ``date``, ``datetime`` or ``time`` (its serial number in the workbook's date system:
        give ``style`` a date format, from :meth:`Styles.with_number_format`), a
        :class:`Formula`, or ``None`` -- a blank that keeps the cell's format.  ``style``
        sets the cell's format; ``None`` keeps the one it has.  The value's type is what is
        stored: text that looks like a number stays text.

        Writing what the cell already holds changes nothing.  A value is checked before
        anything is touched, so a refusal leaves the sheet as it was: text over
        :data:`MAX_TEXT` characters, a number that is not finite, a position off the grid,
        a type not listed (``TypeError``), or the master of a shared formula
        (:class:`SharedFormulaError`).  A formula replaced or written drops the workbook's
        calculation chain.
        """
        if not (1 <= cell.row <= MAX_ROW and 1 <= cell.column <= MAX_COLUMN):
            raise ValueError(f"row {cell.row}, column {cell.column} is outside the sheet")
        stored = self._stored(value)
        if style is not None and (isinstance(style, bool) or not isinstance(style, int)
                                  or style < 0):
            raise ValueError(f"style {style!r} is not a cell format index")
        node = self._find(cell)
        if node is None:
            if stored is None and not style:
                return False
        else:
            same_value = self._is(node, stored)
            if same_value and (style is None or _style_of(node) == style):
                return False
            if not same_value:
                self._check_formula(node)
        if node is None:
            node = self._create(cell)
        if style is not None and _style_of(node) != style:
            if style:
                node.set("s", str(style))
            else:
                node.attrib.pop("s", None)
        if not self._is(node, stored):
            self._put(node, stored)
        if stored is None and node.get("s") is None and len(node) == 0:
            self._remove_cell(node)
        self._touch(cell)
        return True

    def _stored(self, value):
        """``value`` as it is stored: a date as its serial number; refused when it cannot be."""
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, Formula):
            if isinstance(value.value, str):
                _check_text(value.value)
            elif isinstance(value.value, (int, float)) and not isinstance(value.value, bool):
                _check_number(value.value)
            return value
        if isinstance(value, (int, float)):
            _check_number(value)
            return value
        if isinstance(value, str):
            _check_text(value)
            return value
        if isinstance(value, (_dt.date, _dt.time)):
            return date_serial(value, date1904=self.workbook.date1904)
        raise TypeError(f"a cell holds text, a number, a boolean, a date, a Formula or None, "
                        f"not {type(value).__name__}")

    def _is(self, node: Element, value) -> bool:
        """Whether the cell holds exactly ``value`` (as :meth:`_stored` gives it), stored as
        the same kind -- a formula as the same plain formula."""
        formula = node.find(_X + "f")
        if isinstance(value, Formula):
            if formula is None or formula.get("t", "normal") != "normal" \
                    or (formula.text or "") != value.text:
                return False
            return value.value is None or self._holds(node, value.value, cached=True)
        if formula is not None:
            return False
        return self._holds(node, value)

    def _check_formula(self, node: Element) -> None:
        formula = node.find(_X + "f")
        if formula is not None and formula.get("t") == "shared" and formula.get("ref"):
            raise SharedFormulaError(node.get("r") or "?", formula.get("ref"), formula.get("si"))

    def _put(self, node: Element, value) -> None:
        """Replace what the cell holds -- formula, value, type -- with ``value``."""
        if isinstance(value, Formula) or node.find(_X + "f") is not None:
            self._drop_formula(node)
            self.workbook.drop_calc_chain()
        for child in list(node):
            if child.tag in {_X + "v", _X + "is"}:
                node.remove(child)
        node.attrib.pop("t", None)
        cached = value.value if isinstance(value, Formula) else value
        if isinstance(value, Formula):
            formula = etree.Element(_X + "f")
            formula.text = value.text
            node.insert(0, formula)
        if cached is None:
            return
        if isinstance(cached, bool):
            node.set("t", "b")
            text = "1" if cached else "0"
        elif isinstance(cached, str):
            index = None if isinstance(value, Formula) else self.workbook.add_shared_string(cached)
            if isinstance(value, Formula):
                node.set("t", "str")
                text = cached
            elif index is None:
                node.set("t", "inlineStr")
                inline = etree.Element(_X + "is")
                _set_text(etree.SubElement(inline, _X + "t"), cached)
                _before_extensions(node, inline)
                return
            else:
                node.set("t", "s")
                text = str(index)
        else:
            text = number_text(cached)
        raw = etree.Element(_X + "v")
        raw.text = text
        _before_extensions(node, raw)

    def set_value(self, cell: Cell, value: "str | float | int | None", *,
                  style_from: Cell | None = None) -> None:
        """Write a number, a string or nothing (``None`` empties the cell, keeping its style).

        A new cell takes its style from ``style_from`` when given (the neighbour whose line it
        continues), so an added point is formatted like its series.  What a chart writes;
        :meth:`write` takes every kind of value.
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
        self._touch(cell)

    def _holds(self, node: Element, value, *, cached: bool = False) -> bool:
        """Whether the cell already holds exactly ``value``, stored as the same kind
        (``cached``: as a formula's result, where text is ``t="str"``)."""
        current = self._read(node)
        if value is None:
            return current is None
        if isinstance(value, bool):
            return node.get("t") == "b" and current is value
        if isinstance(value, str):
            kinds = {"str"} if cached else {"s", "inlineStr", "str"}
            return node.get("t") in kinds and current == value
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
        self._touch(target)

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

    def _touch(self, cell: Cell | None = None) -> None:
        """The sheet was written (``cell``, when one was): it is saved as its tree, and its
        ``dimension`` follows -- computed from every cell the first time and after a cell
        was removed, grown to take in ``cell`` otherwise."""
        self.workbook.package.mark_dirty(self.part)
        self._update_dimension(cell)

    def _index(self) -> list[tuple[int, Element]]:
        """The rows in document order with their numbers -- a row without ``r`` is the one
        after the row before it -- built once per tree."""
        self._root
        if self._order is None:
            order: list[tuple[int, Element]] = []
            by_number: dict[int, Element] = {}
            number = 0
            ascending = True
            for row in self._sheet_data.iterchildren(_X + "row"):
                given = row.get("r")
                following = int(given) if given else number + 1
                ascending = ascending and following > number
                number = following
                order.append((number, row))
                by_number.setdefault(number, row)
            self._order, self._by_number, self._ascending = order, by_number, ascending
        return self._order

    def _cells(self, row: Element) -> Iterator[tuple[int, Element]]:
        """A row's cells with their column numbers; a cell without ``r`` is the one after
        the cell before it."""
        column = 0
        for node in row.iterchildren(_X + "c"):
            given = node.get("r")
            column = Cell.parse(given).column if given else column + 1
            yield column, node

    def _find(self, cell: Cell) -> Element | None:
        self._index()
        row = self._by_number.get(cell.row)
        if row is None:
            return None
        for column, node in self._cells(row):
            if column == cell.column:
                return node
        return None

    def _row(self, number: int, *, create: bool) -> Element | None:
        order = self._index()
        existing = self._by_number.get(number)
        if existing is not None or not create:
            return existing
        # The first row after it in document order.  A new row never renumbers a row
        # without ``r`` after it: such a row is the one after its predecessor, so there is
        # no gap before it for a new row to fall into.
        if self._ascending:
            position = _bisect(order, number)
        else:
            position = next((i for i, (n, _) in enumerate(order) if n > number), len(order))
        row = etree.Element(_X + "row")
        row.set("r", str(number))
        if position < len(order):
            order[position][1].addprevious(row)
        else:
            last = order[-1][1] if order else None
            if last is not None:
                last.addnext(row)
            else:
                self._sheet_data.append(row)
        order.insert(position, (number, row))
        self._by_number[number] = row
        return row

    def _create(self, cell: Cell) -> Element:
        node = etree.Element(_X + "c")
        node.set("r", cell.ref)
        self._place(node, cell)
        return node

    def _place(self, node: Element, cell: Cell) -> None:
        row = self._row(cell.row, create=True)
        for column, sibling in self._cells(row):
            if column > cell.column:
                sibling.addprevious(node)
                break
        else:
            extensions = row.find(_X + "extLst")
            if extensions is not None:
                extensions.addprevious(node)
            else:
                row.append(node)
        self._update_spans(row)

    def _remove_cell(self, node: Element) -> None:
        row = node.getparent()
        remove(node)
        if row is not None:
            self._bounds = None
            if len(row) == 0 and not set(row.attrib.keys()) - {"r", "spans"}:
                remove(row)
                self._order = None
            else:
                self._update_spans(row)

    def _drop_formula(self, node: Element) -> None:
        formula = node.find(_X + "f")
        if formula is None:
            return
        self._check_formula(node)
        node.remove(formula)
        self.workbook.drop_calc_chain()

    def _update_spans(self, row: Element) -> None:
        """Keep a row's optional ``spans`` hint (its first and last used column) true."""
        if row.get("spans") is None:
            return
        columns = [column for column, _ in self._cells(row)]
        if not columns:
            return
        wanted = f"{min(columns)}:{max(columns)}"
        if row.get("spans") != wanted:
            row.set("spans", wanted)

    def _update_dimension(self, cell: Cell | None = None) -> None:
        dimension = self._root.find(_X + "dimension")
        if dimension is None:
            return
        if self._bounds is None:
            self._bounds = self._measure()
        elif cell is not None:
            low_row, low_column, high_row, high_column = self._bounds
            self._bounds = (min(low_row, cell.row), min(low_column, cell.column),
                            max(high_row, cell.row), max(high_column, cell.column))
        if self._bounds is None:
            wanted = "A1"
        else:
            low_row, low_column, high_row, high_column = self._bounds
            wanted = Area(Cell(low_row, low_column), Cell(high_row, high_column)).ref
        if dimension.get("ref") != wanted:
            dimension.set("ref", wanted)

    def _measure(self) -> tuple[int, int, int, int] | None:
        """The rectangle every cell is in, or ``None`` for a sheet without cells."""
        low_row = low_column = high_row = high_column = None
        for number, row in self._index():
            for column, _ in self._cells(row):
                if low_row is None:
                    low_row = high_row = number
                    low_column = high_column = column
                else:
                    low_row, high_row = min(low_row, number), max(high_row, number)
                    low_column = min(low_column, column)
                    high_column = max(high_column, column)
        return None if low_row is None else (low_row, low_column, high_row, high_column)


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


def _check_number(value: float | int) -> None:
    if not math.isfinite(float(value)):
        raise ValueError(f"{value!r} is not a finite number; a cell holds finite numbers only")


def _check_text(text: str) -> None:
    if len(text) > MAX_TEXT:
        raise ValueError(f"text of {len(text):,} characters is longer than a cell holds "
                         f"({MAX_TEXT:,}); shorten it or split it over cells")


def _style_of(node: Element) -> int:
    raw = node.get("s")
    return int(raw) if raw and raw.isdigit() else 0


def _bisect(order: list[tuple[int, Element]], number: int) -> int:
    low, high = 0, len(order)
    while low < high:
        middle = (low + high) // 2
        if order[middle][0] < number:
            low = middle + 1
        else:
            high = middle
    return low


def _before_extensions(node: Element, child: Element) -> None:
    """Append ``child`` to a cell, before its ``extLst`` if it has one."""
    extensions = node.find(_X + "extLst")
    if extensions is not None:
        extensions.addprevious(child)
    else:
        node.append(child)


def _rich_text(item: Element) -> str:
    """The text of a shared string or inline string: plain ``t``, or runs' ``t`` joined."""
    plain = item.find(_X + "t")
    if plain is not None and item.find(_X + "r") is None:
        return plain.text or ""
    return "".join(node.text or "" for node in item.iter(_X + "t")
                   if node.getparent().tag == _X + "r")


def _set_text(node: Element, text: str) -> None:
    node.text = text
    _keep_spaces(node, text)


def _keep_spaces(node: Element, text: str) -> None:
    if text != text.strip() or "\n" in text:
        node.set(_XML_SPACE, "preserve")
    else:
        node.attrib.pop(_XML_SPACE, None)


#: ``CT_Workbook``'s children in order (ECMA-376 Part 1, 18.2.27), for inserting ``calcPr``.
_WORKBOOK_ORDER = ("fileVersion", "fileSharing", "workbookPr", "workbookProtection",
                   "bookViews", "sheets", "functionGroups", "externalReferences",
                   "definedNames", "calcPr", "oleSize", "customWorkbookViews", "pivotCaches",
                   "smartTagPr", "smartTagTypes", "webPublishing", "fileRecoveryPr",
                   "webPublishObjects", "extLst")


def _insert_ranked(parent: Element, child: Element, order: tuple[str, ...]) -> None:
    """Insert ``child`` before the first of ``parent``'s children that comes after it in
    ``order``; children ``order`` does not name (``mc:AlternateContent``, a later
    version's) are stepped over."""
    rank = {_X + name: index for index, name in enumerate(order)}
    mine = rank[child.tag]
    for existing in parent:
        if rank.get(existing.tag, -1) > mine:
            existing.addprevious(child)
            return
    parent.append(child)


from .styles import Styles  # noqa: E402  (Styles needs nothing above; kept last for reading order)
