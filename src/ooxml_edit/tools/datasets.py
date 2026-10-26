"""Datasets: tables of values a call derived, kept in the session under ``d:<id>``.

A format library that computes figures from a document -- counts, sums, groups -- hands
them to the session as a :class:`Dataset` (``call.define_dataset("by_region", dataset)``),
and any later call, on any document of any format, may name it as ``d:by_region`` in an
argument its tool declares (``Tool.datasets``, as ``Tool.refs`` declares ref targets).  The
dispatcher checks the id before the handler runs -- an unknown one is ``not_found`` with
the known ids as ``valid_options`` -- and the handler reads it with ``call.dataset(...)``.
No number is retyped between documents.

**What a dataset is.**  Columns, rows and, beside every value, its sources: whatever the
producing library says the value came from (address strings, say).  The tool layer stores
and hands them on, and never reads them.  ``keys`` says how many leading columns name a
row; ``types`` is a label per column (:data:`TYPE_LABELS`, the ones the format libraries
read; ``None`` for none); ``detail`` is anything else the producer keeps for itself (its
own result object), which no other library should read.

**Made in Python.**  :meth:`Dataset.from_records` makes one of a list of mappings (or of
tuples, with ``columns``) and labels its columns: ``types={"when": "date"}``.  A Python
``date``, ``datetime`` or ``time`` is a value as it is: a tool that places it writes it in
the session's locale (:func:`.sources.label`), and a format library reads it as a date
without a label.  ISO 8601 text (``"2026-03-01"``) stays text until its column is
labelled ``date`` (or ``datetime``, ``time``): a column of codes that happen to look like
dates is not read as dates on its own.  A labelled column's ISO text is placed as the
date it names is (:meth:`.sources.Rows.label`).

**Its source.**  A dataset records the document it was derived from and that document's
version (:attr:`DocumentEntry.version`) after the call that defined it.  It outlives
``close_document``.  It is never refreshed: when its source changes, it is marked
:attr:`~DatasetEntry.stale`, and a call that names it gets a warning saying so.

* *Stale* means the source is not in the state the dataset was derived from.  While the
  source is open that is a comparison of versions -- and a history gives equal versions to
  equal content only, so **undoing the change that made a dataset stale makes it current
  again**, and redoing it makes it stale again.  A scoped undo leaves the document in a state
  that never existed before (a new version): stale.  A document without a history counts its
  changes upwards, so there a dataset, once stale, stays stale.
* When the source is closed, the dataset keeps what it was at that moment, for good (the
  document can no longer change, or be undone).
* A dataset derived in a ``batch`` whose later op changed the same source is stale from the
  start: the state it came from had no version of its own.
* A dataset derived from another dataset (``source=`` an entry) inherits that dataset's
  source and version: it goes stale when its origin does.
* A dataset whose values were computed from **invalidated formula caches** is stale from
  the start, whatever the versions say (:attr:`Dataset.cache`): the cells it read hold
  values an edit (or the file itself) has asked the application to recalculate, and no
  library here calculates.  Undo cannot make it current: the state it was derived from
  never had current values.

**Formula caches** (:attr:`Dataset.cache`).  A producer whose values come from formula
cells -- a document's cached results -- says how many of the cells it read had which
:data:`CACHE_STATUSES`: ``cached_unverified`` (as last saved, nothing known to have
changed them, never checked to be current), ``invalidated`` (an input changed since, or
the file asks for a full recalculation) or ``absent`` (no cached value), and how many
refer to another file (``external``: they can be stale without any local change).  The
tool layer carries the counts where it says where values came from
(:meth:`Rows.describe <.sources.Rows.describe>`) and reads only ``invalidated``.

Datasets are session state, not document content: undo never removes one, defining one is
not an undo step, and a reading call may define one.  A call that fails defines none; a
batch defines its ops' datasets only if every op succeeds.

**A run of its rows.**  ``d:by_region/#9:#16`` names rows 9 to 16 (from 1, the last
included), ``d:by_region/#9:`` row 9 to the last, ``d:by_region/#9`` one row: what a tool
that takes a source places (:meth:`Call.source <.dispatch.Call.source>`), each value with
its own sources, as the format libraries' ``t:`` addresses write a row by position.  A
range given the other way round (``#16:#9``) is the same rows; a row past the last is
refused, never cut (:func:`row_range`, :meth:`Dataset.row_slice`).

**Limits.**  ``Limits.max_datasets`` per session (defining an id that exists replaces it,
which needs no room) and ``Limits.max_dataset_cells`` (rows times columns) per dataset;
sources are not counted.

**Workers.**  Datasets live in the session.  Nothing sends them to worker processes; a
handler that needs one there passes the :class:`Dataset` itself, which pickles when its
``detail`` does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from .results import ToolError
from ..errors import ArgumentError

#: What every dataset address starts with: ``d:by_region``.  A document handle (``d1``) has
#: no colon, so the two never meet.
DATASET_PREFIX = "d:"

#: A dataset's id: a letter or ``_`` first, then letters, digits, ``_``, ``.`` and ``-``;
#: at most 64 characters.  Case counts.
DATASET_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]{0,63}$")

#: What may follow a dataset's id to name a run of its rows: ``/#9:#16``, ``/#9:``, ``/#9``.
_ROW_RANGE = re.compile(r"^/#([1-9][0-9]{0,6})(?:(:)(?:#([1-9][0-9]{0,6}))?)?$")


#: The column type labels a dataset may carry -- what the format libraries read: ``number``, ``percent`` (a fraction, 0.25 for 25%),
#: ``date``, ``time`` and ``datetime`` (a Python ``date``/``time``/``datetime``, or ISO
#: 8601 text: ``2026-03-01``, ``14:05:00``, ``2026-03-01T14:05:00``), ``text``, ``bool``
#: and ``error`` (an error code as text, ``#N/A``).  A label says how a column's values
#: are read where a value alone does not say it (ISO text as a date, a number as a
#: percent); a value the label cannot be keeps its own type.
TYPE_LABELS = ("number", "percent", "date", "time", "datetime", "text", "bool", "error")

#: A formula's cached value, as a producer reads it: ``cached_unverified`` (as last saved,
#: no known invalidation -- never checked to be current), ``invalidated`` (an input changed
#: since, or the file asks for a recalculation) or ``absent`` (no cached value at all).
CACHE_STATUSES = ("cached_unverified", "invalidated", "absent")

#: What :attr:`Dataset.cache` counts beside the statuses: formulas that refer to another
#: file, whose cached values can be stale without any local change.
CACHE_EXTERNAL = "external"


def dataset_id(address: str) -> str | None:
    """The id an address names: ``by_region`` of ``d:by_region``, ``d:by_region[total]`` or
    ``d:by_region/North/total`` (what follows the id is the format's to read); ``None``
    when ``address`` is not a ``d:`` address."""
    if not isinstance(address, str) or not address.startswith(DATASET_PREFIX):
        return None
    body = address[len(DATASET_PREFIX):]
    ends = [i for i in (body.find("["), body.find("/")) if i >= 0]
    return body[:min(ends)] if ends else body


def row_range(address: str) -> tuple[str, int, int | None] | None:
    """The id and the rows a run of a dataset's rows names: ``("by_region", 9, 16)`` of
    ``d:by_region/#9:#16`` (from 1, the last included; given the other way round, the same),
    ``("by_region", 9, None)`` of ``d:by_region/#9:`` (to the last row), ``("by_region",
    9, 9)`` of ``d:by_region/#9``.  ``None`` for anything else -- a whole dataset, a
    column, a row by key."""
    id = dataset_id(address)
    if id is None:
        return None
    match = _ROW_RANGE.match(address[len(DATASET_PREFIX) + len(id):])
    if match is None:
        return None
    first = int(match.group(1))
    if match.group(2) is None:
        return id, first, first
    if match.group(3) is None:
        return id, first, None
    last = int(match.group(3))
    return id, min(first, last), max(first, last)


def rows_text(first: int, last: int | None) -> str:
    """A run of rows as an address writes it after an id: ``/#9:#16``, ``/#9:``, ``/#9``."""
    if last is None:
        return f"/#{first}:"
    return f"/#{first}" if first == last else f"/#{first}:#{last}"


