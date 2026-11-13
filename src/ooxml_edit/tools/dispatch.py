"""The toolbox: definitions out, calls in, results back.

:class:`Toolbox` holds the tools of one or more format libraries and the formats they
describe, makes sessions, emits provider definitions, and runs calls.  A call goes:

1. the tool is looked up (an unknown name is ``invalid_arguments``, with the near names);
2. the arguments -- a dict, or the JSON string OpenAI sends; an array or object argument
   written as a JSON string is parsed too -- are validated against the
   tool's schema, bounds and either/or rules included, whatever the provider's strict mode
   did (``invalid_arguments`` names the field and the allowed values);
3. the documents the call names are locked, in a fixed order;
4. the handler runs; a mutating call runs inside the document's batch, so it is one undo
   step and an exception rolls it back whole (a changing tool's reading mode, such as
   ``edit_chart`` with ``action: "read"``, runs as a reading call: ``Tool.reads``);
5. warnings the libraries raise are collected into the result, never printed; exceptions
   become error results with a code and, where there is a closed set, ``valid_options``.

:meth:`Toolbox.dispatch_many` runs one assistant turn's parallel calls: calls on the same
document serially in the order the model emitted them -- its reads side by side, an edit
once the reads before it hold their locks (round 28) -- calls on different documents at
the same time, results in call order.

**Refs.**  A creating call may name what it makes (``call.define_ref("box1", "256.7")``);
the name is kept per document for the session, listed in the result's ``refs``, and a later
call may write ``$box1`` (or ``$box1/p0``) wherever its tool declares a target
(``Tool.refs``).  The dispatcher replaces it with the address before the handler runs.

**Datasets.**  A call may keep a table of values it derived for the session
(``call.define_dataset("by_region", dataset)``, :class:`~.datasets.Dataset`); a later call,
on any document, may name it ``d:by_region`` wherever its tool declares a dataset
(``Tool.datasets``).  The dispatcher checks the id exists before the handler runs -- an
unknown one is ``not_found`` with the known ids -- and warns when a dataset a call names is
stale (its source changed since).  The handler reads it with ``call.dataset(value)``.

**Sources.**  A tool that takes rows (``Tool.sources``: a chart's data) may name a dataset
or an address in another open document that its format resolves (``t:sales``, or
``d2/t:sales`` naming the document; :mod:`.sources`).  The dispatcher qualifies the address
with its document before the call runs and locks that document too, for reading: no undo
step, no version, no checks.  The handler reads either with ``call.source(value)``.

**Text facts.**  A tool may declare text arguments that hold references to one value of a
source (``Tool.facts``; ``{{d:by_topic/door/mean}}``, :mod:`.textfacts`).  The dispatcher
qualifies and locks their documents as for sources, writes each value in its reference's
place before the handler runs (``call.facts`` says what went where) and lists them in the
result's ``data.facts``; a reference that names no single value refuses the call.

**Checks.**  Handlers say what they touched (``call.touch(page)``); after a changing call
the dispatcher asks the document's format for the facts (``DocumentFormat.checks``) once --
where the format copies documents (``DocumentFormat.read_copy``, round 45), on the copy of
the version the call made, once its locks are let go (:class:`After`).

**Reads of the committed version** (round 45).  A call that only reads -- a reading call of
a tool of no role of its own, or a ``read_batch`` -- works on each document's last
committed version (:meth:`~.session.Session.read_lock`) where the format copies documents:
it never waits for an edit in flight.  A changing call keeps the version it leaves as
committed before it lets its locks go.

**What changed.**  Every call watches its document (:meth:`DocumentEntry.watch`); the
parts whose bytes changed, in the format's addresses (``DocumentFormat.touched``), are the
result's ``touched`` -- for the application, never the model -- whatever the tool did, so
no handler says it.  A batch watches each document it changes for the union, and marks the
state before each op that changes one (:meth:`DocumentEntry.mark`) for ``touched_by_op``.

**Batch.**  The shared ``batch`` tool runs other calls (*ops*) in order under every lock they
need, inside one undo step per document.  Each op stands on its own: one that fails -- an
unknown tool, arguments its schema refuses, an address that names nothing, an error while
it runs -- is rolled back alone (:meth:`DocumentEntry.savepoint`) and reported with its
error, and the ops after it still run; what applied is one undo step and one version.  A
``$ref`` that a failed op would have made fails naming that op.  Only when no op applied is
the result an error.  ``atomic: true`` makes it all or none instead: the first failing op
rolls the whole batch back.  Each op is validated against its own tool's schema --
``batch`` itself is never sent strict, since its ``arguments`` are free-form objects -- so a
model's malformed op is caught by the dispatcher, not by constrained decoding.  That is the
trade-off: one round trip instead of forty, at the price of strict decoding for the ops.
Checks run once, at the end.  The reading ops after the last op that changes a document
(round 33: a ``check`` at the end) run after that, the undo step closed, each as the same
call alone -- so a slow one lets the locks go (:meth:`Call.unlocked`) -- and one that fails
is reported as its op's error, atomic or not: the batch applied (:meth:`Toolbox._run_trailing`).

**Work ahead.**  A changing tool may name work to do before its change (``Tool.aheads``,
round 33): slow reading -- a layout -- run before the undo step with the locks let go where
the call may (outside a batch), after which the call goes on as if it had just taken them.

**Read batch.**  ``read_batch`` (round 32) runs reading calls the same way, under the locks
of the documents they name, each as it would run alone -- no undo step, no checks -- and
answers each within ``max_result_chars``, all of them within ``max_read_batch_chars``; what
does not fit is listed with the call that asks for it (:meth:`Toolbox._run_reads`).
"""

from __future__ import annotations

import copy
import dataclasses
import difflib
import json
import logging
import re
import threading
import time
import warnings
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from .. import provenance
from ..errors import ArgumentError, ArgumentTypeError, UnitError
from . import adapters, prompts, textfacts
from .hints import action_hints, tool_alias, tool_hints
from .datasets import (DATASET_PREFIX, Dataset, DatasetEntry, check_dataset_id,
                       dataset_id, row_range, rows_text)
from .numbers import NumberRules, rules_of
from .blocks import BlockConflict
from .limits import LimitError, Limits
from .logs import CallRecord, digest, log_call, logger, shape
from .registry import (ACCEPT, ANY_ROLE, CORE, EDIT, RESOLVE, ROLES, Tool, ToolGroup,
                       merge_tools, role_set)
from .results import (Image, Result, ToolError, anthropic_image_tokens,
                      openai_image_tokens)
from .schema import CallError, validate_call
from .session import (NO_CONTEXT, REF, REF_USE, Clock, DocumentEntry, DocumentFormat, Output,
                      Session, SessionView, StepOwner, doc_order, fold_ref, font_dirs_of,
                      locale_of, narrowed, part_entries)
from .session import _folded_use, _misspelt_ref
from .sources import Rows, qualify, refusal, resolve as resolve_rows
from .rendercache import ContentStore
from .savegate import validation
from .worker import (InProcess, SubprocessPool, ThreadRunner, WorkerPool, default_workers,
                     gil_enabled, refuse_while_bootstrapping, run_all, takes_affinity,
                     takes_lane)

#: A ref's name (``REF``) and a ref's use, ``$name`` or ``$name/rest`` (:mod:`.session`'s,
#: whose :meth:`~.session.Session.resolve_ref` reads them as a call does).
_REF_USE = REF_USE


class CallContext:
    """What the calls of one dispatch share: a single call's, or every op of a batch's."""

    def __init__(self) -> None:
        #: ``doc_id -> what the handlers touched``, in order, without repeats.
        self.touched: dict[str, list[Any]] = {}
        #: ``doc_id -> {name: address}``: refs defined, not yet kept (a failure drops them).
        self.refs: dict[str, dict[str, str]] = {}
        #: True inside a batch.
        self.batch = False
        #: ``id -> entry``: datasets defined, not yet kept (a failure drops them).
        self.datasets: dict[str, DatasetEntry] = {}
        #: ``id -> the op that defined it`` (0 outside a batch).
        self.dataset_ops: dict[str, int] = {}
        #: Datasets the calls named in declared arguments, by id: warned about when stale.
        self.datasets_used: dict[str, DatasetEntry] = {}
        #: Round 45 (lane B): every session dataset the calls read, by id, and whether it
        #: was stale when first read -- what a call on a draft checks again when its change
        #: is applied (``Toolbox._block_reads_changed``).
        self.datasets_seen: dict[str, tuple[DatasetEntry, bool]] = {}
        #: ``doc_id -> the last op that changed it``, in a batch.
        self.changed_at: dict[str, int] = {}
        #: The op running now (0 outside a batch).
        self.op = 0
        #: ``ref name -> the op that would have made it``: the refs of a batch's failed ops.
        self.failed_refs: dict[str, int] = {}
        #: Round 47: whether a call checked a ref name against the document's refs (whose
        #: it is, :meth:`Call._own_ref`) -- what a call on a draft checks again when its
        #: change is applied, as it does refs it used or made.
        self.refs_read = False
        #: The roles the calls run with: the session's, a view's, or the dispatch's own.
        self.roles: tuple[str, ...] = ROLES
        #: The application's context of the calls (:attr:`Call.caller`); none by default.
        self.caller: Mapping[str, Any] = NO_CONTEXT
        #: In a batch, ``doc_id -> (op, state)``: the state each document was in when the
        #: last op that changes it began (:meth:`DocumentEntry.mark`), for what it changed.
        self.states: dict[str, tuple[int, Any]] = {}
        #: In a batch, ``(op, doc_id, change)`` for each op that changed a document.
        self.op_changes: list[tuple[int, str, Any]] = []
        #: In a batch, ``(op, tool, work, result, at, count)`` for each op that deferred
        #: work (:meth:`Call.defer`): its result, and where its warnings are in the
        #: batch's (``at``, ``count`` of them).
        self.deferred: list[tuple[int, str, list[Callable[[Result], None]], Result, int, int]] = []
        #: Whether an op of a batch deferred work at all (round 45, lane B: work on the
        #: whole document as the batch left it, which a draft's patch does not carry).
        self.deferred_any = False

    def mark(self) -> tuple[Any, ...]:
        """What the ops so far defined and touched, for :meth:`reset` after a failed op."""
        return ({doc: dict(refs) for doc, refs in self.refs.items()}, dict(self.datasets),
                dict(self.dataset_ops), dict(self.datasets_used),
                {doc: list(seen) for doc, seen in self.touched.items()})

    def reset(self, mark: tuple[Any, ...]) -> None:
        """Forget what a failed op defined and touched: back to :meth:`mark`'s state."""
        self.refs, self.datasets, self.dataset_ops, self.datasets_used, self.touched = mark

    def changed_since(self, entry: DatasetEntry) -> bool:
        """Whether a later op of the batch changed the source of a dataset it defined."""
        return entry.doc is not None and \
            self.changed_at.get(entry.doc, -1) > self.dataset_ops.get(entry.id, self.op)

    def pending_stale(self, entry: DatasetEntry) -> bool:
        """Whether a dataset this call (or batch) defined is stale so far: before the call
        ends it has no version to compare (:meth:`changed_since`)."""
        own = entry.stale if entry.version is not None else (
            entry.dataset.invalidated or self.changed_since(entry))
        return own or any(self.pending_stale(other) for other in entry.also)

    def missing_ref(self, name: str, field: str | None) -> ToolError | None:
        """The error for ``$name`` that a failed op of the batch would have made."""
        failed = self.failed_refs.get(name)
        if failed is None:
            return None
        return ToolError("not_found", f"${name} was to be made by ops[{failed}], which "
                         "failed: fix that op and resend both", field=field,
                         details={"op": failed})


#: ``define_dataset``'s ``source`` when not given: the call's own document.
_PRIMARY: Any = object()


def _queues(pool: Any) -> bool:
    """Whether ``pool.run`` takes ``queue_timeout``: this package's runners do."""
    return (isinstance(pool, (WorkerPool, SubprocessPool, ThreadRunner, InProcess))
            and takes_lane(pool))


