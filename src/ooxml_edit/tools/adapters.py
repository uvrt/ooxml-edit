"""Provider adapters: tool definitions and results as each API wants them, as plain dicts.

One canonical schema per tool (see :mod:`.schema`) becomes:

* **Anthropic Messages** (:func:`to_anthropic`): optional properties left out of
  ``required``; ``strict: true`` on as many tools as the per-request strict limits allow,
  writing tools first (:func:`anthropic_strict_plan`); the rest are sent non-strict, and the
  dispatcher validates every call anyway.  Optionally ``defer_loading`` on the non-core tools
  with the BM25 tool-search tool, and ``cache_control`` on the last tool that is not deferred.
* **OpenAI Responses** (:func:`to_openai_responses`): every property required, an optional
  one made nullable, ``strict: true`` everywhere; optionally each group as a ``namespace``
  with ``defer_loading`` and the ``tool_search`` tool.
* **OpenAI Chat Completions** (:func:`to_openai_chat`), the fallback: the same parameters,
  nested under ``function``.

Results become ``tool_result`` blocks (Anthropic), ``function_call_output`` items (OpenAI
Responses) or a tool message plus a user message carrying the images (Chat Completions,
whose tool messages are text only).  Nothing here imports a provider SDK, and the checkers
(:func:`anthropic_problems`, :func:`openai_problems`) state each provider's documented strict
rules so a test can hold the output to them.

Provider facts, from the official documentation as of 2026-10-06:

* Claude strict tool use: at most 20 strict tools, 24 optional parameters and 16 union-typed
  parameters per request, across all strict schemas
  (platform.claude.com/docs/en/build-with-claude/structured-outputs#explicit-limits).
* A tool with ``defer_loading`` cannot carry ``cache_control``; at least one tool must not be
  deferred (platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool).
* OpenAI strict mode: every property required, ``additionalProperties: false``, a root object
  that is not ``anyOf``; no ``allOf``/``not``/``if``/``dependent*``; 5000 properties, 10
  levels, 1000 enum values (developers.openai.com/api/docs/guides/structured-outputs).
* A Responses ``function_call_output.output`` is a string or a list of ``input_text``,
  ``input_image`` and ``input_file``; a Chat Completions tool message is text only.
"""

from __future__ import annotations

import base64
import copy
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .registry import CORE, Tool, ToolGroup
from .results import Result

# -- provider limits ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrictLimits:
    """Claude's per-request strict-mode limits, summed over every strict tool."""

    tools: int = 20
    optional_parameters: int = 24
    union_parameters: int = 16


ANTHROPIC_STRICT_LIMITS = StrictLimits()
ANTHROPIC_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
ANTHROPIC_TOOL_SEARCH = {"type": "tool_search_tool_bm25_20251119", "name": "tool_search_tool_bm25"}
#: Keywords Claude's strict mode accepts in a schema.
ANTHROPIC_KEYWORDS = frozenset({
    "type", "description", "properties", "required", "additionalProperties", "items", "enum",
    "const", "anyOf", "allOf", "$ref", "$defs", "definitions", "default", "format", "pattern",
    "minItems", "title"})

OPENAI_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
OPENAI_UNSUPPORTED = frozenset({
    "allOf", "not", "if", "then", "else", "dependentRequired", "dependentSchemas",
    "patternProperties", "minLength", "maxLength", "unevaluatedProperties"})
OPENAI_FORMATS = frozenset({"date-time", "time", "date", "duration", "email", "hostname",
                            "ipv4", "ipv6", "uuid"})
OPENAI_MAX_PROPERTIES = 5000
OPENAI_MAX_DEPTH = 10
OPENAI_MAX_ENUM_VALUES = 1000


def _ordered(tools: Iterable[Tool]) -> list[Tool]:
    """Core tools first, the rest after, each in registration order."""
    tools = list(tools)
    return [t for t in tools if t.core] + [t for t in tools if not t.core]


# -- Anthropic ---------------------------------------------------------------------------------


