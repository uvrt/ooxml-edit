"""``read_batch`` (round 32, R15): many reading calls in one, each answered as if called
alone with its own size budget, in the ``review`` role, never an undo step or checks; what
does not fit the reply is listed with the call that asks for it.  Made-up tools only."""

from __future__ import annotations

import json

import pytest

from ooxml_edit.tools import (REVIEW, Limits, Result, Toolbox, shared, string, tool)
from ooxml_edit.tools.results import page_text

import synthetic
from test_tools_shared import CHECKS, CLOCK, DECK, DECK_TOOLS, TEXT, TEXT_TOOLS, _ops

#: A made-up record set to read a page at a time: forty records of about 400 characters.
RECORDS = "\n".join(f"record {n}: " + "lorem ipsum dolor " * 22 for n in range(40)) + "\n"


@tool("t_records", "Read the records, a page at a time.",
      {"doc": string("Document id."), "cursor": string("next_cursor.", optional=True)},
      kind="docx")
def t_records(call, doc, cursor=None):
    page, next_cursor = page_text(RECORDS, cursor=cursor,
                                  limit=call.limits.max_result_chars - 500)
    return Result(summary=f"{len(page)} characters", data={"text": page},
                  next_cursor=next_cursor)


@tool("t_record", "Read one record.",
      {"doc": string("Document id."), "n": string("Record number.")}, kind="docx")
def t_record(call, doc, n):
    return {"n": n, "text": RECORDS.splitlines()[int(n)]}


@tool("t_box", "Read or set a box's text.",
      {"doc": string("Document id."),
       "action": string("What to do; read changes nothing.", enum=["read", "set"]),
       "text": string("set: the text.", optional=True)},
      kind="docx", mutates=True, reads=lambda arguments: arguments.get("action") == "read",
      action_roles={"read": REVIEW})
def t_box(call, doc, action, text=None):
    if action == "set":
        call.document.insert(text or "", "end")
    return Result(summary=f"box {action}")


READ_TOOLS = [t_records, t_record, t_box, shared.READ_BATCH]


@pytest.fixture
def toolbox():
    CHECKS.clear()
    with Toolbox(TEXT_TOOLS + DECK_TOOLS + READ_TOOLS, formats=[DECK, TEXT],
                 groups=shared.GROUPS, workers=0) as box:
        yield box


def _session(toolbox, **limits):
    session = toolbox.session(clock=CLOCK, limits=Limits(**limits) if limits else None)
    session.open(synthetic.outer_package(), "text.docx")      # d1
    session.open(synthetic.outer_package(), "deck.pptx")      # d2
    return session


@pytest.fixture
def session(toolbox):
    return _session(toolbox)


def _reads(n, doc="d1"):
    return _ops(*[("t_record", {"doc": doc, "n": str(i)}) for i in range(n)])


# -- the definition --------------------------------------------------------------------------


def test_read_batch_is_a_readers_tool_that_changes_nothing_and_says_it_is_for_many_reads():
    read_batch = shared.READ_BATCH
    assert read_batch.composite and not read_batch.mutates and not read_batch.strict
    assert read_batch.roles == {REVIEW} and read_batch.core
    assert "many" in read_batch.description and "Changes nothing" in read_batch.description
    assert "undo" not in read_batch.description
    assert read_batch not in shared.SESSION_TOOLS     # a library lists it itself
    assert shared.definition("read_batch").same_definition(read_batch)


def test_a_reviewer_is_sent_read_batch_and_told_of_it(toolbox):
    names = [tool["name"] for tool in toolbox.definitions("anthropic", roles=["review"])]
    assert "read_batch" in names
    assert "read_batch makes many reads" in toolbox.system_prompt(roles=["review"])
    assert "read_batch" in toolbox.system_prompt()
    # Not where it is not sent: a toolbox without it, or tools sent without it.
    assert "read_batch" not in toolbox.system_prompt(tools=["t_record", "batch"])
    with Toolbox(TEXT_TOOLS, formats=[TEXT], groups=shared.GROUPS, workers=0) as plain:
        assert "read_batch" not in plain.system_prompt()


# -- reading -------------------------------------------------------------------------------


def test_each_op_is_answered_as_if_called_alone_with_no_undo_step_and_no_checks(toolbox,
                                                                              session):
    alone = toolbox.dispatch(session, "t_record", {"doc": "d1", "n": "3"})
    before = {doc: session.entry(doc).version for doc in ("d1", "d2")}
    result = toolbox.dispatch(session, "read_batch", {"ops": _ops(
        ("t_record", {"doc": "d1", "n": "3"}), ("t_box", {"doc": "d1", "action": "read"}),
        ("t_record", {"doc": "d1", "n": "4"}))})
    assert result.ok and result.tool == "read_batch"
    answers = result.data["ops"]
    assert [answer["op"] for answer in answers] == [0, 1, 2]
    assert answers[0]["data"] == alone.data and answers[0]["doc"] == "d1"
    assert answers[1]["summary"] == "box read"
    assert "changed" not in answers[0] and "ok" not in answers[0]
    assert {doc: session.entry(doc).version for doc in ("d1", "d2")} == before
    assert not session.entry("d1").history.can_undo()
    assert CHECKS == [] and result.checks == {}
    assert result.summary.startswith("Read 3 of 3 op(s)")