class Call:
    """What a handler gets: the session, the documents it names, and the means to answer."""

    def __init__(self, toolbox: "Toolbox", session: Session, tool: Tool,
                 arguments: Mapping[str, Any], entries: dict[str, DocumentEntry],
                 primary: DocumentEntry | None, context: CallContext | None = None) -> None:
        self.toolbox = toolbox
        self.session = session
        self.tool = tool
        self.arguments = arguments
        self.entries = entries
        #: The document the call edits or reads first (the first document argument).
        self.entry = primary
        self.result = Result()
        self.context = context or CallContext()
        #: The documents whose locks the dispatcher holds for this call, taken once each
        #: (:meth:`unlocked` may let them go): an op of a ``read_batch``'s, or a batch's
        #: read after its last change, every lock of the batch (round 35); empty inside a
        #: batch's run of changes.
        self.held: list[DocumentEntry] = []
        #: The version of the primary document the result describes, when the handler
        #: worked on a snapshot (:meth:`unlocked`); ``None``: the version after the call.
        self.version_used: int | None = None
        #: Round 47 (lane B): what of the primary document the result is of, where the
        #: handler says (a format's addresses: the blocks a ranged check read); ``None``: the
        #: whole document.  Another call's change made while the locks were let go warns
        #: that the document changed while this call read it only when it touched this
        #: (the format's ``BlockEdits.touches``).
        self.read_scope: Any = None
        #: The values the dispatcher wrote for references in the text arguments
        #: (``Tool.facts``), in order: each a :class:`~.textfacts.Fact` -- the argument,
        #: the value's place in its text as the handler gets it, and its record.
        self.facts: list[textfacts.Fact] = []
        #: Whether :meth:`unlocked` let the locks go at some point: another call may have
        #: changed the documents meanwhile, so what changed is not this call's to report.
        self.released = False
        #: Work for the result after the handler (:meth:`defer`).
        self.deferred: list[Callable[[Result], None]] = []
        #: Whether this is a changing call's work ahead of its change (:attr:`Tool.aheads`,
        #: round 33): nothing is changed yet, so the locks may be let go.
        self.ahead = False

    @property
    def in_batch(self) -> bool:
        return self.context.batch

    @property
    def caller(self) -> Mapping[str, Any]:
        """The application's context of this call, read-only: what the session view it came
        through carries (:meth:`Session.view`) and the dispatch added
        (``Toolbox.dispatch(..., context=...)``) -- say ``{"agent": "reviewer-1"}``, for
        an application's tool to know which of its agents called.  Empty for a call on a
        session itself; the same for every op of a batch.  The model never sees it."""
        return self.context.caller

    @property
    def roles(self) -> tuple[str, ...]:
        """The roles this call runs with (the session's, or its view's): what it was
        checked against."""
        return self.context.roles

    @property
    def changing(self) -> bool:
        """Whether this call changes its document (:meth:`Tool.changes`): false for a
        changing tool's reading mode."""
        return self.tool.changes(self.arguments)

    def defer(self, work: Callable[[Result], None]) -> None:
        """Do ``work(result)`` once the document is as the call leaves it: a call alone runs
        it as the handler returns, a batch after its last op (before the checks), for each
        op in order, each given its op's own result -- whose warnings it may add to, kept
        in their place among the batch's.  For what the whole document has to be read
        (laid out) for: done after every op, a batch would cost the document's size per op;
        deferred, the ops after the first find the reading done (round 28, W8).  What it
        says is of the document after the batch, which the ops after this one may have
        changed."""
        self.deferred.append(work)

    def touch(self, *scopes: Any, doc: str | None = None) -> None:
        """Say what this call changed (pages, blocks): the checks after it cover them."""
        doc = doc or (self.entry.doc_id if self.entry else None)
        if doc is None:
            return
        seen = self.context.touched.setdefault(doc, [])
        for scope in scopes:
            if scope not in seen:
                seen.append(scope)

    def define_ref(self, name: str, address: str, *, doc: str | None = None) -> None:
        """Name ``address`` ``name``, so later calls on the document may write ``$name``.

        A camelCase or hyphenated name (``recNow``, ``scale-note``) is kept as the ref name
        it folds to (:func:`fold_ref`), with a warning -- unless that name already names
        another address: then the call is refused rather than moving that ref."""
        doc = doc or (self.entry.doc_id if self.entry else None)
        if not REF.match(name):
            folded = fold_ref(name)
            if folded is None:
                raise ToolError("invalid_arguments", ref_rule(name), field="ref")
            taken = self.refs(doc).get(folded) if doc is not None else None
            if taken is not None and taken != address:
                raise ToolError("invalid_arguments", f"ref {name!r} reads as {folded!r}, which "
                                f"already names {taken}: give ref {folded!r} to point it here "
                                "instead, or a name of its own", field="ref",
                                details={"ref": folded, "address": taken})
            self.result.warnings.append(f"ref {name!r} read as {folded!r}: write ${folded}")
            name = folded
        if doc is None:
            raise ToolError("invalid_arguments", "a ref needs a document")
        self._own_ref(doc, name, address)
        self.context.refs.setdefault(doc, {})[name] = address
        self.result.refs[name] = address

    def _own_refs_first(self, arguments: Mapping[str, Any]) -> None:
        """Before a changing call's handler runs: refuse a ref name it is given (``ref``, or
        ``ref`` in the objects of a list argument, ``items[].ref``) that another caller's
        ref has (:meth:`_own_ref`) -- what it makes is somewhere new, so it would point that
        ref elsewhere -- before anything is made, so the refusal draws no new ids."""
        if not self.context.caller or self.entry is None:
            return
        names = [arguments.get("ref")]
        for value in arguments.values():
            if isinstance(value, list):
                names += [item.get("ref") for item in value if isinstance(item, Mapping)]
        for name in names:
            if isinstance(name, str):
                folded = name if REF.match(name) else fold_ref(name)
                if folded is not None:
                    self._own_ref(self.entry.doc_id, folded, None)

    def _own_ref(self, doc: str, name: str, address: str | None) -> None:
        """Refuse to point ``name`` at ``address`` (``None``: somewhere new) when another
        caller's ref has that name and names another address (round 47: a ref is its
        definer's, :mod:`.session`).  A call with no context may point any ref, as before; a
        ref this call (or batch) defined already is its own."""
        caller = self.context.caller
        if not caller or name in self.context.refs.get(doc, {}):
            return
        self.context.refs_read = True
        entry = self.entries.get(doc)
        if entry is None or entry.refs.get(name, address) == address:
            return
        owner = entry.ref_owner(name)
        if owner is None or dict(owner) == dict(caller):
            return
        taken = set(self.refs(doc))
        n = 2
        while f"{name[:28]}_{n}" in taken:
            n += 1
        other = f"{name[:28]}_{n}"
        # The owner is not named: a caller's context is the application's, never the model's
        # (Call.caller), and an error's details reach the model.
        raise ToolError("invalid_arguments", f"ref {name!r} was defined by another caller (it "
                        f"names {entry.refs[name]}), and only its definer points it elsewhere: "
                        f"give this one another name, such as {other!r}", field="ref",
                        details={"ref": name, "address": entry.refs[name]},
                        valid_options=[other])

    def refs(self, doc: str | None = None) -> dict[str, str]:
        """Every ref of a document this call can use: kept ones, then this call's own."""
        doc = doc or (self.entry.doc_id if self.entry else None)
        entry = self.entries.get(doc) if doc else None
        known = dict(entry.refs) if entry is not None else {}
        known.update(self.context.refs.get(doc, {}) if doc is not None else {})
        return known

    def resolve_ref(self, value: str, *, doc: str | None = None, field: str | None = None) -> str:
        """``value`` with a leading ``$name`` replaced by its address; other values as given."""
        match = _REF_USE.match(value) if isinstance(value, str) else None
        known = self.refs(doc)
        if match is None:
            folded = _folded_use(value, known, self.context.failed_refs)
            if folded is None:
                return value
            self.result.warnings.append(f"{field + ' ' if field else ''}{value!r} read as "
                                       f"{folded!r}")
            match = _REF_USE.match(folded)
            assert match is not None  # a folded use is a ref use
        name, rest = match.group(1), match.group(2) or ""
        if name not in known:
            here = doc or (self.entry.doc_id if self.entry else None)
            raise self.context.missing_ref(name, field) or ToolError(
                "not_found", f"no ref ${name} in {here}",
                field=field, valid_options=[f"${key}" for key in sorted(known)])
        return known[name] + rest

    def define_dataset(self, id: str, dataset: Dataset, *, source: Any = _PRIMARY,
                       origin: str | None = None, also: Sequence[Any] = ()) -> str:
        """Keep ``dataset`` in the session as ``d:<id>`` once the call succeeds; its address.

        ``source`` is what it was derived from: by default the call's document; another
        document's handle; a :class:`~.datasets.DatasetEntry` (a dataset derived from a
        dataset, which inherits its source); or ``None`` (no document: never stale).  The
        source's version is taken when the call ends, so a changing call defines a dataset
        from the state it leaves.  ``origin``: the address in the source document it was
        derived from (``t:sales``), which ``describe`` lists as ``d3/t:sales``; a dataset
        derived from a dataset has that dataset as its origin unless given.  ``also``: the
        other sources it read (a join's other table or dataset), each a document's handle or
        a :class:`~.datasets.DatasetEntry`; it is stale when any of them is too
        (:attr:`DatasetEntry.also <.datasets.DatasetEntry.also>`), a document's version taken
        when the call ends, as the source's is.  An existing id is replaced.  ``limit`` when the dataset is
        over ``Limits.max_dataset_cells`` or the session would keep over
        ``Limits.max_datasets``."""
        id = check_dataset_id(id)
        if not isinstance(dataset, Dataset):
            raise TypeError(f"define_dataset takes a Dataset, not {type(dataset).__name__}")
        limits = self.limits
        if dataset.cells > limits.max_dataset_cells:
            raise ToolError("limit", f"the dataset has {dataset.cells} values ({len(dataset.rows)} "
                            f"rows of {len(dataset.columns)} columns); at most "
                            f"{limits.max_dataset_cells}: group, filter or take the top rows")
        known = set(self.session.dataset_ids()) | {DATASET_PREFIX + key
                                                     for key in self.context.datasets}
        if DATASET_PREFIX + id not in known and len(known) >= limits.max_datasets:
            raise ToolError("limit", f"{len(known)} datasets are kept; at most "
                            f"{limits.max_datasets}: reuse an id to replace one",
                            valid_options=sorted(known))
        defined_at = self.session.now()
        if isinstance(source, DatasetEntry):
            entry = source.derived(id, dataset, defined_at=defined_at)
            if origin is not None:
                entry.origin = origin
        else:
            if source is _PRIMARY:
                document = self.entry
            elif source is None:
                document = None
            else:
                document = self.entries.get(source) or self.session.entry(source)
            entry = DatasetEntry(id, dataset, defined_at=defined_at,
                                 doc=document.doc_id if document else None,
                                 kind=document.kind if document else None,
                                 name=document.name if document else None, source=document,
                                 origin=origin)
        others = list(entry.also)
        for other in also:
            if isinstance(other, DatasetEntry):
                others.append(other.derived(other.id, other.dataset))
            else:
                document = self.entries.get(other) or self.session.entry(other)
                others.append(DatasetEntry(id, dataset, doc=document.doc_id,
                                           kind=document.kind, name=document.name,
                                           source=document))
        entry.also = tuple(others)
        self.context.datasets.pop(id, None)
        self.context.datasets[id] = entry
        self.context.dataset_ops[id] = self.context.op
        if entry.address not in self.result.datasets:
            self.result.datasets.append(entry.address)
        return entry.address

    def dataset(self, address: str, *, field: str | None = None) -> DatasetEntry:
        """The dataset ``address`` names (``d:by_region``, with or without what follows the
        id): one this call (or an earlier op of its batch) defined, else the session's.
        ``not_found`` lists the ids there are."""
        found = dataset_id(address) if isinstance(address, str) and address.startswith(
            DATASET_PREFIX) else address
        pending = self.context.datasets.get(found) if isinstance(found, str) else None
        if pending is not None:
            return pending
        try:
            entry = self.session.dataset(address, field=field)
            if entry.id not in self.context.datasets_seen:
                self.context.datasets_seen[entry.id] = (entry, entry.stale)
            return entry
        except ToolError as error:
            error.valid_options = list(dict.fromkeys(
                error.valid_options + [DATASET_PREFIX + key for key in self.context.datasets]))
            raise

    def source(self, value: str, *, field: str | None = None, fact: bool = False) -> Rows:
        """The rows a source names (:mod:`.sources`): a whole dataset (``d:by_region``) or a
        run of its rows (``d:by_region/#9:#16``, ``/#9:`` to the last, ``/#9``;
        :func:`~.datasets.row_range`), or an address in a document this call holds
        (``d2/t:sales``, ``t:sales``), read now by that document's format.  A row past a
        dataset's last is refused, never cut.  ``limit`` over ``Limits.max_dataset_cells``.
        ``fact``: the source of a reference in a text (:meth:`fact`), for a refusal's words."""
        if isinstance(value, str) and value.startswith(DATASET_PREFIX):
            entry = self.dataset(value, field=field)
            address, dataset = entry.address, entry.dataset
            if value != entry.address:
                found = row_range(value)
                if found is None:
                    raise ToolError("invalid_arguments", f"a source is a whole dataset "
                                    f"({entry.address}) or a run of its rows "
                                    f"({entry.address}/#1:#9, /#10: to the last), not "
                                    "another part of one", field=field,
                                    valid_options=[entry.address, f"{entry.address}/#1:"])
                _, first, last = found
                count = len(dataset.rows)
                if max(first, last or first) > count:
                    raise ToolError("invalid_arguments", f"{entry.address} has {count} rows; "
                                    f"there is no row #{max(first, last or first)} (nothing "
                                    "is cut)", field=field,
                                    valid_options=[entry.address + rows_text(min(first, count),
                                                                            count)]
                                    if count else [])
                dataset = dataset.row_slice(first, last)
                address = entry.address + rows_text(first, last)
            self.context.datasets_used.setdefault(entry.id, entry)
            fmt = self.session.formats.get(entry.kind) if entry.kind else None
            return Rows(address, dataset, doc=entry.doc, kind=entry.kind,
                        name=entry.name, version=entry.version, stale=entry.stale,
                        merge=getattr(fmt, "merge_sources", None))
        try:
            doc, address = qualify(value, self.session.documents, self.session.formats,
                                   field=field)
        except ToolError as error:
            raise self._source_refused(error, value, field, fact=fact) from None
        held = self.entries.get(doc)
        if held is None:
            raise ToolError("refused", f"{doc} is not held by this call: name it in an "
                            "argument the tool declares as a source", field=field)
        fmt = self.session.formats[held.kind]
        try:
            dataset = resolve_rows(fmt, held.document, address)
        except ToolError as error:
            raise self._source_refused(error, value, field, doc, fact=fact) from None
        if dataset.cells > self.limits.max_dataset_cells:
            raise ToolError("limit", f"{doc}/{address} has {dataset.cells} values; at most "
                            f"{self.limits.max_dataset_cells}: derive a smaller dataset",
                            field=field)
        return Rows(f"{doc}/{address}", dataset, doc=doc, kind=held.kind, name=held.name,
                    version=held.version, stale=dataset.invalidated,
                    merge=fmt.merge_sources)

    def _source_refused(self, error: ToolError, value: str, field: str | None,
                        doc: str | None = None, *, fact: bool = False) -> ToolError:
        """Round 56 (R120): the refusal of a source that names no rows, as the dispatcher's
        (:meth:`Toolbox._source_refiner`): what it names when it is the call's own document's
        table, the sources there are, only options that resolve."""
        doc_ids = [self.entry.doc_id] if self.entry is not None else []
        refine = Toolbox._source_refiner(self.session, doc_ids, self.tool, self.arguments,
                                         self.context.datasets)
        return refine(error, value, error.field or field, doc, fact)

    def facts_at(self, field: str) -> list[textfacts.Fact]:
        """The values written into the argument ``field`` (``items[0].text``), in order."""
        return [fact for fact in self.facts if fact.field == field]

    def fact(self, address: str, *, field: str | None = None) -> tuple[str, dict[str, Any]]:
        """The text a reference's ``address`` writes (:mod:`.textfacts`) and the record of
        it: one value of a dataset (``d:by_topic/door/mean``) or of an address in a
        document this call holds (``d2/t:checks/N-1/rating``), in the session's locale,
        through the number format after it (``:0.0``) when there is one; or a thing of the
        call's own document, as its format writes it (``DocumentFormat.own_facts``)."""
        owners = textfacts.own_prefixes(self.session.formats)
        prefix = textfacts.own_prefix(address, {p for found in owners.values() for p in found})
        if prefix is not None:
            return self._own_fact(address, prefix, owners, field)
        try:
            return self._fact(address, field)
        except ToolError as error:
            suffix = textfacts.bad_format(address)
            if suffix is not None and error.code in ("not_found", "invalid_arguments"):
                raise textfacts.format_error(address, suffix, field) from error
            if error.field is None:
                error.field = field
            raise

    def _own_fact(self, address: str, prefix: str, owners: Mapping[str, Sequence[str]],
                  field: str | None) -> tuple[str, dict[str, Any]]:
        name = textfacts.own_name(address, prefix, field)
        entry = self.entry
        fmt = self.session.formats.get(entry.kind) if entry is not None else None
        resolve = (getattr(fmt, "own_facts", None) or {}).get(prefix)
        if resolve is None or isinstance(resolve, str):
            takers = sorted(kind for kind, other in self.session.formats.items()
                            if callable((getattr(other, "own_facts", None) or {}).get(prefix)))
            raise textfacts.not_own(address, prefix, entry.kind if entry is not None else None,
                                    takers, field, said=resolve)
        if name.startswith("$"):
            name = self.resolve_ref(name, field=field)
        assert entry is not None  # resolve is its format's
        try:
            text, fields = resolve(entry.document, name)
        except ToolError as error:
            if error.field is None:
                error.field = field
            raise
        return text, textfacts.own_record(prefix, name, text, fields)

    def _fact(self, address: str, field: str | None) -> tuple[str, dict[str, Any]]:
        source, segments, fmt = textfacts.split(address)
        selected = False
        if source.startswith(DATASET_PREFIX):
            rows = self.source(source, field=field, fact=True)
        elif len(segments) >= 2:
            segment, quoted = segments[0]
            numbered = re.fullmatch(r"#[1-9][0-9]*", segment)
            raw = textfacts.quote(segment) if quoted or not numbered else segment
            rows = self.source(f"{source}/{raw}", field=field, fact=True)
            segments, selected = segments[1:], True
        else:
            rows = self.source(source, field=field, fact=True)
        row, column = textfacts.pick(rows, segments, address, field=field,
                                     locale=self.locale, row_selected=selected)
        text = textfacts.value_text(rows.dataset.rows[row][column], self.locale,
                                    type=rows.type(column), format=fmt,
                                    where=rows.where(row, column), field=field)
        return text, textfacts.record(rows, row, column, address, text, fmt)

    def keep_provenance(self, address: str, rows: Rows,
                        placed: Mapping[str, Any]) -> dict[str, Any]:
        """Keep, in the call's document, where the values just placed at ``address`` came
        from: the :mod:`ooxml_edit.provenance` record of ``placed`` (the result's
        ``data.from``, from ``rows`` -- what :meth:`source` returned), handed to the
        format's :attr:`~.session.DocumentFormat.store_provenance`, inside this call's
        undo step.  Call it after the placement succeeded.  Returns the record (also when
        the format keeps none)."""
        found = provenance.record(placed, document=rows.name)
        self.store_provenance(address, found)
        return found

    def store_provenance(self, address: str, found: Mapping[str, Any] | None) -> None:
        """Hand ``found`` -- a record as it is, or ``None`` to drop the one ``address`` has
        -- to the format's :attr:`~.session.DocumentFormat.store_provenance`, if it has
        one.  For a call that changes its document, inside its undo step."""
        if self.entry is None or not self.changing:
            raise RuntimeError("provenance is kept by a call that changes its document")
        hook = getattr(self.session.formats.get(self.entry.kind), "store_provenance", None)
        if hook is not None:
            hook(self.entry.document, address, found)

    @property
    def document(self) -> Any:
        if self.entry is None:
            raise ToolError("invalid_arguments", f"{self.tool.name} names no document")
        return self.entry.document

    @property
    def limits(self) -> Limits:
        return self.session.limits

    @property
    def font_dirs(self) -> tuple[str, ...] | None:
        """The session's font folders (:attr:`Session.font_dirs`): pass them to work run in
        a worker process, which does not see the session."""
        return self.session.font_dirs

    @property
    def locale(self) -> Any:
        """The session's regional settings (:attr:`Session.locale`, an
        :class:`ooxml_common.locales.Locale`; en-US unless the application gave one): pass
        it to work run in a worker process -- a renderer's ``ConvertOptions(locale=...)`` --
        which does not see the session."""
        return self.session.locale

    def now(self) -> Any:
        return self.session.now()

    def blob(self, handle: str) -> Any:
        return self.session.blob(handle)

    def warn(self, message: str) -> None:
        self.result.warnings.append(message)

    def image(self, data: bytes, width: int, height: int, *, media_type: str = "image/png",
              label: str = "") -> Image:
        """Attach an image to the result, within the per-call and session limits."""
        limits = self.limits
        if len(self.result.images) >= limits.max_images_per_call:
            raise ToolError("limit", f"at most {limits.max_images_per_call} images per call")
        if max(width, height) > limits.max_image_edge:
            raise ToolError("limit", f"{width}x{height} px is over the "
                            f"{limits.max_image_edge} px long-edge limit")
        self.session.reserve_images(1)
        image = Image(data, width, height, media_type, label)
        self.result.images.append(image)
        return image

    @property
    def may_unlock(self) -> bool:
        """Whether :meth:`unlocked` lets the documents' locks go: a reading call outside a
        batch -- a batch's reads after its last change among them (round 33: they run once
        its undo step is closed) -- or a changing call's work ahead of its change
        (:attr:`Tool.aheads`).  A handler that will work unlocked takes its snapshot first."""
        return bool(self.held) and not self.context.batch and (self.ahead or not self.changing)

    @contextmanager
    def unlocked(self) -> Iterator[bool]:
        """Let the call's document locks go for slow work on a snapshot -- rasterising, a
        layout -- and take them back after; yields whether they were let go.

        Take the snapshot first, under the lock: the bytes (``to_bytes()``), the version
        (:attr:`DocumentEntry.version`), ids and anything else the work needs.  Inside, read
        nothing from the live document: another call may be changing it -- an edit to
        one part while another rasterises does not wait.  After, the locks are held again;
        cache what was made under the snapshot's version, which the result reports
        (:attr:`version_used`, set here to the version when the locks were let go).

        Inside a batch (before its last change), or in a changing call, the locks stay held
        (the work is part of an undo step or of a run of ops) and this yields ``False``;
        a changing call's work ahead of its change (:attr:`Tool.aheads`) may let them go."""
        if self.entry is not None and self.version_used is None:
            self.version_used = self.entry.version
        if not self.may_unlock:
            yield False
            return
        held = sorted(self.held, key=lambda entry: doc_order(entry.doc_id))
        self.released = True
        for entry in reversed(held):
            entry.lock.release()
        try:
            yield True
        finally:
            for entry in held:
                entry.lock.acquire()

    def run_unlocked(self, fn: Callable[..., Any], *args: Any, timeout: float | None = None,
                     **kwargs: Any) -> Any:
        """:meth:`run` inside :meth:`unlocked`: ``fn`` gets a snapshot (bytes), never the
        live document."""
        with self.unlocked():
            return self.run(fn, *args, timeout=timeout, **kwargs)

    def on_snapshot(self, fn: Callable[..., Any], *args: Any, timeout: float | None = None,
                    **kwargs: Any) -> Any:
        """``fn(data, *args, **kwargs)`` in a worker process, ``data`` the primary
        document's bytes at this version, the call's locks let go while it runs: a slow
        read -- a check -- side by side with other calls on the document, and an edit to it
        meanwhile does not wait (round 28, P2).

        The bytes are taken here, under the lock (:meth:`DocumentEntry.snapshot`: made once
        per version, so concurrent calls on one version share them), and the result
        reports their version (:attr:`version_used`); the dispatcher warns when the
        document moved on before the call ended.  Each worker opens its own copy from the
        bytes and drops it with the task, so there are at most as many copies as the
        pool has workers; such tasks take all the workers but one
        (:attr:`Toolbox.snapshot_slots`: a render need not wait for a turn of them).
        ``timeout`` counts from when a worker takes the task; the wait for one has its own
        bound, :attr:`~.limits.Limits.worker_queue_timeout` (300 s), so a busy pool does
        not time out work that never started (an application's own runner: ``timeout``
        bounds both, as for :meth:`run`).  A :class:`~.worker.ThreadRunner` runs it as the
        pool does, in one of its threads, with the same slots and bounds.  ``fn`` is a
        module-level :func:`~.worker.worker_task`; its
        arguments and result pickle.  Where the pool runs in this process (``workers=0``,
        an :class:`~.worker.InProcess` runner, or no worker could start) ``fn`` runs in
        this thread, still unlocked and without a deadline: a thread cannot be stopped at
        one.  Inside a batch or a changing call the locks stay held (:meth:`unlocked`),
        the work still on the bytes."""
        if self.entry is None:
            raise ValueError("on_snapshot needs the call's document")
        data = self.entry.snapshot()
        pool = self.toolbox.pool
        with self.unlocked():
            if getattr(pool, "in_process", False) or isinstance(pool, InProcess):
                return fn(data, *args, **kwargs)
            queue = self.limits.worker_queue_timeout
            began = time.monotonic()
            slots = self.toolbox.snapshot_slots
            if not slots.acquire(timeout=queue):
                raise ToolError("timeout", f"no worker was free within {queue:g} s",
                                details={"timeout": queue})
            try:
                timeout = timeout or self.limits.render_timeout
                if not isinstance(pool, (WorkerPool, SubprocessPool, ThreadRunner)):
                    return pool.run(fn, data, *args, timeout=timeout, **kwargs)
                return pool.run(fn, data, *args, timeout=timeout,
                                queue_timeout=max(0.0, queue - (time.monotonic() - began)),
                                **kwargs)
            finally:
                slots.release()

    def run(self, fn: Callable[..., Any], *args: Any, timeout: float | None = None,
            affinity: Any = None, affinity_wait: float | None = None, lane: Any = None,
            on_late: "Callable[[Any], Any] | None" = None, queued: bool = False,
            **kwargs: Any) -> Any:
        """Run ``fn`` in a worker process (or in-process: :mod:`.worker`); ``timeout`` (s)
        defaults to the render deadline.  ``affinity``: to the worker that took the last
        task with that key, waited for at most ``affinity_wait`` s while busy (the
        :mod:`.worker` module's notes; an application's runner that does not say it takes
        them is not handed them).  ``lane``: :data:`~.worker.RENDER_LANE` for a task that
        draws images (:meth:`_lane`).  ``queued``: the wait for a worker bounded on its own
        (:attr:`~.limits.Limits.worker_queue_timeout`), ``timeout`` counting from when one
        takes the task, and past its deadline its worker finishes it rather than being stopped,
        as the render lane's (a render's layout, round 39: the worker holds the document's
        layouts, which a stop would lose)."""
        pool = self.toolbox.pool
        if affinity is not None and takes_affinity(pool):
            kwargs = {**kwargs, "affinity": affinity, "affinity_wait": affinity_wait}
        kwargs.update(self._lane(pool, lane, on_late))
        if queued and _queues(pool):
            kwargs.setdefault("queue_timeout", self.limits.worker_queue_timeout)
        if queued and takes_lane(pool):
            kwargs["overrun"] = True
        return pool.run(fn, *args, timeout=timeout or self.limits.render_timeout, **kwargs)

    def run_all(self, fn: Callable[..., Any], calls: Sequence[tuple], *,
                timeouts: Sequence[float], lane: Any = None,
                on_late: "Sequence[Callable[[Any], Any] | None] | None" = None) -> list[Any]:
        """``fn(*args)`` for each ``args`` in ``calls``, at once over the pool's workers, each
        with its own deadline; the results in order, an exception in its place (none is
        raised: the handler keeps what succeeded, then raises).  The chunks of one render
        (:func:`~.worker.run_all`); give each a deadline by its size
        (:meth:`~.limits.Limits.render_timeout_for`).  ``lane`` and each call's
        ``on_late``: :meth:`run`'s."""
        pool = self.toolbox.pool
        options = [self._lane(pool, lane, on_late[index] if on_late else None)
                   for index in range(len(calls))]
        return run_all(pool, fn, calls, timeouts, options)

    def _lane(self, pool: Any, lane: Any, on_late: Any) -> dict:
        """What ``pool.run`` is handed for a task in ``lane``: for the render lane (round
        39), the lane -- it yields to other work, and its worker is not stopped at its
        deadline unless it is still busy as long again -- ``on_late`` (what to do with a
        result that came after it), and its wait for a worker bounded on its own
        (:attr:`~.limits.Limits.worker_queue_timeout`), so its deadline counts from when a
        worker takes it: a render that waited behind layouts is not stopped half drawn.
        Nothing for other work, or a runner that does not take them."""
        if lane is None or not takes_lane(pool):
            return {}
        out: dict = {"lane": lane, "on_late": on_late}
        if _queues(pool):
            out["queue_timeout"] = self.limits.worker_queue_timeout
        return out

    @property
    def parallelism(self) -> int:
        """How many tasks :meth:`run_all` runs at once: split work into this many chunks."""
        return self.toolbox.parallelism

    @property
    def images(self) -> ContentStore:
        """The toolbox's rendered images by content key (:mod:`.rendercache`)."""
        return self.toolbox.images

    @property
    def decoded_fonts(self) -> ContentStore:
        """The toolbox's fonts decoded from embedded ones, by payload digest: hand those of
        the document to the worker with its task (:mod:`.rendercache`)."""
        return self.toolbox.decoded_fonts

    def check_images(self, count: int) -> None:
        """Refuse with ``limit`` before rendering when ``count`` more images would pass the
        per-call limit or the session's or round's budget (:meth:`Session.check_images`):
        a call to be refused costs nothing.  :meth:`image` still counts each image."""
        limits = self.limits
        if len(self.result.images) + count > limits.max_images_per_call:
            raise ToolError("limit", f"at most {limits.max_images_per_call} images per call")
        self.session.check_images(count)

    def output(self, name: str, format: str, data: bytes, *,
               entry: DocumentEntry | None = None, state: bool = False) -> dict[str, Any]:
        """Hand a file to the application, behind the validate gate.

        The document's problems now are compared with those it had when opened; a new one
        refuses the save unless the application allowed it (``allow_new_problems``).  The
        model gets the name, format, size and the report -- never the bytes.

        ``state``: the file is the document itself, as it is now, so the output carries the
        format's state beside the bytes (:attr:`DocumentFormat.state`, :attr:`Output.state`),
        for the application to open them again with.  Never the model's to see.
        """
        entry = entry or self.entry
        report: dict[str, Any] | None = None
        if entry is not None:
            fmt = self.session.formats.get(entry.kind)
            if fmt is not None and fmt.problems is not None:
                # Round 53, R109: the comparison check's ``validation`` shows
                # (:mod:`.savegate`), the problems found afresh -- never a kept list --
                # and kept for the version's checks (not inside a batch).
                report = validation(entry, fmt, fresh=True, cache=not self.in_batch)
                new = report["new"]
                if new and not self.toolbox.allow_new_problems:
                    raise ToolError("refused", f"saving would add {len(new)} validation "
                                    "problem(s); fix them first", valid_options=new,
                                    details={"validate": report})
        kept = None
        if state and entry is not None:
            fmt = self.session.formats.get(entry.kind)
            if fmt is not None and fmt.state is not None:
                kept = fmt.state(entry.document)
        output = Output(name=name, format=format, data=data,
                        doc=entry.doc_id if entry else None, validate=report, state=kept)
        self.session.add_output(output)
        return output.describe()


class _OpFailed(Exception):
    """An op of a batch failed: which one, and its error."""

    def __init__(self, index: int, tool: str, error: ToolError) -> None:
        super().__init__(error.message)
        self.index = index
        self.tool = tool
        self.error = error


#: :meth:`Toolbox.session`'s ``font_dirs`` and ``locale`` when not given: the toolbox's own.
_TOOLBOX_DEFAULT: Any = object()


def _with_format_tools(tools: dict[str, Tool],
                       formats: Iterable[DocumentFormat]) -> dict[str, Tool]:
    """``tools`` with each format's configured tools (:attr:`DocumentFormat.tools`): new
    names after the others, a name already there replaced in its place -- only a tool
    that serves that format's kind alone, so no other library's handler is lost."""
    for fmt in formats:
        for extra in fmt.tools:
            existing = tools.get(extra.name)
            if existing is not None and not set(existing.handlers) <= {fmt.kind}:
                raise ValueError(f"the {fmt.kind} format's {extra.name!r} would replace a "
                                 "tool other kinds serve too")
            tools[extra.name] = extra
    return tools


def _served(tools: dict[str, Tool]) -> dict[str, Tool]:
    """``tools``, each with a handler for any kind and parameters of some kinds only
    (``undo``'s ``scope``) copied with the kinds it serves here -- the kinds the other
    tools' handlers serve, the libraries loaded (:attr:`Tool.kinds_served`) -- so it is sent
    only theirs.  The libraries decide, not the formats configured, so configuring a
    format changes no definition."""
    kinds = tuple(dict.fromkeys(kind for item in tools.values() for kind in item.handlers
                                if kind is not None))
    if not kinds:
        return tools
    for name, item in tools.items():
        if None in item.handlers and (item.kind_fields or item.kind_values
                                      or item.kind_descriptions):
            served = copy.copy(item)
            served.kinds_served = kinds
            tools[name] = served
    return tools


#: The runners ``Toolbox(runner=...)`` takes by name: the default pool, or threads.
RUNNERS = ("processes", "threads")
#: When ``Toolbox(block_edits=...)`` runs an admitted changing call on a draft (:mod:`.blocks`).
BLOCK_MODES = ("auto", "always", "off")

_THREADS_GIL_WARNED: list[bool] = []


def _warn_threads_gil() -> None:
    """Once per process: ``runner="threads"`` asked for, the GIL on."""
    if _THREADS_GIL_WARNED:
        return
    _THREADS_GIL_WARNED.append(True)
    logger.warning("Toolbox(runner=\"threads\") needs a free-threaded Python with the GIL off "
                   "(python3.15t, PYTHON_GIL=0, lxml 7 or later); this one has the GIL on, "
                   "where threads take turns, so the work runs in worker processes instead")


