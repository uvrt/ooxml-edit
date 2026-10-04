"""SmartArt: the text of a diagram's nodes, and adding or removing nodes.

A SmartArt diagram is stored as data -- a tree of points (``dgm:pt``) wired by connections
(``dgm:cxn``) in the data-model part -- plus a laid-out copy the application caches beside it
(``dsp:drawing``, related from the slide through ``dsp:dataModelExt@relId``).  The data
model is the truth.  Measured on PowerPoint for Mac 16 (see ROADMAP.md, Phase E4):

* PowerPoint **re-lays the diagram out from the data model** whenever it opens a deck.  A
  cached drawing whose text disagrees with the data model is ignored -- the PDF shows the
  data model's text -- and a missing drawing is regenerated, on screen and on save.
* So is a node added to the data model with no presentation points of its own, and a node
  removed with its presentation points: PowerPoint lays the diagram out with the node, or
  without it, and writes the presentation points back on save.

Every other reader (pptx2svg, Keynote, LibreOffice, Google Slides) draws the *cached*
drawing.  So a text edit updates the data model and, where it can be done exactly, the
drawing too: the drawing shape that shows a node is found through the node's ``presOf``
connection (data point -> presentation point, whose ``modelId`` the drawing's ``dsp:sp``
carries), and its paragraphs are only rewritten when they are, verifiably, the texts of
the nodes that shape presents, in their ``destOrd`` order.  Anything less certain -- and
every added or removed node, which moves the layout -- drops the cached drawing instead,
which PowerPoint regenerates and other readers show as an empty frame until it has.
"""

from __future__ import annotations

import copy
import uuid
from typing import TYPE_CHECKING, Callable

from ..oxml.xml import Element, append_in_order, local_name, make, qn, register_child_order, remove
from .text import _paragraph_text, _replace_body_text

if TYPE_CHECKING:  # pragma: no cover
    from .document import Shape

DGM_NS = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
DSP_NS = "http://schemas.microsoft.com/office/drawing/2008/diagram"
#: The cached drawing's relationship type, both spellings (Microsoft's and ISO's).
REL_DIAGRAM_DRAWING = (
    "http://schemas.microsoft.com/office/2007/relationships/diagramDrawing",
    "http://purl.oclc.org/ooxml/officeDocument/relationships/diagramDrawing",
)
#: ``a:ext`` URI under which the data model names its drawing.
DATA_MODEL_EXT_URI = "http://schemas.microsoft.com/office/drawing/2008/diagram"

register_child_order({
    "dgm:pt": ("dgm:prSet", "dgm:spPr", "dgm:t", "dgm:extLst"),
    "dgm:t": ("a:bodyPr", "a:lstStyle", "a:p"),
    "dsp:txBody": ("a:bodyPr", "a:lstStyle", "a:p"),
    "dgm:dataModel": ("dgm:ptLst", "dgm:cxnLst", "dgm:bg", "dgm:whole", "dgm:extLst"),
})

#: Point types that carry the diagram's text.  A ``dgm:pt`` without a type is a node.
TEXT_TYPES = frozenset({"node", "asst"})
_PARENT_OF = frozenset({None, "parOf"})


def _type(point: Element) -> str:
    return point.get("type") or "node"


def _text(point: Element) -> str:
    body = point.find(qn("dgm:t"))
    if body is None:
        return ""
    return "\n".join(_paragraph_text(p) for p in body.findall(qn("a:p")))


def _ord(cxn: Element, attribute: str = "srcOrd") -> int:
    try:
        return int(cxn.get(attribute, "0"))
    except ValueError:
        return 0


def new_model_id() -> str:
    return "{%s}" % str(uuid.uuid4()).upper()


