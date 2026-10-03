"""lxml helpers for OOXML: a namespace registry, qualified names and schema-ordered insertion.

Two things make OOXML unforgiving, and both are handled here.

**Namespace prefixes are load-bearing.**  ``mc:Ignorable="a14 p14"`` names *prefixes*, not URIs,
so a serializer that renames or garbage-collects a declaration corrupts the file.  The standard
library's ``xml.etree`` does exactly that -- it rewrites prefixes to ``ns0``/``ns1`` and drops
declarations it believes are unused, which silently invalidates ``mc:Ignorable``.  lxml
reproduces the input byte for byte apart from CRLF -> LF, which the XML specification mandates.
That is the whole reason this project depends on lxml.

**Child order is schema-enforced.**  Most OOXML content models are ``xsd:sequence``, not
``xsd:all``, and Office refuses a file that gets the order wrong -- with a repair prompt rather
than an error message that says why.  :func:`insert_in_order` places a new child at the
position its schema sequence requires.

This module is format-neutral.  It knows the packaging namespaces every OOXML format shares;
a format layer adds its own vocabulary with :func:`register_namespaces` and
:func:`register_child_order`.
"""

from __future__ import annotations

from typing import Iterable, Iterator, Mapping, Sequence, Union

from lxml import etree

Element = etree._Element

#: prefix -> namespace URI.  Seeded with the namespaces every OOXML package uses; format layers
#: extend it.  Unknown namespaces in a document are preserved regardless, since nodes are never
#: detached from their tree -- this table only governs what :func:`qn` can spell.
NAMESPACES: dict[str, str] = {
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}

#: A step in a child sequence: one tag, or a tuple of tags that share a rank -- a repeating
#: ``xsd:choice`` whose members may interleave freely, such as the runs of a paragraph.
OrderStep = Union[str, tuple[str, ...]]

#: Parent tag -> the schema's child sequence.  Only parents a format layer inserts into need an
#: entry; :func:`insert_in_order` leaves a child where it is (appended) when its parent has no
#: entry.
CHILD_ORDER: dict[str, tuple[OrderStep, ...]] = {}

_RANKS: dict[str, dict[str, int]] = {}


def register_namespaces(namespaces: Mapping[str, str]) -> None:
    """Teach :func:`qn` a format's prefixes.  Re-registering a prefix to a new URI is refused."""
    for prefix, uri in namespaces.items():
        existing = NAMESPACES.get(prefix)
        if existing is not None and existing != uri:
            raise ValueError(f"prefix {prefix!r} is already bound to {existing!r}")
        NAMESPACES[prefix] = uri


def register_child_order(orders: Mapping[str, Sequence[OrderStep]]) -> None:
    """Add or replace child sequences, keyed by prefixed parent tag."""
    for parent, steps in orders.items():
        CHILD_ORDER[parent] = tuple(steps)
        _RANKS.pop(parent, None)


def qn(tag: str) -> str:
    """``"r:id"`` -> ``"{http://...relationships}id"``.

    An unprefixed name is returned unchanged, so ``qn("val")`` works for attributes in no
    namespace (which is most of them in OOXML).
    """
    prefix, _, local = tag.partition(":")
    if not local:
        return tag
    try:
        return "{%s}%s" % (NAMESPACES[prefix], local)
    except KeyError:
        raise KeyError(f"unknown namespace prefix {prefix!r} in {tag!r}") from None


def local_name(element: Element) -> str:
    """Tag name without its namespace, e.g. ``"off"``."""
    tag = element.tag
    if not isinstance(tag, str):  # comments and processing instructions
        return ""
    return tag.rpartition("}")[2]


def parse_xml(data: bytes) -> Element:
    """Parse a part.  Whitespace is kept, because it is part of the bytes we must reproduce."""
    # resolve_entities=False: parts are attacker-controlled, and entity expansion is a
    # denial-of-service vector.  huge_tree stays off for the same reason.
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False)
    return etree.fromstring(data, parser)


def serialize(element: Element) -> bytes:
    """Serialize a part the way Office writes one: UTF-8, standalone, declaration included."""
    return etree.tostring(
        element.getroottree(),
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )


# -- reading -------------------------------------------------------------------------------


def find(parent: Element | None, path: str) -> Element | None:
    """First descendant matching a ``/``-separated prefixed path, or ``None``."""
    if parent is None:
        return None
    node: Element | None = parent
    for step in path.split("/"):
        if node is None:
            return None
        node = node.find(qn(step))
    return node


def findall(parent: Element | None, path: str) -> list[Element]:
    """Every child matching the final step of ``path`` (earlier steps must be unique)."""
    if parent is None:
        return []
    *lead, last = path.split("/")
    node: Element | None = parent
    for step in lead:
        node = node.find(qn(step)) if node is not None else None
    if node is None:
        return []
    return list(node.findall(qn(last)))


def get_int(element: Element | None, attribute: str, default: int | None = None) -> int | None:
    """Integer attribute, or ``default`` when absent or unparseable."""
    if element is None:
        return default
    raw = element.get(qn(attribute))
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def get_bool(element: Element | None, attribute: str, default: bool | None = None) -> bool | None:
    """OOXML boolean: ``1``/``true``/``on`` are true, ``0``/``false``/``off`` are false."""
    if element is None:
        return default
    raw = element.get(qn(attribute))
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "on"}


