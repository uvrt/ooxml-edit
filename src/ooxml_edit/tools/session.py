"""A session: the documents, inputs and outputs of one conversation, all in memory.

The application opens documents from bytes (:meth:`Session.open` → ``"d1"``) and registers
every other input as a blob (:meth:`Session.add_blob` → ``"b1"``).  Tools refer to both by
those handles only; nothing in a session is a path.  What a tool saves goes to
:attr:`Session.outputs` (or the ``on_output`` callback) as bytes for the application; the
model sees its name and size, never the bytes.

Concurrency.  A document is an lxml tree mutated in place, so every call on it holds its
entry's re-entrant lock.  Calls on different documents run concurrently.  A call on several
documents takes their locks in :func:`doc_order`, so two such calls cannot deadlock.  The
session's own maps have a lock of their own, never held while a document lock is awaited.

Fonts.  :attr:`Session.font_dirs` is the application's own font folders, for every render
and every measurement of text the session's documents make: application configuration,
never a tool argument.  ``None`` (the default) leaves it to the renderers, which read
``OOXML_FONT_DIRS`` and the operating system's folders.  A format applies it to each
document that joins the session (:attr:`DocumentFormat.configure`), and passes it on to
the work it runs in a worker process.

Datasets.  :attr:`Session.datasets` holds the tables of values calls derived, by id
(``d:by_region``): session state, not any document's, so they outlive the document they
came from and are marked stale, never refreshed, when it changes (:mod:`.datasets`).

Locale.  :attr:`Session.locale` is the regional settings the session's documents are drawn
and edited in -- decimal and thousands separators, date order, language, list separator --
for every render and every measurement, as :attr:`~Session.font_dirs` is: application
configuration (per user or per client), never a tool argument, and never read from the
operating system.  The session keeps the object it was given -- an
:class:`ooxml_common.locales.Locale`, which the application imports -- and checks only that
it has a locale's attributes (:func:`locale_of`): this package imports nothing of
ooxml-common, so it stays lxml-only.  Not given, it is :data:`DEFAULT_LOCALE`, en-US with
:data:`ooxml_common.locales.EN_US`'s values.

Views.  Several agents may work in one session at once -- the same documents, undo
history, refs, datasets and outputs -- each with roles of its own: :meth:`Session.view`
gives one a :class:`SessionView`, the session with narrower roles and the application's
``context`` for its calls (``{"agent": "reviewer-1"}``), which a handler reads as
:attr:`~.dispatch.Call.caller`.  A view holds no state of its own beyond those two: every
other attribute and method is the session's, so its locks and maps are shared, and a view
goes wherever a session goes (``Toolbox.dispatch``, ``dispatch_many``).  A view narrows,
never widens: its roles are some of its session's.

Undo by caller (round 9).  Each document entry keeps who made each step of its history
(:attr:`DocumentEntry.owners`: the call's context and tool, by the version it made).  An
undo whose call has a context -- through a view, or ``dispatch(..., context=...)`` --
takes back that caller's own latest change to the document, never another's: plainly when
it is the document's latest change; else, in a format with undo scopes (pages whose parts
are their own), taken out from under the later changes as a scoped undo is, when none
of them touched a part it touched; otherwise it is refused as ``entangled``, naming the
later change that depends on it and who made it.  Two callers are the same when their
contexts are equal.  An undo with no context (the session itself, or a view made without
one) is as before: the latest change, whoever made it.  See :meth:`Session.undo`.
"""

from __future__ import annotations

import codecs
import datetime as _dt
import itertools
import re
import threading
import uuid
from collections import OrderedDict
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from os import PathLike, fspath
from types import MappingProxyType
from typing import Any, Callable, Hashable, Iterable, Iterator, Mapping, Sequence

from .datasets import DATASET_PREFIX, DatasetEntry, dataset_id
from .limits import LimitError, Limits, check_declared_type, check_image, check_package
from .logs import CallRecord
from .registry import role_set
from .results import ToolError

Clock = Callable[[], _dt.datetime]


def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


