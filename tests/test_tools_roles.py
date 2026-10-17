"""Roles (round 7, item 9): additive toolsets -- ``review``, ``resolve``, ``accept``,
``edit`` -- that a
toolbox or session loads in any combination, enforced by the dispatcher, inside ``batch``
too.  All three, the default, is the toolbox as it was without roles.  Made-up tools only."""

from __future__ import annotations

import pytest

from ooxml_edit.tools import (ACCEPT, ANY_ROLE, EDIT, RESOLVE, REVIEW, ROLES, Result, SubsetError,
                              ToolGroup, Toolbox, array, boolean, obj, role_set, shared,
                              string, tool)
from ooxml_edit.tools.adapters import anthropic_problems, openai_problems
from ooxml_edit.tools.prompts import role_line

import synthetic
from test_tools_shared import CHECKS, CLOCK, DECK, DECK_TOOLS, TEXT, TEXT_TOOLS, _ops

COMMENT_ACTIONS = ["list", "add", "reply", "resolve", "reopen", "edit", "delete"]


@tool("t_comments", "Comment threads: list, add, reply, resolve, reopen, edit, delete.",
      {"doc": string("Document id."),
       "action": string("What to do.", enum=COMMENT_ACTIONS),
       "items": array(obj({"text": string("The text.", optional=True),
                           "resolve": boolean("reply: also resolve.", optional=True)}),
                      "The comments.", optional=True)},
      kind="docx", group="t_review", mutates=True,
      reads=lambda arguments: arguments.get("action") == "list",
      action_roles=shared.COMMENT_ACTION_ROLES, call_role=shared.comment_role,
      review_core=True)
def t_comments(call, doc, action, items=None):
    return Result(summary=f"{action}: {len(items or [])} comment(s)")


@tool("t_chart", "Read or retitle a chart.",
      {"doc": string("Document id."),
       "action": string("What to do; read changes nothing.", enum=["read", "set_title"]),
       "text": string("set_title: the title.", optional=True)},
      kind="docx", group="t_objects", mutates=True,
      reads=lambda arguments: arguments.get("action") == "read",
      action_roles={"read": REVIEW})
def t_chart(call, doc, action, text=None):
    return Result(summary=f"chart {action}")


@tool("t_changes", "Tracked changes: list, accept or reject them.",
      {"doc": string("Document id."),
       "action": string("What to do; list changes nothing.", enum=["list", "accept", "reject"])},
      kind="docx", group="t_review", mutates=True,
      reads=lambda arguments: arguments.get("action") == "list",
      action_roles={"list": REVIEW, "accept": ACCEPT, "reject": ACCEPT}, review_core=True)
def t_changes(call, doc, action):
    return Result(summary=f"changes {action}")


@tool("t_tracking", "Turn tracking on or off.", {"doc": string("Document id."),
                                                 "on": boolean("Track.")},
      kind="docx", group="t_objects", mutates=True)
def t_tracking(call, doc, on):
    return Result(summary="tracking set")


@tool("t_check", "Facts about the document.", {"doc": string("Document id.")}, kind="docx")
def t_check(call, doc):
    return {"items": len(call.document.items())}


ROLE_TOOLS = [t_comments, t_changes, t_chart, t_tracking, t_check]
GROUPS = [*shared.GROUPS, ToolGroup("t_review", "Comments."),
          ToolGroup("t_objects", "Charts and tracking.")]


def _toolbox(**options):
    CHECKS.clear()
    return Toolbox(TEXT_TOOLS + DECK_TOOLS + ROLE_TOOLS, formats=[DECK, TEXT], groups=GROUPS,
                   **options)


@pytest.fixture
def toolbox():
    with _toolbox() as box:
        yield box


def _session(toolbox, roles):
    session = toolbox.session(clock=CLOCK, roles=roles)
    session.open(synthetic.outer_package(), "text.docx")      # d1
    return session


def _names(definitions):
    return [d.get("name") for d in definitions if "name" in d]


# -- the role of a tool and of a call -----------------------------------------------------------


