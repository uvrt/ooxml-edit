"""Provider adapters: tool definitions and results as each API wants them, as plain dicts.

One canonical schema per tool (see :mod:`.schema`) becomes:

* **Anthropic Messages** (:func:`to_anthropic`): optional properties left out of
  ``required`` and listed after the required ones at every level (strict decoding writes
  keys in schema order, so an optional property before a required one would be lost); ``strict: true`` on as many tools as the per-request strict limits allow,
  writing tools first (:func:`anthropic_strict_plan`); the rest are sent non-strict, and the
  dispatcher validates every call anyway.  Optionally ``defer_loading`` on the non-core tools
  with the BM25 tool-search tool, and ``cache_control`` on the last tool that is not deferred.
* **OpenAI Responses** (:func:`to_openai_responses`): every property required, an optional
  one made nullable and listed after the ones the tool requires, ``strict: true`` everywhere; optionally each group as a ``namespace``
  with ``defer_loading`` and the ``tool_search`` tool.
* **OpenAI Chat Completions** (:func:`to_openai_chat`), the fallback: the same parameters,
  nested under ``function``.

Results become ``tool_result`` blocks (Anthropic), ``function_call_output`` items (OpenAI
Responses) or a tool message plus a user message carrying the images (Chat Completions,
whose tool messages are text only).  Nothing here imports a provider SDK, and the checkers
(:func:`anthropic_problems`, :func:`openai_problems`) state each provider's documented strict
rules so a test can hold the output to them; :func:`strict_problems` is the public form of
the OpenAI one, for an application's own definitions as well as these.

Provider facts, from the official documentation as of 2026-10-06:

* Claude strict tool use: at most 20 strict tools, 24 optional parameters and 16 union-typed
  parameters per request, across all strict schemas
  (platform.claude.com/docs/en/build-with-claude/structured-outputs#explicit-limits).
* A tool with ``defer_loading`` cannot carry ``cache_control``; at least one tool must not be
  deferred (platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool).
* OpenAI strict mode: every property required, ``additionalProperties: false``, a root object
  that is not ``anyOf``; no ``allOf``/``not``/``if``/``dependent*`` (``oneOf`` is not among
  the supported keywords); ``minItems``/``maxItems`` supported except on fine-tuned models;
  5000 properties, 10 levels, 1000 enum values
  (developers.openai.com/api/docs/guides/structured-outputs, checked again 2026-10-13).
* A Responses ``function_call_output.output`` is a string or a list of ``input_text``,
  ``input_image`` and ``input_file``; a Chat Completions tool message is text only.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .registry import Tool, ToolGroup
from .results import Result
from .schema import required_after_optional, required_first

# -- provider limits ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrictLimits:
    """Claude's per-request strict-mode limits, summed over every strict tool."""

    tools: int = 20
    optional_parameters: int = 24
    union_parameters: int = 16
    #: Not documented: past about 33 free-text strings (string properties and array items
    #: with no enum) over the strict tools, Claude returns 400 "Schema is too complex"
    #: (measured on Sonnet 5.5, 2026-10-07: 34 compiled beside the rest of a request, 35
    #: did not).  It is a guard, not the whole rule: some 32-string sets failed too, which
    #: is why reading tools are not sent strict by default (``reads``).
    string_parameters: int = 32
    #: Whether reading tools may be strict.  Their arguments are an id and a few options,
    #: which the dispatcher checks as well; strict grammar is spent on writing tools.
    reads: bool = False


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
    "allOf", "oneOf", "not", "if", "then", "else", "dependentRequired", "dependentSchemas",
    "patternProperties", "minLength", "maxLength", "unevaluatedProperties"})
OPENAI_FORMATS = frozenset({"date-time", "time", "date", "duration", "email", "hostname",
                            "ipv4", "ipv6", "uuid"})