@dataclass(frozen=True)
class DocumentFormat:
    """What a format library tells the tool layer about its documents.

    The tool layer knows no format; a library describes its own with this, and the toolbox
    opens, checks and validates documents of that kind through it.
    """

    #: The kind tools are written for (``Tool.kinds``) and results report.
    kind: str
    #: Opens bytes as a document.  Must accept bytes; the tool layer never passes a path.
    open: Callable[[bytes], Any]
    #: Whether bytes (with their name) are a document of this kind.
    detect: Callable[[bytes, str], bool]
    #: The document's validation problems, compared to the baseline taken at open.
    problems: Callable[[Any], Sequence[Any]] | None = None
    #: How a problem is compared with the baseline; ``str`` by default.
    problem_key: Callable[[Any], Hashable] = str
    #: Warning categories the library raises; collected into results, never printed.
    warnings: tuple[type[Warning], ...] = ()
    #: The format's system-prompt fragment, appended after the shared one.
    prompt: str = ""
    #: Exceptions the library raises, mapped to error codes: ``{type: code}`` or
    #: ``{type: (code, options)}`` where ``options(exception)`` lists the valid options.
    errors: dict[type[BaseException], Any] = field(default_factory=dict)
    #: A short summary of an open document (its page count and title), returned by the
    #: shared ``open_document`` tool.
    summary: Callable[[Any], Any] | None = None
    #: The facts every changing call returns: ``checks(entry, touched)`` -> the result's
    #: ``checks``, where ``touched`` lists what the call's handlers named with
    #: :meth:`~.dispatch.Call.touch` (pages, blocks), in order.  Run once per call, and once
    #: per document at the end of a ``batch``.
    checks: Callable[[Any, Sequence[Any]], dict[str, Any]] | None = None
    #: The format's tools in the order they should get ``strict: true`` on Claude, whose
    #: per-request limits (20 tools, 24 optional parameters) allow only some: its most-used
    #: writing tools first.  Tools not named follow, writing tools first.
    strict_first: tuple[str, ...] = ()
    #: Undo scopes, for ``undo``'s ``scope``: ``undo_scope(document, scope)`` -> the part
    #: names the scope owns (a page: its part and what it alone relates to).
    #: Raise :class:`~.results.ToolError` ``not_found`` for an unknown scope.  ``None``: the
    #: format undoes document-wide only (as a format whose one body part every edit
    #: shares should).  The document's history must have ``undo_in`` and ``redo_in``.
    undo_scope: Callable[[Any, str], Iterable[str]] | None = None
    #: Parts every undo scope depends on at once -- templates every page is drawn from:
    #: ``undo_shared(document)`` -> part names.  A scoped undo whose latest step changed one
    #: is refused as ``entangled`` (it is no one scope's to take back).  ``None``: none.
    #: A scope that should find such a step lists the parts in ``undo_scope`` too.
    undo_shared: Callable[[Any], Iterable[str]] | None = None
    #: Called after a scoped undo or redo restored parts behind the document's back, so it
    #: drops what it caches (what its own ``undo`` does after the history's) -- and after a
    #: ``batch`` rolled one failed op back inside its open undo step
    #: (:meth:`DocumentEntry.savepoint`).
    restored: Callable[[Any], None] | None = None
    #: Called with ``(document, session)`` when a document joins a session -- opened,
    #: created or adopted -- before anything is measured: the format applies the
    #: session's settings to it (:attr:`Session.font_dirs`, :attr:`Session.locale`).
    configure: Callable[[Any, "Session"], None] | None = None
    #: The addresses of this format's documents that other documents' tools may take rows
    #: from (a chart's data from a table, :mod:`.sources`): ``{prefix: resolve}``, where
    #: ``resolve(document, address)`` returns a :class:`~.datasets.Dataset` -- columns,
    #: rows, and each value's sources as the format's own address strings -- or raises
    #: :class:`~.results.ToolError` (``not_found`` with the names there are).  Never ``d:``.
    datasets: dict[str, Callable[[Any, str], Any]] = field(default_factory=dict)
    #: Writes a run of this format's source addresses compactly for a result -- neighbours
    #: as one range -- in the order given: ``merge_sources(addresses) -> addresses``.
    #: ``None``: they are listed as they are, each once.
    merge_sources: Callable[[Sequence[str]], Sequence[str]] | None = None
    #: A word on a source value that names nothing -- a ``d:`` that is no dataset, a
    #: ``t:`` no open document takes -- when this format's document is the call's
    #: (its first document argument): ``source_hint(document, value) -> str | None``,
    #: appended to the ``not_found`` message (say, that ``d:3`` is one of the format's
    #: own drawings, not a dataset).  It only adds wording: what resolves never changes,
    #: and a hint that raises is ignored.  ``None``: the message is left as it is.
    source_hint: Callable[[Any, str], str | None] | None = None
    #: References a text argument may hold to things of this format's own document -- the
    #: call's (:mod:`.textfacts`): ``{prefix: resolve}``, ``resolve(document, name) ->
    #: (text, fields)`` for ``{{<prefix><name>}}``, ``name`` with a ``$ref`` already
    #: replaced by its address, ``fields`` what the kept fact adds.  Raise
    #: :class:`~.results.ToolError` for a name that is not there.  Keeping the text current
    #: is the format's.  Another format's document refuses the prefix.  Never ``d:``.  A
    #: text instead of a function refuses the prefix in this format's documents in those
    #: words -- a prefix another format resolves, which this one wants to say no to even
    #: when no such format is loaded.
    own_facts: dict[str, Callable[[Any, str], tuple[str, Mapping[str, Any]]] | str] = \
        field(default_factory=dict)
    #: Keeps where values placed from a source came from, in the saved document:
    #: ``store_provenance(document, address, record)``, with ``address`` the table or chart
    #: the values went into and ``record`` an :mod:`ooxml_edit.provenance` record (a dict:
    #: write it with :func:`ooxml_edit.provenance.dumps`), or ``None``: drop the address'
    #: record.  It replaces the record the address had.  Called inside the call's undo step, after the placement succeeded:
    #: by the shared ``edit_chart`` for a chart from ``from``, and by a tool's handler
    #: through :meth:`~.dispatch.Call.keep_provenance`.  ``None``: the format keeps none
    #: (a chart still keeps its own copy in its part).
    store_provenance: Callable[[Any, str, Any], None] | None = None
    #: The records the document keeps: ``read_provenance(document) -> {address: record}``,
    #: each as :func:`ooxml_edit.provenance.loads` reads it, under the addresses the
    #: format's tools take now.  :meth:`Session.provenance` asks it.  ``None``: none.
    read_provenance: Callable[[Any], Any] | None = None
    #: Tools the format offers only by the application's configuration -- an asset
    #: catalogue's search (:mod:`.assets`) -- so a toolbox without it sends no definition
    #: for them.  The toolbox adds each after its own tools; one named like a toolbox tool
    #: replaces it (the same tool with what the configuration adds: an ``asset`` argument),
    #: which must then serve this format's kind alone.
    tools: tuple[Any, ...] = ()
    #: Tool names a model writes for one of this format's tools, or an action of one, that
    #: the toolbox's own reading of names misses (:mod:`.hints`): ``{name: "tool"}`` or
    #: ``{name: "tool action"}``.  A key also stands for its spellings with another verb,
    #: a plural or another prefix.  Only wording: the call still fails, its message naming
    #: the tool to use.  An entry naming a tool or action the toolbox lacks is ignored.
    tool_hints: dict[str, str] = field(default_factory=dict)
    #: What a new worker process runs before its first task, so the first render is not
    #: the one to pay for the format's imports and font index: a module-level function of
    #: no arguments, registered (:func:`~.worker.worker_task`).  Its time is no call's
    #: (:mod:`.worker`).  ``None``: nothing.
    warm_up: Callable[[], Any] | None = None


#: :attr:`DocumentEntry.render_keys` and :attr:`DocumentEntry.font_keys`, in entries.
RENDER_KEYS = 4096
FONT_KEYS = 256


class LRU:
    """A small least-recently-used cache."""

    def __init__(self, size: int) -> None:
        self.size = size
        self._items: OrderedDict[Hashable, Any] = OrderedDict()

    def get(self, key: Hashable, default: Any = None) -> Any:
        if key not in self._items:
            return default
        self._items.move_to_end(key)
        return self._items[key]

    def put(self, key: Hashable, value: Any) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self.size:
            self._items.popitem(last=False)

    def __contains__(self, key: Hashable) -> bool:
        return key in self._items

    def keys(self) -> list[Hashable]:
        """The keys, least recently used first."""
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()


@dataclass
class Blob:
    """An input the application registered, under a handle (``b1``)."""

    handle: str
    name: str
    mime: str
    data: bytes = field(repr=False)

    @property
    def size(self) -> int:
        return len(self.data)

    def describe(self) -> dict[str, Any]:
        return {"handle": self.handle, "name": self.name, "mime": self.mime, "size": self.size}


@dataclass
class Output:
    """A file a tool produced for the application: its bytes never reach the model."""

    name: str
    format: str
    data: bytes = field(repr=False)
    doc: str | None = None
    validate: dict[str, Any] | None = None

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "format": self.format, "size": len(self.data),
                "doc": self.doc, "validate": self.validate}


@dataclass(frozen=True)
class StepOwner:
    """Who made a step of a document's history: the call's context (:attr:`Call.caller`,
    empty for the session itself) and its tool."""

    caller: Mapping[str, Any]
    tool: str

    def describe(self) -> str:
        """The caller in words: ``t_add by agent=builder-2``."""
        if not self.caller:
            return f"{self.tool} (a call with no caller context)"
        return f"{self.tool} by " + ", ".join(f"{key}={value}"
                                             for key, value in self.caller.items())

    def to_json(self) -> dict[str, Any]:
        return {"tool": self.tool, "context": dict(self.caller)}