class _Model:
    """The data-model part, indexed."""

    def __init__(self, root: Element) -> None:
        self.root = root
        self.points_list = root.find(qn("dgm:ptLst"))
        self.cxn_list = root.find(qn("dgm:cxnLst"))
        points = [] if self.points_list is None else self.points_list.findall(qn("dgm:pt"))
        self.points = {p.get("modelId"): p for p in points}
        self.cxns = [] if self.cxn_list is None else self.cxn_list.findall(qn("dgm:cxn"))

    @property
    def document(self) -> Element | None:
        return next((p for p in self.points.values() if p.get("type") == "doc"), None)

    def children(self, model_id: str) -> list[tuple[Element, Element]]:
        """``(connection, child point)`` under ``model_id``, in order."""
        found = []
        for cxn in self.cxns:
            if cxn.get("type") in _PARENT_OF and cxn.get("srcId") == model_id:
                child = self.points.get(cxn.get("destId"))
                if child is not None and _type(child) in TEXT_TYPES:
                    found.append((cxn, child))
        return sorted(found, key=lambda item: _ord(item[0]))

    def walk(self) -> list[tuple[Element, int, str | None]]:
        """``(point, level, parent id)`` for every node, depth first in order."""
        document = self.document
        result: list[tuple[Element, int, str | None]] = []
        if document is None:
            return result
        seen: set[str] = set()

        def visit(model_id: str, level: int, parent: str | None) -> None:
            for _, child in self.children(model_id):
                child_id = child.get("modelId")
                if child_id in seen:
                    continue
                seen.add(child_id)
                result.append((child, level, parent))
                visit(child_id, level + 1, child_id)

        visit(document.get("modelId"), 0, None)
        return result

    def connection_to(self, model_id: str) -> Element | None:
        return next((c for c in self.cxns if c.get("type") in _PARENT_OF
                     and c.get("destId") == model_id), None)

    def drawing_rel_id(self) -> str | None:
        extensions = self.root.find(qn("dgm:extLst"))
        for extension in [] if extensions is None else extensions.findall(qn("a:ext")):
            node = extension.find(qn("dsp:dataModelExt"))
            if node is not None and node.get("relId"):
                return node.get("relId")
        return None

    def forget_drawing(self) -> None:
        extensions = self.root.find(qn("dgm:extLst"))
        if extensions is None:
            return
        for extension in extensions.findall(qn("a:ext")):
            if extension.find(qn("dsp:dataModelExt")) is not None:
                remove(extension)
        if len(extensions) == 0:
            remove(extensions)