class Toolbox:
    """The tools of one or more libraries, ready for a provider and a session."""

    #: Whether a batch does the work its ops deferred (:meth:`Call.defer`) once, after its
    #: last op; ``False`` does each op's after it, as a call alone does -- the same results
    #: where no later op changes what the work reads (for comparing the two).
    batch_defers: bool = True

    def __init__(self, tools: Iterable[Tool], *, formats: Iterable[DocumentFormat] = (),
                 groups: Iterable[ToolGroup] = (), limits: Limits | None = None,
                 allow_new_problems: bool = False, workers: int | None = None,
                 start_method: str = "spawn", log_arguments: bool = False,
                 error_map: Mapping[type[BaseException], Any] | None = None,
                 strict_first: Sequence[str] | None = None,
                 runner: Any = None, font_dirs: Any = None, locale: Any = None,
                 roles: str | Iterable[str] | None = None, prewarm: bool = True,
                 numbers: Any = None, block_edits: str = "auto") -> None:
        """``workers`` worker processes render and lay out (:class:`~.worker.WorkerPool`);
        ``None`` (the default) is what this process may use -- its CPUs and its container's
        CPU quota, at most eight (:func:`~.worker.default_workers`).  One call's views
        are drawn over all of them at once, in chunks.  In a daemonic process, which
        may not start multiprocessing children, the pool uses ``workers`` subprocess
        workers instead (:class:`~.worker.SubprocessPool`), by itself; ``0`` runs the work
        in-process, in threads, whose deadlines bound the wait but cannot stop the work.
        ``runner`` replaces the pool: anything with ``run(fn, *args, timeout=...,
        **kwargs)`` and ``close()`` -- a :class:`~.worker.SubprocessPool`, an
        :class:`~.worker.InProcess`, a ``WorkerPool(fallback=...)`` of the application's
        choice, or its own executor (with a ``size``, its work is spread over that many).
        ``runner="threads"`` opts in to :class:`~.worker.ThreadRunner`: ``workers``
        threads of this process instead of processes, for a free-threaded Python with the
        GIL off (``python3.15t``, ``PYTHON_GIL=0``, lxml 7) -- warmed once per process, no
        worker to start, but a task past its deadline is abandoned, not stopped.  With the
        GIL on, threads would take turns, so the default pool runs the work instead, with a
        warning once.  ``runner="processes"`` is the default pool by name.

        ``prewarm`` starts every worker in the background when the pool is first needed,
        each running the formats' warm-up (:attr:`~.session.DocumentFormat.warm_up`), so
        the first render waits for one worker, not for all; :meth:`warm` does it at once
        (at the application's start).  Starting and warming never count against a call's
        deadline.  Rendered images are kept by content for the whole toolbox
        (:attr:`images`, in memory, sized by ``limits.image_cache_bytes``).

        ``font_dirs`` -- the application's own font folders, searched before the operating
        system's for every render and measurement -- is the default of the sessions this
        toolbox makes (:meth:`session` overrides it per session).  ``None`` leaves it to
        the renderers: ``OOXML_FONT_DIRS``, then the system's folders.  It is never a tool
        argument: the model neither sees nor sets it.

        ``locale`` -- the regional settings documents are drawn and edited in, an
        :class:`ooxml_common.locales.Locale` (decimal and thousands separators, date order,
        language, list separator) -- is likewise the default of the sessions this toolbox
        makes, forwarded to every format and to the work run in worker processes.  ``None``
        is en-US (:data:`~.session.DEFAULT_LOCALE`, which is
        :data:`ooxml_common.locales.EN_US`), always: never the operating system's.  Not a
        tool argument either.

        ``roles`` -- what the model may do: any of ``review`` (read, find, render, check,
        compare; add and reply to comments), ``resolve`` (resolve, reopen and delete
        comments), ``accept`` (accept and reject tracked changes) and ``edit`` (every other
        change) -- is the default of the sessions this
        toolbox makes and of :meth:`definitions`, :meth:`allowed_tools` and
        :meth:`system_prompt`; each takes ``roles=`` to override it.  ``None``, the
        default, is all four: every tool, the definitions and prompt as without roles.  A
        call outside the session's roles is refused, inside ``batch`` too.

        ``numbers`` -- the rules of ``check``'s numbers (round 36): the application's ignore
        list and whether whole numbers are compared, a :class:`~.numbers.NumberRules` (or a
        mapping of its fields) -- is the default of the sessions this toolbox makes.  Not a
        tool argument.

        ``block_edits`` (round 45, lane B) -- when a changing call a format admits runs on a
        draft of its document, so edits of other blocks run beside it (:mod:`.blocks`):
        ``"auto"`` (the default) while another call edits the same document, or when the
        format says the call is worth a draft alone (``BlockEdits.alone``), ``"always"``
        for every one it admits, ``"off"`` never.  Results and bytes are the same either
        way; ``"auto"`` leaves a call made with no other edit in flight exactly as it was.
        Not a tool argument."""
        if block_edits not in BLOCK_MODES:
            raise ArgumentError(f"block_edits is one of {', '.join(BLOCK_MODES)}, not {block_edits!r}")
        #: When a changing call runs on a draft (:mod:`.blocks`): ``auto``, ``always``, ``off``.
        self.block_edits = block_edits
        self.formats: dict[str, DocumentFormat] = {fmt.kind: fmt for fmt in formats}
        self.tools: dict[str, Tool] = _served(_with_format_tools(merge_tools(tools),
                                                                 self.formats.values()))
        #: Claude's strict priority: the application's order, else each format's in turn.
        self.strict_first: list[str] = list(dict.fromkeys(
            strict_first if strict_first is not None
            else [name for fmt in self.formats.values() for name in fmt.strict_first]))
        self.groups: dict[str, ToolGroup] = {group.name: group for group in groups}
        #: The formats' :attr:`~.session.DocumentFormat.tool_hints`, for a name that is
        #: no tool.
        self.tool_hints: dict[str, str] = {name: target for fmt in self.formats.values()
                                           for name, target in fmt.tool_hints.items()}
        self.limits = limits or Limits()
        #: The validate gate's override: the application's to set, never the model's.
        self.allow_new_problems = allow_new_problems
        self.log_arguments = log_arguments
        #: The default :attr:`Session.font_dirs` of the sessions this toolbox makes.
        self.font_dirs: tuple[str, ...] | None = font_dirs_of(font_dirs)
        #: The default :attr:`Session.locale` of the sessions this toolbox makes.
        self.locale: Any = locale_of(locale)
        #: The default :attr:`Session.numbers` of the sessions this toolbox makes.
        self.numbers: NumberRules = rules_of(numbers)
        #: The default roles of the sessions, definitions and prompt (:data:`~.registry.ROLES`).
        self.roles: tuple[str, ...] = role_set(roles)
        self._workers = default_workers() if workers is None else workers
        self._start_method = start_method
        self._prewarm = prewarm
        if isinstance(runner, str) and runner not in RUNNERS:
            raise ArgumentError(f"runner is one of {RUNNERS}, or a runner object, "
                                f"not {runner!r}")
        #: ``"threads"`` when the application asked for :class:`~.worker.ThreadRunner`.
        self._runner_name = runner if isinstance(runner, str) else None
        self._pool: Any = None if isinstance(runner, str) else runner
        self._pool_lock = threading.Lock()
        self._snapshot_slots: threading.BoundedSemaphore | None = None
        #: Rendered images by content key, every session's and view's (:mod:`.rendercache`).
        self.images = ContentStore(self.limits.image_cache_bytes)
        #: Fonts a worker decoded from a document's embedded ones, by payload digest, for
        #: the next worker's task (:mod:`.rendercache`).
        self.decoded_fonts = ContentStore(self.limits.font_cache_bytes)
        # The libraries' typed refusals first, so a format's map (and the application's)
        # may name a subclass otherwise; the closest class in an exception's MRO wins.
        self._errors: dict[type[BaseException], Any] = dict(BASE_ERRORS)
        for fmt in self.formats.values():
            self._errors.update(fmt.errors)
        self._errors.update(error_map or {})
        self._warning_categories = tuple(category for fmt in self.formats.values()
                                         for category in fmt.warnings)
        for category in self._warning_categories:
            # Every occurrence reaches the collector, not only the first per location.  A
            # filter the application adds later for these categories takes precedence.
            warnings.filterwarnings("always", category=category)

    # -- sessions and definitions ------------------------------------------------------------

    def session(self, *, clock: Clock | None = None, limits: Limits | None = None,
                on_output: Callable[[Output], None] | None = None,
                font_dirs: Any = _TOOLBOX_DEFAULT, locale: Any = _TOOLBOX_DEFAULT,
                roles: Any = _TOOLBOX_DEFAULT, numbers: Any = _TOOLBOX_DEFAULT) -> Session:
        """A new session.  ``font_dirs`` replaces the toolbox's :attr:`font_dirs` for this
        session (``None``: the renderers' default; ``[]``: no folders of the
        application's, ``OOXML_FONT_DIRS`` not read either); ``locale`` the toolbox's
        :attr:`locale` (``None``: en-US) -- one user's or client's own; ``roles`` the
        toolbox's :attr:`roles` (``None``: all), to match the definitions sent for it;
        ``numbers`` the toolbox's :attr:`numbers` (``None``: the defaults)."""
        return Session(self.formats.values(), clock=clock, limits=limits or self.limits,
                       on_output=on_output,
                       font_dirs=self.font_dirs if font_dirs is _TOOLBOX_DEFAULT else font_dirs,
                       locale=self.locale if locale is _TOOLBOX_DEFAULT else locale,
                       roles=self.roles if roles is _TOOLBOX_DEFAULT else roles,
                       numbers=self.numbers if numbers is _TOOLBOX_DEFAULT else numbers)

    def tool_named(self, name: Any, notes: list[str] | None = None, *,
                   aliases: bool = True) -> Any:
        """The tool name ``name`` stands for: itself; the name after the last dot when a
        model wrote a namespace before a tool's name (``functions.ppt_set_text``, the
        prefix OpenAI's APIs show a model; a Responses group's ``charts.edit_chart``) -- no
        tool name has a dot; and with ``aliases``, the one tool whose name says the same in
        other words (:func:`.hints.tool_alias`: ``word_replace_text`` for
        ``replace_text``, ``x_insert_picture`` for ``x_add_picture``), with a namespace or
        without.  ``notes`` gets a line for each name read as another.  The calls a
        provider sends, a ``batch``'s and a ``read_batch``'s ops are read the same way
        (round 40, R44); a name that is none of these is returned as it is."""
        if not isinstance(name, str) or name in self.tools:
            return name
        bare = name.rsplit(".", 1)[1] if "." in name else name
        meant = bare if bare in self.tools else \
            tool_alias(bare, self.tools, self.tool_hints) if aliases else None
        if meant is None:
            return name
        if notes is not None:
            notes.append(f"tool {name!r} read as {meant!r}")
        return meant

    def tool(self, name: str, notes: list[str] | None = None,
             roles: Sequence[str] | None = None) -> Tool:
        """The tool ``name`` names (:meth:`tool_named`); ``invalid_arguments`` otherwise,
        naming the tool (and action) it most likely means (:meth:`_no_tool`)."""
        name = self.tool_named(name, notes)
        if name not in self.tools:
            raise self._no_tool(name, roles)
        return self.tools[name]

    def _no_tool(self, name: Any, roles: Sequence[str] | None, *,
                 batch: bool = False, within: str = "batch") -> ToolError:
        """``invalid_arguments`` for a name that is no tool: the tools, and their actions,
        it most likely means (:func:`.hints.tool_hints`: ``x_add_chart``, ``edit_chart``
        with action ``add``), else the names close to it -- of ``roles``' tools (all
        without).  A hint outside the roles is named with the role it needs, never in
        ``valid_options``; inside ``batch`` (or ``within``: ``read_batch``, whose options
        are the reading tools), one it cannot run is to be called alone."""
        roles = tuple(ROLES if roles is None else roles)
        tools = [tool for tool in self.tools.values() if tool.roles & set(roles)
                 and (within != "read_batch" or _reads_some(tool))]
        found = tool_hints(name, self.tools, self.tool_hints)
        usable = [hint for hint in found if self._hint_role(hint, roles) is None]
        bare = str(name).rsplit(".", 1)[-1]        # a namespace is no part of the likeness
        near = difflib.get_close_matches(bare, [tool.name for tool in tools], 5, 0.5)
        message = f"no tool {name!r}"
        if usable:
            message += ": use " + ", or ".join(
                f"{hint} on its own ({within} cannot run it)"
                if batch and not _runs_in(self.tools[hint.tool], within) else str(hint)
                for hint in usable)
        elif found:
            message += (f": {found[0]} does that, and needs the "
                        f"{self._hint_role(found[0], roles)} role; this session has "
                        f"{', '.join(roles)}")
        elif batch and not difflib.get_close_matches(bare, self.tools, 1, 0.8):
            # No name this close is a slip: likely a tool the application runs itself,
            # outside the toolbox, which a batch cannot reach.
            message += (f" that {within} can run: it runs only the tools in valid_options; "
                        f"call {name} on its own")
        elif near:
            message += "; the nearest: " + ", ".join(near[:3])
        barred = {hint.tool for hint in found} - {hint.tool for hint in usable}
        options = list(dict.fromkeys([hint.tool for hint in usable]
                                     + [name for name in near if name not in barred]))[:5]
        if not options and not found:
            options = sorted(tool.name for tool in tools
                             if not batch or _runs_in(tool, within))
        return ToolError("invalid_arguments", message, field="tool" if batch else None,
                         valid_options=options)

    def _hint_role(self, hint: Any, roles: Sequence[str]) -> str | None:
        """The role a hint's call needs that ``roles`` lack; ``None`` when they have it."""
        tool = self.tools[hint.tool]
        if hint.action is None:
            if tool.roles & set(roles):
                return None
            return sorted(tool.roles, key=lambda role: (role != EDIT, role))[0]
        needed = tool._action_role(hint.action)
        return None if needed == ANY_ROLE or needed in roles else needed

    def select(self, groups: str | Iterable[str] | None = None, *,
               roles: str | Iterable[str] | None = _TOOLBOX_DEFAULT) -> list[Tool]:
        """The tools to send: all (``None`` or ``"all"``), or core plus the named groups;
        of those, the ones in the toolsets of ``roles`` (default the toolbox's), each
        offering only the roles' actions, a reviewer's main tools in the core of a role set
        without ``edit`` (:meth:`Tool.for_roles`)."""
        roles = self.roles if roles is _TOOLBOX_DEFAULT else role_set(roles)
        sent = [tool for tool in (tool.for_roles(roles) for tool in self.tools.values())
                if tool is not None]
        if groups is None or groups == "all":
            return sent
        wanted = {CORE} | ({groups} if isinstance(groups, str) else set(groups))
        unknown = wanted - {tool.group for tool in self.tools.values()} - {CORE}
        if unknown:
            raise ArgumentError(f"unknown group(s) {sorted(unknown)}")
        return [tool for tool in sent if tool.group in wanted]

    def definitions(self, provider: str = "anthropic", *,
                    groups: str | Iterable[str] | None = None, defer: bool | None = None,
                    roles: str | Iterable[str] | None = _TOOLBOX_DEFAULT,
                    **options: Any) -> list[dict[str, Any]]:
        """Provider-ready tool definitions: ``anthropic``, ``openai-responses`` or
        ``openai-chat``.

        ``groups`` picks what is sent: ``None`` or ``"all"`` for every tool, or the core
        plus the named groups.  ``defer`` loads the non-core tools on demand through the
        provider's tool search: on Claude ``defer_loading`` and the BM25 search tool, with
        ``cache_control`` on the last core tool; on the Responses API each group a
        ``namespace`` with ``defer_loading``, and ``tool_search``.  By default it is on when
        every tool is sent to a provider that has tool search, and off when the application
        picked groups.  Chat Completions has no tool search: pick groups there.  Other
        options go to the adapter (``cache``, ``strict``, ``namespaces``).

        ``roles`` (default the toolbox's :attr:`roles`) sends only the tools of those
        roles, a mixed tool with only their actions in its ``action`` enum: the core is
        the core tools of the roles, and for a role set without ``edit`` its reviewing
        tools too (:attr:`Tool.review_core`: comments).  All roles is every tool, as
        without roles.
        """
        tools = self.select(groups, roles=roles)
        everything = groups is None or groups == "all"
        if defer is None:
            defer = everything and provider in ("anthropic", "openai-responses")
        if provider == "anthropic":
            options.setdefault("strict_first", self.strict_first)
            options["defer"] = defer
        elif provider == "openai-responses":
            options.setdefault("groups", self.groups.values())
            options.setdefault("namespaces", defer)
            options["defer"] = defer
        elif defer:
            options["defer"] = defer
        return adapters.definitions_for(provider, tools, **options)

    def allowed_tools(self, groups: str | Iterable[str] | None = None, *,
                      provider: str = "openai-responses",
                      roles: str | Iterable[str] | None = _TOOLBOX_DEFAULT) -> dict[str, Any]:
        """An OpenAI ``tool_choice`` limiting calls to the core and ``groups`` (of the
        ``roles``' tools), while the ``tools`` sent (and so the cache) stay the same: for an
        application that sends every definition without tool search and narrows by turn."""
        names = [tool.name for tool in self.select(groups, roles=roles)]
        return adapters.openai_allowed_tools(names, chat=provider == "openai-chat")

    def system_prompt(self, *, extra: str | None = None,
                      roles: str | Iterable[str] | None = _TOOLBOX_DEFAULT,
                      tools: Iterable[Any] | None = None,
                      groups: str | Iterable[str] | None = None) -> str:
        """The shared fragment, each format's fragment, the role line of ``roles`` (default
        the toolbox's; a role set without ``edit`` is told it reviews, and that it may comment
        where a tool places comments, :func:`.prompts.role_line`; one with ``edit`` gets no
        line), then the application's ``extra``.

        Where a tool places comments, or settles tracked changes, is decided over the tools
        sent: ``tools`` -- what the application sends, as names, :class:`Tool` objects or
        the definitions themselves (any provider's; a name the toolbox does not have, an
        application's own tool or a tool search, says nothing) -- or ``groups``, the
        same as :meth:`definitions`' with the same ``roles`` (one or the other).  Neither:
        over the whole toolbox, as before."""
        roles = self.roles if roles is _TOOLBOX_DEFAULT else role_set(roles)
        if tools is not None and groups is not None:
            raise ArgumentError("system_prompt takes tools or groups, not both: groups for the "
                                "definitions(groups=...) sent, tools for any other set")
        if groups is not None:
            sent = [self.tools[tool.name] for tool in self.select(groups, roles=roles)]
        elif tools is not None:
            sent = self._sent(tools)
        else:
            sent = list(self.tools.values())
        # Comments are what a resolver acts on, tracked changes what accept settles: a tool
        # with a resolve (accept) action has them.
        comments = any(_needs(tool, RESOLVE) for tool in sent)
        tracked = any(_needs(tool, ACCEPT) for tool in sent)
        # read_batch is named where it is sent, and a role set has it (round 32, R15).
        reads = any(tool.name == "read_batch" and tool.roles & set(roles) for tool in sent)
        return prompts.system_prompt(*(fmt.prompt for fmt in self.formats.values()), extra=extra,
                                     roles=roles, comments=comments, tracked=tracked,
                                     reads=reads)

    def _sent(self, tools: Iterable[Any]) -> list[Tool]:
        """The toolbox's tools among ``tools``: names, :class:`Tool` objects (an
        application's own, not in the toolbox, as it is) or provider definitions -- an
        Anthropic or Responses function, a Chat Completions ``function``, a Responses
        ``namespace`` of them.  What names none of the toolbox's tools is left out."""
        if isinstance(tools, (str, Mapping, Tool)):
            tools = [tools]
        found: dict[str, Tool] = {}
        stack = list(tools)[::-1]
        while stack:
            item = stack.pop()
            if isinstance(item, Tool):
                found.setdefault(item.name, self.tools.get(item.name, item))
                continue
            if isinstance(item, Mapping):
                if item.get("type") == "namespace":
                    stack.extend(list(item.get("tools") or [])[::-1])
                    continue
                function = item.get("function")
                item = function.get("name") if isinstance(function, Mapping) \
                    else item.get("name")
                if not isinstance(item, str):
                    continue        # a definition of no tool of its own: a tool search
            if not isinstance(item, str):
                raise ArgumentTypeError(f"system_prompt's tools are names, Tool objects or "
                                        f"definitions, not {type(item).__name__}")
            name = self.tool_named(item, aliases=False)    # the application's own names
            if name in self.tools:
                found.setdefault(name, self.tools[name])
        return list(found.values())

    def render_result(self, provider: str, result: Result, call_id: str, *,
                      images: str | None = None, detail: str | None = None) -> Any:
        """One result as the provider wants it (for Chat Completions, and for the Responses
        API with ``images="message"``: a list).  ``images`` and ``detail`` are OpenAI's:
        :func:`~.adapters.openai_responses_items`."""
        limit = self.limits.max_result_chars
        if provider == "anthropic" and images is None and detail is None:
            return adapters.anthropic_tool_result(result, call_id, limit=limit)
        if provider == "openai-responses" and images in (None, "output"):
            return adapters.openai_function_call_output(result, call_id, limit=limit,
                                                        detail=detail or "auto")
        return adapters.results_for(provider, [(call_id, result)], limit=limit,
                                    images=images, detail=detail)

    def render_results(self, provider: str, results: Sequence[tuple[str, Result]], *,
                       images: str | None = None, detail: str | None = None) -> Any:
        """One assistant turn's results, in call order, as the provider wants them.  On the
        Responses API ``images="message"`` puts the images in a user message after the
        outputs instead of in them; ``detail`` sets OpenAI's image detail."""
        return adapters.results_for(provider, results, limit=self.limits.max_result_chars,
                                    images=images, detail=detail)

    # -- running calls -----------------------------------------------------------------------

    @property
    def pool(self) -> Any:
        """What runs CPU-bound work: the :class:`~.worker.WorkerPool`, made when first
        needed, or the ``runner`` the application gave."""
        with self._pool_lock:
            if self._pool is None:
                if self._runner_name == "threads" and not gil_enabled():
                    self._pool = ThreadRunner(
                        max(1, self._workers), warm_up=self.warm_ups,
                        start_timeout=self.limits.worker_start_timeout,
                        max_abandoned=self.limits.max_abandoned_threads)
                else:
                    if self._runner_name == "threads":
                        _warn_threads_gil()
                    self._pool = WorkerPool(self._workers, start_method=self._start_method,
                                            warm_up=self.warm_ups,
                                            start_timeout=self.limits.worker_start_timeout)
                    self._pool.render_workers = self.limits.render_workers
                if self._prewarm:
                    self._pool.warm()
            return self._pool

    @property
    def warm_ups(self) -> list[Callable[[], Any]]:
        """The formats' :attr:`~.session.DocumentFormat.warm_up` tasks, each once."""
        return list(dict.fromkeys(fmt.warm_up for fmt in self.formats.values()
                                  if fmt.warm_up is not None))

    @property
    def parallelism(self) -> int:
        """How many tasks the pool runs at once: its ``size`` (1 in-process, or for a
        runner that says nothing)."""
        pool = self.pool
        if getattr(pool, "in_process", False):
            return 1
        size = getattr(pool, "size", 1)
        return max(1, size) if isinstance(size, int) else 1

    @property
    def snapshot_slots(self) -> threading.BoundedSemaphore:
        """How many :meth:`Call.on_snapshot` tasks hold a worker at once:
        :attr:`~.limits.Limits.snapshot_workers`, by default all of the pool's workers but
        one (at least one), so a render does not queue behind a turn of checks that fills
        the pool."""
        size = self.parallelism                # takes the pool's lock itself
        wanted = self.limits.snapshot_workers
        count = max(1, size - 1) if wanted is None else max(1, int(wanted))
        with self._pool_lock:
            if self._snapshot_slots is None:
                self._snapshot_slots = threading.BoundedSemaphore(count)
            return self._snapshot_slots

    def warm(self, *, wait: bool = False) -> None:
        """Start the worker processes now and warm them (the formats' imports and font
        index), in the background -- at the application's start, say, so no call waits for
        a cold worker; ``wait`` returns once they are ready.  A runner without ``warm``
        is left as it is."""
        pool = self.pool
        warm = getattr(pool, "warm", None)
        if warm is not None:
            warm(wait=wait)

    def close(self) -> None:
        with self._pool_lock:
            pool, self._pool = self._pool, None
            self._snapshot_slots = None
        if pool is not None:
            pool.close()

    def __enter__(self) -> "Toolbox":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def dispatch(self, session: Session | SessionView, name: str, arguments: Any, *,
                 roles: str | Iterable[str] | None = None,
                 context: Mapping[str, Any] | None = None) -> Result:
        """Run one call and return its result; never raises for the model's mistakes.

        ``session`` is a session or a view of one (:meth:`Session.view`), whose roles the
        call is held to and whose context its handlers see (:attr:`Call.caller`).
        ``roles`` narrows them for this call alone, ``context`` adds to the context: what a
        view does, per call.  Roles the session (view) does not have are a ``ValueError``
        -- the application's mistake, not the model's.

        In a worker process that is still starting -- a main module with no ``__main__``
        guard, imported again there -- it stops that process instead (see
        :mod:`.worker`): the program's work does not run once more in each worker."""
        return self._dispatch(session, name, arguments, roles=roles, context=context)

    def _dispatch(self, session: Session | SessionView, name: str, arguments: Any, *,
                  roles: str | Iterable[str] | None = None,
                  context: Mapping[str, Any] | None = None,
                  holding: threading.Event | None = None, turn: "_Turn | None" = None) -> Result:
        """:meth:`dispatch`; ``holding`` is set once the call holds its documents' locks, or
        has ended without them (:meth:`dispatch_many`: an edit after a read waits for it);
        ``turn``: the changes the turn's calls made (:class:`_Turn`)."""
        refuse_while_bootstrapping()
        try:
            return self._dispatch_held(session, name, arguments, roles, context, holding,
                                       turn)
        finally:
            if holding is not None:
                holding.set()

    def _dispatch_held(self, session: Session | SessionView, name: str, arguments: Any,
                       roles: str | Iterable[str] | None, context: Mapping[str, Any] | None,
                       holding: threading.Event | None, turn: "_Turn | None" = None) -> Result:
        session, roles, caller = _caller(session, roles, context)
        started = time.perf_counter()
        tool: Tool | None = None
        doc_ids: list[str] = []
        primary: DocumentEntry | None = None
        entries: dict[str, DocumentEntry] = {}
        clean: Any = arguments
        notes: list[str] = []       # aliases read: warnings first
        comments: list[dict[str, Any]] = []   # a batch's ops' comments, for the log
        block: dict[str, Any] = {}  # round 45 (lane B): how a call on a draft went
        # The version a call that failed under its documents' locks ran on, read there: once
        # they are let go another caller may change the document before the failure is
        # made a result (0.112.1).
        failed_at: int | None = None
        try:
            tool = self.tool(name, notes, roles)
            name = tool.name
            _check_role(tool, None, roles)
            clean = self._arguments(tool, arguments, notes, comments, roles)
            _check_role(tool, clean, roles)
            if tool.composite:
                doc_ids = self._batch_documents(clean)
                if not clean.get("atomic"):
                    # An op naming no open document fails alone, when it runs.
                    doc_ids = [doc for doc in doc_ids if doc in session.documents]
                found = self._batch_sources(session, clean)
            else:
                doc_ids = tool.doc_ids(clean)
                clean, found = self._sources(
                    session, tool, clean,
                    refine=self._source_refiner(session, doc_ids, tool, clean))
            reading = [doc for doc in found if doc not in doc_ids]
            # Round 45: a call that only reads works on each document's committed version
            # and never waits for an edit in flight (Session.read_lock); one that changes a
            # document keeps the version it leaves as committed, before it lets the lock go.
            reads_only = self._reads_only(tool, clean)
            counted = self._enter(session, doc_ids)
            editing = _Editing([] if reads_only else [entry for entry, _n in counted.values()])
            try:
                # Round 45 (lane B): a changing call the format admits, beside another
                # edit of its document, runs on a draft (blocks.py); else as before.
                others: list[str] = []
                drafted = (None if reads_only else
                           self._block_entry(session, tool, clean, doc_ids, reading, roles,
                                             counted, editing, others))
                outcome = None
                if drafted is not None:
                    if holding is not None:
                        holding.set()
                    editing.drafting(drafted)
                    outcome = self._run_block(session, tool, clean, drafted, roles, caller,
                                              turn, block, editing,
                                              [*reading, *others], doc_ids)
                    editing.drafted()
                if outcome is not None:
                    result, entries, primary = outcome
                else:
                    if not reads_only:
                        # A long draft in flight would be made again were this call -- which
                        # takes the documents' locks for its whole run -- to change them first:
                        # it waits for those drafts to be applied (or to take the lock) first.
                        waited = sum(entry.wait_for_long_drafts(session.limits.block_redo_cpu)
                                     for entry, _n in counted.values())
                        if waited >= 0.001:
                            block["waited"] = round(waited, 3)
                    locking = session.read_lock if reads_only else session.lock
                    # Round 45 (stage 2): a changing call's checks run on the version it made
                    # once its locks are let go (After), where its format copies documents.
                    after = None if reads_only else After()
                    with locking(doc_ids + reading) as locked:
                        if holding is not None:
                            holding.set()
                        entries = {entry.doc_id: entry for entry in locked}
                        primary = entries[doc_ids[0]] if doc_ids else None
                        try:
                            result = self._run(session, tool, clean, entries, primary, reading,
                                               roles=roles, caller=caller, turn=turn, after=after)
                            if primary is not None and result.version is None:
                                # The version the call left, under its lock (round 45: its
                                # checks run after the lock is let go, when another call may
                                # change it).
                                result.version = primary.version
                        except Exception:
                            # Failed: the version it ran on (and left: a failed call changes
                            # nothing), read under its lock, as a call that ends does.
                            if primary is not None:
                                failed_at = primary.version
                            raise
                        finally:
                            if after is not None:
                                for entry in locked:
                                    entry.commit(session.formats.get(entry.kind))
                                after.committed(locked)
                    editing.done()
                    if after is not None:
                        after.run(result)
            finally:
                editing.done()
                for entry, _count in counted.values():
                    entry.leave()
        except ToolError as error:
            result = Result.failure(error, version=failed_at)
        except Exception as exc:  # noqa: BLE001 -- the model gets an error result
            result = Result.failure(self.map_exception(exc), version=failed_at)
        result.warnings[:0] = notes
        if primary is None and result.ok and result.doc is None:
            # A call that names no document and makes one (new_document, open_document):
            # its doc is the one it made, as a later call's is the one it names.
            made = [session.documents[doc] for doc in result.created if doc in session.documents]
            if len(made) == 1:
                primary = made[0]
        if primary is not None:
            result.doc = primary.doc_id
            if result.version is None:      # a snapshot's (Call.unlocked), else the latest
                result.version = primary.version
        result.tool = name
        result.duration = time.perf_counter() - started
        if block:
            result.block = dict(block)
            _redo_timing(result, len(block.get("conflicts") or ()))
        self._record(session, name, doc_ids, clean, result, entries, roles, caller, comments)
        return result

    def dispatch_many(self, session: Session | SessionView, calls: Sequence[tuple[str, Any]],
                      *, max_workers: int = 8, roles: str | Iterable[str] | None = None,
                      context: Mapping[str, Any] | None = None) -> list[Result]:
        """Run one turn's calls: per document in the order given, documents concurrently.
        ``session``, ``roles`` and ``context`` as for :meth:`dispatch`, for every call.

        Reads of one document run side by side (round 28, P2): a call that only reads
        (:meth:`_side_by_side`) waits for the turn's earlier edits of its documents, never
        for its earlier reads; an edit waits for the earlier edits to end and for the
        earlier reads to hold their locks -- a read that works on a snapshot
        (:meth:`Call.on_snapshot`) lets them go while it works, so the edit runs then, and
        the read's result is still of the document before the edit.  Every result is the
        one the calls in the order given would have."""
        refuse_while_bootstrapping()
        if not calls:
            return []
        session, roles, caller = _caller(session, roles, context)
        documents = [self._documents_named(session, name, arguments)
                     for name, arguments in calls]
        reads = [bool(documents[index]) and self._side_by_side(name, arguments)
                 for index, (name, arguments) in enumerate(calls)]
        futures: list[Future[Result]] = []
        holding: list[threading.Event] = []
        turn = _Turn()
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(calls)))) as executor:
            for index, (name, arguments) in enumerate(calls):
                ended: list[Future[Result]] = []
                held: list[threading.Event] = []
                for j in range(index):
                    if (documents[j] and documents[index]
                            and not documents[j] & documents[index]):
                        continue
                    if reads[j] and reads[index]:
                        continue
                    if reads[j]:
                        held.append(holding[j])
                    else:
                        ended.append(futures[j])
                holding.append(threading.Event())
                futures.append(executor.submit(self._after, ended, held, holding[index],
                                               session, name, arguments, roles, caller, turn))
            return [future.result() for future in futures]

    def _after(self, ended: list[Future[Result]], held: list[threading.Event],
               holding: threading.Event, session: Session, name: str, arguments: Any,
               roles: Sequence[str], caller: Mapping[str, Any], turn: "_Turn") -> Result:
        for future in ended:
            future.exception()  # wait; its outcome is its own
        for event in held:
            event.wait()        # the earlier read holds its locks (or has ended)
        return self._dispatch(session, name, arguments, roles=roles, context=caller,
                              holding=holding, turn=turn)

    @staticmethod
    def _reads_only(tool: Tool, arguments: Mapping[str, Any]) -> bool:
        """Whether a validated call only reads its documents, so it may work on their
        committed versions (round 45, :meth:`Session.read_lock`): a ``read_batch``, or a
        reading call of a tool of no role of its own (not a session tool: undo,
        close_document, save_document, which act on the document itself)."""
        if tool.composite:
            return not tool.mutates
        return tool.role is None and not tool.changes(arguments)

    # -- edits on drafts (round 45, lane B; blocks.py) -----------------------------------------

    @staticmethod
    def _enter(session: Session, doc_ids: Sequence[str]) -> dict[str, tuple[DocumentEntry, int]]:
        """Count the call as running on each open document it names
        (:meth:`DocumentEntry.enter`): ``doc -> (entry, how many run now)``."""
        counted: dict[str, tuple[DocumentEntry, int]] = {}
        for doc in dict.fromkeys(doc_ids):
            entry = session.documents.get(doc)
            if entry is not None:
                counted[doc] = (entry, entry.enter())
        return counted

    def _block_entry(self, session: Session, tool: Tool, clean: Mapping[str, Any],
                     doc_ids: Sequence[str], reading: Sequence[str], roles: Sequence[str],
                     counted: Mapping[str, tuple[DocumentEntry, int]],
                     editing: "_Editing", others: list[str] | None = None
                     ) -> DocumentEntry | None:
        """The document a changing call runs on a draft of (:mod:`.blocks`), or ``None``: it
        names one document (round 50: and may read rows of others, ``reading`` -- a table
        of another open document, read at its committed version: :meth:`_run_block`; round
        51: or it is a batch whose changes are all of one document, its other ops reading
        other documents -- a dataset derived of another document, say -- which go in
        ``others``: :meth:`_batch_target`), its format drafts and admits it, and -- with
        ``block_edits="auto"`` -- another call edits that document now (its change not yet
        made: :meth:`DocumentEntry.begin_edit`), or the format says the call is worth a
        draft alone (``BlockEdits.alone``: one that may run long).  A draft costs a copy
        of the blocks changed and a check of them; beside reads and checks alone, which
        never wait for an edit, nothing waits that it would spare."""
        if self.block_edits == "off" or any(doc not in session.documents for doc in reading):
            return None
        target: str | None = doc_ids[0] if len(doc_ids) == 1 else None
        read: list[str] = []
        if target is None and tool.composite:
            target, read = self._batch_target(clean, doc_ids, roles)
        found = counted.get(target) if target is not None else None
        if found is None or any(doc not in session.documents for doc in read):
            return None
        entry, _running = found
        fmt = session.formats.get(entry.kind)
        hook = getattr(fmt, "blocks", None)
        if hook is None or getattr(fmt, "read_copy", None) is None or entry.pinned is not None:
            return None
        try:
            admitted = self._admitted(hook, tool, clean, entry.kind, entry.doc_id, roles,
                                      session)
            if admitted and self.block_edits == "auto" and editing.beside(entry) == 0:
                admitted = self._alone(hook, tool, clean, roles)
        except Exception:  # noqa: BLE001 -- not admitted: the call runs as before
            logger.exception("the %s format's admits failed", entry.kind)
            return None
        if admitted and others is not None:
            others[:] = read
        return entry if admitted else None

    def _batch_target(self, clean: Mapping[str, Any], doc_ids: Sequence[str],
                      roles: Sequence[str]) -> tuple[str | None, list[str]]:
        """Round 51 (R105): the one document a batch naming several changes, and the others,
        which it only reads -- ``(doc, others)``; ``(None, [])`` when its ops before its last
        change change more than one document, or its reads after it name another (they run
        on the version it makes, once applied: :meth:`_finish_block`).  Which of its reads
        may go on a draft is :meth:`_admitted`'s."""
        ops = clean.get("ops") or []
        split = self._trailing(ops, roles)
        changed: list[str] = []
        late: set[str] = set()
        for index, op in enumerate(ops):
            try:
                op_tool, op_clean = self._prepare(op, None, roles)
            except ToolError:
                continue        # fails alone, reading nothing
            named = op_tool.doc_ids(op_clean)
            if index >= split:
                late.update(named)
            elif op_tool.changes(op_clean):
                changed += [doc for doc in named if doc not in changed]
        if len(changed) != 1 or not late <= set(changed):
            return None, []
        return changed[0], [doc for doc in doc_ids if doc != changed[0]]

    def _alone(self, hook: Any, tool: Tool, clean: Mapping[str, Any], roles: Sequence[str]) -> bool:
        """Whether the format says an admitted call is worth a draft with no other edit in
        flight (``BlockEdits.alone``): a batch when one of its ops is."""
        alone = getattr(hook, "alone", None)
        if alone is None:
            return False
        if tool.composite:
            for op in (clean.get("ops") or [])[:self._trailing(clean.get("ops") or [], roles)]:
                try:
                    op_tool, op_clean = self._prepare(op, None, roles)
                except ToolError:
                    continue
                if op_tool.changes(op_clean) and alone(op_tool.name, op_clean):
                    return True
            return False
        return bool(alone(tool.name, clean))

    def _admitted(self, hook: Any, tool: Tool, clean: Mapping[str, Any], kind: str, doc: str,
                  roles: Sequence[str], session: Session | None = None) -> bool:
        """Whether the format admits the call on a draft: a changing call of its own tool
        on ``doc`` alone (no session tool, no work ahead), or a ``batch`` whose every op
        before its last change is one -- its reads after the last change read the version
        the batch makes, once applied; an op that cannot be validated fails alone, reading
        nothing.  Round 51 (R105): or, before its last change, a read of other documents
        only (:meth:`_reads_elsewhere`: a dataset derived of another document, its
        rows), read at their committed version and checked again when the change is
        applied (:meth:`_run_block`)."""
        if tool.composite:
            if not tool.mutates:
                return False
            ops = clean.get("ops") or []
            split = self._trailing(ops, roles)
            if not split:
                return False
            for op in ops[:split]:
                try:
                    op_tool, op_clean = self._prepare(op, None, roles)
                except ToolError:
                    continue
                if not op_tool.changes(op_clean) and session is not None \
                        and self._reads_elsewhere(session, op_tool, op_clean, doc):
                    continue
                if not self._admitted(hook, op_tool, op_clean, kind, doc, roles):
                    return False
            return True
        return (tool.role is None and tool.changes(clean) and tool.ahead_for(kind) is None
                and tool.doc_ids(clean) == [doc] and bool(hook.admits(tool.name, clean)))

    def _reads_elsewhere(self, session: Session, tool: Tool, clean: Mapping[str, Any],
                         doc: str) -> bool:
        """Whether a batch's op, before its last change, only reads -- a tool of no role of
        its own, no batch -- and reads nothing of ``doc``, the document drafted: neither
        names it nor takes rows from it (a source, a reference in its text).  Round 51
        (R105): it may run in the batch on a draft, the documents it reads read at their
        committed version (:meth:`_run_block`)."""
        if tool.composite or tool.role is not None or doc in tool.doc_ids(clean):
            return False
        try:
            found = self._sources(session, tool, dict(clean))[1]
        except ToolError:
            return True         # it fails alone, reading nothing
        return doc not in found

    def _run_block(self, session: Session, tool: Tool, clean: dict[str, Any],
                   entry: DocumentEntry, roles: Sequence[str], caller: Mapping[str, Any],
                   turn: "_Turn | None", stats: dict[str, Any], editing: "_Editing",
                   reading: Sequence[str] = (), doc_ids: Sequence[str] = ()
                   ) -> tuple[Result, dict[str, DocumentEntry], DocumentEntry] | None:
        """:meth:`_drafted_run`; round 51: a batch's answer names the document its ops name
        first, at the version read (as under its lock), and its log is every named one's."""
        seen: dict[str, tuple[DocumentEntry, int]] = {}
        outcome = self._drafted_run(session, tool, clean, entry, roles, caller, turn, stats,
                                    editing, reading, doc_ids, seen)
        if outcome is None or not doc_ids or doc_ids[0] == entry.doc_id:
            return outcome
        result, entries, _primary = outcome
        first = session.documents.get(doc_ids[0])
        if first is None:
            return outcome
        result.version = seen[first.doc_id][1] if first.doc_id in seen else first.version
        named = {doc: session.documents[doc] for doc in doc_ids if doc in session.documents}
        return result, {**named, **entries}, first

    def _drafted_run(self, session: Session, tool: Tool, clean: dict[str, Any],
                     entry: DocumentEntry, roles: Sequence[str], caller: Mapping[str, Any],
                     turn: "_Turn | None", stats: dict[str, Any], editing: "_Editing",
                     reading: Sequence[str], doc_ids: Sequence[str],
                     seen: dict[str, tuple[DocumentEntry, int]]
                     ) -> tuple[Result, dict[str, DocumentEntry], DocumentEntry] | None:
        """The call on a draft of ``entry``'s committed version, its change applied under
        the document's lock (:mod:`.blocks`): ``(result, entries, primary)``; ``None`` when
        it is to run on the document under its lock instead -- the format cannot draft it,
        the draft did what a patch cannot carry, or another call's change came first
        :attr:`~.limits.Limits.block_tries` times.  ``stats`` gets how it went, for
        :attr:`Result.block`: ``tries``, each conflict's reason, why it ran under the lock.

        Round 50: ``reading``, the other documents the call takes rows from (a ``t:``
        table), are read as a call that only reads them reads them
        (:meth:`Session.read_lock`: at their committed version, or under their lock where
        their format makes no copy), each one's version kept; under the document's lock
        the change applies only when every one is at that version still -- else, as when
        a dataset it read changed, it is prepared again on what is committed now.  Their
        locks are held with the document's for that check and the change (briefly), so the
        values written are the source's when the change is made, as under both locks.

        Round 51 (R105): a batch's ``reading`` also holds the documents its reads before its
        last change name (``doc_ids``, the batch's documents, the first of which its result
        names, as when it runs under their locks); each op holds the locks of those it reads
        only while it runs (:class:`_Reads`), so the batch's own work on the draft waits for
        no change of them, nor they for it.  The datasets its ops derive -- of documents
        other than this one -- are the later ops' on the draft and the session's once its
        change is applied, kept under the same locks (:meth:`_keep_datasets`)."""
        fmt = session.formats[entry.kind]
        hook = fmt.blocks
        doc = entry.doc_id
        tries = max(1, int(session.limits.block_tries))
        stats.update(tries=0, conflicts=[])
        for _attempt in range(tries):
            stats["tries"] += 1
            committed = entry.committed
            if committed is None:
                with entry.lock:
                    entry.commit(fmt)
                    committed = entry.committed
            if committed is None:
                stats["lock"] = "no committed version"
                return None
            try:
                draft = hook.draft(entry.document, committed)
            except Exception:  # noqa: BLE001 -- no draft: the call runs as before
                logger.exception("the %s format could not draft %s", entry.kind, doc)
                draft = None
            if draft is None:
                stats["lock"] = "no draft"
                return None
            current: Any = None
            read: dict[str, tuple[DocumentEntry, int]] = {}
            try:
                drafted = entry.drafted(committed, draft.document)
                keys = set(drafted.keys)
                drafting = _Drafting()
                after = After()
                spent = time.thread_time()
                try:
                    if tool.composite:
                        # Round 51: each op holds the locks of what it reads, as it runs.
                        drafting.reads = _Reads(session, reading,
                                                [d for d in doc_ids if d != doc])
                        with drafted.lock:
                            result = self._run(session, tool, dict(clean),
                                               {doc: drafted, **drafting.reads.entries},
                                               drafted, tuple(reading), roles=roles,
                                               caller=caller, after=after, drafting=drafting)
                        read = drafting.reads.read
                        seen.clear()
                        seen.update(read)
                    else:
                        with session.read_lock(reading) as readers, drafted.lock:
                            read = {reader.doc_id: (reader.live or reader, reader.version)
                                    for reader in readers}
                            result = self._run(session, tool, dict(clean),
                                               {doc: drafted, **{r.doc_id: r for r in readers}},
                                               drafted, tuple(reading), roles=roles,
                                               caller=caller, after=after, drafting=drafting)
                except ToolError as refused:
                    if _drew(hook, draft):
                        # It drew ids before it failed: under the lock it would consume them,
                        # so later calls draw others -- it runs there instead (round 47).
                        stats["lock"] = "drew ids, then failed"
                        return None
                    # Refused on the committed version: of that version, nothing changed.
                    return Result.failure(refused, version=committed.version), {doc: entry}, entry
                except Exception as exc:  # noqa: BLE001
                    mapped = self.map_exception(exc)
                    if mapped.code == "internal":
                        stats["lock"] = "failed on its draft"
                        return None     # not the call's own refusal: run it as before
                    if _drew(hook, draft):
                        stats["lock"] = "drew ids, then failed"
                        return None
                    return Result.failure(mapped, version=committed.version), {doc: entry}, entry
                # Its preparation's own CPU (the thread's: what it would cost again).
                spent = time.thread_time() - spent
                stats["cpu"] = round(stats.get("cpu", 0.0) + spent, 3)
                context = drafting.context
                if not drafting.local or context is None:
                    stats["lock"] = "not a patch: datasets made, or work on the whole document"
                    return None
                if any(stale or found.stale or found.doc == doc
                       or any(other.doc == doc for other in found.also)
                       for found, stale in context.datasets_seen.values()):
                    # A stale dataset's warning says the versions as they are when the call
                    # ends; one of this document's goes stale with the call's own change.
                    stats["lock"] = "a dataset of this document, or a stale one"
                    return None
                if any(found.doc == doc or any(other.doc == doc for other in found.also)
                       or context.pending_stale(found) for found in context.datasets.values()):
                    # Round 51: one derived of this document, as the call leaves it, or
                    # stale already (its warning says the versions when the call ends).
                    stats["lock"] = "a dataset derived of this document, or a stale one"
                    return None
                if drafting.reads is not None and drafting.reads.moved:
                    # Two of its ops read another document at two versions: not one state.
                    stats["conflicts"].append(f"{drafting.reads.moved} changed")
                    continue
                if any(other != doc for other in context.refs):
                    stats["lock"] = "refs of another document"
                    return None
                made_keys = {key: value for key, value in drafted.keys.items() if key not in keys}
                if not result.ok or drafted.version == committed.version:
                    # Failed, or changed nothing: of the committed version, nothing to apply.
                    if context.refs or made_keys:
                        stats["lock"] = "named or keyed, changing nothing"
                        return None     # (what it named is the document's: as before)
                    if _drew(hook, draft):
                        stats["lock"] = "drew ids, then changed nothing"
                        return None     # (under the lock the ids drawn are spent: as before)
                    if drafting.datasets is not None and context.datasets:
                        # Round 51: its datasets the session's, as of the versions read.
                        with session.lock([other for other in read if other in session.documents]):
                            reason = self._block_sources_changed(session, context, read)
                            if reason is not None:
                                stats["conflicts"].append(reason)
                                continue
                            if not self._block_datasets(session, context, drafting, stats):
                                return None
                    result.version = committed.version
                    after.retarget(drafted, entry)
                    after.keep(entry, committed)
                    editing.done()
                    return self._finish_block(result, drafting, after, committed, entry), \
                        {doc: entry}, entry
                try:
                    patch = hook.prepared(draft, result, clean)
                except Exception:  # noqa: BLE001 -- not a patch: the call runs as before
                    logger.exception("the %s format could not make a patch of %s", entry.kind, doc)
                    patch = None
                if patch is None:
                    stats["lock"] = getattr(draft, "why", None) or "not a patch"
                    return None
                # Round 50: and the documents it read rows of, so none changes between the
                # check that each is at the version read and the change applied.
                with session.lock([doc, *(other for other in read if other in session.documents)]):
                    if session.documents.get(doc) is not entry:
                        stats["lock"] = "closed"
                        return None     # closed meanwhile: the call says so, as before
                    reason = self._block_reads_changed(session, entry, committed, context,
                                                       clean, made_keys, read)
                    plan = None
                    op_changes = None
                    if reason is None:
                        try:
                            plan = hook.check(entry.document, patch,
                                              entry.parts_changed_since(committed.seq))
                            if context.op_changes and hasattr(hook, "replay_changes"):
                                # What each op of a batch changed, as the document's.
                                op_changes = hook.replay_changes(entry.document, plan,
                                                                 context.op_changes)
                        except BlockConflict as conflict:
                            reason, plan = str(conflict) or "conflict", None
                        except Exception:  # noqa: BLE001 -- nothing applied: run it as before
                            logger.exception("checking a draft's patch of %s failed", doc)
                            stats["lock"] = "the patch could not be checked"
                            return None
                    if plan is None:
                        stats["conflicts"].append(reason)
                        if spent > session.limits.block_redo_cpu:
                            # A long preparation is not made again: once more, under the lock.
                            stats["lock"] = "a conflict after a long preparation"
                            return None
                        continue        # its lock let go: prepared again, on what is committed now
                    if not self._block_datasets(session, context, drafting, stats):
                        return None
                    version = entry.version
                    before = entry.snapshot()
                    try:
                        with entry.watch() as change:
                            with entry.batch(StepOwner(dict(caller), tool.name)):
                                renames = dict(hook.apply(entry.document, plan))
                            entry.bump()
                    except Exception:  # noqa: BLE001 -- rolled back: the call runs as before
                        logger.exception("applying a draft's patch to %s failed", doc)
                        stats["lock"] = "the patch did not apply"
                        return None
                    entry.keep_change(version, change)
                    entry.note_change(change.parts if change is not None else None, version)
                    try:
                        hook.settle(entry.document, plan, touched=context.touched.setdefault(doc, []),
                                    before=before)
                    except Exception:  # noqa: BLE001 -- applied: the call is done, said in the log
                        logger.exception("settling a draft's patch of %s failed", doc)
                    rename = self._block_result(session, entry, hook, result, context, renames,
                                                change, made_keys, turn, version, op_changes)
                    entry.commit(fmt)
                    after.retarget(drafted, entry)
                    after.committed([entry])
                    mine = entry.committed
                    if (version == committed.version and entry.version == drafted.version
                            and all(old == new for old, new in renames.items())):
                        current = mine      # the draft is this version: kept to reuse
                editing.done()
                return (self._finish_block(result, drafting, after, mine, entry, rename),
                        {doc: entry}, entry)
            finally:
                hook.release(draft, current=current)
        stats["lock"] = "conflicts"
        return None

    def _block_reads_changed(self, session: Session, entry: DocumentEntry, committed: Any,
                             context: CallContext, clean: Mapping[str, Any],
                             made_keys: Mapping[tuple[str, str], Any],
                             read: Mapping[str, tuple[DocumentEntry, int]] | None = None
                             ) -> str | None:
        """What a call on a draft read of the session that is not as it was then, under the
        document's lock (:meth:`_run_block`); ``None``: nothing.  The refs, when it used or
        made one; its retry keys; the datasets it named (the same, and as stale); the other
        documents it read rows of (round 50: open still, at the version read); whether the
        document is tracked."""
        if (context.refs or context.refs_read or _uses_refs(clean)) and entry.refs != committed.refs:
            return "the document's refs changed"
        for key in made_keys:
            if key in entry.keys:
                return f"retry key {key[1]!r} was used meanwhile"
        reason = self._block_sources_changed(session, context, read)
        if reason is not None:
            return reason
        if (entry.tracking or None) != (committed.tracking or None):
            return "tracking was switched"
        return None

    @staticmethod
    def _block_sources_changed(session: Session, context: CallContext,
                               read: Mapping[str, tuple[DocumentEntry, int]] | None) -> str | None:
        """Of :meth:`_block_reads_changed`: the datasets a call on a draft named, and the
        other documents it read, as they were when it read them (``None``) or not."""
        for id_, (found, stale) in context.datasets_seen.items():
            if session.datasets.get(id_) is not found or found.stale != stale:
                return f"dataset d:{id_} changed"
        for other, (live, version) in (read or {}).items():
            if session.documents.get(other) is not live or live.version != version:
                return f"{other} changed"
        return None

    def _block_datasets(self, session: Session, context: CallContext, drafting: "_Drafting",
                        stats: dict[str, Any]) -> bool:
        """Round 51 (R105): the datasets a batch on a draft derived, the session's -- under
        the locks of the documents it read, each at the version read, as under their locks
        (:meth:`_keep_datasets`; its stale warnings go to the batch's result).  ``False``:
        they could not be kept (``Limits.max_datasets``): the batch runs under the locks
        instead, where it says so."""
        if drafting.datasets is None or not context.datasets:
            return True
        try:
            self._keep_datasets(session, context, drafting.datasets)
        except ToolError:
            stats["lock"] = "datasets over the limit"
            return False
        return True

    def _block_result(self, session: Session, entry: DocumentEntry, hook: Any, result: Result,
                      context: CallContext, renames: Mapping[str, str], change: Any,
                      made_keys: Mapping[tuple[str, str], Any], turn: "_Turn | None",
                      version: int, op_changes: Sequence[tuple[int, str, Any]] | None = None
                      ) -> Callable[[Any], Any] | None:
        """A draft's result as the document's, its patch applied (under the lock): the ids
        it made under the names the document gave them, its refs and retry keys kept, its
        version, what it changed (``touched``, ``touched_by_op``) read from the document.
        The renaming, for what a batch says once its reads after its
        last change are done (``None``: none)."""
        renames = {old: new for old, new in renames.items() if old != new}
        rename: Callable[[Any], Any] | None = None
        if renames:
            def rename(value: Any) -> Any:
                return hook.rename(value, renames)

            result.summary = rename(result.summary)
            result.warnings = rename(result.warnings)
            result.changed = rename(result.changed)
            result.created = rename(result.created)
            result.removed = rename(result.removed)
            result.renamed = rename(result.renamed)
            result.refs = rename(result.refs)
            result.data = rename(result.data)
            result.checks = rename(result.checks)
            result.touched_by_op = {op: rename(found) for op, found in result.touched_by_op.items()}
            context.refs = {doc: rename(refs) for doc, refs in context.refs.items()}
        self._keep_refs({entry.doc_id: entry}, context, [entry.doc_id])
        for key in made_keys:
            entry.keys[key] = result
        result.version = entry.version
        # What it changed, read from the document (not from the draft: the document keeps
        # what it read of its earlier states, so only the blocks changed are read again).
        result.touched = self._touched(session, entry, change)
        if op_changes is None and not renames:
            # Not replayed (the body as the draft's, nothing renamed): each op's change on
            # the draft is the document's.
            op_changes = context.op_changes
        if op_changes is not None:
            # A batch's ops' changes as the document's (BlockEdits.replay_changes), read as
            # _batch_touched reads a batch's on the document.
            result.touched_by_op = {}
            for op, _doc, found in sorted(op_changes, key=lambda item: item[0]):
                said = self._touched(session, entry, found)
                if said:
                    result.touched_by_op.setdefault(op, []).extend(said)
        if turn is not None:
            turn.made({entry.doc_id: entry}, {entry.doc_id: version})
        return rename

    def _finish_block(self, result: Result, drafting: "_Drafting", after: "After",
                      committed: Any, entry: DocumentEntry,
                      rename: Callable[[Any], Any] | None = None) -> Result:
        """After a draft's change is applied (or it changed nothing): a batch's reads after
        its last change, on the version it made (``committed``'s copy), then its checks
        (:class:`After`).  A batch whose every op failed is refused here, as before."""
        try:
            if drafting.finish is not None:
                reader = committed.entry(entry) if committed is not None else None
                if reader is not None:
                    with reader.lock:
                        result = drafting.finish({entry.doc_id: reader}, rename)
                else:
                    with entry.lock:
                        result = drafting.finish({entry.doc_id: entry}, rename)
        except ToolError as error:
            failed = Result.failure(error, version=committed.version if committed else None)
            return failed
        after.run(result)
        return result

    def _side_by_side(self, name: str, arguments: Any) -> bool:
        """Whether a call of a turn only reads its documents, so it may run beside the
        turn's other reads of them (:meth:`dispatch_many`): a reading call of a tool of no
        role of its own (not a session tool: undo, close_document, save_document) that
        takes no datasets, sources or text facts, whose values an earlier call of the turn
        may make.  Anything that cannot be told is ``False``: in order, as before."""
        try:
            tool = self.tools[self.tool_named(name)]
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
            if not isinstance(parsed, Mapping) or tool.composite or tool.role is not None:
                return False
            if tool.datasets or tool.sources or tool.facts:
                return False
            return not tool.changes(parsed)
        except Exception:  # noqa: BLE001 -- the call will fail on its own, in order
            return False

    def _documents_named(self, session: Session, name: str,
                         arguments: Any) -> frozenset[str]:
        """The documents a call will lock, as far as can be told before validating it."""
        try:
            tool = self.tools[self.tool_named(name)]
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
            if not isinstance(parsed, Mapping):
                return frozenset()
            if tool.composite:
                return frozenset(self._batch_documents(parsed)
                                 + self._batch_sources(session, parsed))
            try:
                found = self._sources(session, tool, dict(parsed))[1]
            except ToolError:
                found = []
            return frozenset(tool.doc_ids(parsed) + found)
        except Exception:  # noqa: BLE001 -- the call will fail on its own, in order
            return frozenset()

    def _arguments(self, tool: Tool, arguments: Any, notes: list[str] | None = None,
                   comments: list[dict[str, Any]] | None = None,
                   roles: Sequence[str] | None = None) -> dict[str, Any]:
        """The call's arguments, parsed, aliases read (``notes`` gets a line for each) and
        validated: ``invalid_arguments`` otherwise.  A batch's ops' ``comment`` and
        ``note`` are taken out first, to ``comments`` (:func:`_op_comments`)."""
        notes = notes if notes is not None else []
        if isinstance(arguments, (str, bytes)):
            try:
                arguments = json.loads(arguments or "{}")
            except ValueError as exc:
                raise ToolError("invalid_arguments", f"the arguments are not JSON: {exc}") from None
        if arguments is None:
            arguments = {}
        if tool.composite:
            arguments = _op_comments(arguments, comments if comments is not None else [])
        arguments = _aliased(tool, arguments, notes)
        arguments = _defaulted(tool, arguments, notes)
        arguments = _json_strings(tool.schema, arguments)
        try:
            clean = validate_call(tool.schema, arguments, notes)
        except CallError as exc:
            message = exc.message
            if exc.field == "action" and isinstance(arguments, Mapping):
                message = self._action_message(tool, arguments.get("action"), message, roles)
            if exc.field == "key" and tool.mutates:
                message = (f"{tool.name} takes no key: a retry key is for the tools that "
                           "create something, so a repeat makes nothing twice; this one is "
                           "safe to repeat as it is -- resend without key")
            raise ToolError("invalid_arguments", message, field=exc.field,
                            valid_options=exc.valid_options) from None
        for group in tool.exactly_one:
            given = [name for name in group if name in clean]
            if len(given) != 1:
                raise ToolError("invalid_arguments",
                                f"give exactly one of {', '.join(group)}"
                                + (f"; got {', '.join(given)}" if given else ""),
                                field=given[1] if len(given) > 1 else group[0],
                                valid_options=list(group))
        return clean

    def _action_message(self, tool: Tool, value: Any, message: str,
                        roles: Sequence[str] | None) -> str:
        """The message for an ``action`` ``tool`` lacks: the action of its own it means with
        a verb read (``add_row``: ``insert_row``), else the other tools that have it, of
        ``roles`` (:func:`.hints.action_hints`); the validator's message otherwise."""
        own, found = action_hints(tool, value, self.tools, self.tool_hints)
        base = message.split("; did you mean", 1)[0]
        if own is not None:
            return f"{base}; did you mean {own!r}?"
        usable = [hint for hint in found
                  if self._hint_role(hint, tuple(ROLES if roles is None else roles)) is None]
        if not usable:
            return message
        return f"{base}; use " + ", or ".join(map(str, usable))

    def _run(self, session: Session, tool: Tool, arguments: dict[str, Any],
             entries: dict[str, DocumentEntry], primary: DocumentEntry | None,
             reading: Sequence[str] = (), *, roles: Sequence[str] = ROLES,
             caller: Mapping[str, Any] = NO_CONTEXT, turn: "_Turn | None" = None,
             failed_refs: Mapping[str, int] | None = None,
             holding: Mapping[str, DocumentEntry] | None = None,
             after: "After | None" = None, drafting: "_Drafting | None" = None) -> Result:
        """One call, its documents' locks held (``entries``; ``reading``: those it only
        takes rows from).  ``failed_refs``: for a batch's read after its last change, the
        refs its failed ops would have made (:meth:`CallContext.missing_ref`).
        ``holding``: every lock this thread holds, where that is more than ``entries`` --
        an op of a ``read_batch`` or a batch's read after its last change: the whole
        batch's.  :meth:`Call.unlocked` lets all of them go and takes them back in
        document order (round 35: letting the op's alone go, and taking it back while
        still holding another, waited on a call that held the one and wanted the other).
        ``drafting`` (round 45, lane B): the call runs on a draft entry (:mod:`.blocks`);
        what is the session's beyond the draft is left for when its change is applied
        (:meth:`_run_block`), and what it did that a patch cannot carry is said there."""
        # The turn's changes so far, and the versions now: what this call changes is the
        # turn's, for its reads that let their locks go meanwhile (never warned about).
        since = turn.mark() if turn is not None else 0
        before = {doc: entry.version for doc, entry in entries.items()}
        if tool.composite and not tool.mutates:
            return self._run_reads(session, arguments, entries, roles=roles, caller=caller)
        if tool.composite:
            edited = {doc: entry for doc, entry in entries.items() if doc not in reading}
            outcome = self._run_batch(session, arguments, edited, primary,
                                      {doc: entries[doc] for doc in reading},
                                      roles=roles, caller=caller, after=after, drafting=drafting)
            if turn is not None:
                turn.made(entries, before)
            return outcome
        handler = self._handler(session, tool, arguments, primary)
        arguments = dict(arguments)
        ignored = tool.ignored_for(primary.kind if primary else None, arguments)
        changing = tool.changes(arguments)
        ahead = tool.ahead_for(primary.kind) if changing and primary is not None else None
        early: list[str] = []
        if ahead is not None and primary is not None:
            # The call's slow reading first, its locks let go where they may; after it the
            # call goes on as if it had just taken them (the document may have moved on).
            early = self._ahead(session, tool, ahead, arguments, entries, primary, roles,
                                caller)
            since = turn.mark() if turn is not None else 0
            before = {doc: entry.version for doc, entry in entries.items()}
        key = arguments.get("key") if changing and tool.retry_key \
            and isinstance(arguments.get("key"), str) else None
        if key is not None and primary is not None and (tool.name, key) in primary.keys:
            replay = primary.keys[(tool.name, key)]
            return Result(summary=f"already done with key {key!r}: nothing new was made",
                          created=list(replay.created), changed=list(replay.changed),
                          refs=dict(replay.refs), data=replay.data)
        context = _context(roles, caller)
        if failed_refs:
            context.failed_refs.update(failed_refs)
        if drafting is not None:
            drafting.context = context
        with collect_warnings() as caught:
            read: list[str] = []
            arguments = self._with_refs(tool, arguments, entries, primary, context, read)
            call = Call(self, session, tool, arguments, entries, primary, context)
            if changing:
                call._own_refs_first(arguments)
            call.result.warnings += ignored + early + read
            self._with_datasets(tool, arguments, call)
            arguments = self._with_facts(tool, arguments, call)
            # dispatch() holds each once; a batch's op lets every lock of the batch go.
            call.held = list((entries if holding is None else holding).values())
            with ExitStack() as watching:
                # Whatever the tool, what its document's content did (undo included).
                change = (watching.enter_context(primary.watch()) if primary is not None
                          else None)
                if changing and primary is not None:
                    with primary.batch(StepOwner(dict(caller), tool.name)):
                        returned = handler(call, **arguments)
                    primary.bump()
                else:
                    returned = handler(call, **arguments)
            if drafting is None and primary is not None and not call.released:
                # Round 45 (lane B): every change counted, by its parts, for the patches
                # prepared on an earlier version (DocumentEntry.parts_changed_since).
                if change is None and changing:
                    primary.keep_change(before.get(primary.doc_id), None)
                    primary.note_change(None, before.get(primary.doc_id))
                elif change is not None and change.changed:
                    primary.keep_change(before.get(primary.doc_id), change)
                    primary.note_change(change.parts, before.get(primary.doc_id))
            result = _as_result(returned, call.result)
            if drafting is not None and (call.deferred or context.datasets):
                # Work on the whole document as the call left it, or datasets for the
                # session: not what a patch of blocks carries.
                drafting.local = False
            if result.ok and call.deferred:
                self._do_deferred(tool.name, call.deferred, result)
            if result.ok and primary is not None and not call.released and drafting is None:
                # (A call that let its lock go -- a reading one, working on a snapshot --
                # changes nothing, and another call's change meanwhile is not its.  One on
                # a draft: read from the document once its change is applied.)
                result.touched = self._touched(session, primary, change)
            _report_facts(call, result)
            if call.version_used is not None:
                result.version = call.version_used
                # A reader's document is its live one's (round 45): moved on is the live's.
                now = (primary.live or primary).version if primary is not None else 0
                if (call.released and primary is not None
                        and now != call.version_used
                        and not (turn is not None and turn.explains(
                            primary.doc_id, call.version_used, now, since))
                        and _changes_touch(session, primary.live or primary, call.version_used,
                                           now, call.read_scope)):
                    result.warnings.append(moved_on(primary.doc_id, call.version_used, now))
            if changing and result.ok:
                self._keep_refs(entries, context, [primary.doc_id] if primary else [])
                if primary is not None:
                    own = result.checks

                    def checked(entry: DocumentEntry, result: Result = result,
                                own: dict = own, context: CallContext = context) -> None:
                        result.checks = {**self._checks(session, entry, context), **own}

                    if after is None or not after.defer(session, primary, checked):
                        checked(primary)
            if not result.ok:
                result.datasets = []
            elif drafting is None or not context.datasets:
                # (A draft's datasets are never the session's: the call runs again.)
                self._keep_datasets(session, context, result)
        result.warnings.extend(caught)
        if key is not None and primary is not None and result.ok:
            primary.keys[(tool.name, key)] = result
        if turn is not None and not call.released:
            turn.made(entries, before)
        return result

    def _ahead(self, session: Session, tool: Tool, ahead: Callable[..., Any],
               arguments: Mapping[str, Any], entries: dict[str, DocumentEntry],
               primary: DocumentEntry, roles: Sequence[str],
               caller: Mapping[str, Any]) -> list[str]:
        """A changing call's work ahead of its change (:attr:`Tool.aheads`, round 33):
        ``ahead(call, **arguments)`` -- the arguments as given, refs not resolved yet --
        before the undo step, :attr:`Call.may_unlock` true, so slow reading work runs with
        the locks let go (:meth:`Call.unlocked`).  Its warnings, for the call's result.  An
        exception it raises ends the call, which has changed nothing."""
        call = Call(self, session, tool, arguments, entries, primary, _context(roles, caller))
        call.held = list(entries.values())
        call.ahead = True
        with collect_warnings() as caught:
            ahead(call, **arguments)
        return call.result.warnings + caught

    def _handler(self, session: Session, tool: Tool, arguments: Mapping[str, Any],
                 primary: DocumentEntry | None) -> Callable[..., Any]:
        kind = primary.kind if primary else None
        if primary is None and tool.route is not None:
            kind = arguments.get(tool.route)
        handler = tool.handler_for(kind)
        if handler is None:
            if primary is None and tool.route is not None:
                raise ToolError("invalid_arguments", f"{tool.name} cannot make a {kind} "
                                "document here: no library for it is loaded", field=tool.route,
                                valid_options=sorted(k for k in tool.handlers if k))
            raise ToolError("invalid_arguments", f"{tool.name} does not work on a {kind} document",
                            field=tool.documents[0] if not callable(tool.documents) else None,
                            valid_options=[f"{e.doc_id} ({e.kind})" for e in
                                           session.documents.values()
                                           if tool.handler_for(e.kind) is not None])
        return handler

    # -- refs and checks ---------------------------------------------------------------------

    def _with_refs(self, tool: Tool, arguments: dict[str, Any],
                   entries: dict[str, DocumentEntry], primary: DocumentEntry | None,
                   context: CallContext, notes: list[str] | None = None) -> dict[str, Any]:
        """``arguments`` with every ``$name`` in a declared target replaced by its address.
        A camelCase or hyphenated ``$recNow`` that folds to a ref (:func:`fold_ref`) is read
        as it, ``notes`` getting a line for that."""
        if not tool.refs or primary is None:
            return arguments
        doc = primary.doc_id
        known = dict(primary.refs)
        known.update(context.refs.get(doc, {}))

        def resolve(value: Any, where: str) -> Any:
            match = _REF_USE.match(value) if isinstance(value, str) else None
            folded = _folded_use(value, known, context.failed_refs) if match is None else None
            if folded is not None:
                if notes is not None:
                    notes.append(f"{where} {value!r} read as {folded!r}")
                value, match = folded, _REF_USE.match(folded)
            if match is None:
                meant = _misspelt_ref(value, known)
                if meant is not None:
                    raise ToolError("not_found", f"{value!r} is no ref; did you mean "
                                    f"${meant}? A ref is lowercase", field=where,
                                    valid_options=[f"${key}" for key in sorted(known)])
                return value
            name, rest = match.group(1), match.group(2) or ""
            if name not in known:
                raise context.missing_ref(name, where) or ToolError(
                    "not_found", f"no ref ${name} in {doc}", field=where,
                    valid_options=[f"${key}" for key in sorted(known)])
            return known[name] + rest

        for path in tool.refs:
            arguments = _walk(arguments, path.split("."), "", resolve)
        return arguments

    @staticmethod
    def _with_datasets(tool: Tool, arguments: dict[str, Any], call: Call) -> None:
        """Check every ``d:<id>`` in a declared dataset argument names a dataset: one this
        call's batch defined, or the session's.  The value is left as it is."""
        if not tool.datasets and not tool.sources:
            return
        used = call.context.datasets_used
        sources = set(tool.sources)
        primary = [call.entry.doc_id] if call.entry is not None else []

        def resolve(value: Any, where: str, hinted: bool) -> Any:
            if isinstance(value, str) and value.startswith(DATASET_PREFIX):
                try:
                    entry = call.dataset(value, field=where)
                except ToolError as error:
                    if not hinted or error.code != "not_found":
                        raise
                    raise _with_hint(error, Toolbox._source_hint(call.session, primary,
                                                                 value)) from None
                used.setdefault(entry.id, entry)
            return value

        for path in (*tool.datasets, *tool.sources):
            hinted = path in sources

            def each(value: Any, where: str, hinted: bool = hinted) -> Any:
                return resolve(value, where, hinted)

            _walk(arguments, path.split("."), "", each)

    @staticmethod
    def _with_facts(tool: Tool, arguments: dict[str, Any], call: Call) -> dict[str, Any]:
        """``arguments`` with every reference in a declared text (``Tool.facts``) replaced
        by the value it names, escaped ones unescaped; ``call.facts`` says what was written
        where.  Refused whole when one names no single value."""
        if not tool.facts:
            return arguments
        known = textfacts.known_prefixes(call.session.formats)
        ids: dict[str, list[str]] = {}

        def expand(text: str, where: str) -> str:
            if "{{" not in text:
                return text
            if "datasets" not in ids:
                # Round 50: braces shaped like a reference that names no source are refused
                # with the nearest one (textfacts.misread), which reads the dataset ids.
                ids["datasets"] = list(dict.fromkeys(
                    [*call.context.datasets, *(entry.id for entry in call.session.dataset_entries())]))
            written, found = textfacts.expand(
                text, where, known, lambda address, at: call.fact(address, field=at),
                datasets=ids["datasets"])
            call.facts.extend(found)
            return written

        for path in tool.facts:
            arguments = textfacts.walk_texts(arguments, path.split("."), "", expand)
        call.arguments = arguments
        return arguments

    @staticmethod
    def _sources(session: Session, tool: Tool, arguments: dict[str, Any], *,
                 refine: Callable[..., ToolError] | None = None,
                 ) -> tuple[dict[str, Any], list[str]]:
        """``arguments`` with every address in a declared source qualified with its document
        (``t:sales`` -> ``d2/t:sales``), and those documents, in order.  Datasets (``d:``)
        are left as they are.  ``refine(error, value, where)``: the refusal a value that
        names no document's rows gets instead (:meth:`_source_refiner`)."""
        found: list[str] = []
        if tool.value_reads is not None and tool.value_reads(arguments):
            # Round 36: a check of numbers reads every open document's values.
            found += [doc for doc, entry in session.documents.items()
                      if getattr(session.formats.get(entry.kind), "value_sources", None)
                      is not None]
        if not tool.sources and not tool.facts:
            return arguments, found
        documents = dict(session.documents)

        def resolve(value: Any, where: str, fact: bool = False) -> Any:
            # Round 45: "" is a strict decoder's placeholder for a source not given (a cell's
            # from beside its text): absent, left for the handler, never an address.
            if not isinstance(value, str) or not value or value.startswith(DATASET_PREFIX):
                return value
            try:
                doc, address = qualify(value, documents, session.formats, field=where)
            except ToolError as error:
                if refine is None:
                    raise
                raise refine(error, value, where, fact=fact) from None
            if doc not in found:
                found.append(doc)
            return f"{doc}/{address}"

        for path in tool.sources:
            arguments = _walk(arguments, path.split("."), "", resolve)
        if tool.facts:
            known = textfacts.known_prefixes(session.formats)
            own = {p for found in textfacts.own_prefixes(session.formats).values()
                   for p in found}

            def references(text: str, where: str) -> str:
                if "{{" not in text:
                    return text
                return textfacts.qualify_text(text, known,
                                              lambda address: resolve(address, where, True), own)

            for path in tool.facts:
                arguments = textfacts.walk_texts(arguments, path.split("."), "", references)
        return arguments, found

    @staticmethod
    def _source_refiner(session: Session, doc_ids: Sequence[str], tool: Tool,
                        arguments: Mapping[str, Any], datasets: Iterable[str] = (),
                        ) -> Callable[..., ToolError]:
        """Round 56 (R120): ``refine(error, value, where, doc=None, fact=False)``, the refusal of a source
        value that names no rows (:func:`.sources.refusal`): the call's own document's table
        said to be one, the sources there are offered, only options that resolve, the
        call's format's hint on a ``not_found`` (:meth:`_source_hint`), and the call with
        the one source there is, when there is one and ``where`` is a top-level argument.
        ``fact``: ``value`` is a reference's address in a text (``{{t:stock/#1/count}}``)."""
        primary = doc_ids[0] if doc_ids else None
        batch = list(datasets)

        def refine(error: ToolError, value: str, where: str | None,
                   doc: str | None = None, fact: bool = False) -> ToolError:
            # The format's hint is for a value the dispatcher finds nothing to resolve in,
            # never for a document's own refusal (``doc``: its resolver's).
            hint = (Toolbox._source_hint(session, doc_ids, value)
                    if error.code == "not_found" and doc is None else None)

            def call(address: str) -> dict[str, Any] | None:
                if not where or where not in arguments:
                    return None
                meant = {key: value for key, value in arguments.items()
                         if value is not None and value != ""}
                return {"tool": tool.name, "arguments": {**meant, where: address}}

            return refusal(error, value, session, field=where, primary=primary,
                           datasets=batch, doc=doc, call=None if fact else call, hint=hint,
                           fact=fact)

        return refine

    @staticmethod
    def _source_hint(session: Session, doc_ids: Sequence[str], value: str) -> str | None:
        """What the format of the call's document (the first of ``doc_ids``) says about a
        source value that names nothing (:attr:`DocumentFormat.source_hint`), or ``None``."""
        entry = session.documents.get(doc_ids[0]) if doc_ids else None
        fmt = session.formats.get(entry.kind) if entry is not None else None
        hook = getattr(fmt, "source_hint", None)
        if hook is None:
            return None
        assert entry is not None  # the hook is its format's
        try:
            with entry.lock:
                said = hook(entry.document, value)
        except Exception:  # noqa: BLE001 -- a hint only adds words; the refusal stands
            logger.exception("source_hint of %s failed", entry.kind)
            return None
        return said.strip() if isinstance(said, str) and said.strip() else None

    def _batch_sources(self, session: Session, arguments: Mapping[str, Any]) -> list[str]:
        """The documents a batch's ops name as sources, as far as can be told before they
        are validated (an op that names none it can is refused when it runs)."""
        found: list[str] = []
        for op in arguments.get("ops") or []:
            tool = self.tools.get(self.tool_named(op.get("tool", ""))) \
                if isinstance(op, Mapping) else None
            args = op.get("arguments") if isinstance(op, Mapping) else None
            if tool is None or not (tool.sources or tool.facts or tool.value_reads) or \
                    not isinstance(args, Mapping):
                continue
            try:
                docs = self._sources(session, tool, dict(args))[1]
            except ToolError:
                continue
            found += [doc for doc in docs if doc not in found]
        return found

    @staticmethod
    def _keep_datasets(session: Session, context: CallContext, result: Result) -> None:
        """Keep the datasets a call (or batch) defined, each with its source's version now;
        warn about the stale ones it defined or named."""
        defined = list(context.datasets.values())
        for entry in defined:
            for source in (entry, *entry.also):
                source.follow_live()
                if source.version is None and source.doc is not None and not source.closed:
                    if context.changed_since(source):
                        # A later op of the batch changed its source: the state it came
                        # from has no version of its own.
                        source.mark_stale()
                    source.version = source.source_version
        session.keep_datasets(defined)
        named = {**context.datasets_used, **context.datasets}
        for entry in named.values():
            if entry.stale:
                result.warnings.append(_stale_warning(entry))

    @staticmethod
    def _count_changes(entries: Mapping[str, DocumentEntry], changes: Mapping[str, Any],
                       before: Mapping[str, int], context: CallContext,
                       drafting: "_Drafting | None") -> None:
        """Round 45 (lane B): a batch's change of each document counted, by its parts
        (:meth:`DocumentEntry.note_change`), unless it ran on a draft."""
        if drafting is not None:
            return
        for doc in sorted(entries, key=doc_order):
            change = changes.get(doc)
            if change is None and doc in context.changed_at:
                entries[doc].keep_change(before.get(doc), None)
                entries[doc].note_change(None, before.get(doc))
            elif change is not None and change.changed:
                entries[doc].keep_change(before.get(doc), change)
                entries[doc].note_change(change.parts, before.get(doc))

    @staticmethod
    def _keep_refs(entries: dict[str, DocumentEntry], context: CallContext,
                   changed: Iterable[str] = ()) -> None:
        """Keep the refs a call defined, and record every changed document's refs under
        its new version (so undo can bring back the refs of the state it returns to)."""
        for doc in set(context.refs) | set(changed):
            if doc in entries:
                entries[doc].keep_refs(context.refs.get(doc, {}), context.caller)

    def _batch_checks(self, session: Session, entries: Mapping[str, DocumentEntry],
                      context: CallContext, combined: Result, after: "After | None",
                      drafting: "_Drafting | None" = None) -> None:
        """A batch's checks, one per document it changed, into ``combined``: once its locks
        are let go, on the versions it made (``after``, round 45), where every document's
        format copies documents; else now.  Round 51: on a draft, also those of the other
        documents its ops named, as under their locks (where they are entries too): now,
        of the version its ops read (:class:`_Reads`)."""
        read: dict[str, dict[str, Any]] = {}
        if drafting is not None and drafting.reads is not None:
            reads = drafting.reads
            for doc in reads.named:
                if doc not in entries and doc in reads.entries:
                    with reads.held([doc]):
                        read[doc] = self._checks(session, reads.entries[doc], context)
        docs = sorted(entries, key=doc_order)
        every = sorted([*docs, *read], key=doc_order)
        found: dict[str, dict[str, Any]] = dict(read)
        if after is None or not all(after.can_defer(session, entries[doc]) for doc in docs):
            found.update({doc: self._checks(session, entries[doc], context) for doc in docs})
            _set_checks(combined, {doc: found[doc] for doc in every})
            return
        for doc in docs:
            def checked(entry: DocumentEntry, doc: str = doc) -> None:
                found[doc] = self._checks(session, entry, context)

            after.defer(session, entries[doc], checked)
        after.then(lambda: _set_checks(combined, {doc: found[doc] for doc in every
                                                  if doc in found}))

    def _checks(self, session: Session, entry: DocumentEntry,
                context: CallContext) -> dict[str, Any]:
        fmt = session.formats.get(entry.kind)
        if fmt is None or fmt.checks is None:
            return {}
        return dict(fmt.checks(entry, list(context.touched.get(entry.doc_id, []))))

    # -- batch -------------------------------------------------------------------------------

    def _prepare(self, op: Any, notes: list[str] | None = None,
                 roles: Sequence[str] | None = None,
                 within: str = "batch") -> tuple[Tool, dict[str, Any]]:
        """One op's tool and its validated arguments (the aliases read added to ``notes``),
        refused when the call is outside ``roles``.  A :class:`ToolError`'s ``field`` is the
        op's own: ``tool``, or ``arguments`` and the path in them.  ``within``: the tool
        running the op, ``batch`` or ``read_batch``, as the refusals name it."""
        name = self.tool_named(op.get("tool", "") if isinstance(op, Mapping) else "", notes)
        if name not in self.tools:
            raise self._no_tool(name, roles, batch=True, within=within)
        tool = self.tools[name]
        if not _runs_in(tool, within):
            if _runs_in(tool, "read_batch"):
                # render (round 35, R20): with the reads, never inside a batch's changes.
                raise ToolError("invalid_arguments", f"{name} cannot run inside a {within}; "
                                "call it in read_batch (with other reads), or on its own",
                                field="tool", valid_options=["read_batch", name])
            raise ToolError("invalid_arguments", f"{name} cannot run inside a {within}; call "
                            "it on its own", field="tool")
        if roles is not None:
            _check_role(tool, None, roles, field="tool")
        try:
            clean = self._arguments(tool, op.get("arguments") or {}, notes, roles=roles)
            if roles is not None:
                _check_role(tool, clean, roles)
        except ToolError as error:
            raise ToolError(error.code, error.message, field="arguments" + (
                f".{error.field}" if error.field else ""),
                valid_options=error.valid_options, details=error.details) from None
        return tool, clean

    def _ops(self, arguments: Mapping[str, Any], notes: list[str],
             roles: Sequence[str] | None = None) -> list[tuple[int, Tool, dict[str, Any]]]:
        """An atomic batch's ops, each validated against its own tool: before anything runs.
        The aliases read go to ``notes``, each naming its op."""
        prepared = []
        for index, op in enumerate(arguments.get("ops") or []):
            where = f"ops[{index}]"
            try:
                read: list[str] = []
                tool, clean = self._prepare(op, read, roles)
                notes += [f"{where}: {note}" for note in read]
            except ToolError as error:
                given = op.get("tool", "") if isinstance(op, Mapping) else ""
                meant = self.tool_named(given)
                said = where if error.field == "tool" else f"{where} ({given})" \
                    if meant == given else f"{where} ({given}, read as {meant})"
                raise ToolError(error.code, f"{said}: {error.message}",
                                field=f"{where}.{error.field}",
                                valid_options=error.valid_options,
                                details=error.details) from None
            prepared.append((index, tool, clean))
        return prepared

    def _batch_documents(self, arguments: Mapping[str, Any]) -> list[str]:
        """Every document a batch's ops name, before they are validated (for the locks)."""
        found: list[str] = []
        for op in arguments.get("ops") or []:
            tool = self.tools.get(self.tool_named(op.get("tool", ""))) \
                if isinstance(op, Mapping) else None
            args = op.get("arguments") if isinstance(op, Mapping) else None
            if tool is None or tool.composite or not isinstance(args, Mapping):
                continue
            for doc in tool.doc_ids(args):
                if doc not in found:
                    found.append(doc)
        return found

    def _run_batch(self, session: Session, arguments: dict[str, Any],
                   entries: dict[str, DocumentEntry], primary: DocumentEntry | None,
                   reading: Mapping[str, DocumentEntry] | None = None, *,
                   roles: Sequence[str] = ROLES,
                   caller: Mapping[str, Any] = NO_CONTEXT,
                   after: "After | None" = None, drafting: "_Drafting | None" = None) -> Result:
        """``entries``: the documents the ops change (one undo step each); ``reading``:
        the documents they only take rows from, locked but left alone.  ``after``: where
        the checks go to run once the locks are let go (round 45).  ``drafting`` (round 45,
        lane B): on a draft, the reads after the last change are left for when the change
        is applied (``drafting.finish``)."""
        ops = arguments.get("ops") or []
        limits = session.limits
        if len(ops) > limits.max_batch_ops:
            raise ToolError("limit", f"{len(ops)} ops; at most {limits.max_batch_ops} per batch",
                            field="ops")
        if not arguments.get("atomic"):
            return self._run_partial(session, ops, entries, reading or {}, primary,
                                     roles=roles, caller=caller, after=after, drafting=drafting)
        notes: list[str] = []
        prepared = self._ops(arguments, notes, roles)
        last = max((index for index, tool, clean in prepared if tool.changes(clean)),
                   default=-1)
        trailing = prepared[last + 1:]
        prepared = prepared[:last + 1]
        context = _context(roles, caller)
        context.batch = True
        if drafting is not None:
            drafting.context = context
        owner = StepOwner(dict(caller), "batch")
        started = time.perf_counter()
        done: list[dict[str, Any]] = []
        combined = Result()
        before = {doc: entry.version for doc, entry in entries.items()}
        with collect_warnings() as caught:
            try:
                with ExitStack() as stack:
                    changes = {doc: stack.enter_context(entries[doc].watch())
                               for doc in sorted(entries, key=doc_order)}
                    for doc in sorted(entries, key=doc_order):
                        stack.enter_context(entries[doc].batch(owner))
                    for index, tool, clean in prepared:
                        context.op = index
                        elapsed = time.perf_counter() - started
                        if elapsed > limits.batch_timeout:
                            note, not_run = _not_run(limits.batch_timeout, index, len(prepared),
                                                     elapsed)
                            raise _OpFailed(index, tool.name, ToolError(
                                "timeout", f"the batch passed its {limits.batch_timeout:g} s "
                                f"deadline: {note}", details={"not_run": not_run}))
                        self._op_states(index, tool, clean, entries, context)
                        with _reads_held(session, self, drafting, tool, clean):
                            done.append(self._run_op(session, index, tool, clean, entries,
                                                     context, combined, reading or {}))
                        if tool.changes(clean):
                            for doc in tool.doc_ids(clean):
                                context.changed_at[doc] = index
                    self._run_deferred(context, combined)
                    self._op_states(len(prepared), None, {}, entries, context)
            except _OpFailed as failed:
                error = failed.error
                raise ToolError(
                    error.code, f"ops[{failed.index}] ({failed.tool}) failed, so nothing in the "
                    f"batch was applied: {error.message}",
                    valid_options=error.valid_options,
                    field=f"ops[{failed.index}]" + (f".arguments.{error.field}"
                                                    if error.field else ""),
                    details={"op": failed.index, "tool": failed.tool, "error": error.to_json(),
                             "completed_before": failed.index,
                             **({"not_run": error.details["not_run"]}
                                if error.details and "not_run" in error.details else {})}) from None
            for doc in sorted(entries, key=doc_order):
                if doc in context.changed_at:
                    # Round 51: a document the batch only read is not counted as changed
                    # (a format without a history), as in a batch that applies op by op.
                    entries[doc].bump()
            self._count_changes(entries, changes, before, context, drafting)
            self._keep_refs(entries, context, list(entries))
            changing = any(tool.changes(clean) for _, tool, clean in prepared)
            if drafting is not None and context.deferred_any:
                drafting.local = False
            if drafting is None:
                self._keep_datasets(session, context, combined)
            else:
                drafting.datasets = combined      # kept when its change is applied
        combined.warnings[:0] = notes
        combined.warnings.extend(caught)
        if changing:
            self._batch_checks(session, entries, context, combined, after, drafting)
        if drafting is None:
            # (On a draft: read from the document once the change is applied.)
            self._batch_touched(session, entries, changes, context, combined)
        if primary is not None:
            combined.version = primary.version

        def rest(readable: Mapping[str, DocumentEntry],
                 rename: Callable[[Any], Any] | None = None) -> Result:
            """The reads after the last change, on ``readable``; the batch's answer.
            ``rename``: what the ops said on a draft, with the ids renamed as applied."""
            if rename is not None:
                done[:] = rename(done)
            errors: dict[int, ToolError] = {}
            stopped = self._run_trailing(session, [(index, None, (tool, clean))
                                                   for index, tool, clean in trailing],
                                         readable, started, combined, done,
                                         errors, context.failed_refs, roles=roles, caller=caller)
            answered = [outcome for outcome in done if "error" not in outcome]
            said = [(f"Ran {len(answered)} op(s) as one step: " if prepared else "Read: ")
                    + _summaries(answered)]
            failures = {index: error for index, error in errors.items()
                        if stopped is None or index < stopped[0]}
            if failures:
                # The batch applied: a read after its last change cannot take it back.
                said.append("Not answered, the batch applied -- resend only these on their own: "
                            + _failures(done, failures))
            combined.data = ({"applied": len(prepared), "failed": list(errors), "ops": done}
                             if errors else {"ops": done})
            if stopped is not None:
                late = _not_run(limits.batch_timeout, stopped[0], len(ops), stopped[1])
                said.append(f"Not run: {late[0]}")
                combined.data["not_run"] = late[1]
            combined.summary = ". ".join(said)
            return combined

        if drafting is not None:
            drafting.finish = rest
            return combined
        return rest({**(reading or {}), **entries})

    def _trailing(self, ops: Sequence[Any], roles: Sequence[str] | None) -> int:
        """Where a batch's reads after its last change begin: the index after the last op
        that changes a document (``len(ops)`` when the last one does, 0 when none does).
        An op that cannot be validated changes nothing (it fails wherever it runs)."""
        last = -1
        for index, op in enumerate(ops):
            try:
                tool, clean = self._prepare(op, None, roles)
            except ToolError:
                continue
            if tool.changes(clean):
                last = index
        return last + 1

    def _run_trailing(self, session: Session,
                      ops: Sequence[tuple[int, Any, tuple[Tool, dict[str, Any]] | None]],
                      entries: Mapping[str, DocumentEntry], started: float,
                      combined: Result, outcomes: list[dict[str, Any]],
                      errors: dict[int, ToolError], failed_refs: Mapping[str, int], *,
                      roles: Sequence[str] = ROLES, caller: Mapping[str, Any] = NO_CONTEXT,
                      stopped: tuple[int, float] | None = None) -> tuple[int, float] | None:
        """A batch's reads after its last change (round 33, R16c), run once its undo step
        is closed and its checks are done, each as the same call alone would run
        (:meth:`_run`): a slow one -- a ``check`` that lays the document out -- lets the
        locks go (:meth:`Call.unlocked`), so other calls do not wait for it, and answers of
        the batch's version (another call's change meanwhile is warned about, and an op that
        finds the document moved on says its own ``version``).  ``ops``: ``(index, op,
        prepared)``, ``prepared`` the op's tool and arguments if validated already, else
        ``op`` is.  Each outcome goes to ``outcomes``, a failure's error to ``errors``
        too: the batch applied, so a failing read takes nothing back.  Returns the first op
        the deadline stopped, and when (``stopped``: an earlier op it stopped), or ``None``."""
        limits = session.limits
        versions = {doc: entry.version for doc, entry in entries.items()}
        for index, op, prepared in ops:
            name = (prepared[0].name if prepared is not None
                    else op.get("tool", "") if isinstance(op, Mapping) else "")
            try:
                elapsed = time.perf_counter() - started
                if stopped is not None or elapsed > limits.batch_timeout:
                    stopped = stopped or (index, elapsed)
                    raise ToolError("timeout", "not run: the batch passed its "
                                    f"{limits.batch_timeout:g} s deadline")
                if prepared is None:
                    read: list[str] = []
                    prepared = self._prepare(op, read, roles)
                    combined.warnings += [f"ops[{index}]: {note}" for note in read]
                tool, clean = prepared
                result, primary = self._read_one(session, tool, clean, entries, roles, caller,
                                                 "batch", failed_refs)
            except Exception as exc:  # noqa: BLE001 -- the op's error; the batch applied
                error = exc if isinstance(exc, ToolError) else self.map_exception(exc)
                errors[index] = error
                outcomes.append({"op": index, "tool": name, "error": _brief(error)})
                continue
            if not result.ok:
                error = result.error or ToolError("internal", "failed")
                errors[index] = error
                combined.warnings += [f"ops[{index}]: {w}" for w in result.warnings]
                outcomes.append({"op": index, "tool": tool.name, "error": _brief(error)})
                continue
            combined.datasets += [a for a in result.datasets if a not in combined.datasets]
            combined.warnings += [f"ops[{index}]: {w}" for w in result.warnings]
            entry: dict[str, Any] = {"op": index, "tool": tool.name, "summary": result.summary}
            if result.data not in (None, {}, []):
                entry["data"] = result.data
            if (primary is not None and result.version is not None
                    and result.version != versions.get(primary.doc_id)):
                entry["version"] = result.version       # another call's change came first
            outcomes.append(entry)
        return stopped

    def _run_partial(self, session: Session, ops: Sequence[Any],
                     entries: dict[str, DocumentEntry],
                     reading: Mapping[str, DocumentEntry],
                     primary: DocumentEntry | None = None, *, roles: Sequence[str] = ROLES,
                     caller: Mapping[str, Any] = NO_CONTEXT,
                     after: "After | None" = None,
                     drafting: "_Drafting | None" = None) -> Result:
        """A batch whose ops apply one by one: a failing op is rolled back alone and
        reported, and the ops after it still run.  What applied is one undo step; the reads
        after the last change run after it (:meth:`_run_trailing`) -- on a draft (round 45,
        lane B: ``drafting``), once the change is applied (``drafting.finish``)."""
        limits = session.limits
        split = self._trailing(ops, roles)
        context = _context(roles, caller)
        context.batch = True
        if drafting is not None:
            drafting.context = context
        owner = StepOwner(dict(caller), "batch")
        started = time.perf_counter()
        outcomes: list[dict[str, Any]] = []
        errors: dict[int, ToolError] = {}
        changing = False
        combined = Result()
        #: The first op the deadline stopped, and the time it was stopped at.
        stopped: tuple[int, float] | None = None
        before = {doc: entry.version for doc, entry in entries.items()}
        with collect_warnings() as caught:
            with ExitStack() as stack:
                changes = {doc: stack.enter_context(entries[doc].watch())
                           for doc in sorted(entries, key=doc_order)}
                for doc in sorted(entries, key=doc_order):
                    stack.enter_context(entries[doc].batch(owner))
                for index, op in enumerate(ops[:split]):
                    context.op = index
                    name = op.get("tool", "") if isinstance(op, Mapping) else ""
                    try:
                        elapsed = time.perf_counter() - started
                        if stopped is not None or elapsed > limits.batch_timeout:
                            stopped = stopped or (index, elapsed)
                            raise ToolError("timeout", "not run: the batch passed its "
                                            f"{limits.batch_timeout:g} s deadline")
                        read: list[str] = []
                        tool, clean = self._prepare(op, read, roles)
                        combined.warnings += [f"ops[{index}]: {note}" for note in read]
                        with _reads_held(session, self, drafting, tool, clean):
                            outcome = self._run_alone(session, index, tool, clean, entries,
                                                      context, combined, reading)
                    except (ToolError, _OpFailed) as failure:
                        error = _op_error(failure)
                        errors[index] = error
                        context.failed_refs.update(
                            dict.fromkeys(_ref_names(op.get("arguments")
                                                     if isinstance(op, Mapping) else None),
                                          index))
                        outcomes.append({"op": index, "tool": name, "error": _brief(error)})
                        continue
                    outcomes.append(outcome)
                    if tool.changes(clean):
                        changing = True
                        for doc in tool.doc_ids(clean):
                            context.changed_at[doc] = index
                self._run_deferred(context, combined)
                self._op_states(len(ops), None, {}, entries, context)
            for doc in sorted(entries, key=doc_order):
                if doc in context.changed_at:
                    entries[doc].bump()
            self._count_changes(entries, changes, before, context, drafting)
            self._keep_refs(entries, context, list(entries))
            if changing:
                self._batch_checks(session, entries, context, combined, after, drafting)
            if drafting is not None and context.deferred_any:
                drafting.local = False
            if drafting is None:
                self._keep_datasets(session, context, combined)
            else:
                drafting.datasets = combined      # kept when its change is applied
            if primary is not None:
                combined.version = primary.version
        # What each op changed is settled: the reads after the last change change nothing.
        # (On a draft: read from the document once the change is applied.)
        if drafting is None:
            self._batch_touched(session, entries, changes, context, combined)
        caught_first = list(caught)

        def rest(readable: Mapping[str, DocumentEntry],
                 rename: Callable[[Any], Any] | None = None) -> Result:
            """The reads after the last change, on ``readable``; the batch's answer.
            ``rename``: what the ops said on a draft, with the ids renamed as applied."""
            nonlocal stopped, caught_first
            if rename is not None:
                outcomes[:] = rename(outcomes)
                caught_first = rename(caught_first)
                # Round 47: the failed ops' errors too, which the summary quotes (an op that
                # failed on ids an earlier op of the batch made, named by a ref).
                for error in errors.values():
                    error.message = rename(error.message)
                    error.valid_options = rename(error.valid_options)
                    error.details = rename(error.details)
            with collect_warnings() as caught_late:
                stopped = self._run_trailing(
                    session, [(index, ops[index], None) for index in range(split, len(ops))],
                    readable, started, combined, outcomes, errors,
                    context.failed_refs, roles=roles, caller=caller, stopped=stopped)
                not_run = (_not_run(limits.batch_timeout, stopped[0], len(ops), stopped[1])
                           if stopped is not None else None)
                if errors and len(errors) == len(outcomes):
                    raise _none_applied(outcomes, errors, not_run)
            combined.warnings.extend(caught_first + caught_late)
            done = [outcome for outcome in outcomes if "error" not in outcome]
            if errors:
                failures = {index: error for index, error in errors.items()
                            if stopped is None or index < stopped[0]}
                said = [f"Ran {len(done)} of {len(outcomes)} ops as one step: " + _summaries(done)]
                if failures:
                    said.append("Not applied -- fix and resend only these: "
                                + _failures(outcomes, failures))
                combined.data = {"applied": len(done), "failed": list(errors), "ops": outcomes}
                if not_run is not None:
                    said.append(f"Not run: {not_run[0]}")
                    combined.data["not_run"] = not_run[1]
                combined.summary = ". ".join(said)
            else:       # every op applied: the result an all-or-none batch gives
                combined.summary = (f"Ran {len(done)} op(s) as one step: " if split
                                    else "Read: ") + _summaries(done)
                combined.data = {"ops": outcomes}
            return combined

        if drafting is not None:
            drafting.finish = rest
            return combined
        return rest({**reading, **entries})

    # -- read_batch ---------------------------------------------------------------------------

    def _run_reads(self, session: Session, arguments: Mapping[str, Any],
                   entries: Mapping[str, DocumentEntry], *, roles: Sequence[str] = ROLES,
                   caller: Mapping[str, Any] = NO_CONTEXT) -> Result:
        """``read_batch`` (round 32, R15): reading calls run one after another, each as the
        same call alone would run (:meth:`_run`: no undo step, no version, no checks), under
        the locks of every document they name (``entries``: the dispatcher took them).

        Each op is answered within ``max_result_chars``, as alone; the answers together
        within ``max_read_batch_chars`` (:attr:`Result.budget`, so the reply is not cut to
        one call's limit).  A ``render`` among them (round 35, R20) runs as alone too --
        its locks let go while it draws -- and its images come in the reply, after the
        text, in op order, each captioned with its op (:attr:`Image.caption`); each counts
        toward the budget as its tokens would (:func:`image_chars`).  One that does not fit
        what is left, and every op the batch's deadline stopped, is listed in
        ``not_returned`` and in ``resend``, the call that asks for them; images not sent
        are given back to the session's budget.  An op whose own answer has a next page
        gets ``next``, the call for it.  A failing op -- a changing call among them:
        refused, pointing at ``batch`` -- is answered with its error; the others still
        run."""
        ops = list(arguments.get("ops") or [])
        limits = session.limits
        if len(ops) > limits.max_batch_ops:
            raise ToolError("limit", f"{len(ops)} ops; at most {limits.max_batch_ops} per "
                            "read_batch: send the rest in another", field="ops")
        own = limits.max_result_chars
        cap = max(limits.max_read_batch_chars, own)
        started = time.perf_counter()
        answers: list[tuple[dict[str, Any], int, Result]] = []
        left: list[tuple[int, str]] = []      # (op, why)
        pictured: dict[int, int] = {}         # op -> what its images count, in characters
        used = _READ_MARGIN
        for index, op in enumerate(ops):
            if time.perf_counter() - started > limits.batch_timeout:
                left.append((index, "deadline"))
                continue
            result, paged = self._read_op(session, op, entries, roles, caller)
            answer = _read_answer(index, result, op, own, paged)
            if result.images:
                pictured[index] = image_chars(result.images)
            size = _size(answer) + pictured.get(index, 0)
            if used + size > cap:
                left.append((index, "budget"))
                session.release_images(len(result.images))     # never sent
                continue
            used += size
            answers.append((answer, index, result))
        combined = _read_result(ops, answers, left, cap, limits.batch_timeout, pictured)
        while answers and (len(combined.to_text()) + image_chars(combined.images)) > cap:
            # What was left out, listed with its call, did not fit beside the answers: the
            # last answer goes to it too.
            _, index, result = answers.pop()
            session.release_images(len(result.images))
            left = sorted([*left, (index, "budget")])
            combined = _read_result(ops, answers, left, cap, limits.batch_timeout, pictured)
        return combined

    def _read_op(self, session: Session, op: Any, entries: Mapping[str, DocumentEntry],
                 roles: Sequence[str], caller: Mapping[str, Any]) -> tuple[Result, bool]:
        """One op of a ``read_batch``: its result, or its error as a failed one; and
        whether its tool takes a ``cursor`` (the next page's call is its own)."""
        notes: list[str] = []
        primary: DocumentEntry | None = None
        tool: Tool | None = None
        try:
            tool, clean = self._prepare(op, notes, roles, "read_batch")
            if tool.changes(clean):
                raise ToolError(
                    "refused", f"{tool.name} changes the document, and read_batch only "
                    "reads: send this op in batch (with the other changes), or call it on its "
                    "own", field="tool", valid_options=["batch", tool.name])
            result, primary = self._read_one(session, tool, clean, entries, roles, caller)
        except ToolError as error:
            result = Result.failure(error)
        except Exception as exc:  # noqa: BLE001 -- the op's error, the others still run
            result = Result.failure(self.map_exception(exc))
        result.warnings[:0] = notes
        if primary is not None:
            result.doc = primary.doc_id
            if result.version is None:
                result.version = primary.version
        given = op.get("tool") if isinstance(op, Mapping) else None
        result.tool = tool.name if tool is not None else given if isinstance(given, str) else ""
        paged = tool is not None and "cursor" in tool.schema.get("properties", {})
        return result, paged

    def _read_one(self, session: Session, tool: Tool, clean: dict[str, Any],
                  entries: Mapping[str, DocumentEntry], roles: Sequence[str],
                  caller: Mapping[str, Any], within: str = "read_batch",
                  failed_refs: Mapping[str, int] | None = None
                  ) -> tuple[Result, DocumentEntry | None]:
        """A reading op run as the same call alone (:meth:`_run`), under the locks
        ``entries`` the dispatcher took for the whole ``read_batch`` (``within``: or for a
        batch); its result and its primary document."""
        doc_ids = tool.doc_ids(clean)
        for doc in doc_ids:
            if doc not in entries:
                session.entry(doc)      # not_found, naming the open documents
                raise ToolError("refused", f"{doc} was opened after the {within} began: read "
                                "it in another call", field="arguments.doc")
        clean, found = self._sources(
            session, tool, clean, refine=self._source_refiner(session, doc_ids, tool, clean))
        held = {doc: entries[doc] for doc in [*doc_ids, *found] if doc in entries}
        primary = held[doc_ids[0]] if doc_ids else None
        result = self._run(session, tool, clean, dict(held), primary,
                           [doc for doc in found if doc not in doc_ids],
                           roles=roles, caller=caller, failed_refs=failed_refs,
                           holding=entries)
        return result, primary

    def _run_deferred(self, context: CallContext, combined: Result) -> None:
        """After a batch's last op: the work its ops deferred (:meth:`Call.defer`), in op
        order, each op's warnings then put back where they were among the batch's.  Work
        that fails is said in a warning: its op applied."""
        placed: list[tuple[int, int, list[str]]] = []
        for index, name, work, result, at, count in context.deferred:
            self._do_deferred(name, work, result)
            placed.append((at, count, [f"ops[{index}]: {w}" for w in result.warnings]))
        for at, count, said in reversed(placed):
            combined.warnings[at:at + count] = said
        context.deferred = []

    def _do_deferred(self, name: str, work: Sequence[Callable[[Result], None]],
                     result: Result) -> None:
        for item in work:
            try:
                item(result)
            except Exception as exc:  # noqa: BLE001 -- the op applied; said, not raised
                error = exc if isinstance(exc, ToolError) else self.map_exception(exc)
                result.warnings.append(f"{name}: what it says once it is done could not be "
                                       f"found: {error.message}")

    def _run_alone(self, session: Session, index: int, tool: Tool, arguments: dict[str, Any],
                   entries: dict[str, DocumentEntry], context: CallContext,
                   combined: Result, reading: Mapping[str, DocumentEntry]) -> dict[str, Any]:
        """:meth:`_run_op` with what the op changes rolled back if it fails: its documents
        to where they were (the format told, ``DocumentFormat.restored``), and the refs,
        datasets and touched scopes it defined."""
        mark = context.mark()
        rolled: list[DocumentEntry] = []
        states = self._op_states(index, tool, arguments, entries, context)
        try:
            with ExitStack() as stack:
                for doc in sorted({d for d in tool.doc_ids(arguments) if d in entries},
                                  key=doc_order):
                    if stack.enter_context(entries[doc].savepoint(states.get(doc))):
                        rolled.append(entries[doc])
                return self._run_op(session, index, tool, arguments, entries, context,
                                    combined, reading)
        except _OpFailed:
            context.reset(mark)
            for entry in rolled:
                fmt = session.formats.get(entry.kind)
                if fmt is not None and fmt.restored is not None:
                    fmt.restored(entry.document)
            raise

    def _run_op(self, session: Session, index: int, tool: Tool, arguments: dict[str, Any],
                entries: dict[str, DocumentEntry], context: CallContext,
                combined: Result, reading: Mapping[str, DocumentEntry]) -> dict[str, Any]:
        doc_ids = tool.doc_ids(arguments)
        primary = entries.get(doc_ids[0]) if doc_ids else None
        if primary is None and doc_ids and not tool.changes(arguments):
            # Round 51: a read of a document the batch only reads (on a draft, R105).
            primary = reading.get(doc_ids[0])
        try:
            for doc in doc_ids:
                if doc not in entries and doc not in reading:
                    session.entry(doc)      # not_found, naming the open documents
                    raise ToolError("refused", f"{doc} was opened after the batch began: "
                                    "call it on its own", field="doc")
            handler = self._handler(session, tool, arguments, primary)
            arguments = dict(arguments)
            read = tool.ignored_for(primary.kind if primary else None, arguments)
            arguments = self._with_refs(tool, arguments, entries, primary, context, read)
            arguments, found = self._sources(
                session, tool, arguments,
                refine=self._source_refiner(session, doc_ids, tool, arguments,
                                            context.datasets))
            held = {**reading, **entries}
            call = Call(self, session, tool, arguments,
                        {doc: held[doc] for doc in [*doc_ids, *found] if doc in held},
                        primary, context)
            if tool.changes(arguments):
                call._own_refs_first(arguments)
            call.result.warnings += read
            self._with_datasets(tool, arguments, call)
            arguments = self._with_facts(tool, arguments, call)
            result = _as_result(handler(call, **arguments), call.result)
            _report_facts(call, result)
        except _OpFailed:
            raise
        except ToolError as error:
            raise _OpFailed(index, tool.name, error) from None
        except Exception as exc:  # noqa: BLE001 -- reported as the op's error
            raise _OpFailed(index, tool.name, self.map_exception(exc)) from None
        if not result.ok:
            raise _OpFailed(index, tool.name, result.error or ToolError("internal", "failed"))
        combined.changed += [a for a in result.changed if a not in combined.changed]
        combined.created += result.created
        combined.removed += result.removed
        combined.renamed.update(result.renamed)
        combined.refs.update(result.refs)
        combined.datasets += [a for a in result.datasets if a not in combined.datasets]
        if call.deferred:
            context.deferred_any = True
        if call.deferred and not self.batch_defers:
            self._do_deferred(tool.name, call.deferred, result)
        elif call.deferred:
            context.deferred.append((index, tool.name, call.deferred, result,
                                     len(combined.warnings), len(result.warnings)))
        combined.warnings += [f"ops[{index}]: {w}" for w in result.warnings]
        entry: dict[str, Any] = {"op": index, "tool": tool.name, "summary": result.summary}
        if result.created:
            entry["created"] = result.created
        if result.data not in (None, {}, []):
            entry["data"] = result.data
        return entry

    # -- what calls changed -----------------------------------------------------------------

    def _touched(self, session: Session, entry: DocumentEntry, change: Any
                 ) -> list[dict[str, Any]]:
        """``Result.touched`` for what ``change`` (a :class:`~ooxml_edit.history.Change`)
        did to ``entry``'s document, in its format's addresses (``DocumentFormat.touched``;
        one entry per part without).  A format whose mapping fails falls back to the
        parts, logged: what changed is never left out."""
        if change is None or not change.parts:
            return []
        fmt = session.formats.get(entry.kind)
        found: list[dict[str, Any]]
        if fmt is None or fmt.touched is None:
            found = part_entries(change)
        else:
            try:
                found = list(fmt.touched(entry.document, change))
            except Exception:  # noqa: BLE001 -- the parts still say what changed
                logger.exception("the %s format's touched failed; reporting parts", entry.kind)
                found = part_entries(change)
        return [{"doc": entry.doc_id, **item} for item in found]

    def _op_states(self, index: int, tool: Tool | None, arguments: Mapping[str, Any],
                   entries: Mapping[str, DocumentEntry], context: CallContext
                   ) -> dict[str, Any]:
        """Before a batch's op ``index`` that changes documents: their states now
        (:meth:`DocumentEntry.mark`), returned for the op's savepoints; and for each, what
        the op that last changed it before did -- from its state then to this one.  With
        ``tool`` ``None`` (after the last op), every document's last op is settled."""
        if tool is None:
            docs = [doc for doc in context.states if doc in entries]
        elif tool.changes(arguments):
            docs = [doc for doc in tool.doc_ids(arguments) if doc in entries]
        else:
            return {}
        states: dict[str, Any] = {}
        for doc in docs:
            entry = entries[doc]
            state = entry.mark()
            if state is None:
                continue
            states[doc] = state
            earlier = context.states.pop(doc, None)
            if earlier is not None:
                context.op_changes.append((earlier[0], doc,
                                           entry.history.change(earlier[1], state)))
            if tool is not None:
                context.states[doc] = (index, state)
        return states

    def _batch_touched(self, session: Session, entries: Mapping[str, DocumentEntry],
                       changes: Mapping[str, Any], context: CallContext,
                       combined: Result) -> None:
        """A batch's ``touched`` (each document's whole change) and ``touched_by_op``."""
        for doc in sorted(entries, key=doc_order):
            combined.touched += self._touched(session, entries[doc], changes.get(doc))
        for op, doc, change in sorted(context.op_changes, key=lambda item: item[0]):
            found = self._touched(session, entries[doc], change)
            if found:
                combined.touched_by_op.setdefault(op, []).extend(found)

    def map_exception(self, exc: BaseException) -> ToolError:
        """A library exception as an error code, with its valid options when known: the
        closest class in its MRO that the formats' maps (or :data:`BASE_ERRORS`, or the
        application's ``error_map``) name; else a :class:`LimitError` is ``limit``, a
        ``TimeoutError`` ``timeout``, a ``KeyError`` or ``IndexError`` ``not_found``, any
        other ``ValueError`` ``unit``, and anything else ``internal``, logged."""
        for cls in type(exc).__mro__:
            if cls in self._errors:
                mapping = self._errors[cls]
                code, options = (mapping, None) if isinstance(mapping, str) else mapping
                valid = list(options(exc)) if options else []
                return ToolError(code, _message(exc), valid_options=valid)
        if isinstance(exc, LimitError):
            return ToolError("limit", _message(exc))
        if isinstance(exc, TimeoutError):
            return ToolError("timeout", _message(exc))
        if isinstance(exc, (KeyError, IndexError)):
            return ToolError("not_found", _message(exc))
        if isinstance(exc, ValueError):
            return ToolError("unit", _message(exc))
        logger.exception("unexpected %s in a tool call", type(exc).__name__, exc_info=exc)
        return ToolError("internal", f"{type(exc).__name__}: {_message(exc)}")

    def _record(self, session: Session, name: str, doc_ids: list[str], arguments: Any,
                result: Result, entries: dict[str, DocumentEntry],
                roles: Sequence[str] = ROLES, caller: Mapping[str, Any] = NO_CONTEXT,
                comments: Sequence[Mapping[str, Any]] = ()) -> None:
        loggable = arguments if isinstance(arguments, Mapping) else {"raw": str(arguments)}
        record = CallRecord(
            tool=name, docs=list(doc_ids), digest=digest(loggable), shape=shape(loggable),
            ok=result.ok, code=result.error.code if result.error else None,
            summary=result.summary, duration=result.duration, version=result.version,
            arguments=dict(loggable) if self.log_arguments else None,
            roles=list(roles) if tuple(roles) != tuple(session.roles) else None,
            context=dict(caller) if caller else None,
            comments=[dict(comment) for comment in comments] or None)
        session.log.append(record)
        for entry in entries.values():
            entry.log.append(record)
        if logger.isEnabledFor(logging.INFO):
            log_call(record)