OPENAI_MAX_PROPERTIES = 5000
OPENAI_MAX_DEPTH = 10
OPENAI_MAX_ENUM_VALUES = 1000


def free_strings(schema: Mapping[str, Any]) -> int:
    """String properties and array items without an enum, at any depth: what makes a strict
    grammar large."""
    return sum(1 for _, node in _nodes(schema)
               if node.get("type") == "string" and "enum" not in node)


def _ordered(tools: Iterable[Tool]) -> list[Tool]:
    """Core tools first, the rest after, each in registration order."""
    tools = list(tools)
    return [t for t in tools if t.core] + [t for t in tools if not t.core]


# -- Anthropic ---------------------------------------------------------------------------------


def anthropic_strict_plan(tools: Iterable[Tool],
                          limits: StrictLimits = ANTHROPIC_STRICT_LIMITS, *,
                          first: Sequence[str] = ()) -> dict[str, bool]:
    """Which tools are sent ``strict: true``: in priority order, while the limits hold.

    Priority is the order of ``first`` (the format libraries' lists of their most-used
    writing tools, :attr:`~.session.DocumentFormat.strict_first`), then the tool's
    ``priority`` (lower first), else writing tools before reading ones, then registration
    order.  A tool that would break a limit is sent non-strict and
    the next one is still tried, so a small tool after an option-heavy one keeps strict.
    The canonical subset has no unions, so the union limit is never reached.  A tool made
    with ``strict=False`` (``batch``) is never strict.
    """
    tools = list(tools)
    rank = {name: index for index, name in enumerate(dict.fromkeys(first))}
    order = sorted(range(len(tools)), key=lambda i: (
        rank.get(tools[i].name, len(rank)),
        tools[i].priority if tools[i].priority is not None else (0 if tools[i].mutates else 1),
        i))
    plan = {tool.name: False for tool in tools}
    count = optional = strings = 0
    for index in order:
        tool = tools[index]
        if not tool.strict or not (tool.mutates or limits.reads):
            continue
        cost = len(tool.optional_parameters())
        text = free_strings(tool.canonical)
        if (count + 1 <= limits.tools and optional + cost <= limits.optional_parameters
                and strings + text <= limits.string_parameters):
            plan[tool.name] = True
            count += 1
            optional += cost
            strings += text
    return plan


def to_anthropic(tools: Iterable[Tool], *, defer: bool = False, cache: bool = True,
                 strict: bool = True, limits: StrictLimits = ANTHROPIC_STRICT_LIMITS,
                 strict_first: Sequence[str] = ()) -> list[dict[str, Any]]:
    """Claude tool definitions for ``tools`` (the Messages API's ``tools`` list).

    With ``defer`` every non-core tool is sent ``defer_loading: true`` and the BM25
    tool-search tool comes first; ``cache_control`` goes on the last tool that is loaded.
    The strict limits are counted over every strict tool sent, deferred or not.
    """
    tools = _ordered(tools)
    plan = anthropic_strict_plan(tools, limits, first=strict_first) if strict else {}
    deferring = defer and any(not tool.core for tool in tools)
    definitions: list[dict[str, Any]] = [dict(ANTHROPIC_TOOL_SEARCH)] if deferring else []
    for tool in tools:
        definition: dict[str, Any] = {"name": tool.name, "description": tool.description,
                                      "input_schema": required_first(tool.canonical)}
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
    strict_tools = optional = unions = strings = 0
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
            strings += free_strings(schema)
            for late in required_after_optional(schema):
                problems.append(f"{name}.{late}: a required property after an optional one; "
                                "strict decoding loses the optional one once it is written")
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
    if strings > limits.string_parameters:
        problems.append(f"{strings} free-text strings in strict tools; above "
                        f"{limits.string_parameters} Claude answers 'Schema is too complex'")
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

    return convert(required_first(schema))


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
                "parameters": required_first(tool.canonical), "strict": False}
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


