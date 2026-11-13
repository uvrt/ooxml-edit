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
import json
import logging
import math
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from .. import dates
from .datasets import DATASET_PREFIX, Dataset, dataset_id
from .results import ToolError

logger = logging.getLogger("ooxml_edit.tools")

#: A source naming its document: ``d2/t:sales``.
QUALIFIED = re.compile(r"^(d[0-9]+)/(.+)$")

#: A source a row range can follow: a whole dataset or table (``d:by_region``,
#: ``d2/t:sales``), or a row range already (``t:sales/#9:``, ``t:sales/#9:#40``).
_RANGEABLE = re.compile(r"^((?:d[0-9]+/)?[a-z]:[^/\[\]]+)"
                        r"(?:/#([1-9][0-9]*):(?:#?[1-9][0-9]*)?)?$")


@dataclass(frozen=True)
class Rows:
    """What a source named: the :class:`~.datasets.Dataset` and where it came from.

    ``address`` the source as resolved (``d:by_region``, ``d2/t:sales``); ``doc``, ``kind``
    and ``name`` the document the values came from (a dataset's source document; ``None``
    without one); ``version`` that document's version the values are of; ``stale`` whether
    it has changed since (a dataset only: an address is read now), or the values came from
    invalidated formula caches (:attr:`Dataset.invalidated <.datasets.Dataset.invalidated>`,
    an address too).  ``merge`` writes a run
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
        """Where the values came from, for a result: never the values.  ``cache``: the
        formula caches they were read from, counted by status (:attr:`Dataset.cache
        <.datasets.Dataset.cache>`), when any was."""
        data: dict[str, Any] = {"source": self.address, "doc": self.doc,
                                "version": self.version}
        if self.stale:
            data["stale"] = True
        if self.dataset.cache:
            data["cache"] = dict(self.dataset.cache)
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

    def type(self, column: int) -> str | None:
        """The column's type label (``Dataset.types``); ``None`` without one."""
        types = self.dataset.types
        return types[column] if types is not None and column < len(types) else None

    def label(self, row: int, column: int, locale: Any = None,
              date_format: str | None = None) -> str | None:
        """The value at ``row``, ``column`` as :func:`label` writes it, by its column's type:
        a ``date``, ``datetime`` or ``time`` column's ISO 8601 text in the locale, as a
        Python date is; a date in ``date_format`` when given (:func:`date_pattern`)."""
        return label(self.dataset.rows[row][column], locale, type=self.type(column),
                     date_format=date_format)

    def date(self, row: int, column: int) -> "_dt.date | None":
        """The value at ``row``, ``column`` when it is a date (:func:`date_value`)."""
        return date_value(self.dataset.rows[row][column], self.type(column))

    def provenance(self, column: int, rows: Sequence[int]) -> dict[str, Any]:
        """Where a column's values at ``rows`` (from 0, in the order placed) came from: the
        column, the rows (from 1, runs as ``1-4``) and their sources, merged."""
        data: dict[str, Any] = {"column": self.dataset.columns[column], "rows": runs(rows)}
        if self.dataset.sources is not None:
            data["sources"] = merged((source for row in rows
                                      for source in self.dataset.sources[row][column]),
                                     self.merge)
        return data


def first_rows(address: str, count: int) -> str | None:
    """The first ``count`` rows of a source as a row range, for a refusal to offer when a
    source gives more rows than there is room for: ``t:sales/#1:#9`` of ``t:sales``,
    ``d:by_region/#10:#14`` of ``d:by_region/#10:``, ``d:by_region/#1`` for one row.
    ``None`` when the source is no whole dataset or table, nor a row range (a column, a key
    set), or ``count`` is under 1."""
    match = _RANGEABLE.match(address.strip()) if isinstance(address, str) else None
    if match is None or count < 1:
        return None
    first = int(match.group(2) or 1)
    last = first + count - 1
    return f"{match.group(1)}/#{first}" + ("" if last == first else f":#{last}")


def blank_text(positions: list[int], one: str, many: str, *, start: int) -> str:
    """Where a series' blank points are, for a result: ``"1 blank point (category 2)"``,
    ``"3 blank points (rows 4-5, 9)"`` -- positions counted from ``start``, consecutive
    ones as runs, never a value::

        blank_text([3, 4, 8], "row", "rows", start=1)   # '3 blank points (rows 4-5, 9)'
    """
    runs: list[str] = []
    for position in positions:
        shown = position + start
        if runs and runs[-1].split("-")[-1] == str(shown - 1):
            runs[-1] = f"{runs[-1].split('-')[0]}-{shown}"
        else:
            runs.append(str(shown))
    count = len(positions)
    return (f"{count} blank point{'s' if count != 1 else ''} "
            f"({one if count == 1 else many} {', '.join(runs)})")


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


