"""A session: the documents, inputs and outputs of one conversation, all in memory.

The application opens documents from bytes (:meth:`Session.open` → ``"d1"``) and registers
every other input as a blob (:meth:`Session.add_blob` → ``"b1"``).  Tools refer to both by
those handles only; nothing in a session is a path.  Ids count up in open order and are never
handed out twice; an application resuming a run reopens a document under the id its saved
conversation names (``Session.open(data, name, doc_id="d1")``), refused while that id is open.  What a tool saves goes to
:attr:`Session.outputs` (or the ``on_output`` callback) as bytes for the application; the
model sees its name and size, never the bytes.

Concurrency.  A document is an lxml tree mutated in place, so every call on it holds its
entry's re-entrant lock.  Calls on different documents run concurrently.  A call on several
documents takes their locks in :func:`doc_order`, so two such calls cannot deadlock.  The
session's own maps have a lock of their own, never held while a document lock is awaited.
A slow read lets its locks go and works on a snapshot (:meth:`DocumentEntry.snapshot`, the
bytes of one version, made once and shared by every reader of it), in a worker process
(:meth:`~.dispatch.Call.on_snapshot`): reads of one document run side by side, and an edit
does not wait for them.

Committed versions (round 45).  Where a format copies its documents
(:attr:`DocumentFormat.read_copy`), a changing call keeps the version it leaves as the
document's committed one before it lets the lock go (:meth:`DocumentEntry.commit`: the
version's bytes, refs and what the format's copy needs), and a reading call works on a copy
of the committed version under the copy's own lock (:meth:`Session.read_lock`,
:meth:`DocumentEntry.reader`): a read never waits for an edit in flight -- which changes
the document, never the copy -- nor an edit for a read.  A caller's own change is committed
before its call returns, so its next read sees it.  The copy shares the document's caches
(keyed by version: equal versions are equal content) and call log, and is never changed.
A changing call's checks run on the copy of the version it made, after it let the lock go
(:class:`~.dispatch.After`), so the next edit does not wait for them either.

Edits of different blocks (round 45, lane B).  Where a format says how
(:attr:`DocumentFormat.blocks`, :mod:`.blocks`), a changing call it admits runs on a draft of
the committed version without the document's lock, and its change is applied under the lock
when what it read is as it was (else it is prepared again): edits of other blocks run beside
it.  The dispatcher counts every change it makes by its parts (:meth:`DocumentEntry.
note_change`), and the calls in flight on each document (:meth:`DocumentEntry.enter`).
Other edits of one document run one at a time, under its lock.

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
it has a locale's attributes (:func:`locale_of`).  Not given, it is
:data:`DEFAULT_LOCALE`, which is :data:`ooxml_common.locales.EN_US`.

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

Refs (round 23, V5).  A creating call may name what it made (``ref``); later calls write
``$name`` for it.  The application reads a document's refs outside a call with
:meth:`Session.refs` -- a copy, ``{name: address}`` -- and resolves one with
:meth:`Session.resolve_ref`, as a call's handler does with
:meth:`~.dispatch.Call.resolve_ref`.
"""

from __future__ import annotations

import codecs
import datetime as _dt
import itertools
import re
import threading
import time
import uuid
from collections import OrderedDict, deque
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from os import PathLike, fspath
from types import MappingProxyType
from typing import Any, Callable, Hashable, Iterable, Iterator, Mapping, Sequence

from ooxml_common.locales import EN_US, Locale

from .datasets import DATASET_PREFIX, DatasetEntry, dataset_id
from .limits import LimitError, Limits, check_declared_type, check_image, check_package
from .logs import CallRecord
from .numbers import NumberRules, rules_of
from .registry import role_set
from .results import ToolError
from ..errors import ArgumentError, ArgumentTypeError

Clock = Callable[[], _dt.datetime]


#: A ref's name: what ``$name`` may say.
REF = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
#: A ref used in an argument: ``$name``, or ``$name/rest`` (an address inside it).
REF_USE = re.compile(r"^\$([a-z][a-z0-9_]{0,31})(/.*)?$")
#: What :func:`fold_ref` folds: letters, digits, ``_`` and ``-``, from a letter.
_FOLDABLE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def fold_ref(name: Any) -> str | None:
    """The ref name a camelCase or hyphenated name stands for: ``recNow`` -> ``rec_now``,
    ``scale-note`` -> ``scale_note``, ``HTTPLink`` -> ``http_link``; ``None`` when it
    folds to no ref name, holds anything but letters, digits, ``_`` and ``-``, or has no
    lowercase letter (``A1`` is an address, not a ref)."""
    if not isinstance(name, str) or not _FOLDABLE.match(name) or not re.search("[a-z]", name):
        return None
    folded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name)
    folded = folded.replace("-", "_").lower()
    return folded if REF.match(folded) else None


def _folded_use(value: Any, known: Mapping[str, str],
                failed: Mapping[str, int] | None = None) -> str | None:
    """``$recNow`` (or ``$recNow/rest``) as ``$rec_now`` when it folds (:func:`fold_ref`)
    to a known ref -- or one a failed op of the batch would have made, whose refusal then
    says so; ``None`` for anything else."""
    if not isinstance(value, str) or not value.startswith("$"):
        return None
    head, slash, rest = value[1:].partition("/")
    folded = fold_ref(head)
    if folded is None or folded == head or (folded not in known and folded not in (failed or {})):
        return None
    return f"${folded}{slash}{rest}"


