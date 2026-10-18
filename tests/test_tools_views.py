"""Session views (round 8, M1): several agents on one shared session at once, each with
roles of its own and the application's context for its calls; and the role line decided
over the tools sent (M2).  Made-up tools only."""

from __future__ import annotations

import threading
from types import MappingProxyType

import pytest

from ooxml_edit.tools import (ROLES, Result, Session, SessionView, Toolbox, ToolError, string,
                              tool)
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


def test_undo_without_a_caller_takes_back_the_latest_change_whoever_made_it(toolbox, shared):
    first = shared.view(roles=["review", "edit"], context={"agent": "builder-1"})
    before = _items(shared)
    assert toolbox.dispatch(first, "t_add", {"doc": "d1", "items": [{"text": "one"}]}).ok
    # The session itself, and a view with no context, undo as before: whoever made it.
    assert toolbox.dispatch(shared.view(roles=["review", "edit"]), "undo", {"doc": "d1"}).ok
    assert _items(shared) == before
    assert toolbox.dispatch(shared, "undo", {"doc": "d1", "redo": True}).ok
    assert toolbox.dispatch(shared, "undo", {"doc": "d1"}).ok and _items(shared) == before
    assert toolbox.dispatch(shared.view(roles=["review"]), "undo",
                            {"doc": "d1"}).error.code == "refused"


# -- undo by caller (round 9) --------------------------------------------------------------------


def _builders(shared, count=2):
    return [shared.view(roles=["review", "edit"], context={"agent": f"builder-{n}"})
            for n in range(1, count + 1)]


def _add(toolbox, view, text, doc="d1"):
    result = toolbox.dispatch(view, "t_add", {"doc": doc, "items": [{"text": text}]})
    assert result.ok, result.error
    return result


def test_a_view_s_undo_takes_back_its_own_latest_change(toolbox, shared):
    first, second = _builders(shared)
    entry = shared.entry("d1")
    before = _items(shared)
    made = _add(toolbox, first, "one")
    _add(toolbox, second, "two")
    assert entry.owners[made.version].caller == {"agent": "builder-1"}
    assert entry.owners[made.version].tool == "t_add"
    undone = toolbox.dispatch(second, "undo", {"doc": "d1"})
    assert undone.ok and undone.data["own"] and undone.summary == "Undid 1 of your own step(s) of d1"
    assert _items(shared) == before + ["one"]
    # builder-1's change is the latest again: its undo takes it back.
    assert toolbox.dispatch(first, "undo", {"doc": "d1"}).ok and _items(shared) == before
    nothing = toolbox.dispatch(first, "undo", {"doc": "d1"})
    assert nothing.error.code == "refused" and "nothing of yours" in nothing.error.message
    assert "your own changes only" in nothing.error.message
    # Redo is its own too: each brings back its own.
    assert toolbox.dispatch(first, "undo", {"doc": "d1", "redo": True}).ok
    assert toolbox.dispatch(second, "undo", {"doc": "d1", "redo": True}).ok
    assert _items(shared) == before + ["one", "two"]


def test_a_view_s_undo_is_refused_when_another_s_later_change_depends_on_it(toolbox, shared):
    first, second = _builders(shared)
    _add(toolbox, first, "one")
    later = _add(toolbox, second, "two")
    before = shared.entry("d1").document.to_bytes()
    refused = toolbox.dispatch(first, "undo", {"doc": "d1"})
    assert refused.error.code == "entangled"
    message = refused.error.message
    assert "your latest change to d1 (t_add" in message and "is not the latest" in message
    assert "t_add by agent=builder-2" in message and f"version {later.version}" in message
    assert "Change it back with an edit" in message
    assert refused.error.details["later"] == [{"version": later.version, "tool": "t_add",
                                               "context": {"agent": "builder-2"}}]
    assert shared.entry("d1").document.to_bytes() == before       # nothing changed
    # Once builder-2 took its change back, builder-1's is the latest and can go.
    assert toolbox.dispatch(second, "undo", {"doc": "d1"}).ok
    assert toolbox.dispatch(first, "undo", {"doc": "d1"}).ok