@dataclass(frozen=True)
class OwnUndo:
    """An undo a caller made of its own change, for its redo: ``plain`` (the document's
    latest change, on the history's redo stack) or ``taken`` (taken out from under later
    changes, :meth:`~ooxml_edit.history.History.redo_step`); ``after`` is the version the
    change made, ``parts`` what it touched (``None`` when unknown)."""

    caller: Mapping[str, Any]
    kind: str
    after: int
    parts: frozenset[str] | None


@dataclass
class DocumentEntry:
    """One open document and what the session keeps beside it."""

    doc_id: str
    kind: str
    name: str
    source: str
    document: Any
    size: int = 0
    #: The session's :attr:`~Session.font_dirs` (``None``: the renderers' own default),
    #: for a format's ``checks``, which get the entry alone.
    font_dirs: tuple[str, ...] | None = None
    #: The session's :attr:`~Session.locale`, for a format's ``checks``, which get the
    #: entry alone.  ``None`` only for an entry made outside a session.
    locale: Any = None
    baseline_problems: list[Any] = field(default_factory=list)
    tracking: dict[str, Any] | None = None
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    render_cache: LRU = field(default_factory=lambda: LRU(16), repr=False)
    check_cache: LRU = field(default_factory=lambda: LRU(16), repr=False)
    #: What each view was drawn as, by version: ``(version, view, options...)`` -> the
    #: content key its image is under in the toolbox's :class:`~.rendercache.ContentStore`.
    #: Keys are small, so many are kept: a repeat render of a long document finds them all.
    render_keys: LRU = field(default_factory=lambda: LRU(RENDER_KEYS), repr=False)
    #: The digests of the fonts a worker decoded from this document's embedded ones (kept
    #: in the toolbox's ``decoded_fonts``): what the next task is handed.
    font_keys: LRU = field(default_factory=lambda: LRU(FONT_KEYS), repr=False)
    log: list[CallRecord] = field(default_factory=list, repr=False)
    #: Idempotence keys of creating calls: ``(tool, key) -> result data``.
    keys: dict[tuple[str, str], Any] = field(default_factory=dict, repr=False)
    #: Refs: names a creating call gave its objects, ``{name: address}``.  ``$name`` in a
    #: target argument of a later call is replaced by the address.
    refs: dict[str, str] = field(default_factory=dict, repr=False)
    #: The refs as they were at each version: undo and redo bring them back with the
    #: content, so a ref never outlives what it names (ids are reused once freed).
    ref_states: dict[int, dict[str, str]] = field(default_factory=dict, repr=False)
    #: Who made each step: the version a call's change made -> its :class:`StepOwner`.
    #: A step made outside a call (the library's API directly) has none.
    owners: dict[int, StepOwner] = field(default_factory=dict, repr=False)
    #: Callers' undos of their own changes, latest last, for their redo.
    own_undos: list[OwnUndo] = field(default_factory=list, repr=False)
    _own_version: int = field(default=0, repr=False)
    #: ``(version, bytes)`` of the last :meth:`snapshot`.
    _snapshot: tuple[int, bytes] | None = field(default=None, repr=False)

    @property
    def history(self) -> Any:
        return getattr(self.document, "history", None)

    @property
    def version(self) -> int:
        """The document's state number: its history's version (LE1), else a local count."""
        history = self.history
        if history is not None and hasattr(history, "version"):
            return history.version
        return self._own_version

    def bump(self) -> None:
        """Count a change in a document without a history (the dispatcher calls this)."""
        if self.history is None or not hasattr(self.history, "version"):
            self._own_version += 1

    @contextmanager
    def batch(self, owner: StepOwner | None = None) -> Iterator[None]:
        """One undo step around a call: the document's ``batch()``, or its history's.
        ``owner``: who makes the step, kept in :attr:`owners` when the call made one."""
        batch = getattr(self.document, "batch", None)
        if batch is None and self.history is not None:
            batch = getattr(self.history, "batch", None)
        before = self.version
        if batch is None:
            yield
        else:
            with batch():
                yield
        if owner is not None and self.version != before:
            self.owners[self.version] = owner
            # A new step drops the history's redo stack: no plain undo is redone now.
            self.own_undos = [undo for undo in self.own_undos if undo.kind != "plain"]

    def renumber_owners(self, step: Any, *, undone: bool) -> None:
        """Owners after a scoped undo or redo (:class:`~ooxml_edit.history.ScopedStep`):
        each later step keeps its owner under its new version; a step brought back is its
        maker's again."""
        if undone:
            for old, new in step.renumbered.items():
                if old != step.after and old in self.owners:
                    self.owners[new] = self.owners[old]
        elif step.after in self.owners:
            self.owners[step.version] = self.owners[step.after]

    @contextmanager
    def savepoint(self) -> Iterator[bool]:
        """Inside :meth:`batch`: roll back what the code inside changed if it raises, the
        batch staying open (the document's ``savepoint()``, or its history's:
        :meth:`~ooxml_edit.history.History.savepoint`).  Yields whether a rollback is
        possible; a document with neither keeps what a failing edit did."""
        savepoint = getattr(self.document, "savepoint", None)
        if savepoint is None and self.history is not None:
            savepoint = getattr(self.history, "savepoint", None)
        if savepoint is None:
            yield False
            return
        with savepoint():
            yield True

    def keep_refs(self, defined: dict[str, str]) -> None:
        """Add refs a call defined, and remember the set as this version's."""
        self.refs.update(defined)
        self.ref_states[self.version] = dict(self.refs)

    def scoped_refs(self, step: Any, *, undone: bool) -> None:
        """Refs after a scoped undo or redo (:class:`~ooxml_edit.history.ScopedStep`): the
        names the step defined go (undo) or come back (redo); other refs are kept."""
        before = self.ref_states.get(step.before, {})
        made = {name: address for name, address in self.ref_states.get(step.after, {}).items()
                if before.get(name) != address}
        if undone:
            for old, new in step.renumbered.items():
                if new not in self.ref_states:
                    self.ref_states[new] = {name: address for name, address in
                                            self.ref_states.get(old, {}).items()
                                            if made.get(name) != address}
        else:
            self.ref_states[step.version] = {**self.refs, **made}
        self.refs = dict(self.ref_states.get(step.version, {}))

    def restore_refs(self) -> None:
        """The refs of the version the document is at now (after an undo or redo)."""
        self.refs = dict(self.ref_states.get(self.version, {}))

    def snapshot(self) -> bytes:
        """The document's bytes (``document.to_bytes()``) at this version, made once per
        version: what work in a worker process gets.  Take it under the document's lock.
        Equal versions are equal content (:mod:`ooxml_edit.history`), so a render after a
        render, a check after a render, reuse the bytes the first made."""
        version = self.version
        kept = self._snapshot
        if kept is not None and kept[0] == version:
            return kept[1]
        data = self.document.to_bytes()
        self._snapshot = (version, data)
        return data

    def cached(self, cache: LRU, key: Hashable, compute: Callable[[], Any]) -> Any:
        """``compute()`` cached under ``(version, key)``: a later edit makes a new entry."""
        full = (self.version, key)
        if full in cache:
            return cache.get(full)
        value = compute()
        cache.put(full, value)
        return value

    def describe(self) -> dict[str, Any]:
        return {"doc": self.doc_id, "kind": self.kind, "name": self.name, "source": self.source,
                "version": self.version}


