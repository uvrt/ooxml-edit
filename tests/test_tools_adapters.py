"""The three provider adapters, checked offline against each provider's documented rules."""

from __future__ import annotations

import base64
import dataclasses
import json

import pytest

from ooxml_edit.tools import (Image, Result, ToolError, Toolbox, anthropic_image_tokens,
                              integer, obj, openai_image_tokens, string, tool)
from ooxml_edit.tools import adapters
from ooxml_edit.tools.adapters import (StrictLimits, anthropic_problems, anthropic_strict_plan,
                                       openai_problems, openai_strict_schema)

import tools_toys


@pytest.fixture
def toolbox():
    with Toolbox(tools_toys.TOOLS, formats=tools_toys.FORMATS, groups=tools_toys.GROUPS) as box:
        yield box


def _many(count: int, optional: int, *, mutates: bool = True, prefix: str = "w"):
    """``count`` tools with ``optional`` optional parameters each."""
    made = []
    for n in range(count):
        params = {"doc": string("Doc.")}
        params.update({f"o{i}": string("Optional.", optional=True) for i in range(optional)})
        made.append(tool(f"{prefix}{n}", "A tool.", params, mutates=mutates,
                         group="core" if n < 3 else "extra")(lambda call, **kw: None))
    return made


# -- Anthropic ---------------------------------------------------------------------------------


def _late_required():
    """A tool written with an optional property before a required one, at the top and in
    its items, as word_insert_markdown was (doc, markdown, blob, at)."""
    from ooxml_edit.tools import Tool, array

    schema = {"type": "object", "properties": {
        "doc": {"type": "string", "description": "Doc."},
        "markdown": {"type": "string", "description": "Text."},
        "at": {"type": "string", "description": "Where."},
        "rows": {"type": "array", "description": "Rows.", "items": {
            "type": "object", "properties": {"note": {"type": "string"},
                                             "cells": {"type": "integer"}},
            "required": ["cells"], "additionalProperties": False}}},
        "required": ["doc", "at", "rows"], "additionalProperties": False}
    return Tool("late", "A tool.", schema, handlers={None: lambda call, **kw: None},
                mutates=True)


def test_the_builders_list_required_properties_first_in_the_given_order():
    made = tool("ordered", "A tool.",
                {"doc": string("Doc."), "markdown": string("Text.", optional=True),
                 "blob": string("Blob.", optional=True), "at": string("Where."),
                 "box": obj({"x": integer(optional=True), "y": integer()}, "Box.")},
                mutates=True)(lambda call, **kw: None)
    assert list(made.schema["properties"]) == ["doc", "at", "box", "markdown", "blob"]
    assert list(made.schema["properties"]["box"]["properties"]) == ["y", "x"]
    assert made.schema["required"] == ["doc", "at", "box"]


def test_every_adapter_sends_required_properties_first_at_every_level():
    late = _late_required()
    for provider in ("anthropic", "openai-responses", "openai-chat"):
        (sent,) = [d for d in adapters.definitions_for(provider, [late])
                   if d.get("name") == "late" or d.get("function", {}).get("name") == "late"]
        schema = (sent.get("input_schema") or sent.get("parameters")
                  or sent["function"]["parameters"])
        assert list(schema["properties"]) == ["doc", "at", "rows", "markdown"], provider
        assert list(schema["properties"]["rows"]["items"]["properties"]) == ["cells", "note"]
    anthropic = adapters.to_anthropic([late])
    assert anthropic[0]["strict"] is True and anthropic_problems(anthropic) == []


def test_anthropic_problems_name_a_required_property_after_an_optional_one():
    late = _late_required()
    sent = adapters.to_anthropic([late])
    sent[0]["input_schema"] = late.canonical          # as written, not as the adapter sends it
    problems = anthropic_problems(sent)
    assert any("late.at: a required property after an optional one" in p for p in problems)
    assert any("late.rows[].cells" in p for p in problems)
    sent[0]["strict"] = False                         # only strict tools are held to it
    assert anthropic_problems(sent) == []



def test_anthropic_definitions_meet_the_documented_rules(toolbox):
    definitions = toolbox.definitions("anthropic", defer=False)
    assert anthropic_problems(definitions) == []
    by_name = {d["name"]: d for d in definitions}
    title = by_name["toy_ppt_set_title"]
    assert title["strict"] is True
    assert title["input_schema"]["required"] == ["doc", "page", "text"]
    assert "minimum" not in title["input_schema"]["properties"]["page"]
    assert definitions[-1]["cache_control"] == {"type": "ephemeral"}
    assert sum("cache_control" in d for d in definitions) == 1