#: The codes of the libraries' typed refusals (:mod:`ooxml_edit.errors`), in every format:
#: a value the library refuses is ``invalid_arguments``, one out of its range or in the
#: wrong unit ``unit``.  A format's ``DocumentFormat.errors`` may map a subclass otherwise.
BASE_ERRORS: dict[type[BaseException], str] = {
    ArgumentError: "invalid_arguments",
    UnitError: "unit",
}

#: The keys of a batch op a model writes about the op, never run (round 9, N4).
OP_COMMENTS = ("comment", "note")

#: The longest comment the call log keeps, in characters.
MAX_COMMENT = 500


def _op_comments(arguments: Any, comments: list[dict[str, Any]]) -> Any:
    """A batch's arguments with each op's ``comment`` and ``note`` taken out, unsent: a
    model's words about its op (``"fix title"``), which change nothing.  Each op's go to
    ``comments`` (``{"op": 3, "comment": "fix title"}``), for the call log -- a value that
    is not a string as its JSON, at most :data:`MAX_COMMENT` characters each."""
    ops = arguments.get("ops") if isinstance(arguments, Mapping) else None
    if not isinstance(ops, list):
        return arguments
    kept = []
    for index, op in enumerate(ops):
        if isinstance(op, Mapping) and any(key in op for key in OP_COMMENTS):
            said = {key: _comment_text(op[key]) for key in OP_COMMENTS
                    if op.get(key) is not None}
            if said:
                comments.append({"op": index, **said})
            op = {key: value for key, value in op.items() if key not in OP_COMMENTS}
        kept.append(op)
    return {**arguments, "ops": kept}


