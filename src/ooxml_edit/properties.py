"""A package's document properties: the core properties and the custom ones, read and set.

Every Office package may carry two property parts, related from the package root:

* **Core properties** (ECMA-376 Part 2, §11: ``docProps/core.xml``, Dublin Core and OPC's
  own terms) -- title, subject, creator, keywords, description, last modified by, revision,
  created, modified, category, content status, language, identifier, version, last printed.
  :data:`CORE_PROPERTIES` names them in the order the applications write them: measured from
  the Office for Mac 16.106 applications (2026-11-09: title, subject, creator, keywords,
  description, last modified by, created, modified and category set through one's object
  model and saved; revision after last modified by, content status and language after
  category, as the word processing library measured earlier).  The rest follow, in the
  schema's order.
* **Custom properties** (ECMA-376 Part 1, §22.3: ``docProps/custom.xml``) -- name and typed
  value pairs, each a ``property`` with the format id every application writes
  (:data:`CUSTOM_FMTID`), a ``pid`` from 2 up, and one value of the document property
  types (§22.4: ``vt:lpwstr``, ``vt:i4``, ``vt:r8``, ``vt:bool``, ``vt:filetime``...).

The functions here work on any :class:`~ooxml_edit.opc.OpcPackage`, so each format library
offers the same properties the same way; the library makes the change one undoable step
(the parts are ordinary parts of its history).  Nothing here sets the modified date on its
own: a save writes the part as it is, and ``modified`` changes only when it is set.  Values
are checked before anything changes, every problem listed (:class:`PropertyError`).

Dates are read as aware ``datetime`` objects and written in UTC, whole seconds, as Office
writes them (``2026-11-09T08:30:00Z``).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Mapping

from lxml import etree

from .opc import OpcPackage
from .xml import Element

CORE_NS = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcmitype": "http://purl.org/dc/dcmitype/",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
CUSTOM_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"

CORE_PART = "docProps/core.xml"
CUSTOM_PART = "docProps/custom.xml"
REL_CORE = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
REL_CUSTOM = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
CT_CORE = "application/vnd.openxmlformats-package.core-properties+xml"
CT_CUSTOM = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
#: The format id of every custom property (ECMA-376 Part 1, §22.3.2.2: "the
#: FMTID_UserDefinedProperties"), the one the applications write.
CUSTOM_FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"

#: Property name -> its element, in the order the applications write them (see the module).
CORE_PROPERTIES: dict[str, str] = {
    "title": "dc:title",
    "subject": "dc:subject",
    "creator": "dc:creator",
    "keywords": "cp:keywords",
    "description": "dc:description",
    "last_modified_by": "cp:lastModifiedBy",
    "revision": "cp:revision",
    "created": "dcterms:created",
    "modified": "dcterms:modified",
    "category": "cp:category",
    "content_status": "cp:contentStatus",
    "language": "dc:language",
    "identifier": "dc:identifier",
    "version": "cp:version",
    "last_printed": "cp:lastPrinted",
}
#: Other names a caller may give a core property by: the element's own (``lastModifiedBy``)
#: and the names the format libraries' tools use (``author``, ``status``, ``comments``).
CORE_ALIASES: dict[str, str] = {
    **{tag.partition(":")[2]: name for name, tag in CORE_PROPERTIES.items()},
    "author": "creator",
    "status": "content_status",
    "comments": "description",
}
#: The dated properties.  ``created`` and ``modified`` carry ``xsi:type="dcterms:W3CDTF"``
#: (ECMA-376 Part 2, §11.4); ``lastPrinted`` is a plain ``xsd:dateTime``.
DATED = ("created", "modified", "last_printed")

#: The value types written for a custom property, by the Python type given.
CUSTOM_TYPES = ("text", "integer", "number", "bool", "date")
#: Custom value elements read, by local name -> the type read as.
_READ_TYPES = {
    "lpwstr": "text", "lpstr": "text", "bstr": "text",
    "i1": "integer", "i2": "integer", "i4": "integer", "i8": "integer", "int": "integer",
    "ui1": "integer", "ui2": "integer", "ui4": "integer", "ui8": "integer", "uint": "integer",
    "r4": "number", "r8": "number", "decimal": "number",
    "bool": "bool", "filetime": "date", "date": "date",
}
#: The element each type is written as.
_WRITE_TYPES = {"text": "lpwstr", "integer": "i4", "number": "r8", "bool": "bool",
                "date": "filetime"}
_I4 = (-(2 ** 31), 2 ** 31 - 1)
#: What XML 1.0 cannot carry: refused in a property's text.
_UNCARRIED = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")
_DECL = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'


class PropertyError(ValueError):
    """Properties refused before anything changed: :attr:`problems` lists each as
    ``(name, why)``; the message joins them."""

    def __init__(self, problems: list[tuple[str, str]]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(f"{name}: {why}" for name, why in self.problems))


@dataclass(frozen=True)
class CustomProperty:
    """One custom property as read: its ``value`` (``str``, ``int``, ``float``, ``bool``,
    an aware ``datetime``; ``None`` for a type not read), its ``type`` (one of
    :data:`CUSTOM_TYPES`, or the value element's own name for another), and ``linked``,
    the name of what a linked property's value comes from (``linkTarget``), whose value
    the application refreshes from the document's content."""

    value: Any
    type: str
    linked: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"value": self.value.isoformat().replace("+00:00", "Z")
                               if isinstance(self.value, datetime) else self.value,
                               "type": self.type}
        if self.linked is not None:
            out["linked"] = self.linked
        return out