# -- refusals that name what there is (round 56, R120) -------------------------------------------

#: The most sources a refusal offers (``valid_options`` and its words); the rest are counted.
MAX_OFFERED = 12

#: The refusals :func:`refusal` rewrites: what names no rows.  ``limit`` and the like name
#: rows that are there, and stand as they are.
_REFINED = ("not_found", "invalid_arguments", "ambiguous")


@contextmanager
def reading(entry: Any, wait: float = 1.0) -> Iterator[Any]:
    """``entry``'s document to read for a refusal's words, or ``None`` when it cannot be
    read now: under its lock when this thread holds it, else its committed copy
    (``DocumentEntry.reader``), else its lock taken for at most ``wait`` seconds -- a
    refusal never waits long on another call's edit."""
    owned = getattr(entry.lock, "_is_owned", None)      # threading.RLock's own test
    if owned is not None and owned():
        yield entry.document
        return
    reader = entry.reader() if hasattr(entry, "reader") else None
    if reader is not None:
        yield reader.document
        return
    if not entry.lock.acquire(timeout=wait):
        yield None
        return
    try:
        yield entry.document
    finally:
        entry.lock.release()


def offered(session: Any, datasets: Iterable[str] = ()) -> tuple[list[str], list[str]]:
    """The sources there are, as a ``from`` takes them: ``(datasets, tables)`` -- the
    datasets (``datasets``: ids an earlier op of the batch defined, then the session's), and
    each open document's tables (its format's ``value_sources``), written bare when one
    open document alone takes their prefix, else with its handle (``d2/t:stock``).  A
    document that cannot be read now (another call's edit holds it) gives none."""
    ids = list(dict.fromkeys([*(DATASET_PREFIX + found for found in datasets),
                              *session.dataset_ids()]))
    formats = session.formats
    documents = dict(session.documents)
    taken = prefixes(formats)
    tables: list[str] = []
    for handle in sorted(documents, key=_doc_number):
        entry = documents[handle]
        hook = getattr(formats.get(entry.kind), "value_sources", None)
        if hook is None or not taken.get(entry.kind):
            continue
        with reading(entry) as document:
            if document is None:
                continue
            try:
                found = [str(address) for address in hook(document)]
            except Exception:  # noqa: BLE001 -- a listing only adds words
                logger.exception("value_sources of %s failed", entry.kind)
                continue
        for address in found:
            prefix = next((p for p in taken[entry.kind] if address.startswith(p)), None)
            if prefix is None:
                continue
            sharing = [h for h, e in documents.items() if prefix in taken.get(e.kind, ())]
            tables.append(address if len(sharing) == 1 else f"{handle}/{address}")
    return ids, list(dict.fromkeys(tables))


def resolving(option: Any, session: Any, datasets: Iterable[str] = (), *,
              doc: str | None = None) -> str | None:
    """``option`` as a source that resolves -- a dataset there is, an address that names
    rows of an open document -- else ``None``.  ``doc``: the document a bare address was
    looked up in (a resolver's own suggestions), which it is then read in, written with
    the handle when bare it would name another document or several."""
    if not isinstance(option, str) or not option:
        return None
    if option.startswith(DATASET_PREFIX):
        found = dataset_id(option)
        known = {*datasets, *(dataset_id(address) for address in session.dataset_ids())}
        return option if found in known else None
    formats = session.formats
    documents = dict(session.documents)
    named = option if doc is None or QUALIFIED.match(option) else f"{doc}/{option}"
    try:
        handle, address = qualify(named, documents, formats)
    except ToolError:
        return None
    entry = documents[handle]
    with reading(entry) as document:
        if document is None:
            return None
        try:
            resolve(formats[entry.kind], document, address)
        except ToolError as error:
            if error.code != "limit":       # too many values: there, but too big
                return None
        except Exception:  # noqa: BLE001 -- a suggestion that fails to read is none
            return None
    try:
        bare = qualify(address, documents, formats)[0] == handle
    except ToolError:
        bare = False
    return address if bare else f"{handle}/{address}"