_DOC_ID = re.compile(r"^d(\d+)$")


def doc_order(doc_id: str) -> tuple[int, str]:
    """The order locks are taken in: ``d2`` before ``d10``."""
    match = _DOC_ID.match(doc_id)
    return (int(match.group(1)) if match else 1 << 62, doc_id)


def font_dirs_of(font_dirs: Any) -> tuple[str, ...] | None:
    """``font_dirs`` as a session keeps it: ``None`` as given (the renderers' default),
    otherwise a tuple of path strings -- one path, or a sequence of them; picklable, so it
    crosses into a worker process, and hashable, so it can key a cache."""
    if font_dirs is None:
        return None
    if isinstance(font_dirs, (str, PathLike)):
        font_dirs = [font_dirs]
    out = []
    for path in font_dirs:
        if not isinstance(path, (str, PathLike)):
            raise TypeError(f"font_dirs takes paths, not {type(path).__name__}")
        out.append(fspath(path))
    return tuple(out)


#: The attributes a locale has (:class:`ooxml_common.locales.Locale`'s fields), each a
#: string: all :func:`locale_of` checks of an object it is given.
LOCALE_FIELDS = ("decimal_separator", "thousands_separator", "date_order", "language",
                 "list_separator")


@dataclass(frozen=True)
class DefaultLocale:
    """en-US, the locale of a session given none, without importing ooxml-common: the
    values of :data:`ooxml_common.locales.EN_US` and the part of its interface a renderer
    reads (the fields, :attr:`primary_language`, :meth:`number_text`).  Frozen, hashable
    and picklable.  An application that has ooxml-common passes its ``EN_US`` or any other
    :class:`~ooxml_common.locales.Locale` instead; this is only the default."""

    decimal_separator: str = "."
    thousands_separator: str = ","
    date_order: str = "mdy"
    language: str = "en-US"
    list_separator: str = ","

    @property
    def primary_language(self) -> str:
        """The language subtag alone: ``en``."""
        return self.language.split("-", 1)[0]

    def number_text(self, text: str) -> str:
        """``text`` as written: en-US separators are Python's own (``1,234.5``)."""
        return text


#: The locale of every session given none: en-US, always -- never the operating system's.
DEFAULT_LOCALE = DefaultLocale()


def locale_of(locale: Any) -> Any:
    """``locale`` as a session keeps it: ``None`` is :data:`DEFAULT_LOCALE` (en-US);
    anything else is kept as given -- normally an :class:`ooxml_common.locales.Locale`,
    which validates its values, is frozen and pickles into a worker process -- once it has
    the :data:`LOCALE_FIELDS`, each a string.  Raises :class:`TypeError` otherwise.  A
    language tag alone (``"nl-NL"``) is not a locale: its separators and date order are
    the application's to state."""
    if locale is None:
        return DEFAULT_LOCALE
    missing = [name for name in LOCALE_FIELDS
               if not isinstance(getattr(locale, name, None), str)]
    if missing:
        raise TypeError(f"locale takes an ooxml_common.locales.Locale (or an object with "
                        f"string {', '.join(LOCALE_FIELDS)}), not {type(locale).__name__}: "
                        f"no {', '.join(missing)}")
    return locale