def test_a_changing_op_is_refused_with_the_way_out_and_the_others_still_run(toolbox,
                                                                            session):
    result = toolbox.dispatch(session, "read_batch", {"ops": _ops(
        ("t_record", {"doc": "d1", "n": "1"}),
        ("t_box", {"doc": "d1", "action": "set", "text": "new"}),
        ("t_add", {"doc": "d1", "items": [{"text": "x"}]}),
        ("t_record", {"doc": "d1", "n": "2"}))})
    assert result.ok
    first, box, add, last = result.data["ops"]
    assert first["data"]["n"] == "1" and last["data"]["n"] == "2"
    assert box["error"]["code"] == "refused" and box["error"]["field"] == "tool"
    assert "send this op in batch" in box["error"]["message"]
    assert box["error"]["valid_options"] == ["batch", "t_box"]
    assert add["error"]["code"] == "refused"
    assert session.entry("d1").document.items() == []          # nothing changed
    assert "2 failed: ops[1] (t_box)" in result.summary


def test_an_unknown_or_unbatchable_op_names_read_batch_and_the_reading_tools(toolbox,
                                                                            session):
    result = toolbox.dispatch(session, "read_batch", {"ops": _ops(
        ("zz_quux", {}), ("batch", {"ops": []}), ("read_batch", {"ops": []}),
        ("save_document", {"doc": "d1", "name": "a.docx", "format": "docx"}))})
    unknown, batch, nested, save = (answer["error"] for answer in result.data["ops"])
    assert "that read_batch can run" in unknown["message"]
    assert "t_add" not in unknown["valid_options"] and "t_record" in unknown["valid_options"]
    for error in (batch, nested, save):
        assert "cannot run inside a read_batch; call it on its own" in error["message"]


def test_a_reviewer_reads_in_one_call_and_an_edit_is_refused_by_role(toolbox, session):
    reviewer = session.view(roles=["review"], context={"agent": "reviewer-1"})
    result = toolbox.dispatch(reviewer, "read_batch", {"ops": _ops(
        ("t_record", {"doc": "d1", "n": "1"}), ("t_box", {"doc": "d1", "action": "read"}),
        ("t_set", {"doc": "d1", "target": "item:1", "text": "x"}))})
    assert result.ok
    read, box, edit = result.data["ops"]
    assert "error" not in read and "error" not in box
    assert edit["error"]["details"]["role"] == "edit"


def test_reads_of_several_documents_and_a_closed_one_fail_alone(toolbox, session):
    result = toolbox.dispatch(session, "read_batch", {"ops": _ops(
        ("t_record", {"doc": "d1", "n": "1"}), ("t_record", {"doc": "d9", "n": "1"}))})
    assert result.data["ops"][0]["doc"] == "d1"
    assert result.data["ops"][1]["error"]["code"] == "not_found"


# -- budgets -------------------------------------------------------------------------------


def test_each_op_keeps_its_own_budget_and_the_reply_is_not_cut_to_one_calls(toolbox):
    session = _session(toolbox, max_result_chars=2_000, max_read_batch_chars=20_000)
    result = toolbox.dispatch(session, "read_batch", {"ops": _reads(30)})
    assert [answer["op"] for answer in result.data["ops"]] == list(range(30))
    assert "not_returned" not in result.data
    text = result.to_text(2_000)            # what a sender with one call's limit sends
    assert len(text) > 2_000 and "truncated" not in text
    assert len(text) <= 20_000
    sent = toolbox.render_result("anthropic", result, "call-1")
    assert json.loads(sent["content"][0]["text"])["data"]["ops"][29]["op"] == 29


def test_what_does_not_fit_is_listed_with_the_call_that_asks_for_it(toolbox):
    session = _session(toolbox, max_result_chars=2_000, max_read_batch_chars=6_000)
    ops = _reads(30)
    result = toolbox.dispatch(session, "read_batch", {"ops": ops})
    returned = [answer["op"] for answer in result.data["ops"]]
    left = [item["op"] for item in result.data["not_returned"]]
    assert returned and left and sorted(returned + left) == list(range(30))
    assert all("budget" in item["why"] for item in result.data["not_returned"])
    assert result.data["resend"] == {"tool": "read_batch",
                                     "arguments": {"ops": [ops[i] for i in left]}}
    assert "send data.resend" in result.summary
    assert len(result.to_text(2_000)) <= 6_000
    # Resent, they come back.
    again = toolbox.dispatch(session, "read_batch", result.data["resend"]["arguments"])
    assert [answer["op"] for answer in again.data["ops"]][:1] == [0]


def test_an_op_over_its_own_budget_is_cut_as_alone_and_a_paged_one_has_its_next_call(
        toolbox):
    session = _session(toolbox, max_result_chars=3_000, max_read_batch_chars=20_000)
    alone = toolbox.dispatch(session, "t_records", {"doc": "d1"})
    result = toolbox.dispatch(session, "read_batch", {"ops": _ops(
        ("t_records", {"doc": "d1"}), ("t_record", {"doc": "d1", "n": "0"}))})
    paged = result.data["ops"][0]
    assert paged["next_cursor"] == alone.next_cursor
    assert paged["next"] == {"tool": "t_records",
                             "arguments": {"doc": "d1", "cursor": alone.next_cursor}}
    nxt = toolbox.dispatch(session, "t_records", paged["next"]["arguments"])
    assert nxt.ok and nxt.data["text"].startswith("record ")


def test_ops_past_the_deadline_are_listed_not_run(toolbox):
    session = _session(toolbox, batch_timeout=0.0)
    result = toolbox.dispatch(session, "read_batch", {"ops": _reads(3)})
    assert result.data["ops"] == []
    assert [item["op"] for item in result.data["not_returned"]] == [0, 1, 2]
    assert "deadline" in result.data["not_returned"][0]["why"]


def test_more_ops_than_a_batch_takes_is_refused_saying_what_to_do(toolbox, session):
    result = toolbox.dispatch(session, "read_batch", {"ops": _reads(201)})
    assert not result.ok and result.error.code == "limit"
    assert "another" in result.error.message