def test_a_tool_s_roles_come_from_its_actions_and_whether_it_changes():
    assert t_check.roles == {REVIEW}                   # a reading tool
    assert t_tracking.roles == {EDIT}                  # a changing one
    assert t_chart.roles == {REVIEW, EDIT}
    assert t_comments.roles == set(ROLES)
    # Editing a comment's text is every role's (the user's decision, 2026-10-17).
    assert t_comments.actions_for([REVIEW]) == ["list", "add", "reply", "edit"]
    assert t_comments.actions_for([REVIEW, RESOLVE]) == ["list", "add", "reply", "resolve",
                                                         "reopen", "edit", "delete"]
    assert t_comments.actions_for([EDIT]) == ["edit"]
    assert t_changes.roles == {REVIEW, ACCEPT}
    assert t_changes.actions_for([ACCEPT]) == ["accept", "reject"]
    assert t_comments.role_of({"action": "add"}) == REVIEW
    assert t_comments.role_of({"action": "reply", "items": [{"text": "ok"}]}) == REVIEW
    assert t_comments.role_of({"action": "reply",
                               "items": [{"text": "ok", "resolve": True}]}) == RESOLVE
    assert t_comments.role_of({"action": "delete"}) == RESOLVE
    assert t_comments.role_of({"action": "edit"}) == ANY_ROLE


def test_the_shared_tools_roles():
    by_name = {spec: shared.definition(spec) for spec in shared.SPECS}
    for name in ("open_document", "save_document", "close_document", "read_blob", "batch"):
        assert by_name[name].role == ANY_ROLE and by_name[name].roles == set(ROLES), name
    for name in ("new_document", "undo", "replace_text", "edit_smartart", "set_properties"):
        assert by_name[name].roles == {EDIT}, name
    for name in ("describe", "find_text", "render", "check"):
        assert by_name[name].roles == {REVIEW}, name
    chart = by_name["edit_chart"]
    assert chart.roles == {REVIEW, EDIT}
    assert chart.actions_for([REVIEW]) == ["read"]
    assert chart.role_of({"action": "read"}) == REVIEW
    assert chart.role_of({"action": "add"}) == EDIT


def test_role_sets_are_named_roles_in_order():
    assert role_set(None) == ROLES
    assert role_set("review") == (REVIEW,)
    assert role_set(["edit", "review"]) == (REVIEW, EDIT)
    assert role_set(["edit", "accept", "review"]) == (REVIEW, ACCEPT, EDIT)
    assert ROLES == (REVIEW, RESOLVE, ACCEPT, EDIT)
    with pytest.raises(ValueError, match="unknown role"):
        role_set(["reviewer"])
    with pytest.raises(ValueError, match="no roles"):
        role_set([])
    with pytest.raises(ValueError, match="unknown role"):
        _toolbox(roles=["write"])


def test_a_definition_with_a_wrong_role_is_refused_when_made():
    with pytest.raises(SubsetError, match="role 'author'"):
        tool("t_bad", "Bad.", {"doc": string("Document id.")}, role="author")(lambda call, doc: 0)
    with pytest.raises(SubsetError, match="'publish', which is not an action"):
        tool("t_bad", "Bad.", {"doc": string("Document id."),
                               "action": string("What.", enum=["read"])},
             action_roles={"publish": REVIEW})(lambda call, doc, action: 0)
    with pytest.raises(SubsetError, match="the role 'owner'"):
        tool("t_bad", "Bad.", {"doc": string("Document id."),
                               "action": string("What.", enum=["read"])},
             action_roles={"read": "owner"})(lambda call, doc, action: 0)


# -- the default: the toolbox as without roles --------------------------------------------------


@pytest.mark.parametrize("provider", ["anthropic", "openai-responses", "openai-chat"])
def test_all_roles_is_the_toolbox_without_roles(toolbox, provider):
    assert toolbox.roles == ROLES
    for groups in (None, "core", ["t_review"]):
        default = toolbox.definitions(provider, groups=groups)
        assert toolbox.definitions(provider, groups=groups, roles=ROLES) == default
        assert toolbox.definitions(provider, groups=groups, roles=None) == default
    assert all(item.for_roles(ROLES) is item for item in toolbox.tools.values())
    assert toolbox.select() == list(toolbox.tools.values())
    prompt = toolbox.system_prompt(extra="House rules: x")
    assert toolbox.system_prompt(extra="House rules: x", roles=ROLES) == prompt
    assert "Role:" not in prompt
    assert toolbox.session().roles == ROLES


