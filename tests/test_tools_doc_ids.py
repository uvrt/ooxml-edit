"""Reopening a document under the id an earlier run gave it (round 35, R23).

An application resuming a run from its saved state reopens its documents under the ids the
saved conversation names (``Session.open(data, name, doc_id="d1")``): refused while the id
is open or not of the session's form; the counter never hands out an id given this way;
closing frees an id."""

from __future__ import annotations

import threading

import pytest

from ooxml_edit.errors import ArgumentError
from ooxml_edit.tools import Limits, Toolbox, shared

import synthetic
import tools_toys


@pytest.fixture
def toolbox():
    with Toolbox(tools_toys.TOOLS + list(shared.SESSION_TOOLS), formats=tools_toys.FORMATS,
                 groups=tools_toys.GROUPS) as box:
        yield box


def _session(box):
    return box.session(limits=Limits(max_documents=16))


def _set(doc, page, text):
    return {"tool": "toy_ppt_set_title", "arguments": {"doc": doc, "page": page, "text": text}}


def test_a_resumed_run_reopens_its_documents_under_their_earlier_ids(toolbox):
    first = _session(toolbox)
    deck = first.open(synthetic.outer_package(), "deck.pptx")
    text = first.open(synthetic.outer_package(), "text.docx")
    assert (deck, text) == ("d1", "d2")
    assert toolbox.dispatch(first, "toy_ppt_set_title",
                            {"doc": "d1", "page": 1, "text": "Before"}).ok
    saved = {doc: first.entry(doc).document.to_bytes() for doc in (deck, text)}
    for doc in (deck, text):
        assert toolbox.dispatch(first, "close_document", {"doc": doc}).ok

    # The saved state brings them back in the other order, each under its own id.
    resumed = _session(toolbox)
    assert resumed.open(saved["d2"], "text.docx", doc_id="d2") == "d2"
    assert resumed.open(saved["d1"], "deck.pptx", doc_id="d1") == "d1"
    assert resumed.entry("d1").kind == "pptx" and resumed.entry("d2").kind == "docx"
    assert resumed.entry("d1").document.title(1) == "Before"

    # The saved conversation's edits name the old ids: a call, a batch, an undo.
    result = toolbox.dispatch(resumed, "toy_ppt_set_title",
                              {"doc": "d1", "page": 1, "text": "After"})
    assert result.ok, result.to_json()
    assert toolbox.dispatch(resumed, "toy_word_insert",
                            {"doc": "d2", "text": "item", "at": "end"}).ok
    batch = toolbox.dispatch(resumed, "batch", {"ops": [_set("d1", 2, "Second")]})
    assert batch.ok, batch.to_json()
    assert toolbox.dispatch(resumed, "undo", {"doc": "d1"}).ok
    assert resumed.entry("d1").document.title(1) == "After"
    assert resumed.open(synthetic.outer_package(), "more.pptx") == "d3"


def test_an_id_in_use_is_refused_naming_its_document_and_the_way_out(toolbox):
    session = _session(toolbox)
    session.open(synthetic.outer_package(), "deck.pptx")
    session.open(synthetic.outer_package(), "text.docx")
    with pytest.raises(ArgumentError) as refused:
        session.open(synthetic.outer_package(), "other.pptx", doc_id="d1")
    message = str(refused.value)
    assert "deck.pptx (pptx) is open as d1" in message
    assert "session.close('d1')" in message and "without doc_id" in message
    assert "d1, d2" in message
    assert sorted(session.documents) == ["d1", "d2"]


def test_closing_frees_an_id(toolbox):
    session = _session(toolbox)
    session.open(synthetic.outer_package(), "deck.pptx")
    session.close("d1")
    assert session.open(synthetic.outer_package(), "again.pptx", doc_id="d1") == "d1"
    assert session.entry("d1").name == "again.pptx"


@pytest.mark.parametrize("bad", ["1", "d0", "d01", "D1", "d1 ", "doc1", "d-1", "", "b1",
                                 "d1/p:A"])
def test_a_malformed_id_is_refused_with_the_accepted_form(toolbox, bad):
    session = _session(toolbox)
    with pytest.raises(ArgumentError, match=r"'d' and a whole number from 1"):
        session.open(synthetic.outer_package(), "deck.pptx", doc_id=bad)
    assert session.documents == {}


