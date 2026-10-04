"""Charts: the data, titles and legend of a chart, kept in step with its workbook.

A chart's numbers are stored twice, and an edit has to change both:

* the **caches** in the chart part (``c:strCache``, ``c:numCache``: ``ptCount`` and one
  ``c:pt idx=`` per point), which is what every application and renderer draws from; and
* the **embedded workbook** the chart's formulas (``c:f``, ``Sheet1!$B$2:$B$4``) point
  into, which is what the application's "Edit Data" opens.  It is a package inside the
  package, edited cell by cell through :mod:`.workbook`.

Update only the cache and the chart looks right until someone clicks Edit Data: the
application then shows, and writes back, the old numbers.  So every edit here writes the cache, the
cells, and -- when series or categories are added or removed -- the formulas and the
workbook's table, in one undo step.  A chart whose workbook is linked from outside the
package, embedded as something other than a workbook, or missing, is edited in the cache
only, with a :class:`ChartDataWarning`.

**The workbook layout this understands** is the one Office writes and every chart
generator copies: categories in one column, each series' values in the column beside it
with its name in the row above (or the same thing transposed, series in rows).  Value and
label edits only need each formula to be one rectangle.  Adding or removing a category or a
series also needs the lines to line up -- every series over the same rows -- because that is
what lets a point be inserted by moving cells; a workbook that is laid out otherwise is
refused with :class:`ChartDataError` rather than guessed at.

Charts are DrawingML, and every Office document embeds the same chart part.  This module
reaches the document only through the :class:`~.host.GraphicHost` the format layer resolves:
the frame that points at the chart, the part that relates it, and the format's undo step.
"""

from __future__ import annotations

import copy
import hashlib
import math
import warnings
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Sequence

from lxml import etree

from ..xml import Element, append_in_order, find, local_name, make, qn, remove, subelement
from .dmltext import paragraph_text, replace_body_text
from .host import GraphicHost, chart_part
from .namespaces import C_NS  # noqa: F401  (re-exported)
from .workbook import Area, Cell, SheetRange, Workbook, Worksheet, number_text, parse_formula

REL_PACKAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package"

#: The plot types a plot area may hold, as ``c:<type>Chart`` elements.
PLOT_TAGS: tuple[str, ...] = (
    "c:areaChart", "c:area3DChart", "c:lineChart", "c:line3DChart", "c:stockChart",
    "c:radarChart", "c:scatterChart", "c:pieChart", "c:pie3DChart", "c:doughnutChart",
    "c:barChart", "c:bar3DChart", "c:ofPieChart", "c:surfaceChart", "c:surface3DChart",
    "c:bubbleChart",
)
PLOT_TYPES: tuple[str, ...] = tuple(tag[2:-5] for tag in PLOT_TAGS)
AXIS_TAGS: tuple[str, ...] = ("c:catAx", "c:valAx", "c:dateAx", "c:serAx")

#: The reference kinds a data source (``c:tx``, ``c:cat``, ``c:val``...) may hold.
_REF_TAGS = ("c:strRef", "c:numRef", "c:multiLvlStrRef", "c:strLit", "c:numLit", "c:v")
_CACHE_OF = {"strRef": "c:strCache", "numRef": "c:numCache",
             "multiLvlStrRef": "c:multiLvlStrCache"}


class ChartDataError(ValueError):
    """A chart edit that cannot be made faithfully -- refused before anything changes."""


class ChartDataWarning(UserWarning):
    """A chart edit reached the chart's cache but not its workbook (none to reach)."""


# ------------------------------------------------------------------------------------------
# Data sources
# ------------------------------------------------------------------------------------------


class _Data:
    """One data source of a series -- its name (``c:tx``), categories or values -- over its
    reference: a formula and a cache, or a literal."""

    def __init__(self, container: Element | None) -> None:
        self.container = container
        self.ref = None
        if container is not None:
            self.ref = next((child for child in container
                             if child.tag in {qn(tag) for tag in _REF_TAGS}), None)

    @property
    def kind(self) -> str | None:
        return None if self.ref is None else local_name(self.ref)

    @property
    def numeric(self) -> bool:
        return self.kind in {"numRef", "numLit"}

    @property
    def formula(self) -> Element | None:
        return None if self.ref is None else self.ref.find(qn("c:f"))

    @property
    def range(self) -> SheetRange | None:
        node = self.formula
        return None if node is None else parse_formula(node.text)

    def set_range(self, sheet_range: SheetRange) -> None:
        node = self.formula
        if node is not None and node.text != sheet_range.formula:
            node.text = sheet_range.formula

    @property
    def cache(self) -> Element | None:
        if self.ref is None:
            return None
        kind = self.kind
        if kind in _CACHE_OF:
            return self.ref.find(qn(_CACHE_OF[kind]))
        if kind in {"strLit", "numLit"}:
            return self.ref
        return None

    def _points_parent(self) -> Element | None:
        """Where the points are: the cache, or the innermost level of a multi-level one."""
        cache = self.cache
        if cache is None:
            return None
        if self.kind == "multiLvlStrRef":
            levels = cache.findall(qn("c:lvl"))
            return levels[0] if levels else None
        return cache

    @property
    def levels(self) -> int:
        cache = self.cache
        if self.kind != "multiLvlStrRef" or cache is None:
            return 1
        return len(cache.findall(qn("c:lvl")))

    @property
    def count(self) -> int:
        if self.kind == "v":
            return 1
        cache = self.cache
        if cache is None:
            return 0
        node = cache.find(qn("c:ptCount"))
        if node is not None and (node.get("val") or "").isdigit():
            return int(node.get("val"))
        parent = self._points_parent()
        indexes = [_idx(pt) for pt in ([] if parent is None else parent.findall(qn("c:pt")))]
        return max(indexes, default=-1) + 1

    def points(self) -> list:
        """The points in index order, ``None`` where the cache has none (a blank)."""
        if self.kind == "v":
            return [self.ref.text or ""]
        values: list = [None] * self.count
        parent = self._points_parent()
        for pt in [] if parent is None else parent.findall(qn("c:pt")):
            index = _idx(pt)
            if 0 <= index < len(values):
                values[index] = self._decode(pt.find(qn("c:v")))
        return values

    def _decode(self, node: Element | None):
        text = "" if node is None or node.text is None else node.text
        if not self.numeric:
            return text
        return decode_number(text)

    def _encode(self, value) -> str:
        if self.numeric:
            if isinstance(value, str):
                raise ChartDataError(f"{value!r} is not a number, and this data is numeric")
            return number_text(value)
        return value if isinstance(value, str) else number_text(value)

    def _writable(self) -> Element:
        if self.levels != 1:
            raise ChartDataError("multi-level categories are not editable; edit them in "
                                 "the chart's workbook")
        parent = self._points_parent()
        if parent is None:
            raise ChartDataError("this data has no cache to write")
        return parent

    def set_point(self, index: int, value) -> bool:
        """Write one point; returns whether the cache changed."""
        if self.kind == "v":
            text = self._encode(value if value is not None else "")
            if self.ref.text == text:
                return False
            self.ref.text = text
            return True
        parent = self._writable()
        current = next((pt for pt in parent.findall(qn("c:pt")) if _idx(pt) == index), None)
        if value is None:
            if current is None:
                return False
            remove(current)
            return True
        text = self._encode(value)
        if current is not None:
            node = current.find(qn("c:v"))
            if node is None:
                node = subelement(current, "c:v")
            if node.text == text:
                return False
            node.text = text
            return True
        self._insert_pt(parent, index, text)
        return True

    def insert(self, index: int, value) -> None:
        """A new point at ``index``; later points move up one."""
        parent = self._writable()
        for pt in parent.findall(qn("c:pt")):
            if _idx(pt) >= index:
                pt.set("idx", str(_idx(pt) + 1))
        self._set_count(self.count + 1)
        if value is not None:
            self._insert_pt(parent, index, self._encode(value))

    def delete(self, index: int) -> None:
        parent = self._writable()
        for pt in parent.findall(qn("c:pt")):
            if _idx(pt) == index:
                remove(pt)
            elif _idx(pt) > index:
                pt.set("idx", str(_idx(pt) - 1))
        self._set_count(self.count - 1)

    def _set_count(self, count: int) -> None:
        cache = self.cache
        node = cache.find(qn("c:ptCount"))
        if node is None:
            node = subelement(cache, "c:ptCount")
        node.set("val", str(count))

    @staticmethod
    def _insert_pt(parent: Element, index: int, text: str) -> None:
        pt = make("c:pt", idx=str(index))
        pt.append(make("c:v"))
        pt[0].text = text
        later = next((p for p in parent.findall(qn("c:pt")) if _idx(p) > index), None)
        if later is not None:
            later.addprevious(pt)
        else:
            append_in_order(parent, pt)