class Session:
    """The documents, blobs and outputs of one conversation.

    ``font_dirs`` is the application's own font folders for every render and measurement
    of the session's documents (see the module's notes); ``None`` leaves the renderers'
    default -- ``OOXML_FONT_DIRS``, then the operating system's folders.  ``locale`` is
    the regional settings of every render and measurement (an
    :class:`ooxml_common.locales.Locale`); ``None``, the default, is en-US.  ``roles`` is
    what the session's calls may do (:data:`~.registry.ROLES`, any of ``review``,
    ``resolve``, ``accept`` and ``edit``); ``None``, the default, is all four: every tool.
    :meth:`view` gives an agent the same session with fewer roles.
    """

    #: The application's context of the calls made here: none (a :class:`SessionView`
    #: carries one).
    context: Mapping[str, Any] = MappingProxyType({})

    def __init__(self, formats: Iterable[DocumentFormat] = (), *, clock: Clock | None = None,
                 limits: Limits | None = None, on_output: Callable[[Output], None] | None = None,
                 session_id: str | None = None, font_dirs: Any = None,
                 locale: Any = None, roles: Any = None) -> None:
        self.id = session_id or uuid.uuid4().hex[:12]
        #: The roles whose calls the toolbox runs here; a call outside them is refused.
        self.roles: tuple[str, ...] = role_set(roles)
        #: The application's own font folders, as path strings; ``None``: the default.
        self.font_dirs: tuple[str, ...] | None = font_dirs_of(font_dirs)
        #: The regional settings of the session's documents; en-US unless given.
        self.locale: Any = locale_of(locale)
        self.clock: Clock = clock or utc_now
        self.created_at = self.clock()
        self.limits = limits or Limits()
        self.on_output = on_output
        self.formats: dict[str, DocumentFormat] = {fmt.kind: fmt for fmt in formats}
        self.documents: dict[str, DocumentEntry] = {}
        self.blobs: dict[str, Blob] = {}
        self.outputs: list[Output] = []
        #: Datasets by id (without ``d:``), in the order defined: :mod:`.datasets`.
        self.datasets: dict[str, DatasetEntry] = {}
        self.log: list[CallRecord] = []
        #: Images returned this session, and since the last :meth:`new_round`.
        self.images_used = 0
        self.images_this_round = 0
        self.rounds = 1
        self._lock = threading.RLock()
        self._doc_numbers = itertools.count(1)
        self._blob_numbers = itertools.count(1)

    def view(self, *, roles: Any = None, context: Mapping[str, Any] | None = None,
             ) -> "SessionView":
        """This session for one agent: the same documents, history, refs, datasets, outputs
        and locks, with ``roles`` -- some of :attr:`roles` (``None``: all of them) -- and
        ``context``, the application's own words about the caller (``{"agent":
        "reviewer-1"}``), which reach every handler of its calls as
        :attr:`~.dispatch.Call.caller`.  Pass the view to :meth:`.Toolbox.dispatch` as the
        session.  Roles outside the session's are a ``ValueError``: a view narrows, never
        widens (see :class:`SessionView`)."""
        return SessionView(self, roles=roles, context=context)

    # -- documents ---------------------------------------------------------------------------

    def open(self, data: bytes, name: str, *, kind: str | None = None,
             source: str = "app") -> str:
        """Open a document from bytes and return its id (``d1``)."""
        data = _require_bytes(data, "open")
        fmt = self._format_for(data, name, kind)
        check_package(data, self.limits, name)
        self._room_for_document(len(data))
        document = fmt.open(data)
        return self.adopt(document, fmt.kind, name, source=source, size=len(data))

    def adopt(self, document: Any, kind: str, name: str, *, source: str = "new",
              size: int = 0) -> str:
        """Add a document made in memory (a new one, or one opened from a blob)."""
        if kind not in self.formats:
            raise ValueError(f"no format {kind!r} is registered; one of {sorted(self.formats)}")
        fmt = self.formats[kind]
        if fmt.configure is not None:
            fmt.configure(document, self)
        baseline = list(fmt.problems(document)) if fmt.problems else []
        with self._lock:
            self._room_for_document(size)
            doc_id = f"d{next(self._doc_numbers)}"
            self.documents[doc_id] = DocumentEntry(
                doc_id=doc_id, kind=kind, name=name, source=source, document=document,
                size=size, baseline_problems=baseline, font_dirs=self.font_dirs,
                locale=self.locale,
                render_cache=LRU(self.limits.render_cache_size),
                check_cache=LRU(self.limits.check_cache_size))
            entry = self.documents[doc_id]
            entry.ref_states[entry.version] = {}
        return doc_id

    def entry(self, doc_id: str) -> DocumentEntry:
        """The open document ``doc_id``; ``not_found`` names the open ones."""
        with self._lock:
            entry = self.documents.get(doc_id)
            if entry is None:
                raise ToolError("not_found", f"no open document {doc_id!r}", field="doc",
                                valid_options=sorted(self.documents, key=doc_order))
            return entry

    def provenance(self, doc_id: str) -> dict[str, dict[str, Any]]:
        """Where the values placed in ``doc_id`` from sources came from, as the saved
        document keeps it (:attr:`DocumentFormat.read_provenance`): ``{address: record}``,
        empty when the format keeps none."""
        entry = self.entry(doc_id)
        hook = getattr(self.formats.get(entry.kind), "read_provenance", None)
        if hook is None:
            return {}
        with entry.lock:
            return {str(address): dict(found)
                    for address, found in (hook(entry.document) or {}).items()}

    def close(self, doc_id: str) -> list[str]:
        """Close a document; the addresses of the datasets derived from it, which stay,
        each as stale or current as it is now, for good."""
        entry = self.entry(doc_id)
        with entry.lock, self._lock:
            self.documents.pop(doc_id, None)
            kept = []
            for dataset in self.datasets.values():
                if dataset.doc == doc_id and not dataset.closed:
                    dataset.source_closed()
                    kept.append(dataset.address)
            return kept

    @contextmanager
    def lock(self, doc_ids: Iterable[str]) -> Iterator[list[DocumentEntry]]:
        """Hold the locks of several documents, taken in :func:`doc_order`."""
        ordered = sorted(set(doc_ids), key=doc_order)
        entries = [self.entry(doc_id) for doc_id in ordered]
        with ExitStack() as stack:
            for entry in entries:
                stack.enter_context(entry.lock)
            yield entries

    def undo(self, doc_id: str, steps: int = 1, *, scope: str | None = None,
             caller: Mapping[str, Any] | None = None) -> int:
        """Undo up to ``steps`` calls; the number undone.

        With ``scope`` (a page, as the format's ``undo_scope`` reads it) only the
        calls that changed the scope are undone, each the latest such call, the others left
        in place.  A call whose changes share a part with a later call outside the scope is
        refused with ``entangled`` -- when it is the first step; a later one stops the run.

        With ``caller`` -- a call's non-empty context (:attr:`~.dispatch.Call.caller`) --
        only that caller's own changes are undone, each its latest (in ``scope``, when
        given): plainly when it is the document's latest change; else taken out from under
        the later changes in a format with undo scopes, when none of them touched a part it
        touched (as a scoped undo); otherwise ``entangled``, naming the later change and
        who made it.  An empty or no ``caller`` is the session's own undo, as above."""
        if caller:
            return self._own(doc_id, steps, scope, "undo", caller)
        if scope is not None:
            return self._scoped(doc_id, steps, scope, "undo")
        return self._step(doc_id, steps, "undo")

    def redo(self, doc_id: str, steps: int = 1, *, scope: str | None = None,
             caller: Mapping[str, Any] | None = None) -> int:
        """Redo up to ``steps`` undone calls; with ``scope``, the scoped undos of it; with
        ``caller``, that caller's own undos (its latest first)."""
        if caller:
            return self._own(doc_id, steps, scope, "redo", caller)
        if scope is not None:
            return self._scoped(doc_id, steps, scope, "redo")
        return self._step(doc_id, steps, "redo")

    def _own(self, doc_id: str, steps: int, scope: str | None, direction: str,
             caller: Mapping[str, Any]) -> int:
        """Undo or redo ``caller``'s own changes (:meth:`undo`)."""
        entry = self.entry(doc_id)
        fmt = self.formats.get(entry.kind)
        if scope is not None and (fmt is None or fmt.undo_scope is None):
            raise ToolError("invalid_arguments", f"{doc_id} ({entry.kind}) undoes document-wide "
                            "only: leave scope out", field="scope")
        caller = dict(caller)
        with entry.lock:
            history = entry.history
            if history is None or not hasattr(history, "steps"):
                raise ToolError("refused", f"{doc_id} keeps no history to {direction} "
                                "one caller's changes")
            scope_parts = (frozenset(fmt.undo_scope(entry.document, scope))
                           if scope is not None else None)
            own = _OwnUndoing(self, entry, fmt, history, caller, scope_parts, doc_id)
            done = 0
            try:
                while done < steps:
                    try:
                        if not (own.undo() if direction == "undo" else own.redo()):
                            break
                    except ToolError:
                        if not done:
                            raise
                        break
                    done += 1
            finally:
                if own.restored and fmt is not None and fmt.restored is not None:
                    fmt.restored(entry.document)
            return done

    def _scoped(self, doc_id: str, steps: int, scope: str, direction: str) -> int:
        from ..history import Entangled

        entry = self.entry(doc_id)
        fmt = self.formats.get(entry.kind)
        if fmt is None or fmt.undo_scope is None:
            raise ToolError("invalid_arguments", f"{doc_id} ({entry.kind}) undoes document-wide "
                            "only: leave scope out", field="scope")
        with entry.lock:
            history = entry.history
            if history is None or not hasattr(history, f"{direction}_in"):
                raise ToolError("refused", f"{doc_id} keeps no history to {direction} by scope")
            parts = list(fmt.undo_scope(entry.document, scope))
            common = list(fmt.undo_shared(entry.document)) if fmt.undo_shared else []
            done = 0
            try:
                while done < steps:
                    step = (history.undo_in(parts, shared=common) if direction == "undo"
                            and common else getattr(history, f"{direction}_in")(parts))
                    if step is None:
                        break
                    done += 1
                    entry.scoped_refs(step, undone=direction == "undo")
                    entry.renumber_owners(step, undone=direction == "undo")
            except Entangled as exc:
                if not done:
                    shared_by_all = sorted(set(exc.shared) & set(common))
                    if shared_by_all:
                        raise ToolError(
                            "entangled", f"the latest change to {scope} also changed "
                            f"{', '.join(shared_by_all)}, which other pages show too: undo "
                            "without scope (it reverts the latest changes, whoever made them), "
                            "or change it back", field="scope",
                            details={"parts": exc.parts, "shared": exc.shared}) from None
                    raise ToolError(
                        "entangled", f"the latest {'' if direction == 'undo' else 'undone '}"
                        f"change to {scope} shares parts with later changes outside it: "
                        "undo without scope (it reverts the latest changes, whoever made "
                        "them), or leave it", field="scope",
                        details={"parts": exc.parts, "shared": exc.shared}) from None
            finally:
                if done and fmt.restored is not None:
                    fmt.restored(entry.document)
            return done

    def _step(self, doc_id: str, steps: int, direction: str) -> int:
        entry = self.entry(doc_id)
        with entry.lock:
            target = getattr(entry.document, direction, None)
            if target is None and entry.history is not None:
                target = getattr(entry.history, direction)
            if target is None:
                raise ToolError("refused", f"{doc_id} keeps no history to {direction}")
            done = 0
            while done < steps and target():
                done += 1
            return done

    # -- blobs -------------------------------------------------------------------------------

    def add_blob(self, data: bytes, name: str, mime: str | None = None) -> str:
        """Register an input and return its handle (``b1``).

        The bytes are checked first: the size limits, that they are what ``mime`` claims
        (and the type is found from them when it is not given), an image's decoded size,
        and a ZIP package's central directory.  Raises :class:`~.limits.LimitError`.
        """
        data = _require_bytes(data, "add_blob")
        mime = check_declared_type(data, name, mime)
        limits = self.limits
        if len(data) > limits.max_blob_bytes:
            raise LimitError(f"{name} is {len(data)} bytes; at most {limits.max_blob_bytes}")
        if mime.startswith("image/"):
            check_image(data, limits, name)
        elif data.startswith(b"PK"):
            check_package(data, limits, name)
        elif mime.startswith("text/") and len(data.decode("utf-8")) > limits.max_markdown_chars:
            raise LimitError(f"{name} is over {limits.max_markdown_chars} characters")
        with self._lock:
            used = sum(blob.size for blob in self.blobs.values())
            if used + len(data) > limits.max_total_blob_bytes:
                raise LimitError(f"the session's blobs would be {used + len(data)} bytes; at "
                                 f"most {limits.max_total_blob_bytes}")
            handle = f"b{next(self._blob_numbers)}"
            self.blobs[handle] = Blob(handle, name, mime, data)
        return handle

    def blob(self, handle: str) -> Blob:
        """The blob ``handle``; ``not_found`` lists the handles there are."""
        with self._lock:
            blob = self.blobs.get(handle)
            if blob is None:
                raise ToolError("not_found", f"no blob {handle!r}",
                                valid_options=[f"{b.handle} ({b.name})"
                                               for b in self.blobs.values()])
            return blob

    def open_blob(self, handle: str, *, kind: str | None = None) -> str:
        """Open a registered blob as a document (what an ``open_document`` tool does)."""
        blob = self.blob(handle)
        try:
            return self.open(blob.data, blob.name, kind=kind, source=handle)
        except LimitError as exc:
            raise ToolError("limit", str(exc)) from None
        except ValueError as exc:
            if _is_text(blob):
                raise ToolError("invalid_arguments", f"{blob.name} is text ({blob.mime}), not "
                                f"a document: read it with read_blob", field="blob",
                                valid_options=["read_blob"]) from None
            if blob.mime.startswith("image/"):
                raise ToolError("invalid_arguments", f"{blob.name} is an image ({blob.mime}), "
                                "not a document: place it with a picture tool",
                                field="blob") from None
            raise ToolError("invalid_arguments", str(exc), field="blob",
                            valid_options=sorted(self.formats)) from None

    # -- datasets ----------------------------------------------------------------------------

    def dataset(self, address: str, *, field: str | None = None) -> DatasetEntry:
        """The dataset ``address`` names (``d:by_region``, ``d:by_region[total]``, or the
        bare id); ``not_found`` lists the ids there are."""
        found = dataset_id(address) if isinstance(address, str) and address.startswith(
            DATASET_PREFIX) else address
        with self._lock:
            entry = self.datasets.get(found) if isinstance(found, str) else None
            if entry is None:
                raise ToolError("not_found", f"no dataset {DATASET_PREFIX}{found}",
                                field=field, valid_options=self.dataset_ids())
            return entry

    def dataset_ids(self) -> list[str]:
        """Every dataset's address, ``d:`` and its id, in the order defined."""
        with self._lock:
            return [entry.address for entry in self.datasets.values()]

    def dataset_entries(self) -> list[DatasetEntry]:
        """Every dataset, in the order defined."""
        with self._lock:
            return list(self.datasets.values())

    def keep_datasets(self, entries: Iterable[DatasetEntry]) -> None:
        """Keep datasets a call defined: all, or none and ``limit``.  An id that exists is
        replaced; a new one needs room under ``Limits.max_datasets``."""
        entries = list(entries)
        if not entries:
            return
        limits = self.limits
        with self._lock:
            new = {entry.id for entry in entries} - set(self.datasets)
            if len(self.datasets) + len(new) > limits.max_datasets:
                raise ToolError("limit", f"{len(self.datasets)} datasets are kept; at most "
                                f"{limits.max_datasets}: reuse an id to replace one",
                                valid_options=self.dataset_ids())
            for entry in entries:
                if entry.doc is not None and entry.doc not in self.documents:
                    entry.source_closed()       # closed while the call ran (in a batch)
                self.datasets.pop(entry.id, None)
                self.datasets[entry.id] = entry

    def drop_dataset(self, address: str) -> None:
        """Forget a dataset (the application's housekeeping; no tool does this)."""
        entry = self.dataset(address)
        with self._lock:
            self.datasets.pop(entry.id, None)

    # -- outputs -----------------------------------------------------------------------------

    def add_output(self, output: Output) -> None:
        if self.on_output is not None:
            self.on_output(output)
            return
        with self._lock:
            self.outputs.append(output)

    def take_outputs(self) -> list[Output]:
        """The outputs saved since the last call, handed over and forgotten."""
        with self._lock:
            outputs, self.outputs = self.outputs, []
        return outputs

    # -- budgets and the clock -----------------------------------------------------------------

    def new_round(self) -> None:
        """Start a round (the application calls this per user message, say): the per-round
        image budget (``Limits.image_budget_per_round``) starts again; the session's does not."""
        with self._lock:
            self.images_this_round = 0
            self.rounds += 1

    def images_remaining(self) -> int | None:
        """Images still allowed now: the smaller of what the session's and the round's
        budgets leave; ``None`` when neither caps."""
        with self._lock:
            left = [budget - used for budget, used in (
                (self.limits.image_budget, self.images_used),
                (self.limits.image_budget_per_round, self.images_this_round))
                if budget is not None]
            return max(0, min(left)) if left else None

    def check_images(self, count: int) -> None:
        """Refuse with ``limit``, as :meth:`reserve_images` would, when ``count`` images
        would pass the session's or the round's budget -- counting none: a format asks
        before it renders, so a call that will be refused costs nothing."""
        limits = self.limits
        with self._lock:
            if limits.image_budget is not None and self.images_used + count > limits.image_budget:
                self._over_budget("session", limits.image_budget, self.images_used)
            per_round = limits.image_budget_per_round
            if per_round is not None and self.images_this_round + count > per_round:
                self._over_budget("round", per_round, self.images_this_round)

    def _over_budget(self, which: str, limit: int, used: int) -> None:
        if which == "session":
            raise ToolError("limit", f"the session's image budget is {limit} and {used} are "
                            "used; work from text (describe, check) from here", details={
                                "budget": "session", "limit": limit,
                                "remaining": max(0, limit - used)})
        raise ToolError("limit", f"this round's image budget is {limit} and {used} are used; "
                        "render again next round",
                        details={"budget": "round", "limit": limit,
                                 "remaining": max(0, limit - used)})

    def reserve_images(self, count: int) -> None:
        """Count ``count`` images against the session's and the round's budgets, or refuse
        with ``limit``."""
        with self._lock:
            self.check_images(count)
            self.images_used += count
            self.images_this_round += count

    def now(self) -> _dt.datetime:
        """The session clock's time: every date a tool writes comes from here."""
        return self.clock()

    def describe(self) -> dict[str, Any]:
        with self._lock:
            data = {"documents": [e.describe() for e in sorted(
                        self.documents.values(), key=lambda e: doc_order(e.doc_id))],
                    "blobs": [blob.describe() for blob in self.blobs.values()]}
            if self.datasets:
                data["datasets"] = [entry.describe() for entry in self.datasets.values()]
            return data

    # -- internals ---------------------------------------------------------------------------

    def _format_for(self, data: bytes, name: str, kind: str | None) -> DocumentFormat:
        if kind is not None:
            if kind not in self.formats:
                raise ValueError(f"no format {kind!r}; one of {sorted(self.formats)}")
            return self.formats[kind]
        for fmt in self.formats.values():
            if fmt.detect(data, name):
                return fmt
        raise ValueError(f"{name} is not a document of any registered format "
                         f"({', '.join(sorted(self.formats)) or 'none registered'})")

    def _room_for_document(self, size: int) -> None:
        with self._lock:
            if len(self.documents) >= self.limits.max_documents:
                raise LimitError(f"{len(self.documents)} documents are open; at most "
                                 f"{self.limits.max_documents}: close_document one you are "
                                 "done with (save it first if it changed)")
            used = sum(entry.size for entry in self.documents.values())
            if used + size > self.limits.max_total_document_bytes:
                raise LimitError(f"open documents would total {used + size} bytes; at most "
                                 f"{self.limits.max_total_document_bytes}")