#: Where the Responses adapter puts a result's images: in the ``function_call_output``
#: (``output`` is then a list of ``input_text`` and ``input_image``, which OpenAI's
#: function-calling guide documents) or in a user message after the outputs, as Chat
#: Completions must -- for a deployment that takes images only in messages.
OPENAI_IMAGE_PLACEMENTS = ("output", "message")
#: ``input_image``'s ``detail``: OpenAI's images guide lists these (``original`` on the
#: models that support it); ``auto``, the API's own default, is the adapters' default.
OPENAI_DETAILS = ("auto", "low", "high", "original")


def _check_image_options(images: str, detail: str) -> None:
    if images not in OPENAI_IMAGE_PLACEMENTS:
        raise ValueError(f"images={images!r}; one of {', '.join(OPENAI_IMAGE_PLACEMENTS)}")
    if detail not in OPENAI_DETAILS:
        raise ValueError(f"detail={detail!r}; one of {', '.join(OPENAI_DETAILS)}")


def openai_function_call_output(result: Result, call_id: str, *,
                                limit: int | None = None,
                                detail: str = "auto") -> dict[str, Any]:
    """A Responses ``function_call_output`` item: a string, or text and images."""
    _check_image_options("output", detail)
    text = result.to_text(limit)
    if not result.images:
        return {"type": "function_call_output", "call_id": call_id, "output": text}
    output: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
    for image in result.images:
        output.append({"type": "input_image", "image_url": _data_url(image),
                       "detail": detail})
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def openai_responses_items(results: Sequence[tuple[str, Result]], *,
                           limit: int | None = None, images: str = "output",
                           detail: str = "auto") -> list[dict[str, Any]]:
    """One ``function_call_output`` per ``call_id``, in call order.

    ``images="output"`` (the default) puts each result's images in its own output;
    ``images="message"`` keeps every output text -- each saying its images follow -- and
    adds one user message after them carrying every image, labelled with its call.
    ``detail`` is each ``input_image``'s: ``auto`` (default), ``low``, ``high``, ``original``.
    """
    _check_image_options(images, detail)
    if images == "output":
        return [openai_function_call_output(result, call_id, limit=limit, detail=detail)
                for call_id, result in results]
    items: list[dict[str, Any]] = []
    parts: list[dict[str, Any]] = []
    for call_id, result in results:
        text = result.to_text(limit)
        if result.images:
            text += f"\n[{len(result.images)} image(s) from this call follow in the next user message]"
            parts.append({"type": "input_text", "text": f"Images from tool call {call_id}:"})
            for image in result.images:
                parts.append({"type": "input_image", "image_url": _data_url(image),
                              "detail": detail})
        items.append({"type": "function_call_output", "call_id": call_id, "output": text})
    if parts:
        items.append({"type": "message", "role": "user", "content": parts})
    return items


#: Items of a Responses ``output`` that go back in the next ``input`` as they came (with
#: ``store=False``, a reasoning item's ``encrypted_content`` with it): the adapters never
#: make them, and :func:`openai_input_problems` lets them pass.  A ``tool_search_call`` with
#: ``execution: "server"`` was run by the API, its ``tool_search_output`` follows it, and
#: neither is dispatched; a ``function_call`` from a deferred namespace carries the bare
#: tool ``name`` and the group in ``namespace`` (OpenAI's tool-search guide).
OPENAI_MODEL_ITEMS = frozenset({"reasoning", "function_call", "tool_search_call",
                                "tool_search_output"})