def _comment_text(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False,
                                                           default=str)
    return text if len(text) <= MAX_COMMENT else text[:MAX_COMMENT - 1] + "\u2026"


def _aliased(tool: Tool, arguments: Any, notes: list[str]) -> Any:
    """``arguments`` with every name in ``tool.aliases`` renamed to the tool's own, a note
    for each (``from_columns read as columns``; a nested one with its place,
    ``items[2].top read as space_before``).  An alias given with the name it stands for is
    refused: the call says one thing twice."""
    if not tool.aliases or not isinstance(arguments, Mapping):
        return arguments
    renamed: Any = dict(arguments)
    for alias, name in tool.aliases.items():
        *path, key = alias.split(".")
        if path:
            def rename(node: Any, where: str, key: str = key, name: Any = name) -> Any:
                return _renamed(node, key, name, where, notes)

            renamed = _walk(renamed, path, "", rename)
        else:
            renamed = _renamed(renamed, key, name, "", notes)
    return renamed


def _defaulted(tool: Tool, arguments: Any, notes: list[str]) -> Any:
    """``arguments`` with each required argument of ``tool.defaults`` it leaves out (or
    gives as null) set to its default, a note for each (``expect not given: read as
    'one'``; round 50, R102)."""
    if not tool.defaults or not isinstance(arguments, Mapping):
        return arguments
    missing = {name: value for name, value in tool.defaults.items() if arguments.get(name) is None}
    if not missing:
        return arguments
    for name, value in missing.items():
        notes.append(f"{name} not given: read as {value!r}")
    return {**arguments, **missing}