def test_a_per_call_context_undoes_its_own_and_steps_stop_at_the_first_refusal(toolbox, shared):
    first, second = _builders(shared)
    before = _items(shared)
    _add(toolbox, first, "a")
    _add(toolbox, second, "b")
    _add(toolbox, first, "c")
    mine = {"agent": "builder-1"}
    undone = toolbox.dispatch(shared, "undo", {"doc": "d1", "steps": 3}, context=mine)
    assert undone.ok and undone.data["steps"] == 1 and _items(shared) == before + ["a", "b"]
    assert undone.warnings == ["1 of 3 step(s): no more of yours, or the next depends on a "
                               "later change by another caller"]
    with pytest.raises(ToolError) as python:                    # the view's own undo, in Python
        shared.view(context=mine).undo("d1")
    assert python.value.code == "entangled"
    assert toolbox.dispatch(shared, "undo", {"doc": "d1"},
                            context=mine).error.code == "entangled"
    # A batch is one step of its caller's, made by "batch".
    made = toolbox.dispatch(second, "batch", {"ops": [
        {"tool": "t_add", "arguments": {"doc": "d1", "items": [{"text": "x"}]}},
        {"tool": "t_add", "arguments": {"doc": "d1", "items": [{"text": "y"}]}}]})
    assert made.ok and shared.entry("d1").owners[made.version].tool == "batch"
    assert toolbox.dispatch(second, "undo", {"doc": "d1", "steps": 2}).data["steps"] == 2
    assert _items(shared) == before + ["a"]


def test_a_view_s_redo_is_refused_while_another_s_undo_comes_first(toolbox, shared):
    first, second = _builders(shared)
    before = _items(shared)
    _add(toolbox, first, "one")
    _add(toolbox, second, "two")
    assert toolbox.dispatch(second, "undo", {"doc": "d1"}).ok
    assert toolbox.dispatch(first, "undo", {"doc": "d1"}).ok and _items(shared) == before
    refused = toolbox.dispatch(second, "undo", {"doc": "d1", "redo": True})
    assert refused.error.code == "entangled" and "not the next to redo" in refused.error.message
    assert toolbox.dispatch(first, "undo", {"doc": "d1", "redo": True}).ok
    assert toolbox.dispatch(second, "undo", {"doc": "d1", "redo": True}).ok
    # A new change drops what was undone, as for the session.
    assert toolbox.dispatch(second, "undo", {"doc": "d1"}).ok
    _add(toolbox, first, "three")
    nothing = toolbox.dispatch(second, "undo", {"doc": "d1", "redo": True})
    assert nothing.error.code == "refused" and "nothing of yours to redo" in nothing.error.message


@pytest.fixture
def deck(shared):
    shared.open(synthetic.outer_package(), "deck.pptx")      # d2
    return shared


def _titles(session, doc="d2"):
    document = session.entry(doc).document
    return [document.title(n) for n in (1, 2)]


def _title(toolbox, view, page, text, **more):
    result = toolbox.dispatch(view, "t_title", {"doc": "d2", "page": page, "text": text,
                                                **more})
    assert result.ok, result.error
    return result


def test_in_a_format_with_scopes_a_view_takes_out_its_own_change_under_others(toolbox, deck):
    import tools_toys

    tools_toys.RESTORED.clear()
    first, second = _builders(deck)
    entry = deck.entry("d2")
    _title(toolbox, first, "1", "A", ref="mine")
    _title(toolbox, second, "2", "B", ref="theirs")
    undone = toolbox.dispatch(first, "undo", {"doc": "d2"})
    assert undone.ok and _titles(deck) == [None, "B"]
    assert tools_toys.RESTORED == [id(entry.document)] and entry.refs == {"theirs": "page:2"}
    # builder-2's step kept its owner under its new version.
    assert entry.owners[entry.version].caller == {"agent": "builder-2"}
    assert toolbox.dispatch(first, "undo", {"doc": "d2", "redo": True}).ok
    assert _titles(deck) == ["A", "B"] and entry.refs == {"mine": "page:1", "theirs": "page:2"}
    assert toolbox.dispatch(second, "undo", {"doc": "d2"}).ok and _titles(deck) == ["A", None]
    assert toolbox.dispatch(first, "undo", {"doc": "d2"}).ok and _titles(deck) == [None, None]
    # The same page changed later by another: refused, naming them.
    _title(toolbox, first, "1", "C")
    later = _title(toolbox, second, "1", "D")
    refused = toolbox.dispatch(first, "undo", {"doc": "d2"})
    assert refused.error.code == "entangled" and _titles(deck) == ["D", None]
    assert "t_title by agent=builder-2" in refused.error.message
    assert refused.error.details["later"][0]["version"] == later.version
    assert refused.error.details["shared"] == [synthetic.PAGE1]
    # A scope narrows it to its own changes there.
    _title(toolbox, first, "2", "E")
    assert toolbox.dispatch(first, "undo", {"doc": "d2", "scope": "1"}).error.code == "entangled"
    assert toolbox.dispatch(first, "undo", {"doc": "d2", "scope": "2"}).ok
    assert _titles(deck) == ["D", None]


