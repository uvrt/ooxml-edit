"""SmartArt: node text, and adding and removing nodes, with the cached drawing in step.

The synthetic data model (``charts_synthetic.py``) is a bullet list: two parents, each with
children, one of them an assistant, and a block list of three nodes beside it.  A parent's
text has a drawing shape of its own; its children's texts are paragraphs of one shared
shape, in ``destOrd`` order -- which is what the drawing update has to get exactly right.
"""

from __future__ import annotations

import re

import pytest
from lxml import etree

import charts_synthetic as cs
from ooxml_edit.charts import DiagramDrawingError, diagram_parts

A = "{%s}" % cs.A
DSP = "{%s}" % cs.DSP
DATA = cs.package([])
TEXTS = ["Goals", "Faster edits", "Fewer prompts", "Risks", "Stale caches", "Helper"]


def opened(**options) -> cs.Opened:
    return cs.Opened(DATA, **options)


def drawing_texts(document: cs.Opened, name: str = "bullets") -> list[list[str]]:
    """Each cached drawing shape's paragraphs, in drawing order."""
    part = document.diagram(name).drawing_part
    root = etree.fromstring(document.package.read(part))
    return [["".join(t.text or "" for t in p.iter(A + "t")) for p in body.findall(A + "p")]
            for body in root.iter(DSP + "txBody")]


def gates(document: cs.Opened, original: bytes = DATA) -> bytes:
    """Save, check validity, undo to the original bytes and redo to the edited ones."""
    edited = document.to_bytes()
    cs.assert_valid(edited)
    document.undo_all()
    assert cs.parts(document.to_bytes()) == cs.parts(original)
    document.redo_all()
    assert document.to_bytes() == edited
    return edited


def test_reading():
    document = opened()
    diagram = document.diagram("bullets")
    assert diagram.texts == TEXTS
    assert [(n.level, n.text) for n in diagram.nodes] == [
        (0, "Goals"), (1, "Faster edits"), (1, "Fewer prompts"), (0, "Risks"),
        (1, "Stale caches"), (1, "Helper")]
    assert diagram.part == cs.DATA and diagram.drawing_part == cs.DRAWING
    assert diagram.layout == "urn:test/layout/vList"
    assert diagram.nodes[1].parent.text == "Goals" and diagram.nodes[0].parent is None
    assert [c.text for c in diagram.nodes[0].children] == ["Faster edits", "Fewer prompts"]
    assert [d.text for d in diagram.nodes[3].descendants] == ["Stale caches", "Helper"]
    assert diagram.node(cs.HELPER).text == "Helper"
    assert diagram.model == {"layout": "urn:test/layout/vList", "nodes": [
        {"id": node.id, "lvl": node.level, "t": node.text} for node in diagram.nodes]}
    assert document.diagram("blocks").texts == ["Plan", "Build", "Ship"]


def test_the_hosts_parts():
    document = opened()
    assert diagram_parts(document.host("bullets")) == {
        "data": cs.DATA, "layout": "doc/diagrams/layout1.xml",
        "style": "doc/diagrams/quickStyle1.xml", "colors": "doc/diagrams/colors1.xml"}
    assert diagram_parts(document.host("blocks"))["data"] == cs.FLAT_DATA


def test_a_node_and_its_drawing_shape_change_together():
    document = opened()
    diagram = document.diagram("bullets")
    diagram.set_text(3, "Risks we take")
    assert drawing_texts(document) == [["Goals"], ["Faster edits", "Fewer prompts"],
                                       ["Risks we take"], ["Stale caches", "Helper"]]
    edited = gates(document)
    reopened = cs.Opened(edited).diagram("bullets")
    assert reopened.texts[3] == "Risks we take" and reopened.drawing_part is not None
    # The run kept its formatting in both places.
    assert b'<a:rPr lang="en-US" sz="6500" b="1"/><a:t>Risks we take</a:t>' in \
        cs.parts(edited)[cs.DRAWING]
    assert b'<a:rPr lang="en-US" b="1"/><a:t>Risks we take</a:t>' in cs.parts(edited)[cs.DATA]


