"""Round 53, R109: check's ``validation`` is the save gate's own comparison (:mod:`ooxml_edit.
tools.savegate`), on toy formats: what a check lists as new is what the save refuses, whatever
the check was asked; kept per version, never stale inside a batch."""

from __future__ import annotations

import dataclasses
import datetime as dt

from ooxml_edit.tools import (Result, Toolbox, compare_problems, problems_now, shared,
                              validation)
from ooxml_edit.tools.savegate import LISTED, validation_fact

import synthetic
import tools_toys

CLOCK = lambda: dt.datetime(2026, 11, 12, 12, 0, tzinfo=dt.timezone.utc)  # noqa: E731

#: Each time the deck format's problems were found.
FOUND: list[int] = []


def _problems(document):
    FOUND.append(1)
    return tools_toys.ToyDocument.problems(document)


DECK = dataclasses.replace(tools_toys.DECK, problems=_problems)
KEPT: dict = {"facts": []}


@shared.handler("check", kind="pptx")
def deck_check(call, doc, slides=None, pages=None, include=None, boxes=None, spacing=None,
               contrast=None):
    """A check that reports layout facts alone -- silent on validation itself -- and hands
    back the data it keeps, as pptx-agent's cached check does."""
    return Result(summary="Checked fit", data=KEPT)


TOOLS = [deck_check, tools_toys.toy_ppt_set_title, tools_toys.toy_save_deck,
         *shared.SESSION_TOOLS]


def _box(**options):
    return Toolbox(TOOLS, formats=[DECK], **options)


def _session(box, titles=("One", "Two")):
    session = box.session(clock=CLOCK)
    session.open(synthetic.outer_package(), "deck.pptx")
    for page, title in enumerate(titles, 1):
        if title is not None:
            assert box.dispatch(session, "toy_ppt_set_title",
                                {"doc": "d1", "page": page, "text": title}).ok
    return session


def _save(box, session):
    return box.dispatch(session, "toy_save", {"doc": "d1", "name": "o.pptx", "format": "pptx"})


def test_check_lists_what_the_save_refuses_whatever_it_was_asked():
    with _box() as box:
        session = _session(box)
        for page in (1, 2):
            box.dispatch(session, "toy_ppt_set_title", {"doc": "d1", "page": page, "text": ""})
        checked = box.dispatch(session, "check", {"doc": "d1", "slides": ["1"],
                                                  "include": ["fit"]})
        refused = _save(box, session)
    assert checked.ok and refused.error.code == "refused"
    fact = checked.data["validation"]
    # the same problems, in the same words, the save refuses: one code path
    assert fact["new"] == refused.error.valid_options == [
        "page 1 has an empty title", "page 2 has an empty title"]
    assert fact["new"] == refused.error.details["validate"]["new"]
    assert fact["baseline"] == refused.error.details["validate"]["baseline"]
    assert "saving would refuse 2 new validation problem(s)" in checked.summary
    assert "cut" not in fact and "fixed" not in fact


def test_after_the_fix_check_says_none_new_and_the_save_passes():
    with _box() as box:
        session = _session(box)
        box.dispatch(session, "toy_ppt_set_title", {"doc": "d1", "page": 2, "text": ""})
        assert box.dispatch(session, "check", {"doc": "d1"}).data["validation"]["new"]
        box.dispatch(session, "toy_ppt_set_title", {"doc": "d1", "page": 2, "text": "Back"})
        checked = box.dispatch(session, "check", {"doc": "d1"})
        saved = _save(box, session)
    assert checked.data["validation"] == {"new": [], "baseline": 0}
    assert checked.summary == "Checked fit"
    assert saved.ok and saved.data["validate"]["new"] == []


def test_baseline_problems_are_counted_and_fixed_ones_named():
    with _box() as box:
        session = box.session(clock=CLOCK)
        session.open(synthetic.outer_package(), "deck.pptx")
        box.dispatch(session, "toy_ppt_set_title", {"doc": "d1", "page": 1, "text": "One"})
        box.dispatch(session, "toy_ppt_set_title", {"doc": "d1", "page": 2, "text": "Two"})
        data = synthetic.outer_package()
        # a deck that arrives with a problem: an empty title on page 2
        other = box.session(clock=CLOCK)
        other.open(data, "deck.pptx")
        box.dispatch(other, "toy_ppt_set_title", {"doc": "d1", "page": 2, "text": ""})
        broken = other.entry("d1").document.to_bytes()
        third = box.session(clock=CLOCK)
        third.open(broken, "deck.pptx")
        before = box.dispatch(third, "check", {"doc": "d1"}).data["validation"]
        box.dispatch(third, "toy_ppt_set_title", {"doc": "d1", "page": 2, "text": "Two"})
        after = box.dispatch(third, "check", {"doc": "d1"}).data["validation"]
        assert _save(box, third).ok
    assert before == {"new": [], "baseline": 1}
    assert after == {"new": [], "baseline": 1, "fixed": ["page 2 has an empty title"]}


