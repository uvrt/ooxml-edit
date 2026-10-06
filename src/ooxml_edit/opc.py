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
embedded packages, thumbnails, parts for features this library has never heard of -- passes
through untouched, so an open/save round trip with no edits is byte-identical.  That is
checked by ``tests/test_opc.py``, and by every format layer across its own corpus, and it is
the property the entire editing layer rests on.
"""

from __future__ import annotations

import copy
import io
import os
import posixpath
import zipfile
from dataclasses import dataclass
from typing import BinaryIO, Callable, Iterable

from lxml import etree

from .xml import Element, parse_xml, serialize

CONTENT_TYPES_PART = "[Content_Types].xml"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
#: The declaration Office writes ``[Content_Types].xml`` with, line end included.
CONTENT_TYPES_DECLARATION = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

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
        #: parts in a different order is legal but makes every diff of a saved file useless.
        self._entries = entries
        self._parts = parts
        self._trees: dict[str, Element] = {}
        self._dirty: set[str] = set()
        self._rels_cache: dict[str, dict[str, Relationship]] = {}
        #: What was read, so undo can put back a part that was replaced or remove one that was
        #: added.  Shares the bytes objects with ``_parts``; nothing is copied.
        self._original: dict[str, bytes] = dict(parts)
        #: Parts written as raw bytes since opening (added, replaced or removed), not as
        #: trees -- in the order they were first changed, which is the order new zip entries
        #: were appended in, so redo can append them in that order again.
        self._raw_changes: dict[str, None] = {}

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

    def changed_parts(self) -> frozenset[str]:
        """Parts that differ from what was read: edited trees, and parts added, replaced or
        removed.  Empty again once every change is undone."""
        return frozenset(self._dirty) | frozenset(self._raw_changes)

    def opened(self) -> "OpcPackage":
        """The package as it was read, before any change -- a separate, read-only copy."""
        return type(self)(list(self._entries), dict(self._original))

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
        self._raw_changes[path] = None
        if new:
            self._ensure_entry(path)

    def add_part(self, path: str, data: bytes, content_type: str | None = None, *,
                 override: bool = False) -> str:
        """Add a new part, declaring its content type when one is given.

        ``content_type`` is registered as a ``Default`` for the part's extension when the
        extension has none yet -- the convention for media -- and as an ``Override`` for the
        part name when the extension's ``Default`` gives another type.  ``override=True``
        asks for an ``Override`` instead of a new ``Default``.  When the extension's
        ``Default`` already gives ``content_type`` nothing is declared, ``override`` or not:
        Word writes no ``Override`` that repeats a ``Default``.
        """
        path = normalize_part_path(path)
        if path in self._parts:
            raise ValueError(f"part {path!r} already exists")
        self.replace_part(path, data)
        if content_type is not None:
            if self._default_type(path) == content_type:
                # A stale Override for this name (a part removed and added again) would
                # still win over the Default; it goes, and nothing new is written.
                self._remove_override(path)
            else:
                self.declare_content_type(path, content_type, override=override)
        return path

    def remove_part(self, path: str) -> None:
        """Remove a part, its relationships part and its content-type ``Override``.

        Nothing checks that the part is unreferenced -- that is :meth:`reap`'s job, and the
        safe way in.  Removal is part of the undo snapshot like any raw write: the zip entry
        keeps its place, so undoing puts the part back exactly where it was.
        """
        path = normalize_part_path(path)
        if path not in self._parts:
            raise KeyError(f"no part {path!r}")
        rels = rels_path_for(path)
        if rels != path and rels in self._parts:
            self.remove_part(rels)
        del self._parts[path]
        self._trees.pop(path, None)
        self._dirty.discard(path)
        self._rels_cache.pop(_part_for_rels(path), None)
        self._rels_cache.pop(path, None)
        self._raw_changes[path] = None
        self._remove_override(path)

    def copy_part(self, source: str, share: Callable[[Relationship], bool],
                  mapping: dict[str, str] | None = None) -> str:
        """Copy ``source`` to a new part beside it, with its relationships; returns the copy.

        Each internal relationship of the original is followed: to a part already in
        ``mapping`` (old name -> new name, filled in as parts are copied, and seedable by the
        caller) it is re-pointed at the copy; where ``share(relationship)`` says the target may
        be shared, the copy relates to the same part; anything else is copied in turn, the same
        way.  Relationship ids are kept, so the copied XML needs no rewriting.  The copy is
        named like the original with the lowest free number (``item3.xml`` -> ``item7.xml``)
        and gets the original's content type.
        """
        source = normalize_part_path(source)
        mapping = {} if mapping is None else mapping
        data = self.read(source)
        if data is None:
            raise KeyError(f"no part {source!r}")
        new = self.unused_part_name(numbered_template(source))
        mapping[source] = new
        content_type = self.content_type(source)
        self.add_part(new, data, content_type, override=self._has_override(source))

        rels = rels_path_for(source)
        root = self.tree(rels)
        if root is None:
            return new
        copied = parse_xml(serialize(root))
        base = posixpath.dirname(new)
        for node, relationship in zip(_relationship_nodes(copied), self._relationship_list(source)):
            if relationship is None or relationship.is_external or relationship.target_part is None:
                continue
            target = relationship.target_part
            if target in mapping:
                new_target = mapping[target]
            elif share(relationship) or not self.has_part(target):
                continue  # same part, and the same (relative or absolute) Target still reaches it
            else:
                new_target = self.copy_part(target, share, mapping)
            node.set("Target", posixpath.relpath(new_target, base or "."))
        self.add_part(rels_path_for(new), serialize(copied), self.content_type(rels))
        return new

    # -- packages inside packages ----------------------------------------------------------

    def open_embedded(self, path: str) -> "OpcPackage":
        """A part that is itself an OPC package (an embedded object, say), opened for editing.

        The nested package is an ordinary :class:`OpcPackage` read from the part's current
        bytes, with the same losslessness: whatever is not edited inside it is written back
        as the bytes that were read.  Changes reach this package only through
        :meth:`replace_embedded`.
        """
        data = self.read(path)
        if data is None:
            raise KeyError(f"no part {normalize_part_path(path)!r}")
        return OpcPackage.open(data)

    def replace_embedded(self, path: str, package: "OpcPackage") -> bool:
        """Store an edited nested package back into its part; returns whether anything changed.

        A nested package with no edits is left alone -- not even rewritten -- so the part
        stays byte-identical.  The write is a raw part replacement, so it is part of the undo
        snapshot like any other: undoing it puts the original bytes back.
        """
        if not package.dirty_parts and not package._raw_changes:
            return False
        data = package.to_bytes()
        if data == self.read(path):
            return False
        self.replace_part(path, data)
        return True

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

    def declare_content_type(self, path: str, content_type: str, *, override: bool = False) -> None:
        """Make ``path`` resolve to ``content_type``, touching ``[Content_Types].xml`` only if
        it does not already.  ``override`` forces a per-part ``Override`` even when the
        extension's ``Default`` already says the same thing.

        A part that already has an ``Override`` keeps it, with its ``ContentType`` changed --
        a template's main part becoming a document's, say -- rather than gaining a second one
        that :meth:`content_type` would never read.  Duplicate ``Override`` elements for the
        part are removed.  An extension's ``Default`` is never changed: other parts share it.
        """
        path = normalize_part_path(path)
        if self.content_type(path) == content_type and not (override and not self._has_override(path)):
            return
        root = self.tree(CONTENT_TYPES_PART)
        if root is None:
            raise ValueError("the package has no [Content_Types].xml")
        existing = self._overrides(path)
        if existing:
            existing[0].set("ContentType", content_type)
            for node in existing[1:]:
                _remove_keeping_layout(node)
            self.mark_dirty(CONTENT_TYPES_PART)
            return
        extension = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        has_default = self._default_node(path) is not None
        if extension and not has_default and not override:
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

    def content_types_with(self, path: str, content_type: str, *, override: bool = True) -> bytes:
        """``[Content_Types].xml`` as :meth:`declare_content_type` would leave it, serialized
        as Office writes that part -- without changing the package.

        For writing a copy of the package as something else -- the main part retyped, say,
        when a template is saved under a document's name -- through :meth:`save`'s
        ``replacements``, while the open package stays what it is::

            package.save(target, {CONTENT_TYPES_PART: package.content_types_with(main, wanted)})

        ``override`` defaults to ``True`` here: a part retyped this way is one whose type
        must not depend on its extension's ``Default``.
        """
        root = self.tree(CONTENT_TYPES_PART)
        if root is None:
            raise ValueError("the package has no [Content_Types].xml")
        was_dirty = CONTENT_TYPES_PART in self._dirty
        self._trees[CONTENT_TYPES_PART] = copy.deepcopy(root)
        try:
            self.declare_content_type(path, content_type, override=override)
            edited = self._trees[CONTENT_TYPES_PART]
        finally:
            self._trees[CONTENT_TYPES_PART] = root
            if not was_dirty:
                self._dirty.discard(CONTENT_TYPES_PART)
        return CONTENT_TYPES_DECLARATION + etree.tostring(edited, encoding="UTF-8")

    def _default_node(self, path: str) -> Element | None:
        """The ``Default`` for ``path``'s extension, if any."""
        root = self.tree(CONTENT_TYPES_PART)
        if root is None or "." not in path:
            return None
        extension = path.rsplit(".", 1)[-1].lower()
        for node in root:
            if node.tag == "{%s}Default" % CONTENT_TYPES_NS \
                    and (node.get("Extension") or "").lower() == extension:
                return node
        return None

    def _default_type(self, path: str) -> str | None:
        node = self._default_node(normalize_part_path(path))
        return None if node is None else node.get("ContentType")

    def _prune_defaults(self, extensions: Iterable[str]) -> None:
        """Remove the ``Default`` of each of ``extensions`` that no part has any more."""
        root = self.tree(CONTENT_TYPES_PART)
        if root is None:
            return
        in_use = {path.rsplit(".", 1)[-1].lower() for path in self._parts if "." in path}
        unused = {extension.lower() for extension in extensions} - in_use
        nodes = [node for node in root
                 if node.tag == "{%s}Default" % CONTENT_TYPES_NS
                 and (node.get("Extension") or "").lower() in unused]
        for node in nodes:
            _remove_keeping_layout(node)
        if nodes:
            self.mark_dirty(CONTENT_TYPES_PART)

    def _overrides(self, path: str) -> list[Element]:
        root = self.tree(CONTENT_TYPES_PART)
        if root is None:
            return []
        return [node for node in root
                if node.tag == "{%s}Override" % CONTENT_TYPES_NS
                and normalize_part_path(node.get("PartName") or "") == path]

    def _has_override(self, path: str) -> bool:
        return bool(self._overrides(normalize_part_path(path)))

    def _remove_override(self, path: str) -> None:
        nodes = self._overrides(path)
        for node in nodes:
            _remove_keeping_layout(node)
        if nodes:
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
        root = self._rels_root(part_path)
        rel_id = self.next_rel_id(part_path)
        base = posixpath.dirname(part_path)
        node = etree.SubElement(root, "{%s}Relationship" % RELS_NS)
        node.set("Id", rel_id)
        node.set("Type", rel_type)
        node.set("Target", posixpath.relpath(target_part, base or "."))
        _match_tail(node)
        self.mark_dirty(rels_path_for(part_path))
        return rel_id

    def add_external_relationship(self, part_path: str, rel_type: str, target: str) -> str:
        """Relate ``part_path`` to an address outside the package (a hyperlink, say).

        An existing external relationship of the same type and address is reused.
        """
        part_path = normalize_part_path(part_path)
        for rel in self.relationships(part_path).values():
            if rel.type == rel_type and rel.is_external and rel.target == target:
                return rel.id
        root = self._rels_root(part_path)
        rel_id = self.next_rel_id(part_path)
        node = etree.SubElement(root, "{%s}Relationship" % RELS_NS)
        node.set("Id", rel_id)
        node.set("Type", rel_type)
        node.set("Target", target)
        node.set("TargetMode", "External")
        _match_tail(node)
        self.mark_dirty(rels_path_for(part_path))
        return rel_id

    def remove_relationship(self, part_path: str, rel_id: str) -> None:
        """Delete one relationship.  The target part is left alone; see :meth:`release`."""
        part_path = normalize_part_path(part_path)
        root = self.tree(rels_path_for(part_path))
        if root is None:
            raise KeyError(f"{part_path!r} has no relationships")
        for node in _relationship_nodes(root):
            if node.get("Id") == rel_id:
                _remove_keeping_layout(node)
                self.mark_dirty(rels_path_for(part_path))
                return
        raise KeyError(f"{part_path!r} has no relationship {rel_id!r}")

    def referenced_values(self, part_path: str) -> set[str]:
        """Every attribute value in a part's XML -- what a relationship id is proved unused
        against.

        Deliberately blunt.  Relationship ids are spelled in many attributes (``r:id``,
        ``r:embed``, ``r:link``, ``r:pict``, a legacy ``o:relid``...), and an id that merely
        *might* be referenced must be kept, so any attribute holding the id's exact text
        counts.  Over-counting costs at most an unreaped relationship; under-counting costs
        a repair prompt.
        """
        root = self.tree(part_path)
        if root is None:
            return set()
        return set(root.xpath("//@*"))

    def relationship_sources(self, target_part: str) -> list[tuple[str, Relationship]]:
        """``(source part, relationship)`` for every relationship in the package that targets
        ``target_part`` -- including ones declared by parts nothing else reaches."""
        target_part = normalize_part_path(target_part)
        found = []
        for owner in self._relationship_owners():
            for rel in self.relationships(owner).values():
                if rel.target_part == target_part:
                    found.append((owner, rel))
        return found

    def release(self, part_path: str, rel_ids: Iterable[str]) -> list[str]:
        """Drop relationships ``part_path`` no longer references, then :meth:`reap` their targets.

        Call it after an edit removed references -- a deleted picture, a replaced image fill.
        A relationship is removed only if its id no longer appears anywhere in the part (see
        :meth:`referenced_values`), so it is safe to pass ids that are still in use.  A
        relationships part left with no relationship is removed too, as Word does: it writes
        no empty one.  Returns the parts removed -- reaped targets, not that relationships
        part.
        """
        part_path = normalize_part_path(part_path)
        relationships = self.relationships(part_path)
        wanted = [rel_id for rel_id in dict.fromkeys(rel_ids) if rel_id in relationships]
        if not wanted:
            return []
        still_used = self.referenced_values(part_path)
        targets = []
        released = False
        for rel_id in wanted:
            if rel_id in still_used:
                continue
            rel = relationships[rel_id]
            self.remove_relationship(part_path, rel_id)
            released = True
            if rel.target_part is not None:
                targets.append(rel.target_part)
        rels = rels_path_for(part_path)
        if released and not _relationship_nodes(self.tree(rels)):
            self.remove_part(rels)
        return self.reap(targets)

    def reap(self, candidates: Iterable[str]) -> list[str]:
        """Remove the candidate parts -- and what only they lead to -- that nothing else uses.

        The proof is package-wide.  Starting from the candidates, everything they relate to is
        gathered; then any part still targeted by a relationship from *outside* that set --
        from any part in the package, the package root included, reachable or not -- is
        dropped from it, repeatedly, until it is stable.  What remains is referenced only from
        within itself (a page and its notes, which point at each other), and is removed with
        its relationships parts and content-type overrides.  An extension's ``Default`` goes
        too once no part has that extension (the last picture of a kind removed), since Word
        writes none for an extension the package does not use.  Returns the removed parts.
        """
        doomed: set[str] = set()
        pending = [normalize_part_path(path) for path in candidates]
        while pending:
            path = pending.pop()
            if path in doomed or path not in self._parts or path == CONTENT_TYPES_PART:
                continue
            doomed.add(path)
            for rel in self.relationships(path).values():
                if rel.target_part is not None:
                    pending.append(rel.target_part)
        if not doomed:
            return []

        incoming: dict[str, set[str]] = {path: set() for path in doomed}
        for owner in self._relationship_owners():
            for rel in self.relationships(owner).values():
                if rel.target_part in incoming:
                    incoming[rel.target_part].add(owner)
        changed = True
        while changed:
            changed = False
            for path in list(doomed):
                if any(source not in doomed for source in incoming[path]):
                    doomed.discard(path)
                    changed = True

        removed = sorted(doomed)
        for path in removed:
            if path in self._parts:
                self.remove_part(path)
        self._prune_defaults({path.rsplit(".", 1)[-1] for path in removed if "." in path})
        return removed

    def unreachable_parts(self) -> set[str]:
        """Parts no chain of relationships from the package root reaches (diagnostics, tests).

        Relationships parts count as reached when their owner is, and the content-types part
        always is.
        """
        reached = {""}
        pending = [""]
        while pending:
            for rel in self.relationships(pending.pop()).values():
                target = rel.target_part
                if target is not None and target not in reached and target in self._parts:
                    reached.add(target)
                    pending.append(target)
        return {
            path for path in self._parts
            if path != CONTENT_TYPES_PART and path not in reached
            and not (_part_for_rels(path) != path and _part_for_rels(path) in reached)
        }

    def _relationship_owners(self) -> list[str]:
        return [_part_for_rels(path) for path in self._parts
                if _part_for_rels(path) != path or path == "_rels/.rels"]

    def _relationship_list(self, part_path: str) -> list[Relationship | None]:
        """The part's relationships in document order, ``None`` for a malformed node."""
        root = self.tree(rels_path_for(part_path))
        relationships = self.relationships(part_path)
        return [relationships.get(node.get("Id") or "") for node in _relationship_nodes(root)]

    def _rels_root(self, part_path: str) -> Element:
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
        return root

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
        for path in list(snapshot.raw) + [p for p in self._raw_changes if p not in snapshot.raw]:
            if path in snapshot.raw:
                data = snapshot.raw[path]  # None: the part did not exist then
            else:
                data = self._original.get(path)  # changed since: back to what was read
            if data is not None:
                self._set_raw(path, data)
            else:
                self._parts.pop(path, None)
                if path not in self._original:
                    # An added part leaves no trace; a removed original keeps its entry, so
                    # putting it back restores the archive's order too.
                    self._entries = [e for e in self._entries
                                     if normalize_part_path(e.filename) != path]
            self._trees.pop(path, None)
            self._rels_cache.pop(_part_for_rels(path), None)
        self._raw_changes = dict.fromkeys(snapshot.raw)

        for path in self._dirty - set(snapshot.trees):
            self._trees.pop(path, None)
            self._rels_cache.pop(_part_for_rels(path), None)
        self._dirty = set()
        for path, data in snapshot.trees.items():
            self._trees[path] = parse_xml(data)
            self._dirty.add(path)
            self._rels_cache.pop(_part_for_rels(path), None)

    def _set_raw(self, path: str, data: bytes) -> None:
        self._ensure_entry(path)
        self._parts[path] = data

    def _ensure_entry(self, path: str) -> None:
        """A zip entry for ``path``: the one it was read with, or a new one at the end."""
        if not any(normalize_part_path(entry.filename) == path for entry in self._entries):
            self._entries.append(_new_entry(path))

    # -- saving ----------------------------------------------------------------------------

    def to_bytes(self, replacements: dict[str, bytes] | None = None) -> bytes:
        buffer = io.BytesIO()
        self.write(buffer, replacements)
        return buffer.getvalue()

    def save(self, target: str | os.PathLike[str],
             replacements: dict[str, bytes] | None = None) -> None:
        with open(os.fspath(target), "wb") as handle:
            self.write(handle, replacements)

    def write(self, target: BinaryIO, replacements: dict[str, bytes] | None = None) -> None:
        """Write the package, reproducing entry order, timestamps and compression.

        ``replacements`` (part name -> bytes) are written instead of the parts' own bytes
        without changing the package: what a format layer derives at save time.  Only
        existing parts can be replaced.
        """
        replacements = {normalize_part_path(k): v for k, v in (replacements or {}).items()}
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for info in self._entries:
                path = normalize_part_path(info.filename)
                data = replacements.get(path) if path in self._parts else None
                if data is None:
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