def _misspelt_ref(value: Any, known: Mapping[str, str]) -> str | None:
    """The known ref a ``$Name`` that is no ref name stands for (``$catChart`` for a ref
    ``catchart`` or ``cat_chart``); ``None`` for anything else, which stays an address."""
    if not isinstance(value, str) or not value.startswith("$"):
        return None
    head = value[1:].split("/", 1)[0]
    folded = head.lower().replace("_", "").replace("-", "")
    found = [name for name in known if name.replace("_", "") == folded]
    return found[0] if len(found) == 1 else None


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
    #: The libraries' typed refusals need no entry (:mod:`ooxml_edit.errors`: an
    #: ``ArgumentError`` is ``invalid_arguments``, a ``UnitError`` ``unit``); a subclass
    #: listed here gets the code given instead.
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
    #: Round 36: the addresses of this format's documents whose values text may quote --
    #: its tables, ``["t:Scores", "t:Costs"]`` -- read with the resolver of
    #: :attr:`datasets` when a check of numbers compares text with values (:mod:`.numbers`):
    #: ``value_sources(document) -> addresses``.  Each dataset's
    #: :attr:`~.datasets.Dataset.formats` say how its values are shown.  ``None``: none.
    value_sources: Callable[[Any], Iterable[str]] | None = None
    #: Round 36: the texts of the call's document a check of numbers reads (``check``'s
    #: ``include: ["numbers"]``, :mod:`.numbers`): ``number_texts(call, scope) ->``
    #: :class:`~.numbers.NumberText` items, ``scope`` the check's arguments given that
    #: narrow it (pages, blocks; refs resolved as the check's).  Each is a paragraph's, a
    #: shape's or a cell's own text, with the spans written by reference marked, and
    #: without tables and charts placed from a source.  ``None``: the kind's documents give none (refused).
    number_texts: Callable[[Any, Mapping[str, Any]], Iterable[Any]] | None = None
    #: Round 36 (R31): the values a scale's targets name in the call's document
    #: (``style_by_value`` and ``check``'s ``scale``, :mod:`.scales`):
    #: ``scale_values(call, scale, scope) ->`` :class:`~.scales.ScaleValue` items, each the
    #: value's text, the step it is styled with now and where it is; ``scope`` the check's
    #: arguments that narrow it (empty when applying).  ``None``: the kind styles no values
    #: by a scale (refused).
    scale_values: Callable[[Any, Any, Mapping[str, Any]], Iterable[Any]] | None = None
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
    #: What a changing call changed, for the application (``Result.touched``; the model
    #: never sees it): ``touched(document, change)`` -> entries (dicts), where ``change``
    #: is a :class:`~ooxml_edit.history.Change` -- its ``parts`` the part names whose bytes
    #: differ, ``before(path)``/``after(path)`` their bytes -- and each entry names what
    #: changed in the format's own addresses (a page and the objects on it).  The
    #: dispatcher adds ``"doc"`` to each.  Called once per call that changed parts, and per
    #: op and document in a ``batch``.  ``None``: :func:`part_entries`, one ``{"part"}``
    #: per changed part.
    touched: Callable[[Any, Any], list[dict[str, Any]]] | None = None
    #: What a new worker process runs before its first task, so the first render is not
    #: the one to pay for the format's imports and font index: a module-level function of
    #: no arguments, registered (:func:`~.worker.worker_task`).  Its time is no call's
    #: (:mod:`.worker`).  ``None``: nothing.
    warm_up: Callable[[], Any] | None = None
    #: What a document keeps beside its bytes for being opened again -- what the file
    #: cannot hold without changing what Office reads (a Word document's retired ids):
    #: ``state(document) -> str``, opaque to the tool layer, describing the document as it
    #: is now.  :meth:`Session.save` and a save tool's output (:attr:`Output.state`) carry
    #: it; :meth:`Session.open`'s ``state`` hands it back.  ``None``: the format keeps none.
    state: Callable[[Any], Any] | None = None
    #: Opens bytes with the state saved beside them: ``open_with_state(data, state) ->
    #: document``.  It refuses a state that is not these bytes' (an
    #: :class:`~ooxml_edit.errors.ArgumentError` saying what to pass instead).
    open_with_state: Callable[[bytes, Any], Any] | None = None
    #: Round 45: reads of a committed version.  ``read_copy(document) -> make``, called
    #: under the document's lock at a version no edit is changing (when a changing call
    #: has ended, or a read finds none taken yet): what a copy of the document at this
    #: version needs, taken now; ``make(data)``, called later with no lock held and the
    #: version's bytes, opens that copy -- one that answers every reading call exactly as
    #: the document did at that version (its ids, its version, its settings) and shares the
    #: document's caches that are keyed by content.  Reading calls work on the copy
    #: (:meth:`Session.read_lock`) and never wait for an edit in flight; the copy is never
    #: changed.  ``None``: reads take the document's lock, as before.
    read_copy: Callable[[Any], Callable[[bytes], Any]] | None = None
    #: Round 45 (lane B): edits of different blocks of one document at the same time -- a
    #: :class:`~.blocks.BlockEdits`: which calls may be prepared on a draft (an editable
    #: copy of the committed version, :attr:`read_copy`'s), what such a call changed (a
    #: patch), whether the patch still applies to the document as it is now, and applying
    #: it, under the document's lock, as the change the call would have made there
    #: (:mod:`.blocks` says how, and what is checked).  ``None``: every changing call takes
    #: the document's lock for its whole run, as before.
    blocks: Any = None


