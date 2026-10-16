"""A document's own references: ``{{slide:s:262}}`` in a text argument is written as what the
call's own document's format resolves it to (``DocumentFormat.own_facts``) and kept as a
fact like a dataset value; a document whose format does not take the prefix refuses it.

The toys: a deck format resolving ``slide:`` from a made-up id list (numbered by position),
a text format with no own references, ``toy_say`` (keeps the texts it gets and the facts'
records) and ``toy_name`` (names a slide with a ref).
"""

from __future__ import annotations

import dataclasses

import pytest

from ooxml_edit.tools import Result, ToolError, Toolbox, array, obj, shared, string, tool
from ooxml_edit.tools import textfacts

import synthetic
import tools_toys

SAID: list[dict] = []
SLIDES = [256, 262, 258]


def _slide(document, name):
    if not (name.startswith("s:") and name[2:].isdigit() and int(name[2:]) in SLIDES):
        raise ToolError("not_found", f"no slide {name!r}",
                        valid_options=[f"s:{sid}" for sid in SLIDES])
    sid = int(name[2:])
    return str(SLIDES.index(sid) + 1), {"slide": sid}


DECK = dataclasses.replace(tools_toys.DECK, own_facts={"slide:": _slide})


@tool("toy_say", "Keep texts.",
      {"doc": string("Document id."), "items": array(obj({"text": string()}), "Texts.")},
      mutates=True, facts=("items[].text",))
def toy_say(call, doc, items):
    SAID.append({"items": items, "records": [f.record for f in call.facts]})
    return Result(summary="said")


@tool("toy_name", "Name a slide.", {"doc": string("Document id."), "ref": string("Name.")},
      mutates=True)
def toy_name(call, doc, ref):
    call.define_ref(ref, "s:262")
    return Result(summary="named")


@pytest.fixture(autouse=True)
def _clear():
    SAID.clear()


@pytest.fixture
def toolbox():
    with Toolbox([toy_say, toy_name, *shared.SESSION_TOOLS],
                 formats=[DECK, tools_toys.TEXT], groups=shared.GROUPS, workers=0) as box:
        yield box


@pytest.fixture
def session(toolbox):
    session = toolbox.session()
    session.open(synthetic.outer_package(), "deck.pptx")       # d1
    session.open(synthetic.outer_package(), "notes.docx")      # d2
    return session


def say(toolbox, session, text, doc="d1"):
    return toolbox.dispatch(session, "toy_say", {"doc": doc, "items": [{"text": text}]})


def test_an_own_reference_is_written_as_the_format_resolves_it(toolbox, session):
    result = say(toolbox, session, "See slide {{slide:s:262}} and {{slide:s:258}}")
    assert result.ok, result.to_json()
    assert SAID[-1]["items"][0]["text"] == "See slide 2 and 3"
    assert SAID[-1]["records"] == [{"source": "slide:s:262", "text": "2", "slide": 262},
                                   {"source": "slide:s:258", "text": "3", "slide": 258}]
    assert result.data["facts"] == [{"from": "slide:s:262", "text": "2"},
                                    {"from": "slide:s:258", "text": "3"}]


def test_a_ref_names_it_and_the_fact_keeps_the_address(toolbox, session):
    assert toolbox.dispatch(session, "toy_name", {"doc": "d1", "ref": "intro"}).ok
    result = say(toolbox, session, "Agenda: {{slide:$intro}}")
    assert result.ok, result.to_json()
    assert SAID[-1]["items"][0]["text"] == "Agenda: 2"
    assert SAID[-1]["records"][0]["source"] == "slide:s:262"
    missing = say(toolbox, session, "{{slide:$outro}}")
    assert missing.error.code == "not_found" and "$outro" in missing.error.message


def test_a_name_that_is_not_there_is_refused_by_the_format(toolbox, session):
    result = say(toolbox, session, "{{slide:s:999}}")
    assert result.error.code == "not_found"
    assert result.error.valid_options == ["s:256", "s:262", "s:258"]
    assert not SAID


def test_a_document_of_another_format_refuses_the_prefix(toolbox, session):
    result = say(toolbox, session, "See {{slide:s:262}}", doc="d2")
    assert result.error.code == "invalid_arguments"
    assert "slide: references are for pptx documents; a docx takes none" in \
        result.error.message
    assert not SAID


def test_a_format_may_refuse_a_prefix_in_its_own_words():
    text = dataclasses.replace(tools_toys.TEXT, own_facts={
        "slide:": "slide references are for decks; this has no slides"})
    with Toolbox([toy_say, *shared.SESSION_TOOLS], formats=[DECK, text],
                 groups=shared.GROUPS, workers=0) as box:
        session = box.session()
        session.open(synthetic.outer_package(), "notes.docx")
        result = say(box, session, "{{slide:s:262}}")
        assert result.error.message == ("{{slide:s:262}}: slide references are for decks; "
                                        "this has no slides. Write the text itself, or "
                                        "\\{{ for the braces")


def test_a_document_handle_or_a_number_format_is_refused(toolbox, session):
    handle = say(toolbox, session, "{{d1/slide:s:262}}")
    assert "leave the document out: {{slide:s:262}}" in handle.error.message
    formatted = say(toolbox, session, "{{slide:s:262:0}}")
    assert "takes no number format; write {{slide:s:262}}" in formatted.error.message
    assert say(toolbox, session, "{{slide:}}").ok          # names nothing: no reference
    assert SAID[-1]["items"][0]["text"] == "{{slide:}}"


def test_an_escaped_own_reference_is_its_text(toolbox, session):
    assert say(toolbox, session, r"\{{slide:s:262}}", doc="d2").ok
    assert SAID[-1]["items"][0]["text"] == "{{slide:s:262}}"
    assert SAID[-1]["records"] == []


def test_the_prefixes_are_known_and_never_qualified():
    formats = {"pptx": DECK, "docx": tools_toys.TEXT}
    assert textfacts.own_prefixes(formats) == {"pptx": ["slide:"], "docx": []}
    known = textfacts.known_prefixes(formats)
    assert "slide:" in known
    assert textfacts.qualify_text("{{slide:s:262}}", known, lambda a: "d9/" + a,
                                  ["slide:"]) == "{{slide:s:262}}"
    assert textfacts.own_prefix("d1/slide:s:1", ["slide:"]) == "slide:"
    assert textfacts.own_prefix("d:x", ["slide:"]) is None
