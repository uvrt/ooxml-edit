"""Durable ids stamped into an OOXML extension list.

OOXML lets almost any properties element carry an ``extLst`` of ``ext`` children, each keyed by
a URI.  Office preserves extensions whose URI it does not recognise, which makes one a safe
place to freeze an id that must outlive the application renumbering things.

Format-neutral: *which* element carries the stamp, and which tags spell the extension list,
is the format layer's business -- it describes them with an :class:`ExtensionStamp`.
"""

from __future__ import annotations

from dataclasses import dataclass

from lxml import etree

from .xml import Element, qn, subelement


@dataclass(frozen=True)
class ExtensionStamp:
    """Where a stamp lives: ``<ext_list><ext uri=URI><value val="..."/></ext></ext_list>``."""

    #: Prefixed tag of the extension list, e.g. ``"x:extLst"``.
    ext_list: str
    #: Prefixed tag of one extension, e.g. ``"x:ext"``.
    ext: str
    #: The URI that identifies this stamp among the extensions.
    uri: str
    #: Prefixed tag of the element whose ``val`` holds the id.
    value: str

    def read(self, owner: Element) -> str | None:
        """The stamped id on ``owner``, if any."""
        for extension in self._extensions(owner):
            if extension.get("uri") != self.uri:
                continue
            node = extension.find(qn(self.value))
            value = node.get("val") if node is not None else None
            if value:
                return value
        return None

    def write(self, owner: Element, value: str) -> None:
        """Freeze ``value`` as ``owner``'s id.  Re-stamping the same value is a no-op."""
        if self.read(owner) == value:
            return
        ext_list = subelement(owner, self.ext_list)
        for extension in ext_list.findall(qn(self.ext)):
            if extension.get("uri") == self.uri:
                target = extension
                break
        else:
            target = etree.SubElement(ext_list, qn(self.ext))
            target.set("uri", self.uri)

        node = target.find(qn(self.value))
        if node is None:
            node = etree.SubElement(target, qn(self.value))
        node.set("val", value)

    def _extensions(self, owner: Element) -> list[Element]:
        ext_list = owner.find(qn(self.ext_list))
        return [] if ext_list is None else list(ext_list.findall(qn(self.ext)))
