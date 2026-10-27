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
from ..errors import ArgumentError

#: The group whose tools are always loaded, never deferred.
CORE = "core"

#: The roles, additive toolsets an application loads in any combination (``Toolbox(roles=...)``,
#: ``Toolbox.session(roles=...)``): ``review`` changes nothing in a document but its comments
#: (reading, finding, rendering, checking, comparing; adding and replying to comments);
#: ``resolve`` closes, reopens and deletes comments; ``accept`` accepts and rejects tracked
#: changes; ``edit`` makes every other change.  All four, the default, is the whole toolbox.
REVIEW = "review"
RESOLVE = "resolve"
ACCEPT = "accept"
EDIT = "edit"
ROLES = (REVIEW, RESOLVE, ACCEPT, EDIT)
#: A tool or action in every role's toolset: the session's documents (open, save, close),
#: inputs, ``batch`` (whose ops are each held to the roles), a comment's text edited (whose
#: author no one knows: the library does not stand between a model and it).
ANY_ROLE = "any"


def role_set(roles: str | Iterable[str] | None) -> tuple[str, ...]:
    """``roles`` -- a role, roles, or ``None`` for all -- in :data:`ROLES` order; an
    unknown or empty one is a ``ValueError``."""
    if roles is None:
        return ROLES
    wanted = {roles} if isinstance(roles, str) else set(roles)
    unknown = wanted - set(ROLES)
    if unknown:
        raise ArgumentError(f"unknown role(s) {sorted(map(str, unknown))}; the roles are "
                            f"{', '.join(ROLES)}")
    if not wanted:
        raise ArgumentError(f"no roles: give one or more of {', '.join(ROLES)}, or None for all")
    return tuple(role for role in ROLES if role in wanted)


# -- parameter builders ------------------------------------------------------------------------


@dataclass(frozen=True)
class Param:
    """One property: its schema, and whether the model may leave it out."""

    schema: Mapping[str, Any]
    optional: bool = False


def _described(kind: str, description: str | None) -> dict[str, Any]:
    """``{"type": kind}`` with the description, if there is one (nested properties whose
    name says it all go without)."""
    schema: dict[str, Any] = {"type": kind}
    if description:
        schema["description"] = description
    return schema


def _param(schema: dict[str, Any], optional: bool, **bounds: Any) -> Param:
    for key, value in bounds.items():
        if value is not None:
            schema[key] = value
    return Param(schema, optional)


def string(description: str | None = None, *, enum: Sequence[str] | None = None,
           format: str | None = None, min_length: int | None = None,
           max_length: int | None = None, or_integer: int | None = None,
           aliases: Mapping[str, str] | None = None, optional: bool = False) -> Param:
    """A string; with ``or_integer``, the dispatcher also takes a whole number of at least
    that in its place (``orInteger``, :data:`~.schema.BOUND_KEYWORDS`): the provider is sent
    a plain string.  ``aliases`` are values read as another (``{"shrink": "normal"}``,
    ``valueAliases``), never sent: the result's warnings say what was read."""
    schema = _described("string", description)
    if enum is not None:
        schema["enum"] = list(enum)
    if aliases:
        schema["valueAliases"] = dict(aliases)
    if format is not None:
        schema["format"] = format
    return _param(schema, optional, minLength=min_length, maxLength=max_length,
                  orInteger=or_integer)


def integer(description: str | None = None, *, minimum: int | None = None,
            maximum: int | None = None, optional: bool = False) -> Param:
    return _param(_described("integer", description), optional,
                  minimum=minimum, maximum=maximum)


def number(description: str | None = None, *, minimum: float | None = None,
           maximum: float | None = None, optional: bool = False) -> Param:
    return _param(_described("number", description), optional,
                  minimum=minimum, maximum=maximum)


def boolean(description: str | None = None, *, optional: bool = False) -> Param:
    return Param(_described("boolean", description), optional)


def array(items: Param, description: str | None = None, *, min_items: int | None = None,
          max_items: int | None = None, optional: bool = False) -> Param:
    item_schema = dict(items.schema)
    schema = _described("array", description)
    schema["items"] = item_schema
    if min_items is not None:
        schema["minItems"] = min_items
    return _param(schema, optional, maxItems=max_items)


