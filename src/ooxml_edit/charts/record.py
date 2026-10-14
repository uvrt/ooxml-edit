"""Where a chart's values came from, kept in the chart part itself.

When a tool places a chart's categories and series from a source (``edit_chart`` with
``from``), the provenance record (:mod:`ooxml_edit.provenance`) is also written into the
chart part, as an extension of the chart space::

    <c:chartSpace ...>
      ...
      <c:extLst>
        <c:ext uri="...">...</c:ext>          (the application's own, kept as they are)
        <c:ext uri="urn:uvrt:ooxml-edit:provenance:v1">
          <uvp:record xmlns:uvp="urn:uvrt:ooxml-edit:provenance:v1"
                      json="{&quot;v&quot;:1,&quot;source&quot;:&quot;t:sales&quot;,...}"/>
        </c:ext>
      </c:extLst>
    </c:chartSpace>

It goes wherever the chart goes -- copied to another document, its record comes along --
and the applications keep it: an own ``c:ext`` in ``c:chartSpace/c:extLst`` came back
unchanged through open, edit and save in both the presentation and the word-processing
application measured (2026-10-14), while they re-serialised the part around it.

The extension list is the chart space's last child (``CT_ChartSpace``, ECMA-376 Part 1); an
existing list is used as it is.  An existing record is replaced where it stands, so the
other extensions keep their order; a new one goes after them.  The record is the
``json`` attribute of one element in the record's own namespace -- an attribute, as the
measured extension held its data.

A :class:`~.chart.Chart` notes its part as a :class:`~ooxml_edit.provenance.Holder`
whenever it is used, and :func:`~.create.add_chart` the chart it makes, so code that places
values and watches (:func:`ooxml_edit.provenance.watch`) finds the chart a format's handler
edited without knowing the format.
"""

from __future__ import annotations

from typing import Any, Mapping

from lxml import etree

from .. import provenance as _provenance
from ..xml import Element
from .namespaces import C_NS

#: The extension's ``uri``, and the namespace of the element holding the record.
PROVENANCE_URI = "urn:uvrt:ooxml-edit:provenance:v1"
PROVENANCE_NS = PROVENANCE_URI

#: A chart part's content type.
CHART_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"

_EXT_LIST = f"{{{C_NS}}}extLst"
_EXT = f"{{{C_NS}}}ext"
_RECORD = f"{{{PROVENANCE_NS}}}record"


def _extension(root: Element) -> Element | None:
    ext_list = root.find(_EXT_LIST)
    if ext_list is None:
        return None
    return next((ext for ext in ext_list.findall(_EXT) if ext.get("uri") == PROVENANCE_URI),
                None)


def read_record(root: Element) -> dict[str, Any] | None:
    """The record in a chart space (the chart part's root), or ``None`` -- also for one
    that cannot be read, which is never an error."""
    ext = _extension(root)
    node = ext.find(_RECORD) if ext is not None else None
    text = node.get("json") if node is not None else None
    if not text:
        return None
    try:
        return _provenance.loads(text)
    except _provenance.RecordError:
        return None


def write_record(root: Element, found: Mapping[str, Any] | None) -> bool:
    """Put ``found`` in a chart space, replacing the record there; ``None`` removes it (and
    an extension list it leaves empty).  Whether anything changed."""
    ext = _extension(root)
    if found is None:
        if ext is None:
            return False
        ext_list = ext.getparent()
        ext_list.remove(ext)
        if len(ext_list) == 0:
            root.remove(ext_list)
        return True
    text = _provenance.dumps(found)
    if ext is not None:
        node = ext.find(_RECORD)
        if node is not None and node.get("json") == text and len(ext) == 1:
            return False
        for child in list(ext):
            ext.remove(child)
    else:
        ext_list = root.find(_EXT_LIST)
        if ext_list is None:
            # CT_ChartSpace ends with extLst: whatever is there comes before it.
            ext_list = etree.SubElement(root, _EXT_LIST)
        ext = etree.SubElement(ext_list, _EXT)
        ext.set("uri", PROVENANCE_URI)
    node = etree.SubElement(ext, _RECORD, nsmap={"uvp": PROVENANCE_NS})
    node.set("json", text)
    return True


def store_provenance(package: Any, part: str, found: Mapping[str, Any] | None) -> bool:
    """Write ``found`` into the chart part ``part`` of ``package`` (``None`` removes the
    record), marking it changed.  Call it inside the format's undo step.  Whether anything
    changed."""
    root = package.tree(part)
    if root is None:
        raise ValueError(f"{part} is missing")
    changed = write_record(root, found)
    if changed:
        package.mark_dirty(part)
    return changed


def holder(package: Any, part: str) -> _provenance.Holder:
    """The chart part ``part`` as a :class:`~ooxml_edit.provenance.Holder`."""
    return _provenance.Holder(
        key=(id(package), part),
        store=lambda found: store_provenance(package, part, found),
        read=lambda: _read_part(package, part))


def note(package: Any, part: str) -> None:
    """Say that the chart part ``part`` was used, for a placement watching."""
    _provenance.note(holder(package, part))


def _read_part(package: Any, part: str) -> dict[str, Any] | None:
    root = package.tree(part)
    return read_record(root) if root is not None else None


def provenance(target: Any, part: str | None = None) -> Any:
    """Where a chart's values came from, as stored in its part.

    * a :class:`~.chart.Chart` -> its record, or ``None``;
    * a package and a chart part's name -> that part's record, or ``None``;
    * a package, or a document with a ``package`` -> ``{chart part: record}`` for every
      chart part that has one.
    """
    if part is None and hasattr(target, "_resolve") and hasattr(target, "part"):
        return _read_part(target._package(), target.part)
    package = target if hasattr(target, "tree") else getattr(target, "package", None)
    if package is None or not hasattr(package, "tree"):
        raise TypeError(f"provenance takes a chart, a package or a document with a package, "
                        f"not {type(target).__name__}")
    if part is not None:
        return _read_part(package, part)
    found: dict[str, dict[str, Any]] = {}
    for name in package.part_names:
        if package.content_type(name) != CHART_CONTENT_TYPE:
            continue
        stored = _read_part(package, name)
        if stored is not None:
            found[name] = stored
    return found


__all__ = ["CHART_CONTENT_TYPE", "PROVENANCE_NS", "PROVENANCE_URI", "holder", "note",
           "provenance", "read_record", "store_provenance", "write_record"]
