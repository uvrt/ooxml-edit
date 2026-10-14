"""Aliases a model naturally writes, read before validation, and refusals that say what would
do: value aliases, argument-name aliases, "did you mean", the retry key and ref names."""

from __future__ import annotations

import datetime as dt

import pytest

from ooxml_edit.tools import (Result, SubsetError, Toolbox, array, check_subset, integer,
                              names_as, shared, string, tool, validate_call)
from ooxml_edit.tools.dispatch import ref_rule
from ooxml_edit.tools.registry import build_schema
from ooxml_edit.tools.schema import CallError, canonical, close_match

import synthetic
import tools_toys

CLOCK = lambda: dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.timezone.utc)  # noqa: E731

FIT = string("Default none.", enum=["none", "normal", "shape"],
             aliases={"shrink": "normal", "resize": "shape"}, optional=True)


@tool("t_fit", "Set how text fits its box.",
      {"doc": string("Document id."), "fit": FIT, "key": string("Retry key.", optional=True)},
      kind="docx", mutates=True)
def t_fit(call, doc, fit="none", key=None):
    return Result(summary=f"fit {fit}", data={"fit": fit})


@tool("t_set", "Set an item's text.",
      {"doc": string("Document id."), "item": integer("Item.", minimum=1),
       "text": string("Text."), "color": string("Colour.", optional=True)},
      kind="docx", mutates=True,
      aliases={"position": "item", "names": lambda a: "text" if a.get("item") == 1 else None})
def t_set(call, doc, item, text, color=None):
    return Result(summary=f"set {item}", data={"item": item, "text": text})


@tool("t_count", "Count to a number, or name columns.",
      {"doc": string("Document id."), "columns": integer("How many.", minimum=1, optional=True),
       "from_columns": array(string(), "Columns by name.", optional=True)},
      kind="docx",
      aliases={"columns": names_as("columns", "from_columns")})
def t_count(call, doc, columns=None, from_columns=None):
    return Result(summary="counted", data={"columns": columns, "from_columns": from_columns})


@tool("t_make", "Make an item, naming it.",
      {"doc": string("Document id."), "ref": string("Ref name.", optional=True),
       "target": string("Where.", optional=True)},
      kind="docx", mutates=True, refs=("target",))
def t_make(call, doc, ref=None, target=None):
    if ref:
        call.define_ref(ref, "item:1")
    return Result(summary="made", data={"target": target})


@shared.handler("edit_chart", kind="docx")
def chart(call, doc, target, action, **given):
    return Result(summary=f"{action} on {target}", data={"target": target})


TOOLS = [t_fit, t_set, t_count, t_make, chart, *shared.SESSION_TOOLS]


@pytest.fixture
def toolbox():
    with Toolbox(TOOLS, formats=[tools_toys.TEXT], groups=shared.GROUPS) as box:
        yield box


@pytest.fixture
def session(toolbox):
    session = toolbox.session(clock=CLOCK)
    session.open(synthetic.outer_package(), "text.docx")
    return session


# -- value aliases -------------------------------------------------------------------------------


def test_a_value_alias_is_read_as_its_value_with_a_warning(toolbox, session):
    result = toolbox.dispatch(session, "t_fit", {"doc": "d1", "fit": "shrink"})
    assert result.ok and result.data == {"fit": "normal"}
    assert result.warnings[0] == "fit 'shrink' read as 'normal'"
    result = toolbox.dispatch(session, "t_fit", {"doc": "d1", "fit": "Resize"})
    assert result.ok and result.data == {"fit": "shape"}


def test_value_aliases_are_never_sent():
    assert "valueAliases" not in str(canonical(t_fit.schema))
    assert t_fit.schema["properties"]["fit"]["valueAliases"] == {"shrink": "normal",
                                                                  "resize": "shape"}