#: :attr:`DocumentEntry.render_keys` and :attr:`DocumentEntry.font_keys`, in entries.
RENDER_KEYS = 4096
FONT_KEYS = 256


class LRU:
    """A small least-recently-used cache, safe to share between threads (round 45: a
    document's caches are shared by the calls reading its committed version, which do not
    hold its lock)."""

    def __init__(self, size: int) -> None:
        self.size = size
        self._items: OrderedDict[Hashable, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Hashable, default: Any = None) -> Any:
        with self._lock:
            if key not in self._items:
                return default
            self._items.move_to_end(key)
            return self._items[key]

    def put(self, key: Hashable, value: Any) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.size:
                self._items.popitem(last=False)

    def __contains__(self, key: Hashable) -> bool:
        with self._lock:
            return key in self._items

    def keys(self) -> list[Hashable]:
        """The keys, least recently used first."""
        with self._lock:
            return list(self._items)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def clear(self) -> None:
        with self._lock:
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
    #: The document's state beside these bytes (:attr:`DocumentFormat.state`), when the
    #: output is the document itself and its format keeps one: store it with the bytes and
    #: pass it to :meth:`Session.open` (``state=``) to open them again.  Never in
    #: :meth:`describe`: the model does not see it.
    state: Any = field(default=None, repr=False)

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "format": self.format, "size": len(self.data),
                "doc": self.doc, "validate": self.validate}


