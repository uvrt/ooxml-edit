"""Tools, tool groups, the ``@tool`` decorator and the canonical-schema builder.

A tool is a function plus its definition: a name, a short description, and parameters built
with the helpers here (:func:`string`, :func:`integer`, :func:`number`, :func:`boolean`,
:func:`array`, :func:`obj`).  The helpers write the *validation* schema; the *canonical*
schema every provider adapter starts from is the same without the dispatcher-only bounds
(see :mod:`.schema`).  A definition that leaves the common strict subset fails when the
tool is made, not when a provider rejects it::

    @tool("toy_set_title", "Set the title of one page. Returns the page's new title.",
          {"doc": string("Document id, e.g. d1."),
           "page": integer("Page number, from 1.", minimum=1),
           "text": string("The new title text.")},
          group="toy_text", mutates=True)
    def set_title(call, doc, page, text):
        ...

The handler gets a :class:`~.dispatch.Call` first and the validated arguments as keywords
(an optional argument the model left out is not passed).  It returns a
:class:`~.results.Result`, or plain data for the result's ``data``, or ``None``.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from .schema import NAME, SubsetError, canonical, subset_problems, walk_properties

#: The group whose tools are always loaded, never deferred.
CORE = "core"


# -- parameter builders ------------------------------------------------------------------------


@dataclass(frozen=True)
class Param:
    """One property: its schema, and whether the model may leave it out."""

    schema: Mapping[str, Any]
    optional: bool = False


def _param(schema: dict[str, Any], optional: bool, **bounds: Any) -> Param:
    for key, value in bounds.items():
        if value is not None:
            schema[key] = value
    return Param(schema, optional)


def string(description: str, *, enum: Sequence[str] | None = None, format: str | None = None,
           min_length: int | None = None, max_length: int | None = None,
           optional: bool = False) -> Param:
    schema: dict[str, Any] = {"type": "string", "description": description}
    if enum is not None:
        schema["enum"] = list(enum)
    if format is not None:
        schema["format"] = format
    return _param(schema, optional, minLength=min_length, maxLength=max_length)


def integer(description: str, *, minimum: int | None = None, maximum: int | None = None,
            optional: bool = False) -> Param:
    return _param({"type": "integer", "description": description}, optional,
                  minimum=minimum, maximum=maximum)


def number(description: str, *, minimum: float | None = None, maximum: float | None = None,
           optional: bool = False) -> Param:
    return _param({"type": "number", "description": description}, optional,
                  minimum=minimum, maximum=maximum)


def boolean(description: str, *, optional: bool = False) -> Param:
    return Param({"type": "boolean", "description": description}, optional)


def array(items: Param, description: str, *, min_items: int | None = None,
          max_items: int | None = None, optional: bool = False) -> Param:
    item_schema = dict(items.schema)
    schema: dict[str, Any] = {"type": "array", "description": description, "items": item_schema}
    if min_items is not None:
        schema["minItems"] = min_items
    return _param(schema, optional, maxItems=max_items)


def obj(properties: Mapping[str, Param], description: str, *, optional: bool = False) -> Param:
    return Param(build_schema(properties, description=description), optional)


def build_schema(properties: Mapping[str, Param], *, description: str | None = None) -> dict[str, Any]:
    """An object schema from named parameters: every key named, the required ones listed."""
    schema: dict[str, Any] = {"type": "object"}
    if description is not None:
        schema["description"] = description
    schema["properties"] = {name: copy.deepcopy(dict(param.schema))
                            for name, param in properties.items()}
    schema["required"] = [name for name, param in properties.items() if not param.optional]
    schema["additionalProperties"] = False
    return schema


# -- tools -------------------------------------------------------------------------------------


Handler = Callable[..., Any]


@dataclass
class Tool:
    """A tool's definition and its handler(s), one per document kind it serves."""

    name: str
    description: str
    schema: dict[str, Any]
    handlers: dict[str | None, Handler] = field(default_factory=dict)
    group: str = CORE
    mutates: bool = False
    #: The arguments that name documents, in order; the first one present is the document
    #: the call edits (its undo step and version).  Or a function of the arguments.
    documents: Sequence[str] | Callable[[Mapping[str, Any]], list[str]] = ("doc",)
    #: Sets of optional arguments of which exactly one must be given.
    exactly_one: Sequence[Sequence[str]] = ()
    #: Order for strict mode on Claude: lower first.  ``None``: writing tools first.
    priority: int | None = None

    def __post_init__(self) -> None:
        problems = definition_problems(self)
        if problems:
            raise SubsetError([f"{self.name}: {problem}" for problem in problems])

    @property
    def canonical(self) -> dict[str, Any]:
        """The schema providers are sent: in the common strict subset, no bounds."""
        return canonical(self.schema)

    @property
    def core(self) -> bool:
        return self.group == CORE

    @property
    def kinds(self) -> list[str | None]:
        return list(self.handlers)

    def optional_parameters(self) -> list[str]:
        """Every optional property at any depth (Claude's strict limit counts these)."""
        return [name for name, _, required in walk_properties(self.canonical) if not required]

    def doc_ids(self, arguments: Mapping[str, Any]) -> list[str]:
        if callable(self.documents):
            return list(self.documents(arguments))
        return [arguments[name] for name in self.documents
                if isinstance(arguments.get(name), str)]

    def handler_for(self, kind: str | None) -> Handler | None:
        return self.handlers.get(kind) or self.handlers.get(None)

    def for_kind(self, kind: str) -> Callable[[Handler], Handler]:
        """Add the handler for another document kind to the same definition."""

        def register(handler: Handler) -> Handler:
            self.handlers[kind] = handler
            return handler

        return register

    def same_definition(self, other: "Tool") -> bool:
        return (self.name, self.description, self.schema, self.group, self.mutates) == (
            other.name, other.description, other.schema, other.group, other.mutates)

    def merged(self, other: "Tool") -> "Tool":
        """One definition serving both tools' kinds: a shared tool two libraries implement."""
        if not self.same_definition(other):
            raise ValueError(f"two different tools are named {self.name!r}")
        clash = set(self.handlers) & set(other.handlers)
        if clash:
            raise ValueError(f"{self.name!r} has two handlers for {sorted(map(str, clash))}")
        combined = copy.copy(self)
        combined.handlers = {**self.handlers, **other.handlers}
        return combined