def _renamed(node: Any, alias: str, name: Any, where: str, notes: list[str]) -> Any:
    """One object of the arguments (at ``where``) with ``alias`` read as ``name``."""
    if not isinstance(node, Mapping) or node.get(alias) is None:
        return node
    meant = name(node) if callable(name) else name
    if meant is None or meant == alias:
        return node
    field = f"{where}.{alias}" if where else alias
    if node.get(meant) is not None:
        raise ToolError("invalid_arguments", f"{alias} and {meant} say the same here; "
                        f"give {meant} only", field=field, valid_options=[meant])
    node = dict(node)
    node[meant] = node.pop(alias)
    notes.append(f"{field} read as {meant}")
    return node


def _caller(session: Session | SessionView, roles: Any,
            context: Mapping[str, Any] | None) -> tuple[Session, tuple[str, ...],
                                                         Mapping[str, Any]]:
    """The session a dispatch runs on (a view's own), and the roles and context of its
    calls: the session's or view's, narrowed by ``roles`` and added to by ``context``.  A
    view's roles are checked against its session's again (narrowed since it was made)."""
    if isinstance(session, SessionView):
        base = session.session
        found, caller = narrowed(base.roles, NO_CONTEXT, session.roles, session.context)
    else:
        base, found, caller = session, tuple(session.roles), NO_CONTEXT
    found, caller = narrowed(found, caller, roles, context)
    return base, found, caller