def _relationship_nodes(root: Element | None) -> list[Element]:
    return [] if root is None else root.findall("{%s}Relationship" % RELS_NS)


def _remove_keeping_layout(node: Element) -> None:
    """Remove a child of a pretty-printed list without leaving its indentation behind."""
    parent = node.getparent()
    if parent is None:
        return
    previous = node.getprevious()
    if node.getnext() is None and previous is not None:
        # The last child's tail is the parent's closing indentation: hand it on.
        previous.tail = node.tail
    elif previous is None and node.getnext() is not None:
        pass  # parent.text already indents the next child
    parent.remove(node)


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


def numbered_template(path: str) -> str:
    """``dir/item3.xml`` -> ``dir/item{n}.xml``: the naming pattern for a sibling copy."""
    import re

    directory, _, name = normalize_part_path(path).rpartition("/")
    stem, dot, extension = name.rpartition(".")
    if not dot:
        stem, extension = name, ""
    stem = re.sub(r"\d+$", "", stem).replace("{", "{{").replace("}", "}}")
    named = f"{stem}{{n}}" + (f".{extension}" if dot else "")
    return f"{directory}/{named}" if directory else named


def resolve_target(base: str, target: str) -> str:
    if target.startswith("/"):
        return normalize_part_path(target)
    return normalize_part_path(posixpath.join(base, target) if base else target)