def _idx(pt: Element) -> int:
    try:
        return int(pt.get("idx", "-1"))
    except ValueError:
        return -1


def decode_number(text: str) -> "float | int | None":
    """A cached number as written: ``3`` stays an int, ``11.7`` a float; junk is ``None``."""
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if number.is_integer() and "." not in text and "e" not in text.lower():
        return int(number)
    return number


class _Series:
    """A ``c:ser`` and its data sources."""

    def __init__(self, element: Element) -> None:
        self.element = element
        self.plot = element.getparent()
        self.name = _Data(element.find(qn("c:tx")))
        self.categories = _Data(element.find(qn("c:cat")) if element.find(qn("c:cat")) is not None
                                else element.find(qn("c:xVal")))
        self.values = _Data(element.find(qn("c:val")) if element.find(qn("c:val")) is not None
                            else element.find(qn("c:yVal")))
        self.sizes = _Data(element.find(qn("c:bubbleSize")))

    @property
    def point_sources(self) -> list[_Data]:
        """Every source with one entry per category (a name has one, full stop)."""
        return [d for d in (self.categories, self.values, self.sizes) if d.ref is not None]

    @property
    def order(self) -> int:
        node = self.element.find(qn("c:order"))
        try:
            return int(node.get("val")) if node is not None else 0
        except (TypeError, ValueError):
            return 0

    @property
    def idx(self) -> int:
        node = self.element.find(qn("c:idx"))
        try:
            return int(node.get("val")) if node is not None else 0
        except (TypeError, ValueError):
            return 0


# ------------------------------------------------------------------------------------------
# Editing context: the chart part, and the workbook behind it
# ------------------------------------------------------------------------------------------


class _Editor:
    """One edit's view of the chart part and (opened lazily) its workbook."""

    def __init__(self, host: GraphicHost, part: str) -> None:
        self.host = host
        self.package = host.package
        self.part = part
        self.root = host.package.tree(part)
        self.changed = False
        self._workbook: Workbook | None = None
        self._workbook_part: str | None = None
        self._opened = False
        #: Why the workbook could not be written, when it could not.
        self.cache_only: str | None = None

    # -- the workbook ------------------------------------------------------------------------

    def workbook(self) -> Workbook | None:
        if not self._opened:
            self._opened = True
            part, reason = workbook_part(self.package, self.part, document=self.host.document)
            if part is None:
                self.cache_only = reason
            else:
                try:
                    self._workbook = Workbook(self.package.open_embedded(part))
                    self._workbook_part = part
                except Exception as error:  # a corrupt or foreign embedding
                    self.cache_only = f"its embedded workbook could not be read ({error})"
        return self._workbook

    def sheet(self, sheet_range: SheetRange | None, source: _Data) -> Worksheet | None:
        """The sheet a formula points into -- or ``None`` (cache only) with the reason noted."""
        if source.formula is None:
            return None  # a literal: nothing in the workbook to keep in step
        if sheet_range is None:
            self._note(f"the formula {source.formula.text!r} is not a single range")
            return None
        workbook = self.workbook()
        if workbook is None:
            return None
        sheet = workbook.sheet(sheet_range.sheet)
        if sheet is None:
            self._note(f"its workbook has no sheet {sheet_range.sheet!r}")
        return sheet

    def _note(self, reason: str) -> None:
        if self.cache_only is None:
            self.cache_only = reason

    def write(self, source: _Data, index: int, value, *, style_from: int | None = None) -> None:
        """Write point ``index`` of ``source`` into its workbook cell, if it has one."""
        sheet_range = source.range
        sheet = self.sheet(sheet_range, source)
        if sheet is None or sheet_range is None:
            return
        if not sheet_range.is_line or index >= sheet_range.length:
            self._note(f"the formula {sheet_range.formula} does not have a cell for point "
                       f"{index}")
            return
        template = sheet_range.cell(style_from) if style_from is not None \
            and 0 <= style_from < sheet_range.length else None
        sheet.set_value(sheet_range.cell(index), value, style_from=template)
        sheet.sync_tables()

    # -- finishing ---------------------------------------------------------------------------

    def finish(self) -> None:
        if self.changed:
            self.package.mark_dirty(self.part)
        if self._workbook is not None and self._workbook.changed:
            self._workbook._finish()
            self.package.replace_embedded(self._workbook_part, self._workbook.package)

    def warn(self) -> None:
        if self.cache_only is not None:
            warnings.warn(f"{self.part}: only the chart's cached data was changed, because "
                          f"{self.cache_only}; {self.host.application}'s Edit Data will show "
                          f"the old values",
                          ChartDataWarning, stacklevel=4)


