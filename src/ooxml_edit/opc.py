"""The OPC package, readable *and* writable.

Format-neutral: this is the container every OOXML format shares, and nothing here knows what
the document inside it means.  Format layers subclass :class:`OpcPackage` to add their entry
points.

An OOXML file is a ZIP of XML "parts" wired together by relationship files.  This module owns
the container: it keeps every part's original bytes, hands out lazily-parsed lxml trees for the
parts a caller wants to change, and writes the archive back.

The losslessness rule lives here and is worth stating plainly:

    A part that was never parsed is written back as the exact bytes that were read.

Only parts whose tree was actually touched get re-serialized.  Everything else -- media,
embedded workbooks, thumbnails, parts for features this library has never heard of -- passes
through untouched, so an open/save round trip with no edits is byte-identical.  That is
checked by ``tests/test_roundtrip.py`` across the whole fixture corpus, and it is the property
the entire editing layer rests on.
"""

from __future__ import annotations

import io
import os
import posixpath
import zipfile
from dataclasses import dataclass
from typing import BinaryIO

from lxml import etree

from .xml import Element, parse_xml, serialize

CONTENT_TYPES_PART = "[Content_Types].xml"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
REL_OFFICE_DOCUMENT = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
REL_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"


@dataclass(frozen=True)
class Relationship:
    id: str
    type: str
    target: str
    #: Normalised package path, or ``None`` for External targets (hyperlinks).
    target_part: str | None
    is_external: bool