@dataclass(frozen=True)
class SavedDocument:
    """An open document's bytes and the state to open them again with, taken at one
    version under its lock (:meth:`Session.save`)."""

    doc: str
    name: str
    kind: str
    version: int
    data: bytes = field(repr=False)
    #: :attr:`DocumentFormat.state` of the document at :attr:`version`; ``None`` when the
    #: format keeps none.  Opaque: store it as it is, beside :attr:`data`.
    state: Any = field(default=None, repr=False)


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
    #: Round 45: the last committed version, for reads (:meth:`commit`, :meth:`reader`).
    committed: "Committed | None" = field(default=None, repr=False)
    #: A reader's (:meth:`reader`): the version it is of, and the entry it reads for.
    pinned: int | None = field(default=None, repr=False)
    live: "DocumentEntry | None" = field(default=None, repr=False)
    #: Round 45 (lane B): the calls naming this document that are running now, this one
    #: included (``Toolbox`` counts them, :meth:`enter`).
    in_flight: int = field(default=0, repr=False)
    #: Round 45 (lane B): the changing calls editing this document now -- preparing or
    #: applying their change, not yet at their checks (:meth:`begin_edit`): with
    #: ``block_edits="auto"``, a call is prepared on a draft only beside another one.
    editing: int = field(default=0, repr=False)
    #: Round 45 (lane B): the calls preparing a draft of this document now, each since when
    #: (``time.monotonic``), until their change is applied or they take the lock instead
    #: (:meth:`draft_began`): a call that takes the document's lock first waits for those
    #: in flight longer than ``Limits.block_redo_cpu`` (:meth:`wait_for_long_drafts`).
    drafts_in_flight: dict[int, float] = field(default_factory=dict, repr=False)
    _drafts_ended: threading.Condition = field(
        default_factory=lambda: threading.Condition(threading.Lock()), repr=False)
    _draft_tokens: Any = field(default_factory=lambda: itertools.count(1), repr=False)
    _flight_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    #: Round 45 (lane B): the changes the dispatcher made to the document, counted
    #: (:meth:`note_change`): the count, the version the last one left, and each part's
    #: count when it last changed -- what a patch prepared on an earlier version checks
    #: (:meth:`parts_changed_since`).  ``_all_changed``: the count at which a change the
    #: dispatcher did not see (the application's own, through the library) was found:
    #: every part may have changed then.
    change_seq: int = field(default=0, repr=False)
    change_version: int | None = field(default=None, repr=False)
    part_stamps: dict[str, int] = field(default_factory=dict, repr=False)
    _all_changed: int = field(default=0, repr=False)
    #: Round 47 (lane B): the latest changes counted (:meth:`note_change`), each ``(version
    #: before, version after, Change)`` -- ``None`` for one not known (the application's own,
    #: through the library) -- for what a read learns changed since its version
    #: (:meth:`changes_between`).  The history keeps the states they read.
    changes_kept: deque = field(default_factory=lambda: deque(maxlen=CHANGES_KEPT), repr=False)

    @property
    def history(self) -> Any:
        return getattr(self.document, "history", None)

    @property
    def version(self) -> int:
        """The document's state number: its history's version (LE1), else a local count."""
        if self.pinned is not None:
            return self.pinned
        history = self.history
        if history is not None and hasattr(history, "version"):
            return history.version
        return self._own_version

    def bump(self) -> None:
        """Count a change in a document without a history (the dispatcher calls this)."""
        if self.pinned is not None:
            return
        if self.history is None or not hasattr(self.history, "version"):
            self._own_version += 1

    # -- reads of the committed version (round 45) ------------------------------------------

    def commit(self, fmt: "DocumentFormat | None") -> None:
        """Keep this version as the committed one, for reads that do not wait for the edit
        after it (:meth:`reader`): its bytes (:meth:`snapshot`), its refs and what the
        format's copy needs (:attr:`DocumentFormat.read_copy`).  Under the document's lock,
        at a version no edit is changing; once per version.  A format without
        ``read_copy``, or one that cannot copy this document, keeps nothing."""
        read_copy = getattr(fmt, "read_copy", None)
        if read_copy is None or self.pinned is not None:
            return
        version = self.version
        committed = self.committed
        if committed is not None and committed.version == version:
            return
        try:
            make = read_copy(self.document)
            data = self.snapshot()
        except Exception:  # noqa: BLE001 -- reads take the lock, as before
            self.committed = None
            return
        self.committed = Committed(version, data, make, dict(self.refs),
                                   dict(self.ref_states.get(version, self.refs)),
                                   tracking=self.tracking, owners=dict(self.owners),
                                   seq=self.change_seq)

    # -- calls in flight, and the changes made (round 45, lane B) ---------------------------

    def enter(self) -> int:
        """Count a call naming this document as running; how many run now, it included."""
        with self._flight_lock:
            self.in_flight += 1
            return self.in_flight

    def leave(self) -> None:
        """The call :meth:`enter` counted has ended."""
        with self._flight_lock:
            self.in_flight -= 1

    def begin_edit(self) -> int:
        """Count a changing call as editing this document (until :meth:`end_edit`: its
        change made, before its checks); how many edit now, it included."""
        with self._flight_lock:
            self.editing += 1
            return self.editing

    def end_edit(self) -> None:
        """The edit :meth:`begin_edit` counted is done."""
        with self._flight_lock:
            self.editing -= 1

    def draft_began(self) -> int:
        """Count a call as preparing a draft of this document from now; its token, for
        :meth:`draft_ended`."""
        with self._drafts_ended:
            token = next(self._draft_tokens)
            self.drafts_in_flight[token] = time.monotonic()
            return token

    def draft_ended(self, token: int) -> None:
        """The draft :meth:`draft_began` counted is done: applied, or the call takes the
        lock instead, or it failed."""
        with self._drafts_ended:
            if self.drafts_in_flight.pop(token, None) is not None:
                self._drafts_ended.notify_all()

    def wait_for_long_drafts(self, longer_than: float) -> float:
        """Before a call takes this document's lock for its whole run: wait until every
        draft in flight now for longer than ``longer_than`` seconds has ended (a long
        preparation that a whole-document change would only make run again), never for
        one begun or turned long meanwhile, so the wait is bounded by those drafts alone.
        Not when this thread holds the lock already (their changes wait for it).  The
        seconds waited."""
        owned = getattr(self.lock, "_is_owned", None)     # threading.RLock's own test
        if owned is not None and owned():
            return 0.0
        with self._drafts_ended:
            now = time.monotonic()
            waiting = {token for token, since in self.drafts_in_flight.items() if now - since >= longer_than}
            while waiting & self.drafts_in_flight.keys():
                self._drafts_ended.wait()
            return time.monotonic() - now

    def note_change(self, parts: Iterable[str] | None, before: int | None, change: Any = None) -> None:
        """Count a change the dispatcher made, under the document's lock: ``parts`` the
        parts whose bytes it changed (``None``: not known -- every part), ``before`` the
        version the document had when the call began, ``change`` the
        :class:`~ooxml_edit.history.Change` (kept, :meth:`changes_between`).  A version then
        that is not the one the last counted change left means a change the dispatcher did
        not make came between (the application's, through the library): every part may
        have changed."""
        self.change_seq += 1
        with self._flight_lock:
            if before is not None and self.change_version is not None and before != self.change_version:
                self.changes_kept.append((self.change_version, before, None))
            self.changes_kept.append((before, self.version, change if parts is not None else None))
        if parts is None or before != self.change_version:
            self._all_changed = self.change_seq
        for part in parts or ():
            self.part_stamps[part] = self.change_seq
        self.change_version = self.version

    def changes_between(self, used: int, now: int) -> list[Any] | None:
        """The changes that took this document from version ``used`` to ``now`` (the
        latest way there: equal versions are equal content), each a
        :class:`~ooxml_edit.history.Change`, ``None`` for one not known; ``None`` when they
        are not all kept (:attr:`changes_kept`), or ``now`` is not this document's version
        then reached."""
        if used == now:
            return []
        with self._flight_lock:
            kept = list(self.changes_kept)
        found: list[Any] = []
        current = now
        for before, after, change in reversed(kept):
            if after != current:
                continue
            found.append(change)
            if before is None:
                return None
            current = before
            if current == used:
                found.reverse()
                return found
        return None

    def parts_changed_since(self, seq: int) -> frozenset[str] | None:
        """The parts the counted changes after count ``seq`` changed, under the document's
        lock; ``None`` when that cannot be said -- a change the dispatcher did not make came
        after it, or since the last counted one (the version is not the one it left)."""
        if self._all_changed > seq or self.version != self.change_version:
            return None
        return frozenset(part for part, stamp in self.part_stamps.items() if stamp > seq)

    def drafted(self, committed: "Committed", document: Any) -> "DocumentEntry":
        """An entry for a draft of this document (:mod:`.blocks`): ``document`` an editable
        copy of the committed version, with the version's refs, owners and tracking, this
        entry's retry keys, caches of its own (its versions are not the document's) and
        its own lock.  Calls run on it as on the document; nothing in it is the session's
        until the dispatcher applies its change to the document."""
        entry = DocumentEntry(
            doc_id=self.doc_id, kind=self.kind, name=self.name, source=self.source,
            document=document, size=self.size, font_dirs=self.font_dirs, locale=self.locale,
            baseline_problems=self.baseline_problems, tracking=committed.tracking,
            render_cache=LRU(1), check_cache=LRU(1), render_keys=LRU(16), font_keys=LRU(16),
            keys=dict(self.keys), refs=dict(committed.refs), owners=dict(committed.owners))
        entry.ref_states[committed.version] = dict(committed.ref_state)
        entry._snapshot = (committed.version, committed.data)
        return entry

    def reader(self) -> "DocumentEntry | None":
        """The entry reads work on: a copy of the document at its committed version, when
        that is the version now (:meth:`commit`) -- taken without the document's lock, so
        a read never waits for an edit in flight, which changes the document, not the copy.
        ``None``: no committed version is the one now (read under the lock)."""
        committed = self.committed
        if committed is None or self.pinned is not None or committed.version != self.version:
            return None
        return committed.entry(self)

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
    def savepoint(self, start: Any = None) -> Iterator[bool]:
        """Inside :meth:`batch`: roll back what the code inside changed if it raises, the
        batch staying open (the document's ``savepoint()``, or its history's:
        :meth:`~ooxml_edit.history.History.savepoint`).  Yields whether a rollback is
        possible; a document with neither keeps what a failing edit did.  ``start``: the
        state now when the caller just took it (:meth:`mark`), for the history's own."""
        savepoint = getattr(self.document, "savepoint", None)
        if savepoint is not None:
            with savepoint():
                yield True
            return
        if self.history is not None and hasattr(self.history, "savepoint"):
            with (self.history.savepoint(start) if start is not None
                  else self.history.savepoint()):
                yield True
            return
        yield False

    @contextmanager
    def watch(self) -> Iterator[Any]:
        """What the document's content did while the block ran: its history's
        :meth:`~ooxml_edit.history.History.watch`, a
        :class:`~ooxml_edit.history.Change`; ``None`` for a document whose history
        cannot say."""
        history = self.history
        if history is None or not hasattr(history, "watch"):
            yield None
            return
        with history.watch() as change:
            yield change

    def mark(self) -> Any:
        """The document's state now (:meth:`~ooxml_edit.history.History.mark`), for
        comparing the ops of a batch; ``None`` for a document whose history cannot."""
        history = self.history
        if history is None or not getattr(history, "compares", False):
            return None
        return history.mark()

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
        found = cache.get(full, _MISSING)
        if found is not _MISSING:
            return found
        value = compute()
        cache.put(full, value)
        return value

    def describe(self) -> dict[str, Any]:
        return {"doc": self.doc_id, "kind": self.kind, "name": self.name, "source": self.source,
                "version": self.version}