class Diagram:
    """The SmartArt in a graphic frame.  Re-resolved from the document on every call.

    Nodes are listed depth first, in order (``diagram.nodes[0]`` is the first top-level
    node), and addressed by their ``modelId``, which never changes.
    """

    def __init__(self, resolve: Callable[[], "Shape"]) -> None:
        self._resolve = resolve

    # -- where it lives ----------------------------------------------------------------------

    @property
    def address(self) -> str:
        return self._resolve().id

    def _relationship(self, attribute: str) -> str | None:
        shape = self._resolve()
        node = shape._element.find(
            f"{qn('a:graphic')}/{qn('a:graphicData')}/{qn('dgm:relIds')}")
        if node is None:
            return None
        return shape._slide.document.package.related_part(shape._slide.part_path,
                                                          node.get(qn(attribute)))

    @property
    def part(self) -> str:
        """The data-model part, ``ppt/diagrams/data1.xml``."""
        part = self._relationship("r:dm")
        if part is None:
            raise ValueError(f"{self.address}: the diagram's data model is missing")
        return part

    @property
    def layout(self) -> str | None:
        """The layout definition's ``uniqueId``, e.g. ``...layout/vList2``."""
        part = self._relationship("r:lo")
        root = None if part is None else self._package().tree(part)
        return None if root is None else root.get("uniqueId")

    @property
    def drawing_part(self) -> str | None:
        """The cached drawing, ``ppt/diagrams/drawing1.xml``, if there is one."""
        shape = self._resolve()
        rel_id = _Model(self._root()).drawing_rel_id()
        if rel_id is None:
            return None
        rel = shape._slide.document.package.relationships(shape._slide.part_path).get(rel_id)
        if rel is None or rel.type not in REL_DIAGRAM_DRAWING or rel.target_part is None:
            return None
        return rel.target_part if self._package().has_part(rel.target_part) else None

    def _package(self):
        return self._resolve()._slide.document.package

    def _root(self) -> Element:
        root = self._package().tree(self.part)
        if root is None:
            raise ValueError(f"{self.part} is missing")
        return root

    # -- reading -----------------------------------------------------------------------------

    @property
    def nodes(self) -> list["DiagramNode"]:
        return [DiagramNode(self, point.get("modelId"))
                for point, _, _ in _Model(self._root()).walk()]

    def node(self, which: "int | str") -> "DiagramNode":
        """A node by position (depth first) or by ``modelId``."""
        nodes = self.nodes
        if isinstance(which, str):
            for node in nodes:
                if node.id == which:
                    return node
            raise KeyError(f"{self.address}: no node {which!r}")
        if not -len(nodes) <= which < len(nodes):
            raise IndexError(f"{self.address}: no node {which} (have {len(nodes)})")
        return nodes[which]

    @property
    def texts(self) -> list[str]:
        return [_text(point) for point, _, _ in _Model(self._root()).walk()]

    @property
    def model(self) -> dict:
        """``{"layout", "nodes": [{"id", "lvl", "t"}]}`` -- what the full-state SVG carries."""
        return diagram_model(self._root(), self.layout)

    # -- editing -----------------------------------------------------------------------------

    def set_text(self, which: "int | str | DiagramNode", text: str) -> "Diagram":
        """A node's text (``"\\n"`` between paragraphs), keeping its formatting -- in the
        data model, and in the cached drawing where that is exact (else the drawing goes)."""
        node = which if isinstance(which, DiagramNode) else self.node(which)
        text = str(text)
        if node.text == text:
            return self
        with self._edit() as model:
            point = model.points[node.id]
            old = {key: _text(p) for key, p in model.points.items()}
            body = point.find(qn("dgm:t"))
            if body is None:
                body = make("dgm:t")
                body.append(make("a:bodyPr"))
                body.append(make("a:lstStyle"))
                paragraph = make("a:p")
                paragraph.append(make("a:endParaRPr", lang="en-US"))
                body.append(paragraph)
                append_in_order(point, body)
            _replace_body_text(body, text)
            properties = point.find(qn("dgm:prSet"))
            if properties is not None and text and properties.get("phldr") is not None:
                del properties.attrib["phldr"]  # no longer a placeholder showing its prompt
            if not self._update_drawing(model, node.id, old, text):
                self._drop_drawing(model)
        return self

    def add_node(self, text: str = "", *, parent: "DiagramNode | str | None" = None,
                 index: int | None = None) -> "DiagramNode":
        """A new node under ``parent`` (default: the top level) at ``index`` among its
        siblings (default: last), formatted like a sibling.  The cached drawing is dropped;
        PowerPoint lays the diagram out again with the node in it."""
        with self._edit() as model:
            document = model.document
            if document is None:
                raise ValueError(f"{self.address}: the data model has no document point")
            parent_id = document.get("modelId") if parent is None else (
                parent.id if isinstance(parent, DiagramNode) else parent)
            if parent_id not in model.points:
                raise KeyError(f"{self.address}: no node {parent_id!r}")
            siblings = model.children(parent_id)
            position = len(siblings) if index is None else index
            if not 0 <= position <= len(siblings):
                raise IndexError(f"index {index} out of range 0..{len(siblings)}")
            template = siblings[min(position, len(siblings) - 1)][1] if siblings else None
            node_id, par_id, sib_id, cxn_id = (new_model_id() for _ in range(4))
            node = _node_point(node_id, template, text)
            parent_transition = _transition(par_id, "parTrans", cxn_id)
            sibling_transition = _transition(sib_id, "sibTrans", cxn_id)
            anchor = next((p for p in model.points_list.findall(qn("dgm:pt"))
                           if p.get("type") == "pres"), None)
            for point in (node, parent_transition, sibling_transition):
                if anchor is not None:
                    anchor.addprevious(point)
                else:
                    model.points_list.append(point)
            for cxn, _ in siblings:
                if _ord(cxn) >= position:
                    cxn.set("srcOrd", str(_ord(cxn) + 1))
            connection = make("dgm:cxn", modelId=cxn_id, srcId=parent_id, destId=node_id,
                              srcOrd=str(position), destOrd="0", parTransId=par_id,
                              sibTransId=sib_id)
            if model.cxn_list is None:
                model.cxn_list = make("dgm:cxnLst")
                append_in_order(model.root, model.cxn_list)
            first_presentation = next((c for c in model.cxn_list.findall(qn("dgm:cxn"))
                                       if c.get("type") not in _PARENT_OF), None)
            if first_presentation is not None:
                first_presentation.addprevious(connection)
            else:
                model.cxn_list.append(connection)
            self._drop_drawing(model)
        return DiagramNode(self, node_id)

    def remove_node(self, which: "int | str | DiagramNode") -> "Diagram":
        """Delete a node and everything under it, with their presentation points.  The
        cached drawing is dropped; PowerPoint lays the diagram out again without them."""
        node = which if isinstance(which, DiagramNode) else self.node(which)
        if len(self.nodes) <= 1 + len(node.descendants):
            raise ValueError(f"{self.address}: a diagram keeps at least one node")
        with self._edit() as model:
            doomed: set[str] = set()
            pending = [node.id]
            while pending:
                current = pending.pop()
                doomed.add(current)
                connection = model.connection_to(current)
                if connection is not None:
                    doomed.update(filter(None, (connection.get("parTransId"),
                                                connection.get("sibTransId"))))
                pending.extend(child.get("modelId") for _, child in model.children(current))
            # Presentation points of anything going, and what only they lead to.
            for point in list(model.points.values()):
                properties = point.find(qn("dgm:prSet"))
                if point.get("type") == "pres" and properties is not None \
                        and properties.get("presAssocID") in doomed:
                    doomed.add(point.get("modelId"))
            own = model.connection_to(node.id)
            parent_id, gone_order = (own.get("srcId"), _ord(own)) if own is not None else (None, -1)
            for cxn in list(model.cxns):
                if cxn.get("srcId") in doomed or cxn.get("destId") in doomed:
                    remove(cxn)
            for model_id in doomed:
                point = model.points.get(model_id)
                if point is not None:
                    remove(point)
            if parent_id is not None:
                for cxn in _Model(model.root).cxns:
                    if cxn.get("type") in _PARENT_OF and cxn.get("srcId") == parent_id \
                            and _ord(cxn) > gone_order:
                        cxn.set("srcOrd", str(_ord(cxn) - 1))
            self._drop_drawing(model)
        return self

    # -- internals ---------------------------------------------------------------------------

    def _edit(self):
        from contextlib import contextmanager

        @contextmanager
        def editing():
            shape = self._resolve()
            document = shape._slide.document
            part = self.part
            with document.batch():
                document.history.checkpoint()
                model = _Model(document.package.tree(part))
                model.shape = shape  # type: ignore[attr-defined]
                yield model
                document.package.mark_dirty(part)

        return editing()

    def _drawing_root(self, model: _Model) -> tuple[str | None, Element | None]:
        shape = model.shape  # type: ignore[attr-defined]
        rel_id = model.drawing_rel_id()
        package = shape._slide.document.package
        rel = None if rel_id is None else package.relationships(shape._slide.part_path).get(rel_id)
        if rel is None or rel.target_part is None or not package.has_part(rel.target_part):
            return None, None
        return rel.target_part, package.tree(rel.target_part)

    def _update_drawing(self, model: _Model, node_id: str, old: dict[str, str],
                        text: str) -> bool:
        """Rewrite the node's paragraphs in the cached drawing; ``False`` when that cannot
        be done exactly (the caller then drops the drawing)."""
        part, root = self._drawing_root(model)
        if root is None:
            return model.drawing_rel_id() is None  # nothing cached: nothing to keep in step
        shapes = {sp.get("modelId"): sp for sp in root.iter(qn("dsp:sp"))}
        targets = [c for c in model.cxns if c.get("type") == "presOf"
                   and c.get("srcId") == node_id]
        if not targets:
            return False
        edits = []
        for target in targets:
            shape = shapes.get(target.get("destId"))
            body = None if shape is None else shape.find(qn("dsp:txBody"))
            if body is None:
                return False
            presented = sorted((c for c in model.cxns if c.get("type") == "presOf"
                                and c.get("destId") == target.get("destId")),
                               key=lambda c: _ord(c, "destOrd"))
            paragraphs = body.findall(qn("a:p"))
            expected: list[str] = []
            start = None
            for cxn in presented:
                source = cxn.get("srcId")
                lines = old.get(source, "").split("\n")
                if source == node_id:
                    start = len(expected)
                    count = len(lines)
                expected.extend(lines)
            if start is None or [_paragraph_text(p) for p in paragraphs] != expected:
                return False
            edits.append((paragraphs[start:start + count]))
        package = model.shape._slide.document.package  # type: ignore[attr-defined]
        for paragraphs in edits:
            holder = make("dsp:txBody")
            for paragraph in paragraphs:
                holder.append(copy.deepcopy(paragraph))
            _replace_body_text(holder, text)
            anchor = paragraphs[0]
            for paragraph in list(holder):
                if local_name(paragraph) == "p":
                    paragraph.tail = anchor.tail
                    anchor.addprevious(paragraph)
            for paragraph in paragraphs:
                remove(paragraph)
        package.mark_dirty(part)
        return True

    def _drop_drawing(self, model: _Model) -> None:
        """Forget the cached drawing: PowerPoint lays the diagram out again (measured)."""
        shape = model.shape  # type: ignore[attr-defined]
        rel_id = model.drawing_rel_id()
        if rel_id is None:
            return
        model.forget_drawing()
        package = shape._slide.document.package
        if rel_id in package.relationships(shape._slide.part_path):
            package.release(shape._slide.part_path, [rel_id])


