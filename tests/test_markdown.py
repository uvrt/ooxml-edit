"""ooxml_edit.markdown: the inline subset read as CommonMark reads it, lines and lists, and
Markdown written back that reads as what it was written from.

The emphasis cases are CommonMark 0.31.2's own (section 6.2) for the subset, with the
runs they give; with markdown-it-py installed (the parser the format libraries use for
whole documents) random strings over the subset's characters are read by both and must
agree run for run.
"""

from __future__ import annotations

import random

import pytest

from ooxml_edit.markdown import (Line, Run, escape, inline_markdown, merge, parse,
                                 parse_inline, plain, to_markdown)


def runs(*spec: "str | tuple[str, str]") -> tuple[Run, ...]:
    """``"text"`` or ``("text", "bisc")``: bold, italic, strike, code."""
    out = []
    for item in spec:
        text, flags = (item, "") if isinstance(item, str) else item
        out.append(Run(text, "b" in flags, "i" in flags, "s" in flags, "c" in flags))
    return merge(out)


# CommonMark 0.31.2, 6.2 "Emphasis and strong emphasis": the examples of the subset.
SPEC = [
    ("*foo bar*", [("foo bar", "i")]),
    ("a * foo bar*", ["a * foo bar*"]),
    ('a*"foo"*', ['a*"foo"*']),
    ("* a *", ["* a *"]),
    ("foo*bar*", ["foo", ("bar", "i")]),
    ("5*6*78", ["5", ("6", "i"), "78"]),
    ("_foo bar_", [("foo bar", "i")]),
    ("_ foo bar_", ["_ foo bar_"]),
    ('a_"foo"_', ['a_"foo"_']),
    ("foo_bar_", ["foo_bar_"]),
    ("5_6_78", ["5_6_78"]),
    ("пристаням_стремятся_", ["пристаням_стремятся_"]),
    ('aa_"bb"_cc', ['aa_"bb"_cc']),
    ("foo-_(bar)_", ["foo-", ("(bar)", "i")]),
    ("_foo*", ["_foo*"]),
    ("*foo bar *", ["*foo bar *"]),
    ("*(*foo)", ["*(*foo)"]),
    ("*(*foo*)*", [("(foo)", "i")]),
    ("*foo*bar", [("foo", "i"), "bar"]),
    ("_foo bar _", ["_foo bar _"]),
    ("_(_foo)", ["_(_foo)"]),
    ("_(_foo_)_", [("(foo)", "i")]),
    ("_foo_bar", ["_foo_bar"]),
    ("_foo_bar_baz_", [("foo_bar_baz", "i")]),
    ("_(bar)_.", [("(bar)", "i"), "."]),
    ("**foo bar**", [("foo bar", "b")]),
    ("** foo bar**", ["** foo bar**"]),
    ('a**"foo"**', ['a**"foo"**']),
    ("foo**bar**", ["foo", ("bar", "b")]),
    ("__foo bar__", [("foo bar", "b")]),
    ("foo__bar__", ["foo__bar__"]),
    ("__foo, __bar__, baz__", [("foo, bar, baz", "b")]),
    ("*foo**bar**baz*", [("foo", "i"), ("bar", "bi"), ("baz", "i")]),
    ("***foo** bar*", [("foo", "bi"), (" bar", "i")]),
    ("*foo**bar***", [("foo", "i"), ("bar", "bi")]),
    ("foo***bar***baz", ["foo", ("bar", "bi"), "baz"]),
    ("foo******bar*********baz", ["foo", ("bar", "b"), "***baz"]),
    ("**foo*", ["*", ("foo", "i")]),
    ("*foo**", [("foo", "i"), "*"]),
    ("***foo**", ["*", ("foo", "b")]),
    ("**foo***", [("foo", "b"), "*"]),
    ("*foo _bar* baz_", [("foo _bar", "i"), " baz_"]),
    ("*a `*`*", [("a ", "i"), ("*", "ic")]),
    ("_a `_`_", [("a ", "i"), ("_", "ic")]),
    ("***strong emph***", [("strong emph", "bi")]),
    ("\\*not emphasized*", ["*not emphasized*"]),
    ("**foo \\*bar**", [("foo *bar", "b")]),
]


@pytest.mark.parametrize("text,expected", SPEC, ids=[t for t, _ in SPEC])
def test_emphasis_is_commonmarks(text, expected):
    assert parse_inline(text) == runs(*expected)


@pytest.mark.parametrize("text,expected", [
    ("~~Hi~~ Hello", [("Hi", "s"), " Hello"]),
    ("~Hi~", ["~Hi~"]),                      # one tilde is text
    ("~~~Hi~~~", ["~", ("Hi", "s"), "~"]),   # an odd run's one more is text, outside
    ("a ~~ b ~~", ["a ~~ b ~~"]),             # not flanking
    ("**~~both~~**", [("both", "bs")]),
    ("~~(~~~~)~~", [("()", "s")]),            # a run never closes itself
])
def test_strikethrough_is_markdown_its(text, expected):
    assert parse_inline(text) == runs(*expected)