def check_dataset_id(id: str, *, field: str | None = "id") -> str:
    """``id`` without a ``d:`` (given or not), or ``invalid_arguments``."""
    if isinstance(id, str) and id.startswith(DATASET_PREFIX):
        id = id[len(DATASET_PREFIX):]
    if not isinstance(id, str) or not DATASET_ID.match(id):
        raise ToolError("invalid_arguments", f"a dataset id must match {DATASET_ID.pattern}",
                        field=field)
    return id


@dataclass(frozen=True)
class Dataset:
    """Columns, rows and the sources of each value: format-neutral, immutable.

    ``rows`` are tuples in column order (a mapping per row is taken too, read by column
    name); a value is ``None``, a bool, a number, a string or a Python ``date``,
    ``datetime`` or ``time`` -- the producer's own types, which the tool layer reads only
    to write them as text (:func:`.sources.label`).  ``sources``, when given, has a tuple per row with a
    tuple per column of opaque strings: where the value came from.  ``keys``: how many
    leading columns name a row.  ``types``: a label per column (``None`` for unlabelled;
    :data:`TYPE_LABELS` are the ones the format libraries read).  ``detail``: anything else
    the producer keeps; not compared, not read here.  ``cache``: how many of the formula
    cells the values were read from have each of :data:`CACHE_STATUSES`, and how many refer
    to another file (:data:`CACHE_EXTERNAL`), ``None`` when none was a formula (see the
    module's notes); not compared.  :meth:`from_records` makes one of records, as Python
    code has them.
    """

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    sources: tuple[tuple[tuple[str, ...], ...], ...] | None = None
    keys: int = 0
    types: tuple[str | None, ...] | None = None
    detail: Any = field(default=None, compare=False, repr=False)
    cache: Mapping[str, int] | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        columns = tuple(self.columns)
        if not columns or not all(isinstance(name, str) and name for name in columns):
            raise ArgumentError("a dataset has at least one column, each named by a string")
        if len(set(columns)) != len(columns):
            raise ArgumentError("a dataset's column names are unique")
        rows = tuple(_row(row, columns) for row in self.rows)
        object.__setattr__(self, "columns", columns)
        object.__setattr__(self, "rows", rows)
        if self.sources is not None:
            sources = tuple(tuple(tuple(_strings(cell)) for cell in row) for row in self.sources)
            if len(sources) != len(rows) or any(len(row) != len(columns) for row in sources):
                raise ArgumentError("a dataset's sources have a row per row and a column per "
                                    "column")
            object.__setattr__(self, "sources", sources)
        if not isinstance(self.keys, int) or not 0 <= self.keys <= len(columns):
            raise ArgumentError(f"keys is 0 to {len(columns)} (the leading columns)")
        if self.types is not None:
            types = tuple(self.types)
            if len(types) != len(columns):
                raise ArgumentError("a dataset has a type label per column")
            object.__setattr__(self, "types", types)
        object.__setattr__(self, "cache", _cache(self.cache))

    @property
    def invalidated(self) -> bool:
        """Whether any value was read from a formula whose cached value is invalidated."""
        return bool(self.cache and self.cache.get("invalidated", 0) > 0)

    @classmethod
    def from_records(cls, rows: Iterable[Any], types: Any = None,
                     columns: Sequence[str] | None = None, *, keys: int = 0) -> "Dataset":
        """A dataset of records, as Python code has them::

            Dataset.from_records(
                [{"region": "North", "opened": date(2026, 3, 1), "total": 3.68},
                 {"region": "South", "opened": "2026-04-15", "total": 0.13}],
                types={"opened": "date"})

        ``rows``: mappings, read by column name -- a name a row lacks is a blank (``None``)
        there -- or sequences in column order, which need ``columns``.  ``columns``: the
        columns and their order; without it, every name the mappings use, in the order
        first met.  A name in ``columns`` that no mapping has is refused (a misspelling
        would otherwise be a blank column); a mapping's other names are left out.

        ``types``: a label per column, as a mapping by name (the rest unlabelled) or a
        sequence in column order; ``None`` for none.  The labels are :data:`TYPE_LABELS`:
        ``number``, ``percent``, ``date``, ``time``, ``datetime``, ``text``, ``bool`` and
        ``error``.  A label is needed where a value alone does not say its type: ISO 8601
        text is text until its column is labelled ``date`` (``datetime``, ``time``), and a
        fraction is a number until labelled ``percent``.  A Python ``date``, ``datetime``
        or ``time`` needs none.  ``keys``: how many leading columns name a row.  The
        dataset has no sources: its values came from the caller.

        :class:`ValueError` for an unknown label, a label for a column there is not, rows
        of the wrong length, or sequences without ``columns``."""
        rows = list(rows)
        if columns is None:
            if any(not isinstance(row, Mapping) for row in rows):
                raise ArgumentError("rows that are not mappings need columns: "
                                    "Dataset.from_records(rows, columns=[...])")
            names: dict[str, None] = {}
            for row in rows:
                names.update(dict.fromkeys(row))
            columns = tuple(names)
            if not columns:
                raise ArgumentError("records without names have no columns: give columns")
        columns = tuple(columns)
        if not all(isinstance(name, str) and name for name in columns):
            raise ArgumentError("a dataset's columns are named by strings")
        mappings = [row for row in rows if isinstance(row, Mapping)]
        if mappings:
            unused = [name for name in columns if not any(name in row for row in mappings)]
            if unused:
                raise ArgumentError(f"no record has {unused[0]!r}; the records' names are "
                                    f"{sorted({str(n) for row in mappings for n in row})[:50]}")
        values = [tuple(row.get(name) for name in columns) if isinstance(row, Mapping)
                  else row for row in rows]
        return cls(columns, tuple(values), keys=keys, types=_labels(types, columns))

    @property
    def cells(self) -> int:
        """Rows times columns: what ``Limits.max_dataset_cells`` counts."""
        return len(self.rows) * len(self.columns)

    def index(self, column: str) -> int:
        """The position of ``column``; ``not_found`` with the columns."""
        try:
            return self.columns.index(column)
        except ValueError:
            raise ToolError("not_found", f"no column {column!r} in the dataset",
                            valid_options=list(self.columns)) from None

    def column(self, name: str) -> tuple[Any, ...]:
        """The values of one column, row by row."""
        at = self.index(name)
        return tuple(row[at] for row in self.rows)

    def records(self) -> list[dict[str, Any]]:
        """The rows as mappings by column name."""
        return [dict(zip(self.columns, row)) for row in self.rows]

    def row_slice(self, first: int, last: int | None = None) -> "Dataset":
        """Rows ``first`` to ``last`` (from 1, both included; ``None``: to the last row),
        with their sources, keys and types; no ``detail`` (the producer's, which describes
        every row).  :class:`ValueError` for a row past the last: nothing is cut.

        For example::

            dataset.row_slice(9, 16)        # rows 9 to 16, eight rows"""
        count = len(self.rows)
        end = count if last is None else last
        if not 1 <= first <= end:
            raise ArgumentError(f"rows run from 1 to the last, first to last: {first} to {end}")
        if end > count:
            raise ArgumentError(f"the dataset has {count} rows; there is no row {end}")
        sources = None if self.sources is None else self.sources[first - 1:end]
        return Dataset(self.columns, self.rows[first - 1:end], sources=sources,
                       keys=self.keys, types=self.types, cache=self.cache)