def free_object(description: str, *, optional: bool = False) -> Param:
    """An object with any keys, for a tool that is never sent strict (``Tool(strict=False)``):
    the dispatcher checks its content against another schema (``batch``'s arguments)."""
    return Param({"type": "object", "description": description, "additionalProperties": True},
                 optional)


def obj(properties: Mapping[str, Param], description: str | None = None, *,
        optional: bool = False) -> Param:
    return Param(build_schema(properties, description=description), optional)


def build_schema(properties: Mapping[str, Param], *, description: str | None = None) -> dict[str, Any]:
    """An object schema from named parameters: every key named, the required ones listed.

    The required properties come first, then the optional ones, each in the order given:
    strict decoding writes keys in schema order, so an optional property listed before a
    required one is lost once the model writes the required one
    (:func:`~.schema.required_first`)."""
    schema: dict[str, Any] = {"type": "object"}
    if description:
        schema["description"] = description
    names = ([name for name, param in properties.items() if not param.optional]
             + [name for name, param in properties.items() if param.optional])
    schema["properties"] = {name: copy.deepcopy(dict(properties[name].schema))
                            for name in names}
    required = [name for name, param in properties.items() if not param.optional]
    if required:  # an empty list says nothing and is sent with every request
        schema["required"] = required
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
    #: For a tool that names no document (``new_document``): the argument whose value is the
    #: document kind whose handler runs.
    route: str | None = None
    #: False: never sent in strict mode (a schema with a free-form object, :func:`free_object`).
    strict: bool = True
    #: Whether the tool may run inside ``batch``: not batch itself, nor saving, rendering or
    #: the session's housekeeping.
    batchable: bool = True
    #: A tool whose arguments are other calls (``batch``): the dispatcher runs it itself.
    composite: bool = False
    #: Argument paths (``target``, ``items[].target``, ``targets[]``) whose string values may
    #: be a ref, ``$name`` or ``$name/rest``: an address a creating call named.
    refs: Sequence[str] = ()
    #: Argument paths, as ``refs`` writes them, whose string values may name a dataset in
    #: the session, ``d:<id>`` (and what the format reads after it): the dispatcher checks
    #: the id exists before the handler runs, which reads it with ``call.dataset(value)``.
    datasets: Sequence[str] = ()
    #: Argument paths whose string values may name rows to take: a dataset (``d:<id>``,
    #: checked as for ``datasets``) or an address in another open document that its format
    #: resolves (``t:sales``, ``d2/t:sales``; :mod:`.sources`).  The dispatcher qualifies
    #: such an address with its document and holds that document's lock for the call; the
    #: handler reads either with ``call.source(value)``.
    sources: Sequence[str] = ()
    #: Argument paths whose text may hold references to one value of a source,
    #: ``{{d:by_topic/door/mean}}`` (:mod:`.textfacts`): the dispatcher qualifies a ``t:``
    #: one with its document and holds that document's lock, and before the handler runs
    #: writes each value in their place (``call.facts`` says where).  A path may end in an
    #: array of arrays of strings (``data[]``: rows of cells).
    facts: Sequence[str] = ()
    #: For a changing tool with a reading mode (``edit_chart`` with ``action: "read"``, a
    #: measure-only call): a function of the arguments, true when the call changes nothing.
    #: Such a call runs as a reading one: no undo step, no version, no checks, no retry key.
    reads: Callable[[Mapping[str, Any]], bool] | None = None
    #: Argument names a model naturally writes for one of the tool's own, never sent:
    #: ``{alias: name}``, or ``{alias: function(arguments) -> name or None}`` where the
    #: alias means the name only in some calls (a page is ``edit_chart``'s ``target``
    #: for ``add``) -- such a function may also take one of the tool's own names whose value
    #: can only mean another (a list of names given as a count).  The dispatcher renames the
    #: argument before validating and says so in the result's warnings.  Only where the
    #: meaning is the same.  An alias inside an object argument is written as a path,
    #: ``refs``-style: ``{"items[].top": "space_before"}`` renames ``top`` in each item
    #: (a function gets that object).
    aliases: Mapping[str, str | Callable[[Mapping[str, Any]], str | None]] = field(
        default_factory=dict)
    #: The role a call needs (:data:`ROLES`), or :data:`ANY_ROLE`.  ``None``: ``edit`` for a
    #: changing tool (``mutates``), ``review`` for a reading one.
    role: str | None = None
    #: For a tool with an ``action`` argument: the role each action needs where it is not
    #: the tool's (``{"read": "review"}`` on ``edit_chart``).  A role set's definitions offer
    #: only the actions of its roles.
    action_roles: Mapping[str, str] = field(default_factory=dict)
    #: For a tool with an ``action`` argument: the optional parameters that apply only to
    #: some actions (``{"ids": ("accept", "reject")}`` on Word's tracked changes).  A role
    #: set offered none of a parameter's actions is not sent the parameter either.
    action_fields: Mapping[str, Sequence[str]] = field(default_factory=dict)
    #: For a call whose action does not settle its role: a function of the arguments giving
    #: the role it needs instead, or ``None`` for its action's (a comment reply that also
    #: resolves the thread needs ``resolve``).
    call_role: Callable[[Mapping[str, Any]], str | None] | None = None
    #: A reviewer's main tool (comments, comparing like pages): in the core of a role set
    #: without ``edit``, which has few tools of its own; in its group otherwise, as without
    #: roles.
    review_core: bool = False

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

    def changes(self, arguments: Mapping[str, Any]) -> bool:
        """Whether a call with ``arguments`` changes its document: a changing tool, unless
        ``reads`` says this call only reads."""
        return self.mutates and not (self.reads is not None and bool(self.reads(arguments)))

    @property
    def base_role(self) -> str:
        """The role of a call whose action names none of its own."""
        return self.role or (EDIT if self.mutates else REVIEW)

    @property
    def actions(self) -> list[str]:
        """The values of the ``action`` argument, if the tool has one with an enum."""
        action = self.schema.get("properties", {}).get("action") or {}
        return list(action.get("enum") or ())

    @property
    def roles(self) -> frozenset[str]:
        """The roles whose toolsets hold the tool: every role one of its actions needs
        (:data:`ANY_ROLE`: all of them)."""
        needed = {self.action_roles.get(action, self.base_role) for action in self.actions} \
            or {self.base_role}
        return frozenset(ROLES) if ANY_ROLE in needed else frozenset(needed)

    def _action_role(self, action: str) -> str:
        return self.action_roles.get(action, self.base_role)

    def role_of(self, arguments: Mapping[str, Any]) -> str:
        """The role a call with ``arguments`` needs: :data:`ANY_ROLE`, or one of
        :data:`ROLES`."""
        if self.call_role is not None:
            needed = self.call_role(arguments)
            if needed is not None:
                return needed
        action = arguments.get("action")
        if isinstance(action, str) and action in self.action_roles:
            return self.action_roles[action]
        return self.base_role

    def actions_for(self, roles: Iterable[str]) -> list[str]:
        """The actions a role set may call (all of them for a tool without ``action``:
        an empty list)."""
        roles = set(roles)
        return [action for action in self.actions
                if self._action_role(action) in roles or self._action_role(action) == ANY_ROLE]

    def for_roles(self, roles: Iterable[str]) -> "Tool | None":
        """The tool as a role set is sent it: itself, ``None`` when it is in none of the
        roles' toolsets, or a copy whose ``action`` enum lists only the roles' actions (and
        without a parameter of :attr:`action_fields` none of those actions takes) --
        in the core, for a :attr:`review_core` tool and a role set without ``edit`` that has
        one of its own actions (not only one every role has)."""
        roles = frozenset(roles)
        if not self.roles & roles:
            return None
        actions = self.actions
        allowed = self.actions_for(roles)
        own = not actions or any(self._action_role(action) in roles for action in actions)
        promoted = self.review_core and EDIT not in roles and self.group != CORE and own
        if len(allowed) == len(actions) and not promoted:
            return self
        sent = copy.copy(self)
        if promoted:
            sent.group = CORE
        if len(allowed) != len(actions):
            sent.schema = copy.deepcopy(self.schema)
            sent.schema["properties"]["action"]["enum"] = allowed
            for name, applies in self.action_fields.items():
                if not set(applies) & set(allowed):
                    del sent.schema["properties"][name]
        return sent

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
        return (self.name, self.description, self.schema, self.group, self.mutates,
                self.route, self.strict, self.batchable, self.composite, tuple(self.refs),
            self.reads) == (
            other.name, other.description, other.schema, other.group, other.mutates,
            other.route, other.strict, other.batchable, other.composite, tuple(other.refs),
            other.reads) and tuple(self.datasets) == tuple(other.datasets) \
            and (self.role, dict(self.action_roles), self.call_role, self.review_core) == (
                other.role, dict(other.action_roles), other.call_role, other.review_core) \
            and {k: tuple(v) for k, v in self.action_fields.items()} \
            == {k: tuple(v) for k, v in other.action_fields.items()} \
            and tuple(self.sources) == tuple(other.sources) \
            and tuple(self.facts) == tuple(other.facts) \
            and dict(self.aliases) == dict(other.aliases)

    def merged(self, other: "Tool") -> "Tool":
        """One definition serving both tools' kinds: a shared tool two libraries implement."""
        if not self.same_definition(other):
            raise ValueError(f"two different tools are named {self.name!r}")
        # The same handler listed twice (a shared session tool both libraries include) is
        # one handler, not a clash.
        clash = {kind for kind in set(self.handlers) & set(other.handlers)
                 if self.handlers[kind] is not other.handlers[kind]}
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
    problems += subset_problems(tool.schema, allow_bounds=True, allow_free=not tool.strict)
    for path in tool.refs:
        if not _path_exists(tool.schema, path):
            problems.append(f"refs names {path!r}, which is not a string parameter")
    for path in tool.datasets:
        if not _path_exists(tool.schema, path):
            problems.append(f"datasets names {path!r}, which is not a string parameter")
    for path in tool.sources:
        if not _path_exists(tool.schema, path):
            problems.append(f"sources names {path!r}, which is not a string parameter")
    for path in tool.facts:
        if not _path_exists(tool.schema, path, nested=True):
            problems.append(f"facts names {path!r}, which is not a string parameter")
    properties = tool.schema.get("properties", {})
    required = set(tool.schema.get("required", ()))
    for group in tool.exactly_one:
        for name in group:
            if name not in properties:
                problems.append(f"exactly_one names {name!r}, which is not a parameter")
            elif name in required:
                problems.append(f"exactly_one names {name!r}, which is required")
    for alias, target in tool.aliases.items():
        *where, key = alias.split(".")
        container = _object_at(tool.schema, where)
        if container is None:
            problems.append(f"aliases names {alias!r}, which is in no object parameter")
            continue
        if callable(target):
            continue
        names = container.get("properties", {})
        if key in names:
            problems.append(f"aliases names {alias!r}, which is a parameter")
        if target not in names:
            problems.append(f"aliases reads {alias!r} as {target!r}, which is not a parameter")
    if tool.role is not None and tool.role not in (*ROLES, ANY_ROLE):
        problems.append(f"role {tool.role!r} is not one of {', '.join((*ROLES, ANY_ROLE))}")
    if tool.action_roles:
        actions = (properties.get("action") or {}).get("enum") or ()
        for action, role in tool.action_roles.items():
            if action not in actions:
                problems.append(f"action_roles names {action!r}, which is not an action")
            if role not in (*ROLES, ANY_ROLE):
                problems.append(f"action_roles gives {action!r} the role {role!r}, which is "
                                f"not one of {', '.join((*ROLES, ANY_ROLE))}")
    if tool.action_fields:
        actions = (properties.get("action") or {}).get("enum") or ()
        required = set(tool.schema.get("required") or ())
        for name, applies in tool.action_fields.items():
            if name not in properties or name in required:
                problems.append(f"action_fields names {name!r}, which is not an optional "
                                "parameter")
            if isinstance(applies, str) or not applies:
                problems.append(f"action_fields gives {name!r} {applies!r}: a list of actions")
                continue
            for action in applies:
                if action not in actions:
                    problems.append(f"action_fields gives {name!r} the action {action!r}, "
                                    "which is not an action")
    if tool.route is not None and tool.route not in properties:
        problems.append(f"route names {tool.route!r}, which is not a parameter")
    if not callable(tool.documents):
        for name in tool.documents:
            if name in properties and properties[name].get("type") != "string":
                problems.append(f"document argument {name!r} is not a string")
    return problems


