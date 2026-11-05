"""``check`` and ``render`` scoped to blocks, and copies compared with their reference
(round 35, R18 and R19): the shared definitions docx-agent's handlers serve.

``range`` (both tools) and ``copies`` (``check``) are a document's parameters alone: a deck's
or a workbook's toolbox is not sent them, and a handler that does not take them refuses one
given, naming it.  ``range`` takes one string as a list of one (``orSingle``, never sent).
``check`` locks every document a copy's reference is in.  ``render(range=...)`` runs in a
``read_batch`` as alone.  Made-up documents and toy handlers only."""

from __future__ import annotations

import json

import pytest

from ooxml_edit.tools import Result, Toolbox, shared
from ooxml_edit.tools.registry import array, merge_tools, string
from ooxml_edit.tools.schema import CallError, subset_problems, validate_call

import synthetic
from test_tools_shared import CLOCK, DECK, DECK_TOOLS, TEXT, TEXT_TOOLS, _ops

SEEN: list = []


@shared.handler("check", kind="docx")
def text_check(call, doc, pages=None, include=None, min_size=None, styles=None, range=None,
               copies=None):
    SEEN.append({"range": range, "copies": copies, "locked": sorted(call.entries)})
    return Result(summary="Checked", data={"range": range, "copies": copies})


@shared.handler("render", kind="docx")
def text_render(call, doc, slides=None, pages=None, width=None, sheet=None, range=None):
    from tools_toys import png

    SEEN.append({"range": range})
    call.check_images(1)
    call.image(png(200, 282), 200, 282, label="page 1")
    return Result(summary="Rendered page(s) 1", data={"pages": [1], "range": range})


@shared.handler("render", kind="pptx")
def deck_render(call, doc, slides=None, pages=None, width=None, sheet=None):
    return Result(summary="Rendered")


@shared.handler("check", kind="pptx")
def deck_check(call, doc, slides=None, pages=None, include=None, boxes=None, spacing=None,
               contrast=None):
    return Result(summary="Checked")


def _box():
    SEEN.clear()
    return Toolbox([*TEXT_TOOLS, *DECK_TOOLS, text_check, text_render, deck_render, deck_check,
                    shared.READ_BATCH], formats=[DECK, TEXT], workers=0)


def _session(box):
    session = box.session(clock=CLOCK)
    session.open(synthetic.outer_package(), "report.docx")    # d1
    session.open(synthetic.outer_package(), "sample.docx")    # d2
    session.open(synthetic.outer_package(), "slides.pptx")    # d3
    return session


def _sent(name, *kinds):
    tools = [shared.handler(name, kind=kind)(lambda call, **given: None) for kind in kinds]
    return merge_tools(tools)[name].canonical["properties"]


def test_range_and_copies_are_a_documents_parameters_alone():
    assert {"range", "copies"} <= set(_sent("check", "docx"))
    assert "range" in _sent("render", "docx")
    for kind in ("pptx", "xlsx"):
        assert not {"range", "copies"} & set(_sent("check", kind)), kind
        assert "range" not in _sent("render", kind), kind
    copies = _sent("check", "docx")["copies"]["items"]
    assert copies["required"] == ["blocks", "reference"]
    assert copies["properties"]["reference"]["required"] == ["block"]


def test_range_s_one_string_is_never_sent_and_costs_nothing():
    for name in ("check", "render"):
        tool = shared.definition(name)
        assert tool.schema["properties"]["range"]["orSingle"] is True
        assert "orSingle" not in json.dumps(tool.canonical)
        assert subset_problems(tool.canonical) == []
    with _box() as box:
        for provider in ("anthropic", "openai-responses", "openai-chat"):
            assert "orSingle" not in json.dumps(box.definitions(provider))


def test_one_string_is_read_as_a_list_of_one_and_anything_else_is_refused():
    schema = {"type": "object", "properties": {
        "range": array(string(), "Blocks.", or_single=True).schema}}
    assert validate_call(schema, {"range": "A..B"}) == {"range": ["A..B"]}
    assert validate_call(schema, {"range": ["A", "B"]}) == {"range": ["A", "B"]}
    with pytest.raises(CallError, match="must be an array"):
        validate_call(schema, {"range": 3})
    problems = subset_problems({"type": "string", "orSingle": True}, allow_bounds=True)
    assert any("orSingle must be true, on an array" in problem for problem in problems)


def test_check_and_render_take_a_range_said_as_one_string():
    with _box() as box:
        session = _session(box)
        checked = box.dispatch(session, "check", {"doc": "d1", "range": "p:1A..p:2B"})
        assert checked.ok and checked.data["range"] == ["p:1A..p:2B"]
        rendered = box.dispatch(session, "render", {"doc": "d1", "range": ["s:2"]})
        assert rendered.ok and rendered.data["range"] == ["s:2"]


def test_check_locks_the_documents_a_copy_s_reference_is_in():
    with _box() as box:
        session = _session(box)
        copies = [{"blocks": "p:1..p:4", "reference": {"doc": "d2", "block": "p:9..p:C"}},
                  {"blocks": "p:5", "reference": {"block": "p:1"}}]
        assert box.tools["check"].doc_ids({"doc": "d1", "copies": copies}) == ["d1", "d2"]
        result = box.dispatch(session, "check", {"doc": "d1", "copies": copies})
        assert result.ok and result.doc == "d1"
        assert SEEN[-1]["locked"] == ["d1", "d2"] and SEEN[-1]["copies"] == copies
        unknown = box.dispatch(session, "check", {"doc": "d1", "copies": [
            {"blocks": "p:5", "reference": {"doc": "d9", "block": "p:1"}}]})
        assert unknown.error.code == "not_found"


def test_a_handler_that_does_not_take_them_refuses_them_naming_the_way_out():
    with _box() as box:
        session = _session(box)
        render = box.dispatch(session, "render", {"doc": "d3", "slides": ["1"],
                                                  "range": ["p:1"]})
        assert render.error.code == "invalid_arguments" and render.error.field == "range"
        assert "leave it out" in render.error.message and "slides" in render.error.message
        check = box.dispatch(session, "check", {"doc": "d3", "copies": [
            {"blocks": "p:1", "reference": {"block": "p:2"}}]})
        assert check.error.field == "copies"
        # left out, as before: a deck's handlers never see them
        assert box.dispatch(session, "render", {"doc": "d3", "slides": ["1"]}).ok


def test_render_of_a_range_runs_in_a_read_batch_with_a_check_of_copies():
    with _box() as box:
        session = _session(box)
        result = box.dispatch(session, "read_batch", {"ops": _ops(
            ("render", {"doc": "d1", "range": "p:1..p:4"}),
            ("check", {"doc": "d1", "range": ["p:1..p:4"], "copies": [
                {"blocks": "p:1..p:4", "reference": {"doc": "d2", "block": "p:1..p:4"}}]}))})
        assert result.ok and [a.get("error") for a in result.data["ops"]] == [None, None]
        assert result.data["ops"][0]["data"]["range"] == ["p:1..p:4"]
        assert [image.caption for image in result.images] == ["ops[0] d1 page 1"]
        assert SEEN[-1]["locked"] == ["d1", "d2"]
