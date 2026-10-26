"""What a cell edit may change in a workbook, checked: the writer's mutation footprint.

:mod:`.workbook` writes cells and keeps the bookkeeping Excel needs in step.  Everything an
edit through :meth:`~.workbook.Worksheet.write` may change is listed here, and
:func:`outside_footprint` compares two packages -- before and after -- and names every
difference outside it:

* **the cells written** -- each cell's element (its value, type, formula, style); a row
  created for a cell (``r`` and ``spans`` only), and a row left empty by a cleared cell,
  removed;
* **the sheet's bookkeeping** -- a row's ``spans`` and the ``dimension``'s ``ref``;
* **shared strings** -- items appended at the end (never changed, removed or reordered),
  and the root's ``count`` and ``uniqueCount``;
* **styles** -- number formats (``numFmt``) and cell formats (``cellXfs/xf``) appended, a
  ``numFmts`` created for the first, and their ``count``;
* **calculation** -- the calculation chain removed with its relationship and its
  content-type override; ``calcPr fullCalcOnLoad`` set to ``1`` (``calcPr`` created for
  it when there was none, after the elements it follows).

Everything else -- every other part's bytes, and in a part that changed every element,
attribute and namespace declaration outside the list -- is preserved, and reported here when
it is not.  Reports carry part names and cell addresses only, never a cell's text.
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from typing import Iterable, Mapping

from lxml import etree
from ..xml import Element, parse_xml

_X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_OFFICE_DOCUMENT = _REL + "officeDocument"
_CELL = re.compile(r"\$?([A-Za-z]{1,3})\$?(\d{1,7})")


def outside_footprint(before: bytes, after: bytes, *,
                      cells: Mapping[str, Iterable[str]] | None = None) -> list[str]:
    """Every difference between two packages of the same workbook that a cell edit may not
    make; empty when the edit stayed inside its footprint (see the module).

    ``cells`` maps a sheet name to the cell references written (``{"Stock": ["D10"]}``):
    a change to any other cell is reported too.  Without it any cell may change, but
    nothing else."""
    old, new = _parts(before), _parts(after)
    found: list[str] = []
    main = _main_part(old)
    sheets = _sheets(old, main) if main else {}
    allowed = None if cells is None else {
        sheets_part: {_position(ref) for ref in cells.get(name, ())}
        for name, sheets_part in sheets.items()}
    chains = _related(old, main, _REL + "calcChain") if main else set()
    removed = set(old) - set(new)
    for part in sorted(removed):
        if part not in chains:
            found.append(f"{part}: removed")
    for part in sorted(set(new) - set(old)):
        found.append(f"{part}: added")
    for part in sorted(set(old) & set(new)):
        if old[part] == new[part]:
            continue
        if not _is_xml(part):
            found.append(f"{part}: changed")
            continue
        a, b = parse_xml(old[part]), parse_xml(new[part])
        if part in sheets.values():
            found += _sheet(part, a, b, None if allowed is None else allowed.get(part, set()))
        elif part in _related(old, main, _REL + "sharedStrings"):
            found += _appended(part, a, b, [("", _X + "si")], {"count", "uniqueCount"})
        elif part in _related(old, main, _REL + "styles"):
            found += _appended(part, a, b, [("numFmts", _X + "numFmt"),
                                            ("cellXfs", _X + "xf")], set(), creatable="numFmts")
        elif part == main:
            found += _workbook(part, a, b)
        elif main and part == _rels_path(main):
            found += _without(part, a, b, lambda node: _target(main, node) in chains
                              and node.get("Type") == _REL + "calcChain")
        elif part == "[Content_Types].xml":
            found += _without(part, a, b, lambda node: node.tag == _CT + "Override"
                              and node.get("PartName", "").lstrip("/") in removed & chains)
        else:
            found.append(f"{part}: changed")
    return found


# -- the parts -------------------------------------------------------------------------------


def _sheet(part: str, a: Element, b: Element, allowed: set[tuple[int, int]] | None
           ) -> list[str]:
    found = _same_node(part, a, b, children=False)
    kids_a, kids_b = list(a), list(b)
    if [k.tag for k in kids_a] != [k.tag for k in kids_b]:
        return found + [f"{part}: its elements changed"]
    for x, y in zip(kids_a, kids_b):
        if x.tag == _X + "dimension":
            if {k: v for k, v in x.attrib.items() if k != "ref"} != \
                    {k: v for k, v in y.attrib.items() if k != "ref"} or len(x) or len(y):
                found.append(f"{part}: dimension changed beyond its ref")
        elif x.tag == _X + "sheetData":
            found += _rows(part, x, y, allowed)
        elif _c14n(x) != _c14n(y):
            found.append(f"{part}: {etree.QName(x).localname} changed")
    return found


def _rows(part: str, a: Element, b: Element, allowed: set[tuple[int, int]] | None
          ) -> list[str]:
    found: list[str] = []
    rows_a, rows_b = _numbered(a), _numbered(b)
    for number in sorted(set(rows_a) | set(rows_b)):
        x, y = rows_a.get(number), rows_b.get(number)
        cells_x = _cells(x) if x is not None else {}
        cells_y = _cells(y) if y is not None else {}
        if x is None or y is None:
            row = y if x is None else x
            assert row is not None  # the number is one of the two parts' rows
            if set(row.attrib) - {"r", "spans"} or any(k.tag != _X + "c" for k in row):
                found.append(f"{part}: row {number} {'added' if x is None else 'removed'} "
                             "with more than cells")
        else:
            if _attrs(x, {"spans"}) != _attrs(y, {"spans"}):
                found.append(f"{part}: row {number}'s attributes changed")
            if [_c14n(k) for k in x if k.tag != _X + "c"] != \
                    [_c14n(k) for k in y if k.tag != _X + "c"]:
                found.append(f"{part}: row {number}'s other content changed")
        for column in sorted(set(cells_x) | set(cells_y)):
            cx, cy = cells_x.get(column), cells_y.get(column)
            if cx is not None and cy is not None and _c14n(cx) == _c14n(cy):
                continue
            if allowed is not None and (number, column) not in allowed:
                found.append(f"{part}: cell {_ref(number, column)} changed but was not written")
    return found


def _workbook(part: str, a: Element, b: Element) -> list[str]:
    """The workbook part: only ``calcPr fullCalcOnLoad`` may have become ``1``."""
    found = _same_node(part, a, b, children=False)
    if [_c14n(k) for k in a if k.tag != _X + "calcPr"] != \
            [_c14n(k) for k in b if k.tag != _X + "calcPr"]:
        found.append(f"{part}: changed beyond calcPr")
    old, new = a.find(_X + "calcPr"), b.find(_X + "calcPr")
    if new is None:
        if old is not None:
            found.append(f"{part}: calcPr removed")
        return found
    before = {} if old is None else dict(old.attrib)
    after = dict(new.attrib)
    full = after.pop("fullCalcOnLoad", None)
    was = before.pop("fullCalcOnLoad", None)
    if before != after or (full != was and full != "1") or len(new) != (
            0 if old is None else len(old)):
        found.append(f"{part}: calcPr changed beyond fullCalcOnLoad")
    return found


def _appended(part: str, a: Element, b: Element, lists: list[tuple[str, str]],
              counts: set[str], creatable: str | None = None) -> list[str]:
    """A part whose only changes are items appended to lists (``(holder, item tag)``, the
    holder ``""`` for the root itself), the lists' ``count``, and ``counts`` on the root."""
    found = []
    if a.tag != b.tag or a.nsmap != b.nsmap or _attrs(a, counts) != _attrs(b, counts):
        found.append(f"{part}: its root changed")
    holders = {name: item for name, item in lists if name}
    own = next((item for name, item in lists if not name), None)
    if own is not None:
        found += _list(part, "root", a, b, own, set(counts))
    kids_a = [k for k in a if k.tag != own]
    kids_b = [k for k in b if k.tag != own and not (
        creatable and k.tag == _X + creatable and a.find(k.tag) is None
        and all(item.tag == holders[creatable] for item in k))]
    if [k.tag for k in kids_a] != [k.tag for k in kids_b]:
        return found + [f"{part}: its elements changed"]
    for x, y in zip(kids_a, kids_b):
        name = etree.QName(x).localname
        if name in holders and x.tag == _X + name:
            found += _list(part, name, x, y, holders[name], {"count"})
        elif _c14n(x) != _c14n(y):
            found.append(f"{part}: {name} changed")
    return found