def openai_input_problems(items: Sequence[Any]) -> list[str]:
    """Where a Responses ``input`` breaks the documented shapes of what answers tool calls:
    an output that is not a string or a list of ``input_text``, ``input_image`` and
    ``input_file``; an image without ``image_url`` or ``file_id``; a ``detail`` outside
    :data:`OPENAI_DETAILS`; an image in a message that is not the user's.  The whole next
    input may be passed: the model's own items (:data:`OPENAI_MODEL_ITEMS`), a message
    whose content is a string and an assistant's ``output_text`` pass, as do SDK objects
    (anything with ``model_dump``).  Offline: what a real request accepts is the API's to
    say."""
    problems: list[str] = []

    def image(where: str, part: Mapping[str, Any]) -> None:
        if not (part.get("image_url") or part.get("file_id")):
            problems.append(f"{where}: an input_image needs image_url or file_id")
        if part.get("detail", "auto") not in OPENAI_DETAILS:
            problems.append(f"{where}: detail {part.get('detail')!r} is not one of "
                            f"{', '.join(OPENAI_DETAILS)}")

    for index, item in enumerate(items):
        where = f"item {index}"
        if not isinstance(item, Mapping) and hasattr(item, "model_dump"):
            item = item.model_dump(exclude_none=True)
        kind = item.get("type", "message" if "role" in item else None)
        if kind in OPENAI_MODEL_ITEMS:
            continue
        if kind == "function_call_output":
            if not item.get("call_id"):
                problems.append(f"{where}: no call_id")
            output = item.get("output")
            if isinstance(output, str):
                continue
            if not isinstance(output, list) or not output:
                problems.append(f"{where}: output is a string or a non-empty list")
                continue
            for part in output:
                if part.get("type") == "input_image":
                    image(where, part)
                elif part.get("type") not in ("input_text", "input_file"):
                    problems.append(f"{where}: {part.get('type')!r} cannot be in an output")
        elif kind == "message":
            content = item.get("content")
            if isinstance(content, str):
                continue
            user = item.get("role") == "user"
            allowed = ("input_text", "input_file") if user else ("input_text", "output_text",
                                                                 "refusal")
            for part in content or ():
                if part.get("type") == "input_image":
                    if user:
                        image(where, part)
                    else:
                        problems.append(f"{where}: images go back in a user message")
                elif part.get("type") not in allowed:
                    problems.append(f"{where}: {part.get('type')!r} cannot be in a "
                                    f"{item.get('role')!r} message")
        else:
            problems.append(f"{where}: unexpected item type {kind!r}")
    return problems


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
    """Where an OpenAI ``tools`` list breaks the documented strict-mode rules, as text.

    A function sent ``strict: false`` (``batch``) is held to the general rules only: its
    name, an object root, and ``strict`` stated rather than left to the API's default.
    :func:`strict_problems` reports the same problems with each one's tool and JSON path,
    and takes :class:`~.registry.Tool` objects and both OpenAI forms in one list.
    """
    problems: list[str] = []
    for index, definition in enumerate(definitions):
        for problem in _definition_problems(definition, f"$[{index}]", chat=chat):
            problems.append(problem.text())
    return problems


@dataclass(frozen=True)
class StrictProblem:
    """One place where a tool definition breaks OpenAI's documented strict-mode rules.

    ``tool`` is the function's name (``""`` for a list entry with none, such as a namespace
    without a name); ``path`` is a JSON path into the list that was checked, such as
    ``$[3].parameters.properties.items.items``; ``message`` says what is wrong.
    """

    tool: str
    path: str
    message: str
    #: The dotted location below the tool's root (``.items[]``), as :func:`openai_problems`
    #: writes it; empty for the root.
    where: str = ""

    def text(self) -> str:
        return f"{self.tool}{self.where}: {self.message}" if self.tool else self.message

    def __str__(self) -> str:
        return f"{self.path} ({self.tool or '-'}): {self.message}"


