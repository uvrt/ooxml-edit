"""Round 9: a batch op's ``comment`` and ``note`` (N4), and what a name that is no tool
most likely means -- a tool, and an action of it (N5).  Made-up tools and calls only."""

from __future__ import annotations

import dataclasses

import pytest

from ooxml_edit.tools import ACCEPT, REVIEW, Result, Toolbox, shared, string, tool
from ooxml_edit.tools.hints import Hint, action_hints, singular, tool_hints

import synthetic
from test_tools_roles import t_changes, t_comments
from test_tools_shared import CHECKS, CLOCK, DECK, DECK_TOOLS, TEXT, TEXT_TOOLS, _ops


def _acting(name, actions, description, **options):
    @tool(name, description, {"doc": string("Document id."),
                              "action": string("What to do.", enum=actions)},
          kind="docx", mutates=True, **options)
    def run(call, doc, action):
        return Result(summary=f"{name} {action}")
    return run


edit_chart = _acting("edit_chart", ["read", "add", "set_title"], "Read, add or retitle a chart.",
                     action_roles={"read": REVIEW})
t_pages = _acting("t_pages", ["duplicate", "move", "delete"], "Duplicate, move or delete a page.")
t_edit_grid = _acting("t_edit_grid", ["insert_row", "delete_row", "merge"], "Change a grid.")
t_arrange = _acting("t_arrange", ["group", "align", "delete"], "Group, align or delete boxes.")


@tool("t_add_picture", "Add a picture.", {"doc": string("Document id.")}, kind="docx",
      mutates=True)
def t_add_picture(call, doc):
    return Result(summary="picture added")


HINT_TOOLS = [edit_chart, t_pages, t_edit_grid, t_arrange, t_add_picture, t_comments, t_changes]
HINTED = dataclasses.replace(TEXT, tool_hints={"t_delete_box": "t_arrange delete",
                                               "t_zz_box": "t_no_such_tool",
                                               "t_drop_box": "t_arrange no_such_action"})


def _toolbox(**options):
    CHECKS.clear()
    return Toolbox(TEXT_TOOLS + DECK_TOOLS + HINT_TOOLS, formats=[DECK, HINTED],
                   groups=shared.GROUPS, **options)


@pytest.fixture
def toolbox():
    with _toolbox() as box:
        yield box


def _session(toolbox, roles=None):
    session = toolbox.session(clock=CLOCK, **({"roles": roles} if roles else {}))
    session.open(synthetic.outer_package(), "text.docx")      # d1
    return session


@pytest.fixture
def session(toolbox):
    return _session(toolbox)


# -- N5: the tool and action a name means ----------------------------------------------------------


def test_an_unknown_tool_names_the_tool_and_action_that_does_it(toolbox, session):
    result = toolbox.dispatch(session, "t_add_chart", {"doc": "d1"})
    assert result.error.code == "invalid_arguments"
    assert result.error.message == "no tool 't_add_chart': use edit_chart with action add"
    assert result.error.valid_options[0] == "edit_chart"
    assert len(result.error.valid_options) <= 5      # the best few, not every tool


def test_inside_a_batch_too_naming_the_op(toolbox, session):
    ops = _ops(("t_add", {"doc": "d1", "items": [{"text": "one"}]}),
               ("t_delete_page", {"doc": "d1"}))
    result = toolbox.dispatch(session, "batch", {"ops": ops, "atomic": True})
    assert result.error.message == "ops[1]: no tool 't_delete_page': use t_pages with action delete"
    assert result.error.field == "ops[1].tool"
    assert result.error.valid_options[0] == "t_pages"
    partial = toolbox.dispatch(session, "batch", {"ops": ops})       # the op fails alone
    assert partial.data["failed"] == [1]
    assert partial.data["ops"][1]["error"]["message"] == (
        "no tool 't_delete_page': use t_pages with action delete")


@pytest.mark.parametrize("name, meant", [
    ("t_insert_picture", "t_add_picture"),                  # a verb for another
    ("t_add_pictures", "t_add_picture"),                    # a plural
    ("t_remove_pages", "t_pages with action delete"),
    ("t_copy_page", "t_pages with action duplicate"),
    ("t_reorder_pages", "t_pages with action move"),
    ("t_add_row", "t_edit_grid with action insert_row"),    # the action is the whole name
    ("t_set_chart_title", "edit_chart with action set_title"),
    ("t_get_chart", "edit_chart with action read"),
    ("add_chart", "edit_chart with action add"),            # no prefix
    ("tx_add_chart", "edit_chart with action add"),         # another prefix
    ("functions.t_add_chart", "edit_chart with action add"),
    ("t_align_boxes", "t_arrange with action align"),       # an action one tool has
    ("t_list_comments", "t_comments with action list"),
    ("t_accept_change", "t_changes with action accept"),
    ("t_remove_boxes", "t_arrange with action delete"),     # the format's table
])
def test_what_a_name_means(toolbox, name, meant):
    assert [str(hint) for hint in tool_hints(name, toolbox.tools, toolbox.tool_hints)] == [meant]


def test_a_table_entry_naming_no_tool_or_action_is_ignored(toolbox):
    assert tool_hints("t_zz_box", toolbox.tools, toolbox.tool_hints) == []
    assert tool_hints("t_drop_box", toolbox.tools, toolbox.tool_hints) == [
        Hint("t_arrange", "delete")]        # read as t_delete_box: the entry that works


