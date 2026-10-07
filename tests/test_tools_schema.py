"""The common strict subset, the call validator, and tool definitions built on them."""

from __future__ import annotations

import pytest

from ooxml_edit.tools import (CallError, SubsetError, Tool, array, boolean, build_schema,
                              canonical, check_subset, integer, number, obj, string, tool,
                              validate_call)
from ooxml_edit.tools.schema import is_path_name, subset_problems, walk_properties

import tools_toys


def _schema(**params):
    return build_schema(params)


# -- the subset --------------------------------------------------------------------------------


def test_a_schema_built_with_the_helpers_is_in_the_subset():
    schema = _schema(
        doc=string("Document id."),
        when=string("A date.", format="date", optional=True),
        kind=string("Kind.", enum=["a", "b"]),
        box=obj({"x": number("Left, pt."), "y": number("Top, pt.")}, "A box in points."),
        rows=array(obj({"text": string("Cell text.")}, "A row."), "Rows.", min_items=1),
        bold=boolean("Bold.", optional=True))
    check_subset(schema)
    assert schema["required"] == ["doc", "kind", "box", "rows"]
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("change, message", [
    (lambda s: s["properties"]["a"].update(type=["string", "null"]), "type union"),
    (lambda s: s["properties"]["a"].update(oneOf=[]), "oneOf"),
    (lambda s: s["properties"]["a"].update(pattern="^x$"), "pattern"),
    (lambda s: s["properties"]["a"].update(default="x"), "default"),
    (lambda s: s["properties"]["a"].update(format="uri"), "format"),
    (lambda s: s["properties"]["a"].pop("description"), "no description"),
    (lambda s: s["properties"]["a"].update(enum=[1, 2]), "strings"),
    (lambda s: s["properties"]["a"].update(enum=[str(i) for i in range(51)]), "at most 50"),
    (lambda s: s.update(additionalProperties=True), "additionalProperties"),
    (lambda s: s["properties"].update(Bad={"type": "string", "description": "x"}), "name"),
    (lambda s: s["properties"].update(n={"type": "integer", "description": "x", "minimum": 1}),
     "minimum"),
    (lambda s: s["properties"].update(r={"$ref": "#/x"}), "$ref"),
    (lambda s: s["properties"].update(l={"type": "array", "description": "x",
                                         "items": {"type": "string"}, "minItems": 2}), "minItems"),
    (lambda s: s.update(required=["a", "zz"]), "not a property"),
])
def test_what_leaves_the_subset_is_named(change, message):
    schema = _schema(a=string("A."))
    change(schema)
    problems = subset_problems(schema)
    assert any(message in problem for problem in problems), problems


def test_only_top_level_properties_need_a_description():
    nested = obj({"bold": boolean(optional=True), "x": number()}, optional=True)
    schema = _schema(a=string("A."), box=nested,
                     sizes=array(number(), "Sizes, pt.", optional=True))
    assert "description" not in schema["properties"]["box"]
    assert subset_problems(schema) == ["$.box: no description"]
    schema["properties"]["box"]["description"] = "A box."
    assert subset_problems(schema) == []
    assert "description" not in schema["properties"]["sizes"]["items"]


def test_nesting_is_limited_to_six_levels():
    inner = string("Leaf.")
    for level in range(6):
        inner = obj({"next": inner}, f"Level {level}.")
    assert not any("deeper" in p for p in subset_problems(_schema(top=inner)))
    inner = obj({"next": inner}, "Level 6.")
    assert any("deeper" in p for p in subset_problems(_schema(top=inner)))


def test_a_plural_tool_may_hold_a_text_spec():
    run = obj({"text": string("Text."), "bold": boolean("Bold.", optional=True)}, "A run.")
    paragraph = obj({"runs": array(run, "Runs.")}, "A paragraph.")
    item = obj({"target": string("Address."), "paragraphs": array(paragraph, "Paragraphs.")},
               "One item.")
    assert subset_problems(_schema(items=array(item, "Items."))) == []


def test_bounds_are_allowed_only_in_a_validation_schema_and_canonical_strips_them():
    schema = _schema(n=integer("N.", minimum=1, maximum=9), s=string("S.", max_length=5),
                     l=array(string("x"), "L.", max_items=3))
    assert subset_problems(schema)
    check_subset(schema, allow_bounds=True)
    stripped = canonical(schema)
    check_subset(stripped)
    assert "minimum" not in stripped["properties"]["n"] and "minimum" in schema["properties"]["n"]


def test_walk_properties_finds_nested_optional_ones():
    schema = _schema(a=string("A."), b=obj({"c": string("C.", optional=True)}, "B.",
                                           optional=True))
    assert [(n, r) for n, _, r in walk_properties(schema)] == [
        ("a", True), ("b", False), ("b.c", False)]


# -- no paths ----------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["path", "out_path", "file", "image_file", "filename", "dir",
                                  "output_directory", "folder"])