class OpcPackage:
    """An open OOXML package: original bytes per part, plus lxml trees for the parts being edited."""

    def __init__(self, entries: list[zipfile.ZipInfo], parts: dict[str, bytes]) -> None:
        #: Preserved so ``save`` reproduces the original entry order and compression.  Writing
        #: parts in a different order is legal but makes every diff of a saved deck useless.
        self._entries = entries
        self._parts = parts
        self._trees: dict[str, Element] = {}
        self._dirty: set[str] = set()
        self._rels_cache: dict[str, dict[str, Relationship]] = {}
        #: What was read, so undo can put back a part that was replaced or remove one that was
        #: added.  Shares the bytes objects with ``_parts``; nothing is copied.
        self._original: dict[str, bytes] = dict(parts)
        #: Parts written as raw bytes since opening (added or replaced), not as trees.
        self._raw_changes: set[str] = set()

    # -- loading ---------------------------------------------------------------------------

    @classmethod
    def open(cls, source: str | os.PathLike[str] | bytes | BinaryIO):
        if isinstance(source, bytes):
            source = io.BytesIO(source)
        elif isinstance(source, os.PathLike):
            source = os.fspath(source)
        with zipfile.ZipFile(source) as archive:
            entries = [info for info in archive.infolist() if not info.is_dir()]
            parts = {normalize_part_path(info.filename): archive.read(info) for info in entries}
        return cls(entries, parts)

    # -- part access -----------------------------------------------------------------------

    @property
    def part_names(self) -> list[str]:
        return list(self._parts)

    def has_part(self, path: str) -> bool:
        return normalize_part_path(path) in self._parts

    def read(self, path: str) -> bytes | None:
        """Current bytes of a part: the live tree if it was edited, the original otherwise."""
        path = normalize_part_path(path)
        if path in self._dirty:
            return serialize(self._trees[path])
        return self._parts.get(path)

    def tree(self, path: str) -> Element | None:
        """Parsed root of a part, cached.  Parsing alone does not mark the part dirty."""
        path = normalize_part_path(path)
        cached = self._trees.get(path)
        if cached is not None:
            return cached
        raw = self._parts.get(path)
        if raw is None:
            return None
        root = parse_xml(raw)
        self._trees[path] = root
        return root

    def mark_dirty(self, path: str) -> None:
        """Record that a part's tree was changed, so ``save`` re-serializes it.

        Callers must invoke this after mutating a tree.  It is deliberately explicit: lxml
        gives no change notification, and inferring dirtiness by re-serializing and comparing
        would defeat the point of not re-serializing.
        """
        path = normalize_part_path(path)
        if path not in self._trees:
            raise KeyError(f"part {path!r} was never parsed, so it cannot be dirty")
        self._dirty.add(path)
        # A rels part that changed invalidates its cached relationships.
        self._rels_cache.pop(_part_for_rels(path), None)

    @property
    def dirty_parts(self) -> frozenset[str]:
        return frozenset(self._dirty)

    def replace_part(self, path: str, data: bytes) -> None:
        """Overwrite (or add) a part with raw bytes, dropping any parsed tree for it.

        Raw writes are part of the undo snapshot, so undoing an edit that added a part removes
        it again and the package saves byte-identical to before.
        """
        path = normalize_part_path(path)
        new = path not in self._parts
        self._parts[path] = data
        self._trees.pop(path, None)
        self._dirty.discard(path)
        self._rels_cache.pop(_part_for_rels(path), None)
        self._raw_changes.add(path)
        if new:
            self._entries.append(_new_entry(path))

    def add_part(self, path: str, data: bytes, content_type: str | None = None) -> str:
        """Add a new part, declaring its content type when one is given.

        ``content_type`` is registered as a ``Default`` for the part's extension when the
        extension has none yet -- the convention for media -- and as an ``Override`` for the
        part name otherwise.
        """
        path = normalize_part_path(path)
        if path in self._parts:
            raise ValueError(f"part {path!r} already exists")
        self.replace_part(path, data)
        if content_type is not None:
            self.declare_content_type(path, content_type)
        return path

    def unused_part_name(self, template: str) -> str:
        """``template`` with ``{n}`` replaced by the lowest number not already a part."""
        index = 1
        while normalize_part_path(template.format(n=index)) in self._parts:
            index += 1
        return normalize_part_path(template.format(n=index))

    def find_part_with_bytes(self, data: bytes, directory: str) -> str | None:
        """An existing part under ``directory`` holding exactly ``data`` -- for de-duplication."""
        prefix = normalize_part_path(directory).rstrip("/") + "/"
        for path, existing in self._parts.items():
            if path.startswith(prefix) and path not in self._dirty and existing == data:
                return path
        return None

    # -- content types ---------------------------------------------------------------------

    def content_type(self, path: str) -> str | None:
        """The declared content type of a part: its Override, else its extension's Default."""
        path = normalize_part_path(path)
        root = self.tree(CONTENT_TYPES_PART)
        if root is None:
            return None
        extension = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        default = None
        for node in root:
            if node.tag == "{%s}Override" % CONTENT_TYPES_NS:
                if normalize_part_path(node.get("PartName") or "") == path:
                    return node.get("ContentType")
            elif node.tag == "{%s}Default" % CONTENT_TYPES_NS:
                if (node.get("Extension") or "").lower() == extension:
                    default = node.get("ContentType")
        return default

    def declare_content_type(self, path: str, content_type: str) -> None:
        """Make ``path`` resolve to ``content_type``, touching ``[Content_Types].xml`` only if
        it does not already."""
        if self.content_type(path) == content_type:
            return
        root = self.tree(CONTENT_TYPES_PART)
        if root is None:
            raise ValueError("the package has no [Content_Types].xml")
        path = normalize_part_path(path)
        extension = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        has_default = any(
            node.tag == "{%s}Default" % CONTENT_TYPES_NS
            and (node.get("Extension") or "").lower() == extension
            for node in root
        )
        if extension and not has_default:
            node = etree.Element("{%s}Default" % CONTENT_TYPES_NS)
            node.set("Extension", extension)
            node.set("ContentType", content_type)
            defaults = [n for n in root if n.tag == "{%s}Default" % CONTENT_TYPES_NS]
            if defaults:
                defaults[-1].addnext(node)
            else:
                root.insert(0, node)
        else:
            node = etree.SubElement(root, "{%s}Override" % CONTENT_TYPES_NS)
            node.set("PartName", "/" + path)
            node.set("ContentType", content_type)
        _match_tail(node)
        self.mark_dirty(CONTENT_TYPES_PART)

    # -- relationships ---------------------------------------------------------------------

    def relationships(self, part_path: str) -> dict[str, Relationship]:
        """Relationships declared by ``part_path``, keyed by relationship id."""
        part_path = normalize_part_path(part_path)
        cached = self._rels_cache.get(part_path)
        if cached is not None:
            return cached

        result: dict[str, Relationship] = {}
        root = self.tree(rels_path_for(part_path))
        if root is not None:
            base = posixpath.dirname(part_path)
            for node in root.findall("{%s}Relationship" % RELS_NS):
                rel_id = node.get("Id")
                rel_type = node.get("Type")
                target = node.get("Target")
                if rel_id is None or rel_type is None or target is None:
                    continue
                external = (node.get("TargetMode") or "") == "External"
                result[rel_id] = Relationship(
                    id=rel_id,
                    type=rel_type,
                    target=target,
                    target_part=None if external else resolve_target(base, target),
                    is_external=external,
                )
        self._rels_cache[part_path] = result
        return result

    def related_part(self, part_path: str, rel_id: str | None) -> str | None:
        if rel_id is None:
            return None
        relationship = self.relationships(part_path).get(rel_id)
        if relationship is None or relationship.is_external:
            return None
        return relationship.target_part

    def related_parts_of_type(self, part_path: str, rel_type: str) -> list[str]:
        return [
            rel.target_part
            for rel in self.relationships(part_path).values()
            if rel.type == rel_type and rel.target_part is not None
        ]

    def add_relationship(self, part_path: str, rel_type: str, target_part: str) -> str:
        """Relate ``part_path`` to ``target_part``; returns the relationship id.

        An existing relationship of the same type to the same target is reused, as Office
        does, rather than duplicated.
        """
        part_path = normalize_part_path(part_path)
        target_part = normalize_part_path(target_part)
        for rel in self.relationships(part_path).values():
            if rel.type == rel_type and rel.target_part == target_part and not rel.is_external:
                return rel.id
        rels_path = rels_path_for(part_path)
        if not self.has_part(rels_path):
            self.add_part(
                rels_path,
                b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                b'<Relationships xmlns="%s"/>' % RELS_NS.encode(),
                "application/vnd.openxmlformats-package.relationships+xml",
            )
        root = self.tree(rels_path)
        assert root is not None
        rel_id = self.next_rel_id(part_path)
        base = posixpath.dirname(part_path)
        node = etree.SubElement(root, "{%s}Relationship" % RELS_NS)
        node.set("Id", rel_id)
        node.set("Type", rel_type)
        node.set("Target", posixpath.relpath(target_part, base or "."))
        _match_tail(node)
        self.mark_dirty(rels_path)
        return rel_id

    def next_rel_id(self, part_path: str) -> str:
        """An ``rIdN`` not already used by ``part_path``."""
        used = self.relationships(part_path)
        index = len(used) + 1
        while f"rId{index}" in used:
            index += 1
        return f"rId{index}"

    def main_document_part(self) -> str | None:
        """Target of the package-level ``officeDocument`` relationship, if any."""
        for rel in self.relationships("").values():
            if rel.type == REL_OFFICE_DOCUMENT and rel.target_part is not None:
                return rel.target_part
        return None

    # -- snapshots (undo) ------------------------------------------------------------------

    def snapshot(self) -> "PackageSnapshot":
        """Current bytes of every edited part.

        Only edited parts are captured: everything else is still the original bytes and cannot
        have changed, so a snapshot costs one serialization per edited part -- tens of KB.
        Parts written raw (added media, say) are captured by reference, not copied.
        """
        return PackageSnapshot(
            trees={path: serialize(self._trees[path]) for path in self._dirty},
            raw={path: self._parts.get(path) for path in self._raw_changes},
        )

    def restore(self, snapshot: "PackageSnapshot") -> None:
        """Reset every edited part to a previous snapshot.

        Parts that were edited then *and* now are reset to the snapshot; parts edited since are
        dropped back to their original bytes, and parts added since are removed.
        """
        # Raw parts first: a tree restored below may belong to a part that was re-added.
        for path in self._raw_changes | set(snapshot.raw):
            if path in snapshot.raw:
                data = snapshot.raw[path]  # None: the part did not exist then
            else:
                data = self._original.get(path)  # changed since: back to what was read
            if data is not None:
                self._set_raw(path, data)
            else:
                self._parts.pop(path, None)
                self._entries = [e for e in self._entries
                                 if normalize_part_path(e.filename) != path]
            self._trees.pop(path, None)
            self._rels_cache.pop(_part_for_rels(path), None)
        self._raw_changes = set(snapshot.raw)

        for path in self._dirty - set(snapshot.trees):
            self._trees.pop(path, None)
            self._rels_cache.pop(_part_for_rels(path), None)
        self._dirty = set()
        for path, data in snapshot.trees.items():
            self._trees[path] = parse_xml(data)
            self._dirty.add(path)
            self._rels_cache.pop(_part_for_rels(path), None)

    def _set_raw(self, path: str, data: bytes) -> None:
        if path not in self._parts:
            self._entries.append(_new_entry(path))
        self._parts[path] = data

    # -- saving ----------------------------------------------------------------------------

    def to_bytes(self) -> bytes:
        buffer = io.BytesIO()
        self.write(buffer)
        return buffer.getvalue()

    def save(self, target: str | os.PathLike[str]) -> None:
        with open(os.fspath(target), "wb") as handle:
            self.write(handle)

    def write(self, target: BinaryIO) -> None:
        """Write the package, reproducing entry order, timestamps and compression."""
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for info in self._entries:
                path = normalize_part_path(info.filename)
                data = self.read(path)
                if data is None:
                    continue
                entry = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                entry.compress_type = info.compress_type
                entry.external_attr = info.external_attr
                entry.create_system = info.create_system
                archive.writestr(entry, data)