def workbook_part(package, chart_part: str, *, document: str = "document"
                  ) -> tuple[str | None, str]:
    """The embedded workbook behind a chart, or ``None`` and why there is none.  ``document``
    is what the reason calls the package ("linked from outside the document")."""
    root = package.tree(chart_part)
    node = None if root is None else root.find(qn("c:externalData"))
    if node is None:
        return None, "the chart has no workbook"
    rel = package.relationships(chart_part).get(node.get(qn("r:id")) or "")
    if rel is None:
        return None, "its workbook relationship is missing"
    if rel.is_external:
        return None, f"its workbook is linked from outside the {document} ({rel.target})"
    if rel.type != REL_PACKAGE or rel.target_part is None or not package.has_part(rel.target_part):
        return None, "its data is embedded as an OLE object, not as a workbook"
    return rel.target_part, ""


# ------------------------------------------------------------------------------------------
# The workbook block: where a chart's lines are
# ------------------------------------------------------------------------------------------


class _Block:
    """The chart's data as lines of cells: each source a row or column, over the same span.

    ``down`` means points run down a column (series in columns, Office's default).
    """

    def __init__(self, sheet: Worksheet, down: bool, start: int, count: int) -> None:
        self.sheet = sheet
        self.down = down
        self.start = start
        self.count = count

    def cell(self, across: int, position: int) -> Cell:
        return Cell(position, across) if self.down else Cell(across, position)

    def line_range(self, template: SheetRange, across: int, start: int, count: int) -> SheetRange:
        first = self.cell(across, start)
        last = self.cell(across, start + count - 1)
        return template.with_area(Area(first, last))

    @property
    def axis(self) -> str:
        """The sheet axis points move along: rows when they run down a column."""
        return "row" if self.down else "column"

    @property
    def across_axis(self) -> str:
        return "column" if self.down else "row"


def _line_of(sheet_range: SheetRange, down: bool) -> tuple[int, int, int]:
    """``(across, start, length)`` of a one-row or one-column range."""
    area = sheet_range.area
    if down:
        return area.first.column, area.first.row, area.rows
    return area.first.row, area.first.column, area.columns


# ------------------------------------------------------------------------------------------
# The chart
# ------------------------------------------------------------------------------------------


