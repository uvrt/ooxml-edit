"""What a model meant by a tool name the toolbox does not have: a tool, and an action of it.

A model that writes ``x_add_chart`` where charts are added with ``edit_chart``'s ``add``,
or ``x_delete_page`` for ``x_pages``' ``delete``, is told so -- not handed every tool's
name.  :func:`tool_hints` reads the name's words against the toolbox's own: each tool's
name and the values of its ``action`` enum, with a verb a model writes for another
(``insert``, ``create``, ``remove``, ``update``...) read as the one it means (:data:`VERBS`).
A format adds the cases that rule misses as data, ``DocumentFormat.tool_hints``
(``{"x_delete_box": "x_arrange delete"}``), its keys read the same way, so a key also
covers its spellings with another verb, plural or prefix.

Ranked, best first, and only the best rank is returned:

1. the format's own table;
2. a tool whose name says the same, verb read (``x_insert_picture``: ``x_add_picture``);
3. an action whose words are in the name, and a word of the name left over is one of the
   tool's (``x_add_chart``: ``edit_chart`` ``add``; ``x_remove_pages``: ``x_pages``
   ``delete``);
4. an action of two words or more that is the whole name but its prefix
   (``x_insert_row``: ``x_edit_table`` ``insert_row``);
5. a tool whose name is the name's start (``x_read_cells``: ``x_read``);
6. an action only one tool has, named as it is (``x_align_boxes``: ``x_arrange``
   ``align``).

Words are a name's parts between underscores, compared ignoring case and a plural ``s``; a
format's prefix -- the first word of three tools or more -- is no word of the thing meant.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

#: A verb a model writes, read as the verb a tool's name or action uses for it.  Only for
#: comparing: a hint names the tool's own action.
VERBS: dict[str, str] = {
    **dict.fromkeys(("add", "insert", "create", "new", "append", "make"), "add"),
    **dict.fromkeys(("delete", "remove", "drop", "del", "erase"), "delete"),
    **dict.fromkeys(("set", "update", "change", "modify", "edit"), "set"),
    **dict.fromkeys(("read", "get", "list", "show", "view", "fetch"), "read"),
    **dict.fromkeys(("move", "reorder", "reposition"), "move"),
    **dict.fromkeys(("duplicate", "copy", "clone"), "duplicate"),
}

#: The most hints a message names.
MAX_HINTS = 3


@dataclass(frozen=True)
class Hint:
    """A tool, and the action of it, that does what an unknown name says."""

    tool: str
    action: str | None = None

    def __str__(self) -> str:
        return self.tool + (f" with action {self.action}" if self.action else "")


def singular(word: str) -> str:
    """``word`` without a plural ending: ``pages`` ``page``, ``categories`` ``category``,
    ``boxes`` ``box``;
    ``series``, ``ss`` and short words as they are."""
    if word == "series" or len(word) <= 3 or word.endswith("ss"):
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "xes", "sses")):
        return word[:-2]
    return word[:-1] if word.endswith("s") else word


def words(name: str) -> list[str]:
    return [singular(part) for part in name.lower().split("_") if part]


def verb(word: str) -> str:
    return VERBS.get(word, word)


def prefixes(tools: Iterable[str]) -> frozenset[str]:
    """The first words that are a format's prefix: each the first of three tools or more."""
    counts: dict[str, int] = {}
    for name in tools:
        first = words(name)[:1]
        if first:
            counts[first[0]] = counts.get(first[0], 0) + 1
    return frozenset(word for word, count in counts.items() if count >= 3)


def _core(name: str, prefix: frozenset[str]) -> list[str]:
    """A name's words without its format prefix."""
    found = words(name)
    return found[1:] if len(found) > 1 and found[0] in prefix else found


def _key(core: list[str]) -> tuple[str, ...]:
    """A name's words as compared: the first, a verb, read as the verb it means (only
    where more words follow: ``x_changes`` stays a noun)."""
    return tuple([verb(core[0]), *core[1:]]) if len(core) > 1 else tuple(core)


def _keys(name: str, prefix: frozenset[str]) -> list[tuple[str, ...]]:
    """The keys ``name`` may stand for: its own, and without a first word that is some
    other prefix (``xx_`` for ``x_``, a namespace written with ``_``)."""
    core = _core(name, prefix)
    found = [_key(core)]
    if len(core) > 2:
        found.append(_key(core[1:]))
    return found


def _same(a: str, b: str) -> bool:
    """Two nouns that name the same thing: equal, or one ends with the other
    (``footnote``, ``note``), four letters or more."""
    return a == b or (min(len(a), len(b)) >= 4 and (a.endswith(b) or b.endswith(a)))