# -- dates -----------------------------------------------------------------------------------------


def w3cdtf(moment: datetime) -> str:
    """``2026-11-09T08:30:00Z``: UTC, whole seconds, as Office writes it (a naive datetime is
    taken as UTC)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_w3cdtf(text: str | None) -> datetime | None:
    """A W3CDTF date (``2026-11-09``, ``2026-11-09T08:30:00Z``, with an offset or fraction)
    as an aware datetime; ``None`` when it is none."""
    if not text:
        return None
    value = text.strip()
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time(), tzinfo=timezone.utc)
    if isinstance(value, str):
        return parse_w3cdtf(value)
    return None


# -- the parts --------------------------------------------------------------------------------------


def _q(tag: str) -> str:
    prefix, _, name = tag.partition(":")
    return "{%s}%s" % (CORE_NS[prefix], name)


def _root_part(package: OpcPackage, rel_type: str) -> str | None:
    for rel in package.relationships("").values():
        if rel.type == rel_type and not rel.is_external and rel.target_part \
                and package.has_part(rel.target_part):
            return rel.target_part
    return None


def core_part(package: OpcPackage) -> str | None:
    """The core properties part the package root relates to, or ``None``."""
    return _root_part(package, REL_CORE)


def custom_part(package: OpcPackage) -> str | None:
    """The custom properties part the package root relates to, or ``None``."""
    return _root_part(package, REL_CUSTOM)


def _new_part(package: OpcPackage, name: str, root: Element, content_type: str,
              rel_type: str) -> str:
    part = package.unused_part_name(name) if package.has_part(name) else name
    package.add_part(part, _DECL + etree.tostring(root, encoding="UTF-8"), content_type,
                     override=True)
    package.add_relationship("", rel_type, part)
    return part


# -- core properties -----------------------------------------------------------------------------


def read_core(package: OpcPackage) -> dict[str, Any]:
    """Every core property the package sets: name -> text; dates as aware ``datetime``
    objects (the text when it is no date), ``revision`` as an ``int`` when it is a number."""
    part = core_part(package)
    root = None if part is None else package.tree(part)
    out: dict[str, Any] = {}
    if root is None:
        return out
    for name, tag in CORE_PROPERTIES.items():
        node = root.find(_q(tag))
        if node is None:
            continue
        text = node.text or ""
        if name in DATED:
            out[name] = parse_w3cdtf(text) or text
        elif name == "revision" and text.strip().isdigit():
            out[name] = int(text.strip())
        else:
            out[name] = text
    return out


def core_name(name: str) -> str | None:
    """The core property ``name`` means (:data:`CORE_PROPERTIES`' name, the element's,
    or an alias), or ``None``."""
    if name in CORE_PROPERTIES:
        return name
    return CORE_ALIASES.get(name)


def check_core(values: Mapping[str, Any]) -> dict[str, Any]:
    """``values`` by :data:`CORE_PROPERTIES`' names, each checked: text, a date (a
    ``datetime``, a ``date`` or W3CDTF text) for :data:`DATED`, a whole number for
    ``revision``; ``None`` removes one.  :class:`PropertyError` lists every problem."""
    problems: list[tuple[str, str]] = []
    out: dict[str, Any] = {}
    for given, value in values.items():
        name = core_name(given) if isinstance(given, str) else None
        if name is None:
            problems.append((str(given), "not a core property; one of "
                             + ", ".join(CORE_PROPERTIES)))
            continue
        if name in out:
            problems.append((given, f"named twice (as {name})"))
            continue
        if value is None:
            out[name] = None
        elif name in DATED:
            moment = None if isinstance(value, bool) else _as_datetime(value)
            if moment is None:
                problems.append((given, "a date: a datetime, a date or W3CDTF text such as "
                                 "2026-11-09T08:30:00Z"))
            else:
                out[name] = moment
        elif name == "revision":
            if isinstance(value, bool) or not (isinstance(value, int) and value >= 0
                                               or isinstance(value, str)
                                               and value.strip().isdigit()):
                problems.append((given, "a whole number, 0 or more"))
            else:
                out[name] = int(value)
        elif not isinstance(value, str):
            problems.append((given, f"text, not {type(value).__name__}"))
        elif _UNCARRIED.search(value):
            problems.append((given, "control characters (other than tab and line breaks) "
                             "cannot be stored in a property"))
        else:
            out[name] = value
    if problems:
        raise PropertyError(problems)
    return out


def core_changes(package: OpcPackage, values: Mapping[str, Any]) -> list[str]:
    """The names of the checked ``values`` (:func:`check_core`) that would change what the
    package holds."""
    current = read_core(package)
    changed = []
    for name, value in values.items():
        held = current.get(name)
        if value is None:
            if name in current:
                changed.append(name)
        elif name in DATED:
            if not isinstance(held, datetime) or w3cdtf(held) != w3cdtf(value):
                changed.append(name)
        elif held != value:
            changed.append(name)
    return changed


def write_core(package: OpcPackage, values: Mapping[str, Any]) -> list[str]:
    """Set (a value) or remove (``None``) core properties, checked first
    (:func:`check_core`); the names that changed.  New elements go in
    :data:`CORE_PROPERTIES`' order; a package without the part gets one, related from the
    package root."""
    checked = check_core(values)
    changed = core_changes(package, checked)
    if not changed:
        return []
    part = core_part(package)
    if part is None:
        if all(checked[name] is None for name in changed):
            return []
        part = _new_part(package, CORE_PART, etree.Element(_q("cp:coreProperties"),
                                                           nsmap=CORE_NS),
                         CT_CORE, REL_CORE)
    root = package.tree(part)
    assert root is not None
    order = list(CORE_PROPERTIES)
    for name in changed:
        value = checked[name]
        tag = _q(CORE_PROPERTIES[name])
        node = root.find(tag)
        if value is None:
            if node is not None:
                root.remove(node)
            continue
        if node is None:
            node = etree.Element(tag)
            rank = order.index(name)
            after = None
            for child in root:
                known = next((n for n, t in CORE_PROPERTIES.items() if _q(t) == child.tag),
                             None)
                if known is not None and order.index(known) < rank:
                    after = child
            if after is None:
                root.insert(0, node)
            else:
                after.addnext(node)
        if name in ("created", "modified"):
            node.set(_q("xsi:type"), "dcterms:W3CDTF")
        node.text = w3cdtf(value) if name in DATED else str(value)
    package.mark_dirty(part)
    return changed


# -- custom properties ---------------------------------------------------------------------------


def _vt(local: str) -> str:
    return "{%s}%s" % (VT_NS, local)


def _value_of(node: Element) -> tuple[Any, str]:
    local = etree.QName(node).localname if isinstance(node.tag, str) else ""
    kind = _READ_TYPES.get(local)
    text = node.text or ""
    if kind == "text":
        return text, kind
    if kind == "integer":
        try:
            return int(text.strip()), kind
        except ValueError:
            return None, kind
    if kind == "number":
        try:
            return float(text.strip()), kind
        except ValueError:
            return None, kind
    if kind == "bool":
        word = text.strip().lower()
        return (word in ("true", "1")) if word in ("true", "false", "1", "0") else None, kind
    if kind == "date":
        return parse_w3cdtf(text), kind
    return None, local or "unknown"


def _properties(root: Element) -> list[Element]:
    return [node for node in root if node.tag == "{%s}property" % CUSTOM_NS]


def read_custom(package: OpcPackage) -> dict[str, CustomProperty]:
    """Every custom property, by name, in the file's order (see :class:`CustomProperty`)."""
    part = custom_part(package)
    root = None if part is None else package.tree(part)
    out: dict[str, CustomProperty] = {}
    if root is None:
        return out
    for node in _properties(root):
        name = node.get("name")
        if not name:
            continue
        values = [child for child in node if isinstance(child.tag, str)]
        value, kind = _value_of(values[0]) if values else (None, "empty")
        out[name] = CustomProperty(value, kind, node.get("linkTarget"))
    return out