def source_like(option: Any, formats: Mapping[str, Any]) -> bool:
    """Whether a refusal's option is a source address (a dataset, an open document's rows),
    which :func:`refusal` offers only when it resolves."""
    if not isinstance(option, str):
        return False
    known = {p for found in prefixes(formats).values() for p in found}
    match = QUALIFIED.match(option)
    body = match.group(2) if match else option
    return option.startswith(DATASET_PREFIX) or any(body.startswith(p) for p in known)


def shapes(formats: Mapping[str, Any]) -> str:
    """What a source is, in words: ``a dataset (d:<id>) or rows of an open <kind> document
    (t:<name>)``."""
    kinds = [f"rows of an open {kind} document ({', '.join(p + '<name>' for p in found)})"
             for kind, found in sorted(prefixes(formats).items()) if found]
    return " or ".join([f"a dataset ({DATASET_PREFIX}<id>)", *kinds])


def _listing(ids: Sequence[str], tables: Sequence[str]) -> str:
    """``datasets d:a, d:b; tables t:stock`` -- at most :data:`MAX_OFFERED` in all, the
    rest counted."""
    room = MAX_OFFERED
    parts = []
    for name, found in (("datasets", ids), ("tables", tables)):
        if found:
            shown = list(found[:max(room, 0)])
            room -= len(shown)
            rest = len(found) - len(shown)
            words = ", ".join(shown) + (f" and {rest} more" if rest else "")
            parts.append(f"{name} {words}" if shown else f"{rest} {name}")
    return " and ".join(parts)


def _whole(value: str) -> bool:
    """Whether ``value`` names a whole source -- no dataset, no row or column of one
    (``t:stock``, ``d2/t:stock``): what a dataset may stand in for."""
    match = QUALIFIED.match(value)
    body = match.group(2) if match else value
    return not body.startswith(DATASET_PREFIX) and "/" not in body


def own(value: str, session: Any, primary: str | None) -> tuple[str, str, str] | None:
    """``(doc, address, what)`` when ``value`` names a thing of a document that gives no
    rows -- the call's own document's table (bare, or with its handle), or another's named
    with its handle -- by that format's ``own_source``; ``None`` otherwise.  A value named
    with another handle whose document has no such thing is looked up in the call's own
    document too: the dispatcher qualifies a bare ``t:`` with the one document taking
    ``t:`` addresses before the handler reads it, so ``t:458D9849`` reaches a resolver as
    ``d2/t:458D9849``."""
    match = QUALIFIED.match(value)
    handle, address = (match.group(1), match.group(2)) if match else (primary, value)
    for doc in dict.fromkeys(h for h in (handle, primary) if h is not None):
        entry = session.documents.get(doc)
        hook = getattr(session.formats.get(entry.kind), "own_source", None) \
            if entry is not None else None
        if hook is None:
            continue
        with reading(entry) as document:
            if document is None:
                continue
            try:
                what = hook(document, address)
            except Exception:  # noqa: BLE001 -- a hook only adds words
                logger.exception("own_source of %s failed", entry.kind)
                continue
        if isinstance(what, str) and what.strip():
            return doc, address, what.strip()
    return None