def anthropic_strict_plan(tools: Iterable[Tool],
                          limits: StrictLimits = ANTHROPIC_STRICT_LIMITS) -> dict[str, bool]:
    """Which tools are sent ``strict: true``: in priority order, while the limits hold.

    Priority is the tool's ``priority`` (lower first), else writing tools before reading
    ones, then registration order.  A tool that would break a limit is sent non-strict and
    the next one is still tried, so a small tool after an option-heavy one keeps strict.
    The canonical subset has no unions, so the union limit is never reached.  A tool made
    with ``strict=False`` (``batch``) is never strict.
    """
    tools = list(tools)
    order = sorted(range(len(tools)), key=lambda i: (
        tools[i].priority if tools[i].priority is not None else (0 if tools[i].mutates else 1),
        i))
    plan = {tool.name: False for tool in tools}
    count = optional = 0
    for index in order:
        tool = tools[index]
        if not tool.strict:
            continue
        cost = len(tool.optional_parameters())
        if count + 1 <= limits.tools and optional + cost <= limits.optional_parameters:
            plan[tool.name] = True
            count += 1
            optional += cost
    return plan


def to_anthropic(tools: Iterable[Tool], *, defer: bool = False, cache: bool = True,
                 strict: bool = True,
                 limits: StrictLimits = ANTHROPIC_STRICT_LIMITS) -> list[dict[str, Any]]:
    """Claude tool definitions for ``tools`` (the Messages API's ``tools`` list)."""
    tools = _ordered(tools)
    plan = anthropic_strict_plan(tools, limits) if strict else {}
    deferring = defer and any(not tool.core for tool in tools)
    definitions: list[dict[str, Any]] = [dict(ANTHROPIC_TOOL_SEARCH)] if deferring else []
    for tool in tools:
        definition: dict[str, Any] = {"name": tool.name, "description": tool.description,
                                      "input_schema": tool.canonical}
        definition["strict"] = bool(plan.get(tool.name, False))
        if deferring and not tool.core:
            definition["defer_loading"] = True
        definitions.append(definition)
    if cache:
        loaded = [d for d in definitions if not d.get("defer_loading")]
        if loaded:
            loaded[-1]["cache_control"] = {"type": "ephemeral"}
    return definitions


def anthropic_tool_result(result: Result, tool_use_id: str, *,
                          limit: int | None = None) -> dict[str, Any]:
    """One ``tool_result`` block: the envelope as text, then any images."""
    content: list[dict[str, Any]] = [{"type": "text", "text": result.to_text(limit)}]
    for image in result.images:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": image.media_type,
            "data": base64.b64encode(image.data).decode("ascii")}})
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id,
                             "content": content}
    if not result.ok:
        block["is_error"] = True
    return block


def anthropic_user_message(results: Sequence[tuple[str, Result]], *,
                           limit: int | None = None, text: str | None = None) -> dict[str, Any]:
    """The user message answering one assistant turn: every ``tool_result``, in call order,
    first; any text after them."""
    content = [anthropic_tool_result(result, call_id, limit=limit) for call_id, result in results]
    if text:
        content.append({"type": "text", "text": text})
    return {"role": "user", "content": content}