@pytest.mark.parametrize("text,expected", [
    ("`foo`", [("foo", "c")]),
    ("`` foo ` bar ``", [("foo ` bar", "c")]),
    ("` `` `", [("``", "c")]),
    ("`  `", [("  ", "c")]),                  # all spaces: nothing stripped
    ("`foo\\`bar`", [("foo\\", "c"), "bar`"]),  # no escapes inside a code span
    ("`**not bold**`", [("**not bold**", "c")]),
    ("```foo``", ["```foo``"]),               # no closing string of the same length
    ("*a `b*` c*", [("a ", "i"), ("b*", "ic"), (" c", "i")]),
])
def test_code_spans_are_literal_text(text, expected):
    assert parse_inline(text) == runs(*expected)


@pytest.mark.parametrize("text,expected", [
    ("\\*\\_\\~\\`\\\\\\#", ["*_~`\\#"]),
    ("\\a\\1", ["\\a\\1"]),                    # not punctuation: the backslash stays
    ("C:\\path\\to", ["C:\\path\\to"]),
    ("ends in \\", ["ends in \\"]),
])
def test_backslash_escapes(text, expected):
    assert parse_inline(text) == runs(*expected)


@pytest.mark.parametrize("text", [
    "[a link](http://example.com)", "![an image](a.png)", "<b>html</b>", "&amp; &#35;",
    "<http://example.com>", "# not a heading", "> not a quote", "a  \nb",
])
def test_everything_else_is_text(text):
    assert plain(parse_inline(text)) == text
    assert not any(run.formatted or run.code for run in parse_inline(text))


# -- lines and lists ---------------------------------------------------------------------------


def kinds(text: str) -> list[tuple]:
    return [(line.kind, line.depth, line.number, line.text) for line in parse(text)]


def test_each_line_is_its_own_and_blank_lines_collapse():
    assert kinds("\n\n  First line  \nsecond\n\n\n\nthird\n\n") == [
        ("text", 0, None, "First line"), ("text", 0, None, "second"),
        ("blank", 0, None, ""), ("text", 0, None, "third")]


def test_emphasis_does_not_cross_a_line():
    assert parse("**a\nb**") == (Line(runs("**a")), Line(runs("b**")))


def test_bullets_nest_by_content_column():
    assert kinds("- one\n* two\n  + nested\n    - deeper\n - back\n+ top") == [
        ("bullet", 0, None, "one"), ("bullet", 0, None, "two"),
        ("bullet", 1, None, "nested"), ("bullet", 2, None, "deeper"),
        ("bullet", 0, None, "back"), ("bullet", 0, None, "top")]


def test_ordered_lists_are_renumbered_from_their_start():
    assert kinds("3. a\n1. b\n7. c\n   1) x\n   9) y\n8. d") == [
        ("number", 0, 3, "a"), ("number", 0, 4, "b"), ("number", 0, 5, "c"),
        ("number", 1, 1, "x"), ("number", 1, 2, "y"), ("number", 0, 6, "d")]
    lines = parse("1) a\n5. b")              # another delimiter: another list
    assert [(line.number, line.delimiter) for line in lines] == [(1, ")"), (5, ".")]


def test_a_list_goes_on_over_a_blank_line_and_ends_at_text():
    assert kinds("1. a\n\n2. b\ntext\n2. c") == [
        ("number", 0, 1, "a"), ("blank", 0, None, ""), ("number", 0, 2, "b"),
        ("text", 0, None, "text"), ("text", 0, None, "2. c")]   # only 1 interrupts text
    assert kinds("text\n1. a\n-\n- b") == [
        ("text", 0, None, "text"), ("number", 0, 1, "a"), ("bullet", 0, None, ""),
        ("bullet", 0, None, "b")]


def test_a_line_inside_an_item_and_one_outside():
    assert kinds("- item\n  more of it\nnot of it\n\n10. ten\n    more") == [
        ("bullet", 0, None, "item"), ("more", 0, None, "more of it"),
        ("text", 0, None, "not of it"), ("blank", 0, None, ""), ("number", 0, 10, "ten"),
        ("more", 0, None, "more")]


def test_what_is_not_a_list_item():
    assert kinds("-no space\n1.5 percent\n    - code in CommonMark\n---\n* * *") == [
        ("text", 0, None, "-no space"), ("text", 0, None, "1.5 percent"),
        ("text", 0, None, "- code in CommonMark"), ("text", 0, None, "---"),
        ("text", 0, None, "* * *")]
    assert kinds("1234567890. too long") == [("text", 0, None, "1234567890. too long")]