class _OwnUndoing:
    """One caller's undo or redo of its own changes to one document, its lock held
    (:meth:`Session.undo` with ``caller``)."""

    def __init__(self, session: Session, entry: DocumentEntry, fmt: DocumentFormat | None,
                 history: Any, caller: dict[str, Any], scope: frozenset[str] | None,
                 doc_id: str) -> None:
        self.session = session
        self.entry = entry
        self.fmt = fmt
        self.history = history
        self.caller = caller
        self.scope = scope
        self.doc_id = doc_id
        #: Whether parts were put back behind the document's back (``fmt.restored``).
        self.restored = False

    def _mine(self, version: int) -> bool:
        owner = self.entry.owners.get(version)
        return owner is not None and dict(owner.caller) == self.caller

    def _in_scope(self, parts: frozenset[str] | None) -> bool:
        return self.scope is None or bool(parts and parts & self.scope)

    def _who(self, version: int) -> str:
        owner = self.entry.owners.get(version)
        if owner is None:
            return "a change made outside the tools"
        if dict(owner.caller) == self.caller:
            return f"your own {owner.tool}"
        return owner.describe()

    def _later(self, versions: Sequence[int]) -> list[dict[str, Any]]:
        out = []
        for version in versions:
            owner = self.entry.owners.get(version)
            out.append({"version": version, **(owner.to_json() if owner else {})})
        return out

    def undo(self) -> bool:
        """Undo the caller's latest change; ``False`` when it has none left to undo."""
        from ..history import Entangled

        entry, history = self.entry, self.history
        steps = history.steps()
        mine = [k for k, step in enumerate(steps)
                if self._mine(step.after) and self._in_scope(step.parts)]
        if not mine:
            return False
        index = mine[-1]
        step = steps[index]
        later = steps[index + 1:]
        if not later:
            # The document's latest change: a plain undo, exact, as the session's.
            target = getattr(entry.document, "undo", None) or history.undo
            target()
            entry.restore_refs()
            entry.own_undos.append(OwnUndo(self.caller, "plain", step.after, step.parts))
            return True
        owner = entry.owners[step.after]
        mine_words = f"your latest change to {self.doc_id} ({owner.tool}, version {step.after})"
        last = later[-1].after
        if (self.fmt is None or self.fmt.undo_scope is None or step.parts is None
                or not hasattr(history, "undo_step")):
            # One body every change shares: a later change may build on any of it.
            raise ToolError(
                "entangled", f"{mine_words} is not the latest: {self._who(last)} changed "
                f"{self.doc_id} after it (version {last}"
                + (f", one of {len(later)} later changes" if len(later) > 1 else "")
                + "), and undoing yours would take back what came after. Change it back "
                "with an edit, or ask whoever made the later change to undo it first",
                details={"version": step.after, "later": self._later(
                    [later_step.after for later_step in later])})
        shared = (list(self.fmt.undo_shared(entry.document)) if self.fmt.undo_shared
                  else [])
        try:
            done = history.undo_step(step.after, shared=shared)
        except Entangled as exc:
            if not exc.later:
                raise ToolError(
                    "entangled", f"{mine_words} also changed {', '.join(exc.shared)}, which "
                    "other pages show too, and others changed the document since: it is no "
                    "one caller's to take back. Change it back with an edit",
                    details={"version": step.after, "parts": exc.parts,
                             "shared": exc.shared}) from None
            first = exc.later[0]
            raise ToolError(
                "entangled", f"{mine_words} is not the latest: {self._who(first)} (version "
                f"{first}) changed {', '.join(exc.shared)} after it, which yours changed "
                "too, so undoing yours would revert that as well. Change it back with an "
                "edit, or ask whoever made the later change to undo it first",
                details={"version": step.after, "parts": exc.parts, "shared": exc.shared,
                         "later": self._later(exc.later)}) from None
        self.restored = True
        entry.scoped_refs(done, undone=True)
        entry.renumber_owners(done, undone=True)
        entry.own_undos.append(OwnUndo(self.caller, "taken", step.after, step.parts))
        return True

    def redo(self) -> bool:
        """Redo the caller's latest undo of its own; ``False`` when there is none."""
        from ..history import Entangled

        entry, history = self.entry, self.history
        while True:
            found = [k for k, undo in enumerate(entry.own_undos)
                     if dict(undo.caller) == self.caller and self._in_scope(undo.parts)]
            if not found:
                return False
            index = found[-1]
            undo = entry.own_undos[index]
            if undo.kind == "plain":
                waiting = getattr(history, "next_redo", None)
                if waiting is None:     # the redo stack went: nothing of it comes back
                    del entry.own_undos[index]
                    continue
                if waiting != undo.after:
                    raise ToolError(
                        "entangled", f"your undone change to {self.doc_id} (version "
                        f"{undo.after}) is not the next to redo: another caller undid a "
                        "change since. Make your change again instead")
                target = getattr(entry.document, "redo", None) or history.redo
                target()
                entry.restore_refs()
                del entry.own_undos[index]
                return True
            try:
                step = history.redo_step(undo.after)
            except Entangled as exc:
                raise ToolError(
                    "entangled", f"your undone change to {self.doc_id} (version "
                    f"{undo.after}) cannot come back: {', '.join(exc.shared)} changed since "
                    "the undo. Make your change again instead",
                    details={"version": undo.after, "parts": exc.parts,
                             "shared": exc.shared}) from None
            del entry.own_undos[index]
            if step is None:        # out of the history's depth: gone; the one before?
                continue
            self.restored = True
            entry.scoped_refs(step, undone=False)
            entry.renumber_owners(step, undone=False)
            return True


