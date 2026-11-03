"""A batch's cost (round 28, W8): work an op defers until the document is as the batch
leaves it (``Call.defer``), and a batch past its deadline saying what it did not run."""

from __future__ import annotations

import datetime as dt

import pytest

from ooxml_edit.tools import Limits, Result, Toolbox, array, obj, shared, string, tool
from ooxml_edit.tools import dispatch as _dispatch

import synthetic
import tools_toys

CLOCK = lambda: dt.datetime(2026, 11, 3, 12, 0, tzinfo=dt.timezone.utc)  # noqa: E731

#: What the deferred work saw: the op's text and the document's items then.
SEEN: list[tuple[str, int]] = []


@tool("t_note", "Add an item; say how many there are once the document is as left.",
      {"doc": string("Document id."), "text": string("Item text."),
       "after": string("A warning said after.", optional=True)},
      kind="docx", mutates=True)
def t_note(call, doc, text, after=None):
    call.document.insert(text, "end")
    result = Result(summary=f"Added {text}", warnings=[f"{text}: first"])

    def count(result: Result) -> None:
        items = len(call.document.items())
        SEEN.append((text, items))
        if text == "boom":
            raise ValueError("cannot count")
        at = result.warnings.index(after) if after in result.warnings else len(result.warnings)
        result.warnings.insert(at, f"{text}: {items} items")

    call.defer(count)
    if after:
        result.warnings.append(after)
    return result


@tool("t_plain", "Add items.",
      {"doc": string("Document id."), "items": array(obj({"text": string("Text.")}), "Items.")},
      kind="docx", mutates=True)
def t_plain(call, doc, items):
    for item in items:
        call.document.insert(item["text"], "end")
    return Result(summary=f"Added {len(items)}")


@pytest.fixture
def toolbox():
    with Toolbox([t_note, t_plain, *shared.SESSION_TOOLS], formats=[tools_toys.TEXT],
                 groups=shared.GROUPS, workers=0) as box:
        yield box


def _session(toolbox, **limits):
    session = toolbox.session(clock=CLOCK, limits=Limits(**limits) if limits else None)
    session.open(synthetic.outer_package(), "text.docx")
    SEEN.clear()
    return session


def _note(text, after=None):
    arguments = {"doc": "d1", "text": text}
    if after:
        arguments["after"] = after
    return {"tool": "t_note", "arguments": arguments}


def test_a_call_alone_does_its_deferred_work_as_it_returns(toolbox):
    session = _session(toolbox)
    before = len(session.entry("d1").document.items())
    result = toolbox.dispatch(session, "t_note", {"doc": "d1", "text": "a", "after": "a: last"})
    assert result.ok and result.warnings == ["a: first", f"a: {before + 1} items", "a: last"]


def test_a_batch_does_its_ops_deferred_work_after_the_last_op_in_their_places(toolbox):
    session = _session(toolbox)
    before = len(session.entry("d1").document.items())
    result = toolbox.dispatch(session, "batch", {"ops": [
        _note("a", after="a: last"), {"tool": "t_plain", "arguments": {"doc": "d1", "items": [{"text": "x"}]}},
        _note("b")]})
    assert result.ok
    final = before + 3
    assert SEEN == [("a", final), ("b", final)]          # once the batch is done, in op order
    assert result.warnings == ["ops[0]: a: first", f"ops[0]: a: {final} items", "ops[0]: a: last",
                               "ops[2]: b: first", f"ops[2]: b: {final} items"]


def test_without_batch_defers_each_op_does_its_work_after_it(toolbox, monkeypatch):
    monkeypatch.setattr(toolbox, "batch_defers", False)
    session = _session(toolbox)
    before = len(session.entry("d1").document.items())
    result = toolbox.dispatch(session, "batch", {"ops": [_note("a"), _note("b")]})
    assert SEEN == [("a", before + 1), ("b", before + 2)]
    assert result.warnings == ["ops[0]: a: first", f"ops[0]: a: {before + 1} items",
                               "ops[1]: b: first", f"ops[1]: b: {before + 2} items"]


def test_deferred_work_that_fails_is_a_warning_and_the_op_applies(toolbox):
    session = _session(toolbox)
    before = len(session.entry("d1").document.items())
    result = toolbox.dispatch(session, "batch", {"ops": [_note("boom"), _note("b")]})
    assert result.ok and len(session.entry("d1").document.items()) == before + 2
    assert result.warnings[:2] == ["ops[0]: boom: first",
                                   "ops[0]: t_note: what it says once it is done could not be found: "
                                   "cannot count"]
    alone = toolbox.dispatch(session, "t_note", {"doc": "d1", "text": "boom"})
    assert alone.ok and alone.warnings[-1].endswith("could not be found: cannot count")


def test_a_failed_op_defers_nothing(toolbox):
    session = _session(toolbox)
    result = toolbox.dispatch(session, "batch", {"ops": [_note("a"), {"tool": "t_note", "arguments": {}}]})
    assert result.ok and [text for text, _ in SEEN] == ["a"] and result.data["failed"] == [1]


class _Clock:
    """``time.perf_counter`` a second further on at every reading."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


def test_a_batch_past_its_deadline_says_how_many_ops_were_not_run_and_a_size_that_fits(
        toolbox, monkeypatch):
    session = _session(toolbox, batch_timeout=10.0)
    monkeypatch.setattr(_dispatch.time, "perf_counter", _Clock())
    ops = [{"tool": "t_plain", "arguments": {"doc": "d1", "items": [{"text": f"x{k}"}]}} for k in range(40)]
    ops[2] = {"tool": "t_plain", "arguments": {"doc": "d1"}}            # fails: no items
    result = toolbox.dispatch(session, "batch", {"ops": ops})
    assert result.ok
    not_run = result.data["not_run"]
    ran = not_run["ran"]
    assert not_run == {"count": 40 - ran, "first": ran, "last": 39, "ran": ran,
                       "seconds": not_run["seconds"], "batch_size": not_run["batch_size"]}
    assert 0 < ran < 40 and not_run["seconds"] > 10
    # 80 % of the deadline at the rate the ops ran.
    assert not_run["batch_size"] == max(1, int(10.0 * 0.8 / (not_run["seconds"] / ran)))
    assert result.data["failed"] == [2, *range(ran, 40)]
    assert "fix and resend only these: ops[2] (t_plain)" in result.summary
    assert (f"Not run: {40 - ran} op(s), ops[{ran}]..ops[39], after the batch passed its 10 s deadline"
            in result.summary)
    assert f"resend them as batches of at most {not_run['batch_size']} ops" in result.summary
    assert all(op["error"]["message"] == "not run: the batch passed its 10 s deadline"
               for op in result.data["ops"][ran:])


def test_an_atomic_batch_past_its_deadline_says_the_same(toolbox, monkeypatch):
    session = _session(toolbox, batch_timeout=10.0)
    before = session.entry("d1").document.items()
    monkeypatch.setattr(_dispatch.time, "perf_counter", _Clock())
    ops = [{"tool": "t_plain", "arguments": {"doc": "d1", "items": [{"text": f"x{k}"}]}} for k in range(40)]
    result = toolbox.dispatch(session, "batch", {"ops": ops, "atomic": True})
    assert not result.ok and result.error.code == "timeout"
    not_run = result.error.details["not_run"]
    assert not_run["first"] == result.error.details["op"] and not_run["last"] == 39
    assert "nothing in the batch was applied" in result.error.message
    assert f"resend them as batches of at most {not_run['batch_size']} ops" in result.error.message
    assert session.entry("d1").document.items() == before