_MISSING = object()


class Committed:
    """A committed version of a document, for its reads (:meth:`DocumentEntry.commit`):
    the version, its bytes, the format's ``make`` for a copy, the refs then; the reader
    entry made from them once, when a read first asks (:meth:`entry`)."""

    def __init__(self, version: int, data: bytes, make: Callable[[bytes], Any],
                 refs: dict[str, str], ref_state: dict[str, str], *,
                 tracking: dict[str, Any] | None = None,
                 owners: dict[int, StepOwner] | None = None, seq: int = 0) -> None:
        self.version = version
        self.tracking = tracking
        self.owners = owners or {}
        #: The document's change count (:attr:`DocumentEntry.change_seq`) when this version
        #: was committed: a patch prepared on it checks the parts changed since
        #: (:meth:`DocumentEntry.parts_changed_since`).
        self.seq = seq
        self.data = data
        self.make = make
        self.refs = refs
        self.ref_state = ref_state
        self._entry: DocumentEntry | None = None
        self._failed = False
        self._lock = threading.Lock()

    def entry(self, live: DocumentEntry) -> DocumentEntry | None:
        """The reader entry: the copy, pinned to :attr:`version`, with a lock of its own,
        sharing ``live``'s caches (keyed by version: equal versions are equal content) and
        call log.  ``None`` when the copy cannot be made (reads take the lock)."""
        with self._lock:
            if self._entry is None and not self._failed:
                try:
                    document = self.make(self.data)
                except Exception:  # noqa: BLE001 -- reads take the lock, as before
                    self._failed = True
                    return None
                entry = DocumentEntry(
                    doc_id=live.doc_id, kind=live.kind, name=live.name, source=live.source,
                    document=document, size=live.size, font_dirs=live.font_dirs,
                    locale=live.locale, baseline_problems=live.baseline_problems,
                    tracking=self.tracking, render_cache=live.render_cache,
                    check_cache=live.check_cache, render_keys=live.render_keys,
                    font_keys=live.font_keys, log=live.log, refs=dict(self.refs),
                    owners=dict(self.owners), pinned=self.version, live=live)
                entry.ref_states[self.version] = dict(self.ref_state)
                entry._snapshot = (self.version, self.data)
                self._entry = entry
            return self._entry