def _cache(counts: Any) -> dict[str, int] | None:
    """:attr:`Dataset.cache` checked: counts by status (and ``external``), zeros left out,
    in :data:`CACHE_STATUSES` order; ``None`` for none."""
    if counts is None:
        return None
    if not isinstance(counts, Mapping):
        raise ArgumentError("a dataset's cache is a mapping of status to count")
    known = (*CACHE_STATUSES, CACHE_EXTERNAL)
    unknown = [key for key in counts if key not in known]
    if unknown:
        raise ArgumentError(f"a dataset's cache counts {', '.join(known)}; not "
                            f"{', '.join(map(repr, unknown))}")
    out: dict[str, int] = {}
    for key in known:
        count = counts.get(key, 0)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ArgumentError(f"a dataset's cache count is a whole number from 0, not {count!r}")
        if count:
            out[key] = count
    return out or None


def _labels(types: Any, columns: tuple[str, ...]) -> tuple[str | None, ...] | None:
    """``types`` (a mapping by column name, or a sequence in column order) as a label per
    column, each one of :data:`TYPE_LABELS` or ``None``."""
    if types is None:
        return None
    if isinstance(types, str):
        raise ArgumentError("types is a mapping of column names to labels, or a label per "
                            "column: types={'opened': 'date'}")
    if isinstance(types, Mapping):
        unknown = [name for name in types if name not in columns]
        if unknown:
            raise ArgumentError(f"types names {unknown[0]!r}, which is no column; the columns "
                                f"are {list(columns)}")
        labels = tuple(types.get(name) for name in columns)
    else:
        labels = tuple(types)
        if len(labels) != len(columns):
            raise ArgumentError(f"{len(labels)} type labels for {len(columns)} columns: give one "
                                "per column (None for none), or a mapping by name")
    for label in labels:
        if label is not None and label not in TYPE_LABELS:
            raise ArgumentError(f"{label!r} is not a type label: one of {', '.join(TYPE_LABELS)}"
                                " (or None)")
    return labels