class SessionView:
    """A session as one agent uses it: :meth:`Session.view`.

    It is the session -- every attribute and method but :attr:`roles` and :attr:`context`
    is the session's own, read through, so documents, undo history, refs, datasets,
    outputs, the call log and every lock are shared with the session and its other views
    -- with roles and a context of its own, both fixed when it is made.  So threads may use
    views of one session at once exactly as they may use the session: a call locks its
    documents, whichever view it came through.  Nothing is set on a view (set it on
    :attr:`session`).

    Its roles are some of its session's: a view narrows, never widens.  The session's
    roles are the most the application allows anything in the conversation; a view handed
    to an agent -- and any view made of that view -- can only take away from them, so no
    holder of a view can give itself a role it was not given.  ``context`` adds to the
    context of the view it is made from (its keys win).  The view's roles are taken when it
    is made: a session whose :attr:`Session.roles` is narrowed later refuses, at its next
    call, a view's role it no longer has (``ValueError``).
    """

    __slots__ = ("session", "roles", "context")

    def __init__(self, session: "Session | SessionView", *, roles: Any = None,
                 context: Mapping[str, Any] | None = None) -> None:
        base = session.session if isinstance(session, SessionView) else session
        if not isinstance(base, Session):
            raise TypeError(f"a view is of a Session, not {type(session).__name__}")
        given_roles, given_context = narrowed(session.roles, session.context, roles, context)
        object.__setattr__(self, "session", base)
        object.__setattr__(self, "roles", given_roles)
        object.__setattr__(self, "context", given_context)

    def view(self, *, roles: Any = None, context: Mapping[str, Any] | None = None,
             ) -> "SessionView":
        """A narrower view: ``roles`` some of this view's, ``context`` added to its."""
        return SessionView(self, roles=roles, context=context)

    def undo(self, doc_id: str, steps: int = 1, *, scope: str | None = None) -> int:
        """:meth:`Session.undo` as this view's caller: its own changes when it has a
        context, the latest whoever made them when it has none."""
        return self.session.undo(doc_id, steps, scope=scope, caller=self.context)

    def redo(self, doc_id: str, steps: int = 1, *, scope: str | None = None) -> int:
        """:meth:`Session.redo` as this view's caller (see :meth:`undo`)."""
        return self.session.redo(doc_id, steps, scope=scope, caller=self.context)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "session"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"a session view sets nothing: set {name} on its session "
                             "(view.session), or make another view")

    def __repr__(self) -> str:
        return (f"<SessionView of {self.session.id} roles={list(self.roles)} "
                f"context={dict(self.context)}>")