def strict_problems(definitions: Iterable[Any]) -> list[StrictProblem]:
    """Every OpenAI strict-mode problem in ``definitions``, each with its tool and JSON path.

    Run it once over everything an application will send -- this library's definitions and
    its own hand-written ones -- before the first call: one bad definition makes the API
    refuse the whole request (a 400), whichever tool the model would have called.

    ``definitions`` may mix, item by item:

    * :class:`~.registry.Tool` objects (the neutral form), checked as
      :func:`to_openai_responses` sends them; their paths point into that form
      (``$[0].parameters...``);
    * OpenAI Responses definitions: ``function``, ``namespace`` and ``tool_search`` items;
    * OpenAI Chat Completions definitions, the function nested under ``function``.

    What it reports (OpenAI's structured-outputs guide, "Supported schemas"): a root that
    is not an object or carries ``anyOf``, ``oneOf``, ``allOf``, ``enum`` or ``not``; an
    object whose properties are not all in ``required``, or whose ``additionalProperties``
    is missing or not ``false``; a keyword strict mode does not take (:data:`OPENAI_UNSUPPORTED`)
    or a ``format`` outside :data:`OPENAI_FORMATS`; a nullable enum without ``null``;
    ``strict`` not stated; a name outside :data:`OPENAI_NAME`; more than
    :data:`OPENAI_MAX_PROPERTIES` properties, :data:`OPENAI_MAX_DEPTH` levels or
    :data:`OPENAI_MAX_ENUM_VALUES` enum values in one tool.  ``anyOf`` branches and
    ``$defs`` are checked as well.  A function sent ``strict: false`` is held to its name and
    an object root only.  An Anthropic definition (``input_schema``) is reported as one:
    pass the :class:`~.registry.Tool` or its OpenAI form.

    Offline and documented rules only; what a deployment accepts is still the API's to say.

    >>> from ooxml_edit.tools.adapters import strict_problems
    >>> theirs = {"type": "function", "name": "lookup", "strict": True,
    ...           "parameters": {"anyOf": [{"type": "object"}, {"type": "string"}]}}
    >>> for problem in strict_problems([theirs]):
    ...     print(problem)
    $[0].parameters (lookup): the root must be an object
    $[0].parameters (lookup): 'anyOf' at the root; the root must be a plain object
    $[0].parameters.anyOf[0] (lookup): additionalProperties must be false (it is missing)
    """
    problems: list[StrictProblem] = []
    for index, definition in enumerate(definitions):
        base = f"$[{index}]"
        if isinstance(definition, Tool):
            problems += _function_problems(_function(definition), base)
        elif not isinstance(definition, Mapping):
            problems.append(StrictProblem("", base, f"not a tool definition: "
                                          f"{type(definition).__name__}"))
        elif definition.get("type") == "function" and "function" in definition:
            problems += _definition_problems(definition, base, chat=True)
        else:
            problems += _definition_problems(definition, base, chat=False)
    return problems


#: Keywords that may not stand at the root of a function's parameters, which must be a
#: plain object.
OPENAI_ROOT_KEYWORDS = ("anyOf", "oneOf", "allOf", "enum", "not")


def _definition_problems(definition: Mapping[str, Any], base: str, *,
                         chat: bool) -> list[StrictProblem]:
    """One entry of a ``tools`` list: a function, namespace or ``tool_search``."""
    kind = definition.get("type")
    if kind == "namespace":
        problems = []
        if chat:
            problems.append(StrictProblem("", f"{base}.type",
                                          "namespaces exist in the Responses API only"))
        if not definition.get("name") or not definition.get("tools"):
            problems.append(StrictProblem(definition.get("name") or "", base,
                                          "a namespace needs a name and tools"))
        for number, function in enumerate(definition.get("tools", ())):
            problems += _function_problems(function, f"{base}.tools[{number}]")
        return problems
    if kind == "tool_search":
        return ([StrictProblem("", f"{base}.type", "tool_search exists in the Responses API only")]
                if chat else [])
    if kind == "function":
        if chat:
            if not isinstance(definition.get("function"), Mapping):
                return [StrictProblem(str(definition.get("name") or ""), base,
                                      "a Chat Completions tool nests its definition under "
                                      "function")]
            return _function_problems(definition["function"], f"{base}.function")
        return _function_problems(definition, base)
    if "input_schema" in definition:
        return [StrictProblem(str(definition.get("name") or ""), base,
                              "an Anthropic definition (input_schema), not an OpenAI one")]
    return [StrictProblem(str(definition.get("name") or ""), f"{base}.type",
                          f"unknown tool type {kind!r}")]