def test_a_value_alias_must_read_as_an_enum_value():
    bad = build_schema({"fit": string("Fit.", enum=["none"], aliases={"shrink": "normal"})})
    with pytest.raises(SubsetError, match="not in the enum"):
        check_subset(bad, allow_bounds=True)
    with pytest.raises(SubsetError, match="outside the subset"):
        check_subset(bad)


def test_value_aliases_inside_a_batch_name_their_op(toolbox, session):
    result = toolbox.dispatch(session, "batch", {"ops": [
        {"tool": "t_fit", "arguments": {"doc": "d1"}},
        {"tool": "t_fit", "arguments": {"doc": "d1", "fit": "shrink"}}]})
    assert result.ok and "ops[1]: fit 'shrink' read as 'normal'" in result.warnings
    result = toolbox.dispatch(session, "batch", {"atomic": True, "ops": [
        {"tool": "t_fit", "arguments": {"doc": "d1", "fit": "shrink"}}]})
    assert result.ok and result.warnings[0] == "ops[0]: fit 'shrink' read as 'normal'"


# -- did you mean --------------------------------------------------------------------------------


def test_an_enum_refusal_says_did_you_mean(toolbox, session):
    result = toolbox.dispatch(session, "t_fit", {"doc": "d1", "fit": "Normal"})
    assert result.error.code == "invalid_arguments"
    assert "did you mean 'normal'?" in result.error.message
    assert result.error.valid_options == ["none", "normal", "shape"]
    result = toolbox.dispatch(session, "t_fit", {"doc": "d1", "fit": "sideways"})
    assert "did you mean" not in result.error.message


def test_an_unknown_argument_says_did_you_mean(toolbox, session):
    result = toolbox.dispatch(session, "t_set", {"doc": "d1", "item": 2, "text": "a",
                                                 "colour": "accent1"})
    assert result.error.field == "colour"
    assert result.error.message == "unknown argument 'colour'; did you mean 'color'?"


def test_close_match():
    assert close_match("Accent-1", ["accent_1", "b"]) == "accent_1"
    assert close_match("insde_end", ["inside_end", "inside_base"]) == "inside_end"
    assert close_match("zzz", ["inside_end"]) is None


def test_validate_call_notes():
    notes: list[str] = []
    schema = build_schema({"items": array(build_param())})
    assert validate_call(schema, {"items": [{"fit": "SHRINK"}]}, notes) == {
        "items": [{"fit": "normal"}]}
    assert notes == ["items[0].fit 'SHRINK' read as 'normal'"]
    with pytest.raises(CallError):
        validate_call(schema, {"items": [{"fit": "squash"}]})


def build_param():
    from ooxml_edit.tools import obj
    return obj({"fit": FIT})


# -- argument-name aliases -----------------------------------------------------------------------


def test_an_argument_alias_is_renamed_with_a_warning(toolbox, session):
    result = toolbox.dispatch(session, "t_set", {"doc": "d1", "position": 2, "text": "a"})
    assert result.ok and result.data == {"item": 2, "text": "a"}
    assert result.warnings == ["position read as item"]


def test_an_alias_and_its_name_together_are_refused(toolbox, session):
    result = toolbox.dispatch(session, "t_set", {"doc": "d1", "position": 2, "item": 2,
                                                 "text": "a"})
    assert result.error.code == "invalid_arguments" and result.error.field == "position"
    assert "give item only" in result.error.message


def test_a_conditional_alias_applies_only_when_it_says_so(toolbox, session):
    result = toolbox.dispatch(session, "t_set", {"doc": "d1", "item": 1, "names": "x"})
    assert result.ok and result.data["text"] == "x"
    result = toolbox.dispatch(session, "t_set", {"doc": "d1", "item": 2, "names": "x",
                                                 "text": "y"})
    assert result.error.message.startswith("unknown argument 'names'")