def test_anthropic_strict_stays_within_the_per_request_limits():
    tools = _many(30, 1)
    roomy = StrictLimits(string_parameters=1000)
    plan = anthropic_strict_plan(tools, roomy)
    assert sum(plan.values()) == 20
    definitions = adapters.to_anthropic(tools, limits=roomy)
    assert anthropic_problems(definitions, roomy) == []
    assert [d["strict"] for d in definitions].count(True) == 20


def test_anthropic_strict_keeps_the_grammar_small():
    tools = _many(30, 1)                      # two free-text strings each
    plan = anthropic_strict_plan(tools)
    assert sum(plan.values()) == 16           # 32 strings
    assert anthropic_problems(adapters.to_anthropic(tools)) == []
    readers = _many(5, 0, mutates=False, prefix="r")
    assert not any(anthropic_strict_plan(readers).values())
    assert all(anthropic_strict_plan(readers, StrictLimits(reads=True)).values())


def test_anthropic_strict_counts_optional_parameters_across_tools():
    heavy = _many(3, 10, prefix="h")          # 30 optional parameters in all
    light = _many(5, 0, prefix="l")
    plan = anthropic_strict_plan(heavy + light)
    assert [plan[t.name] for t in heavy] == [True, True, False]   # 20, then 30 > 24
    assert all(plan[t.name] for t in light)                        # small tools still strict
    assert anthropic_problems(adapters.to_anthropic(heavy + light)) == []


def test_anthropic_writing_tools_get_strict_first():
    readers = _many(15, 0, mutates=False, prefix="r")
    writers = _many(15, 0, mutates=True, prefix="w")
    plan = anthropic_strict_plan(readers + writers, StrictLimits(reads=True))
    assert all(plan[t.name] for t in writers)
    assert sum(plan[t.name] for t in readers) == 5


def test_anthropic_strict_follows_the_formats_first_list():
    readers = _many(3, 10, mutates=False, prefix="r")
    writers = _many(3, 10, mutates=True, prefix="w")
    reads = StrictLimits(reads=True, string_parameters=1000)
    plan = anthropic_strict_plan(readers + writers, reads, first=["r2", "w1"])
    assert [name for name, strict in plan.items() if strict] == ["r2", "w1"]  # 20 of 24
    definitions = adapters.to_anthropic(readers + writers, strict_first=["r1", "w2"],
                                        limits=reads)
    assert {d["name"] for d in definitions if d["strict"]} == {"r1", "w2"}


def test_the_toolbox_takes_the_strict_order_from_its_formats():
    first = tools_toys.FORMATS[0]
    with Toolbox(tools_toys.TOOLS, formats=[dataclasses.replace(first, strict_first=(
            "toy_word_insert",)), *tools_toys.FORMATS[1:]], groups=tools_toys.GROUPS) as box:
        assert box.strict_first == ["toy_word_insert"]
        plan = adapters.anthropic_strict_plan(box.tools.values(), first=box.strict_first)
        assert plan["toy_word_insert"] and not plan["toy_ppt_render"]   # readers: never
    with Toolbox(tools_toys.TOOLS, formats=tools_toys.FORMATS,
                 strict_first=["toy_docx_read"]) as box:
        assert box.strict_first == ["toy_docx_read"]


def test_by_default_every_tool_is_sent_with_tool_search_and_picked_groups_are_loaded(toolbox):
    everything = toolbox.definitions("anthropic")
    assert everything[0]["type"].startswith("tool_search_tool_bm25")
    deferred = {d["name"] for d in everything if d.get("defer_loading")}
    assert deferred == {t.name for t in toolbox.tools.values() if not t.core}
    assert anthropic_problems(everything) == []
    picked = toolbox.definitions("anthropic", groups=["toy_render"])
    assert not any(d.get("defer_loading") or "type" in d for d in picked)
    assert {d["name"] for d in picked} == {t.name for t in toolbox.select("toy_render")}
    spaced = toolbox.definitions("openai-responses")
    assert any(d["type"] == "namespace" for d in spaced) and spaced[-1] == {"type": "tool_search"}
    assert openai_problems(spaced) == []
    flat = toolbox.definitions("openai-chat")
    assert openai_problems(flat, chat=True) == []
    with pytest.raises(ValueError, match="no tool search"):
        toolbox.definitions("openai-chat", defer=True)