def anthropic_problems(definitions: Sequence[Mapping[str, Any]],
                       limits: StrictLimits = ANTHROPIC_STRICT_LIMITS) -> list[str]:
    """Where a Claude ``tools`` list breaks the documented rules for strict tool use."""
    problems: list[str] = []
    strict_tools = optional = unions = 0
    loaded = 0
    names = set()
    for definition in definitions:
        if "type" in definition and "input_schema" not in definition:
            loaded += 1  # a server tool (tool search)
            continue
        name = definition.get("name", "")
        if not ANTHROPIC_NAME.match(name):
            problems.append(f"{name!r}: the name does not match {ANTHROPIC_NAME.pattern}")
        if name in names:
            problems.append(f"{name!r}: named twice")
        names.add(name)
        allowed = {"name", "description", "input_schema", "strict", "defer_loading",
                   "cache_control", "input_examples", "allowed_callers"}
        for key in definition:
            if key not in allowed:
                problems.append(f"{name}: {key!r} is not a tool definition property")
        if definition.get("defer_loading") and "cache_control" in definition:
            problems.append(f"{name}: a deferred tool cannot carry cache_control")
        if not definition.get("defer_loading"):
            loaded += 1
        schema = definition.get("input_schema") or {}
        if schema.get("type") != "object":
            problems.append(f"{name}: input_schema must be an object")
        if definition.get("strict"):
            strict_tools += 1
            for where, node in _nodes(schema):
                for key in node:
                    if key not in ANTHROPIC_KEYWORDS:
                        problems.append(f"{name}{where}: {key!r} is not supported in strict mode")
                if node.get("type") == "object" and node.get("additionalProperties") is not False:
                    problems.append(f"{name}{where}: additionalProperties must be false")
                if "minItems" in node and node["minItems"] not in (0, 1):
                    problems.append(f"{name}{where}: minItems above 1")
                if isinstance(node.get("type"), list) or "anyOf" in node:
                    unions += 1
                if node.get("type") == "object":
                    required = set(node.get("required", ()))
                    optional += sum(1 for key in node.get("properties", {}) if key not in required)
    if loaded == 0 and definitions:
        problems.append("every tool is deferred; at least one must not be")
    if strict_tools > limits.tools:
        problems.append(f"{strict_tools} strict tools; at most {limits.tools} per request")
    if optional > limits.optional_parameters:
        problems.append(f"{optional} optional parameters in strict tools; at most "
                        f"{limits.optional_parameters}")
    if unions > limits.union_parameters:
        problems.append(f"{unions} union-typed parameters in strict tools; at most "
                        f"{limits.union_parameters}")
    return problems


# -- OpenAI ------------------------------------------------------------------------------------


def openai_strict_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """The canonical schema in OpenAI's strict form: all required, optional ones nullable."""

    def convert(node: Mapping[str, Any]) -> dict[str, Any]:
        node = dict(node)
        if node.get("type") == "object":
            required = set(node.get("required", ()))
            properties = {}
            for name, child in node.get("properties", {}).items():
                child = convert(child)
                if name not in required:
                    child = _nullable(child)
                properties[name] = child
            node["properties"] = properties
            node["required"] = list(properties)
            node["additionalProperties"] = False
        elif node.get("type") == "array" and isinstance(node.get("items"), Mapping):
            node["items"] = convert(node["items"])
        return node

    return convert(copy.deepcopy(dict(schema)))


def _nullable(node: dict[str, Any]) -> dict[str, Any]:
    kind = node.get("type")
    if isinstance(kind, str):
        node["type"] = [kind, "null"]
    if "enum" in node and None not in node["enum"]:
        node["enum"] = list(node["enum"]) + [None]
    if node.get("description"):
        node["description"] = node["description"].rstrip() + " Null when not used."
    return node


def _function(tool: Tool) -> dict[str, Any]:
    if not tool.strict:
        # Sent as written, ``strict: false`` stated: omitted, the Responses API would
        # normalise it into strict mode, which a free-form object cannot take.
        return {"type": "function", "name": tool.name, "description": tool.description,
                "parameters": tool.canonical, "strict": False}
    return {"type": "function", "name": tool.name, "description": tool.description,
            "parameters": openai_strict_schema(tool.canonical), "strict": True}