class Chart:
    """The chart a :class:`~.host.GraphicHost` holds.  ``resolve`` is called afresh on every
    call, so a ``Chart`` survives undo.

    Series are numbered in document order (``chart.series[0]``); categories by position.
    Values are numbers or ``None`` for a blank; category labels are text (numbers, for a
    chart whose categories are numeric or dates).
    """

    def __init__(self, resolve: Callable[[], GraphicHost]) -> None:
        self._resolve = resolve

    # -- where it lives ----------------------------------------------------------------------

    @property
    def address(self) -> str:
        return self._resolve().address

    @property
    def part(self) -> str:
        """The chart part, e.g. ``.../charts/chart1.xml``."""
        host = self._resolve()
        part = chart_part(host)
        if part is None:
            raise ValueError(f"{host.address}: the chart's part is missing")
        return part

    @property
    def workbook_part(self) -> str | None:
        """The embedded workbook (``.../embeddings/...xlsx``), or ``None`` when there is none
        to keep in step -- then edits change the cache only, with a warning."""
        host = self._resolve()
        return workbook_part(host.package, self.part, document=host.document)[0]

    def _package(self):
        return self._resolve().package

    def _root(self) -> Element:
        root = self._package().tree(self.part)
        if root is None:
            raise ValueError(f"{self.part} is missing")
        return root

    def _plot_area(self, root: Element | None = None) -> Element | None:
        return find(self._root() if root is None else root, "c:chart/c:plotArea")

    def _series(self, root: Element | None = None) -> list[_Series]:
        return _series_of(self._root() if root is None else root)

    # -- reading -----------------------------------------------------------------------------

    @property
    def chart_types(self) -> list[str]:
        """The plot types, in order: ``["bar"]``, ``["bar", "line"]`` for a combination."""
        plot_area = self._plot_area()
        if plot_area is None:
            return []
        return [local_name(child)[:-5] for child in plot_area
                if child.tag in {qn(t) for t in PLOT_TAGS}]

    @property
    def chart_type(self) -> str | None:
        types = self.chart_types
        return types[0] if types else None

    @property
    def series(self) -> list["Series"]:
        return [Series(self, index) for index in range(len(self._series()))]

    def series_named(self, name: str) -> "Series":
        for series in self.series:
            if series.name == name:
                return series
        raise KeyError(f"{self.address}: no series named {name!r}")

    @property
    def categories(self) -> list:
        """The category labels, from the first series that has any."""
        for series in self._series():
            if series.categories.ref is not None:
                return series.categories.points()
        return []

    @property
    def point_count(self) -> int:
        counts = [source.count for series in self._series() for source in series.point_sources]
        return max(counts, default=0)

    @property
    def number_format(self) -> str | None:
        """The values' number format (``c:numCache/c:formatCode``), e.g. ``General``."""
        for series in self._series():
            cache = series.values.cache
            node = None if cache is None else cache.find(qn("c:formatCode"))
            if node is not None:
                return node.text or ""
        return None

    @property
    def title(self) -> str | None:
        """The title's text; ``None`` when the chart shows none of its own."""
        return _title_text(find(self._root(), "c:chart/c:title"))

    @property
    def has_legend(self) -> bool:
        return find(self._root(), "c:chart/c:legend") is not None

    @has_legend.setter
    def has_legend(self, value: bool) -> None:
        self.set_legend(value)

    def axis_title(self, axis: str) -> str | None:
        """The title of the ``"category"`` (x) or ``"value"`` (y) axis."""
        node = self._axis(self._root(), axis)
        if node is None:
            raise KeyError(f"{self.address}: the chart has no {axis} axis")
        return _title_text(node.find(qn("c:title")))

    @property
    def axes(self) -> list[str]:
        """The axes the chart has: ``["category", "value"]``, or none for a pie."""
        root = self._root()
        return [axis for axis in ("category", "value") if self._axis(root, axis) is not None]

    @property
    def data(self) -> dict[str, Any]:
        """Everything above in one dictionary -- what :mod:`.model` reads."""
        return chart_model(self._root())

    # -- editing: values and labels ----------------------------------------------------------

    def set_category(self, index: int, label) -> "Chart":
        """Relabel category ``index`` in every series' cache and in the workbook."""
        index = self._point_index(index)
        if all(s.categories.ref is None or _same_number(s.categories.points()[index], label)
               for s in self._series()):
            return self
        with self._edit() as editor:
            written: set[str] = set()
            for series in self._series(editor.root):
                source = series.categories
                if source.ref is None:
                    continue
                if source.set_point(index, label):
                    editor.changed = True
                if source.formula is not None and source.formula.text not in written:
                    editor.write(source, index, _label_for(source, label))
                    written.add(source.formula.text)
        return self

    # -- editing: structure ------------------------------------------------------------------

    def add_category(self, label=None, values: "Sequence | dict | None" = None, *,
                     index: int | None = None) -> "Chart":
        """Insert a category at ``index`` (default: last), with a value per series.

        ``values`` is a list in series order or a ``{series name: value}`` dictionary; a
        series given nothing gets a blank.  In the workbook the lines below (or right of) the
        insertion move along by one cell, the formulas grow by one, and a table over the data
        grows with them.
        """
        count = self.point_count
        position = count if index is None else index
        if not 0 <= position <= count:
            raise IndexError(f"category index {index} out of range 0..{count}")
        with self._edit() as editor:
            series = self._series(editor.root)
            per_series = _per_series(values, series)
            block = self._block(editor, series, count)
            if block is not None:
                self._check_free(block, series, [block.start + count])
            for ser, value in zip(series, per_series):
                for source in ser.point_sources:
                    payload = label if source is ser.categories else (
                        value if source is ser.values else None)
                    source.insert(position, payload)
                _shift_point_formats(ser.element, position, +1)
            editor.changed = True
            if block is not None:
                self._insert_line_cells(block, series, position, label, per_series)
        return self

    def remove_category(self, index: int) -> "Chart":
        """Delete category ``index`` and its value in every series, cache and workbook."""
        count = self.point_count
        index = self._point_index(index)
        if count <= 1:
            raise ChartDataError(f"{self.address}: a chart keeps at least one category")
        with self._edit() as editor:
            series = self._series(editor.root)
            block = self._block(editor, series, count)
            for ser in series:
                for source in ser.point_sources:
                    source.delete(index)
                _shift_point_formats(ser.element, index, -1)
            editor.changed = True
            if block is not None:
                self._delete_line_cells(block, series, index)
        return self

    def add_series(self, name: str | None, values: Sequence | None = None, *,
                   index: int | None = None) -> "Series":
        """Add a series at position ``index`` (default: last), in the plot of the series
        before it, formatted by the application's automatic colours.

        In the workbook it gets the next free column (or row) beside the data, with its name
        in the header cell; a table over the data takes the column in.
        """
        with self._edit() as editor:
            series = self._series(editor.root)
            if not series:
                raise ChartDataError(f"{self.address}: a chart with no series has no template "
                                     f"to add one from")
            position = len(series) if index is None else index
            if not 0 <= position <= len(series):
                raise IndexError(f"series index {index} out of range 0..{len(series)}")
            count = self.point_count
            values = list(values) if values is not None else [None] * count
            if len(values) != count:
                raise ChartDataError(f"{self.address}: {len(values)} values for {count} "
                                     f"categories")
            block = self._block(editor, series, count)
            template = series[position - 1] if position > 0 else series[0]
            element = _new_series(template, series, name, values)
            if position > 0:
                series[position - 1].element.addnext(element)
            else:
                series[0].element.addprevious(element)
            element.tail = template.element.tail
            new = _Series(element)
            _give_unique_id(editor, element)
            _renumber_order(series, new, position)
            editor.changed = True
            if block is not None:
                self._append_series_cells(editor, block, series, new, name, values)
            else:
                # No workbook to put it in: the copied formulas would name the template's
                # cells, so the new series' own data becomes literal.
                _name_to_literal(new, name)
                for source in (new.values, new.sizes):
                    _to_literal(source)
        return Series(self, position)

    def remove_series(self, which: "Series | int | str") -> "Chart":
        """Delete a series, from the chart and from the workbook: later series' columns move
        back one when the series sit side by side, and a table over them shrinks."""
        with self._edit() as editor:
            series = self._series(editor.root)
            position = self._series_position(which, series)
            if len(series) <= 1:
                raise ChartDataError(f"{self.address}: a chart keeps at least one series")
            doomed = series[position]
            block = self._block(editor, series, self.point_count)
            if block is not None:
                self._delete_series_cells(block, series, doomed)
            legend = find(editor.root, "c:chart/c:legend")
            if legend is not None:
                for entry in legend.findall(qn("c:legendEntry")):
                    node = entry.find(qn("c:idx"))
                    if node is not None and node.get("val") == str(doomed.idx):
                        remove(entry)
            remove(doomed.element)
            editor.changed = True
        return self

    # -- editing: titles and legend ----------------------------------------------------------

    def set_title(self, text: str | None) -> "Chart":
        """The chart's title; ``None`` removes it (and stops an automatic one appearing)."""
        if text == self.title and (text is not None or find(self._root(), "c:chart/c:title") is None):
            return self
        with self._edit() as editor:
            chart = find(editor.root, "c:chart")
            _write_title(editor, chart, text, vertical=False)
            deleted = chart.find(qn("c:autoTitleDeleted"))
            wanted = "1" if text is None else "0"
            if deleted is None:
                deleted = subelement(chart, "c:autoTitleDeleted")
            deleted.set("val", wanted)
            editor.changed = True
        return self

    def set_axis_title(self, axis: str, text: str | None) -> "Chart":
        """Title the ``"category"`` or ``"value"`` axis; ``None`` removes its title."""
        if self.axis_title(axis) == text:
            return self
        with self._edit() as editor:
            node = self._axis(editor.root, axis)
            _write_title(editor, node, text, vertical=node.find(qn("c:axPos")) is not None
                         and node.find(qn("c:axPos")).get("val") in {"l", "r"})
            editor.changed = True
        return self

    def set_legend(self, visible: bool, position: str = "r") -> "Chart":
        """Show (at ``position``: ``r``, ``l``, ``t``, ``b``, ``tr``) or hide the legend."""
        if bool(visible) == self.has_legend:
            return self
        if position not in {"r", "l", "t", "b", "tr"}:
            raise ValueError("legend position is r, l, t, b or tr")
        with self._edit() as editor:
            chart = find(editor.root, "c:chart")
            if visible:
                legend = make("c:legend")
                legend.append(make("c:legendPos", val=position))
                legend.append(make("c:overlay", val="0"))
                append_in_order(chart, legend)
            else:
                remove(chart.find(qn("c:legend")))
            editor.changed = True
        return self

    # -- internals ---------------------------------------------------------------------------

    @contextmanager
    def _edit(self) -> Iterator[_Editor]:
        host = self._resolve()
        editor = _Editor(host, self.part)
        with host.edit():
            yield editor
            editor.finish()
        editor.warn()

    def _point_index(self, index: int) -> int:
        count = self.point_count
        if not -count <= index < count:
            raise IndexError(f"{self.address}: no category {index} (have {count})")
        return index % count

    def _series_position(self, which, series: list[_Series]) -> int:
        if isinstance(which, Series):
            return which.index
        if isinstance(which, str):
            for position, ser in enumerate(series):
                if ser.name.points()[:1] == [which]:
                    return position
            raise KeyError(f"{self.address}: no series named {which!r}")
        if not -len(series) <= which < len(series):
            raise IndexError(f"{self.address}: no series {which}")
        return which % len(series)

    @staticmethod
    def _axis(root: Element, axis: str) -> Element | None:
        return _axis_of(root, axis)

    # -- the workbook block ------------------------------------------------------------------

    def _block(self, editor: _Editor, series: list[_Series], count: int) -> _Block | None:
        """Where the chart's lines are, for an edit that changes their shape -- ``None`` when
        there is no workbook to keep in step (the edit is then cache only, and warned about).
        A workbook laid out in a way insertion cannot follow is refused."""
        sources = [source for ser in series for source in ser.point_sources
                   if source.formula is not None]
        if not sources:
            return None
        ranges = [source.range for source in sources]
        if any(r is None for r in ranges):
            bad = next(s for s, r in zip(sources, ranges) if r is None)
            raise ChartDataError(f"{self.address}: the formula {bad.formula.text!r} is not a "
                                 f"single range, so the workbook cannot be kept in step")
        sheets = {r.sheet for r in ranges}
        if len(sheets) != 1:
            raise ChartDataError(f"{self.address}: the chart's data spans several sheets")
        sheet = editor.sheet(ranges[0], sources[0])
        if sheet is None:
            return None
        if any(not r.is_line for r in ranges):
            raise ChartDataError(f"{self.address}: a data range is not a single row or column "
                                 f"(multi-level categories?); edit the structure in Excel")
        down = _direction(ranges, series)
        lines = {_line_of(r, down) for r in ranges}
        starts = {(start, length) for _, start, length in lines}
        if len(starts) != 1 or next(iter(starts))[1] != count:
            raise ChartDataError(f"{self.address}: the chart's ranges do not line up "
                                 f"({', '.join(sorted({r.formula for r in ranges}))}); "
                                 f"the workbook cannot be kept in step")
        start, _ = next(iter(starts))
        return _Block(sheet, down, start, count)

    @staticmethod
    def _lines(block: _Block, series: list[_Series]) -> list[tuple[_Data, int]]:
        """``(source, across)`` for every source with cells, each line once."""
        seen: dict[int, _Data] = {}
        for ser in series:
            for source in ser.point_sources:
                sheet_range = source.range
                if sheet_range is not None:
                    across = _line_of(sheet_range, block.down)[0]
                    seen.setdefault(across, source)
        return sorted(((source, across) for across, source in seen.items()),
                      key=lambda item: item[1])

    def _check_free(self, block: _Block, series: list[_Series], positions: list[int],
                    across: list[int] | None = None) -> None:
        lines = across if across is not None else [a for _, a in self._lines(block, series)]
        for line in lines:
            for position in positions:
                cell = block.cell(line, position)
                if not block.sheet.is_empty(cell):
                    raise ChartDataError(
                        f"{self.address}: workbook cell {cell.ref} is in the way of the "
                        f"chart's data growing; move it in Excel first")

    def _insert_line_cells(self, block: _Block, series: list[_Series], position: int,
                           label, per_series: list) -> None:
        sheet = block.sheet
        target = block.start + position
        last = block.start + block.count - 1
        lines = self._lines(block, series)
        values = {}
        for ser, value in zip(series, per_series):
            for source in ser.point_sources:
                sheet_range = source.range
                if sheet_range is None:
                    continue
                across = _line_of(sheet_range, block.down)[0]
                payload = label if source is ser.categories else (
                    value if source is ser.values else None)
                values.setdefault(across, (source, payload))
        for source, across in lines:
            for at in range(last, target - 1, -1):
                sheet.move(block.cell(across, at), block.cell(across, at + 1))
            payload = values.get(across, (source, None))[1]
            neighbour = target + 1 if position < block.count else target - 1
            sheet.set_value(block.cell(across, target), _label_for(source, payload),
                            style_from=block.cell(across, neighbour))
        span = (lines[0][1], lines[-1][1])
        sheet.shift_tables(block.axis, target, +1, span)
        for ser in series:
            for source in ser.point_sources:
                sheet_range = source.range
                if sheet_range is not None:
                    across = _line_of(sheet_range, block.down)[0]
                    source.set_range(block.line_range(sheet_range, across, block.start,
                                                      block.count + 1))
        sheet.sync_tables()

    def _delete_line_cells(self, block: _Block, series: list[_Series], index: int) -> None:
        sheet = block.sheet
        target = block.start + index
        last = block.start + block.count - 1
        lines = self._lines(block, series)
        for _, across in lines:
            sheet.clear(block.cell(across, target))
            for at in range(target + 1, last + 1):
                sheet.move(block.cell(across, at), block.cell(across, at - 1))
        sheet.shift_tables(block.axis, target, -1, (lines[0][1], lines[-1][1]))
        for ser in series:
            for source in ser.point_sources:
                sheet_range = source.range
                if sheet_range is not None:
                    across = _line_of(sheet_range, block.down)[0]
                    source.set_range(block.line_range(sheet_range, across, block.start,
                                                      block.count - 1))
        sheet.sync_tables()

    def _header(self, block: _Block, series: list[_Series]) -> int | None:
        """The position of the series names' header cells, if the names live in the sheet."""
        for ser in series:
            sheet_range = ser.name.range
            values = ser.values.range
            if sheet_range is None or values is None:
                continue
            name_across, name_at, _ = _line_of(sheet_range, block.down)
            if name_across == _line_of(values, block.down)[0] and name_at == block.start - 1:
                return name_at
        return None

    def _append_series_cells(self, editor: _Editor, block: _Block, series: list[_Series],
                             new: _Series, name, values: list) -> None:
        sheet = block.sheet
        lines = self._lines(block, series)
        value_lines = sorted({_line_of(s.values.range, block.down)[0] for s in series
                              if s.values.range is not None})
        across = max(a for _, a in lines) + 1
        header = self._header(block, series)
        positions = list(range(block.start, block.start + block.count))
        if header is not None:
            positions.insert(0, header)
        self._check_free(block, series, positions, [across])
        previous = value_lines[-1] if value_lines else lines[-1][1]
        template_range = next(s.values.range for s in series if s.values.range is not None)
        new.values.set_range(block.line_range(template_range, across, block.start, block.count))
        for offset, value in enumerate(values):
            position = block.start + offset
            sheet.set_value(block.cell(across, position), value,
                            style_from=block.cell(previous, position))
        if header is not None and new.name.formula is not None:
            name_range = block.line_range(template_range, across, header, 1)
            new.name.set_range(name_range)
            sheet.set_value(block.cell(across, header), name,
                            style_from=block.cell(previous, header))
        elif new.name.formula is not None:
            _name_to_literal(new, name)
        low = header if header is not None else block.start
        sheet.shift_tables(block.across_axis, across, +1, (low, block.start + block.count - 1))
        sheet.sync_tables()

    def _delete_series_cells(self, block: _Block, series: list[_Series],
                             doomed: _Series) -> None:
        sheet = block.sheet
        own = doomed.values.range
        if own is None:
            return
        across = _line_of(own, block.down)[0]
        others = [s for s in series if s is not doomed]
        if any(s.values.range is not None and _line_of(s.values.range, block.down)[0] == across
               for s in others):
            return  # another series draws from the same cells
        header = self._header(block, series)
        low = header if header is not None else block.start
        high = block.start + block.count - 1
        value_lines = sorted({_line_of(s.values.range, block.down)[0] for s in series
                              if s.values.range is not None})
        contiguous = value_lines == list(range(value_lines[0], value_lines[-1] + 1))
        category_lines = {_line_of(s.categories.range, block.down)[0] for s in series
                          if s.categories.range is not None}
        later = [a for a in value_lines if a > across]
        if not contiguous or category_lines & set(later):
            for position in range(low, high + 1):
                sheet.clear(block.cell(across, position))
            sheet.sync_tables()
            return
        for position in range(low, high + 1):
            sheet.clear(block.cell(across, position))
        for line in later:
            for position in range(low, high + 1):
                sheet.move(block.cell(line, position), block.cell(line - 1, position))
        for ser in others:
            for source in (ser.name, ser.values, ser.sizes):
                sheet_range = source.range
                if sheet_range is None:
                    continue
                line, start, length = _line_of(sheet_range, block.down)
                if line in later:
                    source.set_range(block.line_range(sheet_range, line - 1, start, length))
        sheet.shift_tables(block.across_axis, across, -1, (low, high))
        sheet.sync_tables()