def test_a_non_string_id_is_a_type_error(toolbox):
    session = _session(toolbox)
    with pytest.raises(TypeError, match="doc_id takes a string"):
        session.open(synthetic.outer_package(), "deck.pptx", doc_id=1)


def test_the_counter_skips_ids_given_explicitly(toolbox):
    session = _session(toolbox)
    assert session.open(synthetic.outer_package(), "c.pptx", doc_id="d3") == "d3"
    assert session.open(synthetic.outer_package(), "a.pptx") == "d1"
    assert session.open(synthetic.outer_package(), "b.pptx") == "d2"
    assert session.open(synthetic.outer_package(), "d.pptx") == "d4"
    session.close("d3")
    # Closed, d3 is free for the application again -- never for the counter.
    assert session.open(synthetic.outer_package(), "e.pptx") == "d5"
    assert session.open(synthetic.outer_package(), "c.pptx", doc_id="d3") == "d3"


def test_the_counter_never_hands_out_a_closed_id_again(toolbox):
    session = _session(toolbox)
    assert session.open(synthetic.outer_package(), "a.pptx") == "d1"
    session.close("d1")
    assert session.open(synthetic.outer_package(), "b.pptx") == "d2"


def test_adopt_and_open_blob_take_an_id_too(toolbox):
    session = _session(toolbox)
    blob = session.add_blob(synthetic.outer_package(), "deck.pptx")
    assert session.open_blob(blob, doc_id="d7") == "d7"
    assert session.entry("d7").source == blob
    with pytest.raises(ArgumentError, match="is in use"):
        session.open_blob(blob, doc_id="d7")
    with pytest.raises(ArgumentError, match="not a document id"):
        session.open_blob(blob, doc_id="x")
    made = tools_toys.ToyDocument(synthetic.outer_package())
    assert session.adopt(made, "pptx", "new.pptx", doc_id="d2") == "d2"
    assert session.open_blob(blob) == "d1"
    assert session.open_blob(blob) == "d3"


def test_a_view_opens_under_an_id_for_its_session(toolbox):
    session = _session(toolbox)
    view = session.view(roles=["review"])
    assert view.open(synthetic.outer_package(), "deck.pptx", doc_id="d4") == "d4"
    assert "d4" in session.documents


def test_the_open_document_tool_takes_no_id():
    """Ids are the application's to give when it resumes; a model opens by blob alone."""
    assert shared.definition("open_document").optional_parameters() == []


def test_two_opens_of_one_id_at_once_give_it_to_one_of_them(toolbox):
    session = _session(toolbox)
    data = synthetic.outer_package()
    start = threading.Barrier(8)
    opened: list[str] = []
    refused: list[str] = []

    def open_one(number):
        start.wait(timeout=10)
        try:
            opened.append(session.open(data, f"copy{number}.pptx", doc_id="d5"))
        except ArgumentError as exc:
            refused.append(str(exc))

    threads = [threading.Thread(target=open_one, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert opened == ["d5"]
    assert len(refused) == 7 and all("is in use" in text for text in refused)
    assert list(session.documents) == ["d5"]


def test_counted_and_given_ids_opened_at_once_never_meet(toolbox):
    """A counted open may take an id before an open that gives it arrives (the
    application's race): that one is refused; no id is ever held twice."""
    session = _session(toolbox)
    data = synthetic.outer_package()
    start = threading.Barrier(12)
    given = [f"d{n}" for n in range(2, 13, 2)]          # d2, d4, ... d12
    results: list[tuple[str | None, str]] = []
    lock = threading.Lock()

    def open_one(doc_id):
        start.wait(timeout=10)
        try:
            outcome = session.open(data, "deck.pptx", doc_id=doc_id)
        except ArgumentError as exc:
            outcome = str(exc)
        with lock:
            results.append((doc_id, outcome))

    threads = [threading.Thread(target=open_one, args=(doc_id,))
               for doc_id in [*given, *[None] * 6]]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert len(results) == 12
    opened = [outcome for _, outcome in results if outcome in session.documents]
    assert len(opened) == len(set(opened)) == len(session.documents)
    counted = [outcome for doc_id, outcome in results if doc_id is None]
    assert len(counted) == 6 and all(doc in session.documents for doc in counted)
    for doc_id, outcome in results:
        if doc_id is not None and outcome != doc_id:
            assert "is in use" in outcome and doc_id in counted