class DiagramNode:
    """One node of a :class:`Diagram`, by ``modelId``."""

    def __init__(self, diagram: Diagram, model_id: str) -> None:
        self.diagram = diagram
        self.id = model_id

    def _point(self) -> Element:
        point = _Model(self.diagram._root()).points.get(self.id)
        if point is None:
            raise KeyError(f"{self.diagram.address}: node {self.id} is gone")
        return point

    @property
    def text(self) -> str:
        return _text(self._point())

    @text.setter
    def text(self, value: str) -> None:
        self.diagram.set_text(self, value)

    def set_text(self, value: str) -> "DiagramNode":
        self.diagram.set_text(self, value)
        return self

    @property
    def level(self) -> int:
        for point, level, _ in _Model(self.diagram._root()).walk():
            if point.get("modelId") == self.id:
                return level
        raise KeyError(self.id)

    @property
    def parent(self) -> "DiagramNode | None":
        for point, _, parent in _Model(self.diagram._root()).walk():
            if point.get("modelId") == self.id:
                return None if parent is None else DiagramNode(self.diagram, parent)
        raise KeyError(self.id)

    @property
    def children(self) -> list["DiagramNode"]:
        model = _Model(self.diagram._root())
        return [DiagramNode(self.diagram, child.get("modelId"))
                for _, child in model.children(self.id)]

    @property
    def descendants(self) -> list["DiagramNode"]:
        found = []
        for child in self.children:
            found.append(child)
            found.extend(child.descendants)
        return found

    def add_child(self, text: str = "", *, index: int | None = None) -> "DiagramNode":
        return self.diagram.add_node(text, parent=self, index=index)

    def remove(self) -> None:
        self.diagram.remove_node(self)

    def __repr__(self) -> str:
        return f"<DiagramNode {self.id} {self.text!r}>"