class Series:
    """One series of a :class:`Chart`, by position.  Re-resolved on every call."""

    def __init__(self, chart: Chart, index: int) -> None:
        self.chart = chart
        self.index = index

    def _element(self, root: Element | None = None) -> _Series:
        series = self.chart._series(root)
        if not 0 <= self.index < len(series):
            raise IndexError(f"{self.chart.address}: no series {self.index}")
        return series[self.index]

    @property
    def name(self) -> str | None:
        """The series name (its legend entry); ``None`` when it has none of its own."""
        source = self._element().name
        if source.ref is None:
            return None
        points = source.points()
        return points[0] if points else None

    @name.setter
    def name(self, value: str) -> None:
        self.set_name(value)

    def set_name(self, value: str) -> "Series":
        if value == self.name:
            return self
        with self.chart._edit() as editor:
            ser = self._element(editor.root)
            if ser.name.ref is None:
                _give_name(ser, value)
            else:
                ser.name.set_point(0, value)
                editor.write(ser.name, 0, value)
            editor.changed = True
        return self

    @property
    def values(self) -> list:
        return self._element().values.points()

    @property
    def categories(self) -> list:
        return self._element().categories.points()

    def set_value(self, index: int, value: "float | int | None") -> "Series":
        """Point ``index`` to ``value`` (``None`` for a blank), in the cache and its cell."""
        _check_number(value)
        index = self.chart._point_index(index)
        if _same_number(self.values[index], value):
            return self
        with self.chart._edit() as editor:
            source = self._element(editor.root).values
            source.set_point(index, value)
            editor.write(source, index, value)
            editor.changed = True
        return self

    def set_values(self, values: Sequence) -> "Series":
        """Every point at once; there must be one value per category."""
        values = list(values)
        for value in values:
            _check_number(value)
        current = self.values
        if len(values) != len(current):
            raise ChartDataError(f"{self.chart.address}: {len(values)} values for "
                                 f"{len(current)} categories; add or remove categories first")
        if all(_same_number(a, b) for a, b in zip(current, values)):
            return self
        with self.chart._edit() as editor:
            source = self._element(editor.root).values
            for index, value in enumerate(values):
                if not _same_number(current[index], value):
                    source.set_point(index, value)
                    editor.write(source, index, value)
            editor.changed = True
        return self

    def __repr__(self) -> str:
        return f"<Series {self.index} {self.name!r} of {self.chart.address}>"


