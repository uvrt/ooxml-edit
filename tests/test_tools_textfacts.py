"""Text facts: ``{{d:by_topic/door/mean}}`` in a text argument is written as the value, in the
session's locale, and reported with its sources; the fact helpers keep what an edit leaves
true.

The toys: ``toy_say`` keeps the texts it is given (``items[].text`` and rows of cells,
``rows[]``) and returns what ``call.facts`` said; a grid whose tables resolve ``t:<name>``
and ``t:<name>/<key>`` (one row) with a source per cell.  Every figure is made up.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from ooxml_edit import dates, provenance
from ooxml_edit.tools import (Dataset, DefaultLocale, DocumentFormat, Result, ToolError,
                              Toolbox, array, obj, shared, string, tool)
from ooxml_edit.tools import textfacts

import synthetic
import tools_toys

CLOCK = lambda: dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.timezone.utc)  # noqa: E731
NL_BE = dataclasses.replace(DefaultLocale(), decimal_separator=",", thousands_separator=".",
                            date_order="dmy", language="nl-BE", list_separator=";")


class Grid:
    def __init__(self, tables: dict) -> None:
        self.tables = tables


def _table(grid: Grid, address: str) -> Dataset:
    name, _, key = address[len("t:"):].partition("/")
    if name not in grid.tables:
        raise ToolError("not_found", f"no table {name!r}", valid_options=sorted(grid.tables))
    header, rows = grid.tables[name]
    numbered = list(enumerate(rows))
    if key:
        key = key[1:-1].replace("''", "'") if key.startswith("'") else key
        numbered = [(i, row) for i, row in numbered
                    if (f"#{i + 1}" == key if key.startswith("#") else row[0] == key)]
        if len(numbered) != 1:
            raise ToolError("not_found", f"no row {key!r} in {name}")
    sources = [[(f"G!{chr(65 + c)}{i + 2}",) for c in range(len(header))] for i, _ in numbered]
    return Dataset(header, [row for _, row in numbered], sources=sources, keys=1)


GRID = DocumentFormat(kind="xlsx", open=lambda data: Grid({}), detect=lambda data, name: False,
                      datasets={"t:": _table})

CHECKS = (("item", "rating", "share", "when"),
          [("N-1", 2.1666666666666665, 0.253, "2026-03-01"), ("A/7", 3, 0.5, "2026-04-02"),
           ("N-2", 1234567.891, 1.0, "2026-05-03")])

SAID: list[dict] = []


@tool("toy_say", "Keep texts.",
      {"doc": string("Document id."),
       "items": array(obj({"text": string()}), "Texts."),
       "rows": array(array(string()), "Cells.", optional=True)},
      mutates=True, facts=("items[].text", "rows[]"))
def toy_say(call, doc, items, rows=None):
    SAID.append({"items": items, "rows": rows,
                 "facts": [(f.field, f.start, f.end, f.text, f.address) for f in call.facts],
                 "first": [f.record for f in call.facts_at("items[0].text")]})
    return Result(summary="said", data={"count": len(items)})


@tool("ds_keep", "Keep a made-up dataset.", {"id": string("Its id.")}, documents=())
def ds_keep(call, id):
    header, rows = CHECKS
    types = (None, "number", "percent", "date")
    sources = [[(f"t:checks/{row[0]}/{name}",) for name in header] for row in rows]
    call.define_dataset(id, Dataset(header, rows, sources=sources, keys=1, types=types),
                        source="d2")
    return Result(summary=f"Kept d:{id}")


TOOLS = [toy_say, ds_keep, *shared.SESSION_TOOLS]


@pytest.fixture(autouse=True)
def _clear():
    SAID.clear()


@pytest.fixture
def toolbox():
    with Toolbox(TOOLS, formats=[*tools_toys.FORMATS, GRID], groups=shared.GROUPS,
                 workers=0) as box:
        yield box


def _session(toolbox, **options):
    session = toolbox.session(clock=CLOCK, **options)
    session.open(synthetic.outer_package(), "deck.pptx")                 # d1
    session.adopt(Grid({"checks": CHECKS}), "xlsx", "checks-made-up.xlsx")  # d2
    return session


def say(toolbox, session, *texts, rows=None):
    arguments = {"doc": "d1", "items": [{"text": text} for text in texts]}
    if rows is not None:
        arguments["rows"] = rows
    return toolbox.dispatch(session, "toy_say", arguments)


def ok(result):
    assert result.ok, result.to_json()
    return result


def failed(result, code):
    assert not result.ok, result.to_json()
    assert result.error.code == code, result.to_json()
    return result.error


# -- writing values ------------------------------------------------------------------------------


def test_a_dataset_value_is_written_with_its_sources(toolbox):
    session = _session(toolbox)
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    result = ok(say(toolbox, session, "Rated {{d:by_item/N-2/rating:#,##0.0}} at N-2"))
    assert SAID[-1]["items"] == [{"text": "Rated 1,234,567.9 at N-2"}]
    assert SAID[-1]["facts"] == [("items[0].text", 6, 17, "1,234,567.9",
                                  "d:by_item/N-2/rating")]
    assert result.data == {"count": 1, "facts": [
        {"from": "d:by_item/N-2/rating", "text": "1,234,567.9",
         "sources": ["t:checks/N-2/rating"]}]}
    record = SAID[-1]["first"][0]
    assert record == {"source": "d:by_item/N-2/rating", "document": "checks-made-up.xlsx",
                      "version": session.documents["d2"].version, "format": "#,##0.0",
                      "text": "1,234,567.9", "sources": ["t:checks/N-2/rating"]}


def test_nl_be_writes_a_decimal_comma_and_its_own_grouping(toolbox):
    session = _session(toolbox, locale=NL_BE)
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    ok(say(toolbox, session, "Gemiddeld {{d:by_item/N-1/rating:0.0}}; "
           "{{d:by_item/N-2/rating:#,##0.00}}; {{d:by_item/N-1/share}}; "
           "{{d:by_item/N-1/share:0.0%}}; {{d:by_item/N-1/when}}; {{d:by_item/#2/rating}}"))
    assert SAID[-1]["items"][0]["text"] == ("Gemiddeld 2,2; 1.234.567,89; 25,3%; 25,3%; "
                                            "1/3/2026; 3")


def test_without_a_format_a_number_is_written_as_a_table_cell_gets_it(toolbox):
    session = _session(toolbox)
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    ok(say(toolbox, session, "{{d:by_item/N-1/rating}} {{d:by_item/N-1/rating:0}}"))
    assert SAID[-1]["items"][0]["text"] == "2.16666666666667 2"


def test_rounding_is_half_away_from_zero_on_the_decimal_shown():
    assert textfacts.number_text(2.675, "0.00") == "2.68"
    assert textfacts.number_text(-2.5, "0") == "-3"
    assert textfacts.number_text(-0.04, "0.0") == "0.0"
    assert textfacts.number_text(0.125, "0%") == "13%"
    assert textfacts.number_text(1234.5, "#,##0", NL_BE) == "1.235"


def test_a_table_value_of_another_document_is_read_by_its_row(toolbox):
    session = _session(toolbox)
    result = ok(say(toolbox, session, "Door: {{t:checks/'A/7'/rating}} ({{t:checks/#1/item}})"))
    assert SAID[-1]["items"][0]["text"] == "Door: 3 (N-1)"
    assert result.data["facts"][0] == {"from": "t:checks/'A/7'/rating", "text": "3",
                                       "sources": ["G!B3"]}
    record = SAID[-1]["first"][0]
    assert record["source"] == "t:checks/'A/7'/rating"
    assert record["document"] == "checks-made-up.xlsx"


def test_rows_of_cells_and_several_texts_each_say_where(toolbox):
    session = _session(toolbox)
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    ok(say(toolbox, session, "a", "x {{d:by_item/N-1/item}}",
           rows=[["{{d:by_item/#3/item}}", "plain"]]))
    assert SAID[-1]["rows"] == [["N-2", "plain"]]
    assert [f[0] for f in SAID[-1]["facts"]] == ["items[1].text", "rows[0][0]"]
    assert SAID[-1]["first"] == []


def test_a_batch_op_expands_too(toolbox):
    session = _session(toolbox)
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    result = ok(toolbox.dispatch(session, "batch", {"ops": [
        {"tool": "toy_say", "arguments": {"doc": "d1", "items": [
            {"text": "{{t:checks/N-1/rating:0.0}}"}]}}]}))
    assert SAID[-1]["items"][0]["text"] == "2.2"
    assert result.data["ops"][0]["data"]["facts"][0]["text"] == "2.2"


# -- what is no reference ------------------------------------------------------------------------


def test_other_braces_stay_and_an_escape_writes_the_reference_literally(toolbox):
    session = _session(toolbox)
    result = ok(say(toolbox, session, "Dear {{name}}, {x:1} {{ q:1 }} \\{{d:by_item/N-1/item}}"))
    assert SAID[-1]["items"][0]["text"] == "Dear {{name}}, {x:1} {{ q:1 }} {{d:by_item/N-1/item}}"
    assert SAID[-1]["facts"] == [] and "facts" not in result.data


# -- refusals ------------------------------------------------------------------------------------


def test_a_reference_to_many_values_is_refused_with_an_example(toolbox):
    session = _session(toolbox)
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    error = failed(say(toolbox, session, "{{d:by_item/rating}}"), "invalid_arguments")
    assert "not one value" in error.message and "d:by_item/" in error.message
    assert error.field == "items[0].text"
    assert SAID == []


def test_an_unknown_dataset_row_or_column_is_not_found(toolbox):
    session = _session(toolbox)
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    error = failed(say(toolbox, session, "{{d:nope/N-1/rating}}"), "not_found")
    assert "d:by_item" in error.valid_options
    error = failed(say(toolbox, session, "{{d:by_item/N-9/rating}}"), "not_found")
    assert "d:by_item/N-1" in error.valid_options
    error = failed(say(toolbox, session, "{{d:by_item/N-1/ratings}}"), "not_found")
    assert "rating" in error.valid_options
    failed(say(toolbox, session, "{{d:by_item/#9/rating}}"), "not_found")


def test_a_format_on_text_is_refused(toolbox):
    session = _session(toolbox)
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    error = failed(say(toolbox, session, "{{d:by_item/N-1/item:0.0}}"), "invalid_arguments")
    assert "is for numbers" in error.message


def test_t_without_an_open_table_document_is_not_found(toolbox):
    session = toolbox.session(clock=CLOCK)
    session.open(synthetic.outer_package(), "deck.pptx")
    failed(say(toolbox, session, "{{t:checks/N-1/rating}}"), "not_found")


# -- the fact helpers ----------------------------------------------------------------------------


def _fact(p, at, text):
    return {"source": "d:x/a/b", "text": text, "p": p, "at": at}


def test_place_and_offset_go_both_ways():
    text = "Title\nAverage 2,2 across\n41"
    assert provenance.place(text, 14) == (1, 8)
    assert provenance.offset(text, 1, 8) == 14
    assert provenance.offset(text, 3, 0) is None
    assert provenance.offset(text, 0, True) is None


def test_facts_found_keeps_only_the_ones_still_where_they_say():
    text = "Average 2,2 across 41"
    facts = [_fact(0, 8, "2,2"), _fact(0, 19, "41"), _fact(0, 8, "9,9"), "junk"]
    assert provenance.facts_found(facts, text) == facts[:2]


def test_an_edit_keeps_untouched_facts_moved_and_drops_touched_or_bordering_ones():
    old = "Average 2,2 across 41 items"
    facts = [_fact(0, 8, "2,2"), _fact(0, 19, "41")]
    new = "Mean 2,2 across 41 items"
    assert provenance.facts_after_edit(facts, old, new) == [_fact(0, 5, "2,2"),
                                                            _fact(0, 16, "41")]
    assert provenance.facts_after_edit(facts, old, "Average 2,2 across 42 items") == [
        _fact(0, 8, "2,2")]
    assert provenance.facts_after_edit(facts, old, "Average 12,2 across 41 items") == [
        _fact(0, 20, "41")]
    assert provenance.facts_after_edit(facts, old, "New\n" + old) == [
        _fact(1, 8, "2,2"), _fact(1, 19, "41")]
    assert provenance.facts_after_edit(facts, old, old) == facts


def test_the_tool_declaration_is_checked():
    with pytest.raises(Exception, match="facts names 'nope'"):
        tool("bad_tool", "x", {"doc": string("d")}, facts=("nope",))(lambda call, doc: None)
    assert shared.definition("replace_text").facts == ("replace",)


# -- a suffix that is no number format (round 6, R5) ----------------------------------------------


@pytest.mark.parametrize("suffix, near", [("0.20", "0.00"), ("0,0", "0.0"), (".2f", "0.00"),
                                          ("#,###.0", "#,##0.0"), ("0.0 %", "0.0%"),
                                          ("00", "0")])
def test_a_suffix_that_is_no_number_format_says_so_with_the_forms(toolbox, suffix, near):
    session = _session(toolbox)
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    error = failed(say(toolbox, session, f"{{{{d:by_item/#1/rating:{suffix}}}}}"),
                   "invalid_arguments")
    assert f"{suffix.strip()!r} is not a number format" in error.message
    assert "no column" not in error.message
    assert f"{{{{d:by_item/#1/rating:{near}}}}}" in error.message
    assert error.valid_options == list(textfacts.FORMS)
    assert error.field == "items[0].text"
    assert SAID == []


def test_a_bad_suffix_on_an_unknown_column_or_a_source_says_the_same(toolbox):
    session = _session(toolbox)
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    error = failed(say(toolbox, session, "{{d:by_item/#1/avg:0.20}}"), "invalid_arguments")
    assert "'0.20' is not a number format" in error.message
    error = failed(say(toolbox, session, "{{d2/t:checks/N-1/rating:0.20}}"),
                   "invalid_arguments")
    assert "'0.20' is not a number format" in error.message


def test_a_column_named_with_a_colon_still_resolves(toolbox):
    session = _session(toolbox)

    @tool("ds_colon", "Keep a dataset with a colon in a column.", {}, documents=())
    def ds_colon(call):
        call.define_dataset("odd", Dataset(("item", "avg:0.20"), [("N-1", 7)], keys=1))
        return Result(summary="kept")

    with Toolbox([toy_say, ds_colon, *shared.SESSION_TOOLS], workers=0,
                 formats=tools_toys.FORMATS, groups=shared.GROUPS) as box:
        session = box.session(clock=CLOCK)
        session.open(synthetic.outer_package(), "deck.pptx")
        ok(box.dispatch(session, "ds_colon", {}))
        ok(say(box, session, "{{d:odd/N-1/avg:0.20}}", "{{d:odd/N-1/'avg:0.20':0.0}}"))
    assert SAID[-1]["items"] == [{"text": "7"}, {"text": "7.0"}]


def test_a_quoted_column_or_a_plain_unknown_one_is_not_read_as_a_format(toolbox):
    session = _session(toolbox)
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    error = failed(say(toolbox, session, "{{d:by_item/#1/'avg:0.20'}}"), "not_found")
    assert "no column 'avg:0.20'" in error.message
    error = failed(say(toolbox, session, "{{d:by_item/#1/avg:total}}"), "not_found")
    assert "no column" in error.message
    error = failed(say(toolbox, session, "{{d:2024}}"), "not_found")
    assert "not a number format" not in error.message
    assert textfacts.bad_format("d:2024") is None
    assert textfacts.bad_format("d:x/#1/avg:0.0") is None


# -- date patterns (round 6, R9) -----------------------------------------------------------------


def _in(language: str) -> DefaultLocale:
    return dataclasses.replace(NL_BE, language=language)


#: ``{{d:by_item/N-1/when:dddd d mmmm yyyy}}`` (Sunday 1 March 2026) per locale: the names
#: measured in Excel for Mac, 2026-10-16.
@pytest.mark.parametrize("language, written", [
    ("en-US", "Sunday 1 March 2026 (Sun, Mar, M)"),
    ("en-GB", "Sunday 1 March 2026 (Sun, Mar, M)"),
    ("nl-NL", "zondag 1 maart 2026 (zo, mrt, m)"),
    ("nl-BE", "zondag 1 maart 2026 (zo, mrt, m)"),
    ("de-DE", "Sonntag 1 März 2026 (So, Mär, M)"),
    ("fr-FR", "dimanche 1 mars 2026 (dim., mars, m)"),
    ("es-ES", "domingo 1 marzo 2026 (do., mar., m)"),
    ("it-IT", "domenica 1 marzo 2026 (dom, mar, m)"),
])
def test_a_date_pattern_writes_the_locales_measured_names(toolbox, language, written):
    session = _session(toolbox, locale=_in(language))
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    ok(say(toolbox, session, "{{d:by_item/N-1/when:dddd d mmmm yyyy}} "
           "({{d:by_item/N-1/when:ddd}}, {{d:by_item/N-1/when:mmm}}, "
           "{{d:by_item/N-1/when:mmmmm}})"))
    assert SAID[-1]["items"][0]["text"] == written


@pytest.mark.parametrize("language", ["en-US", "nl-BE", "de-DE", "ja-JP"])
def test_a_date_patterns_separators_are_written_as_given_in_every_locale(toolbox, language):
    session = _session(toolbox, locale=_in(language))
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    ok(say(toolbox, session, "{{d:by_item/N-1/when:dd/mm/yyyy}} {{d:by_item/#3/when:d-m-yy}} "
           "{{d2/t:checks/'A/7'/when:yyyy.mm.dd}} {{d:by_item/N-1/when:DD/MM/YYYY}}"))
    assert SAID[-1]["items"][0]["text"] == "01/03/2026 3-5-26 2026.04.02 01/03/2026"


def test_a_date_patterns_fact_keeps_the_pattern_as_its_format(toolbox):
    session = _session(toolbox)
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    result = ok(say(toolbox, session, "On {{d:by_item/N-1/when:d mmmm yyyy}}"))
    assert result.data["facts"] == [{"from": "d:by_item/N-1/when", "text": "1 March 2026",
                                     "sources": ["t:checks/N-1/when"]}]
    assert SAID[-1]["facts"] == [("items[0].text", 3, 15, "1 March 2026", "d:by_item/N-1/when")]
    record = SAID[-1]["first"][0]
    assert record["format"] == "d mmmm yyyy" and record["source"] == "d:by_item/N-1/when"


def test_a_name_token_without_measured_names_is_refused_numbers_still_work(toolbox):
    session = _session(toolbox, locale=_in("ja-JP"))
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    error = failed(say(toolbox, session, "{{d:by_item/N-1/when:d mmmm yyyy}}"),
                   "invalid_arguments")
    assert "no month or day names are measured for ja-JP" in error.message
    assert "dd/mm/yyyy" in error.message and error.field == "items[0].text"
    ok(say(toolbox, session, "{{d:by_item/N-1/when:yyyy-mm-dd}}"))
    assert SAID[-1]["items"][0]["text"] == "2026-03-01"


def test_a_date_pattern_on_a_number_and_a_number_format_on_a_date_name_each_other(toolbox):
    session = _session(toolbox)
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    error = failed(say(toolbox, session, "{{d:by_item/N-1/rating:d mmmm yyyy}}"),
                   "invalid_arguments")
    assert "a date pattern (:d mmmm yyyy) is for dates" in error.message
    assert ":0.0" in error.message
    error = failed(say(toolbox, session, "{{d:by_item/N-1/when:0.0}}"), "invalid_arguments")
    assert "a number format (:0.0) is for numbers" in error.message
    assert "date pattern" in error.message and "mmmm" in error.message
    error = failed(say(toolbox, session, "{{d:by_item/N-1/item:dd/mm/yyyy}}"),
                   "invalid_arguments")
    assert "is for dates" in error.message


@pytest.mark.parametrize("suffix, why", [
    ("DD/MM/YYYY HH:mm", "'H' is no date token"),
    ("%d-%m-%Y", "no date token"),
    ("ddddd mmmm yyyy", "'ddddd' is no date token"),
    ("d mmmm yyy", "'yyy' is no date token"),
])
def test_a_suffix_that_is_no_date_pattern_says_so_with_the_tokens(toolbox, suffix, why):
    session = _session(toolbox)
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    error = failed(say(toolbox, session, f"{{{{d:by_item/#1/when:{suffix}}}}}"),
                   "invalid_arguments")
    assert f"{suffix!r} is not a date pattern" in error.message
    assert why in error.message
    assert "{{d:by_item/#1/when:d mmmm yyyy}}" in error.message
    assert error.valid_options == list(dates.TOKENS)
    assert SAID == []


def test_the_number_format_refusal_lists_the_date_tokens_too(toolbox):
    session = _session(toolbox)
    ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
    error = failed(say(toolbox, session, "{{d:by_item/#1/rating:0.20}}"), "invalid_arguments")
    assert "d dd ddd dddd m mm mmm mmmm mmmmm yy yyyy" in error.message
    assert ":d mmmm yyyy" in error.message


def test_a_date_suffix_is_read_only_after_the_sources_prefix():
    assert textfacts.date_suffix("d:x/#1/when:dd/mm/yyyy") == ("d:x/#1/when", "dd/mm/yyyy")
    assert textfacts.date_suffix("d2/t:c/N-1/when:d mmmm") == ("d2/t:c/N-1/when", "d mmmm")
    assert textfacts.date_suffix("d:dmy") is None                    # a dataset's name
    assert textfacts.date_suffix("d:x/#1/avg:0.0") is None           # a number format
    assert textfacts.date_suffix("d:x/'a:dd'/v") is None             # a quoted key
    assert textfacts.date_suffix("d:x/#1/avg:total") is None
    assert textfacts.strip_format("d:x/#1/when:d mmmm yyyy") == "d:x/#1/when"
    assert textfacts.split("d:x/#1/when:dd/mm/yyyy") == (
        "d:x", [("#1", False), ("when", False)], "dd/mm/yyyy")


def test_without_a_pattern_a_date_is_written_as_before(toolbox):
    for locale, written in ((None, "3/1/2026"), (NL_BE, "1/3/2026")):
        session = _session(toolbox, locale=locale)
        ok(toolbox.dispatch(session, "ds_keep", {"id": "by_item"}))
        ok(say(toolbox, session, "{{d:by_item/N-1/when}}"))
        assert SAID[-1]["items"][0]["text"] == written
