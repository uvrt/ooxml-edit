"""A workbook's cell formats (``styles.xml``), indexed once: read a cell's number format, and
find or add the format a value needs.

A cell's ``s`` is an index into ``cellXfs``; that ``xf`` names a number format by
``numFmtId``: one of the workbook's own (a ``numFmt`` with its ``formatCode``) or a built-in
id whose code is implied (ECMA-376 Part 1, 18.8.30).  A value that needs a different number
format -- a date written where the cell shows plain numbers -- needs a different ``xf``:
:meth:`Styles.with_number_format` gives the one that is the cell's own with only the number
format changed, reusing an identical ``xf`` (and ``numFmt``) when the workbook has one, and
appending it when not.  Nothing that exists is changed or reordered: an appended ``xf`` or
``numFmt`` and the ``count`` attributes are the whole footprint.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from lxml import etree
from ooxml_common.numfmt import is_date_code

from ..opc import OpcPackage
from ..xml import Element
from ..errors import ArgumentError, ArgumentTypeError

_X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

#: ECMA-376 Part 1, 18.8.30, "All Languages": the built-in ids that show a number as a date
#: or a time (14-22 dates and times, 45-47 minutes and seconds, elapsed hours).
BUILTIN_DATE_IDS = frozenset(range(14, 23)) | frozenset({45, 46, 47})

#: ECMA-376 Part 1, 18.8.30: the built-in ids whose code depends on the application's
#: language (the zh-TW, zh-CN, ja-JP, ko-KR and th-TH tables).  Whether one is a date is the
#: reader's call, with its locale; :meth:`Styles.is_date` answers ``None`` for them.
LANGUAGE_IDS = frozenset(range(27, 37)) | frozenset(range(50, 82))

#: Ids from here on are free for a workbook's own formats, clear of every built-in id the
#: standard lists (the highest, 81, is Thai).
FIRST_CUSTOM_ID = 164


@dataclass(frozen=True)
class NumberFormat:
    """A cell format's number format: its ``numFmtId``, and its code when the workbook
    defines it (``None`` for a built-in id, whose code is implied)."""

    id: int
    code: str | None


class Styles:
    """The cell formats of one ``styles.xml``.  Indexes are ``cellXfs`` positions, which is
    what a cell's ``s`` holds."""

    def __init__(self, package: OpcPackage, part: str) -> None:
        self.package = package
        self.part = part
        self._seen: Element | None = None
        self._xf_index: dict[bytes, int] = {}

    @property
    def _root(self) -> Element:
        root = self.package.tree(self.part)
        if root is not self._seen:  # first use, or an undo replaced the tree
            self._seen = root
            self._xf_index = {}
        return root

    def _xfs(self) -> list[Element]:
        container = self._root.find(_X + "cellXfs")
        return [] if container is None else container.findall(_X + "xf")

    @property
    def count(self) -> int:
        """How many cell formats there are."""
        return len(self._xfs())

    def number_format(self, index: int) -> NumberFormat:
        """Cell format ``index``'s number format; 0 (``General``) for an index past the end,
        as for a cell without ``s``."""
        xfs = self._xfs()
        raw = xfs[index].get("numFmtId", "0") if 0 <= index < len(xfs) else "0"
        number = int(raw) if raw.isdigit() else 0
        return NumberFormat(number, self._codes().get(number))

    def is_date(self, index: int) -> bool | None:
        """Whether cell format ``index`` shows a number as a date or a time: its own code
        read as Excel reads it (:func:`ooxml_common.numfmt.is_date_code`: its first section
        decides), or a built-in id of the all-languages table.  ``None`` for a built-in id whose code depends on the
        application's language: the caller, which knows the locale, decides."""
        fmt = self.number_format(index)
        if fmt.code is not None:
            return is_date_code(fmt.code)
        if fmt.id in LANGUAGE_IDS:
            return None
        return fmt.id in BUILTIN_DATE_IDS

    def font(self, index: int) -> Element | None:
        """Cell format ``index``'s font (the ``font`` of ``fonts`` its ``fontId`` names,
        ECMA-376 Part 1, 18.8.22), the live element; ``None`` when there is none (an index
        past the end is format 0's, as for a cell without ``s``)."""
        xfs = self._xfs()
        if not xfs:
            return None
        xf = xfs[index] if 0 <= index < len(xfs) else xfs[0]
        raw = xf.get("fontId", "0")
        fonts = self._root.find(_X + "fonts")
        if fonts is None or not raw.isdigit():
            return None
        found = fonts.findall(_X + "font")
        number = int(raw)
        return found[number] if number < len(found) else None

    def wraps_text(self, index: int) -> bool:
        """Whether cell format ``index`` wraps its text (``alignment wrapText``): what shows
        a line break in the text as one."""
        xfs = self._xfs()
        if not 0 <= index < len(xfs):
            return False
        alignment = xfs[index].find(_X + "alignment")
        return alignment is not None and alignment.get("wrapText", "").strip().lower() in {
            "1", "true"}

    def with_wrap_text(self, index: int) -> int:
        """The cell format that is ``index`` with its text wrapped (``alignment
        wrapText="1"``, ``applyAlignment="1"``) and everything else the same: ``index``
        itself when it wraps already, an identical existing format, or one appended.
        Measured in Excel for Mac 16.106 (2026-10-20): a line break shows only in a cell
        whose format wraps its text; without it the cell shows one line."""
        xfs = self._xfs()
        if not 0 <= index < len(xfs):
            raise IndexError(f"cell format {index} does not exist; the workbook has "
                             f"{len(xfs)}")
        if self.wraps_text(index):
            return index
        wanted = copy.deepcopy(xfs[index])
        alignment = wanted.find(_X + "alignment")
        if alignment is None:
            alignment = etree.Element(_X + "alignment")
            wanted.insert(0, alignment)     # alignment is the first child of xf (18.8.45)
        alignment.set("wrapText", "1")
        wanted.set("applyAlignment", "1")
        return self._derived(xfs, xfs[index], wanted)

    def with_number_format(self, index: int, number_format: "int | str") -> int:
        """The cell format that is ``index`` with its number format set to
        ``number_format`` -- a built-in id, or a format code, which is found among the
        workbook's own formats or added -- and everything else the same.  ``index`` itself
        when it already has it; an identical existing format when there is one; otherwise
        one appended."""
        xfs = self._xfs()
        if not 0 <= index < len(xfs):
            raise IndexError(f"cell format {index} does not exist; the workbook has "
                             f"{len(xfs)}")
        if isinstance(number_format, bool) or not isinstance(number_format, (int, str)):
            raise ArgumentTypeError("a number format is a built-in id or a format code")
        if isinstance(number_format, str):
            number = self._code_id(number_format)
        else:
            if number_format < 0:
                raise ArgumentError(f"{number_format} is not a number format id")
            number = number_format
        base = xfs[index]
        if base.get("numFmtId", "0") == str(number):
            return index
        wanted = copy.deepcopy(base)
        wanted.set("numFmtId", str(number))
        if number:
            wanted.set("applyNumberFormat", "1")
        return self._derived(xfs, base, wanted)

    def _derived(self, xfs: list[Element], base: Element, wanted: Element) -> int:
        """``wanted`` (a changed copy of ``base``): an identical format's index, or its own
        once appended."""
        key = _key(wanted)
        index_of = self._xf_lookup(xfs)
        if key in index_of:
            return index_of[key]
        container = base.getparent()
        xfs[-1].addnext(wanted)
        _set_count(container, _X + "xf")
        index_of[key] = len(xfs)
        self.package.mark_dirty(self.part)
        return len(xfs)

    # -- internals ---------------------------------------------------------------------------

    def _codes(self) -> dict[int, str]:
        container = self._root.find(_X + "numFmts")
        codes: dict[int, str] = {}
        if container is not None:
            for node in container.iterchildren(_X + "numFmt"):
                raw = node.get("numFmtId", "")
                if raw.isdigit():
                    codes.setdefault(int(raw), node.get("formatCode", ""))
        return codes

    def _code_id(self, code: str) -> int:
        """The id of the workbook's own format ``code``, added when it has none."""
        if not code:
            raise ArgumentError("a format code needs text")
        codes = self._codes()
        for number, existing in codes.items():
            if existing == code:
                return number
        root = self._root
        container = root.find(_X + "numFmts")
        if container is None:
            container = etree.Element(_X + "numFmts")
            root.insert(0, container)  # numFmts is the first child of styleSheet (18.8.39)
        number = max([FIRST_CUSTOM_ID - 1, *codes]) + 1
        node = etree.SubElement(container, _X + "numFmt")
        node.set("numFmtId", str(number))
        node.set("formatCode", code)
        _set_count(container, _X + "numFmt")
        self.package.mark_dirty(self.part)
        return number

    def _xf_lookup(self, xfs: list[Element]) -> dict[bytes, int]:
        """Each distinct cell format's first index, built once per tree."""
        self._root
        if not self._xf_index:
            for position, xf in enumerate(xfs):
                self._xf_index.setdefault(_key(xf), position)
        return self._xf_index


def _key(xf: Element) -> bytes:
    """A cell format's identity: its attributes (in any order) and children."""
    attributes = sorted(xf.attrib.items())
    children = b"".join(etree.tostring(child, method="c14n") for child in xf)
    return repr(attributes).encode() + children


def _set_count(container: Element, tag: str) -> None:
    """Keep a list's ``count`` true; it is optional, and written for a list created here."""
    if container.get("count") is not None or not len(container) > 1:
        container.set("count", str(sum(1 for _ in container.iterchildren(tag))))