def tool_hints(name: Any, tools: Mapping[str, Any], table: Mapping[str, str] | None = None,
               *, exclude: Iterable[str] = ()) -> list[Hint]:
    """The tools (and actions) ``name``, which is no tool, most likely means, best first:
    at most :data:`MAX_HINTS`, all of the best rank (see the module); ``[]`` when none.
    ``tools`` maps names to :class:`~.registry.Tool`; ``table`` is the formats' own
    (``{name: "tool"}`` or ``{name: "tool action"}``), its entries naming no tool of
    ``tools`` ignored; ``exclude`` names tools never to suggest."""
    if not isinstance(name, str) or not name.strip():
        return []
    name = name.rsplit(".", 1)[-1]
    excluded = set(exclude)
    known = {tool_name: tool for tool_name, tool in tools.items() if tool_name not in excluded}
    prefix = prefixes(tools)
    wanted = _keys(name, prefix)
    tiers: list[list[tuple[tuple[int, ...], Hint]]] = [[] for _ in range(6)]

    for key, target in (table or {}).items():
        tool_name, _, action = str(target).partition(" ")
        tool = known.get(tool_name)
        if tool is None or (action and action not in tool.actions):
            continue
        if any(_key(_core(key, prefix)) == want for want in wanted):
            tiers[0].append(((0,), Hint(tool_name, action or None)))

    for tool_name, tool in known.items():
        key = _key(_core(tool_name, prefix))
        if any(key == want for want in wanted):
            tiers[1].append(((0,), Hint(tool_name)))
        elif any(len(want) > len(key) and want[:len(key)] == key for want in wanted):
            tiers[4].append(((len(key),), Hint(tool_name)))

    given = _core(name, prefix)
    verbs = [verb(word) for word in given]
    owners: dict[str, list[str]] = {}
    for tool_name, tool in known.items():
        for action in tool.actions:
            owners.setdefault(action, []).append(tool_name)
    for tool_name, tool in known.items():
        nouns = _nouns(_core(tool_name, prefix))
        for action in tool.actions:
            needed = [verb(word) for word in words(action)]
            rest = _unmatched(given, verbs, needed)
            if rest is None:
                continue
            matched = sum(1 for word in rest if any(_same(word, noun) for noun in nouns))
            hint = Hint(tool_name, action)
            if matched:
                tiers[2].append(((matched, len(needed), -(len(rest) - matched)), hint))
            elif not rest and len(needed) > 1:
                tiers[3].append(((len(needed),), hint))
            elif len(owners.get(action, ())) == 1 and all(word in given
                                                          for word in words(action)):
                tiers[5].append(((len(needed), -len(rest)), hint))

    for tier in tiers:
        if tier:
            ranked = sorted(tier, key=lambda pair: pair[0], reverse=True)
            return list(dict.fromkeys(hint for _, hint in ranked))[:MAX_HINTS]
    return []


def _nouns(core: list[str]) -> list[str]:
    """The words of a name's core that name a thing: not its verbs (``edit`` of
    ``edit_chart``), unless the verb is all there is (``x_changes``)."""
    found = [word for word in core if word not in VERBS]
    return found if found and len(core) > 1 else core


def _unmatched(given: list[str], verbs: list[str], needed: list[str]) -> list[str] | None:
    """The words of ``given`` (``verbs``: each read as a verb) that the action's words
    (``needed``) leave over, in the name's own spelling; ``None`` when one of the action's
    words is not in the name."""
    taken = list(needed)
    rest = []
    for word, as_verb in zip(given, verbs):
        if as_verb in taken:
            taken.remove(as_verb)
        else:
            rest.append(word)
    return None if taken else rest


def action_hints(tool: Any, value: Any, tools: Mapping[str, Any],
                 table: Mapping[str, str] | None = None) -> tuple[str | None, list[Hint]]:
    """For a call of ``tool`` whose ``action`` ``value`` it does not have: the action of
    its own the value means with a verb read (``add_row``: ``insert_row``), or ``None``;
    and the other tools whose action it is (``accept`` on a comments tool: the tracked
    changes tool's) -- the value read as a name alone and with each word of the tool's
    name (``add`` on ``x_pages``: ``x_add_page``)."""
    if not isinstance(value, str) or not value:
        return None, []
    given = tuple(verb(word) for word in words(value))
    own = next((action for action in tool.actions
                if tuple(verb(word) for word in words(action)) == given), None)
    if own is not None:
        return own, []
    prefix = prefixes(tools)
    found: list[Hint] = []
    for name in [value] + [f"{value}_{noun}" for noun in _nouns(_core(tool.name, prefix))]:
        for hint in tool_hints(name, tools, table, exclude=(tool.name,)):
            if hint not in found:
                found.append(hint)
    return None, found[:MAX_HINTS]