def _list(part: str, label: str, x: Element, y: Element, item: str, counts: set[str]
          ) -> list[str]:
    found = []
    if label != "root" and _attrs(x, counts) != _attrs(y, counts):
        found.append(f"{part}: {label}'s attributes changed")
    items_x = [_c14n(k) for k in x if k.tag == item]
    items_y = [_c14n(k) for k in y if k.tag == item]
    if items_y[:len(items_x)] != items_x:
        found.append(f"{part}: existing {etree.QName(item).localname} items changed")
    if label != "root" and [_c14n(k) for k in x if k.tag != item] != \
            [_c14n(k) for k in y if k.tag != item]:
        found.append(f"{part}: {label} changed beyond its items")
    return found


def _without(part: str, a: Element, b: Element, may_go) -> list[str]:
    """A part whose only change is children removed that ``may_go`` allows."""
    if _attrs(a, set()) != _attrs(b, set()):
        return [f"{part}: its root changed"]
    kept = [_c14n(k) for k in a if not may_go(k)]
    if kept != [_c14n(k) for k in b]:
        return [f"{part}: changed beyond the calculation chain's entries"]
    return []


# -- helpers ---------------------------------------------------------------------------------


def _parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {info.filename.lstrip("/"): archive.read(info) for info in archive.infolist()
                if not info.is_dir()}


