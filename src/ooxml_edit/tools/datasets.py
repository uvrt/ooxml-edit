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

Datasets are session state, not document content: undo never removes one, defining one is
not an undo step, and a reading call may define one.  A call that fails defines none; a
batch defines its ops' datasets only if every op succeeds.

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
from typing import Any, Iterable, Mapping, Sequence

from .results import ToolError

#: What every dataset address starts with: ``d:by_region``.  A document handle (``d1``) has
#: no colon, so the two never meet.
DATASET_PREFIX = "d:"

#: A dataset's id: a letter or ``_`` first, then letters, digits, ``_``, ``.`` and ``-``;
#: at most 64 characters.  Case counts.
DATASET_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]{0,63}$")


#: The column type labels a dataset may carry -- what the format libraries read: ``number``, ``percent`` (a fraction, 0.25 for 25%),
#: ``date``, ``time`` and ``datetime`` (a Python ``date``/``time``/``datetime``, or ISO
#: 8601 text: ``2026-03-01``, ``14:05:00``, ``2026-03-01T14:05:00``), ``text``, ``bool``
#: and ``error`` (an error code as text, ``#N/A``).  A label says how a column's values
#: are read where a value alone does not say it (ISO text as a date, a number as a
#: percent); a value the label cannot be keeps its own type.
TYPE_LABELS = ("number", "percent", "date", "time", "datetime", "text", "bool", "error")


def dataset_id(address: str) -> str | None:
    """The id an address names: ``by_region`` of ``d:by_region``, ``d:by_region[total]`` or
    ``d:by_region/North/total`` (what follows the id is the format's to read); ``None``
    when ``address`` is not a ``d:`` address."""
    if not isinstance(address, str) or not address.startswith(DATASET_PREFIX):
        return None
    body = address[len(DATASET_PREFIX):]
    ends = [i for i in (body.find("["), body.find("/")) if i >= 0]
    return body[:min(ends)] if ends else body


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
    the producer keeps; not compared, not read here.  :meth:`from_records` makes one of
    records, as Python code has them.
    """

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    sources: tuple[tuple[tuple[str, ...], ...], ...] | None = None
    keys: int = 0
    types: tuple[str | None, ...] | None = None
    detail: Any = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        columns = tuple(self.columns)
        if not columns or not all(isinstance(name, str) and name for name in columns):
            raise ValueError("a dataset has at least one column, each named by a string")
        if len(set(columns)) != len(columns):
            raise ValueError("a dataset's column names are unique")
        rows = tuple(_row(row, columns) for row in self.rows)
        object.__setattr__(self, "columns", columns)
        object.__setattr__(self, "rows", rows)
        if self.sources is not None:
            sources = tuple(tuple(tuple(_strings(cell)) for cell in row) for row in self.sources)
            if len(sources) != len(rows) or any(len(row) != len(columns) for row in sources):
                raise ValueError("a dataset's sources have a row per row and a column per "
                                 "column")
            object.__setattr__(self, "sources", sources)
        if not isinstance(self.keys, int) or not 0 <= self.keys <= len(columns):
            raise ValueError(f"keys is 0 to {len(columns)} (the leading columns)")
        if self.types is not None:
            types = tuple(self.types)
            if len(types) != len(columns):
                raise ValueError("a dataset has a type label per column")
            object.__setattr__(self, "types", types)

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
                raise ValueError("rows that are not mappings need columns: "
                                 "Dataset.from_records(rows, columns=[...])")
            names: dict[str, None] = {}
            for row in rows:
                names.update(dict.fromkeys(row))
            columns = tuple(names)
            if not columns:
                raise ValueError("records without names have no columns: give columns")
        columns = tuple(columns)
        if not all(isinstance(name, str) and name for name in columns):
            raise ValueError("a dataset's columns are named by strings")
        mappings = [row for row in rows if isinstance(row, Mapping)]
        if mappings:
            unused = [name for name in columns if not any(name in row for row in mappings)]
            if unused:
                raise ValueError(f"no record has {unused[0]!r}; the records' names are "
                                 f"{sorted({str(n) for row in mappings for n in row})[:50]}")
        values = [tuple(row.get(name) for name in columns) if isinstance(row, Mapping)
                  else row for row in rows]
        return cls(columns, values, keys=keys, types=_labels(types, columns))

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


def _labels(types: Any, columns: tuple[str, ...]) -> tuple[str | None, ...] | None:
    """``types`` (a mapping by column name, or a sequence in column order) as a label per
    column, each one of :data:`TYPE_LABELS` or ``None``."""
    if types is None:
        return None
    if isinstance(types, str):
        raise ValueError("types is a mapping of column names to labels, or a label per "
                         "column: types={'opened': 'date'}")
    if isinstance(types, Mapping):
        unknown = [name for name in types if name not in columns]
        if unknown:
            raise ValueError(f"types names {unknown[0]!r}, which is no column; the columns "
                             f"are {list(columns)}")
        labels = tuple(types.get(name) for name in columns)
    else:
        labels = tuple(types)
        if len(labels) != len(columns):
            raise ValueError(f"{len(labels)} type labels for {len(columns)} columns: give one "
                             "per column (None for none), or a mapping by name")
    for label in labels:
        if label is not None and label not in TYPE_LABELS:
            raise ValueError(f"{label!r} is not a type label: one of {', '.join(TYPE_LABELS)}"
                             " (or None)")
    return labels


def _row(row: Any, columns: tuple[str, ...]) -> tuple[Any, ...]:
    if isinstance(row, Mapping):
        missing = [name for name in columns if name not in row]
        if missing:
            raise ValueError(f"a row has no {missing[0]!r}")
        return tuple(row[name] for name in columns)
    values = tuple(row)
    if len(values) != len(columns):
        raise ValueError(f"a row has {len(values)} values for {len(columns)} columns")
    return values


def _strings(cell: Any) -> Sequence[str]:
    values = (cell,) if isinstance(cell, str) else tuple(cell)
    if not all(isinstance(value, str) for value in values):
        raise ValueError("a value's sources are strings")
    return values


class DatasetEntry:
    """A dataset in a session: the :class:`Dataset`, its id, and where it came from.

    ``doc`` is the source document's handle (``None``: no document), ``kind`` and ``name``
    its kind and name, ``version`` its version when the dataset was defined.  :attr:`stale`
    is computed when read (see the module's notes); :attr:`closed` says the source was
    closed, after which it no longer changes.
    """

    def __init__(self, id: str, dataset: Dataset, *, doc: str | None = None,
                 kind: str | None = None, name: str | None = None,
                 version: int | None = None, source: Any = None,
                 stale: bool = False, defined_at: Any = None) -> None:
        self.id = id
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
        """Whether the source has changed since: now, not as of any earlier look."""
        if self._stale:
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
        """A dataset derived from this one: the same source, version and fate."""
        return DatasetEntry(id, dataset, doc=self.doc, kind=self.kind, name=self.name,
                            version=self.version, source=self._source, stale=self._stale,
                            defined_at=defined_at)

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
