"""Numbers in text no value holds: ``check``'s ``include: ["numbers"]`` (round 36).

The toys: a board (kind ``pptx``) whose slides hold texts and give them to a check of
numbers (``number_texts``), each with the spans written by reference; a grid (kind
``xlsx``) whose tables resolve ``t:<name>`` and ``t:<name>/<key>`` and are listed as values
(``value_sources``), each column with a number format.  Every figure is made up.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal

import pytest
from ooxml_common.locales import Locale

from ooxml_edit.tools import (Dataset, DocumentFormat, Result, ToolError, Toolbox, shared,
                              string, tool)
from ooxml_edit.tools import numbers
from ooxml_edit.tools.numbers import NumberRules, NumberText, find_numbers, shown

CLOCK = lambda: dt.datetime(2026, 11, 6, 12, 0, tzinfo=dt.timezone.utc)  # noqa: E731
NL_BE = dataclasses.replace(Locale(), decimal_separator=",", thousands_separator=".",
                            date_order="dmy", language="nl-BE", list_separator=";")
EN = Locale()


# -- the toys ------------------------------------------------------------------------------------


class Board:
    """Slides of texts: ``{slide: [(at, text, written spans)]}``."""

    def __init__(self, slides: dict) -> None:
        self.slides = slides


def board_texts(call, scope):
    board = call.document
    wanted = scope.get("slides")
    if scope.get("pages") is not None:
        raise ToolError("invalid_arguments", "a board has slides", field="pages")
    out = []
    for slide, texts in board.slides.items():
        if wanted is not None and slide not in wanted:
            continue
        for at, text, written in texts:
            out.append(NumberText(at, text, tuple(written), {"slides": [slide]},
                                  {"slide": slide}))
    return out


BOARD = DocumentFormat(kind="pptx", open=lambda data: Board({}),
                       detect=lambda data, name: False, number_texts=board_texts)
BARE = DocumentFormat(kind="docx", open=lambda data: Board({}),
                      detect=lambda data, name: False)


class Grid:
    def __init__(self, tables: dict) -> None:
        self.tables = tables


def grid_table(grid: Grid, address: str) -> Dataset:
    name, _, key = address[len("t:"):].partition("/")
    if name not in grid.tables:
        raise ToolError("not_found", f"no table {name!r}", valid_options=sorted(grid.tables))
    header, rows, formats, *keyed = grid.tables[name]
    numbered = list(enumerate(rows))
    if key:
        key = key[1:-1].replace("''", "'") if key.startswith("'") else key
        numbered = [(i, row) for i, row in numbered
                    if (f"#{i + 1}" == key if key.startswith("#") else row[0] == key)]
        if len(numbered) != 1:
            raise ToolError("not_found", f"no row {key!r} in {name}")
    sources = [[(f"{name}!{chr(65 + c)}{i + 2}",) for c in range(len(header))]
               for i, _ in numbered]
    return Dataset(header, [row for _, row in numbered], sources=sources,
                   keys=keyed[0] if keyed else 1, formats=formats)


GRID = DocumentFormat(kind="xlsx", open=lambda data: Grid({}), detect=lambda data, name: False,
                      datasets={"t:": grid_table},
                      value_sources=lambda grid: [f"t:{name}" for name in grid.tables])

SCORES = (("topic", "mean", "share", "count"),
          [("door", 2.66, 0.853, 41), ("window", 2.549, 0.12, 17), ("roof", -1.25, 0.5, 9)],
          (None, "0.00", "0.0%", "0"))

CHECKED: list[dict] = []


@shared.handler("check", kind="pptx")
def board_check(call, doc, slides=None, pages=None, include=None, boxes=False, spacing=None,
                contrast=None):
    CHECKED.append({"include": include, "slides": slides})
    return Result(summary="0 fit fact(s)", data={"slides": slides or "all", "overflows": []})


@tool("ds_keep", "Keep a made-up dataset.", {"id": string("Its id.")}, documents=())
def ds_keep(call, id):
    call.define_dataset(id, Dataset(("region", "avg", "rate"),
                                    [("North", 3.14159, 0.25), ("South", 1.5, 0.0372)],
                                    sources=[[("t:x/North/region",), ("t:x/North/avg",),
                                              ("t:x/North/rate",)],
                                             [("t:x/South/region",), ("t:x/South/avg",),
                                              ("t:x/South/rate",)]],
                                    keys=1, types=(None, "number", "percent")))
    return Result(summary=f"Kept d:{id}")


TOOLS = [board_check, ds_keep, *shared.SESSION_TOOLS]


@pytest.fixture(autouse=True)
def _clear():
    CHECKED.clear()


@pytest.fixture
def toolbox():
    with Toolbox(TOOLS, formats=[BOARD, GRID], groups=shared.GROUPS, workers=0) as box:
        yield box


def _session(toolbox, slides, **options):
    session = toolbox.session(clock=CLOCK, **options)
    session.adopt(Board(slides), "pptx", "board.pptx")                     # d1
    session.adopt(Grid({"Scores": SCORES}), "xlsx", "scores-made-up.xlsx")  # d2
    return session


def check(toolbox, session, **arguments):
    result = toolbox.dispatch(session, "check", {"doc": "d1", **arguments})
    assert result.ok, result.to_json()
    return result


def numbers_of(result):
    return result.data["numbers"]


# -- reading numbers -----------------------------------------------------------------------------


def _read(text, locale=EN, **rules):
    found, aside = find_numbers(text, locale, NumberRules(**rules))
    return [(f.text, f.value, f.decimals, f.percent, f.negative) for f in found], aside


def test_decimals_and_percentages_are_read_in_the_locale():
    found, aside = _read("Gemiddeld 2,7/5 voor 1.234,5 klanten (85 %), daling van −2,5.", NL_BE)
    assert found == [("2,7", Decimal("2.7"), 1, None, False),
                     ("1.234,5", Decimal("1234.5"), 1, None, False),
                     ("85 %", Decimal("85"), 0, 100, False),
                     ("−2,5", Decimal("2.5"), 1, None, True)]
    assert aside == {"whole": 1}                       # the /5
    found, _ = _read("Rated 2.7/5 by 1,234.5 users; 85% said yes; 0.5‰ left")
    assert [f[0] for f in found] == ["2.7", "1,234.5", "85%", "0.5‰"]
    assert found[3][3] == 1000


def test_a_space_groups_thousands_only_where_the_locale_does():
    assert [f[0] for f in _read("123.4 567.8 and 12.5")[0]] == ["123.4", "567.8", "12.5"]
    fr = dataclasses.replace(Locale(), decimal_separator=",", thousands_separator="\u202f",
                             language="fr-FR", date_order="dmy", list_separator=";")
    assert _read("soit 1\u202f234,5 et 2,5", fr)[0] == [
        ("1\u202f234,5", Decimal("1234.5"), 1, None, False), ("2,5", Decimal("2.5"), 1, None, False)]


def test_the_other_conventions_decimal_is_read_as_a_reader_would():
    found, aside = _read("Prijs 2.7 euro voor 2.500 stuks", NL_BE)
    assert found == [("2.7", Decimal("2.7"), 1, None, False)]
    assert aside == {"whole": 1}                       # 2.500 is two thousand five hundred
    found, _ = _read("rated 2,7 here")
    assert found == [("2,7", Decimal("2.7"), 1, None, False)]


def test_a_range_is_two_numbers_and_a_sign_only_before_a_number():
    found, _ = _read("between 2.5-3.5 days, down -1.25")
    assert found == [("2.5", Decimal("2.5"), 1, None, False),
                     ("3.5", Decimal("3.5"), 1, None, False),
                     ("-1.25", Decimal("1.25"), 2, None, True)]


def test_dates_times_years_list_numbers_and_ids_are_left_aside():
    _, aside = _read("On 12/03/2026, 2026-03-12 and 12.03.2026 at 14:05 (or 14.30 uur), "
                     "3 March, in 2024: Q3, CO2, 2.1.3", whole_numbers=True)
    assert aside == {"dates": 12, "years": 1}
    found, aside = _read("1. First 2.5\n2.3 Results were 4.25\n(4) last")
    assert [f[0] for f in found] == ["2.5", "4.25"]
    assert aside == {"list numbers": 3}
    found, aside = _read("v2.1 build, 3D view at #4.5, 2nd and 1.5x")
    assert found == [] and aside == {"ids": 3}          # 3D, 2nd, 1.5x


def test_a_number_named_by_the_word_before_it_is_an_id():
    found, aside = _read("see section 4.2, page 12.5 and version 2.1; hoofdstuk 3.4 holds 4.25")
    assert [f[0] for f in found] == ["4.25"]
    assert aside == {"ids": 4}


def test_a_scores_scale_is_left_aside_with_whole_numbers_on():
    found, aside = _read("rated 2,7/5 and 3,8 op 5, kost van 12.500", NL_BE, whole_numbers=True)
    assert [f[0] for f in found] == ["2,7", "3,8", "12.500"]
    assert aside == {"scales": 2}


def test_a_scale_word_comes_with_its_number():
    found, _ = _read("revenue 2.5m and 1,2 mln", NL_BE)
    assert [(f[0], f[1]) for f in found] == [("2.5m", Decimal("2.5")), ("1,2 mln", Decimal("1.2"))]


def test_whole_numbers_only_when_the_application_asks():
    assert _read("41 items, 85%")[0] == [("85%", Decimal("85"), 0, 100, False)]
    assert [f[0] for f in _read("41 items, 85%", whole_numbers=True)[0]] == ["41", "85%"]


def test_the_ignore_list_takes_numbers_and_phrases():
    found, aside = _read("ISO 9001.2 and WCAG 2.1, 4.5 and 4.25",
                         ignore=("iso 9001.2", "WCAG 2.1", 4.5))
    assert [f[0] for f in found] == ["4.25"]
    assert aside == {"ignored": 3}


def test_written_spans_are_left_out():
    text = "Average 2.66 across 3.5 items"
    found, _ = find_numbers(text, EN, NumberRules(), written=[(8, 12)])
    assert [f.text for f in found] == ["3.5"]


def test_a_number_format_says_its_shown_rounding():
    assert shown("0.0") == (1, False, 1)
    assert shown("0.00%") == (2, True, 1)
    assert shown("#,##0") == (0, False, 1)
    assert shown('0.0,,"m"') == (1, False, 1_000_000)
    assert shown("[Red]0.0;-0.0") == (1, False, 1)
    assert shown("#,##0.00_);(#,##0.00)") == (2, False, 1)
    for code in ("General", "@", "0.00E+00", "d/m/yyyy", None, ""):
        assert shown(code) is None


# -- matching ------------------------------------------------------------------------------------


def _index(rows, types=None, formats=None):
    columns = tuple(f"c{i}" for i in range(len(rows[0])))
    return numbers.Index([numbers.Source("d:x", "d:x", Dataset(columns, rows, types=types,
                                                                formats=formats))])


def _matches(index, text, locale=EN):
    found, _ = find_numbers(text, locale, NumberRules(whole_numbers=True))
    return [sorted(index.values[i].number for i, _ in index.matches(number))
            for number in found]


def test_a_number_matches_every_value_that_rounds_to_it():
    index = _index([("a", 2.66), ("b", 2.75), ("c", 2.751), ("d", 2.64)])
    assert _matches(index, "2,7", NL_BE) == [[2.66, 2.75]]
    assert _matches(index, "2.66 and 2.6") == [[2.66], [2.64]]


def test_a_value_shown_rounded_matches_as_shown():
    index = _index([("a", 2.649)], formats=(None, "0.00"))
    assert _matches(index, "2.7") == [[2.649]]          # shown as 2.65, rounded again
    assert _matches(index, "2.6") == [[2.649]]          # rounded from the value itself


def test_percentages_match_fractions_and_points():
    index = _index([("a", 0.853, 85.3), ("b", 0.12, 3)], types=(None, "percent", None))
    assert _matches(index, "85%") == [[0.853, 85.3]]
    assert _matches(index, "85.3 %") == [[0.853, 85.3]]
    assert _matches(index, "12.0") == [[0.12]]          # a percentage column quoted bare
    assert _matches(index, "0.12") == [[0.12]]


def test_a_written_sign_must_agree():
    index = _index([("a", -1.25), ("b", 1.3)])
    assert _matches(index, "-1.25 and 1.25 and -1.30") == [[-1.25], [-1.25], []]


def test_a_scaled_number_matches_the_value_as_it_is_and_scaled():
    index = _index([("a", 2_500_000.0), ("b", 2.5)])
    assert _matches(index, "2.5m") == [[2.5, 2_500_000.0]]


# -- the check -----------------------------------------------------------------------------------


SLIDES = {"s:256": [("256.2", "Doors rated 2,7/5, windows 2,6 and roofs 2,3.", []),
                    ("256.3", "Shares: 85,3% and 12,0%", [])],
          "s:257": [("257.2", "Mean {{written}} 2,66 noted", [(5, 16)])]}


def test_numbers_are_listed_unverified_with_the_nearest_values(toolbox):
    session = _session(toolbox, SLIDES, locale=NL_BE)
    data = numbers_of(check(toolbox, session, include=["numbers"]))
    assert CHECKED == []                                # nothing else asked: no kind run
    first = data["texts"][0]
    assert first["at"] == "256.2" and first["slide"] == "s:256"
    assert first["unverified"] == [{"text": "2,3", "near": [
        {"value": "2,55", "from": "t:Scores/window/mean", "sources": ["Scores!B3"]},
        {"value": "2,66", "from": "t:Scores/door/mean", "sources": ["Scores!B2"]}]}]
    assert [entry["text"] for entry in first["typed"]] == ["2,7", "2,6"]
    assert data["left_aside"] == {"whole": 1}
    assert data["values"] == 9
    assert data["note"] == numbers.NOTE
    assert data["fix_hint"].startswith("4 fixes: send them as one batch")


def test_a_typed_value_comes_with_the_call_that_writes_it_by_reference(toolbox):
    session = _session(toolbox, SLIDES, locale=NL_BE)
    data = numbers_of(check(toolbox, session, include=["numbers"], slides=["s:256"]))
    rated = data["texts"][0]["typed"][0]
    assert rated["from"] == "t:Scores/door/mean"
    assert rated["fix"] == {"tool": "replace_text", "arguments": {
        "doc": "d1", "find": "Doors rated 2,7/5, windows 2,6", "replace":
            "Doors rated {{t:Scores/door/mean:0.0}}/5, windows 2,6", "expect": "one",
        "slides": ["s:256"]}}
    shares = data["texts"][1]["typed"]
    assert shares[0]["fix"]["arguments"]["replace"] == \
        "Shares: {{t:Scores/door/share:0.0%}} and 12,0%"
    assert "fix" in shares[1]


def test_a_value_rounded_twice_gets_no_fix(toolbox):
    session = _session(toolbox, {"s:256": [("256.2", "window 2,6 here", [])]}, locale=NL_BE)
    typed = numbers_of(check(toolbox, session, include=["numbers"]))["texts"][0]["typed"]
    assert typed == [{"text": "2,6", "from": "t:Scores/window/mean", "sources": ["Scores!B3"]}]


def test_text_written_by_reference_is_left_out(toolbox):
    session = _session(toolbox, {"s:257": SLIDES["s:257"]}, locale=NL_BE)
    data = numbers_of(check(toolbox, session, include=["numbers"]))
    assert [t["at"] for t in data["texts"]] == ["257.2"]
    session = _session(toolbox, {"s:257": [("257.2", "Mean 2,66 noted", [(5, 9)])]},
                       locale=NL_BE)
    assert numbers_of(check(toolbox, session, include=["numbers"]))["texts"] == []


def test_datasets_are_values_too_and_a_derived_value_is_listed_once(toolbox):
    session = _session(toolbox, {"s:256": [("256.2", "North 3.14 and 25%; 1.5", [])]})
    assert toolbox.dispatch(session, "ds_keep", {"id": "by_region"}).ok
    typed = numbers_of(check(toolbox, session, include=["numbers"]))["texts"][0]["typed"]
    assert [(t["text"], t["from"]) for t in typed] == [
        ("3.14", "d:by_region/North/avg"), ("25%", "d:by_region/North/rate"),
        ("1.5", "d:by_region/South/avg")]


def test_several_values_match_without_a_fix(toolbox):
    tables = {"Scores": SCORES, "Again": SCORES}
    session = toolbox.session(clock=CLOCK)
    session.adopt(Board({"s:256": [("256.2", "rated 2.66", [])]}), "pptx", "board.pptx")
    session.adopt(Grid(tables), "xlsx", "scores-made-up.xlsx")
    typed = numbers_of(check(toolbox, session, include=["numbers"]))["texts"][0]["typed"]
    assert typed == [{"text": "2.66", "from": ["t:Scores/door/mean", "t:Again/door/mean"],
                      "matches": 2}]


def test_two_workbooks_name_their_documents(toolbox):
    session = _session(toolbox, {"s:256": [("256.2", "rated 2.66", [])]})
    session.adopt(Grid({"Other": SCORES}), "xlsx", "other-made-up.xlsx")    # d3
    typed = numbers_of(check(toolbox, session, include=["numbers"]))["texts"][0]["typed"]
    assert typed[0]["from"] == ["d2/t:Scores/door/mean", "d3/t:Other/door/mean"]


def test_other_facts_are_the_kinds_and_numbers_are_added(toolbox):
    session = _session(toolbox, SLIDES, locale=NL_BE)
    result = check(toolbox, session, include=["numbers", "fit"], slides=["s:257"])
    assert CHECKED == [{"include": ["fit"], "slides": ["s:257"]}]
    assert result.data["overflows"] == [] and "numbers" in result.data
    assert result.summary.startswith("0 fit fact(s); ")
    check(toolbox, session, include=["fit"])
    assert CHECKED[-1]["include"] == ["fit"]
    assert "numbers" not in check(toolbox, session).data          # never by default


def test_the_lists_are_capped_and_what_did_not_fit_is_counted(toolbox):
    many = {"s:256": [(f"256.{n}", "7.1 7.2 7.3 7.4 7.5 7.6 7.7", []) for n in range(2, 6)]}
    session = _session(toolbox, many, numbers=NumberRules(texts=2, per_text=3))
    data = numbers_of(check(toolbox, session, include=["numbers"]))
    assert len(data["texts"]) == 2 and data["texts_left_out"] == 2
    assert len(data["texts"][0]["unverified"]) == 3 and data["texts"][0]["more"] == 4
    assert data["unverified"] == 28
    assert "left_out_hint" in data


def test_the_rules_are_the_applications(toolbox):
    slides = {"s:256": [("256.2", "WCAG 2.1 asks 4.5 and 41 items", [])]}
    session = _session(toolbox, slides, numbers={"ignore": ["WCAG 2.1"], "whole_numbers": True})
    data = numbers_of(check(toolbox, session, include=["numbers"]))
    assert data["left_aside"] == {"ignored": 1}
    assert [e["text"] for e in data["texts"][0]["unverified"]] == ["4.5"]
    assert [e["text"] for e in data["texts"][0]["typed"]] == ["41"]
    with Toolbox(TOOLS, formats=[BOARD, GRID], workers=0,
                 numbers=NumberRules(ignore=(4.5, "wcag 2.1"))) as box:
        session = _session(box, slides)
        assert numbers_of(check(box, session, include=["numbers"]))["texts"] == []
    with pytest.raises(ValueError):
        NumberRules(ignore=([1],))
    with pytest.raises(ValueError):
        NumberRules(texts=0)
    with pytest.raises(ValueError):
        numbers.rules_of({"whole": True})


def test_a_session_without_values_says_how_to_get_some(toolbox):
    session = toolbox.session(clock=CLOCK)
    session.adopt(Board({"s:256": [("256.2", "rated 2.66", [])]}), "pptx", "board.pptx")
    data = numbers_of(check(toolbox, session, include=["numbers"]))
    assert data["values"] == 0 and data["texts"] == []
    assert "define a dataset" in data["notes"][0]


def test_a_kind_that_gives_no_texts_refuses_with_the_way_out():
    @shared.handler("check", kind="docx")
    def bare_check(call, doc, include=None, **rest):
        return Result(summary="checked")

    with Toolbox([bare_check, *shared.SESSION_TOOLS], formats=[BARE], workers=0) as box:
        session = box.session(clock=CLOCK)
        session.adopt(Board({}), "docx", "notes.docx")
        result = box.dispatch(session, "check", {"doc": "d1", "include": ["numbers"]})
        assert not result.ok and result.error.code == "invalid_arguments"
        assert "leave numbers out" in result.error.message
        assert box.dispatch(session, "check", {"doc": "d1", "include": ["validate"]}).ok


def test_the_index_is_kept_while_nothing_changes(toolbox):
    session = _session(toolbox, {"s:256": [("256.2", "rated 2.66", [])]})
    check(toolbox, session, include=["numbers"])
    kept = session._number_index[1]
    check(toolbox, session, include=["numbers"])
    assert session._number_index[1] is kept
    assert toolbox.dispatch(session, "ds_keep", {"id": "by_region"}).ok
    check(toolbox, session, include=["numbers"])
    assert session._number_index[1] is not kept
    session.close("d2")                                 # the workbook's values go with it
    assert numbers_of(check(toolbox, session, include=["numbers"]))["values"] == 4


def test_the_workbooks_are_held_for_reading_in_a_batch_and_read_batch_too(toolbox):
    session = _session(toolbox, {"s:256": [("256.2", "rated 2.66", [])]})
    result = toolbox.dispatch(session, "batch", {"ops": [
        {"tool": "check", "arguments": {"doc": "d1", "include": ["numbers"]}}]})
    assert result.ok, result.to_json()
    assert "not held" not in str(result.to_json())
    assert "t:Scores/door/mean" in str(result.to_json())


def test_decks_and_documents_are_offered_numbers():
    sent = shared.SPECS["check"].make({"pptx": board_check.handlers["pptx"]}).canonical
    assert "numbers" in sent["properties"]["include"]["items"]["enum"]
    word = shared.SPECS["check"].make({"docx": lambda call, **a: None}).canonical
    assert "numbers" in word["properties"]["include"]["items"]["enum"]


def test_a_datasets_formats_are_checked_and_kept_by_a_slice():
    found = Dataset(("a", "b"), [("x", 1.5), ("y", 2.5)], formats=(None, "0.0"))
    assert found.row_slice(2).formats == (None, "0.0")
    with pytest.raises(ValueError):
        Dataset(("a", "b"), [("x", 1.5)], formats=("0.0",))


def test_a_table_not_keyed_by_its_first_column_is_quoted_by_row_position(toolbox):
    session = toolbox.session(clock=CLOCK)
    session.adopt(Board({"s:256": [("256.2", "door rated 2.66 overall", [])]}), "pptx", "board.pptx")
    session.adopt(Grid({"Plain": (*SCORES, 0)}), "xlsx", "plain-made-up.xlsx")
    typed = numbers_of(check(toolbox, session, include=["numbers"]))["texts"][0]["typed"][0]
    assert typed["from"] == "t:Plain/#1/mean"           # its resolver reads no key "door"
    assert typed["fix"]["arguments"]["replace"] == "door rated {{t:Plain/#1/mean:0.00}} overall"