def _function_problems(function: Mapping[str, Any], base: str) -> list[StrictProblem]:
    """One function's name, root and, when it is strict, every node of its parameters."""
    name = str(function.get("name") or "")
    problems: list[StrictProblem] = []

    def report(path: str, message: str, where: str = "") -> None:
        problems.append(StrictProblem(name, path, message, where))

    if not OPENAI_NAME.match(name):
        report(f"{base}.name", f"the name {name!r} does not match {OPENAI_NAME.pattern}")
    schema = function.get("parameters") or {}
    root = f"{base}.parameters"
    if not isinstance(schema, Mapping):
        report(root, "parameters must be a JSON Schema object")
        return problems
    if schema.get("type") != "object":
        report(root, "the root must be an object")
    for key in OPENAI_ROOT_KEYWORDS:
        if key in schema:
            report(root, f"{key!r} at the root; the root must be a plain object")
    if function.get("strict") is False:
        return problems
    if function.get("strict") is not True:
        report(f"{base}.strict", "strict is not true")
    properties = depth_max = enum_values = 0
    for where, path, depth, node in _schema_nodes(schema, "", root, 0):
        depth_max = max(depth_max, depth)
        for key in node:
            if key in OPENAI_UNSUPPORTED and not (where == "" and key in OPENAI_ROOT_KEYWORDS):
                report(path, f"{key!r} is not supported in strict mode", where)
        if "format" in node and node["format"] not in OPENAI_FORMATS:
            report(path, f"format {node['format']!r} is not supported", where)
        types = node.get("type")
        types = types if isinstance(types, list) else [types]
        if "object" in types:
            keys = list(node.get("properties") or {})
            properties += len(keys)
            if "additionalProperties" not in node:
                report(path, "additionalProperties must be false (it is missing)", where)
            elif node["additionalProperties"] is not False:
                report(path, "additionalProperties must be false", where)
            required = node.get("required") or ()
            missing = [key for key in keys if key not in required]
            if missing:
                report(path, "every property must be required; not in required: "
                       + ", ".join(missing), where)
        if "enum" in node:
            enum_values += len(node["enum"])
            if "null" in types and None not in node["enum"]:
                report(path, "a nullable enum must list null", where)
    if properties > OPENAI_MAX_PROPERTIES:
        report(root, f"{properties} properties; at most {OPENAI_MAX_PROPERTIES}")
    if depth_max > OPENAI_MAX_DEPTH:
        report(root, f"nested {depth_max} levels; at most {OPENAI_MAX_DEPTH}")
    if enum_values > OPENAI_MAX_ENUM_VALUES:
        report(root, f"{enum_values} enum values; at most {OPENAI_MAX_ENUM_VALUES}")
    return problems


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _json_key(path: str, key: str) -> str:
    return f"{path}.{key}" if _IDENTIFIER.match(key) else f"{path}[{json.dumps(key)}]"