def test_a_toolbox_s_roles_are_its_sessions_and_definitions_default():
    with _toolbox(roles="review") as box:
        assert box.session().roles == (REVIEW,)
        assert box.session(roles=None).roles == ROLES
        assert "t_tracking" not in _names(box.definitions("anthropic"))
        assert "t_tracking" in _names(box.definitions("anthropic", roles=None))
        assert box.system_prompt().count("Role: review.") == 1


# -- definitions per role set -------------------------------------------------------------------


def test_a_review_set_is_sent_only_its_tools_and_actions(toolbox):
    names = _names(toolbox.definitions("anthropic", roles=["review"], defer=False))
    assert {"t_read", "t_check", "t_comments", "t_chart", "open_document", "save_document",
            "close_document", "read_blob", "batch"} <= set(names)
    for edit_only in ("t_add", "t_set", "t_tracking", "new_document", "undo", "replace_text",
                      "toy_ppt_set_title", "set_properties", "edit_smartart"):
        assert edit_only not in names
    sent = {d["name"]: d for d in toolbox.definitions("anthropic", roles=["review"],
                                                      defer=False)}
    assert sent["t_comments"]["input_schema"]["properties"]["action"]["enum"] == \
        ["list", "add", "reply", "edit"]
    assert sent["t_changes"]["input_schema"]["properties"]["action"]["enum"] == ["list"]
    assert sent["t_chart"]["input_schema"]["properties"]["action"]["enum"] == ["read"]
    # The toolbox's own tools keep every action: only what is sent is trimmed.
    assert toolbox.tools["t_comments"].actions == COMMENT_ACTIONS
    second = {d["name"]: d for d in toolbox.definitions("anthropic", roles=["review", "resolve"],
                                                        defer=False)}
    assert second["t_comments"]["input_schema"]["properties"]["action"]["enum"] == \
        ["list", "add", "reply", "resolve", "reopen", "edit", "delete"]


def test_the_core_of_a_role_set_is_its_roles_core_tools(toolbox):
    core = {"review": set(_names(toolbox.definitions("anthropic", groups="core",
                                                     roles=["review"]))),
            "edit": set(_names(toolbox.definitions("anthropic", groups="core",
                                                   roles=["edit"])))}
    everything = set(_names(toolbox.definitions("anthropic", groups="core")))
    # A reviewer's main tool joins the core of a role set without edit: comments.
    assert core["review"] | core["edit"] == everything | {"t_comments", "t_changes"}
    assert "undo" in core["edit"] - core["review"]
    assert {"t_read", "t_check", "t_comments", "t_changes"} <= core["review"] - core["edit"]
    assert {"open_document", "save_document", "batch"} <= core["review"] & core["edit"]


@pytest.mark.parametrize("roles, core", [(["review"], True), (["review", "resolve"], True),
                                         (["resolve"], True), (["review", "edit"], False),
                                         (None, False)])
def test_a_reviewer_s_main_tools_are_in_the_core_of_a_set_without_edit(toolbox, roles, core):
    loaded = _names(toolbox.definitions("anthropic", groups="core", roles=roles))
    assert ("t_comments" in loaded) is core
    deferred = {d["name"]: d for d in toolbox.definitions("anthropic", roles=roles)
                if "name" in d}
    assert ("defer_loading" not in deferred["t_comments"]) is core
    assert toolbox.tools["t_comments"].group == "t_review"     # the toolbox's own: unchanged


@pytest.mark.parametrize("roles", [["review"], ["review", "resolve"], ["resolve"], ["edit"],
                                   ["review", "edit"], ["accept"], ["review", "accept"]])
def test_every_role_set_s_definitions_meet_each_provider_s_rules(toolbox, roles):
    for groups in ("core", None):
        assert anthropic_problems(toolbox.definitions("anthropic", groups=groups,
                                                      roles=roles)) == []
        assert openai_problems(toolbox.definitions("openai-chat", groups=groups, roles=roles),
                               chat=True) == []
    assert anthropic_problems(toolbox.definitions("anthropic", defer=True, roles=roles)) == []
    spaced = toolbox.definitions("openai-responses", defer=True, namespaces=True, roles=roles)
    assert openai_problems(spaced) == []
    allowed = toolbox.allowed_tools("core", roles=roles)
    assert allowed["tools"] and all(
        entry["name"] in _names(toolbox.definitions("anthropic", roles=roles))
        for entry in allowed["tools"])