def test_tabs_count_to_four_columns():
    assert kinds("- a\n\t- b") == [("bullet", 0, None, "a"), ("bullet", 1, None, "b")]


def test_inline_marks_work_in_items_and_markers_are_not_text():
    (line,) = parse("- **Total**: *due*")
    assert line.runs == runs(("Total", "b"), ": ", ("due", "i"))


# -- writing -----------------------------------------------------------------------------------


def test_escape_covers_what_could_be_markup():
    assert escape("a*b_c~d`e\\f[g]h<i &amp; & j") == \
        "a\\*b\\_c\\~d\\`e\\\\f\\[g\\]h\\<i \\&amp; & j"
    for text in ["**x**", "a_b_c", "~~s~~", "`c`", "\\*", "[l](u)"]:
        assert parse_inline(escape(text)) == runs(text)


@pytest.mark.parametrize("given", [
    runs(("Total", "b"), " due ", ("now", "i")),
    runs("foo", ("bar", "b"), "baz"),                 # inside a word
    runs(("both", "bi"), " and ", ("struck", "s")),
    runs(("bold ", "b"), "plain"),                    # a space at a mark's end moves out
    runs(("a", "i"), ("b", "b")),                     # neighbours with other marks
    runs(("x", "b"), ("y", "bi"), ("z", "b")),
    runs("a", ("(b)", "b"), "c"),                     # flanking needs another way
    runs(("**literal**", ""), " ", ("code*", "c")),
])
def test_inline_markdown_reads_back(given):
    written = inline_markdown(given)
    back = parse_inline(written)
    assert plain(back) == plain(given)
    if given == runs("a", ("(b)", "b"), "c"):
        assert written == "a(b)c"                       # marks dropped, never the text
    elif given == runs(("bold ", "b"), "plain"):
        assert back == runs(("bold", "b"), " plain")
    else:
        assert back == given


def test_a_line_start_that_would_be_an_item_is_escaped():
    for text in ["- dash", "+ plus", "* star", "1. one", "12) twelve", "# hash", "> quote"]:
        written = to_markdown([Line(runs(text))])
        assert parse(written) == (Line(runs(text)),), (text, written)


@pytest.mark.parametrize("text", [
    "- one\n- two\n  - nested **bold**\n    1. deep\n    2. deeper\n- three",
    "3. a\n4. b\n\n5. c\n   - x\n   more of c",
    "**Head**\n\nline *two*\n- item\n\ntext after",
    "1) a\n2) b",
])
def test_lines_round_trip(text):
    lines = parse(text)
    assert parse(to_markdown(lines)) == lines


def test_lines_markdown_cannot_hold_are_written_as_text():
    lines = (Line(runs("a"), "number", 0, 3, "."), Line(runs("b"), "number", 0, 9, "."))
    written = to_markdown(lines)
    assert [line.text for line in parse(written)] == ["3. a", "9. b"]
    shown = (Line(runs("• a")), Line(runs("• b")))
    assert [line.text for line in parse(to_markdown(lines, fallback=shown))] == ["• a",
                                                                                  "• b"]


# -- agreement with markdown-it-py -------------------------------------------------------------


def _markdown_it_runs(md, text: str) -> tuple[Run, ...]:
    out, marks = [], []
    for token in md.parseInline(text)[0].children:
        kind = token.type
        if kind in ("text", "text_special", "code_inline"):
            out.append(Run(token.content, "strong" in marks, "em" in marks, "s" in marks,
                           kind == "code_inline"))
        elif kind.endswith("_open"):
            marks.append(kind[:-5])
        elif kind.endswith("_close"):
            marks.remove(kind[:-6])
        else:
            raise AssertionError(kind)
    return merge(out)


def test_agrees_with_markdown_it_on_random_text():
    markdown_it = pytest.importorskip("markdown_it")
    md = markdown_it.MarkdownIt("commonmark").enable("strikethrough").disable(
        ["link", "image", "autolink", "html_inline", "entity", "newline"])
    pieces = list("ab *_~`\\.( )!xé—") + ["**", "__", "~~", "***", "``"]
    rng = random.Random(20261020)
    for _ in range(20_000):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 16))).strip(" ")
        if not text or text.endswith("\\"):
            continue
        assert parse_inline(text) == _markdown_it_runs(md, text), text


def test_a_space_at_a_marks_end_does_not_turn_lines_into_text():
    lines = (Line(runs(("Total ", "b"), "due"), "bullet"), Line(runs("next"), "bullet"))
    assert to_markdown(lines) == "- **Total** due\n- next"


@pytest.mark.parametrize("text", ["**a* " * 8000, "*a " * 12000, "~~ a" * 12000,
                                  "`a" * 16000, "_" * 40000])
def test_long_text_is_read_in_linear_time(text):
    import time

    start = time.monotonic()
    found = parse_inline(text)
    inline_markdown(found)
    assert time.monotonic() - start < 5