def test_a_parameter_whose_value_can_only_mean_another(toolbox, session):
    result = toolbox.dispatch(session, "t_count", {"doc": "d1", "columns": ["a", "b"]})
    assert result.ok and result.data == {"columns": None, "from_columns": ["a", "b"]}
    assert result.warnings == ["columns read as from_columns"]
    result = toolbox.dispatch(session, "t_count", {"doc": "d1", "columns": 3})
    assert result.ok and result.data == {"columns": 3, "from_columns": None}
    assert result.warnings == []
    # Numbers written as strings, or names given with from_columns, are not read as it.
    result = toolbox.dispatch(session, "t_count", {"doc": "d1", "columns": ["3"]})
    assert result.error.field == "columns" and not result.warnings
    result = toolbox.dispatch(session, "t_count", {"doc": "d1", "columns": ["a"],
                                                   "from_columns": ["a"]})
    assert result.error.field == "columns"


def test_alias_definitions_are_checked():
    with pytest.raises(SubsetError, match="which is a parameter"):
        tool("t_x", "X.", {"doc": string("Doc."), "a": string("A.")},
             aliases={"a": "doc"})(lambda call, doc, a: None)
    with pytest.raises(SubsetError, match="which is not a parameter"):
        tool("t_x", "X.", {"doc": string("Doc.")}, aliases={"b": "c"})(lambda call, doc: None)


def test_edit_chart_reads_slide_as_target_when_adding(toolbox, session):
    result = toolbox.dispatch(session, "edit_chart", {"doc": "d1", "action": "add",
                                                      "slide": "p:1"})
    assert result.ok and result.data == {"target": "p:1"}
    assert result.warnings == ["slide read as target"]
    result = toolbox.dispatch(session, "edit_chart", {"doc": "d1", "action": "set_title",
                                                      "target": "c:1", "slide": "p:1"})
    assert result.error.message.startswith("unknown argument 'slide'")


def test_edit_chart_reads_from_columns_as_columns(toolbox, session):
    result = toolbox.dispatch(session, "edit_chart", {"doc": "d1", "action": "add",
                                                      "target": "p:1", "from_columns": ["a"]})
    # Read as columns: which without from is edit_chart's own refusal.
    assert result.error.field == "columns" and "give from too" in result.error.message
    assert result.warnings == ["from_columns read as columns"]


# -- the retry key and ref names -----------------------------------------------------------------


def test_a_key_on_a_tool_without_one_explains_why(toolbox, session):
    result = toolbox.dispatch(session, "t_set", {"doc": "d1", "item": 1, "text": "a",
                                                 "key": "k1"})
    assert result.error.field == "key"
    assert "retry key is for the tools that create something" in result.error.message
    assert "safe to repeat" in result.error.message


def test_a_ref_name_refusal_says_the_rule_and_a_name_that_would_do(toolbox, session):
    result = toolbox.dispatch(session, "t_make", {"doc": "d1", "ref": "catChart"})
    assert result.error.field == "ref"
    assert "starts with a lowercase letter" in result.error.message
    assert "'cat_chart'" in result.error.message
    assert "^" not in result.error.message


@pytest.mark.parametrize("name, suggestion", [
    ("catChart", "cat_chart"), ("Sales-2024", "sales_2024"), ("2nd", "r_2nd"),
    ("big chart", "big_chart"), ("x" * 40, "x" * 32), ("--", "r1")])
def test_ref_rule_suggests_a_valid_name(name, suggestion):
    from ooxml_edit.tools.dispatch import REF
    assert f"e.g. {suggestion!r}" in ref_rule(name) and REF.match(suggestion)


def test_a_capitalised_use_of_a_known_ref_says_did_you_mean(toolbox, session):
    assert toolbox.dispatch(session, "t_make", {"doc": "d1", "ref": "cat_chart"}).ok
    result = toolbox.dispatch(session, "t_make", {"doc": "d1", "target": "$catChart"})
    assert result.error.code == "not_found" and "did you mean $cat_chart?" in result.error.message
    # Something that only looks like one stays an address.
    result = toolbox.dispatch(session, "t_make", {"doc": "d1", "target": "$A$1"})
    assert result.ok and result.data == {"target": "$A$1"}
