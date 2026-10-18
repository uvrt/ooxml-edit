"""Session views (round 8, M1): several agents on one shared session at once, each with
roles of its own and the application's context for its calls; and the role line decided
over the tools sent (M2).  Made-up tools only."""

from __future__ import annotations

import threading
from types import MappingProxyType

import pytest

from ooxml_edit.tools import ROLES, Result, Session, SessionView, Toolbox, string, tool
from ooxml_edit.tools.prompts import REVIEW_ACCEPT, REVIEW_COMMENTS, role_line

import synthetic
from test_tools_roles import GROUPS, ROLE_TOOLS, t_changes, t_check, t_comments
from test_tools_shared import CHECKS, CLOCK, DECK, DECK_TOOLS, TEXT, TEXT_TOOLS, _ops

SEEN: list[dict] = []


@tool("app_note", "Keep a note for the people running the agents.",
      {"doc": string("Document id."), "text": string("The note.")}, kind="docx")
def app_note(call, doc, text):
    """An application's own tool: it wants to know which of its agents called."""
    SEEN.append(dict(call.caller))
    return Result(summary=f"noted for {call.caller.get('agent', 'nobody')}")


@pytest.fixture
def toolbox():
    CHECKS.clear()
    SEEN.clear()
    with Toolbox(TEXT_TOOLS + DECK_TOOLS + ROLE_TOOLS + [app_note], formats=[DECK, TEXT],
                 groups=GROUPS) as box:
        yield box


@pytest.fixture
def shared(toolbox):
    session = toolbox.session(clock=CLOCK)                  # every role: the most allowed
    session.open(synthetic.outer_package(), "text.docx")     # d1
    return session


def _items(session):
    return session.entry("d1").document.items()


# -- two agents, one session --------------------------------------------------------------------


def test_a_reviewer_and_a_builder_share_one_session_with_their_own_roles(toolbox, shared):
    reviewer = shared.view(roles=["review"], context={"agent": "reviewer-1"})
    builder = shared.view(roles=["review", "edit"], context={"agent": "builder-1"})
    assert isinstance(reviewer, SessionView) and reviewer.session is shared
    assert reviewer.roles == ("review",) and builder.roles == ("review", "edit")
    # The same documents, history and outputs: nothing is copied.
    assert reviewer.documents is shared.documents is builder.documents
    assert reviewer.entry("d1") is builder.entry("d1")

    added = toolbox.dispatch(builder, "t_add", {"doc": "d1", "items": [{"text": "new",
                                                                         "ref": "fresh"}]})
    assert added.ok
    refused = toolbox.dispatch(reviewer, "t_set", {"doc": "d1", "target": "$fresh",
                                                   "text": "no"})
    assert refused.error.code == "refused" and refused.error.details == {
        "role": "edit", "roles": ["review"]}
    assert refused.error.message.startswith("t_set needs the edit role; this session has "
                                            "review.")
    # The reviewer reads what the builder made, at the version it made.
    read = toolbox.dispatch(reviewer, "t_read", {"doc": "d1"})
    assert read.ok and read.data[-1] == "new" and read.version == added.version
    assert toolbox.dispatch(reviewer, "t_comments", {"doc": "d1", "action": "add",
                                                     "items": [{"text": "why?"}]}).ok
    assert toolbox.dispatch(reviewer, "t_comments",
                            {"doc": "d1", "action": "resolve"}).error.code == "refused"
    # The session itself keeps every role.
    assert toolbox.dispatch(shared, "t_set", {"doc": "d1", "target": "$fresh",
                                              "text": "yes"}).ok
    saved = toolbox.dispatch(reviewer, "save_document", {"doc": "d1", "name": "r.docx",
                                                         "format": "docx"})
    assert saved.ok and [out.name for out in shared.take_outputs()] == ["r.docx"]


