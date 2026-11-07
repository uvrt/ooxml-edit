"""Round 40 (R44): a tool's name read the same in a direct call, a ``batch``'s ops and a
``read_batch``'s -- a namespace before it (``functions.``, the prefix OpenAI's APIs show a
model), and another spelling of one tool's name (another verb, a plural, the format's
prefix added or left out) -- and a name that is no tool naming the nearest real ones.
Made-up tools and calls only."""

from __future__ import annotations

import pytest

from ooxml_edit.tools import REVIEW, Toolbox, shared
from ooxml_edit.tools.hints import tool_alias

from test_tools_hints import HINT_TOOLS, HINTED, _session
from test_tools_shared import CHECKS, DECK, DECK_TOOLS, TEXT_TOOLS, _ops


@pytest.fixture
def toolbox():
    CHECKS.clear()
    with Toolbox(TEXT_TOOLS + DECK_TOOLS + HINT_TOOLS + [shared.READ_BATCH],
                 formats=[DECK, HINTED], groups=shared.GROUPS) as box:
        yield box


@pytest.fixture
def session(toolbox):
    return _session(toolbox)


@pytest.mark.parametrize("name, meant", [
    ("t_insert_picture", "t_add_picture"),       # another verb
    ("t_add_pictures", "t_add_picture"),         # a plural
    ("add_picture", "t_add_picture"),            # the format's prefix left out
    ("t_save_document", "save_document"),        # the format's prefix added
    ("t_reads", "t_read"),
    ("read", "t_read"),
])
def test_another_spelling_of_one_tools_name(toolbox, name, meant):
    assert tool_alias(name, toolbox.tools, toolbox.tool_hints) == meant


@pytest.mark.parametrize("name", [
    "t_delete_box",         # the format's table: a task another tool does, never an alias
    "t_remove_boxes",       # the same entry, spelt otherwise
    "t_add_chart",          # an action of a tool: a hint only
    "save_document_now",    # a tool's name is its start: a hint only
    "functions.t_read",     # a namespace is read before (Toolbox.tool_named)
    "", None, 3,
])
def test_what_is_no_alias(toolbox, name):
    assert tool_alias(name, toolbox.tools, toolbox.tool_hints) is None


def test_two_tools_saying_the_same_are_no_alias():
    tools = dict.fromkeys(["t_add_note", "t_insert_note", "t_read", "t_set"])
    assert tool_alias("t_create_note", tools) is None
    assert tool_alias("t_read_all", tools) is None


def test_a_direct_call_reads_another_spelling_as_the_tool(toolbox, session):
    for name in ("t_insert_picture", "functions.t_insert_picture", "functions.add_picture"):
        result = toolbox.dispatch(session, name, {"doc": "d1"})
        assert result.ok and result.tool == "t_add_picture", result.to_json()
        assert result.warnings == [f"tool {name!r} read as 't_add_picture'"]


@pytest.mark.parametrize("atomic", [False, True])
def test_a_batch_reads_its_ops_names_as_a_direct_call_does(toolbox, session, atomic):
    result = toolbox.dispatch(session, "batch", {"atomic": atomic, "ops": _ops(
        ("functions.t_add_picture", {"doc": "d1"}),
        ("t_insert_picture", {"doc": "d1"}),
        ("functions.t_add_pictures", {"doc": "d1"}))})
    assert result.ok, result.to_json()
    assert [op["tool"] for op in result.data["ops"]] == ["t_add_picture"] * 3
    assert result.warnings == [
        "ops[0]: tool 'functions.t_add_picture' read as 't_add_picture'",
        "ops[1]: tool 't_insert_picture' read as 't_add_picture'",
        "ops[2]: tool 'functions.t_add_pictures' read as 't_add_picture'"]


def test_a_read_batch_reads_its_ops_names_as_a_direct_call_does(toolbox, session):
    result = toolbox.dispatch(session, "read_batch", {"ops": _ops(
        ("functions.t_read", {"doc": "d1"}), ("t_reads", {"doc": "d1"}))})
    assert result.ok, result.to_json()
    ops = result.data["ops"]
    assert [op["tool"] for op in ops] == ["t_read", "t_read"]
    assert ops[0]["warnings"] == ["tool 'functions.t_read' read as 't_read'"]
    assert ops[1]["warnings"] == ["tool 't_reads' read as 't_read'"]
    assert "error" not in ops[0] and "error" not in ops[1]


def test_an_atomic_batchs_refusal_names_the_tool_an_op_was_read_as(toolbox, session):
    result = toolbox.dispatch(session, "batch", {"atomic": True, "ops": _ops(
        ("functions.t_insert_picture", {"doc": "d1", "nope": 1}))})
    assert result.error.field == "ops[0].arguments.nope"
    assert result.error.message.startswith(
        "ops[0] (functions.t_insert_picture, read as t_add_picture): unknown argument 'nope'")


def test_a_name_that_is_no_tool_names_the_nearest(toolbox, session):
    result = toolbox.dispatch(session, "functions.t_ad", {"doc": "d1"})
    assert result.error.message == "no tool 'functions.t_ad'; the nearest: t_add, t_read, t_half"
    assert result.error.valid_options[0] == "t_add"
    result = toolbox.dispatch(session, "batch", {"ops": _ops(("t_ad", {"doc": "d1"}))})
    assert result.error.details["ops"][0]["error"]["message"] == (
        "no tool 't_ad'; the nearest: t_add, t_read, t_half")


def test_a_hint_batch_cannot_run_is_to_be_called_alone(toolbox, session):
    result = toolbox.dispatch(session, "batch", {"atomic": True, "ops": _ops(
        ("save_document_now", {"doc": "d1"}))})
    assert result.error.message == ("ops[0]: no tool 'save_document_now': use save_document on "
                                    "its own (batch cannot run it)")


def test_another_spelling_keeps_the_tools_roles(toolbox):
    reviewing = _session(toolbox, [REVIEW])
    result = toolbox.dispatch(reviewing, "t_insert_picture", {"doc": "d1"})
    assert not result.ok and result.error.code == "refused"
    assert "t_add_picture" in result.error.message


def test_the_applications_tool_names_are_never_read_as_others(toolbox):
    # system_prompt's tools are the application's own names: a namespace is read, another
    # spelling is not (it may be a tool the application runs itself).
    assert toolbox.tool_named("functions.t_read", aliases=False) == "t_read"
    assert toolbox.tool_named("t_reads", aliases=False) == "t_reads"
    assert toolbox.tool_named("t_reads") == "t_read"
    assert toolbox.tool_named("app_send_mail") == "app_send_mail"