def _context(roles: Sequence[str], caller: Mapping[str, Any]) -> CallContext:
    """A dispatch's :class:`CallContext`, with the roles and context of its calls."""
    context = CallContext()
    context.roles = tuple(roles)
    context.caller = caller
    return context


def _reads_some(tool: Tool) -> bool:
    """Whether some call of ``tool`` only reads, and may run in a ``read_batch``."""
    return _runs_in(tool, "read_batch") and (not tool.mutates or tool.reads is not None)


def _runs_in(tool: Tool, within: str) -> bool:
    """Whether ``tool`` may run as an op of ``within`` (``batch`` or ``read_batch``): a
    batchable tool, or in a ``read_batch`` one that is :attr:`~Tool.read_batchable` too
    (``render``, round 35); never a composite one."""
    if tool.composite:
        return False
    return tool.batchable or (within == "read_batch" and tool.read_batchable)


def _needs(tool: Tool, role: str) -> bool:
    """Whether some call of ``tool`` needs ``role`` itself (not every role's)."""
    return tool.base_role == role or role in tool.action_roles.values()


def _check_role(tool: Tool, arguments: Mapping[str, Any] | None, roles: Sequence[str], *,
                field: str | None = None) -> None:
    """Refuse a call outside ``roles``: with ``arguments`` ``None``, a tool in none of the
    roles' toolsets; else the call's own role (:meth:`Tool.role_of`)."""
    if arguments is None:
        if tool.roles & set(roles):
            return
        needed = sorted(tool.roles, key=lambda role: (role != EDIT, role))[0]
        action = None
    else:
        needed = tool.role_of(arguments)
        if needed == ANY_ROLE or needed in roles:
            return
        action = arguments.get("action") if isinstance(arguments.get("action"), str) else None
    if arguments is not None and tool.call_role is not None and tool.call_role(arguments):
        what = f"this {tool.name} call"     # its action's role is loaded, not this one's
    elif action and tool.actions:
        what = f"{tool.name} {action}"
    else:
        what = tool.name
    message = (f"{what} needs the {needed} role; this session has "
               f"{', '.join(roles)}. ")
    if needed == EDIT:
        message += ("Do not make the change: report it with the call that would make it "
                    "(tool and arguments).")
    elif needed == RESOLVE:
        message += "Leave the thread as it is and say it should be resolved."
    elif needed == ACCEPT:
        message += ("Leave the tracked changes as they are and say which should be accepted "
                    "or rejected.")
    else:
        message += "Reading needs the review role."
    allowed = tool.actions_for(roles) if action else []
    raise ToolError("refused", message, field=field or ("action" if action else None),
                    valid_options=allowed, details={"role": needed, "roles": list(roles)})


def ref_rule(name: str) -> str:
    """Why ``name`` is no ref name, in words, with one that would do."""
    suggestion = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()
    suggestion = re.sub(r"[^a-z0-9_]+", "_", suggestion).strip("_")
    if not suggestion or not suggestion[0].isalpha():
        suggestion = "r_" + suggestion if suggestion else "r1"
    suggestion = suggestion[:32].rstrip("_")
    return (f"ref {name!r} is not a ref name: a ref starts with a lowercase letter, then "
            f"up to 31 lowercase letters, digits or _ (no capitals, spaces or -); "
            f"e.g. {suggestion!r}")


def _walk(node: Any, parts: list[str], where: str, resolve: Callable[[Any, str], Any]) -> Any:
    """``node`` with ``resolve(value, where)`` applied at the argument path ``parts``
    (``items[].target`` split at the dots), each value named by where it is."""
    if not parts:
        if isinstance(node, list):
            return [resolve(item, f"{where}[{i}]") for i, item in enumerate(node)]
        return resolve(node, where)
    part, rest = parts[0], parts[1:]
    many = part.endswith("[]")
    name = part[:-2] if many else part
    if not isinstance(node, Mapping) or name not in node:
        return node
    node = dict(node)
    path = f"{where}.{name}" if where else name
    if many and isinstance(node[name], list):
        if rest:
            node[name] = [_walk(item, rest, f"{path}[{i}]", resolve)
                          for i, item in enumerate(node[name])]
        else:
            node[name] = [resolve(item, f"{path}[{i}]") for i, item in enumerate(node[name])]
    else:
        node[name] = _walk(node[name], rest, path, resolve)
    return node


def _op_error(failure: ToolError | _OpFailed) -> ToolError:
    """A failed op's error with its ``field`` the op's own (``tool``, ``arguments.x``)."""
    if isinstance(failure, ToolError):
        return failure
    error = failure.error
    return ToolError(error.code, error.message, valid_options=error.valid_options,
                     field=f"arguments.{error.field}" if error.field else None,
                     details=error.details)


