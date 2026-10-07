"""Where a chart or a diagram lives: the one thing a document format has to tell this package.

A chart part and a diagram's data model are the same DrawingML whichever document embeds
them.  What differs is the holder -- the element that points at them, the part whose
relationships resolve that pointer, and how an edit becomes one undo step -- and a
:class:`GraphicHost` is exactly that, built by the format layer each time a chart or a
diagram is resolved::

    host = GraphicHost(
        package=package,                     # an ooxml_edit.opc.OpcPackage
        part="doc/main.xml",                 # the part whose relationships name the chart
        frame=frame_element,                 # c:chart or dgm:relIds is somewhere under it
        edit=history.batch,                  # one undo step; rolls back on an exception
        address="chart 1",                   # how messages name it
        application="the editor",            # "...'s Edit Data will show the old values"
    )
    chart = Chart(lambda: host)

Resolve it afresh on every call, as the format layer would resolve a shape, so a
:class:`~.chart.Chart` survives undo.

**A new chart** is the same split.  :func:`~.create.add_chart` writes the chart part and its
workbook and relates them to ``part``, inside the format's undo step; the format then puts
:meth:`~.create.NewChart.graphic` in a frame of its own, and resolves a host for that frame
as for any other chart::

    with history.batch():
        made = add_chart(package, "doc/main.xml", "column", ["Q1", "Q2"],
                         [{"name": "North", "values": [12.4, 13.1]}], title="Revenue")
        frame.append(made.graphic())         # the format's frame, wherever it goes
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, ContextManager

from ..xml import Element, qn
from . import namespaces as _namespaces  # noqa: F401  (registers c:, dgm: before qn is used)

#: ``dgm:relIds`` attributes, by what they name.
DIAGRAM_RELATIONSHIPS = {"data": "r:dm", "layout": "r:lo", "style": "r:qs", "colors": "r:cs"}


@dataclass(frozen=True)
class GraphicHost:
    """The holder of a chart or a diagram, as the document format sees it."""

    #: The package the chart or diagram is in (an :class:`~ooxml_edit.opc.OpcPackage`).
    package: Any
    #: The part whose relationships resolve the frame's ``r:id``/``r:dm`` attributes, and
    #: which holds a diagram's cached-drawing relationship.
    part: str
    #: The element under which ``c:chart`` or ``dgm:relIds`` is found, at any depth.
    frame: Element
    #: Returns a context manager that makes everything inside it one undo step, and rolls it
    #: back if it raises (``History.batch`` does both).
    edit: Callable[[], ContextManager[Any]]
    #: How messages name the chart or diagram.
    address: str
    #: The application whose Edit Data opens the chart's workbook, for messages.
    application: str = "the application"
    #: What the format calls the whole document, for messages ("linked from outside the ...").
    document: str = "document"
    #: ``lang`` for the run and paragraph properties of text this package creates -- the
    #: end-of-paragraph properties of a new diagram node and of a new title's paragraph,
    #: which the title's text then takes its run's properties from; ``None`` writes none.
    lang: str | None = None
    #: Builds the ``c:tx`` of a new chart or axis title, given whether the title is vertical;
    #: ``None`` uses :func:`~.chart.default_title_text`.  The text is then written into it
    #: by :func:`~.dmltext.replace_body_text`, so a run with no text to inherit from takes
    #: its formatting from the paragraph's ``a:endParaRPr``, if the template gives one.
    title_template: Callable[[bool], Element] | None = None


def _find(frame: Element, tag: str) -> Element | None:
    return next(frame.iter(qn(tag)), None)


def chart_part(host: GraphicHost) -> str | None:
    """The chart part the host's frame points at, or ``None``."""
    node = _find(host.frame, "c:chart")
    if node is None:
        return None
    return host.package.related_part(host.part, node.get(qn("r:id")))


def diagram_parts(host: GraphicHost) -> dict[str, str | None]:
    """``{"data", "layout", "style", "colors"}`` -> the parts a diagram's ``dgm:relIds``
    names (``None`` for any that is missing), or ``{}`` when the frame holds no diagram."""
    node = _find(host.frame, "dgm:relIds")
    if node is None:
        return {}
    return {key: host.package.related_part(host.part, node.get(qn(attribute)))
            for key, attribute in DIAGRAM_RELATIONSHIPS.items()}


def has_chart(frame: Element) -> bool:
    return _find(frame, "c:chart") is not None


def has_diagram(frame: Element) -> bool:
    return _find(frame, "dgm:relIds") is not None


__all__ = ["DIAGRAM_RELATIONSHIPS", "GraphicHost", "chart_part", "diagram_parts", "has_chart",
           "has_diagram"]