def test_a_path_parameter_is_refused_when_the_tool_is_made(name):
    assert is_path_name(name)
    with pytest.raises(SubsetError, match="path parameter"):
        tool("toy_x", "X.", {name: string("Somewhere.")})(lambda call, **kw: None)


@pytest.mark.parametrize("name", ["profile", "filter", "direction", "image", "blob", "pathway"])
def test_names_that_only_look_like_paths_are_fine(name):
    assert not is_path_name(name)


def test_no_toy_tool_accepts_a_path():
    for item in tools_toys.TOOLS:
        for name, node, _ in walk_properties(item.canonical):
            assert not is_path_name(name.split(".")[-1].strip("[]")), (item.name, name)
            assert "path" not in node.get("description", "").lower(), (item.name, name)


# -- tool definitions --------------------------------------------------------------------------


def test_a_tool_name_must_be_lower_snake_case():
    with pytest.raises(SubsetError, match="name"):
        tool("Toy-X", "X.", {})(lambda call: None)


def test_exactly_one_must_name_optional_parameters():
    with pytest.raises(SubsetError, match="required"):
        tool("toy_x", "X.", {"text": string("T."), "spec": string("S.", optional=True)},
             exactly_one=[("text", "spec")])(lambda call, **kw: None)


def test_same_definitions_merge_their_kinds_and_different_ones_clash():
    params = {"doc": string("Doc.")}
    first = tool("toy_x", "X.", params, kind="a")(lambda call, doc: 1)
    second = tool("toy_x", "X.", params, kind="b")(lambda call, doc: 2)
    merged = first.merged(second)
    assert sorted(merged.handlers) == ["a", "b"]
    other = tool("toy_x", "Y.", params, kind="c")(lambda call, doc: 3)
    with pytest.raises(ValueError):
        first.merged(other)


def test_optional_parameters_are_counted_at_every_depth():
    item = tool("toy_x", "X.", {
        "a": string("A.", optional=True),
        "b": obj({"c": string("C.", optional=True), "d": string("D.")}, "B.")})(lambda c, **k: 0)
    assert item.optional_parameters() == ["a", "b.c"]
    assert isinstance(item, Tool)


# -- the call validator ------------------------------------------------------------------------


SCHEMA = _schema(
    doc=string("Doc."),
    page=integer("Page.", minimum=1),
    scale=number("Scale.", minimum=0, maximum=4, optional=True),
    align=string("Align.", enum=["left", "right"], optional=True),
    when=string("When.", format="date", optional=True),
    stamp=string("Stamp.", format="date-time", optional=True),
    box=obj({"x": number("X."), "w": number("W.", minimum=0)}, "Box.", optional=True),
    rows=array(string("Row."), "Rows.", max_items=2, optional=True),
    bold=boolean("Bold.", optional=True))


def test_a_valid_call_comes_back_normalised():
    clean = validate_call(SCHEMA, {"doc": "d1", "page": 2.0, "align": None, "bold": False,
                                   "box": {"x": 1, "w": 2.5}, "when": "2026-10-06",
                                   "stamp": "2026-10-06T10:00:00Z"})
    assert clean == {"doc": "d1", "page": 2, "bold": False, "box": {"x": 1, "w": 2.5},
                     "when": "2026-10-06", "stamp": "2026-10-06T10:00:00Z"}
    assert isinstance(clean["page"], int)


@pytest.mark.parametrize("arguments, field, options", [
    ({"page": 1}, "doc", []),
    ({"doc": "d1", "page": 0}, "page", []),
    ({"doc": "d1", "page": 1.5}, "page", []),
    ({"doc": "d1", "page": True}, "page", []),
    ({"doc": "d1", "page": 1, "scale": 5}, "scale", []),
    ({"doc": "d1", "page": 1, "align": "middle"}, "align", ["left", "right"]),
    ({"doc": "d1", "page": 1, "when": "06-10-2026"}, "when", []),
    ({"doc": "d1", "page": 1, "stamp": "2026-10-06"}, "stamp", []),
    ({"doc": "d1", "page": 1, "box": {"x": 1}}, "box.w", []),
    ({"doc": "d1", "page": 1, "box": {"x": 1, "w": -1}}, "box.w", []),
    ({"doc": "d1", "page": 1, "rows": ["a", "b", "c"]}, "rows", []),
    ({"doc": "d1", "page": 1, "rows": ["a", 3]}, "rows[1]", []),
    ({"doc": "d1", "page": 1, "colour": "red"}, "colour", sorted(SCHEMA["properties"])),
])
def test_an_invalid_call_names_the_field_and_the_options(arguments, field, options):
    with pytest.raises(CallError) as caught:
        validate_call(SCHEMA, arguments)
    assert caught.value.field == field
    assert caught.value.valid_options == options


def test_the_arguments_must_be_an_object():
    with pytest.raises(CallError):
        validate_call(SCHEMA, ["d1"])