def to_openai_responses(tools: Iterable[Tool], *, defer: bool = False,
                        namespaces: bool = False,
                        groups: Iterable[ToolGroup] = ()) -> list[dict[str, Any]]:
    """OpenAI Responses tool definitions.  With ``namespaces`` each non-core group is a
    ``namespace``; with ``defer`` its functions are ``defer_loading`` and ``tool_search``
    is added (gpt-5.4 and later)."""
    tools = _ordered(tools)
    descriptions = {group.name: group.description for group in groups}
    definitions: list[dict[str, Any]] = []
    spaces: dict[str, dict[str, Any]] = {}
    deferring = defer and any(not tool.core for tool in tools)
    for tool in tools:
        function = _function(tool)
        if deferring and not tool.core:
            function["defer_loading"] = True
        if namespaces and not tool.core:
            space = spaces.get(tool.group)
            if space is None:
                space = spaces[tool.group] = {
                    "type": "namespace", "name": tool.group,
                    "description": descriptions.get(tool.group, f"The {tool.group} tools."),
                    "tools": []}
                definitions.append(space)
            space["tools"].append(function)
        else:
            definitions.append(function)
    if deferring:
        definitions.append({"type": "tool_search"})
    return definitions


def to_openai_chat(tools: Iterable[Tool]) -> list[dict[str, Any]]:
    """OpenAI Chat Completions tool definitions (the fallback; no tool search)."""
    definitions = []
    for tool in _ordered(tools):
        function = _function(tool)
        definitions.append({"type": "function", "function": {
            "name": function["name"], "description": function["description"],
            "parameters": function["parameters"], "strict": function["strict"]}})
    return definitions


def openai_function_call_output(result: Result, call_id: str, *,
                                limit: int | None = None,
                                detail: str = "auto") -> dict[str, Any]:
    """A Responses ``function_call_output`` item: a string, or text and images."""
    text = result.to_text(limit)
    if not result.images:
        return {"type": "function_call_output", "call_id": call_id, "output": text}
    output: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
    for image in result.images:
        output.append({"type": "input_image", "image_url": _data_url(image),
                       "detail": detail})
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def openai_responses_items(results: Sequence[tuple[str, Result]], *,
                           limit: int | None = None) -> list[dict[str, Any]]:
    """One ``function_call_output`` per ``call_id``, in call order."""
    return [openai_function_call_output(result, call_id, limit=limit)
            for call_id, result in results]


def openai_chat_messages(results: Sequence[tuple[str, Result]], *, limit: int | None = None,
                         detail: str = "auto") -> list[dict[str, Any]]:
    """Chat Completions messages for one assistant turn's tool calls.

    A tool message is text only, so each one says where its images are, and a single user
    message after all of them -- tool messages must follow the assistant message directly --
    carries every image, labelled with its call.
    """
    messages: list[dict[str, Any]] = []
    parts: list[dict[str, Any]] = []
    for call_id, result in results:
        text = result.to_text(limit)
        if result.images:
            text += f"\n[{len(result.images)} image(s) from this call follow in the next user message]"
            parts.append({"type": "text", "text": f"Images from tool call {call_id}:"})
            for image in result.images:
                parts.append({"type": "image_url",
                              "image_url": {"url": _data_url(image), "detail": detail}})
        messages.append({"role": "tool", "tool_call_id": call_id, "content": text})
    if parts:
        messages.append({"role": "user", "content": parts})
    return messages


def _data_url(image: Any) -> str:
    return f"data:{image.media_type};base64," + base64.b64encode(image.data).decode("ascii")