@dataclass(frozen=True)
class PackageSnapshot:
    """What :meth:`OpcPackage.snapshot` captures: edited trees as bytes, raw parts by value."""

    trees: dict[str, bytes]
    raw: dict[str, bytes | None]


def _new_entry(path: str) -> zipfile.ZipInfo:
    entry = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
    # XML compresses well; media is usually compressed already.
    if path.endswith((".xml", ".rels")):
        entry.compress_type = zipfile.ZIP_DEFLATED
    return entry


def _match_tail(node: Element) -> None:
    """Indent a new child like its siblings, for parts written pretty-printed."""
    previous = node.getprevious()
    if previous is None or not previous.tail or previous.tail.strip():
        return
    if node.getnext() is None:
        # The old last child's tail was the parent's closing indentation: it moves to the new
        # last child, and the old one takes the between-siblings indentation.
        node.tail = previous.tail
        before = previous.getprevious()
        if before is not None and before.tail and not before.tail.strip():
            previous.tail = before.tail
    else:
        node.tail = previous.tail


# -- path helpers ------------------------------------------------------------------------


def normalize_part_path(path: str) -> str:
    path = path.replace("\\", "/").lstrip("/")
    return posixpath.normpath(path) if path else path


def rels_path_for(part_path: str) -> str:
    """``dir/part.xml`` -> ``dir/_rels/part.xml.rels``."""
    directory, _, name = normalize_part_path(part_path).rpartition("/")
    return f"{directory}/_rels/{name}.rels" if directory else f"_rels/{name}.rels"


def _part_for_rels(rels_path: str) -> str:
    """Inverse of :func:`rels_path_for`; returns ``rels_path`` unchanged if it is not one."""
    directory, _, name = rels_path.rpartition("/")
    if not directory.endswith("_rels") or not name.endswith(".rels"):
        return rels_path
    owner_dir = directory[: -len("_rels")].rstrip("/")
    owner = name[: -len(".rels")]
    return f"{owner_dir}/{owner}" if owner_dir else owner


def resolve_target(base: str, target: str) -> str:
    if target.startswith("/"):
        return normalize_part_path(target)
    return normalize_part_path(posixpath.join(base, target) if base else target)
