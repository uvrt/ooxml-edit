"""A document's state kept beside its bytes (round 38): ``Session.save`` gives both at one
version, a save tool's output carries the state, ``Session.open(..., state=...)`` hands it
back to the format, which refuses one that is not these bytes'."""

from __future__ import annotations

import dataclasses
import hashlib

import pytest

from ooxml_edit.errors import ArgumentError
from ooxml_edit.history import History
from ooxml_edit.tools import Limits, SavedDocument, StateRefused, Toolbox, shared

import synthetic
import tools_toys


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _state(document) -> str:
    """The toy text format's state: its version and the digest of its bytes."""
    return f"{document.history.version}:{document.history.last_version}:{_digest(document.to_bytes())}"


def _open_with_state(data: bytes, state: str):
    version, last, digest = state.split(":")
    if digest != _digest(data):
        raise ArgumentError("this state is of other bytes: pass the state saved with them, or none")
    document = tools_toys.ToyDocument(data)
    document.history.continue_from(int(version), int(last))
    return document


KEEPING = dataclasses.replace(tools_toys.TEXT, state=_state, open_with_state=_open_with_state)


@pytest.fixture
def toolbox():
    with Toolbox(tools_toys.TOOLS + list(shared.SESSION_TOOLS), formats=[tools_toys.DECK, KEEPING],
                 groups=tools_toys.GROUPS) as box:
        yield box


def _session(box):
    return box.session(limits=Limits(max_documents=16))


def test_save_gives_bytes_and_state_of_one_version_and_open_takes_them_back(toolbox):
    first = _session(toolbox)
    doc = first.open(synthetic.outer_package(), "text.docx")
    for k in range(3):
        assert toolbox.dispatch(first, "toy_word_insert", {"doc": doc, "text": f"item {k}", "at": "end"}).ok
    assert toolbox.dispatch(first, "undo", {"doc": doc}).ok
    saved = first.save(doc)
    assert isinstance(saved, SavedDocument)
    assert (saved.doc, saved.name, saved.kind, saved.version) == (doc, "text.docx", "docx", 2)
    assert saved.data == first.entry(doc).document.to_bytes()
    assert saved.state == "2:3:" + _digest(saved.data)
    assert "state" not in repr(saved)
    first.close(doc)

    later = _session(toolbox)
    assert later.open(saved.data, saved.name, doc_id=saved.doc, state=saved.state) == doc
    assert later.entry(doc).version == 2
    result = toolbox.dispatch(later, "toy_word_insert", {"doc": doc, "text": "next", "at": "end"})
    assert result.ok and result.version == 4


def test_a_save_tool_s_output_carries_the_state_the_model_never_sees(toolbox):
    session = _session(toolbox)
    doc = session.open(synthetic.outer_package(), "text.docx")
    assert toolbox.dispatch(session, "toy_word_insert", {"doc": doc, "text": "item", "at": "end"}).ok
    result = toolbox.dispatch(session, "toy_save", {"doc": doc, "name": "out.docx", "format": "docx"})
    assert result.ok, result.to_json()
    assert "state" not in str(result.to_json())
    output = session.take_outputs()[-1]
    assert output.state == "1:1:" + _digest(output.data)
    assert "state" not in output.describe()


def test_a_format_keeping_no_state_gives_none_and_refuses_one(toolbox):
    session = _session(toolbox)
    deck = session.open(synthetic.outer_package(), "deck.pptx")
    saved = session.save(deck)
    assert saved.state is None and saved.data
    assert toolbox.dispatch(session, "toy_save", {"doc": deck, "name": "d.pptx", "format": "pptx"}).ok
    assert session.take_outputs()[-1].state is None
    with pytest.raises(StateRefused, match="keeps no state beside its bytes: open it without state"):
        session.open(saved.data, "again.pptx", state="1:1:x")
    assert session.open(saved.data, "again.pptx", state="") == "d2"


def test_a_state_of_other_bytes_is_refused_and_nothing_is_opened(toolbox):
    session = _session(toolbox)
    doc = session.open(synthetic.outer_package(), "text.docx")
    old = session.save(doc)
    assert toolbox.dispatch(session, "toy_word_insert", {"doc": doc, "text": "item", "at": "end"}).ok
    new = session.save(doc)
    session.close(doc)
    with pytest.raises(StateRefused, match="of other bytes") as refused:
        session.open(new.data, "text.docx", doc_id=doc, state=old.state)
    assert isinstance(refused.value, ArgumentError)
    assert doc not in session.documents
    handle = session.add_blob(new.data, "text.docx")
    with pytest.raises(StateRefused):
        session.open_blob(handle, doc_id=doc, state=old.state)
    assert session.open_blob(handle, doc_id=doc, state=new.state) == doc


def test_continue_from_numbers_after_the_earlier_history():
    document = tools_toys.ToyDocument(synthetic.outer_package())
    history = document.history
    history.continue_from(5, 9)
    assert (history.version, history.last_version) == (5, 9)
    document.insert("item", "end")
    assert history.version == 10
    assert document.undo() and history.version == 5
    with pytest.raises(ValueError, match="before its first step"):
        history.continue_from(1)
    fresh = History(tools_toys.ToyDocument(synthetic.outer_package()).package)
    for version, last in ((-1, None), (3, 2), (True, None), ("2", None)):
        with pytest.raises(ValueError):
            fresh.continue_from(version, last)
    fresh.continue_from(4)
    assert (fresh.version, fresh.last_version) == (4, 4)