def _schema_nodes(node: Mapping[str, Any], where: str, path: str,
                  depth: int) -> Iterable[tuple[str, str, int, Mapping[str, Any]]]:
    """Every node of a hand-written schema: its dotted location, JSON path and nesting
    depth (a property or an array's items is a level; an ``anyOf`` branch or a ``$defs``
    entry is not), through properties, items, ``anyOf``/``oneOf``/``allOf`` branches and
    ``$defs``/``definitions``.  A ``$ref`` is not followed."""
    yield where, path, depth, node
    properties = node.get("properties")
    if isinstance(properties, Mapping):
        for name, child in properties.items():
            if isinstance(child, Mapping):
                yield from _schema_nodes(child, f"{where}.{name}",
                                         _json_key(f"{path}.properties", name), depth + 1)
    items = node.get("items")
    if isinstance(items, Mapping):
        yield from _schema_nodes(items, f"{where}[]", f"{path}.items", depth + 1)
    for key in ("anyOf", "oneOf", "allOf"):
        branches = node.get(key)
        if isinstance(branches, list):
            for number, branch in enumerate(branches):
                if isinstance(branch, Mapping):
                    yield from _schema_nodes(branch, f"{where}.{key}[{number}]",
                                             f"{path}.{key}[{number}]", depth)
    for key in ("$defs", "definitions"):
        defined = node.get(key)
        if isinstance(defined, Mapping):
            for name, child in defined.items():
                if isinstance(child, Mapping):
                    yield from _schema_nodes(child, f"{where}.{key}.{name}",
                                             _json_key(_json_key(path, key), name), depth)


def _nodes(schema: Mapping[str, Any], where: str = "") -> Iterable[tuple[str, Mapping[str, Any]]]:
    """Every schema node with its dotted location."""
    yield where, schema
    for name, child in (schema.get("properties") or {}).items():
        if isinstance(child, Mapping):
            yield from _nodes(child, f"{where}.{name}")
    items = schema.get("items")
    if isinstance(items, Mapping):
        yield from _nodes(items, f"{where}[]")


def openai_allowed_tools(names: Iterable[str], *, chat: bool = False,
                         mode: str = "auto") -> dict[str, Any]:
    """An OpenAI ``tool_choice`` that lets the model call only ``names``, without changing
    the ``tools`` list (so its cache holds): the Responses shape, or with ``chat`` the Chat
    Completions one."""
    names = list(dict.fromkeys(names))
    if chat:
        return {"type": "allowed_tools", "allowed_tools": {
            "mode": mode, "tools": [{"type": "function", "function": {"name": name}}
                                    for name in names]}}
    return {"type": "allowed_tools", "mode": mode,
            "tools": [{"type": "function", "name": name} for name in names]}


# -- one entry point per direction -------------------------------------------------------------

PROVIDERS = ("anthropic", "openai-responses", "openai-chat")


def definitions_for(provider: str, tools: Iterable[Tool], **options: Any) -> list[dict[str, Any]]:
    if provider == "anthropic":
        return to_anthropic(tools, **options)
    if provider == "openai-responses":
        return to_openai_responses(tools, **options)
    if provider == "openai-chat":
        if options.get("defer") or options.get("namespaces"):
            raise ValueError("Chat Completions has no tool search or namespaces; pick groups "
                             "instead (definitions(groups=[...]))")
        return to_openai_chat(tools)
    raise ValueError(f"unknown provider {provider!r}; one of {', '.join(PROVIDERS)}")


def results_for(provider: str, results: Sequence[tuple[str, Result]], *,
                limit: int | None = None, images: str | None = None,
                detail: str | None = None) -> Any:
    """What answers one assistant turn: a user message (Anthropic), a list of items
    (Responses) or a list of messages (Chat Completions).  ``images`` and ``detail`` are
    OpenAI's (:func:`openai_responses_items`); Chat Completions always puts images in a user
    message, and Anthropic takes neither."""
    if provider == "anthropic":
        if images is not None or detail is not None:
            raise ValueError("images and detail are OpenAI options")
        return anthropic_user_message(results, limit=limit)
    if provider == "openai-responses":
        return openai_responses_items(results, limit=limit, images=images or "output",
                                      detail=detail or "auto")
    if provider == "openai-chat":
        if images not in (None, "message"):
            raise ValueError("Chat Completions puts images in a user message only")
        return openai_chat_messages(results, limit=limit, detail=detail or "auto")
    raise ValueError(f"unknown provider {provider!r}; one of {', '.join(PROVIDERS)}")