def _row(row: Any, columns: tuple[str, ...]) -> tuple[Any, ...]:
    if isinstance(row, Mapping):
        missing = [name for name in columns if name not in row]
        if missing:
            raise ArgumentError(f"a row has no {missing[0]!r}")
        return tuple(row[name] for name in columns)
    values = tuple(row)
    if len(values) != len(columns):
        raise ArgumentError(f"a row has {len(values)} values for {len(columns)} columns")
    return values


def _strings(cell: Any) -> Sequence[str]:
    values = (cell,) if isinstance(cell, str) else tuple(cell)
    if not all(isinstance(value, str) for value in values):
        raise ArgumentError("a value's sources are strings")
    return values


#: The most columns ``describe`` names per dataset (:meth:`DatasetEntry.brief`), and the
#: most datasets it lists, the latest defined (:func:`briefs`).
BRIEF_COLUMNS = 20
BRIEF_DATASETS = 20


def briefs(entries: Iterable["DatasetEntry"],
           stale: Callable[["DatasetEntry"], bool] | None = None) -> dict[str, Any]:
    """``describe``'s ``datasets`` (and ``more_datasets``, the earlier ones not listed) of
    ``entries`` in the order defined: the latest :data:`BRIEF_DATASETS`, each
    :meth:`DatasetEntry.brief` (``stale(entry)`` its staleness when given).  Empty when
    there are none."""
    entries = list(entries)
    if not entries:
        return {}
    data: dict[str, Any] = {"datasets": [
        e.brief(stale=None if stale is None else stale(e)) for e in entries[-BRIEF_DATASETS:]]}
    if len(entries) > BRIEF_DATASETS:
        data["more_datasets"] = len(entries) - BRIEF_DATASETS
    return data