def _is_xml(part: str) -> bool:
    return part.endswith((".xml", ".rels"))


def _rels_path(part: str) -> str:
    directory, name = posixpath.split(part)
    return posixpath.join(directory, "_rels", name + ".rels")


def _target(source: str, rel: Element) -> str:
    target = rel.get("Target", "")
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(source), target))


def _relationships(parts: dict[str, bytes], source: str) -> list[Element]:
    raw = parts.get(_rels_path(source) if source else "_rels/.rels")
    return [] if raw is None else [n for n in parse_xml(raw) if n.tag == _PR + "Relationship"]


def _main_part(parts: dict[str, bytes]) -> str | None:
    for rel in _relationships(parts, ""):
        if rel.get("Type") == _OFFICE_DOCUMENT:
            return rel.get("Target", "").lstrip("/")
    return None


def _related(parts: dict[str, bytes], source: str | None, kind: str) -> set[str]:
    if not source:
        return set()
    return {_target(source, rel) for rel in _relationships(parts, source)
            if rel.get("Type") == kind and rel.get("TargetMode") != "External"}


def _sheets(parts: dict[str, bytes], main: str) -> dict[str, str]:
    """Sheet name -> worksheet part."""
    targets = {rel.get("Id"): _target(main, rel) for rel in _relationships(parts, main)
               if rel.get("Type") == _REL + "worksheet"}
    found = {}
    for node in parse_xml(parts[main]).iter(_X + "sheet"):
        part = targets.get(node.get(_R + "id"))
        if part is not None:
            found[node.get("name", "")] = part
    return found


def _numbered(data: Element) -> dict[int, Element]:
    rows: dict[int, Element] = {}
    number = 0
    for row in data.iterchildren(_X + "row"):
        number = int(row.get("r")) if row.get("r") else number + 1
        rows.setdefault(number, row)
    return rows


def _cells(row: Element) -> dict[int, Element]:
    cells: dict[int, Element] = {}
    column = 0
    for node in row.iterchildren(_X + "c"):
        column = _position(node.get("r"))[1] if node.get("r") else column + 1
        cells.setdefault(column, node)
    return cells


def _position(ref: str) -> tuple[int, int]:
    match = _CELL.fullmatch(ref.strip())
    if match is None:
        raise ValueError(f"{ref!r} is not a cell reference")
    column = 0
    for letter in match.group(1).upper():
        column = column * 26 + ord(letter) - 64
    return int(match.group(2)), column


def _ref(row: int, column: int) -> str:
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row}"


def _attrs(node: Element, ignore: set[str]) -> dict[str, str]:
    return {k: v for k, v in node.attrib.items() if k not in ignore}


def _same_node(part: str, a: Element, b: Element, *, children: bool) -> list[str]:
    if a.tag != b.tag or dict(a.attrib) != dict(b.attrib) or a.nsmap != b.nsmap:
        return [f"{part}: its root element, attributes or namespace declarations changed"]
    if children and _c14n(a) != _c14n(b):
        return [f"{part}: changed"]
    return []


def _c14n(node: Element) -> bytes:
    return etree.tostring(node, method="c14n")