def set_int(element: Element, attribute: str, value: int | None) -> None:
    """Write an integer attribute, or remove it when ``value`` is ``None``."""
    name = qn(attribute)
    if value is None:
        element.attrib.pop(name, None)
    else:
        element.set(name, str(int(value)))


def set_attr(element: Element, attribute: str, value: str | None) -> None:
    """Write a string attribute, or remove it when ``value`` is ``None``."""
    name = qn(attribute)
    if value is None:
        element.attrib.pop(name, None)
    else:
        element.set(name, value)


# -- writing -------------------------------------------------------------------------------


def make(tag: str, **attributes: str) -> Element:
    """A new, detached element.  ``r__id="rId3"`` spells the ``r:id`` attribute."""
    element = etree.Element(qn(tag))
    for name, value in attributes.items():
        element.set(qn(name.replace("__", ":")), value)
    return element


def subelement(parent: Element, tag: str, **attributes: str) -> Element:
    """Find ``tag`` under ``parent``, creating it in schema order when missing.

    Returns the existing element untouched when present, so calling this to reach a container
    never disturbs content already there.
    """
    existing = parent.find(qn(tag))
    if existing is not None:
        return existing
    element = etree.SubElement(parent, qn(tag))
    for name, value in attributes.items():
        element.set(qn(name.replace("__", ":")), value)
    insert_in_order(parent, element)
    return element


def append_in_order(parent: Element, child: Element) -> Element:
    """Attach a detached ``child`` to ``parent`` at its schema position.  Returns ``child``."""
    parent.append(child)
    insert_in_order(parent, child)
    return child


def replace_choice(parent: Element, choices: Iterable[str], new: Element | None) -> Element | None:
    """Swap whichever member of an ``xsd:choice`` ``parent`` holds for ``new``.

    ``choices`` are the prefixed tags of the choice group.  Every existing member is removed;
    ``new`` (if any) is inserted in schema order.  This is how a fill or a geometry is replaced
    without leaving two members of a one-of group behind -- which Office reports as a repair.
    """
    wanted = {qn(tag) for tag in choices}
    for child in list(parent):
        if child.tag in wanted:
            remove(child)
    if new is not None:
        append_in_order(parent, new)
    return new


def _ranks(parent_name: str) -> dict[str, int] | None:
    ranks = _RANKS.get(parent_name)
    if ranks is not None:
        return ranks
    order = CHILD_ORDER.get(parent_name)
    if order is None:
        return None
    ranks = {}
    for rank, step in enumerate(order):
        for name in (step,) if isinstance(step, str) else step:
            ranks[name] = rank
    _RANKS[parent_name] = ranks
    return ranks


def insert_in_order(parent: Element, child: Element) -> None:
    """Move ``child`` to the position ``parent``'s schema sequence requires.

    ``child`` must already be a child of ``parent``.  Parents with no sequence entry keep the
    child where it is -- appended, which is what repeating-choice parents want.  Among siblings
    of equal rank (a repeating choice) the child lands after the last of them, so a run added
    to a paragraph goes after the existing runs but before the end-of-paragraph properties.
    """
    ranks = _ranks(prefixed_name(parent))
    if ranks is None:
        return

    rank = ranks.get(prefixed_name(child))
    if rank is None:
        return  # not in the sequence: an extension element, which belongs at the end

    unknown = len(ranks) + 1
    for sibling in parent:
        if sibling is child or not isinstance(sibling.tag, str):
            continue
        # Unknown siblings (foreign namespaces) sort last, like an extLst.
        if ranks.get(prefixed_name(sibling), unknown) > rank:
            sibling.addprevious(child)
            return
    parent.append(child)


def remove(element: Element) -> None:
    """Detach an element from its parent, keeping the tail text where it belongs."""
    parent = element.getparent()
    if parent is None:
        return
    # lxml attaches trailing whitespace to the element as .tail; dropping the element would
    # take that whitespace with it and reflow the document.
    if element.tail:
        previous = element.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + element.tail
        else:
            parent.text = (parent.text or "") + element.tail
    parent.remove(element)


def prefixed_name(element: Element) -> str:
    """``"{...relationships}id"`` -> ``"r:id"``, for lookups in :data:`CHILD_ORDER`."""
    tag = element.tag
    if not isinstance(tag, str):
        return ""
    if not tag.startswith("{"):
        return tag
    uri, _, local = tag[1:].partition("}")
    for prefix, known in NAMESPACES.items():
        if known == uri:
            return f"{prefix}:{local}"
    return local


#: Kept for callers of the earlier, private name.
_prefixed_name = prefixed_name


def iter_descendants(parent: Element, tag: str) -> Iterator[Element]:
    """Every descendant with the given prefixed tag, in document order."""
    return parent.iter(qn(tag))


def child_elements(parent: Element, tags: Sequence[str] | None = None) -> list[Element]:
    """Direct children, optionally filtered to a set of prefixed tags."""
    if tags is None:
        return [child for child in parent if isinstance(child.tag, str)]
    wanted = {qn(tag) for tag in tags}
    return [child for child in parent if child.tag in wanted]


def declared_prefixes(root: Element) -> Iterable[str]:
    """Namespace prefixes declared on the document root (diagnostics only)."""
    return (prefix for prefix in root.nsmap if prefix)
