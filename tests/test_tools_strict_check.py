"""``strict_problems``: OpenAI's strict-mode rules over any definitions, an application's too."""

from __future__ import annotations

import doctest

import pytest

from ooxml_edit.tools import StrictProblem, Toolbox, shared, strict_problems
from ooxml_edit.tools import adapters
from ooxml_edit.tools.adapters import openai_problems

import tools_toys


def _function(parameters, name="theirs", strict=True):
    return {"type": "function", "name": name, "description": "Theirs.",
            "parameters": parameters, "strict": strict}


def _found(problems):
    """(tool, path, message) of each problem."""
    return [(p.tool, p.path, p.message) for p in problems]


def _plain(**properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def test_the_docstring_example_holds():
    finder = doctest.DocTestFinder()
    runner = doctest.DocTestRunner()
    for test in finder.find(adapters.strict_problems, "strict_problems",
                            globs={"__name__": "doctest"}):
        runner.run(test)
    summary = runner.summarize(verbose=False)
    assert summary.attempted > 0 and summary.failed == 0


def test_a_top_level_any_of_is_found_with_its_tool_and_path():
    """The production 400: one hand-written definition whose parameters were an anyOf."""
    ours = _function(_plain(doc={"type": "string"}), name="ours")
    theirs = _function({"anyOf": [_plain(a={"type": "string"}), _plain(b={"type": "integer"})]})
    problems = strict_problems([ours, theirs])
    assert _found(problems) == [
        ("theirs", "$[1].parameters", "the root must be an object"),
        ("theirs", "$[1].parameters", "'anyOf' at the root; the root must be a plain object")]
    assert str(problems[1]) == ("$[1].parameters (theirs): 'anyOf' at the root; the root must "
                                "be a plain object")


@pytest.mark.parametrize("key, value", [
    ("anyOf", [{"type": "object"}]), ("oneOf", [{"type": "object"}]),
    ("allOf", [{"type": "object"}]), ("enum", [{}]), ("not", {"type": "string"})])
def test_every_composition_at_the_root_is_found_once(key, value):
    schema = dict(_plain(a={"type": "string"}), **{key: value})
    messages = [p.message for p in strict_problems([_function(schema)])
                if p.path == "$[0].parameters"]
    assert messages == [f"{key!r} at the root; the root must be a plain object"]


def test_a_non_strict_function_is_still_held_to_an_object_root():
    """``batch`` is sent ``strict: false``; its root must still be a plain object."""
    loose = _function({"type": "object", "properties": {"x": {"type": "object"}}}, strict=False)
    assert strict_problems([loose]) == []
    rooted = _function({"oneOf": [{"type": "object"}]}, strict=False)
    assert [p.message for p in strict_problems([rooted])] == [
        "the root must be an object", "'oneOf' at the root; the root must be a plain object"]


def test_objects_need_every_property_required_and_additional_properties_false():
    schema = {"type": "object", "additionalProperties": False, "required": ["a"],
              "properties": {
                  "a": {"type": "string"}, "b": {"type": "integer"},
                  "box": {"type": "object", "properties": {"x": {"type": "number"}},
                          "required": ["x"]},
                  "rows": {"type": "array", "items": {
                      "type": ["object", "null"], "properties": {"y": {"type": "string"}},
                      "required": ["y"], "additionalProperties": True}}}}
    assert _found(strict_problems([_function(schema)])) == [
        ("theirs", "$[0].parameters",
         "every property must be required; not in required: b, box, rows"),
        ("theirs", "$[0].parameters.properties.box",
         "additionalProperties must be false (it is missing)"),
        ("theirs", "$[0].parameters.properties.rows.items", "additionalProperties must be false")]


def test_unsupported_keywords_formats_and_nullable_enums():
    schema = _plain(
        name={"type": "string", "minLength": 1, "format": "uri"},
        kind={"type": ["string", "null"], "enum": ["a", "b"]},
        either={"oneOf": [{"type": "string"}, {"type": "integer"}]},
        extra={"type": "object", "properties": {}, "required": [],
               "additionalProperties": False, "patternProperties": {"^x": {}}},
        rule={"type": "string", "if": {"const": "a"}, "then": {"const": "b"}})
    found = _found(strict_problems([_function(schema)]))
    assert ("theirs", "$[0].parameters.properties.name",
            "'minLength' is not supported in strict mode") in found
    assert ("theirs", "$[0].parameters.properties.name", "format 'uri' is not supported") in found
    assert ("theirs", "$[0].parameters.properties.kind", "a nullable enum must list null") in found
    assert ("theirs", "$[0].parameters.properties.either",
            "'oneOf' is not supported in strict mode") in found
    assert ("theirs", "$[0].parameters.properties.extra",
            "'patternProperties' is not supported in strict mode") in found
    assert {m for t, p, m in found if p.endswith(".rule")} == {
        "'if' is not supported in strict mode", "'then' is not supported in strict mode"}
    assert len(found) == 7


def test_any_of_branches_and_defs_are_checked_and_paths_quote_odd_keys():
    schema = {"type": "object", "additionalProperties": False, "required": ["x-y", "node"],
              "properties": {
                  "x-y": {"anyOf": [{"type": "object", "properties": {}}, {"type": "null"}]},
                  "node": {"$ref": "#/$defs/node"}},
              "$defs": {"node": {"type": "object", "properties": {"v": {"type": "string"}},
                                 "additionalProperties": False}}}
    assert _found(strict_problems([_function(schema)])) == [
        ("theirs", '$[0].parameters.properties["x-y"].anyOf[0]',
         "additionalProperties must be false (it is missing)"),
        ("theirs", '$[0].parameters["$defs"].node',
         "every property must be required; not in required: v")]


def test_a_valid_hand_written_schema_with_any_of_defs_and_min_items_passes():
    node = _plain(value={"type": "string"},
                  next={"anyOf": [{"$ref": "#/$defs/node"}, {"type": "null"}]})
    schema = dict(_plain(
        head={"$ref": "#/$defs/node"},
        tags={"type": "array", "items": {"type": "string", "enum": ["a", "b"]}, "minItems": 1},
        when={"type": ["string", "null"], "format": "date-time"}), **{"$defs": {"node": node}})
    assert strict_problems([_function(schema)]) == []


def test_limits_names_strict_and_list_shapes():
    deep = {"type": "string"}
    for _ in range(11):
        deep = _plain(x=deep)
    wide = _plain(**{f"p{i}": {"type": "string", "enum": [f"v{j}" for j in range(101)]}
                     for i in range(10)})
    unstated = _function(_plain(a={"type": "string"}), name="unstated")
    del unstated["strict"]
    definitions = [
        _function(deep, name="deep"), _function(wide, name="wide"),
        _function(_plain(), name="bad name!"), unstated,
        {"type": "namespace", "name": "space", "tools": [
            _function(_plain(a={"type": "string"}), name="inner"),
            _function({"type": "object"}, name="loose_inner")]},
        {"type": "tool_search"},
        {"name": "claude_form", "input_schema": _plain()},
        {"type": "mystery"},
        "not a definition"]
    found = _found(strict_problems(definitions))
    assert found == [
        ("deep", "$[0].parameters", "nested 11 levels; at most 10"),
        ("wide", "$[1].parameters", "1010 enum values; at most 1000"),
        ("bad name!", "$[2].name", "the name 'bad name!' does not match "
         + adapters.OPENAI_NAME.pattern),
        ("unstated", "$[3].strict", "strict is not true"),
        ("loose_inner", "$[4].tools[1].parameters",
         "additionalProperties must be false (it is missing)"),
        ("claude_form", "$[6]", "an Anthropic definition (input_schema), not an OpenAI one"),
        ("", "$[7].type", "unknown tool type 'mystery'"),
        ("", "$[8]", "not a tool definition: str")]


def test_chat_completions_definitions_are_checked_under_function():
    chat = {"type": "function", "function": _function(
        {"type": "object", "properties": {"a": {"type": "string"}}})}
    del chat["function"]["type"]
    assert _found(strict_problems([chat])) == [
        ("theirs", "$[0].function.parameters", "additionalProperties must be false (it is missing)"),
        ("theirs", "$[0].function.parameters", "every property must be required; not in required: a")]


def test_openai_problems_is_the_same_check_as_text():
    bad = [_function({"anyOf": [{"type": "object"}]}),
           _function(_plain(a={"type": "string", "maxLength": 3}), name="other")]
    assert openai_problems(bad) == [p.text() for p in strict_problems(bad)] == [
        "theirs: the root must be an object",
        "theirs: 'anyOf' at the root; the root must be a plain object",
        "theirs.anyOf[0]: additionalProperties must be false (it is missing)",
        "other.a: 'maxLength' is not supported in strict mode"]
    assert isinstance(strict_problems(bad)[0], StrictProblem)


# -- the toolbox's own definitions -------------------------------------------------------------


def _handler(call, **arguments):
    return None


@pytest.mark.parametrize("kind", shared.KINDS)
def test_no_problems_in_the_shared_definitions_for_every_kind(kind):
    tools = [spec.make({kind: _handler}) for spec in shared.SPECS.values()]
    assert strict_problems(tools) == []
    with Toolbox(tools, groups=shared.GROUPS, workers=0) as box:
        for options in ({"defer": False}, {"defer": True}, {"namespaces": True, "defer": True}):
            assert strict_problems(box.definitions("openai-responses", **options)) == []
        chat = box.definitions("openai-chat")
        assert strict_problems(chat) == []
        # what an application sends: these and its own, in one list
        mixed = box.definitions("openai-responses", defer=False) + [
            _function(_plain(q={"type": "string"}), name="their_lookup")]
        assert strict_problems(mixed) == []
        assert any(d.get("name") == "batch" and d["strict"] is False for d in mixed)


def test_no_problems_in_the_toy_formats_definitions():
    with Toolbox(tools_toys.TOOLS, formats=tools_toys.FORMATS, groups=tools_toys.GROUPS) as box:
        assert strict_problems(box.tools.values()) == []
        assert strict_problems(box.definitions("openai-responses", defer=True)) == []
        assert strict_problems(box.definitions("openai-chat")) == []