def refusal(error: ToolError, value: Any, session: Any, *, field: str | None = None,
            primary: str | None = None, datasets: Iterable[str] = (), doc: str | None = None,
            call: Callable[[str], Mapping[str, Any] | None] | None = None,
            hint: str | None = None, fact: bool = False) -> ToolError:
    """A source refusal that offers only what resolves (round 56, R120).

    ``error`` is what resolving ``value`` raised (the qualifying, or the resolver of the
    document ``doc``); ``primary`` the call's document; ``datasets`` the ids an earlier op of
    the batch defined; ``call(address)`` the call that would work with ``address`` in
    ``value``'s place (``None`` when it cannot be written); ``hint`` words the call's format
    adds to a ``not_found`` (:attr:`DocumentFormat.source_hint`); ``fact``: ``value`` is a
    reference's address in a text (``{{t:stock/#1/count}}``), which names one value.

    * ``value`` names a thing of a document that gives no rows (:func:`own`, the call's own
      document's table): refused as ``invalid_arguments`` saying so, what a source is
      (:func:`shapes`), the datasets and tables there are (:func:`offered`) as the options,
      an example call; with one source alone, ``details.call`` is the call with it.
    * Otherwise the error stands, its source options kept only when they resolve
      (:func:`resolving`; a bare one written with its handle where it must be); when none
      is left, the sources there are are offered in their place -- and a ``not_found``
      offers the datasets beside the tables.

    Other codes (``limit``) are returned as they are."""
    if error.code not in _REFINED or not isinstance(value, str):
        return error
    where = error.field or field
    ids = list(datasets)
    names, tables = offered(session, ids)
    every = [*names, *tables]
    shown = every[:MAX_OFFERED]
    details = dict(error.details)
    found = own(value, session, primary)
    if found is not None:
        handle, address, what = found
        kind = session.documents[handle].kind
        whose = (f"{handle}, the document this call edits" if handle == primary
                 else f"{handle} ({kind})")
        details = {}
        if fact:
            message = (f"{address} is {what} of {whose}, not a value to quote: a reference in "
                       f"{where or 'a text'} names one value of {shapes(session.formats)}, "
                       "as {{<source>/#<row>/<column>}}")
            message += (f"; there are {_listing(names, tables)}" if every else
                        "; there is none: define a dataset or open a document with tables first")
            details["names"] = {"doc": handle, "address": address, "is": what}
            return ToolError("invalid_arguments", message, field=where, valid_options=shown,
                             details=details)
        message = (f"{address} is {what} of {whose}, not rows to take: "
                   f"{where or 'a source'} takes {shapes(session.formats)}")
        if every:
            message += f"; there are {_listing(names, tables)}"
            message += f"; e.g. {where or 'from'}: {json.dumps(shown[0])}"
        else:
            message += ("; there is none: define a dataset or open a document with tables "
                        "first")
        details["names"] = {"doc": handle, "address": address, "is": what}
        if len(every) == 1 and call is not None:
            meant = call(every[0])
            if meant is not None:
                details["call"] = dict(meant)
        return ToolError("invalid_arguments", message, field=where, valid_options=shown,
                         details=details)
    options: list[Any] = []
    dropped = False
    for option in error.valid_options:
        if not source_like(option, session.formats):
            options.append(option)
            continue
        kept = resolving(option, session, ids, doc=doc)
        if kept is None:
            dropped = True
        else:
            options.append(kept)
    message = error.message + (f"; {hint}" if hint and error.code == "not_found" else "")
    sourced = [o for o in options if source_like(o, session.formats)]
    if dropped and not sourced and every:
        message += f"; the sources there are: {_listing(names, tables)}"
        options += shown
    elif error.code == "not_found" and names and not fact and _whole(value) \
            and not any(isinstance(o, str) and o.startswith(DATASET_PREFIX) for o in options):
        extra = names[:max(MAX_OFFERED - len(options), 1)]
        rest = len(names) - len(extra)
        message += f"; or a dataset: {', '.join(extra)}" + (f" and {rest} more" if rest else "")
        options += extra
    options = [o for i, o in enumerate(options)
               if not isinstance(o, str) or o not in options[:i]]
    sourced = [o for o in options if source_like(o, session.formats)]
    if len(every) == 1 and sourced == every and call is not None:
        meant = call(every[0])
        if meant is not None:
            details["call"] = dict(meant)
    return ToolError(error.code, message, field=where, valid_options=options, details=details)


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


#: The column type labels whose ISO 8601 text :func:`label` writes as a date or a time.
MOMENT_TYPES = ("date", "datetime", "time")

_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})"
                  r"(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?)?$")
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?$")
_LOCAL = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?: (.+))?$")


def label(value: Any, locale: Any = None, *, type: str | None = None,
          date_format: str | None = None) -> str | None:
    """A value as a category label or a cell's text: text as it is; a number as a cell in
    the General format writes it, with the locale's decimal separator (``1.5`` is ``1,5``
    in nl-NL; ``2024`` stays ``2024``, no thousands separator); a logical value as ``TRUE``
    or ``FALSE``; a Python ``date``, ``datetime`` or ``time`` as :func:`moment_text` writes
    it, in the locale's day order.  ``None`` for a blank, or anything else.

    ``type`` is the value's column type label (:meth:`Rows.label` passes it): in a
    ``date``, ``datetime`` or ``time`` column, ISO 8601 text (``2026-03-01``,
    ``2026-03-01T14:05:00``, ``14:05:00.250`` -- how a format library gives the dates it
    reads, and those derived from them) is written as the Python value it names is, so one
    document never mixes ``1/3/2026`` and ``2026-01-20``; text that is not ISO 8601 (an
    elapsed time, ``PT25H30M0S``) is written as it is.  Unlabelled ISO text stays text.

    ``date_format`` (a pattern :func:`date_pattern` has checked) writes a date -- a Python
    ``date`` or ``datetime`` (its date), or a ``date`` or ``datetime`` column's ISO text --
    as :func:`ooxml_edit.dates.format_date` does: ``2 March 2026`` in ``d mmmm yyyy``.
    Anything else is written as without it."""
    if date_format is not None:
        day = date_value(value, type)
        if day is not None:
            return dates.format_date(day, date_format, locale)
    if isinstance(value, str):
        if type in MOMENT_TYPES:
            found = moment_of(value)
            if found is not None:
                return moment_text(found, locale)
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


