"""Sources: rows a call takes from a dataset or from another open document.

A tool that places values -- a chart's categories and series -- may take them from a
*source* instead of literal arrays: a dataset in the session (``d:by_region``,
:mod:`.datasets`) or an address in another open document that the document's format turns
into rows (``t:sales``, a table).  The tool declares the argument (``Tool.sources``, paths as
``Tool.refs`` writes them); the handler reads it with ``call.source(value)`` and gets
:class:`Rows`: a :class:`~.datasets.Dataset` -- columns, rows, each value's sources -- and
where it came from.

**The resolver hook.**  A format says which addresses of its documents are rows, and how
to read them, in :attr:`~.session.DocumentFormat.datasets`: ``{prefix: resolve}``, where
``resolve(document, address)`` returns a :class:`~.datasets.Dataset` whose sources name
each value's origin (its own address strings, which the tool layer never reads) or raises
:class:`~.results.ToolError` (``not_found`` with the names there are).  A format may also
give :attr:`~.session.DocumentFormat.merge_sources`, which writes a run of its addresses
compactly (cells next to each other as one range) for a result; without one they are
listed as they are, each once.

**Which document.**  ``d2/t:sales`` names the document; ``t:sales`` alone is the one open
document whose format takes ``t:`` addresses, and with several it is ``ambiguous``, the
qualified forms its ``valid_options``.  The dispatcher qualifies the value before the call
runs and holds that document's lock for the call (for reading: it is not the call's
document, gets no undo step, and its version does not change).  ``d:`` is never a format's
prefix: it is the session's datasets.
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from .datasets import DATASET_PREFIX, Dataset
from .results import ToolError

#: A source naming its document: ``d2/t:sales``.
QUALIFIED = re.compile(r"^(d[0-9]+)/(.+)$")


@dataclass(frozen=True)
class Rows:
    """What a source named: the :class:`~.datasets.Dataset` and where it came from.

    ``address`` the source as resolved (``d:by_region``, ``d2/t:sales``); ``doc``, ``kind``
    and ``name`` the document the values came from (a dataset's source document; ``None``
    without one); ``version`` that document's version the values are of; ``stale`` whether
    it has changed since (a dataset only: an address is read now).  ``merge`` writes a run
    of the format's source addresses compactly (:func:`merged`)."""

    address: str
    dataset: Dataset
    doc: str | None = None
    kind: str | None = None
    name: str | None = None
    version: int | None = None
    stale: bool = False
    merge: Callable[[Sequence[str]], Sequence[str]] | None = None

    def describe(self) -> dict[str, Any]:
        """Where the values came from, for a result: never the values."""
        data: dict[str, Any] = {"source": self.address, "doc": self.doc,
                                "version": self.version}
        if self.stale:
            data["stale"] = True
        return data

    def column(self, name: str, *, field: str | None = None) -> int:
        """The position of the column ``name`` -- exactly, else the one equal case aside;
        ``not_found`` with the columns there are."""
        columns = self.dataset.columns
        if name in columns:
            return columns.index(name)
        folded = [i for i, column in enumerate(columns) if column.casefold() == name.casefold()]
        if len(folded) == 1:
            return folded[0]
        raise ToolError("not_found", f"{self.address} has no column {name!r}", field=field,
                        valid_options=list(columns))

    def where(self, row: int, column: int) -> str:
        """One value named for a message: its sources (the first two), else its position in
        the source (row from 1)."""
        sources = self.dataset.sources
        found = sources[row][column] if sources is not None else ()
        if found:
            shown = ", ".join(found[:2]) + (f" and {len(found) - 2} more" if len(found) > 2
                                            else "")
            return f"{shown} (row {row + 1} of {self.address}, column " \
                   f"{self.dataset.columns[column]!r})"
        return f"row {row + 1} of {self.address}, column {self.dataset.columns[column]!r}"

    def provenance(self, column: int, rows: Sequence[int]) -> dict[str, Any]:
        """Where a column's values at ``rows`` (from 0, in the order placed) came from: the
        column, the rows (from 1, runs as ``1-4``) and their sources, merged."""
        data: dict[str, Any] = {"column": self.dataset.columns[column], "rows": runs(rows)}
        if self.dataset.sources is not None:
            data["sources"] = merged((source for row in rows
                                      for source in self.dataset.sources[row][column]),
                                     self.merge)
        return data


def runs(rows: Iterable[int]) -> str:
    """Row positions from 0 as text from 1, consecutive ones as runs: ``1-4,7``."""
    parts: list[str] = []
    start = previous = None
    for row in rows:
        if previous is not None and row == previous + 1:
            previous = row
            continue
        if start is not None:
            parts.append(_run(start, previous))
        start = previous = row
    if start is not None:
        parts.append(_run(start, previous))
    return ",".join(parts)


def _run(start: int, end: int | None) -> str:
    return str(start + 1) if end == start else f"{start + 1}-{(end or 0) + 1}"


def merged(sources: Iterable[str],
           merge: Callable[[Sequence[str]], Sequence[str]] | None = None) -> list[str]:
    """Source addresses in order, each once; through the format's ``merge`` when it has
    one (which may join neighbours into ranges)."""
    unique = list(dict.fromkeys(sources))
    if merge is None or not unique:
        return unique
    return [str(source) for source in merge(unique)]


def prefixes(formats: Mapping[str, Any]) -> dict[str, list[str]]:
    """``{kind: [prefix, ...]}``: the address prefixes each format resolves to rows."""
    return {kind: sorted((p for p in (getattr(fmt, "datasets", None) or {})
                          if p and not p.startswith(DATASET_PREFIX)), key=len, reverse=True)
            for kind, fmt in formats.items()}