def openai_problems(definitions: Sequence[Mapping[str, Any]], *, chat: bool = False) -> list[str]:
    """Where an OpenAI ``tools`` list breaks the documented strict-mode rules.

    A function sent ``strict: false`` (``batch``) is held to the general rules only: its
    name, an object root, and ``strict`` stated rather than left to the API's default.
    """
    problems: list[str] = []
    functions: list[Mapping[str, Any]] = []
    for definition in definitions:
        kind = definition.get("type")
        if kind == "namespace":
            if chat:
                problems.append("namespaces exist in the Responses API only")
            if not definition.get("name") or not definition.get("tools"):
                problems.append("a namespace needs a name and tools")
            functions += [tool for tool in definition.get("tools", ())]
        elif kind == "tool_search":
            if chat:
                problems.append("tool_search exists in the Responses API only")
        elif kind == "function":
            if chat and not isinstance(definition.get("function"), Mapping):
                problems.append(f"{definition.get('name')!r}: a Chat Completions tool nests "
                                "its definition under function")
                continue
            functions.append(definition["function"] if chat else definition)
        else:
            problems.append(f"unknown tool type {kind!r}")
    for function in functions:
        name = function.get("name", "")
        if not OPENAI_NAME.match(name):
            problems.append(f"{name!r}: the name does not match {OPENAI_NAME.pattern}")
        schema = function.get("parameters") or {}
        if schema.get("type") != "object" or "anyOf" in schema:
            problems.append(f"{name}: the root must be an object, not anyOf")
        if function.get("strict") is False:
            continue
        if function.get("strict") is not True:
            problems.append(f"{name}: strict is not true")
        properties = depth_max = enum_values = 0
        for where, node in _nodes(schema):
            depth_max = max(depth_max, where.count(".") + where.count("[]"))
            for key in node:
                if key in OPENAI_UNSUPPORTED:
                    problems.append(f"{name}{where}: {key!r} is not supported in strict mode")
            if "format" in node and node["format"] not in OPENAI_FORMATS:
                problems.append(f"{name}{where}: format {node['format']!r} is not supported")
            types = node.get("type")
            types = types if isinstance(types, list) else [types]
            if "object" in types:
                keys = list(node.get("properties", {}))
                properties += len(keys)
                if node.get("additionalProperties") is not False:
                    problems.append(f"{name}{where}: additionalProperties must be false")
                if sorted(node.get("required", ())) != sorted(keys):
                    problems.append(f"{name}{where}: every property must be required")
            if "enum" in node:
                enum_values += len(node["enum"])
                if "null" in types and None not in node["enum"]:
                    problems.append(f"{name}{where}: a nullable enum must list null")
        if properties > OPENAI_MAX_PROPERTIES:
            problems.append(f"{name}: {properties} properties; at most {OPENAI_MAX_PROPERTIES}")
        if depth_max > OPENAI_MAX_DEPTH:
            problems.append(f"{name}: nested {depth_max} levels; at most {OPENAI_MAX_DEPTH}")
        if enum_values > OPENAI_MAX_ENUM_VALUES:
            problems.append(f"{name}: {enum_values} enum values; at most {OPENAI_MAX_ENUM_VALUES}")
    return problems


def _nodes(schema: Mapping[str, Any], where: str = "") -> Iterable[tuple[str, Mapping[str, Any]]]:
    """Every schema node with its dotted location."""
    yield where, schema
    for name, child in (schema.get("properties") or {}).items():
        if isinstance(child, Mapping):
            yield from _nodes(child, f"{where}.{name}")
    items = schema.get("items")
    if isinstance(items, Mapping):
        yield from _nodes(items, f"{where}[]")


# -- one entry point per direction -------------------------------------------------------------

PROVIDERS = ("anthropic", "openai-responses", "openai-chat")


def definitions_for(provider: str, tools: Iterable[Tool], **options: Any) -> list[dict[str, Any]]:
    if provider == "anthropic":
        return to_anthropic(tools, **options)
    if provider == "openai-responses":
        return to_openai_responses(tools, **options)
    if provider == "openai-chat":
        return to_openai_chat(tools)
    raise ValueError(f"unknown provider {provider!r}; one of {', '.join(PROVIDERS)}")


def results_for(provider: str, results: Sequence[tuple[str, Result]], *,
                limit: int | None = None) -> Any:
    """What answers one assistant turn: a user message (Anthropic), a list of items
    (Responses) or a list of messages (Chat Completions)."""
    if provider == "anthropic":
        return anthropic_user_message(results, limit=limit)
    if provider == "openai-responses":
        return openai_responses_items(results, limit=limit)
    if provider == "openai-chat":
        return openai_chat_messages(results, limit=limit)
    raise ValueError(f"unknown provider {provider!r}; one of {', '.join(PROVIDERS)}")