def definition_problems(tool: Tool) -> list[str]:
    problems = []
    if not NAME.match(tool.name):
        problems.append(f"the name does not match {NAME.pattern}")
    if not tool.description.strip():
        problems.append("no description")
    problems += subset_problems(tool.schema, allow_bounds=True)
    properties = tool.schema.get("properties", {})
    required = set(tool.schema.get("required", ()))
    for group in tool.exactly_one:
        for name in group:
            if name not in properties:
                problems.append(f"exactly_one names {name!r}, which is not a parameter")
            elif name in required:
                problems.append(f"exactly_one names {name!r}, which is required")
    if not callable(tool.documents):
        for name in tool.documents:
            if name in properties and properties[name].get("type") != "string":
                problems.append(f"document argument {name!r} is not a string")
    return problems


def tool(name: str, description: str, params: Mapping[str, Param] | None = None, *,
         group: str = CORE, mutates: bool = False, kind: str | None = None,
         documents: Sequence[str] | Callable[[Mapping[str, Any]], list[str]] = ("doc",),
         exactly_one: Sequence[Sequence[str]] = (),
         priority: int | None = None) -> Callable[[Handler], Tool]:
    """Make a :class:`Tool` of a handler.  ``kind`` restricts it to one document kind."""

    def make(handler: Handler) -> Tool:
        return Tool(name=name, description=description, schema=build_schema(params or {}),
                    handlers={kind: handler}, group=group, mutates=mutates,
                    documents=documents, exactly_one=exactly_one, priority=priority)

    return make


@dataclass(frozen=True)
class ToolGroup:
    """A named group of tools: loaded together, and an OpenAI namespace."""

    name: str
    description: str

    def __post_init__(self) -> None:
        if not NAME.match(self.name):
            raise ValueError(f"group name {self.name!r} does not match {NAME.pattern}")


def merge_tools(tools: Iterable[Tool]) -> dict[str, Tool]:
    """Tools by name, in order; same-named tools with one definition merge their kinds."""
    merged: dict[str, Tool] = {}
    for item in tools:
        merged[item.name] = merged[item.name].merged(item) if item.name in merged else item
    return merged