def test_a_group_with_no_tool_of_the_roles_is_not_sent(toolbox):
    spaced = toolbox.definitions("openai-responses", defer=True, namespaces=True,
                                 roles=["resolve"])
    assert "t_objects" not in [d.get("name") for d in spaced]


# -- refusals ------------------------------------------------------------------------------------


def test_a_reviewer_reads_comments_and_saves_but_changes_nothing(toolbox):
    session = _session(toolbox, ["review"])
    assert toolbox.dispatch(session, "t_read", {"doc": "d1"}).ok
    assert toolbox.dispatch(session, "t_check", {"doc": "d1"}).ok
    assert toolbox.dispatch(session, "t_chart", {"doc": "d1", "action": "read"}).ok
    for action in ("list", "add", "reply"):
        assert toolbox.dispatch(session, "t_comments",
                                {"doc": "d1", "action": action, "items": [{"text": "x"}]}).ok
    saved = toolbox.dispatch(session, "save_document",
                             {"doc": "d1", "name": "reviewed.docx", "format": "docx"})
    assert saved.ok and [out.name for out in session.take_outputs()] == ["reviewed.docx"]

    refused = toolbox.dispatch(session, "t_set", {"doc": "d1", "target": "item:1",
                                                  "text": "changed"})
    assert not refused.ok and refused.error.code == "refused"
    assert refused.error.message.startswith("t_set needs the edit role; this session has "
                                            "review. Do not make the change: report it")
    assert refused.error.details == {"role": "edit", "roles": ["review"]}
    assert session.entry("d1").document.items() == synthetic_items()

    action = toolbox.dispatch(session, "t_chart", {"doc": "d1", "action": "set_title",
                                                   "text": "T"})
    assert action.error.code == "refused" and action.error.field == "action"
    assert action.error.message.startswith("t_chart set_title needs the edit role")
    assert action.error.valid_options == ["read"]

    for name, arguments in (("new_document", {"kind": "docx"}), ("undo", {"doc": "d1"}),
                            ("t_tracking", {"doc": "d1", "on": True})):
        result = toolbox.dispatch(session, name, arguments)
        assert result.error.code == "refused" and "needs the edit role" in \
            result.error.message, name


def synthetic_items():
    from tools_toys import ToyDocument

    return ToyDocument(synthetic.outer_package()).items()


def test_a_refusal_comes_before_the_arguments_are_checked(toolbox):
    session = _session(toolbox, ["review"])
    result = toolbox.dispatch(session, "t_set", {"doc": "d1"})     # target, text missing
    assert result.error.code == "refused"


def test_resolving_a_comment_needs_the_resolve_role(toolbox):
    review = _session(toolbox, ["review"])
    for arguments in ({"action": "resolve", "items": [{}]}, {"action": "reopen"},
                      {"action": "delete"}):
        result = toolbox.dispatch(review, "t_comments", {"doc": "d1", **arguments})
        assert result.error.code == "refused", arguments
        assert "needs the resolve role" in result.error.message
        assert "Leave the thread as it is" in result.error.message
        assert result.error.valid_options == ["list", "add", "reply", "edit"]
    closing = toolbox.dispatch(review, "t_comments", {
        "doc": "d1", "action": "reply", "items": [{"text": "done", "resolve": True}]})
    assert closing.error.code == "refused"
    assert closing.error.message.startswith("this t_comments call needs the resolve role")

    second = _session(toolbox, ["review", "resolve"])
    for arguments in ({"action": "resolve", "items": [{}]}, {"action": "reopen"},
                      {"action": "delete"},
                      {"action": "reply", "items": [{"text": "done", "resolve": True}]}):
        assert toolbox.dispatch(second, "t_comments", {"doc": "d1", **arguments}).ok, arguments
    assert toolbox.dispatch(second, "t_comments", {"doc": "d1", "action": "edit",
                                                   "items": [{"text": "new"}]}).ok
    assert not toolbox.dispatch(second, "t_set", {"doc": "d1", "target": "item:1",
                                                  "text": "x"}).ok