def test_allowed_tools_narrow_calls_without_changing_the_tools(toolbox):
    choice = toolbox.allowed_tools(["toy_render"])
    names = [entry["name"] for entry in choice["tools"]]
    assert choice["type"] == "allowed_tools" and choice["mode"] == "auto"
    assert set(names) == {t.name for t in toolbox.select("toy_render")}
    chat = toolbox.allowed_tools("toy_render", provider="openai-chat")
    assert [entry["function"]["name"] for entry in chat["allowed_tools"]["tools"]] == names


def test_anthropic_problems_catch_a_broken_request():
    tools = _many(25, 0)
    definitions = adapters.to_anthropic(tools, strict=False)
    for d in definitions:
        d["strict"] = True
    definitions[0]["input_schema"]["properties"]["doc"]["minimum"] = 1
    definitions[1]["defer_loading"] = True
    definitions[1]["cache_control"] = {"type": "ephemeral"}
    problems = anthropic_problems(definitions)
    assert any("25 strict tools" in p for p in problems)
    assert any("minimum" in p for p in problems)
    assert any("deferred tool" in p for p in problems)
    wider = StrictLimits(tools=30)
    assert anthropic_problems(adapters.to_anthropic(tools, limits=wider), wider) == []


def test_anthropic_deferred_loading_keeps_core_tools_loaded_and_cached():
    tools = _many(6, 0)
    definitions = adapters.to_anthropic(tools, defer=True)
    assert definitions[0] == adapters.ANTHROPIC_TOOL_SEARCH
    loaded = [d for d in definitions[1:] if not d.get("defer_loading")]
    assert [d["name"] for d in loaded] == ["w0", "w1", "w2"]
    assert "cache_control" in loaded[-1]
    assert not any("cache_control" in d for d in definitions if d.get("defer_loading"))
    assert anthropic_problems(definitions) == []


def test_anthropic_tool_result_text_and_image():
    result = Result(summary="Rendered", images=[Image(tools_toys.png(4, 2), 4, 2)])
    block = adapters.anthropic_tool_result(result, "toolu_1")
    assert block["type"] == "tool_result" and block["tool_use_id"] == "toolu_1"
    assert "is_error" not in block
    text, image = block["content"]
    assert text["type"] == "text" and json.loads(text["text"])["summary"] == "Rendered"
    assert image == {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                  "data": base64.b64encode(tools_toys.png(4, 2)).decode()}}


def test_anthropic_error_result_sets_is_error_and_parallel_results_share_one_message():
    error = Result.failure(ToolError("not_found", "no page 9", valid_options=["1", "2"]))
    message = adapters.anthropic_user_message([("a", Result(summary="ok")), ("b", error)],
                                              text="Continue.")
    assert message["role"] == "user"
    kinds = [block["type"] for block in message["content"]]
    assert kinds == ["tool_result", "tool_result", "text"]   # results first, text after
    assert message["content"][1]["is_error"] is True
    body = json.loads(message["content"][1]["content"][0]["text"])
    assert body["error"] == {"code": "not_found", "message": "no page 9",
                             "valid_options": ["1", "2"]}


# -- OpenAI Responses --------------------------------------------------------------------------


def test_responses_definitions_meet_the_strict_rules(toolbox):
    definitions = toolbox.definitions("openai-responses", defer=False)
    assert openai_problems(definitions) == []
    render = next(d for d in definitions if d.get("name") == "toy_ppt_render")
    assert render["strict"] is True and render["type"] == "function"
    params = render["parameters"]
    assert params["required"] == ["doc", "page", "width", "stall"]
    assert params["properties"]["width"]["type"] == ["integer", "null"]
    assert params["properties"]["page"]["type"] == "integer"


def test_responses_nullable_enums_list_null_and_nested_objects_are_strict():
    item = tool("toy_x", "X.", {
        "align": string("Align.", enum=["l", "r"], optional=True),
        "box": obj({"x": integer("X."), "w": integer("W.", optional=True)}, "Box.",
                   optional=True)})(lambda call, **kw: None)
    schema = openai_strict_schema(item.canonical)
    assert schema["properties"]["align"]["enum"] == ["l", "r", None]
    box = schema["properties"]["box"]
    assert box["type"] == ["object", "null"]
    assert box["required"] == ["x", "w"] and box["properties"]["w"]["type"] == ["integer", "null"]
    assert openai_problems([{"type": "function", "name": "toy_x", "parameters": schema,
                             "strict": True}]) == []