def test_a_name_with_no_hint_lists_near_names_as_before(toolbox, session):
    result = toolbox.dispatch(session, "t_ad", {"doc": "d1"})
    assert result.error.message == "no tool 't_ad'"
    assert "t_add" in result.error.valid_options


def test_a_hint_outside_the_roles_says_which_role_it_needs(toolbox):
    session = _session(toolbox, [REVIEW])
    result = toolbox.dispatch(session, "t_accept_change", {"doc": "d1"})
    assert result.error.message == ("no tool 't_accept_change': t_changes with action accept "
                                    "does that, and needs the accept role; this session has "
                                    "review")
    assert "t_changes" not in result.error.valid_options
    accepting = _session(toolbox, [REVIEW, ACCEPT])
    result = toolbox.dispatch(accepting, "t_accept_change", {"doc": "d1"})
    assert result.error.message == "no tool 't_accept_change': use t_changes with action accept"


def test_in_a_batch_a_tool_batch_cannot_run_is_to_be_called_alone(toolbox, session):
    result = toolbox.dispatch(session, "batch", {"ops": _ops(("t_save_document", {"doc": "d1"})),
                                                 "atomic": True})
    assert result.error.message == ("ops[0]: no tool 't_save_document': use save_document on its "
                                    "own (batch cannot run it)")


def test_an_action_of_another_tool_names_that_tool(toolbox, session):
    result = toolbox.dispatch(session, "t_comments", {"doc": "d1", "action": "accept"})
    assert result.error.field == "action"
    assert result.error.message == ("action is 'accept'; not one of the allowed values; use "
                                    "t_changes with action accept")
    assert result.error.valid_options == t_comments.actions     # the field's own values
    result = toolbox.dispatch(session, "edit_chart", {"doc": "d1", "action": "delete_page"})
    assert result.error.message.endswith("; use t_pages with action delete")


def test_an_action_spelt_with_another_verb_is_the_tools_own(toolbox, session):
    result = toolbox.dispatch(session, "t_edit_grid", {"doc": "d1", "action": "add_row"})
    assert result.error.message == ("action is 'add_row'; not one of the allowed values; did "
                                    "you mean 'insert_row'?")
    assert action_hints(t_edit_grid, "add_row", toolbox.tools) == ("insert_row", [])


def test_an_action_hint_outside_the_roles_leaves_the_message_as_it_was(toolbox):
    session = _session(toolbox, [REVIEW])
    result = toolbox.dispatch(session, "t_comments", {"doc": "d1", "action": "accept"})
    assert "t_changes" not in result.error.message


def test_singular():
    assert [singular(word) for word in ("pages", "categories", "series", "class", "row",
                                        "boxes", "classes")] == [
        "page", "category", "series", "class", "row", "box", "class"]


# -- N4: a batch op's comment ----------------------------------------------------------------------


@pytest.mark.parametrize("atomic", [False, True])
def test_a_batch_op_may_carry_a_comment_and_a_note(toolbox, session, atomic):
    ops = _ops(("t_add", {"doc": "d1", "items": [{"text": "one"}]}),
               ("t_add", {"doc": "d1", "items": [{"text": "two"}]}),
               ("t_add", {"doc": "d1", "items": [{"text": "three"}]}))
    ops[0]["comment"] = "the first"
    ops[1]["note"] = {"why": 2}           # not a string: kept as its JSON
    ops[2].update(comment="x" * 600, note=None)
    result = toolbox.dispatch(session, "batch", {"ops": ops, "atomic": atomic})
    assert result.ok, result.error
    assert all("comment" not in entry and "note" not in entry for entry in result.data["ops"])
    record = session.log[-1]
    assert record.comments == [{"op": 0, "comment": "the first"},
                               {"op": 1, "note": '{"why": 2}'},
                               {"op": 2, "comment": "x" * 499 + "…"}]
    assert record.to_json()["comments"] == record.comments
    assert "comment" not in str(record.shape)       # the arguments as run, without them


def test_a_batch_without_comments_logs_none(toolbox, session):
    toolbox.dispatch(session, "batch", {"ops": _ops(("t_add", {"doc": "d1",
                                                              "items": [{"text": "one"}]}))})
    assert session.log[-1].comments is None
    assert "comments" not in session.log[-1].to_json()


def test_a_comment_is_logged_when_the_batch_fails(toolbox, session):
    ops = _ops(("t_fail", {"doc": "d1"}))
    ops[0]["comment"] = "will fail"
    result = toolbox.dispatch(session, "batch", {"ops": ops})
    assert not result.ok
    assert session.log[-1].comments == [{"op": 0, "comment": "will fail"}]


def test_comment_is_still_unknown_outside_a_batch_op(toolbox, session):
    result = toolbox.dispatch(session, "t_add", {"doc": "d1", "items": [{"text": "one"}],
                                                 "comment": "why"})
    assert result.error.message.startswith("unknown argument 'comment'")


def test_the_batch_definition_does_not_advertise_comment(toolbox):
    batch = next(item for item in toolbox.definitions("anthropic") if item["name"] == "batch")
    assert set(batch["input_schema"]["properties"]["ops"]["items"]["properties"]) == {
        "tool", "arguments"}