# ------------------------------------------------------------------------------------------
# Reading helpers (shared with :mod:`.model`)
# ------------------------------------------------------------------------------------------


def _series_of(root: Element) -> list[_Series]:
    plot_area = find(root, "c:chart/c:plotArea")
    if plot_area is None:
        return []
    plots = [child for child in plot_area if child.tag in {qn(t) for t in PLOT_TAGS}]
    return [_Series(ser) for plot in plots for ser in plot.findall(qn("c:ser"))]


def _axis_of(root: Element, axis: str) -> Element | None:
    """The ``"category"`` (x) or ``"value"`` (y) axis element, if the chart has one."""
    if axis not in {"category", "value"}:
        raise ValueError("axis is 'category' or 'value'")
    plot_area = find(root, "c:chart/c:plotArea")
    if plot_area is None:
        return None
    axes = [child for child in plot_area if child.tag in {qn(t) for t in AXIS_TAGS}]
    categorical = [a for a in axes if local_name(a) in {"catAx", "dateAx"}]
    values = [a for a in axes if local_name(a) == "valAx"]
    if not categorical and len(values) >= 2:  # scatter, bubble: x is a value axis too
        horizontal = [a for a in values if _ax_pos(a) in {"b", "t"}]
        categorical = horizontal[:1] or values[:1]
        values = [a for a in values if a is not categorical[0]]
    chosen = categorical if axis == "category" else values
    return chosen[0] if chosen else None