#: Changes kept per document for what a read learns changed since its version
#: (:meth:`DocumentEntry.changes_between`, round 47).
CHANGES_KEPT = 32

_DOC_ID = re.compile(r"^d(\d+)$")
#: The ids an application may give a document itself: ``d`` and a whole number from 1,
#: no leading zero -- the form the session's own ids have, which addresses read
#: (``d2/p:A``).
_GIVEN_DOC_ID = re.compile(r"d[1-9][0-9]*")


class StateRefused(ArgumentError):
    """:meth:`Session.open` refused the ``state`` it was given: not the state of these bytes,
    or one their format does not keep.  The message says what to pass instead."""


class _DocIdTaken(ArgumentError):
    """An id given to :meth:`Session.open` is open already (a race :meth:`Session.open_blob`
    must not mistake for a bad blob)."""


def _given_doc_id(doc_id: Any) -> str:
    """``doc_id`` as an application gives it to :meth:`Session.open`, checked: ``d1``,
    ``d2``, ...; anything else an :class:`ArgumentError` saying the accepted form."""
    if not isinstance(doc_id, str):
        raise ArgumentTypeError(f"doc_id takes a string such as 'd1', not {type(doc_id).__name__}")
    if _GIVEN_DOC_ID.fullmatch(doc_id) is None:
        raise ArgumentError(f"doc_id {doc_id!r} is not a document id: 'd' and a whole number "
                            "from 1 with no leading zero ('d1', 'd2', 'd17'), the form the "
                            "session gives and addresses such as 'd2/p:A' read")
    return doc_id


def part_entries(change: Any) -> list[dict[str, Any]]:
    """What a format without its own ``touched`` reports: ``{"part": name}`` for every part
    whose bytes changed, in name order."""
    return [{"part": part} for part in sorted(change.parts)]


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
            raise ArgumentTypeError(f"font_dirs takes paths, not {type(path).__name__}")
        out.append(fspath(path))
    return tuple(out)


#: The attributes a locale has (:class:`ooxml_common.locales.Locale`'s fields), each a
#: string: all :func:`locale_of` checks of an object it is given.
LOCALE_FIELDS = ("decimal_separator", "thousands_separator", "date_order", "language",
                 "list_separator")

#: The old name of the default's class, kept for one release: the default is now
#: :data:`ooxml_common.locales.EN_US` itself, a :class:`~ooxml_common.locales.Locale`.
DefaultLocale = Locale