def narrowed(roles: Sequence[str], context: Mapping[str, Any], wanted: Any = None,
             more: Mapping[str, Any] | None = None) -> tuple[tuple[str, ...],
                                                              Mapping[str, Any]]:
    """``roles`` and ``context`` narrowed by ``wanted`` -- a role set (``None``: as they
    are), which must be some of ``roles`` -- and added to by ``more``: a ``ValueError``
    for a role ``roles`` does not have, a ``TypeError`` for a context that is no mapping."""
    if wanted is None:
        found = tuple(roles)
    else:
        found = role_set(wanted)
        extra = [role for role in found if role not in roles]
        if extra:
            raise ValueError(f"{', '.join(extra)} is not among this session's roles "
                             f"({', '.join(roles)}): a view or a call narrows the session's "
                             "roles, never widens them -- make the session with every role "
                             "its agents need (toolbox.session(roles=...)) and give each "
                             "agent a view of some of them")
    if more is None:
        return found, context
    if not isinstance(more, Mapping):
        raise TypeError(f"context takes a mapping (a dict), not {type(more).__name__}")
    return found, MappingProxyType({**context, **more})


#: No context: what a call made on a session itself sees.
NO_CONTEXT: Mapping[str, Any] = Session.context


def _is_text(blob: Blob) -> bool:
    """Whether a blob holds text (CSV, Markdown, plain text, JSON): what ``read_blob`` reads."""
    if blob.data.startswith(b"PK") or blob.mime.startswith("image/"):
        return False
    try:
        # Not final: a cut through a multi-byte character at the end is still text.
        text = codecs.getincrementaldecoder("utf-8-sig")().decode(blob.data[:65536])
    except UnicodeDecodeError:
        return False
    return "\x00" not in text


def _require_bytes(data: Any, what: str) -> bytes:
    """Bytes only: a path, a string or a file object is refused, by design (no paths)."""
    if isinstance(data, (bytearray, memoryview)):
        return bytes(data)
    if not isinstance(data, bytes):
        kind = "a path" if isinstance(data, (str, PathLike)) else type(data).__name__
        raise TypeError(f"{what} takes bytes, not {kind}: the tool layer reads no files")
    return data