def test_problems_are_found_once_per_version_and_afresh_by_the_gate():
    with _box() as box:
        session = _session(box)
        FOUND.clear()
        for _ in range(3):
            box.dispatch(session, "check", {"doc": "d1"})
        assert len(FOUND) == 1                     # one version, found once
        box.dispatch(session, "toy_ppt_set_title", {"doc": "d1", "page": 1, "text": ""})
        assert box.dispatch(session, "check", {"doc": "d1"}).data["validation"]["new"] == [
            "page 1 has an empty title"]           # a new version: found again
        assert len(FOUND) == 2
        assert _save(box, session).error.code == "refused"
        assert len(FOUND) == 3                     # the gate never trusts a kept list
        box.dispatch(session, "check", {"doc": "d1"})
        assert len(FOUND) == 3                     # ...and keeps what it found


def test_a_kept_list_is_never_read_inside_a_batch():
    """A batch's ops change the document before its version moves: a check op after an
    edit op sees the edit's problem, and nothing it found is kept for the version."""
    with _box() as box:
        session = _session(box)
        box.dispatch(session, "check", {"doc": "d1"})          # kept for this version
        result = box.dispatch(session, "batch", {"ops": [
            {"tool": "toy_ppt_set_title", "arguments": {"doc": "d1", "page": 1, "text": ""}},
            {"tool": "check", "arguments": {"doc": "d1"}}]})
        assert result.ok, result.to_json()
        inside = result.data["ops"][1]["data"]["validation"]
        after = box.dispatch(session, "check", {"doc": "d1"}).data["validation"]
    assert inside["new"] == ["page 1 has an empty title"]
    assert after["new"] == ["page 1 has an empty title"]


def test_the_kinds_kept_data_is_not_changed():
    with _box() as box:
        session = _session(box)
        box.dispatch(session, "check", {"doc": "d1"})
    assert KEPT == {"facts": []}


def test_a_kind_without_problems_gets_no_validation():
    plain = dataclasses.replace(tools_toys.DECK, problems=None)
    with Toolbox(TOOLS, formats=[plain]) as box:
        session = _session(box)
        checked = box.dispatch(session, "check", {"doc": "d1"})
    assert checked.ok and "validation" not in checked.data


def test_the_listing_has_a_budget_and_says_what_it_left_out():
    report = {"new": [f"problem {n}" for n in range(LISTED + 7)], "fixed": [], "baseline": 3}
    fact = validation_fact(report)
    assert len(fact["new"]) == LISTED and fact["cut"] == 7 and fact["baseline"] == 3


def test_the_comparison_is_by_the_formats_key():
    baseline = [("a", "part1"), ("b", "part1")]
    now = [("a", "part2"), ("c", "part1")]
    by_text = compare_problems(baseline, now, key=lambda p: p[0])
    assert by_text == {"new": ["('c', 'part1')"], "fixed": ["('b', 'part1')"], "baseline": 2}
    assert compare_problems(baseline, now)["new"] == [str(p) for p in now]


def test_validation_reads_given_problems_and_otherwise_the_kept_ones():
    with _box() as box:
        session = _session(box)
        entry = session.entry("d1")
        FOUND.clear()
        assert validation(entry, DECK, problems=["x"]) == {"new": ["x"], "fixed": [],
                                                             "baseline": 0}
        assert not FOUND
        assert problems_now(entry, DECK) == [] and problems_now(entry, DECK) == []
        assert len(FOUND) == 1
        assert problems_now(entry, DECK, cache=False) == [] and len(FOUND) == 2
        assert problems_now(entry, None) == []


def test_the_baseline_found_at_open_is_kept_for_the_first_check():
    with _box() as box:
        session = box.session(clock=CLOCK)
        FOUND.clear()
        session.open(synthetic.outer_package(), "deck.pptx")
        assert len(FOUND) == 1                     # the baseline
        box.dispatch(session, "check", {"doc": "d1"})
        assert len(FOUND) == 1                     # the same version: kept