def date_value(value: Any, type: str | None = None) -> "_dt.date | None":
    """``value`` when it is a date, for a date pattern: a Python ``date`` or ``datetime``,
    or the ISO 8601 text of a ``date`` or ``datetime`` column (``type``); ``None`` for a
    time of day, unlabelled text and anything else."""
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str) and type in ("date", "datetime"):
        found = moment_of(value)
        return found if isinstance(found, _dt.date) else None
    return None


def date_pattern(pattern: Any, locale: Any = None, *, field: str = "date_format") -> str:
    """``pattern`` checked as a date pattern (:mod:`ooxml_edit.dates`) for ``locale``: a
    pattern that is none, or a name token where the locale's names are not measured, is
    refused as ``invalid_arguments`` naming the tokens."""
    try:
        dates.parse(pattern)
        dates.format_date(_dt.date(2026, 3, 2), pattern, locale)
    except dates.PatternError as error:
        raise ToolError("invalid_arguments", str(error), field=field,
                        valid_options=list(dates.TOKENS)) from None
    return pattern


def no_dates(rows: "Rows", columns: Sequence[int], field: str = "date_format") -> ToolError:
    """The refusal of a ``date_format`` for ``columns`` of ``rows`` that hold no date."""
    names = ", ".join(repr(rows.dataset.columns[column]) for column in columns[:5])
    return ToolError("invalid_arguments", f"date_format is for dates, and no value of "
                     f"{rows.address}'s column{'s' if len(columns) > 1 else ''} {names} is "
                     "one (a Python date, or a column labelled date or datetime); leave "
                     "date_format out", field=field)


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


def moment_of(value: Any, locale: Any = None, *,
              written: bool = False) -> "_dt.date | _dt.time | None":
    """The date, datetime or time ``value`` is or names: a Python one as it is; ISO 8601
    text (``2026-03-01``, ``2026-03-01T14:05:00`` or with a space, ``14:05``, seconds and
    their fraction optional); with ``written``, also text as :func:`moment_text` writes it
    in ``locale`` (``1/3/2026`` in ``dmy``, ``1/3/2026 14:05``).  ``None`` for anything
    else -- a day that does not exist, a time zone, another separator.  A chart's
    categories are matched to a date column's rows by it, whichever way they were
    written."""
    if isinstance(value, (_dt.date, _dt.time)):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        match = _ISO.match(text)
        if match:
            day = _dt.date(*(int(part) for part in match.groups()[:3]))
            if match.group(4) is None:
                return day
            return _dt.datetime.combine(day, _clock(match.groups()[3:]))
        match = _CLOCK.match(text)
        if match:
            return _clock(match.groups())
        order = getattr(locale, "date_order", None) or "mdy"
        match = _LOCAL.match(text) if written and order in ("dmy", "mdy") else None
        if match:
            parts = dict(zip(order, (int(part) for part in match.groups()[:3])))
            day = _dt.date(parts["y"], parts["m"], parts["d"])
            if match.group(4) is None:
                return day
            clock = _CLOCK.match(match.group(4))
            return _dt.datetime.combine(day, _clock(clock.groups())) if clock else None
    except ValueError:
        return None
    return None


def _clock(parts: Sequence[str | None]) -> _dt.time:
    """A time of day of regex groups: hour, minute, second, fraction (the last two may be
    ``None``); ``ValueError`` when there is no such time."""
    hour, minute, second, fraction = parts
    assert hour is not None and minute is not None  # the patterns' required groups
    micro = int((fraction or "0").ljust(6, "0"))
    return _dt.time(int(hour), int(minute), int(second or 0), micro)


__all__ = ["MOMENT_TYPES", "QUALIFIED", "Rows", "blank_text", "date_pattern", "date_value", "first_rows", "label",
           "merged", "moment_of", "moment_text", "no_dates",
           "number", "prefixes", "qualify", "resolve", "runs", "what"]