def test_a_view_s_batch_holds_every_op_to_the_view_s_roles(toolbox, shared):
    reviewer = shared.view(roles=["review"])
    before = _items(shared)
    partial = toolbox.dispatch(reviewer, "batch", {"ops": _ops(
        ("t_read", {"doc": "d1"}),
        ("t_add", {"doc": "d1", "items": [{"text": "x"}]}),
        ("t_comments", {"doc": "d1", "action": "add", "items": [{"text": "c"}]}))})
    assert partial.ok and partial.data["failed"] == [1]
    assert "t_add needs the edit role" in partial.data["ops"][1]["error"]["message"]
    atomic = toolbox.dispatch(reviewer, "batch", {"atomic": True, "ops": _ops(
        ("t_read", {"doc": "d1"}),
        ("t_set", {"doc": "d1", "target": "item:1", "text": "x"}))})
    assert atomic.error.code == "refused" and atomic.error.field == "ops[1].tool"
    assert _items(shared) == before
    # The same batch through the session, or a builder's view, applies.
    builder = shared.view(roles=["review", "edit"])
    assert toolbox.dispatch(builder, "batch", {"atomic": True, "ops": _ops(
        ("t_read", {"doc": "d1"}),
        ("t_add", {"doc": "d1", "items": [{"text": "x"}]}))}).ok
    assert _items(shared) == before + ["x"]


def test_per_call_roles_on_dispatch_narrow_a_session_or_a_view(toolbox, shared):
    refused = toolbox.dispatch(shared, "t_set", {"doc": "d1", "target": "item:1", "text": "x"},
                               roles=["review"], context={"agent": "reviewer-1"})
    assert refused.error.code == "refused" and refused.error.details["roles"] == ["review"]
    assert toolbox.dispatch(shared, "t_read", {"doc": "d1"}, roles="review").ok
    builder = shared.view(roles=["review", "edit"])
    assert toolbox.dispatch(builder, "t_add", {"doc": "d1", "items": [{"text": "y"}]},
                            roles=["review"]).error.code == "refused"
    assert toolbox.dispatch(builder, "t_add", {"doc": "d1", "items": [{"text": "y"}]}).ok
    results = toolbox.dispatch_many(builder, [
        ("t_read", {"doc": "d1"}), ("t_add", {"doc": "d1", "items": [{"text": "z"}]})],
        roles=["review"])
    assert results[0].ok and results[1].error.code == "refused"


# -- a view narrows, never widens ---------------------------------------------------------------


def test_a_view_cannot_widen_its_session_s_roles(toolbox):
    session = toolbox.session(roles=["review", "resolve"])
    with pytest.raises(ValueError, match="never widens"):
        session.view(roles=["review", "edit"])
    reviewer = session.view(roles=["review"])
    with pytest.raises(ValueError, match="resolve is not among this session's roles"):
        reviewer.view(roles=["resolve"])
    assert reviewer.view(roles="review").roles == ("review",)
    assert reviewer.view().roles == ("review",)                 # None: as it is
    with pytest.raises(ValueError, match="edit is not among"):
        toolbox.dispatch(session, "t_read", {"doc": "d1"}, roles=["edit"])
    with pytest.raises(ValueError, match="resolve is not among"):
        toolbox.dispatch(reviewer, "t_read", {"doc": "d1"}, roles=["review", "resolve"])
    with pytest.raises(ValueError, match="unknown role"):
        session.view(roles=["admin"])
    with pytest.raises(TypeError, match="context takes a mapping"):
        session.view(context=["agent"])
    with pytest.raises(TypeError, match="a view is of a Session"):
        SessionView(object())


def test_a_session_narrowed_after_a_view_was_made_refuses_the_view(toolbox, shared):
    builder = shared.view(roles=["review", "edit"])
    shared.roles = ("review",)
    with pytest.raises(ValueError, match="edit is not among this session's roles"):
        toolbox.dispatch(builder, "t_read", {"doc": "d1"})


def test_a_view_sets_nothing_and_reads_through(shared):
    view = shared.view(roles=["review"])
    with pytest.raises(AttributeError, match="sets nothing"):
        view.roles = ROLES
    with pytest.raises(AttributeError, match="sets nothing"):
        view.limits = None
    assert view.limits is shared.limits and view.id == shared.id
    handle = view.add_blob(b"a,b\n1,2\n", "t.csv")              # the session's own blob
    assert shared.blob(handle).name == "t.csv"
    assert "roles=['review']" in repr(view)


# -- context ---------------------------------------------------------------------------------


