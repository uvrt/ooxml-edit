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

from .xml import Element, parse_xml, serialize

CONTENT_TYPES_PART = "[Content_Types].xml"

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
        """Overwrite a part with raw bytes, dropping any parsed tree for it."""
        path = normalize_part_path(path)
        new = path not in self._parts
        self._parts[path] = data
        self._trees.pop(path, None)
        self._dirty.discard(path)
        self._rels_cache.pop(_part_for_rels(path), None)
        if new:
            self._entries.append(zipfile.ZipInfo(path))

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

    def snapshot(self) -> dict[str, bytes]:
        """Current bytes of every edited part.

        Only dirty parts are captured: everything else is still the original bytes and cannot
        have changed, so a snapshot costs one serialization per edited part -- tens of KB.
        """
        return {path: serialize(self._trees[path]) for path in self._dirty}

    def restore(self, snapshot: dict[str, bytes]) -> None:
        """Reset every edited part to a previous snapshot.

        Parts that were dirty then *and* now are reparsed from the snapshot; parts dirtied
        since are dropped back to their original bytes.
        """
        for path in self._dirty - set(snapshot):
            self._trees.pop(path, None)
            self._rels_cache.pop(_part_for_rels(path), None)
        self._dirty = set()
        for path, data in snapshot.items():
            self._trees[path] = parse_xml(data)
            self._dirty.add(path)
            self._rels_cache.pop(_part_for_rels(path), None)

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