def qualify(value: str, documents: Mapping[str, Any], formats: Mapping[str, Any], *,
            field: str | None = None) -> tuple[str, str]:
    """``(doc, address)`` of a source that is not a dataset: the document ``value`` names
    (``d2/t:sales``), or the one open document whose format takes its prefix.

    ``documents`` maps handles to entries (with ``kind``); ``invalid_arguments`` when no
    format takes the address, ``not_found`` when no open document does, ``ambiguous``
    when several do."""
    taken = prefixes(formats)
    match = QUALIFIED.match(value)
    doc, address = (match.group(1), match.group(2)) if match else (None, value)
    prefix = next((p for kind in sorted(taken) for p in taken[kind]
                   if address.startswith(p)), None)
    known = sorted({p for found in taken.values() for p in found})
    if prefix is None:
        raise ToolError("invalid_arguments", f"{value!r} is not a source: give a dataset "
                        f"(d:<id>) or an address of an open document"
                        + (f" ({', '.join(p + '...' for p in known)})" if known else ""),
                        field=field, valid_options=[DATASET_PREFIX + "<id>"] + known)
    candidates = sorted((handle for handle, entry in documents.items()
                         if prefix in taken.get(entry.kind, ())), key=_doc_number)
    if doc is not None:
        if doc not in documents:
            raise ToolError("not_found", f"no open document {doc!r}", field=field,
                            valid_options=[f"{handle}/{address}" for handle in candidates])
        if doc not in candidates:
            raise ToolError("invalid_arguments", f"{doc} ({documents[doc].kind}) takes no "
                            f"{prefix} addresses", field=field,
                            valid_options=[f"{handle}/{address}" for handle in candidates])
        return doc, address
    if not candidates:
        raise ToolError("not_found", f"no open document takes {prefix} addresses: open one "
                        "first", field=field)
    if len(candidates) > 1:
        raise ToolError("ambiguous", f"{len(candidates)} open documents take {prefix} "
                        "addresses: name one", field=field,
                        valid_options=[f"{handle}/{address}" for handle in candidates])
    return candidates[0], address


def _doc_number(handle: str) -> tuple[int, str]:
    digits = handle[1:]
    return (int(digits), handle) if digits.isdigit() else (1 << 30, handle)


def resolve(fmt: Any, document: Any, address: str) -> Dataset:
    """The rows ``address`` names in ``document``, by its format's resolver."""
    for prefix in sorted(getattr(fmt, "datasets", None) or {}, key=len, reverse=True):
        if prefix and address.startswith(prefix):
            found = fmt.datasets[prefix](document, address)
            if not isinstance(found, Dataset):
                raise TypeError(f"the {fmt.kind} resolver for {prefix} returned "
                                f"{type(found).__name__}, not a Dataset")
            return found
    raise ToolError("invalid_arguments", f"{fmt.kind} documents take no such address: "
                    f"{address!r}")


# -- values --------------------------------------------------------------------------------------


def number(value: Any) -> bool:
    """Whether ``value`` is a finite number (a bool is not one)."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def what(value: Any) -> str:
    """A value's kind for a message -- never the value itself, which may be private."""
    if value is None:
        return "blank"
    if isinstance(value, bool):
        return "a logical value"
    if isinstance(value, str):
        return "text" if value.strip() else "empty text"
    if isinstance(value, float) and not math.isfinite(value):
        return "not a finite number"
    return f"a {type(value).__name__}"


def label(value: Any, locale: Any = None) -> str | None:
    """A value as a category label or a cell's text: text as it is; a number as a cell in
    the General format writes it, with the locale's decimal separator (``1.5`` is ``1,5``
    in nl-NL; ``2024`` stays ``2024``, no thousands separator); a logical value as ``TRUE``
    or ``FALSE``; a Python ``date``, ``datetime`` or ``time`` as :func:`moment_text` writes
    it, in the locale's day order.  ``None`` for a blank, or anything else."""
    if isinstance(value, str):
        return value if value != "" else None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (_dt.date, _dt.time)):
        return moment_text(value, locale)
    if not number(value):
        return None
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    text = value if isinstance(value, int) else format(value, ".15g")
    text = str(text)
    if locale is not None and getattr(locale, "decimal_separator", ".") != ".":
        text = text.replace(".", locale.decimal_separator)
    return text


def moment_text(value: "_dt.date | _dt.time", locale: Any = None) -> str:
    """A Python ``date``, ``datetime`` or ``time`` as text in the locale's day order
    (``date_order``; en-US's ``mdy`` without one): a date's day, month and four-digit
    year as digits joined by ``/`` -- ``3/1/2026`` in ``mdy``, ``1/3/2026`` in ``dmy`` --
    or ISO 8601 in ``ymd`` (``2026-03-01``); a time of day on the 24-hour clock,
    ``14:05`` (``14:05:30`` with seconds); a datetime the two, a space between.  A time
    zone is not written: the wall-clock time is.

    This is the library's own way to write a date into a cell, not Office's short-date
    format for the locale, which is not measured: the order is the locale's, the day and
    the month unpadded."""
    if isinstance(value, _dt.datetime):
        return f"{moment_text(value.date(), locale)} {moment_text(value.time(), locale)}"
    if isinstance(value, _dt.time):
        clock = f"{value.hour:02d}:{value.minute:02d}"
        return f"{clock}:{value.second:02d}" if value.second else clock
    order = getattr(locale, "date_order", None) or "mdy"
    if order == "ymd" or order not in ("dmy", "mdy"):
        return f"{value.year:04d}-{value.month:02d}-{value.day:02d}"
    parts = {"d": value.day, "m": value.month, "y": value.year}
    return "/".join(str(parts[letter]) for letter in order)


__all__ = ["QUALIFIED", "Rows", "label", "merged", "moment_text", "number", "prefixes",
           "qualify", "resolve", "runs", "what"]