def chart_model(root: Element) -> dict[str, Any]:
    """The chart's data and labels: ``{"types", "title", "axis_titles", "legend", "format",
    "categories", "series": [{"name", "values"}]}``."""
    plot_area = find(root, "c:chart/c:plotArea")
    series = _series_of(root)
    categories: list = []
    for ser in series:
        if ser.categories.ref is not None:
            categories = ser.categories.points()
            break
    format_code = None
    for ser in series:
        cache = ser.values.cache
        node = None if cache is None else cache.find(qn("c:formatCode"))
        if node is not None:
            format_code = node.text or ""
            break
    axes = {}
    for axis in ("category", "value"):
        node = _axis_of(root, axis)
        if node is not None:
            axes[axis] = _title_text(node.find(qn("c:title")))
    return {
        "types": [] if plot_area is None else [local_name(child)[:-5] for child in plot_area
                                               if child.tag in {qn(t) for t in PLOT_TAGS}],
        "title": _title_text(find(root, "c:chart/c:title")),
        "axis_titles": axes,
        "legend": find(root, "c:chart/c:legend") is not None,
        "format": format_code,
        "categories": categories,
        "series": [{"name": (ser.name.points() or [None])[0] if ser.name.ref is not None
                    else None, "values": ser.values.points()} for ser in series],
    }


def _title_text(title: Element | None) -> str | None:
    if title is None:
        return None
    tx = title.find(qn("c:tx"))
    if tx is None:
        return None
    rich = tx.find(qn("c:rich"))
    if rich is not None:
        return "\n".join(paragraph_text(p) for p in rich.findall(qn("a:p")))
    source = _Data(tx)
    points = source.points()
    return points[0] if points else None


def _ax_pos(axis: Element) -> str | None:
    node = axis.find(qn("c:axPos"))
    return None if node is None else node.get("val")


def _direction(ranges: list[SheetRange], series: list[_Series]) -> bool:
    """Whether points run down columns.  A one-point chart is told by where its name sits."""
    for sheet_range in ranges:
        if sheet_range.area.rows > 1:
            return True
        if sheet_range.area.columns > 1:
            return False
    for ser in series:
        name, values = ser.name.range, ser.values.range
        if name is not None and values is not None:
            if name.area.first.column == values.area.first.column:
                return True
            if name.area.first.row == values.area.first.row:
                return False
    return True


def _label_for(source: _Data, label):
    """A category label as a cell value: numeric categories stay numbers."""
    if label is None or not source.numeric or not isinstance(label, str):
        return label
    number = decode_number(label)
    if number is None:
        raise ChartDataError(f"{label!r} is not a number, and these categories are numeric")
    return number


def _check_number(value) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(float(value)):
        raise ChartDataError(f"{value!r} is not a finite number")