def test_an_editor_alone_has_no_reading_tools(toolbox):
    session = _session(toolbox, ["edit"])
    assert toolbox.dispatch(session, "t_add", {"doc": "d1", "items": [{"text": "x"}]}).ok
    read = toolbox.dispatch(session, "t_read", {"doc": "d1"})
    assert read.error.code == "refused" and "needs the review role" in read.error.message
    assert toolbox.dispatch(session, "t_comments", {"doc": "d1", "action": "edit",
                                                    "items": [{"text": "y"}]}).ok
    assert not toolbox.dispatch(session, "t_comments", {"doc": "d1", "action": "add"}).ok
    accept = toolbox.dispatch(session, "t_changes", {"doc": "d1", "action": "accept"})
    assert accept.error.code == "refused" and "t_changes needs the accept role" in \
        accept.error.message
    assert toolbox.dispatch(session, "save_document",
                            {"doc": "d1", "name": "x.docx", "format": "docx"}).ok


def test_every_role_runs_everything_by_default(toolbox):
    session = _session(toolbox, None)
    for name, arguments in (("t_add", {"doc": "d1", "items": [{"text": "x"}]}),
                            ("t_comments", {"doc": "d1", "action": "delete"}),
                            ("t_chart", {"doc": "d1", "action": "set_title", "text": "T"}),
                            ("undo", {"doc": "d1"})):
        assert toolbox.dispatch(session, name, arguments).ok, name


# -- batch: each op held to the roles ------------------------------------------------------------


def test_a_review_batch_runs_its_review_ops_and_refuses_the_others(toolbox):
    session = _session(toolbox, ["review"])
    result = toolbox.dispatch(session, "batch", {"ops": _ops(
        ("t_read", {"doc": "d1"}),
        ("t_comments", {"doc": "d1", "action": "add", "items": [{"text": "too long"}]}),
        ("t_set", {"doc": "d1", "target": "item:1", "text": "changed"}),
        ("t_comments", {"doc": "d1", "action": "resolve", "items": [{}]}),
        ("t_chart", {"doc": "d1", "action": "read"}))})
    assert result.ok and result.data["applied"] == 3 and result.data["failed"] == [2, 3]
    ops = result.data["ops"]
    assert ops[2]["error"]["code"] == "refused" and ops[2]["error"]["field"] == "tool"
    assert "t_set needs the edit role" in ops[2]["error"]["message"]
    assert ops[3]["error"]["field"] == "arguments.action"
    assert "t_comments resolve needs the resolve role" in ops[3]["error"]["message"]
    assert session.entry("d1").document.items() == synthetic_items()


def test_an_atomic_batch_with_an_op_outside_the_roles_runs_nothing(toolbox):
    session = _session(toolbox, ["review"])
    result = toolbox.dispatch(session, "batch", {"atomic": True, "ops": _ops(
        ("t_comments", {"doc": "d1", "action": "add", "items": [{"text": "x"}]}),
        ("t_chart", {"doc": "d1", "action": "set_title", "text": "T"}))})
    assert not result.ok and result.error.code == "refused"
    assert result.error.field == "ops[1].arguments.action"
    assert result.error.message.startswith("ops[1] (t_chart): t_chart set_title needs the "
                                           "edit role")
    assert result.error.details["role"] == "edit"


def test_a_batch_of_only_refused_ops_is_an_error(toolbox):
    session = _session(toolbox, ["review"])
    result = toolbox.dispatch(session, "batch", {"ops": _ops(
        ("t_set", {"doc": "d1", "target": "item:1", "text": "x"}))})
    assert not result.ok and "needs the edit role" in result.error.message


# -- the prompt ---------------------------------------------------------------------------------


def test_a_review_set_s_prompt_has_its_role_line(toolbox):
    plain = toolbox.system_prompt(extra="House rules: x")
    review = toolbox.system_prompt(extra="House rules: x", roles=["review"])
    line = role_line(["review"])
    assert line.startswith("Role: review. Do not change the document")
    assert "Report each finding with the call that would fix it" in line
    assert "add comments" in line and "resolve" not in line
    assert review == plain.replace("\n\nHouse rules: x", f"\n\n{line}\n\nHouse rules: x")
    assert role_line(["review", "resolve"]).endswith(
        "You may also resolve, reopen and delete them.")
    assert role_line(["review", "edit"]) == role_line(["edit"]) == role_line(ROLES) == ""
    assert role_line(["resolve"]) == ("Role: resolve. Do not change the document; you may "
                                      "only resolve, reopen and delete comments.")
    assert role_line(["review", "accept"]).endswith(
        "reply to them. You may also accept and reject tracked changes.")
    assert role_line(["review", "accept"], tracked=False) == role_line(["review"])
    assert role_line(["accept"]) == ("Role: accept. Do not change the document; you may only "
                                     "accept and reject tracked changes.")
    assert role_line(["resolve", "accept"]).startswith("Role: resolve and accept.")
    assert role_line(["accept", "edit"]) == ""