def test_a_childs_paragraph_is_found_in_the_shape_it_shares():
    document = opened()
    diagram = document.diagram("bullets")
    diagram.node(2).text = "Far fewer prompts"
    diagram.node(5).text = "Assistant"           # the assistant, second in its shape
    diagram.node(0).text = "Goals\nfor now"      # two paragraphs where there was one
    assert drawing_texts(document) == [["Goals", "for now"], ["Faster edits",
                                       "Far fewer prompts"], ["Risks"],
                                       ["Stale caches", "Assistant"]]
    edited = gates(document)
    assert cs.Opened(edited).diagram("bullets").texts == [
        "Goals\nfor now", "Faster edits", "Far fewer prompts", "Risks", "Stale caches",
        "Assistant"]


def test_a_placeholder_stops_being_one_once_it_has_text():
    document = opened()
    root = document.package.tree(cs.DATA)
    point = next(p for p in root.iter("{%s}pt" % cs.DGM) if p.get("modelId") == cs.GOALS)
    point.find("{%s}prSet" % cs.DGM).set("phldr", "1")
    document.package.mark_dirty(cs.DATA)
    document.diagram("bullets").set_text(0, "Aims")
    assert b'<dgm:pt modelId="%s"><dgm:prSet phldrT="[Text]"/>' % cs.GOALS.encode() in \
        document.package.read(cs.DATA)


def _stale(document: cs.Opened) -> bytes:
    """The package with the drawing saying something the data model does not."""
    root = document.package.tree(cs.DRAWING)
    next(t for t in root.iter(A + "t") if t.text == "Helper").text = "Stale"
    document.package.mark_dirty(cs.DRAWING)
    return document.to_bytes()


def test_a_drawing_that_already_disagrees_is_dropped():
    """If the cache does not say what the data model says, it cannot be patched exactly --
    by default it goes, relationship, part and dataModelExt together."""
    original = _stale(opened())
    document = cs.Opened(original)
    heard: list[str] = []
    diagram = document.diagram("bullets", notify=heard.append)
    diagram.set_text(cs.STALE, "Fresh caches")
    assert diagram.drawing_part is None
    assert heard == ["bullets: the cached drawing was dropped; the application lays the "
                     "diagram out again"]
    edited = gates(document, original)
    assert cs.DRAWING not in cs.parts(edited)
    assert b"dataModelExt" not in cs.parts(edited)[cs.DATA]
    assert b"drawing1.xml" not in cs.parts(edited)[cs.MAIN_RELS]
    assert cs.FLAT_DRAWING in cs.parts(edited)  # the other diagram's drawing is untouched


def test_a_host_may_keep_a_drawing_it_cannot_patch():
    original = _stale(opened())
    document = cs.Opened(original)
    heard: list[str] = []
    diagram = document.diagram("bullets", on_inexact_drawing="keep", notify=heard.append)
    diagram.set_text(cs.STALE, "Fresh caches")
    assert diagram.texts[4] == "Fresh caches"
    assert diagram.drawing_part == cs.DRAWING
    assert cs.parts(document.to_bytes())[cs.DRAWING] == cs.parts(original)[cs.DRAWING]
    assert heard == ["bullets: the cached drawing was kept, and no longer shows what the "
                     "data model says"]
    gates(document, original)


def test_a_host_may_refuse_an_edit_it_cannot_keep_the_drawing_in_step_with():
    original = _stale(opened())
    document = cs.Opened(original)
    diagram = document.diagram("bullets", on_inexact_drawing="refuse")
    with pytest.raises(DiagramDrawingError, match="bullets: the edit cannot keep"):
        diagram.set_text(cs.STALE, "Fresh caches")
    with pytest.raises(DiagramDrawingError):
        diagram.add_node("More")
    assert document.to_bytes() == original and not document.history.can_undo()
    diagram.set_text(cs.GOALS, "Aims")  # a drawing kept exactly in step is no refusal
    assert drawing_texts(document)[0] == ["Aims"]


def test_an_unknown_policy_is_refused():
    with pytest.raises(ValueError, match="drop, keep, refuse"):
        opened().diagram("bullets", on_inexact_drawing="ignore")