def test_context_reaches_an_application_s_tool_in_the_toolbox(toolbox, shared):
    reviewer = shared.view(roles=["review"], context={"agent": "reviewer-1", "run": 7})
    result = toolbox.dispatch(reviewer, "app_note", {"doc": "d1", "text": "slide 3 is long"})
    assert result.ok and result.summary == "noted for reviewer-1"
    # A dispatch's context adds to the view's (its keys win); a batch's ops see it too.
    toolbox.dispatch(reviewer, "batch", {"ops": _ops(
        ("app_note", {"doc": "d1", "text": "a"}), ("app_note", {"doc": "d1", "text": "b"}))},
        context={"run": 8})
    narrower = reviewer.view(context={"pass": 2})
    toolbox.dispatch(narrower, "app_note", {"doc": "d1", "text": "c"})
    toolbox.dispatch(shared, "app_note", {"doc": "d1", "text": "d"})     # none: empty
    assert SEEN == [{"agent": "reviewer-1", "run": 7}, {"agent": "reviewer-1", "run": 8},
                    {"agent": "reviewer-1", "run": 8},
                    {"agent": "reviewer-1", "run": 7, "pass": 2}, {}]
    # The dispatcher puts none of it in what the model gets.
    read = toolbox.dispatch(reviewer, "t_read", {"doc": "d1"})
    assert "reviewer-1" not in str(toolbox.render_result("anthropic", read, "c1"))


def test_a_call_s_context_is_read_only_and_its_own(toolbox, shared):
    seen = {}

    @tool("app_peek", "Look at the caller.", {"doc": string("Document id.")}, kind="docx")
    def app_peek(call, doc):
        seen["caller"], seen["roles"] = call.caller, call.roles
        with pytest.raises(TypeError):
            call.caller["agent"] = "someone else"
        return {}

    with Toolbox(TEXT_TOOLS + [app_peek], formats=[TEXT]) as box:
        session = box.session()
        session.open(synthetic.outer_package(), "text.docx")
        given = {"agent": "reviewer-1"}
        view = session.view(roles=["review"], context=given)
        given["agent"] = "changed later"                        # the view kept a copy
        assert box.dispatch(view, "app_peek", {"doc": "d1"}).ok
        assert dict(seen["caller"]) == {"agent": "reviewer-1"}
        assert seen["roles"] == ("review",)
        assert box.dispatch(session, "app_peek", {"doc": "d1"}).ok
        assert dict(seen["caller"]) == {} and seen["roles"] == ROLES
    assert isinstance(Session.context, MappingProxyType) and not Session.context


def test_the_log_tells_the_agents_apart(toolbox, shared):
    toolbox.dispatch(shared, "t_read", {"doc": "d1"})
    reviewer = shared.view(roles=["review"], context={"agent": "reviewer-1"})
    toolbox.dispatch(reviewer, "t_read", {"doc": "d1"})
    plain, viewed = shared.log[-2:]
    assert plain.roles is None and plain.context is None
    assert set(plain.to_json()) == {"tool", "docs", "digest", "shape", "ok", "code",
                                    "summary", "duration", "version"}
    assert viewed.roles == ["review"] and viewed.context == {"agent": "reviewer-1"}
    assert viewed.to_json()["context"] == {"agent": "reviewer-1"}
    assert shared.entry("d1").log[-1] is viewed


# -- concurrency and undo ------------------------------------------------------------------------


def test_views_in_threads_at_once_share_the_session_s_locks(toolbox, shared):
    views = [shared.view(roles=["review", "edit"], context={"agent": f"builder-{n}"})
             for n in range(4)]
    reviewer = shared.view(roles=["review"])
    before = len(_items(shared))
    errors = []

    def build(view):
        for n in range(10):
            result = toolbox.dispatch(view, "t_add", {"doc": "d1", "items": [{"text": "x"}]})
            if not result.ok:
                errors.append(result.error)

    def review():
        for _ in range(10):
            if toolbox.dispatch(reviewer, "t_add", {"doc": "d1",
                                                     "items": [{"text": "no"}]}).ok:
                errors.append("a reviewer added")
            toolbox.dispatch(reviewer, "t_read", {"doc": "d1"})

    threads = [threading.Thread(target=build, args=(view,)) for view in views]
    threads.append(threading.Thread(target=review))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors
    assert len(_items(shared)) == before + 40
    assert {record.context["agent"] for record in shared.log if record.context and
            record.tool == "t_add" and record.ok} == {f"builder-{n}" for n in range(4)}