#: What a ``read_batch``'s reply needs besides its ops' answers: its envelope, summary and
#: what was left out, in characters.
_READ_MARGIN = 2_000


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))


def _read_answer(index: int, result: Result, op: Any, limit: int,
                 paged: bool) -> dict[str, Any]:
    """A ``read_batch`` op's answer: its result as the model reads a lone call's -- cut to
    ``limit`` as one would be -- without what a reading call never has (an empty
    ``changed``, ``created``...), led by ``op`` and ``tool``; with ``next``, the call for
    the next page, when there is one and its tool takes a ``cursor`` (``paged``)."""
    envelope = json.loads(result.to_text(limit))
    answer: dict[str, Any] = {"op": index, "tool": result.tool}
    for key, value in envelope.items():
        if key == "ok" or value is None or value in ([], {}, ""):
            continue
        answer[key] = value
    cursor = result.next_cursor
    arguments = op.get("arguments") if isinstance(op, Mapping) else None
    if cursor and paged and isinstance(arguments, Mapping):
        answer["next"] = {"tool": result.tool, "arguments": {**arguments, "cursor": cursor}}
    return answer


def _read_result(ops: Sequence[Any], answers: Sequence[tuple[dict[str, Any], int, Result]],
                 left: Sequence[tuple[int, str]], cap: int, timeout: float,
                 pictured: Mapping[int, int] | None = None) -> Result:
    """A ``read_batch``'s result: the answers in op order, then what was left out; the
    images of the answers, each captioned with its op (``ops[2] d1 page 3``).
    ``pictured``: what each op's images count toward ``cap``, by op."""
    pictured = pictured or {}
    failed = sum(not result.ok for _, _, result in answers)
    combined = Result(budget=cap)
    for _, index, result in answers:
        combined.images += [dataclasses.replace(image, caption=" ".join(
            part for part in (f"ops[{index}]", result.doc or "", image.label) if part))
            for image in result.images]
    data: dict[str, Any] = {"ops": [answer for answer, _, _ in answers]}
    said = [f"Read {len(answers) - failed} of {len(ops)} op(s)"]
    if failed:
        errors = [answer for answer, _, result in answers if not result.ok]
        shown = "; ".join(f"ops[{answer['op']}] ({answer['tool'] or '?'}): "
                          f"{answer.get('error', {}).get('message', '')}"
                          for answer in errors[:3])
        said.append(f"{failed} failed: {shown}" + (f"; and {failed - 3} more"
                                                   if failed > 3 else ""))
    if left:
        budget = [index for index, why in left if why == "budget"]
        late = [index for index, why in left if why == "deadline"]
        data["not_returned"] = [{"op": index, "why": (
            f"over the reply's {cap:,}-character budget" + (
                f" (its images count {pictured[index]:,})" if index in pictured else "")
            if why == "budget" else f"not run: past the {timeout:g} s deadline")}
            for index, why in left]
        data["resend"] = {"tool": "read_batch",
                          "arguments": {"ops": [ops[index] for index, _ in left]}}
        parts = []
        if budget:
            parts.append(f"{len(budget)} did not fit the {cap:,}-character reply")
        if late:
            parts.append(f"{len(late)} not run before the {timeout:g} s deadline")
        said.append(f"Not returned: {' and '.join(parts)}; send data.resend for them")
    if combined.images:
        said.append(f"{len(combined.images)} image(s) follow, in op order, each after its "
                    "op's caption")
    combined.summary = ". ".join(said)
    combined.data = data
    return combined


#: Characters an image counts for in a ``read_batch``'s reply, per token it costs: the rate
#: ``Limits.max_read_batch_chars`` is set at (96,000 characters, about 24,000 tokens).
CHARS_PER_IMAGE_TOKEN = 4


def image_chars(images: Iterable[Image]) -> int:
    """What ``images`` count toward a ``read_batch``'s character budget (round 35, R20):
    each its tokens -- the larger of Claude's and OpenAI's count for its size
    (:meth:`Image.describe`), the provider unknown here -- times
    :data:`CHARS_PER_IMAGE_TOKEN`.  A 1280 x 720 image is 4,784; a 1000 x 1414 one 7,344."""
    return sum(CHARS_PER_IMAGE_TOKEN * max(anthropic_image_tokens(image.width, image.height),
                                           openai_image_tokens(image.width, image.height))
               for image in images)


def _brief(error: ToolError) -> dict[str, Any]:
    """An op's error in its outcome: code and message, and what else it has."""
    brief: dict[str, Any] = {"code": error.code, "message": error.message}
    if error.field:
        brief["field"] = error.field
    if error.valid_options:
        brief["valid_options"] = error.valid_options
    if error.details:
        brief["details"] = error.details
    return brief


def _summaries(done: Sequence[Mapping[str, Any]]) -> str:
    return "; ".join(op["summary"] for op in done[:5] if op.get("summary")) + (
        f"; and {len(done) - 5} more" if len(done) > 5 else "")


def _failures(outcomes: Sequence[Mapping[str, Any]], errors: Mapping[int, ToolError],
              shown: int = 5) -> str:
    """The failed ops for a message: ``ops[1] (tool): message``, the first few."""
    said = [f"ops[{index}] ({outcomes[index]['tool'] or '?'}): {error.message}"
            for index, error in list(errors.items())[:shown]]
    more = len(errors) - shown
    return "; ".join(said) + (f"; and {more} more (data.ops)" if more > 0 else "")


def _none_applied(outcomes: list[dict[str, Any]], errors: Mapping[int, ToolError],
                  not_run: tuple[str, dict[str, Any]] | None = None) -> ToolError:
    """A batch none of whose ops applied: the first failure's code, field and options, and
    every op's error in ``details``; with ``not_run`` (:func:`_not_run`), what the deadline
    stopped."""
    index, first = next(iter(errors.items()))
    lead = (f"ops[{index}] ({outcomes[index]['tool'] or '?'}) failed, so nothing was "
            f"applied: {first.message}" if len(errors) == 1
            else f"none of the {len(errors)} ops applied: " + _failures(outcomes, errors))
    details: dict[str, Any] = {"applied": 0, "failed": list(errors), "ops": outcomes}
    if not_run is not None:
        lead += f". Not run: {not_run[0]}"
        details["not_run"] = not_run[1]
    return ToolError(first.code, lead, valid_options=first.valid_options,
                     field=f"ops[{index}]" + (f".{first.field}" if first.field else ""),
                     details=details)


#: The share of a batch's deadline a suggested batch size is to fill (the checks after
#: the last op, and ops slower than the average, take the rest).
FILL = 0.8


def _not_run(timeout: float, first: int, total: int, elapsed: float
             ) -> tuple[str, dict[str, Any]]:
    """What a batch's deadline stopped -- ops ``first`` to ``total - 1``, the batch having
    run for ``elapsed`` s -- as a sentence and as ``data.not_run`` (round 28, W8): how many,
    the first and last index, and the batch size that would have fitted at the rate the ops
    before ran (:data:`FILL` of the deadline), or none when none ran."""
    count = total - first
    data: dict[str, Any] = {"count": count, "first": first, "last": total - 1,
                            "ran": first, "seconds": round(elapsed, 1)}
    span = f"ops[{first}]" if count == 1 else f"ops[{first}]..ops[{total - 1}]"
    text = (f"{count} op(s), {span}, after the batch passed its {timeout:g} s deadline "
            f"({first} op(s) ran in {elapsed:.1f} s)")
    if first > 0 and elapsed > 0:
        size = max(1, int(timeout * FILL / (elapsed / first)))
        data["batch_size"] = size
        text += (f"; resend them as batches of at most {size} ops (about {elapsed / first:.2f} s "
                 "an op so far)")
    else:
        text += "; resend them as smaller batches"
    return text, data


class After:
    """A changing call's work after its change -- its checks -- run once its locks are let
    go (round 45, R64 stage 2): on the version it made, through that version's read copy
    (:meth:`DocumentEntry.commit`, kept before the locks were let go), so the next edit of
    the document does not wait for them.  The checks of a version are the same on its copy
    as on the document (equal versions are equal content).  Where the format does not copy
    documents (:attr:`~.session.DocumentFormat.read_copy`), the work is done under the
    locks, as before (:meth:`defer` says no)."""

    def __init__(self) -> None:
        self._work: list[tuple[DocumentEntry, Callable[[DocumentEntry], None]]] = []
        self._then: list[Callable[[], None]] = []
        self._kept: dict[str, tuple[Any, int]] = {}

    @staticmethod
    def can_defer(session: Session, entry: DocumentEntry) -> bool:
        fmt = session.formats.get(entry.kind)
        return (entry.pinned is None and fmt is not None and fmt.checks is not None
                and getattr(fmt, "read_copy", None) is not None)

    def defer(self, session: Session, entry: DocumentEntry,
              work: Callable[[DocumentEntry], None]) -> bool:
        """``work(entry)`` after the locks are let go, on the version's copy; ``False``
        (nothing deferred: do it now) where it cannot be."""
        if not self.can_defer(session, entry):
            return False
        self._work.append((entry, work))
        return True

    def then(self, work: Callable[[], None]) -> None:
        """``work()`` after every deferred piece."""
        self._then.append(work)

    def committed(self, entries: Iterable[DocumentEntry]) -> None:
        """The versions the call leaves, as committed (under the locks, last)."""
        for entry in entries:
            self._kept[entry.doc_id] = (entry.committed, entry.version)

    def retarget(self, draft: DocumentEntry, entry: DocumentEntry) -> None:
        """Round 45 (lane B): the work deferred for a call that ran on ``draft`` (an entry
        :meth:`DocumentEntry.drafted` made) is for ``entry``, the document its change was
        applied to."""
        self._work = [(entry if found is draft else found, work) for found, work in self._work]

    def keep(self, entry: DocumentEntry, committed: Any) -> None:
        """Round 45 (lane B): the version ``entry``'s deferred work reads is ``committed``'s
        (a call that changed nothing on a draft of it)."""
        self._kept[entry.doc_id] = (committed, committed.version)

    def run(self, result: Result) -> None:
        """The deferred work, each piece under its copy's lock; the warnings it raises go to
        ``result``.  A copy that cannot be made: the document's lock, while the document is
        still at the version (else the checks are said not to have run)."""
        for entry, work in self._work:
            committed, version = self._kept.get(entry.doc_id, (None, None))
            reader = (committed.entry(entry) if committed is not None
                      and committed.version == version else None)
            with collect_warnings() as caught:
                if reader is not None:
                    with reader.lock:
                        work(reader)
                else:
                    with entry.lock:
                        if entry.version == version:
                            work(entry)
                        else:
                            result.warnings.append(
                                f"{entry.doc_id}: the checks after this change did not run: "
                                "the document changed first; call check")
            result.warnings.extend(caught)
        for then in self._then:
            then()


class _Editing:
    """A changing call counted as editing its documents (:meth:`DocumentEntry.begin_edit`)
    from when it begins until its change is made (:meth:`done`, once), before its checks."""

    def __init__(self, entries: Sequence[DocumentEntry]) -> None:
        self._counts = {id(entry): (entry, entry.begin_edit()) for entry in entries}
        self._draft: tuple[DocumentEntry, int] | None = None

    def drafting(self, entry: DocumentEntry) -> None:
        """The call prepares a draft of ``entry`` from now (:meth:`DocumentEntry.draft_began`)
        until :meth:`drafted`."""
        self._draft = (entry, entry.draft_began())

    def drafted(self) -> None:
        """Its draft is done: applied, or it takes the lock instead."""
        draft, self._draft = self._draft, None
        if draft is not None:
            draft[0].draft_ended(draft[1])

    def beside(self, entry: DocumentEntry) -> int:
        """How many other calls edited ``entry`` when this one began."""
        found = self._counts.get(id(entry))
        return found[1] - 1 if found is not None else 0

    def done(self) -> None:
        self.drafted()
        counts, self._counts = self._counts, {}
        for entry, _count in counts.values():
            entry.end_edit()


class _Drafting:
    """What a call run on a draft (round 45, lane B: :meth:`Toolbox._run_block`) leaves for
    when its change is applied: its context (its refs, touched scopes, datasets named),
    whether a patch can carry what it did, and a batch's reads after its last change
    (``finish(entries)``, run on the version it made)."""

    def __init__(self) -> None:
        self.context: CallContext | None = None
        #: False: the call did what a draft's patch does not carry (datasets for the
        #: session, work deferred on the whole document): it runs on the document instead.
        self.local = True
        self.finish: Callable[..., Result] | None = None
        #: Round 51 (R105): a batch's reads of other documents (:class:`_Reads`).
        self.reads: _Reads | None = None
        #: Round 51: the batch's result, whose datasets the session keeps when the change
        #: is applied (:meth:`Toolbox._keep_datasets`, under the documents' locks).
        self.datasets: Result | None = None


class _Reads:
    """Round 51 (R105): the other documents a batch on a draft reads (:meth:`Toolbox.
    _run_block`), each as a call that only reads it reads it (:meth:`Session.read_lock`):
    its committed version's copy where its format makes one, else the document itself.
    No lock is held between ops: :meth:`held` takes those an op reads while it runs, and
    keeps the version each was first read at (:attr:`read`, checked again when the change
    is applied); one read again at another version is :attr:`moved` -- the batch did not
    read one state of it, and is prepared again."""

    def __init__(self, session: Session, docs: Sequence[str], named: Sequence[str] = ()) -> None:
        self.session = session
        #: ``doc -> the entry read``: a copy at the committed version, or the document.
        self.entries: dict[str, DocumentEntry] = {}
        #: ``doc -> (the document, the version read)``, from the first op that read it.
        self.read: dict[str, tuple[DocumentEntry, int]] = {}
        #: The documents the batch's ops name (not only take rows from): their checks are
        #: the batch's, as under their locks (:meth:`Toolbox._batch_checks`).
        self.named = [doc for doc in named if doc in docs]
        #: The first document read at two versions, or ``None``.
        self.moved: str | None = None
        for doc in dict.fromkeys(docs):
            entry = session.entry(doc)
            fmt = session.formats.get(entry.kind)
            reader = None
            if getattr(fmt, "read_copy", None) is not None:
                reader = entry.reader()
                if reader is None:
                    with entry.lock:
                        entry.commit(fmt)
                        reader = entry.reader()
            self.entries[doc] = reader or entry

    @contextmanager
    def held(self, docs: Iterable[str]) -> Iterator[None]:
        """The locks of ``docs`` (those of :attr:`entries`), in :func:`doc_order`, while the
        block runs; each one's version kept or compared."""
        with ExitStack() as stack:
            for doc in sorted({d for d in docs if d in self.entries}, key=doc_order):
                entry = self.entries[doc]
                stack.enter_context(entry.lock)
                live = entry.live or entry
                version = entry.version
                first = self.read.setdefault(doc, (live, version))
                if first[1] != version and self.moved is None:
                    self.moved = doc
            yield


@contextmanager
def _reads_held(session: Session, toolbox: "Toolbox", drafting: "_Drafting | None",
                tool: Tool, clean: Mapping[str, Any]) -> Iterator[None]:
    """Round 51: around a batch's op on a draft, the locks of the other documents it reads
    (names, or takes rows from: :meth:`Toolbox._sources`); every one when that cannot be
    told (the op is refused when it runs).  Elsewhere nothing."""
    reads = drafting.reads if drafting is not None else None
    if reads is None or not reads.entries:
        yield
        return
    try:
        docs = [*tool.doc_ids(clean), *toolbox._sources(session, tool, dict(clean))[1]]
    except ToolError:
        docs = list(reads.entries)
    with reads.held(docs):
        yield


def _uses_refs(node: Any) -> bool:
    """Whether arguments name a ref anywhere (a ``$`` in a string): what such a call reads
    of the document's refs is checked when its draft's change is applied."""
    if isinstance(node, str):
        return "$" in node
    if isinstance(node, Mapping):
        return any(_uses_refs(value) for value in node.values())
    if isinstance(node, (list, tuple)):
        return any(_uses_refs(value) for value in node)
    return False


def _redo_timing(result: Result, redone: int) -> None:
    """Round 45 (lane B): a result that says where its time went (``data.timing``) says
    how many times it was prepared again (``redo``), from one up."""
    timing = result.data.get("timing") if isinstance(result.data, dict) else None
    if redone and isinstance(timing, dict):
        timing["redo"] = redone


class _Turn:
    """The changes one :meth:`Toolbox.dispatch_many` turn's calls made, in order:
    ``(doc, version before, version after)``, each kept while the call held the lock.  A
    read of the turn that let its locks go is not warned that its document moved on when
    the turn's later calls alone moved it: the turn's order already says the read sees
    the document before them."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._steps: list[tuple[str, int, int]] = []

    def mark(self) -> int:
        with self._lock:
            return len(self._steps)

    def made(self, entries: Mapping[str, DocumentEntry], before: Mapping[str, int]) -> None:
        with self._lock:
            for doc, entry in entries.items():
                if entry.version != before.get(doc, entry.version):
                    self._steps.append((doc, before[doc], entry.version))

    def explains(self, doc: str, used: int, now: int, since: int) -> bool:
        """Whether the turn's changes since ``since`` take ``doc`` from ``used`` to ``now``,
        one after another, nothing else between."""
        with self._lock:
            version = used
            for step_doc, start, end in self._steps[since:]:
                if step_doc == doc:
                    if start != version:
                        return False
                    version = end
            return version == now


def _drew(hook: Any, draft: Any) -> bool:
    """Whether a call on ``draft`` drew values from the document's sequences (ids) -- the
    format's optional ``BlockEdits.drew``; one that cannot tell: no."""
    drew = getattr(hook, "drew", None)
    try:
        return bool(drew(draft)) if drew is not None else False
    except Exception:  # noqa: BLE001 -- cannot be told: run it under the lock, as before
        return True


def _changes_touch(session: Session, entry: DocumentEntry, used: int, now: int, scope: Any) -> bool:
    """Whether the changes that took ``entry`` from version ``used`` to ``now`` touched what a
    read covered (``scope``, :attr:`Call.read_scope`; ``None``: the whole document) -- by its
    format's ``BlockEdits.touches``; anything that cannot be told did."""
    if scope is None:
        return True
    hook = getattr(getattr(session.formats.get(entry.kind), "blocks", None), "touches", None)
    if hook is None:
        return True
    changes = entry.changes_between(used, now)
    if changes is None:
        return True
    try:
        return any(change is None or hook(change, scope) for change in changes)
    except Exception:  # noqa: BLE001 -- it cannot be told: it did
        logger.exception("the %s format's touches failed", entry.kind)
        return True


def moved_on(doc: str, used: int, now: int) -> str:
    """The warning of a reading call whose document changed while it worked on a snapshot
    (:meth:`Call.unlocked`): its result stands for the version it read."""
    return (f"{doc} changed while this call read it: the result is of version {used}, "
            f"the document is at version {now} now; call again for the latest")


def _set_checks(result: Result, checks: Mapping[str, Any]) -> None:
    if len(checks) == 1:
        result.checks = next(iter(checks.values()))
    elif checks:
        result.checks = dict(checks)


def _ref_names(node: Any) -> list[str]:
    """The ref names an op's arguments ask for (every ``ref``, at any depth), so a later
    op's ``$name`` can say which failed op would have made it."""
    if isinstance(node, str):
        try:
            node = json.loads(node)
        except ValueError:
            return []
    found: list[str] = []
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, Mapping):
            ref = item.get("ref")
            usable = (ref if REF.match(ref) else fold_ref(ref)) if isinstance(ref, str) else None
            if usable:
                found.append(usable)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found


def _stale_warning(entry: DatasetEntry) -> str:
    if not entry._own_stale():
        # stale through another source it read (DatasetEntry.also): that one says why
        for other in entry.also:
            if other.stale:
                return f"{entry.address} is stale: {_stale_reason(other)} (a source it also read)"
    return f"{entry.address} is stale: {_stale_reason(entry)}"


def _stale_reason(entry: DatasetEntry) -> str:
    if not entry._own_stale():
        for other in entry.also:
            if other.stale:
                return _stale_reason(other)
    if entry.closed:
        return f"{entry.doc} changed after it was derived, before it was closed"
    if entry.version is not None and entry.source_version is not None \
            and entry.version != entry.source_version:
        return (f"{entry.doc} has changed since it was derived (version {entry.version}, now "
                f"{entry.source_version}); derive it again for current values")
    if entry.dataset.invalidated:
        return ("it was computed from formula values whose cache is invalidated "
                f"({(entry.dataset.cache or {})['invalidated']} cell(s)): they are as last "
                "saved, and nothing here recalculates; recalculate the source in its "
                "application, then derive it again")
    return (f"{entry.doc} was changed after it was derived in the same batch; derive it "
            "again for current values")


#: ``\v`` in JSON text, not itself escaped: no JSON escape, but what a model writes for U+000B.
_VERTICAL_TAB = re.compile(r"(?<!\\)((?:\\\\)*)\\v")


def _json_strings(schema: Mapping[str, Any], arguments: Any) -> Any:
    """``arguments`` with an array or object argument that came as a JSON string parsed.

    A model sometimes writes a non-strict tool's array as a string holding the JSON
    (``batch``'s ``ops: "[{...}]"``, a long ``items``), as the OpenAI APIs send a whole
    call's arguments.  Only a top-level property whose schema is an array or object, and
    only when the string parses to that type, is taken; anything else is left for the
    validator to refuse.  Inside such a string a model writes a line break as ``\\v``, which
    JSON has no escape for: it is read as U+000B, the line break the text tools take."""
    if not isinstance(arguments, Mapping):
        return arguments
    properties = schema.get("properties") or {}
    fixed = None
    for name, value in arguments.items():
        kind = (properties.get(name) or {}).get("type")
        if kind not in ("array", "object") or not isinstance(value, str):
            continue
        text = value.strip()
        if not text.startswith("[" if kind == "array" else "{"):
            continue
        try:
            parsed = json.loads(text)
        except ValueError:
            try:
                parsed = json.loads(_VERTICAL_TAB.sub(r"\1\\u000b", text))
            except ValueError:
                continue
        if isinstance(parsed, list if kind == "array" else dict):
            fixed = dict(arguments) if fixed is None else fixed
            fixed[name] = parsed
    return arguments if fixed is None else fixed


def _message(exc: BaseException) -> str:
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc) or type(exc).__name__


def _report_facts(call: Call, result: Result) -> None:
    """The values a call wrote for references, in its result's ``data.facts``: each
    reference, the text written and its sources."""
    if not call.facts or not result.ok:
        return
    said = textfacts.briefs(call.facts)
    if result.data is None:
        result.data = {"facts": said}
    elif isinstance(result.data, dict) and "facts" not in result.data:
        result.data["facts"] = said


def _as_result(returned: Any, built: Result) -> Result:
    if isinstance(returned, Result):
        returned.images = built.images + returned.images
        returned.warnings = built.warnings + returned.warnings
        returned.refs = {**built.refs, **returned.refs}
        returned.datasets = list(dict.fromkeys(built.datasets + returned.datasets))
        return returned
    if returned is not None:
        built.data = returned
    return built


# -- warnings, per thread ----------------------------------------------------------------------

_local = threading.local()
_install_lock = threading.Lock()


def _route(message: Any, category: Any, filename: str, lineno: int, file: Any = None,
           line: Any = None) -> None:
    stack = getattr(_local, "collectors", None)
    if stack:
        stack[-1].append(f"{category.__name__}: {message}")
        return
    _route.previous(message, category, filename, lineno, file, line)  # type: ignore[attr-defined]


@contextmanager
def collect_warnings() -> Iterator[list[str]]:
    """Collect the warnings raised in this thread into a list, instead of printing them.

    ``warnings.catch_warnings`` is process-wide and not thread-safe; this routes through
    one hook that looks up the current thread's collector, so concurrent calls on
    different documents each get their own warnings.
    """
    with _install_lock:
        if warnings.showwarning is not _route:
            _route.previous = warnings.showwarning  # type: ignore[attr-defined]
            warnings.showwarning = _route
    stack = getattr(_local, "collectors", None)
    if stack is None:
        stack = _local.collectors = []
    caught: list[str] = []
    stack.append(caught)
    try:
        yield caught
    finally:
        stack.pop()


def _with_hint(error: ToolError, hint: str | None) -> ToolError:
    """``error`` with a format's hint added to its message; the code, field, options and
    details stay as they are."""
    if not hint:
        return error
    return ToolError(error.code, f"{error.message}; {hint}", field=error.field,
                     valid_options=error.valid_options, details=error.details)