def test_adding_a_node_drops_the_drawing():
    document = opened()
    diagram = document.diagram("blocks")
    added = diagram.add_node("Measure", index=1)
    assert diagram.texts == ["Plan", "Measure", "Build", "Ship"]
    assert added.level == 0 and diagram.drawing_part is None
    child = diagram.node(0).add_child("Scope")
    assert [(n.level, n.text) for n in diagram.nodes][:2] == [(0, "Plan"), (1, "Scope")]
    assert child.parent.text == "Plan"
    edited = gates(document)
    data = cs.parts(edited)[cs.FLAT_DATA].decode()
    orders = [int(v) for v in re.findall(
        r'<dgm:cxn modelId="[^"]+" srcId="\{5B000000-0000-4000-8000-000000000001\}" '
        r'destId="[^"]+" srcOrd="(\d+)"', data)]  # parent-of connections: no type
    assert sorted(orders) == [0, 1, 2, 3]
    assert cs.FLAT_DRAWING not in cs.parts(edited)
    assert cs.DRAWING in cs.parts(edited)  # the other diagram's is untouched


def test_the_same_additions_give_the_same_bytes():
    """New points and connections get ids made from the edit (uuid5), not random ones."""
    outputs = []
    for _ in range(2):
        document = opened()
        diagram = document.diagram("blocks")
        diagram.add_node("Measure", index=1)
        diagram.add_node("Measure", index=1)
        outputs.append(cs.parts(gates(document))[cs.FLAT_DATA])
    assert outputs[0] == outputs[1]
    ids = re.findall(r'modelId="([^"]+)"', outputs[0].decode())
    assert len(ids) == len(set(ids))


def test_a_node_added_beside_an_assistant_is_one():
    document = opened(lang="en-GB")
    diagram = document.diagram("bullets")
    added = diagram.add_node("Deputy", parent=cs.RISKS, index=1)
    assert [c.text for c in diagram.node(cs.RISKS).children] == [
        "Stale caches", "Deputy", "Helper"]
    data = document.package.read(cs.DATA).decode()
    assert re.search(rf'<dgm:pt modelId="{re.escape(added.id)}" type="asst">', data)
    # Transitions are new text the package writes: in the host's language.
    assert data.count('<a:endParaRPr lang="en-GB"/>') == 2
    gates(document)


def test_removing_a_node_takes_its_children_and_presentation_points():
    document = opened()
    diagram = document.diagram("bullets")
    goals = diagram.node(0)
    assert goals.id in re.findall(r'presAssocID="([^"]+)"',
                                  document.package.read(diagram.part).decode())
    diagram.remove_node(goals)
    assert diagram.texts == ["Risks", "Stale caches", "Helper"]
    data = document.package.read(diagram.part).decode()
    for gone in (cs.GOALS, cs.FASTER, cs.FEWER, cs.GOALS_PRES, "Faster edits",
                 "Fewer prompts"):
        assert gone not in data
    assert re.search(rf'srcId="{re.escape(cs.DOC)}" destId="{re.escape(cs.RISKS)}" '
                     r'srcOrd="0"', data)
    edited = gates(document)
    assert cs.Opened(edited).diagram("bullets").texts == ["Risks", "Stale caches", "Helper"]


def test_the_last_node_stays():
    diagram = opened().diagram("blocks")
    diagram.remove_node(0)
    diagram.remove_node(0)
    with pytest.raises(ValueError, match="blocks: a diagram keeps at least one node"):
        diagram.remove_node(0)


def test_unchanged_text_changes_nothing():
    document = opened()
    document.diagram("bullets").set_text(0, "Goals")
    assert not document.history.can_undo() and not document.package.dirty_parts


def test_nodes_are_found_by_position_or_id():
    diagram = opened().diagram("bullets")
    assert diagram.node(-1).text == "Helper"
    with pytest.raises(IndexError, match="bullets: no node 6"):
        diagram.node(6)
    with pytest.raises(KeyError, match="no node"):
        diagram.node("{nobody}")
    with pytest.raises(KeyError, match="no node"):
        diagram.add_node("x", parent="{nobody}")
    with pytest.raises(IndexError):
        diagram.add_node("x", index=5)