def _object_at(schema: Mapping[str, Any], parts: Sequence[str]) -> Mapping[str, Any] | None:
    """The object schema at ``parts`` (``["items[]", "frame"]``; ``[]``: the root), or
    ``None``."""
    node: Any = schema
    for part in parts:
        array = part.endswith("[]")
        name = part[:-2] if array else part
        node = (node.get("properties") or {}).get(name) if isinstance(node, Mapping) else None
        if array:
            node = node.get("items") if isinstance(node, Mapping) and \
                node.get("type") == "array" else None
    return node if isinstance(node, Mapping) and node.get("type") == "object" else None


def _path_exists(schema: Mapping[str, Any], path: str, *, nested: bool = False) -> bool:
    """Whether ``path`` (``items[].from.shape``) names a string, or an array of strings;
    ``nested``: or an array of arrays of strings."""
    node: Any = schema
    for part in path.split("."):
        array = part.endswith("[]")
        name = part[:-2] if array else part
        node = (node.get("properties") or {}).get(name) if isinstance(node, Mapping) else None
        if not isinstance(node, Mapping):
            return False
        if array:
            if node.get("type") != "array":
                return False
            node = node.get("items")
    while nested and isinstance(node, Mapping) and node.get("type") == "array":
        node = node.get("items")
    return isinstance(node, Mapping) and node.get("type") == "string"