def test_responses_namespaces_and_tool_search(toolbox):
    definitions = toolbox.definitions("openai-responses", namespaces=True, defer=True)
    assert openai_problems(definitions) == []
    spaces = {d["name"]: d for d in definitions if d["type"] == "namespace"}
    assert set(spaces) == {"toy_render", "toy_read"}
    assert spaces["toy_render"]["description"] == "Rendering pages to images."
    assert all(f["defer_loading"] for s in spaces.values() for f in s["tools"])
    assert len(spaces["toy_read"]["tools"]) < 10
    assert definitions[-1] == {"type": "tool_search"}
    top = [d for d in definitions if d["type"] == "function"]
    assert top and not any(d.get("defer_loading") for d in top)


def test_responses_output_with_an_image_is_a_list():
    image = Image(tools_toys.png(4, 2), 4, 2)
    item = adapters.openai_function_call_output(Result(summary="r", images=[image]), "call_1")
    assert item["type"] == "function_call_output" and item["call_id"] == "call_1"
    text, picture = item["output"]
    assert text["type"] == "input_text" and json.loads(text["text"])["ok"] is True
    assert picture == {"type": "input_image", "detail": "auto",
                       "image_url": "data:image/png;base64,"
                       + base64.b64encode(image.data).decode()}
    plain = adapters.openai_function_call_output(Result(summary="r"), "call_2")
    assert isinstance(plain["output"], str)


# -- OpenAI Chat Completions -------------------------------------------------------------------


def test_chat_definitions_nest_under_function(toolbox):
    definitions = toolbox.definitions("openai-chat")
    assert openai_problems(definitions, chat=True) == []
    first = definitions[0]
    assert set(first) == {"type", "function"}
    assert set(first["function"]) == {"name", "description", "parameters", "strict"}
    with pytest.raises(AssertionError):
        assert openai_problems(toolbox.definitions("openai-responses", namespaces=True,
                                                   defer=True), chat=True) == []


def test_chat_images_go_in_a_user_message_after_every_tool_message():
    image = Image(tools_toys.png(4, 2), 4, 2)
    messages = adapters.openai_chat_messages([("c1", Result(summary="r", images=[image])),
                                              ("c2", Result(summary="s"))])
    assert [m["role"] for m in messages] == ["tool", "tool", "user"]
    assert all(isinstance(m["content"], str) for m in messages[:2])
    assert "next user message" in messages[0]["content"]
    parts = messages[2]["content"]
    assert parts[0] == {"type": "text", "text": "Images from tool call c1:"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert len(adapters.openai_chat_messages([("c3", Result())])) == 1


# -- rendering through the toolbox, sizes and tokens -------------------------------------------


def test_the_toolbox_renders_a_result_for_each_provider(toolbox):
    result = Result(summary="r", images=[Image(tools_toys.png(4, 2), 4, 2)])
    assert toolbox.render_result("anthropic", result, "t")["type"] == "tool_result"
    assert toolbox.render_result("openai-responses", result, "t")["type"] == "function_call_output"
    assert [m["role"] for m in toolbox.render_result("openai-chat", result, "t")] == ["tool", "user"]
    with pytest.raises(ValueError):
        toolbox.definitions("gemini")


def test_a_long_result_is_cut_to_the_limit_and_says_so():
    result = Result(summary="big", data="x" * 50_000)
    text = result.to_text(24_000)
    assert len(text) <= 24_000
    body = json.loads(text)
    assert body["data"] is None and "50" in body["truncated"]


@pytest.mark.parametrize("size, claude, openai", [
    ((1280, 720), 1196, 1104),     # a slide at the default width
    ((1000, 1294), 1692, 1575),    # a Letter page at 1000 px
    ((1000, 1414), 1836, 1728),    # an A4 page at 1000 px
])
def test_image_token_estimates(size, claude, openai):
    assert anthropic_image_tokens(*size) == claude
    assert openai_image_tokens(*size) == openai


def test_claude_image_tokens_follow_the_downscale():
    assert anthropic_image_tokens(5000, 5000) <= 4784
    assert anthropic_image_tokens(3000, 1000, max_edge=1568, max_tokens=1568) <= 1568


def test_a_result_with_huge_lists_still_cuts_to_valid_json():
    result = Result(summary="big", changed=[f"256.{n}" for n in range(5000)],
                    warnings=["w" * 100] * 500)
    text = result.to_text(2000)
    body = json.loads(text)
    assert len(text) <= 2000 and body["changed"] == {"omitted": 5000}