class DatasetEntry:
    """A dataset in a session: the :class:`Dataset`, its id, and where it came from.

    ``doc`` is the source document's handle (``None``: no document), ``kind`` and ``name``
    its kind and name, ``version`` its version when the dataset was defined.  :attr:`stale`
    is computed when read (see the module's notes); :attr:`closed` says the source was
    closed, after which it no longer changes.  ``origin`` is the address in the source it
    was derived from (``t:sales``), or the dataset it was derived from (``d:by_region``);
    ``None`` when the producer did not say.  ``defined_at`` is the session clock's time of
    the call that defined it; ``None`` for one the application kept itself
    (:meth:`Session.keep_datasets <.session.Session.keep_datasets>`).
    """

    def __init__(self, id: str, dataset: Dataset, *, doc: str | None = None,
                 kind: str | None = None, name: str | None = None,
                 version: int | None = None, source: Any = None,
                 stale: bool = False, defined_at: Any = None,
                 origin: str | None = None) -> None:
        self.id = id
        self.origin = origin
        self.dataset = dataset
        self.doc = doc
        self.kind = kind
        self.name = name
        self.version = version
        self.defined_at = defined_at
        #: The source's :class:`~.session.DocumentEntry` while it is open, else ``None``.
        self._source = source
        #: Whether it is stale for good: closed while stale, or born stale.
        self._stale = stale

    @property
    def address(self) -> str:
        return DATASET_PREFIX + self.id

    @property
    def closed(self) -> bool:
        """Whether the source document was closed (a dataset without one never is)."""
        return self.doc is not None and self._source is None

    @property
    def stale(self) -> bool:
        """Whether the source has changed since -- now, not as of any earlier look -- or the
        values came from invalidated formula caches (:attr:`Dataset.invalidated`)."""
        if self._stale or self.dataset.invalidated:
            return True
        source = self._source
        return source is not None and source.version != self.version

    @property
    def source_version(self) -> int | None:
        """The source's version now; ``None`` once closed (or with no source)."""
        source = self._source
        return source.version if source is not None else None

    def source_closed(self) -> None:
        """The session closed the source: keep its staleness as it is now, for good."""
        if self._source is not None:
            self._stale = self.stale
            self._source = None

    def mark_stale(self) -> None:
        """Stale for good: the state it was derived from never had a version."""
        self._stale = True

    def derived(self, id: str, dataset: Dataset, *, defined_at: Any = None) -> "DatasetEntry":
        """A dataset derived from this one: the same source, version and fate; its origin
        this dataset."""
        return DatasetEntry(id, dataset, doc=self.doc, kind=self.kind, name=self.name,
                            version=self.version, source=self._source, stale=self._stale,
                            defined_at=defined_at, origin=self.address)

    def brief(self, *, stale: bool | None = None) -> dict[str, Any]:
        """What ``describe`` lists of it: id, columns (the first :data:`BRIEF_COLUMNS`, the
        rest counted in ``more_columns``), the type labels it has (``types``, by column),
        row count, and ``from``: ``d3/t:sales`` (a document's address), ``d:by_region``
        (a dataset), ``d3`` (a document, no address said) or ``application`` (kept by the
        application, not by a call).  ``stale`` and ``closed`` only when true.  Never its
        values.  ``stale``: as known to the caller (a dataset a running call defined has no
        version yet), else :attr:`stale`."""
        columns = list(self.dataset.columns)
        data: dict[str, Any] = {"id": self.address, "columns": columns[:BRIEF_COLUMNS]}
        if len(columns) > BRIEF_COLUMNS:
            data["more_columns"] = len(columns) - BRIEF_COLUMNS
        types = {name: label for name, label in zip(data["columns"], self.dataset.types or ())
                 if label is not None}
        if types:
            data["types"] = types
        data["rows"] = len(self.dataset.rows)
        origin = self.origin
        if origin is not None and self.doc is not None and not origin.startswith(
                DATASET_PREFIX):
            origin = f"{self.doc}/{origin}"
        if origin is None:
            origin = self.doc if self.doc is not None else (
                "application" if self.defined_at is None else None)
        if origin is not None:
            data["from"] = origin
        if self.stale if stale is None else stale:
            data["stale"] = True
        if self.closed:
            data["closed"] = True
        return data

    def describe(self) -> dict[str, Any]:
        """What a result says about it: never its values."""
        data: dict[str, Any] = {"id": self.address, "columns": list(self.dataset.columns),
                                "rows": len(self.dataset.rows), "doc": self.doc,
                                "version": self.version, "stale": self.stale}
        if self.closed:
            data["closed"] = True
        return data

    def __repr__(self) -> str:
        return (f"DatasetEntry({self.address}, doc={self.doc!r}, version={self.version!r}, "
                f"stale={self.stale})")