def diagram_model(root: Element, layout: str | None = None) -> dict:
    """``{"layout": ..., "nodes": [{"id": "{...}", "lvl": 0, "t": "Plan"}]}``."""
    model = {"nodes": [{"id": point.get("modelId"), "lvl": level, "t": _text(point)}
                       for point, level, _ in _Model(root).walk()]}
    if layout is not None:
        model = {"layout": layout, **model}
    return model


def _node_point(model_id: str, template: Element | None, text: str) -> Element:
    point = make("dgm:pt", modelId=model_id)
    if template is not None and template.get("type") == "asst":
        point.set("type", "asst")
    point.append(make("dgm:prSet"))
    point.append(make("dgm:spPr"))
    body = None
    if template is not None and template.find(qn("dgm:t")) is not None:
        body = copy.deepcopy(template.find(qn("dgm:t")))
        for paragraph in body.findall(qn("a:p"))[1:]:
            remove(paragraph)
    if body is None:
        body = make("dgm:t")
        body.append(make("a:bodyPr"))
        body.append(make("a:lstStyle"))
        paragraph = make("a:p")
        paragraph.append(make("a:endParaRPr", lang="en-US"))
        body.append(paragraph)
    point.append(body)
    _replace_body_text(body, text)
    return point


def _transition(model_id: str, kind: str, cxn_id: str) -> Element:
    point = make("dgm:pt", modelId=model_id, type=kind, cxnId=cxn_id)
    point.append(make("dgm:prSet"))
    point.append(make("dgm:spPr"))
    body = make("dgm:t")
    body.append(make("a:bodyPr"))
    body.append(make("a:lstStyle"))
    paragraph = make("a:p")
    paragraph.append(make("a:endParaRPr", lang="en-US"))
    body.append(paragraph)
    point.append(body)
    return point


__all__ = ["Diagram", "DiagramNode", "diagram_model"]
