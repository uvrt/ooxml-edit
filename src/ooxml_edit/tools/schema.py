"""The common strict subset of JSON Schema, and a validator for calls against it.

Every tool's *canonical* schema is written in a subset that both providers accept in strict
mode, so one definition serves Claude and the OpenAI APIs alike:

* a root ``type: object``; ``properties``, ``required`` and ``additionalProperties: false``
  on every object, every key named;
* the types ``string``, ``integer``, ``number``, ``boolean``, ``array`` (with ``items``) and
  ``object``, one type per property -- no unions;
* ``enum`` of strings, at most :data:`MAX_ENUM` values each and :data:`MAX_ENUM_TOTAL` in a
  tool; ``format`` only ``date`` and ``date-time``; ``minItems`` only 0 or 1;
* a ``description`` on every top-level property, and property names that match
  :data:`NAME`.  Nested properties and array items may leave it out where the name, type
  and enum say it all (``bold``, ``x``): every description is sent with every request, so
  one that repeats the name is cost without information;
* nesting at most :data:`MAX_DEPTH` levels below the root, no ``$ref``, no recursion.

One exception: a tool that is never sent strict (``Tool(strict=False)``, the generic
``batch`` tool) may hold a *free-form* object, ``{"type": "object", "additionalProperties":
true}`` with no properties, whose content the dispatcher checks against another schema.

What the subset cannot say -- numeric bounds, string and array lengths, "exactly one of" --
the dispatcher enforces.  A tool keeps those in its *validation* schema, which is the
canonical schema plus the bound keywords in :data:`BOUND_KEYWORDS`; :func:`canonical` strips
them, and :func:`validate_call` checks them.  No jsonschema dependency: the subset is small
enough to check by hand, and the messages are written for a model to act on.
"""

from __future__ import annotations

import copy
import datetime as _dt
import re
from typing import Any, Iterable, Mapping

#: Tool and property names: lower snake case, at most 64 characters (the tightest provider
#: rule is OpenAI Chat Completions' 64; Anthropic allows 128).
NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

TYPES = ("string", "integer", "number", "boolean", "array", "object")
FORMATS = ("date", "date-time")
MAX_ENUM = 50
MAX_ENUM_TOTAL = 1000
#: How deep an object may sit below the root (an array's items count as a level).  Six
#: lets a plural tool hold a text spec: ``items[] -> paragraphs[] -> runs[]``, the runs at
#: depth 6.  OpenAI strict mode allows 10 levels; Anthropic documents no depth limit, only
#: an overall complexity limit (a 400, "Schema is too complex for compilation"), which the
#: online test checks with every definition sent at once.
MAX_DEPTH = 6

#: The keywords a canonical schema node may carry.
SUBSET_KEYWORDS = frozenset({
    "type", "description", "properties", "required", "additionalProperties", "items",
    "enum", "format", "minItems",
})

#: Keywords a validation schema may add for the dispatcher; never sent to a provider.
BOUND_KEYWORDS = frozenset({"minimum", "maximum", "minLength", "maxLength", "maxItems"})

#: Words that make a property name a file-system path.  The tool layer has no paths: inputs
#: are blob handles, outputs go to the application.
PATH_WORDS = frozenset({"path", "paths", "file", "files", "filename", "filepath", "dir",
                        "dirs", "directory", "folder", "filesystem"})


