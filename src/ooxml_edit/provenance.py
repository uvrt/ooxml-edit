"""Provenance records: where placed values came from, small enough to keep in the file.

A tool that places values from a source -- a dataset, or a table of another open document
-- says where each one came from in its result (``data.from``).  A *record* is the part of
that worth keeping in the saved document: the source, the name of the document it came
from, that document's version, and per column (or category and series) the column name,
the rows placed and their sources.  What only means something inside one session -- the
source document's handle, ``d3`` -- is left out, and the source is written without it
(``t:sales``, not ``d3/t:sales``).

A record is a JSON object, versioned and compact::

    {"v":1,"source":"t:sales","document":"budget-made-up.ods","version":3,
     "categories":{"column":"region","rows":"1-3","sources":["G!A2:A4"]},
     "series":[{"name":"units","column":"units","rows":"1-3","sources":["G!B2:B4"]}]}

:func:`record` builds one from a result's ``data.from``; :func:`dumps` writes it as compact
JSON and :func:`loads` reads it back, refusing what is not a record (:class:`RecordError`).
``v`` is :data:`VERSION`; a reader gets a record of a later version as it is, and decides.

**Holders.**  Some places take a record wherever their document puts them: an embedded
object that keeps it inside its own part, and so takes it along when it is copied
elsewhere.  The code that edits one says so while a placement is watched -- :func:`note`
with a :class:`Holder` that can store and read its record -- and the placing code, which
does not know that kind of object, collects them with :func:`watch` and hands the record to
the one the placement touched.  Outside :func:`watch`, :func:`note` does nothing.

Format-neutral: which document, which part and which element hold a record is the format
layer's business (and the embedded objects' own).
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Hashable, Iterator, Mapping

#: The record format's version, ``v`` in every record.
VERSION = 1

#: What a result's ``data.from`` holds that only means something inside one session.
SESSION_ONLY = ("doc",)

#: A source naming its session's document: ``d3/t:sales``.
_QUALIFIED = re.compile(r"^d[0-9]+/(.+)$")


class RecordError(ValueError):
    """Text that is not a provenance record."""


def record(placed: Mapping[str, Any], *, document: str | None = None) -> dict[str, Any]:
    """The record of a placement: ``placed`` (a result's ``data.from``) without what only
    the session knows, its source without the document handle, and ``document`` -- the
    name of the document the values came from -- when there is one.  Keys whose value is
    ``None`` are left out; the rest are kept in order, after ``v``, ``source`` and
    ``document``."""
    if "source" not in placed:
        raise RecordError("a placement names its source")
    source = str(placed["source"])
    match = _QUALIFIED.match(source)
    out: dict[str, Any] = {"v": VERSION, "source": match.group(1) if match else source}
    if document:
        out["document"] = document
    for key, value in placed.items():
        if key in ("source", "document", "v", *SESSION_ONLY) or value is None:
            continue
        out[key] = value
    return out


def dumps(found: Mapping[str, Any]) -> str:
    """A record as compact JSON (no spaces; text as it is, not escaped to ASCII)."""
    _check(found)
    return json.dumps(dict(found), ensure_ascii=False, separators=(",", ":"))


def loads(text: str | bytes) -> dict[str, Any]:
    """The record ``text`` holds; :class:`RecordError` when it is not JSON, not an object,
    or has no whole-number ``v`` of at least 1."""
    try:
        found = json.loads(text)
    except ValueError as error:
        raise RecordError(f"not JSON: {error}") from None
    _check(found)
    return found


def _check(found: Any) -> None:
    if not isinstance(found, Mapping):
        raise RecordError(f"a record is a JSON object, not {type(found).__name__}")
    version = found.get("v")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise RecordError(f"a record has a version v of 1 or more, not {version!r}")


# -- holders -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Holder:
    """An object that keeps a record inside itself: ``store(record)`` replaces it,
    ``read()`` returns it (``None`` when there is none).  ``key`` tells one from another:
    the same object noted twice is one holder."""

    key: Hashable
    store: Callable[[Mapping[str, Any]], None]
    read: Callable[[], "dict[str, Any] | None"]


_WATCHED: ContextVar["list[Holder] | None"] = ContextVar("ooxml_edit_provenance_holders",
                                                        default=None)


@contextmanager
def watch() -> Iterator[list[Holder]]:
    """Collect the holders noted inside, each once, in the order first noted.  Nests: an
    inner watch collects for itself, and the outer one sees its holders too."""
    outer = _WATCHED.get()
    found: list[Holder] = []
    token = _WATCHED.set(found)
    try:
        yield found
    finally:
        _WATCHED.reset(token)
        if outer is not None:
            for holder in found:
                _add(outer, holder)


def note(holder: Holder) -> None:
    """Say that ``holder`` was used, for the :func:`watch` around this code (if any)."""
    found = _WATCHED.get()
    if found is not None:
        _add(found, holder)


def _add(found: list[Holder], holder: Holder) -> None:
    if all(known.key != holder.key for known in found):
        found.append(holder)


__all__ = ["Holder", "RecordError", "SESSION_ONLY", "VERSION", "dumps", "loads", "note",
           "record", "watch"]