def _same_number(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    return float(a) == float(b)


def _per_series(values, series: list[_Series]) -> list:
    if values is None:
        return [None] * len(series)
    if isinstance(values, dict):
        names = [(ser.name.points() or [None])[0] if ser.name.ref is not None else None
                 for ser in series]
        unknown = set(values) - set(names)
        if unknown:
            raise KeyError(f"no series named {sorted(unknown)}")
        result = [values.get(name) for name in names]
    else:
        result = list(values)
        if len(result) != len(series):
            raise ChartDataError(f"{len(result)} values for {len(series)} series")
    for value in result:
        _check_number(value)
    return result


def _shift_point_formats(ser: Element, index: int, delta: int) -> None:
    """Keep per-point formatting (``c:dPt``) and labels (``c:dLbl``) on their points."""
    holders = list(ser.findall(qn("c:dPt")))
    labels = ser.find(qn("c:dLbls"))
    if labels is not None:
        holders += labels.findall(qn("c:dLbl"))
    for holder in holders:
        node = holder.find(qn("c:idx"))
        if node is None:
            continue
        try:
            position = int(node.get("val"))
        except (TypeError, ValueError):
            continue
        if delta < 0 and position == index:
            remove(holder)
        elif position >= index if delta > 0 else position > index:
            node.set("val", str(position + delta))


#: The ``c:ext`` a series' ``c16:uniqueId`` is written in, and its namespace.
SERIES_ID_EXT = "{C3380CC4-5D6E-409C-BE32-E72D297353CC}"
C16_NS = "http://schemas.microsoft.com/office/drawing/2014/chart"


def _give_unique_id(editor: _Editor, element: Element) -> str:
    """Give a series this package adds a ``c16:uniqueId``, as Word and PowerPoint both do
    when they save a chart one of whose series has none: in the series' own ``c:extLst``,
    ``<c:ext uri="{C3380CC4-...}" xmlns:c16="...">``, its value ``{00000000-XXXX-XXXX-
    XXXX-XXXXXXXXXXXX}`` -- the first group zero, as both write it for a series added
    beside others.  Both choose the rest at random; here it is the first 24 hex digits of a
    SHA-256 of the chart part's name, the series' ``c:idx`` and every id the chart already
    holds, so the same edit writes the same bytes.  An id the chart already holds moves the
    hash on.  The chart's other series are left as they are, with or without an id."""
    taken = {node.get("val") for node in editor.root.iter(f"{{{C16_NS}}}uniqueId")}
    idx = element.find(qn("c:idx")).get("val")
    seed = "\n".join([editor.part, idx, *sorted(v for v in taken if v)])
    step = 0
    while True:
        digest = hashlib.sha256(f"{seed}\n{step}".encode()).hexdigest().upper()
        value = f"{{00000000-{digest[0:4]}-{digest[4:8]}-{digest[8:12]}-{digest[12:24]}}}"
        if value not in taken:
            break
        step += 1
    extensions = append_in_order(element, make("c:extLst"))
    ext = etree.SubElement(extensions, qn("c:ext"), nsmap={"c16": C16_NS})
    ext.set("uri", SERIES_ID_EXT)
    etree.SubElement(ext, f"{{{C16_NS}}}uniqueId").set("val", value)
    return value


def _new_series(template: _Series, series: list[_Series], name, values: list) -> Element:
    """A copy of ``template`` for a new series: its data replaced, its own look dropped so
    the application's automatic colour for the new index applies."""
    element = copy.deepcopy(template.element)
    for tag in ("c:spPr", "c:dPt", "c:trendline", "c:errBars", "c:extLst", "c:explosion"):
        for node in element.findall(qn(tag)):
            remove(node)
    marker = element.find(qn("c:marker"))
    if marker is not None:
        for node in marker.findall(qn("c:spPr")):
            remove(node)
    labels = element.find(qn("c:dLbls"))
    if labels is not None:
        for node in labels.findall(qn("c:dLbl")):
            remove(node)
    idx = max((s.idx for s in series), default=-1) + 1
    subelement(element, "c:idx").set("val", str(idx))
    subelement(element, "c:order").set("val", str(idx))
    new = _Series(element)
    # The name.
    if new.name.ref is not None and new.name.kind == "strRef":
        cache = new.name.cache
        if cache is not None:
            for pt in cache.findall(qn("c:pt")):
                remove(pt)
            new.name._set_count(1)
            if name is not None:
                _Data._insert_pt(cache, 0, name)
    else:
        tx = element.find(qn("c:tx"))
        if tx is not None:
            remove(tx)
        if name is not None:
            _give_name(new, name)
    # The values.
    source = new.values
    cache = source.cache
    if cache is None:
        raise ChartDataError("the template series has no value cache to copy")
    for pt in cache.findall(qn("c:pt")):
        remove(pt)
    source._set_count(len(values))
    for index, value in enumerate(values):
        if value is not None:
            _Data._insert_pt(cache, index, source._encode(value))
    if new.sizes.ref is not None:
        sizes = new.sizes.cache
        for pt in [] if sizes is None else sizes.findall(qn("c:pt")):
            remove(pt)
        if sizes is not None:
            new.sizes._set_count(len(values))
        # Sizes for a new series are not in the workbook, so they become a literal: a
        # reference without its formula is not allowed by the schema.
        _to_literal(new.sizes)
    return element


def _renumber_order(series: list[_Series], new: _Series, position: int) -> None:
    """Give the new series the plot order of its position and move the others along."""
    orders = sorted(s.order for s in series)
    wanted = orders[position] if position < len(orders) else (max(orders, default=-1) + 1)
    for ser in series:
        if ser.order >= wanted:
            ser.element.find(qn("c:order")).set("val", str(ser.order + 1))
    new.element.find(qn("c:order")).set("val", str(wanted))


def _give_name(ser: _Series, name: str) -> None:
    """A literal name (``c:tx/c:v``) for a series that had none."""
    tx = ser.element.find(qn("c:tx"))
    if tx is None:
        tx = make("c:tx")
        append_in_order(ser.element, tx)
    for child in list(tx):
        remove(child)
    node = make("c:v")
    node.text = name
    tx.append(node)
    ser.name = _Data(tx)


def _to_literal(source: _Data) -> None:
    """Turn a formula-backed source into a literal holding the same points."""
    if source.kind not in {"strRef", "numRef"}:
        return
    cache = source.cache
    literal = make("c:strLit" if source.kind == "strRef" else "c:numLit")
    if cache is not None:
        for child in list(cache):
            literal.append(child)
    source.ref.addprevious(literal)
    remove(source.ref)
    source.ref = literal


def _name_to_literal(ser: _Series, name) -> None:
    if ser.name.kind == "v" and name is not None:
        ser.name.ref.text = name
        return
    if name is None:
        tx = ser.element.find(qn("c:tx"))
        if tx is not None:
            remove(tx)
        ser.name = _Data(None)
    else:
        _give_name(ser, name)


def _write_title(editor: _Editor, owner: Element, text: str | None, *, vertical: bool) -> None:
    """Set, create or remove the ``c:title`` of the chart or of an axis."""
    title = owner.find(qn("c:title"))
    if text is None:
        if title is not None:
            remove(title)
        return
    if title is None:
        title = make("c:title")
        append_in_order(owner, title)
        overlay = make("c:overlay", val="0")
        append_in_order(title, overlay)
    tx = title.find(qn("c:tx"))
    if tx is not None and tx.find(qn("c:rich")) is None:
        source = _Data(tx)
        if source.kind == "strRef":
            source.set_point(0, text)
            editor.write(source, 0, text)
            return
        remove(tx)
        tx = None
    if tx is None:
        host = editor.host
        template = host.title_template
        tx = template(vertical) if template is not None else default_title_text(
            vertical, lang=host.lang)
        append_in_order(title, tx)
    replace_body_text(tx.find(qn("c:rich")), text)


def default_title_text(vertical: bool, *, lang: str | None = None) -> Element:
    """The ``c:tx`` of a new title: rich text, one empty paragraph, rotated when
    ``vertical``.

    ``lang`` goes on the paragraph's ``a:endParaRPr``, which is where the text then written
    into the title (:func:`~.dmltext.replace_body_text`) takes its run's properties from: an
    empty run's would be dropped with the run, and the title would have no language."""
    tx = make("c:tx")
    rich = make("c:rich")
    body = make("a:bodyPr")
    if vertical:
        body.set("rot", "-5400000")
        body.set("vert", "horz")
    rich.append(body)
    rich.append(make("a:lstStyle"))
    paragraph = make("a:p")
    properties = make("a:pPr")
    properties.append(make("a:defRPr"))
    paragraph.append(properties)
    if lang is not None:
        paragraph.append(make("a:endParaRPr", lang=lang))
    rich.append(paragraph)
    tx.append(rich)
    return tx


__all__ = ["AXIS_TAGS", "Chart", "ChartDataError", "ChartDataWarning", "PLOT_TAGS", "PLOT_TYPES",
           "Series", "chart_model", "chart_part", "decode_number", "default_title_text",
           "workbook_part"]