def tool(name: str, description: str, params: Mapping[str, Param] | None = None, *,
         group: str = CORE, mutates: bool = False, kind: str | None = None,
         documents: Sequence[str] | Callable[[Mapping[str, Any]], list[str]] = ("doc",),
         exactly_one: Sequence[Sequence[str]] = (),
         priority: int | None = None, strict: bool = True, batchable: bool = True,
         refs: Sequence[str] = (), datasets: Sequence[str] = (), sources: Sequence[str] = (),
         facts: Sequence[str] = (), reads: Callable[[Mapping[str, Any]], bool] | None = None,
         aliases: Mapping[str, Any] | None = None, role: str | None = None,
         action_roles: Mapping[str, str] | None = None,
         call_role: Callable[[Mapping[str, Any]], str | None] | None = None,
         review_core: bool = False,
         action_fields: Mapping[str, Sequence[str]] | None = None) -> Callable[[Handler], Tool]:
    """Make a :class:`Tool` of a handler.  ``kind`` restricts it to one document kind;
    ``reads`` marks the calls of a changing tool that only read (:attr:`Tool.reads`);
    ``aliases`` the argument names read as its own (:attr:`Tool.aliases`);
    ``refs``, ``datasets`` and ``sources`` declare the arguments that may hold a ref, a
    dataset, or a source (a dataset or another document's rows); ``facts`` the text that
    may hold references to one value (:attr:`Tool.facts`); ``role``, ``action_roles`` and
    ``call_role`` the roles its calls need (:attr:`Tool.role`; by default ``edit`` for a
    changing tool, ``review`` for a reading one); ``review_core`` puts it in a reviewer's
    core (:attr:`Tool.review_core`); ``action_fields`` the parameters only some actions
    take, left out for a role set offered none of them (:attr:`Tool.action_fields`)."""

    def make(handler: Handler) -> Tool:
        return Tool(name=name, description=description, schema=build_schema(params or {}),
                    handlers={kind: handler}, group=group, mutates=mutates,
                    documents=documents, exactly_one=exactly_one, priority=priority,
                    strict=strict, batchable=batchable, refs=tuple(refs),
                    datasets=tuple(datasets), sources=tuple(sources), facts=tuple(facts),
                    reads=reads, aliases=dict(aliases or {}), role=role,
                    action_roles=dict(action_roles or {}), call_role=call_role,
                    review_core=review_core,
                    action_fields={k: tuple(v) for k, v in (action_fields or {}).items()})

    return make


def names_as(alias: str, name: str) -> Callable[[Mapping[str, Any]], str | None]:
    """A :attr:`Tool.aliases` function for a parameter ``alias`` of the tool's own that
    takes no names (a count, numbers): given a list of names -- strings that are not
    numbers -- it can only mean ``name`` (``columns: ["region", "total"]`` for
    ``from_columns``), unless ``name`` is given too."""

    def meant(arguments: Mapping[str, Any]) -> str | None:
        value = arguments.get(alias)
        if arguments.get(name) is not None or not isinstance(value, list) or not value:
            return None
        if all(isinstance(item, str) and not _is_number(item) for item in value):
            return name
        return None

    return meant


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


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