#: The locale of every session given none: en-US, always -- never the operating system's.
#: :data:`ooxml_common.locales.EN_US` itself.
DEFAULT_LOCALE = EN_US


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
        raise ArgumentTypeError(f"locale takes an ooxml_common.locales.Locale (or an object with "
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
    ``numbers`` is the application's rules for ``check``'s numbers -- its ignore list,
    whole numbers -- a :class:`~.numbers.NumberRules` (or a mapping of its fields);
    ``None``, the default, is :data:`~.numbers.DEFAULT_RULES`.
    :meth:`view` gives an agent the same session with fewer roles.
    """

    #: The application's context of the calls made here: none (a :class:`SessionView`
    #: carries one).
    context: Mapping[str, Any] = MappingProxyType({})

    def __init__(self, formats: Iterable[DocumentFormat] = (), *, clock: Clock | None = None,
                 limits: Limits | None = None, on_output: Callable[[Output], None] | None = None,
                 session_id: str | None = None, font_dirs: Any = None,
                 locale: Any = None, roles: Any = None, numbers: Any = None) -> None:
        self.id = session_id or uuid.uuid4().hex[:12]
        #: The roles whose calls the toolbox runs here; a call outside them is refused.
        self.roles: tuple[str, ...] = role_set(roles)
        #: The application's own font folders, as path strings; ``None``: the default.
        self.font_dirs: tuple[str, ...] | None = font_dirs_of(font_dirs)
        #: The regional settings of the session's documents; en-US unless given.
        self.locale: Any = locale_of(locale)
        #: The application's rules for ``check``'s numbers (:class:`~.numbers.NumberRules`).
        self.numbers: NumberRules = rules_of(numbers)
        #: The index of values a check of numbers built last, and what it was built of.
        self._number_index: Any = None
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
        #: Ids the application gave (:meth:`open`'s ``doc_id``): never handed out by the
        #: counter, open or closed.
        self._given_ids: set[str] = set()
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
             source: str = "app", doc_id: str | None = None, state: Any = None) -> str:
        """Open a document from bytes and return its id (``d1``).

        ``doc_id`` reopens it under an id of the application's choosing -- the one a resumed
        run's saved conversation names -- instead of the next one counted: ``d`` and a whole
        number from 1 (``d3``).  An id that is open is refused (an :class:`ArgumentError`
        naming the document that has it); a closed one is free again.  The counter never
        hands out an id given here: after ``open(..., doc_id="d3")`` the counted ids skip
        ``d3``.

        ``state`` is what was saved beside these bytes (:meth:`save`'s
        :attr:`SavedDocument.state`, a save tool's :attr:`Output.state`): the document goes
        on from where it was (a Word document's retired ids stay retired).  The format
        refuses a state that is not these bytes' (:attr:`DocumentFormat.open_with_state`);
        ``None`` (or an empty value) opens the bytes alone, as before."""
        data = _require_bytes(data, "open")
        if doc_id is not None:
            self._free_id(_given_doc_id(doc_id))
        fmt = self._format_for(data, name, kind)
        if isinstance(state, (str, bytes, dict, list, tuple)) and not state:
            state = None                        # "" or {}: no state given
        open_with_state = fmt.open_with_state
        if state is not None and open_with_state is None:
            raise StateRefused(f"{name} is a {fmt.kind} document, and that format keeps no state "
                                "beside its bytes: open it without state")
        check_package(data, self.limits, name)
        self._room_for_document(len(data))
        if state is None or open_with_state is None:
            document = fmt.open(data)
        else:
            try:
                document = open_with_state(data, state)
            except ArgumentError as exc:
                raise StateRefused(str(exc)) from exc
        return self.adopt(document, fmt.kind, name, source=source, size=len(data),
                          doc_id=doc_id)

    def adopt(self, document: Any, kind: str, name: str, *, source: str = "new",
              size: int = 0, doc_id: str | None = None) -> str:
        """Add a document made in memory (a new one, or one opened from a blob); ``doc_id``
        as :meth:`open` takes it."""
        if doc_id is not None:
            doc_id = _given_doc_id(doc_id)
        if kind not in self.formats:
            raise ArgumentError(f"no format {kind!r} is registered; one of {sorted(self.formats)}")
        fmt = self.formats[kind]
        if fmt.configure is not None:
            fmt.configure(document, self)
        baseline = list(fmt.problems(document)) if fmt.problems else []
        with self._lock:
            self._room_for_document(size)
            if doc_id is None:
                doc_id = self._counted_id()
            else:
                self._free_id(doc_id)
                self._given_ids.add(doc_id)
            self.documents[doc_id] = DocumentEntry(
                doc_id=doc_id, kind=kind, name=name, source=source, document=document,
                size=size, baseline_problems=baseline, font_dirs=self.font_dirs,
                locale=self.locale,
                render_cache=LRU(self.limits.render_cache_size),
                check_cache=LRU(self.limits.check_cache_size))
            entry = self.documents[doc_id]
            entry.ref_states[entry.version] = {}
            entry.change_version = entry.version
        return doc_id

    def _counted_id(self) -> str:
        """The next id the counter gives: one never given before, by it or by the
        application.  Called with :attr:`_lock` held."""
        while True:
            doc_id = f"d{next(self._doc_numbers)}"
            if doc_id not in self._given_ids and doc_id not in self.documents:
                return doc_id

    def _free_id(self, doc_id: str) -> None:
        """Refuse ``doc_id`` when a document has it, saying which and how to free it."""
        with self._lock:
            held = self.documents.get(doc_id)
            if held is None:
                return
            others = sorted((d for d in self.documents if d != doc_id), key=doc_order)
            raise _DocIdTaken(
                f"doc_id {doc_id!r} is in use: {held.name} ({held.kind}) is open as {doc_id}; "
                f"close it first (session.close({doc_id!r}), after saving it if it changed), or "
                "open this one without doc_id for the next free id"
                + (f" (open now: {', '.join([doc_id, *others])})" if others else ""))

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

    def save(self, doc_id: str) -> SavedDocument:
        """The open document ``doc_id``'s bytes (``document.to_bytes()``) and the state to
        open them again with (:attr:`DocumentFormat.state`; ``None`` for a format that keeps
        none), both of one version, under the document's lock: store the two together, and
        reopen with ``open(saved.data, saved.name, doc_id=saved.doc, state=saved.state)``.
        Writes nothing anywhere: the application keeps them where it keeps its run."""
        entry = self.entry(doc_id)
        fmt = self.formats.get(entry.kind)
        with entry.lock:
            data = entry.snapshot()
            state = fmt.state(entry.document) if fmt is not None and fmt.state is not None else None
            return SavedDocument(doc=doc_id, name=entry.name, kind=entry.kind,
                                 version=entry.version, data=data, state=state)

    def close(self, doc_id: str) -> list[str]:
        """Close a document; the addresses of the datasets derived from it, which stay,
        each as stale or current as it is now, for good."""
        entry = self.entry(doc_id)
        with entry.lock, self._lock:
            self.documents.pop(doc_id, None)
            kept = []
            for dataset in self.datasets.values():
                if dataset.doc == doc_id and not dataset.closed:
                    kept.append(dataset.address)
                dataset.document_closed(doc_id)     # its own source, and any it also read
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

    @contextmanager
    def read_lock(self, doc_ids: Iterable[str]) -> Iterator[list[DocumentEntry]]:
        """:meth:`lock` for a call that only reads (round 45): each document's committed
        version (:meth:`DocumentEntry.reader`), its copy's lock held instead of the
        document's -- so the read never waits for an edit in flight, nor an edit for it --
        where its format copies documents (:attr:`DocumentFormat.read_copy`).  A document
        with no committed version yet takes its lock as before, keeps the version now as
        committed (:meth:`DocumentEntry.commit`) and lets the lock go for the copy.  Taken
        in :func:`doc_order`, as :meth:`lock`'s; the entries are readers or documents."""
        ordered = sorted(set(doc_ids), key=doc_order)
        entries = [self.entry(doc_id) for doc_id in ordered]
        with ExitStack() as stack:
            taken: list[DocumentEntry] = []
            for entry in entries:
                fmt = self.formats.get(entry.kind)
                copies = getattr(fmt, "read_copy", None) is not None
                reader = entry.reader() if copies else None
                if reader is None and copies:
                    with entry.lock:
                        entry.commit(fmt)
                        reader = entry.reader()
                if reader is None:
                    # A format that does not copy, or could not copy it: under its lock.
                    stack.enter_context(entry.lock)
                    taken.append(entry)
                    continue
                stack.enter_context(reader.lock)
                taken.append(reader)
            yield taken

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
            # (fmt and its undo_scope are there whenever scope is: checked above.)
            scope_parts = (frozenset(fmt.undo_scope(entry.document, scope))
                           if scope is not None and fmt is not None
                           and fmt.undo_scope is not None else None)
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

    def open_blob(self, handle: str, *, kind: str | None = None,
                  doc_id: str | None = None, state: Any = None) -> str:
        """Open a blob (:meth:`add_blob`) as a document (what an ``open_document`` tool does);
        ``doc_id`` and ``state`` as :meth:`open` takes them, an application's alone (the tool
        has neither)."""
        blob = self.blob(handle)
        if doc_id is not None:
            self._free_id(_given_doc_id(doc_id))
        try:
            return self.open(blob.data, blob.name, kind=kind, source=handle, doc_id=doc_id,
                             state=state)
        except (_DocIdTaken, StateRefused):
            raise                               # the application's argument, not the blob's fault
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

    # -- refs --------------------------------------------------------------------------------

    def refs(self, doc_id: str) -> dict[str, str]:
        """The refs of the open document ``doc_id``: ``{name: address}``, each ``$name`` its
        calls defined and the address it stands for (``{"intro": "item:3"}``), as they
        are now -- an undo takes back the refs of what it undoes.  A copy, read under the
        document's lock: changing it changes nothing.  For the application outside a call
        (a handler reads :meth:`~.dispatch.Call.refs`, which has the call's own too);
        ``not_found`` names the open documents."""
        entry = self.entry(doc_id)
        with entry.lock:
            return dict(entry.refs)

    def resolve_ref(self, doc_id: str, value: str) -> str:
        """``value`` as an address in ``doc_id``: a leading ``$name`` replaced by the address
        it names, ``$name/rest`` by that address and ``/rest``; any other value as given.
        Read as a call reads it (:meth:`~.dispatch.Call.resolve_ref`): a camelCase or
        hyphenated ``$recNow`` that folds to a ref (:func:`fold_ref`) is that ref -- outside a
        call no warning can be given, so it is read silently -- and a ``$Name`` that is no
        ref name but stands for one (``$catChart`` for ``catchart``) is ``not_found`` asking
        whether that one was meant. ``not_found`` for a name the document has not, with its
        refs as the valid options."""
        if not isinstance(value, str):
            return value
        known = self.refs(doc_id)
        match = REF_USE.match(value)
        if match is None:
            folded = _folded_use(value, known)
            if folded is not None:
                match = REF_USE.match(folded)
        if match is None:
            meant = _misspelt_ref(value, known)
            if meant is not None:
                raise ToolError("not_found", f"{value!r} is no ref; did you mean ${meant}? "
                                "A ref is lowercase",
                                valid_options=[f"${key}" for key in sorted(known)])
            return value
        name, rest = match.group(1), match.group(2) or ""
        if name not in known:
            raise ToolError("not_found", f"no ref ${name} in {doc_id}",
                            valid_options=[f"${key}" for key in sorted(known)])
        return known[name] + rest

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
                for doc in {source.doc for source in (entry, *entry.also)}:
                    if doc is not None and doc not in self.documents:
                        entry.document_closed(doc)   # closed while the call ran (a batch)
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

    def release_images(self, count: int) -> None:
        """Give back ``count`` images :meth:`reserve_images` counted that the model is never
        sent (a ``read_batch`` op's that did not fit its reply, round 35): the budgets count
        what is shown."""
        with self._lock:
            self.images_used = max(0, self.images_used - count)
            self.images_this_round = max(0, self.images_this_round - count)

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
                raise ArgumentError(f"no format {kind!r}; one of {sorted(self.formats)}")
            return self.formats[kind]
        for fmt in self.formats.values():
            if fmt.detect(data, name):
                return fmt
        raise ArgumentError(f"{name} is not a document of any registered format "
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
            raise ArgumentTypeError(f"a view is of a Session, not {type(session).__name__}")
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
            raise ArgumentError(f"{', '.join(extra)} is not among this session's roles "
                                f"({', '.join(roles)}): a view or a call narrows the session's "
                                "roles, never widens them -- make the session with every role "
                                "its agents need (toolbox.session(roles=...)) and give each "
                                "agent a view of some of them")
    if more is None:
        return found, context
    if not isinstance(more, Mapping):
        raise ArgumentTypeError(f"context takes a mapping (a dict), not {type(more).__name__}")
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
        raise ArgumentTypeError(f"{what} takes bytes, not {kind}: the tool layer reads no files")
    return data