def test_a_reviewer_without_a_comments_tool_is_not_told_it_may_comment():
    with Toolbox(TEXT_TOOLS + [t_check], formats=[TEXT]) as box:
        prompt = box.system_prompt(roles=["review"])
    assert "Role: review." in prompt and "comment" not in prompt.split("Role: review.")[1]
    assert role_line(["review"], comments=False) == role_line(["review"]).replace(
        " You may add comments and reply to them.", "")


# -- accept: settling tracked changes ------------------------------------------------------------


def test_an_editor_in_chief_accepts_and_rejects_but_does_not_edit(toolbox):
    session = _session(toolbox, ["review", "accept"])
    for action in ("list", "accept", "reject"):
        assert toolbox.dispatch(session, "t_changes", {"doc": "d1", "action": action}).ok
    assert toolbox.dispatch(session, "t_comments", {"doc": "d1", "action": "add",
                                                    "items": [{"text": "ok"}]}).ok
    for name, arguments in (("t_add", {"doc": "d1", "items": [{"text": "x"}]}),
                            ("t_comments", {"doc": "d1", "action": "resolve"})):
        assert toolbox.dispatch(session, name, arguments).error.code == "refused", name
    sent = {d["name"]: d for d in toolbox.definitions("anthropic", groups="core",
                                                      roles=["review", "accept"])}
    assert sent["t_changes"]["input_schema"]["properties"]["action"]["enum"] == [
        "list", "accept", "reject"]
    assert "You may also accept and reject tracked changes." in \
        toolbox.system_prompt(roles=["review", "accept"])


def test_accepting_needs_the_accept_role(toolbox):
    review = _session(toolbox, ["review", "resolve"])
    refused = toolbox.dispatch(review, "t_changes", {"doc": "d1", "action": "reject"})
    assert refused.error.code == "refused" and refused.error.field == "action"
    assert refused.error.message == (
        "t_changes reject needs the accept role; this session has review, resolve. Leave "
        "the tracked changes as they are and say which should be accepted or rejected.")
    assert refused.error.valid_options == ["list"]
    assert refused.error.details == {"role": "accept", "roles": ["review", "resolve"]}


def test_accept_alone_settles_changes_and_reads_nothing(toolbox):
    session = _session(toolbox, ["accept"])
    assert toolbox.dispatch(session, "t_changes", {"doc": "d1", "action": "accept"}).ok
    listed = toolbox.dispatch(session, "t_changes", {"doc": "d1", "action": "list"})
    assert listed.error.code == "refused" and "needs the review role" in listed.error.message
    core = _names(toolbox.definitions("anthropic", groups="core", roles=["accept"]))
    assert "t_changes" in core
    # The comments tool is there for its text edits, every role's, but not in the core.
    assert "t_comments" not in core
    assert "t_comments" in _names(toolbox.definitions("anthropic", roles=["accept"]))


def test_a_batch_holds_each_op_to_the_accept_role(toolbox):
    session = _session(toolbox, ["review", "accept"])
    result = toolbox.dispatch(session, "batch", {"ops": _ops(
        ("t_changes", {"doc": "d1", "action": "accept"}),
        ("t_add", {"doc": "d1", "items": [{"text": "x"}]}),
        ("t_changes", {"doc": "d1", "action": "reject"}))})
    assert result.ok and result.data["failed"] == [1]
    review = _session(toolbox, ["review"])
    atomic = toolbox.dispatch(review, "batch", {"atomic": True, "ops": _ops(
        ("t_changes", {"doc": "d1", "action": "list"}),
        ("t_changes", {"doc": "d1", "action": "accept"}))})
    assert atomic.error.code == "refused" and atomic.error.field == "ops[1].arguments.action"
    assert "t_changes accept needs the accept role" in atomic.error.message