def test_a_view_s_change_to_what_every_page_shares_is_its_own_only_while_latest(toolbox):
    import dataclasses

    from test_tools_shared import DECK as TOY_DECK

    deck_format = dataclasses.replace(TOY_DECK, undo_shared=lambda document: [synthetic.MAIN])
    with Toolbox(DECK_TOOLS, formats=[deck_format], groups=GROUPS) as box:
        session = box.session(clock=CLOCK)
        session.open(synthetic.outer_package(), "deck.pptx")      # d1
        first, second = _builders(session)
        entry = session.entry("d1")

        def shared_change():
            with entry.batch(owner=_owner(first)):
                entry.document.insert("x", "end")      # the part every page shows

        shared_change()
        assert box.dispatch(first, "undo", {"doc": "d1"}).ok     # the latest: plain undo
        shared_change()
        assert box.dispatch(second, "t_title", {"doc": "d1", "page": "2", "text": "B"}).ok
        refused = box.dispatch(first, "undo", {"doc": "d1"})
        assert refused.error.code == "entangled" and "other pages show" in refused.error.message


def _owner(view):
    from ooxml_edit.tools import StepOwner

    return StepOwner(dict(view.context), "app")


def test_a_change_made_outside_the_tools_is_no_caller_s(toolbox, shared):
    first, = _builders(shared, 1)
    _add(toolbox, first, "one")
    shared.entry("d1").document.insert("direct", "end")       # the library's API, no call
    refused = toolbox.dispatch(first, "undo", {"doc": "d1"})
    assert refused.error.code == "entangled"
    assert "a change made outside the tools" in refused.error.message
    assert refused.error.details["later"] == [{"version": shared.entry("d1").version}]
    session_made = toolbox.dispatch(shared, "t_add", {"doc": "d1", "items": [{"text": "s"}]})
    assert shared.entry("d1").owners[session_made.version].caller == {}
    assert "(a call with no caller context)" in toolbox.dispatch(
        first, "undo", {"doc": "d1"}).error.message


def test_views_undo_their_own_in_threads_and_never_another_s(toolbox, shared):
    """Builders on one document at once, each adding and undoing its own: an undo either
    takes back that builder's item or is refused because another's came after -- never
    another's item."""
    views = _builders(shared, 4)
    before = _items(shared)
    kept: list[str] = []
    undone: list[str] = []
    errors = []
    lock = threading.Lock()

    def build(view, n):
        for k in range(12):
            text = f"{n}-{k}"
            if not toolbox.dispatch(view, "t_add", {"doc": "d1", "items": [{"text": text}]}).ok:
                errors.append("add failed")
                continue
            result = toolbox.dispatch(view, "undo", {"doc": "d1"})
            with lock:
                if result.ok:
                    undone.append(text)
                elif result.error.code == "entangled":
                    kept.append(text)
                else:
                    errors.append(result.error)

    threads = [threading.Thread(target=build, args=(view, n)) for n, view in enumerate(views)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors and len(kept) + len(undone) == 48
    assert sorted(_items(shared)[len(before):]) == sorted(kept)
    assert _items(shared)[:len(before)] == before


def test_views_take_out_their_own_page_changes_in_threads(toolbox, deck):
    first, second = _builders(deck)
    errors = []

    def build(view, page):
        for k in range(15):
            if not toolbox.dispatch(view, "t_title", {"doc": "d2", "page": page,
                                                      "text": f"{page}-{k}"}).ok:
                errors.append("title failed")
            result = toolbox.dispatch(view, "undo", {"doc": "d2"})
            if not result.ok:
                errors.append(result.error)

    threads = [threading.Thread(target=build, args=(first, "1")),
               threading.Thread(target=build, args=(second, "2"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors and _titles(deck) == [None, None]


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
