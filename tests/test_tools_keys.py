"""Round 52: ``loose_key``, the one rule for keys compared loosely, held to a table of pairs
that must match and pairs that must not; and ``orMap``, pairs written as one object.  Made-up
key shapes only."""

from __future__ import annotations

import json

import pytest

from ooxml_edit.tools import array, loose_key, obj, string, tool, validate_call
from ooxml_edit.tools.schema import CallError, check_subset, subset_problems

MATCH = [
    # case
    ("K7-12.3", "k7-12.3"),
    ("DOC-4.2", "doc-4.2"),
    # separators between number groups, and between letters and numbers
    ("K7-12.3", "K7-12-3"),
    ("K7-12.3", "K7_12_3"),
    ("K7-12.3", "K7/12/3"),
    ("K7-12", "K7 12"),
    ("K7", "K-7"),
    ("K7", "K 7"),
    # leading zeros: numbers compared as numbers
    ("K7-12.3", "k7-12.03"),
    ("DOC-004.2", "DOC-4.2"),
    ("DOC-0", "DOC-000"),
    # spacing, at the ends and between runs, and a run of separators is one boundary
    ("  K7-12 ", "K7-12"),
    ("K7 - 12", "K7-12"),
    ("K7--12", "K7.12"),
    ("K7\u00a012", "K7-12"),
    ("K7-12\t", "K7-12"),
    # invisible characters
    ("K7\u200b-12", "K7-12"),
    ("\ufeffK7-12", "K7-12"),
    ("K7\u00ad12", "K7-12"),
    # all at once
    (" k7_12 . 03 ", "K7-12.3"),
    # other characters count, but stay where they are
    ("A#1", "a#01"),
    ("A-#1", "A#1"),
    # long digit runs compare by value
    ("X" + "0" * 30 + "1" + "9" * 5000, "x1" + "9" * 5000),
]

DIFFER = [
    # a boundary is kept: no joining of runs
    ("03-1", "031"),
    ("K7-12", "K712"),
    ("AB", "A B"),
    ("AB-1", "A-B-1"),
    # numbers are numbers, not decimals
    ("K1.5", "K1.50"),
    ("12", "120"),
    # a separator at either end counts
    ("K7-", "K7"),
    ("-5", "5"),
    ("K7.", "K7"),
    (".5", "5"),
    # other punctuation counts
    ("K7#12", "K7-12"),
    ("K7,12", "K7-12"),
    ("K7(12)", "K7-12"),
    ("K7+12", "K7-12"),
    ("K7:12", "K7-12"),
    # accents count; letters are not digits
    ("CAFÉ-1", "CAFE-1"),
    ("KO-1", "K0-1"),
    ("KI-1", "K1-1"),
    # digits other than 0-9 are other characters
    ("K\u0667", "K7"),
    ("K\uff17", "K7"),
    ("K²", "K2"),
    # different words, numbers, order
    ("K7-12", "K12-7"),
    ("K7-12", "L7-12"),
    ("K7-12.3", "K7-12.4"),
    ("K7-12", "K7-12-0"),
    # empty is no key
    ("", "0"),
]


@pytest.mark.parametrize("a, b", MATCH)
def test_keys_that_match_loosely(a, b):
    assert loose_key(a) == loose_key(b)


@pytest.mark.parametrize("a, b", DIFFER)
def test_keys_that_do_not(a, b):
    assert loose_key(a) != loose_key(b)


def test_empty_and_blank_keys_are_no_runs():
    assert loose_key("") == () and loose_key(" \u200b\t ") == ()


def test_the_form_sorts_naturally():
    keys = ["K10", "k9", "K-9.1", "K1", "K02", "DOC-4.2", "doc-4.10"]
    assert sorted(keys, key=loose_key) == ["DOC-4.2", "doc-4.10", "K1", "K02", "k9", "K-9.1", "K10"]


def test_the_rule_is_documented_once_in_the_module():
    from ooxml_edit.tools import keys

    for said in ("case folded", "as a number", "one boundary", "counts", "Nothing else is guessed"):
        assert said in keys.__doc__


# -- orMap: pairs written as one object --------------------------------------------------------------


PAIRS = array(obj({"table": string(), "data": string()}), "Pairs.", or_map=True, optional=True)
SCHEMA = {"type": "object", "properties": {"match": PAIRS.schema}, "additionalProperties": False}


def test_an_object_is_read_as_pairs_in_its_order():
    notes: list[str] = []
    found = validate_call(SCHEMA, {"match": {"K7-1": "k7.01", "DOC-4": "DOC-004"}}, notes)
    assert found == {"match": [{"table": "K7-1", "data": "k7.01"}, {"table": "DOC-4", "data": "DOC-004"}]}
    assert notes == ["match given as an object: read as 2 pair(s) {table, data}"]


def test_the_array_is_taken_as_it_is_and_a_bad_value_still_refused():
    notes: list[str] = []
    given = {"match": [{"table": "K7-1", "data": "k7.01"}]}
    assert validate_call(SCHEMA, given, notes) == given and notes == []
    with pytest.raises(CallError, match=r"match\[0\].data must be a string"):
        validate_call(SCHEMA, {"match": {"K7-1": 5}})
    with pytest.raises(CallError, match="must be an array"):
        validate_call(SCHEMA, {"match": "K7-1"})


def test_or_map_is_never_sent_and_only_on_pairs():
    @tool("pairs_tool", "Take pairs.", {"match": PAIRS}, documents=())
    def pairs_tool(call, match=None):
        return None

    assert pairs_tool.schema["properties"]["match"]["orMap"] is True
    assert "orMap" not in json.dumps(pairs_tool.canonical)
    check_subset(pairs_tool.schema, allow_bounds=True)
    wrong = array(obj({"table": string(), "data": string(), "more": string()}), "Pairs.", or_map=True)
    assert any("orMap must be true" in p for p in subset_problems(wrong.schema, allow_bounds=True))
    assert any("orMap must be true" in p for p in subset_problems({"type": "string", "orMap": True},
                                                                   allow_bounds=True))
