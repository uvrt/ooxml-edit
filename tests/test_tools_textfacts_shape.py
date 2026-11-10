"""Braces shaped like a reference that names no source (round 50), and a required argument a
batch's op leaves out (``replace_text``'s ``expect``).

``{{x/#1/name}}`` -- a reference without its ``d:`` -- was written as text, braces and all.  It
is refused now, naming the reference meant and how braces are written as text (``\\{{``); braces
around anything else (a template's ``{{name}}``) stay text, as before.  The toys and the made-up
grid are ``test_tools_textfacts``'s.
"""

from __future__ import annotations

import pytest

from ooxml_edit.tools import Toolbox, shared, textfacts

import synthetic
import tools_toys
from test_tools_textfacts import CLOCK, GRID, SAID, TOOLS, _session, failed, ok, say  # noqa: F401

KNOWN = ["d:", "t:"]


@pytest.fixture(autouse=True)
def _clear():
    SAID.clear()


@pytest.fixture
def toolbox():
    with Toolbox(TOOLS, formats=[*tools_toys.FORMATS, GRID], groups=shared.GROUPS, workers=0) as box:
        yield box


@pytest.mark.parametrize("inside, meant", [
    ("by_item/#1/rating", ["d:by_item/#1/rating"]),                 # the prefix left out
    ("x/*/rating", ["d:x/*/rating"]),
    ("by_item/N-1/rating", ["d:by_item/N-1/rating"]),               # a dataset's id first
    ("d2/#1/rating", ["d2/t:<table>/#1/rating", "d:<dataset>/#1/rating"]),   # a document's handle
    ("D:by_item/#1/rating", ["d:by_item/#1/rating"]),               # other capitals, spacing
    ("d : by_item", ["d:by_item"]),
    ("ds:by_item/#1/rating", ["d:by_item/#1/rating"]),              # a word for a dataset
    ("table:checks/#1/rating", ["t:checks/#1/rating"]),
    ("src:x/#1/y", ["d:x/#1/y", "t:x/#1/y"]),                       # some prefix, then a path
])
def test_braces_shaped_like_a_reference_name_the_one_meant(inside, meant):
    assert textfacts.misread(inside, KNOWN, ["by_item"]) == meant


@pytest.mark.parametrize("inside", [
    "name", " first_name ", "client.name", "TODO: see a/b", "and/or", "1/2", "http://example.com/a",
    "d:", "Note: x", "TODO: a/b", "Note:x/y", "see: by_item/#1/rating", "d:by_item/#1/rating", "t:checks/#1/rating", "'x/#1'",
])
def test_other_braces_are_no_reference(inside):
    assert textfacts.misread(inside, KNOWN, ["by_item"]) == []


def test_a_reference_without_its_prefix_is_refused_with_the_one_meant_and_the_escape(toolbox):
    session = _session(toolbox)
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    error = failed(say(toolbox, session, "Rated {{by_item/#1/rating}} here"), "invalid_arguments")
    assert "did you mean {{d:by_item/#1/rating}}?" in error.message
    assert "put a backslash before them: \\{{by_item/#1/rating}}" in error.message
    assert error.field == "items[0].text" and error.valid_options[0] == "d:by_item/#1/rating"
    assert SAID == []                                                   # nothing written
    handle = failed(say(toolbox, session, "{{d2/#1/rating}}"), "invalid_arguments")
    assert "{{d2/t:<table>/#1/rating}} or {{d:<dataset>/#1/rating}}" in handle.message
    assert "d:by_item" in handle.valid_options


def test_the_escape_and_a_templates_braces_still_write_text(toolbox):
    session = _session(toolbox)
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    result = ok(say(toolbox, session, "Dear {{name}}, {{ client.name }} \\{{by_item/#1/rating}} "
                                      "{{TODO: see a/b}} {{d:by_item/#1/item}}"))
    assert SAID[-1]["items"] == [{"text": "Dear {{name}}, {{ client.name }} {{by_item/#1/rating}} "
                                          "{{TODO: see a/b}} N-1"}]
    assert result.warnings == []


def test_a_dataset_defined_earlier_in_the_batch_is_the_one_meant(toolbox):
    session = _session(toolbox)
    result = toolbox.dispatch(session, "batch", {"ops": [
        {"tool": "ds_keep", "arguments": {"id": "fresh"}},
        {"tool": "toy_say", "arguments": {"doc": "d1", "items": [{"text": "{{fresh/N-1/rating}}"}]}}]})
    assert "did you mean {{d:fresh/N-1/rating}}?" in str(result.to_json())


# -- replace_text's expect, left out ---------------------------------------------------------------


@pytest.fixture
def words():
    with Toolbox(tools_toys.TOOLS + [shared.handler("replace_text", kind="pptx")(_replace), *shared.SESSION_TOOLS],
                 formats=tools_toys.FORMATS, groups=tools_toys.GROUPS, workers=0) as box:
        session = box.session()
        doc = session.open(synthetic.outer_package(), "deck.pptx")
        yield box, session, doc


SEEN: list = []


def _replace(call, doc, find, replace, expect, slides=None, stories=None, regex=False):
    SEEN.append(expect)
    return {"summary": f"replaced ({expect})"}


def test_expect_left_out_is_one_in_a_call_and_in_a_batch(words):
    box, session, doc = words
    SEEN.clear()
    alone = box.dispatch(session, "replace_text", {"doc": doc, "find": "a", "replace": "b"})
    batched = box.dispatch(session, "batch", {"ops": [
        {"tool": "replace_text", "arguments": {"doc": doc, "find": "a", "replace": "b"}},
        {"tool": "replace_text", "arguments": {"doc": doc, "find": "a", "replace": "b", "expect": "all"}}]})
    assert alone.ok and alone.warnings == ["expect not given: read as 'one'"]
    assert batched.ok and batched.warnings == ["ops[0]: expect not given: read as 'one'"]
    assert SEEN == ["one", "one", "all"]


def test_expect_stays_required_in_the_definition():
    spec = shared.definition("replace_text")
    assert "expect" in spec.schema["required"] and spec.defaults == {"expect": "one"}