class SubsetError(ValueError):
    """A canonical schema that leaves the common strict subset."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


# -- the subset checker ------------------------------------------------------------------------


def subset_problems(schema: Mapping[str, Any], *, allow_bounds: bool = False,
                    allow_free: bool = False) -> list[str]:
    """Every way ``schema`` leaves the common strict subset; empty when it is inside.

    With ``allow_bounds`` the dispatcher's bound keywords are accepted too (a validation
    schema); without, the schema must be exactly what a provider is sent.  With
    ``allow_free`` a free-form object (:func:`is_free`) is accepted: only for a tool that is
    never sent strict.
    """
    problems: list[str] = []
    if not isinstance(schema, Mapping):
        return ["the schema is not an object"]
    if schema.get("type") != "object":
        problems.append("the root must be type object")
    enum_total = [0]
    _check_node(schema, "$", 0, problems, enum_total, allow_bounds, is_root=True,
                allow_free=allow_free)
    if enum_total[0] > MAX_ENUM_TOTAL:
        problems.append(f"{enum_total[0]} enum values in total; at most {MAX_ENUM_TOTAL}")
    return problems


def check_subset(schema: Mapping[str, Any], *, allow_bounds: bool = False,
                 allow_free: bool = False) -> None:
    """Raise :class:`SubsetError` unless ``schema`` is inside the common strict subset."""
    problems = subset_problems(schema, allow_bounds=allow_bounds, allow_free=allow_free)
    if problems:
        raise SubsetError(problems)


def is_free(node: Mapping[str, Any]) -> bool:
    """A free-form object: any keys, checked by the dispatcher against another schema."""
    return node.get("type") == "object" and node.get("additionalProperties") is True


def _check_node(node: Any, where: str, depth: int, problems: list[str], enum_total: list[int],
                allow_bounds: bool, *, is_root: bool = False, allow_free: bool = False) -> None:
    if not isinstance(node, Mapping):
        problems.append(f"{where}: not a schema object")
        return
    if allow_free and not is_root and is_free(node):
        if set(node) - {"type", "description", "additionalProperties"}:
            problems.append(f"{where}: a free-form object takes no other keywords")
        if not str(node.get("description") or "").strip():
            problems.append(f"{where}: no description")
        return
    allowed = SUBSET_KEYWORDS | (BOUND_KEYWORDS if allow_bounds else frozenset())
    for key in node:
        if key not in allowed:
            problems.append(f"{where}: keyword {key!r} is outside the subset")
    kind = node.get("type")
    if isinstance(kind, list):
        problems.append(f"{where}: a type union {kind}; use two optional properties instead")
        return
    if kind not in TYPES:
        problems.append(f"{where}: type {kind!r} is not one of {', '.join(TYPES)}")
        return
    if depth == 1 and not str(node.get("description") or "").strip():
        problems.append(f"{where}: no description")
    if "description" in node and not isinstance(node["description"], str):
        problems.append(f"{where}: description must be a string")
    if "enum" in node:
        values = node["enum"]
        if kind != "string" or not isinstance(values, list) or not values:
            problems.append(f"{where}: enum must be a non-empty list on a string")
        else:
            if not all(isinstance(value, str) for value in values):
                problems.append(f"{where}: enum values must be strings")
            if len(values) > MAX_ENUM:
                problems.append(f"{where}: {len(values)} enum values; at most {MAX_ENUM}")
            if len(set(values)) != len(values):
                problems.append(f"{where}: repeated enum values")
            enum_total[0] += len(values)
    if "format" in node and (kind != "string" or node["format"] not in FORMATS):
        problems.append(f"{where}: format {node['format']!r}; only {' and '.join(FORMATS)}")
    if "minItems" in node and (kind != "array" or node["minItems"] not in (0, 1)):
        problems.append(f"{where}: minItems may only be 0 or 1, on an array")
    for key in ("minimum", "maximum"):
        if key in node and kind not in ("integer", "number"):
            problems.append(f"{where}: {key} on a {kind}")
    for key in ("minLength", "maxLength"):
        if key in node and kind != "string":
            problems.append(f"{where}: {key} on a {kind}")
    if "maxItems" in node and kind != "array":
        problems.append(f"{where}: maxItems on a {kind}")

    if kind == "object":
        if depth > MAX_DEPTH:
            problems.append(f"{where}: nested deeper than {MAX_DEPTH} levels")
        if node.get("additionalProperties") is not False:
            problems.append(f"{where}: additionalProperties must be false")
        properties = node.get("properties")
        if not isinstance(properties, Mapping):
            problems.append(f"{where}: an object needs properties")
            properties = {}
        required = node.get("required", [])
        if not isinstance(required, list) or not all(isinstance(r, str) for r in required):
            problems.append(f"{where}: required must be a list of names")
            required = []
        for name in required:
            if name not in properties:
                problems.append(f"{where}: required names {name!r}, which is not a property")
        if len(set(required)) != len(required):
            problems.append(f"{where}: repeated names in required")
        for name, child in properties.items():
            if not NAME.match(name):
                problems.append(f"{where}.{name}: the name does not match {NAME.pattern}")
            if is_path_name(name):
                problems.append(f"{where}.{name}: a path parameter; take a blob handle instead")
            _check_node(child, f"{where}.{name}", depth + 1, problems, enum_total, allow_bounds,
                        allow_free=allow_free)
    elif "properties" in node or "required" in node or "additionalProperties" in node:
        problems.append(f"{where}: object keywords on a {kind}")
    if kind == "array":
        if "items" not in node:
            problems.append(f"{where}: an array needs items")
        else:
            # Items take their meaning from the array's own description.
            items = node["items"]
            _check_node(items, f"{where}[]", depth + 1, problems, enum_total, allow_bounds,
                        allow_free=allow_free)
    elif "items" in node:
        problems.append(f"{where}: items on a {kind}")


def is_path_name(name: str) -> bool:
    """Whether a property name reads as a file-system path (``path``, ``out_file``, ``dir``)."""
    return any(word in PATH_WORDS for word in name.lower().split("_"))


def canonical(schema: Mapping[str, Any]) -> dict[str, Any]:
    """The schema a provider is sent: ``schema`` without the dispatcher's bound keywords."""

    def strip(node: Any) -> Any:
        if isinstance(node, Mapping):
            return {key: strip(value) for key, value in node.items()
                    if key not in BOUND_KEYWORDS}
        if isinstance(node, list):
            return [strip(value) for value in node]
        return node

    return strip(copy.deepcopy(dict(schema)))