def custom_type(value: Any) -> str | None:
    """The type a value is written as (:data:`CUSTOM_TYPES`), or ``None``: ``str`` text,
    ``bool`` bool, ``int`` integer (within 32 bits; a larger one is a number), ``float``
    number, ``datetime`` or ``date`` date."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "integer" if _I4[0] <= value <= _I4[1] else "number"
    if isinstance(value, float):
        return "number" if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return "date"
    if isinstance(value, str):
        return "text"
    return None


def check_custom(package: OpcPackage, values: Mapping[str, Any]) -> dict[str, Any]:
    """``values`` (name -> a value, or ``None`` to remove the property) checked against
    the package: a name is text of 1 to 255 characters without control characters, a
    value one of :func:`custom_type`'s, a linked property is not given a value (its
    application would overwrite it; remove it instead).  Names compare as the
    applications compare them, case aside: a name differing from a property's only in case
    is that property.  :class:`PropertyError` lists every problem."""
    problems: list[tuple[str, str]] = []
    current = read_custom(package)
    folded = {name.casefold(): name for name in current}
    out: dict[str, Any] = {}
    for name, value in values.items():
        if not isinstance(name, str) or not name.strip():
            problems.append((str(name), "a custom property's name is text"))
            continue
        if len(name) > 255 or _UNCARRIED.search(name) or "\n" in name or "\r" in name \
                or "\t" in name:
            problems.append((name, "a custom property's name is at most 255 characters, "
                             "with no control characters"))
            continue
        held = folded.get(name.casefold())
        key = held or name
        if key in out:
            problems.append((name, f"named twice (as {key})"))
            continue
        if value is not None:
            kind = custom_type(value)
            if kind is None:
                problems.append((name, "text, a whole number, a number, true or false, or a "
                                 f"date; not {type(value).__name__}"))
                continue
            if kind == "text" and _UNCARRIED.search(value):
                problems.append((name, "control characters (other than tab and line breaks) "
                                 "cannot be stored in a property"))
                continue
            if held is not None and current[held].linked is not None:
                problems.append((name, f"linked to {current[held].linked!r}, whose value its "
                                 "application sets: remove it (None) instead"))
                continue
        out[key] = value
    if problems:
        raise PropertyError(problems)
    return out


def _text(value: Any, kind: str) -> str:
    if kind == "bool":
        return "true" if value else "false"
    if kind == "date":
        moment = _as_datetime(value)
        assert moment is not None
        return w3cdtf(moment)
    if kind == "number":
        number = float(value)
        # As the applications write a number: a whole one without its ".0" (measured: an
        # r8 of 2**40 read and saved again as 1099511627776), others shortest round-trip.
        return str(int(number)) if number.is_integer() and abs(number) < 1e15 \
            else repr(number)
    return str(value)


def _same(held: CustomProperty, value: Any) -> bool:
    kind = custom_type(value)
    if kind != held.type:
        return False
    if kind == "date":
        return isinstance(held.value, datetime) and w3cdtf(held.value) == _text(value, kind)
    return bool(held.value == value)


def custom_changes(package: OpcPackage, values: Mapping[str, Any]) -> list[str]:
    """The names of the checked ``values`` (:func:`check_custom`) that would change what
    the package holds."""
    current = read_custom(package)
    changed = []
    for name, value in values.items():
        if value is None:
            if name in current:
                changed.append(name)
        elif name not in current or not _same(current[name], value):
            changed.append(name)
    return changed


def write_custom(package: OpcPackage, values: Mapping[str, Any]) -> list[str]:
    """Set (a value) or remove (``None``) custom properties, checked first
    (:func:`check_custom`); the names that changed.  A property keeps its place and
    ``pid``; a new one goes last with the next ``pid``.  A package without the part gets
    one, related from the package root; the part goes, with its relationship, when its
    last property is removed."""
    checked = check_custom(package, values)
    changed = custom_changes(package, checked)
    if not changed:
        return []
    part = custom_part(package)
    if part is None:
        part = _new_part(package, CUSTOM_PART, etree.Element(
            "{%s}Properties" % CUSTOM_NS, nsmap={None: CUSTOM_NS, "vt": VT_NS}),
            CT_CUSTOM, REL_CUSTOM)
    root = package.tree(part)
    assert root is not None
    nodes = {node.get("name"): node for node in _properties(root)}
    for name in changed:
        value = checked[name]
        node = nodes.get(name)
        if value is None:
            if node is not None:
                root.remove(node)
            continue
        if node is None:
            pids = [int(n.get("pid")) for n in _properties(root)
                    if (n.get("pid") or "").isdigit()]
            node = etree.SubElement(root, "{%s}property" % CUSTOM_NS)
            node.set("fmtid", CUSTOM_FMTID)
            node.set("pid", str(max(pids + [1]) + 1))
            node.set("name", name)
        for child in list(node):
            node.remove(child)
        kind = custom_type(value)
        assert kind is not None
        etree.SubElement(node, _vt(_WRITE_TYPES[kind])).text = _text(value, kind)
    if not _properties(root):
        for rel_id, rel in list(package.relationships("").items()):
            if rel.type == REL_CUSTOM and rel.target_part == part:
                package.remove_relationship("", rel_id)
        package.remove_part(part)
    else:
        package.mark_dirty(part)
    return changed


__all__ = ["CORE_ALIASES", "CORE_NS", "CORE_PART", "CORE_PROPERTIES", "CT_CORE", "CT_CUSTOM",
           "CUSTOM_FMTID", "CUSTOM_NS", "CUSTOM_PART", "CUSTOM_TYPES", "CustomProperty",
           "DATED", "PropertyError", "REL_CORE", "REL_CUSTOM", "VT_NS", "check_core",
           "check_custom", "core_changes", "core_name", "core_part", "custom_changes",
           "custom_part", "custom_type", "parse_w3cdtf", "read_core", "read_custom",
           "w3cdtf", "write_core", "write_custom"]