def test_undo_in_a_shared_session_takes_back_the_latest_change_whoever_made_it(toolbox,
                                                                               shared):
    first = shared.view(roles=["review", "edit"], context={"agent": "builder-1"})
    second = shared.view(roles=["review", "edit"], context={"agent": "builder-2"})
    before = _items(shared)
    assert toolbox.dispatch(first, "t_add", {"doc": "d1", "items": [{"text": "one"}]}).ok
    assert toolbox.dispatch(second, "undo", {"doc": "d1"}).ok
    assert _items(shared) == before
    assert toolbox.dispatch(shared.view(roles=["review"]), "undo",
                            {"doc": "d1"}).error.code == "refused"


# -- the role line follows the tools sent (M2) ---------------------------------------------------


def test_the_role_line_follows_the_tools_sent(toolbox):
    whole = toolbox.system_prompt(roles=["review"])
    assert REVIEW_COMMENTS in whole                             # the toolbox has comments
    reading = toolbox.system_prompt(roles=["review"], tools=["t_read", "t_check"])
    assert "Role: review." in reading and REVIEW_COMMENTS not in reading
    assert reading == whole.replace(REVIEW_COMMENTS, "")
    with_comments = toolbox.system_prompt(roles=["review"], tools=["t_read", t_comments])
    assert with_comments == whole
    accept = toolbox.system_prompt(roles=["review", "accept"], tools=["t_comments"])
    assert REVIEW_COMMENTS in accept and REVIEW_ACCEPT not in accept
    assert REVIEW_ACCEPT in toolbox.system_prompt(roles=["review", "accept"],
                                                  tools=[t_changes, t_check])
    # An application's own names, and a tool search, say nothing.
    assert toolbox.system_prompt(roles=["review"], tools=["my_own_tool"]) == reading
    # With edit there is no line, whatever is sent.
    assert toolbox.system_prompt(tools=["t_read"]) == toolbox.system_prompt()


@pytest.mark.parametrize("provider,options", [
    ("anthropic", {}), ("anthropic", {"defer": True}),
    ("openai-responses", {"defer": True, "namespaces": True}), ("openai-chat", {})])
def test_the_role_line_can_be_read_from_the_definitions_sent(toolbox, provider, options):
    for groups in ("core", ["core", "t_review"], None):
        for roles in (["review"], ["review", "accept"], ["accept"], ["review", "resolve"]):
            sent = toolbox.definitions(provider, groups=groups, roles=roles, **options)
            by_definitions = toolbox.system_prompt(roles=roles, tools=sent)
            assert by_definitions == toolbox.system_prompt(roles=roles, groups=groups or "all")


def test_groups_follow_definitions_and_neither_is_the_whole_toolbox():
    with Toolbox(TEXT_TOOLS + [t_check, t_comments], formats=[TEXT], groups=GROUPS) as box:
        # A reviewer's core holds its comments tool (review_core): the line says so.
        assert REVIEW_COMMENTS in box.system_prompt(roles=["review"], groups="core")
        # An editor's core does not, but its prompt has no role line anyway.
        assert box.system_prompt(roles=["edit"], groups="core") == box.system_prompt(
            roles=["edit"])
        with pytest.raises(ValueError, match="tools or groups, not both"):
            box.system_prompt(roles=["review"], tools=["t_read"], groups="core")
        with pytest.raises(TypeError, match="names, Tool objects or definitions"):
            box.system_prompt(roles=["review"], tools=[3])
    assert role_line(["review"], comments=False) + REVIEW_COMMENTS == role_line(["review"])


def test_the_default_prompt_is_unchanged(toolbox):
    for roles in (None, ["review"], ["review", "resolve"], ["accept"], ["edit"]):
        kwargs = {} if roles is None else {"roles": roles}
        assert toolbox.system_prompt(**kwargs) == toolbox.system_prompt(
            tools=list(toolbox.tools), **kwargs)


def test_the_model_s_mistakes_through_a_view_are_still_results(toolbox, shared):
    view = shared.view(roles=["review"])
    assert toolbox.dispatch(view, "no_such_tool", {}).error.code == "invalid_arguments"
    assert toolbox.dispatch(view, "t_read", "not json").error.code == "invalid_arguments"