def walk_properties(schema: Mapping[str, Any]) -> Iterable[tuple[str, Mapping[str, Any], bool]]:
    """Every property at any depth: ``(dotted name, schema, required)``."""

    def walk(node: Mapping[str, Any], prefix: str) -> Iterable[tuple[str, Mapping[str, Any], bool]]:
        if node.get("type") == "array" and isinstance(node.get("items"), Mapping):
            yield from walk(node["items"], prefix + "[]")
            return
        required = set(node.get("required", ()))
        for name, child in (node.get("properties") or {}).items():
            dotted = f"{prefix}.{name}" if prefix else name
            yield dotted, child, name in required
            yield from walk(child, dotted)

    yield from walk(schema, "")


# -- the call validator ------------------------------------------------------------------------


class CallError(ValueError):
    """A call that does not match its tool's schema: what is wrong, where, and what would do.

    ``field`` is the dotted path of the argument (``box.x``, ``cells[2].text``), and
    ``valid_options`` the allowed values or names where there is a closed set.
    """

    def __init__(self, message: str, field: str | None = None,
                 valid_options: list[Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        self.valid_options = valid_options or []


def validate_call(schema: Mapping[str, Any], arguments: Any) -> dict[str, Any]:
    """Check ``arguments`` against a (validation) schema and return them normalised.

    Normalising drops an optional property given as ``null`` -- the OpenAI adapters send
    every optional property, as ``null`` when unused -- so a handler sees it as absent
    either way.  An integer given as ``3.0`` becomes ``3``.  Raises :class:`CallError` on
    the first problem.
    """
    if not isinstance(arguments, Mapping):
        raise CallError("the arguments must be a JSON object")
    return _validate(schema, arguments, "")


def _validate(node: Mapping[str, Any], value: Any, where: str) -> Any:
    kind = node.get("type")
    label = where or "the arguments"
    if kind == "object":
        if not isinstance(value, Mapping):
            raise CallError(f"{label} must be an object", where or None)
        if is_free(node):
            return dict(value)
        properties = node.get("properties") or {}
        required = set(node.get("required", ()))
        unknown = [key for key in value if key not in properties]
        if unknown:
            name = _join(where, unknown[0])
            raise CallError(f"unknown argument {name!r}", name, sorted(properties))
        result: dict[str, Any] = {}
        for name, child in properties.items():
            path = _join(where, name)
            if name not in value or value[name] is None:
                if name in required:
                    raise CallError(f"{path!r} is required", path)
                continue
            result[name] = _validate(child, value[name], path)
        return result
    if kind == "array":
        if not isinstance(value, list):
            raise CallError(f"{label} must be an array", where)
        if "minItems" in node and len(value) < node["minItems"]:
            raise CallError(f"{label} needs at least {node['minItems']} item(s)", where)
        if "maxItems" in node and len(value) > node["maxItems"]:
            raise CallError(f"{label} has {len(value)} items; at most {node['maxItems']}", where)
        items = node.get("items") or {}
        return [_validate(items, item, f"{where}[{index}]") for index, item in enumerate(value)]
    if kind == "string":
        if not isinstance(value, str):
            raise CallError(f"{label} must be a string", where)
        if "enum" in node and value not in node["enum"]:
            raise CallError(f"{label} is {value!r}; not one of the allowed values", where,
                            list(node["enum"]))
        if "minLength" in node and len(value) < node["minLength"]:
            raise CallError(f"{label} must be at least {node['minLength']} characters", where)
        if "maxLength" in node and len(value) > node["maxLength"]:
            raise CallError(f"{label} is {len(value)} characters; at most {node['maxLength']}",
                            where)
        fmt = node.get("format")
        if fmt == "date" and not _parses(_dt.date.fromisoformat, value, 10):
            raise CallError(f"{label} must be a date, YYYY-MM-DD", where)
        if fmt == "date-time" and not _parses(_parse_datetime, value):
            raise CallError(f"{label} must be a date-time, YYYY-MM-DDThh:mm:ssZ", where)
        return value
    if kind == "boolean":
        if not isinstance(value, bool):
            raise CallError(f"{label} must be true or false", where)
        return value
    if kind in ("integer", "number"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CallError(f"{label} must be a number", where)
        if kind == "integer":
            if isinstance(value, float):
                if not value.is_integer():
                    raise CallError(f"{label} must be a whole number", where)
                value = int(value)
        if value != value or value in (float("inf"), float("-inf")):
            raise CallError(f"{label} must be a finite number", where)
        if "minimum" in node and value < node["minimum"]:
            raise CallError(f"{label} is {value}; at least {node['minimum']}", where)
        if "maximum" in node and value > node["maximum"]:
            raise CallError(f"{label} is {value}; at most {node['maximum']}", where)
        return value
    raise CallError(f"{label}: the schema has no usable type")


def _join(where: str, name: str) -> str:
    return f"{where}.{name}" if where else name


def _parses(parse: Any, value: str, length: int | None = None) -> bool:
    if length is not None and len(value) != length:
        return False
    try:
        parse(value)
    except ValueError:
        return False
    return True


def _parse_datetime(value: str) -> _dt.datetime:
    if len(value) < 19 or value[10] not in "Tt":
        raise ValueError(value)
    return _dt.datetime.fromisoformat(value[:-1] + "+00:00" if value[-1] in "Zz" else value)
