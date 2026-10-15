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
document serially in the order the model emitted them, calls on different documents at
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

**Checks.**  Handlers say what they touched (``call.touch(page)``); after a changing call
the dispatcher asks the document's format for the facts (``DocumentFormat.checks``) once.

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
Checks run once, at the end.
"""

from __future__ import annotations

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
from . import adapters, prompts
from .datasets import (DATASET_PREFIX, Dataset, DatasetEntry, check_dataset_id,
                       dataset_id, row_range, rows_text)
from .limits import LimitError, Limits
from .logs import CallRecord, digest, log_call, logger, shape
from .registry import CORE, Tool, ToolGroup, merge_tools
from .results import Image, Result, ToolError
from .schema import CallError, validate_call
from .session import (Clock, DocumentEntry, DocumentFormat, Output, Session, doc_order,
                      font_dirs_of, locale_of)
from .sources import Rows, qualify, resolve as resolve_rows
from .worker import WorkerPool


#: A ref's name: what ``$name`` may say.
REF = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_REF_USE = re.compile(r"^\$([a-z][a-z0-9_]{0,31})(/.*)?$")


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
        #: ``doc_id -> the last op that changed it``, in a batch.
        self.changed_at: dict[str, int] = {}
        #: The op running now (0 outside a batch).
        self.op = 0
        #: ``ref name -> the op that would have made it``: the refs of a batch's failed ops.
        self.failed_refs: dict[str, int] = {}

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
        return entry.stale if entry.version is not None else self.changed_since(entry)

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
        #: (:meth:`unlocked` may let them go); empty inside a batch.
        self.held: list[DocumentEntry] = []
        #: The version of the primary document the result describes, when the handler
        #: worked on a snapshot (:meth:`unlocked`); ``None``: the version after the call.
        self.version_used: int | None = None

    @property
    def in_batch(self) -> bool:
        return self.context.batch

    @property
    def changing(self) -> bool:
        """Whether this call changes its document (:meth:`Tool.changes`): false for a
        changing tool's reading mode."""
        return self.tool.changes(self.arguments)

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
        """Name ``address`` ``name``, so later calls on the document may write ``$name``."""
        if not REF.match(name):
            raise ToolError("invalid_arguments", ref_rule(name), field="ref")
        doc = doc or (self.entry.doc_id if self.entry else None)
        if doc is None:
            raise ToolError("invalid_arguments", "a ref needs a document")
        self.context.refs.setdefault(doc, {})[name] = address
        self.result.refs[name] = address

    def refs(self, doc: str | None = None) -> dict[str, str]:
        """Every ref of a document this call can use: kept ones, then this call's own."""
        doc = doc or (self.entry.doc_id if self.entry else None)
        entry = self.entries.get(doc) if doc else None
        known = dict(entry.refs) if entry is not None else {}
        known.update(self.context.refs.get(doc, {}))
        return known

    def resolve_ref(self, value: str, *, doc: str | None = None, field: str | None = None) -> str:
        """``value`` with a leading ``$name`` replaced by its address; other values as given."""
        match = _REF_USE.match(value) if isinstance(value, str) else None
        if match is None:
            return value
        known = self.refs(doc)
        name, rest = match.group(1), match.group(2) or ""
        if name not in known:
            raise self.context.missing_ref(name, field) or ToolError(
                "not_found", f"no ref ${name} in {doc or self.entry.doc_id}",
                field=field, valid_options=[f"${key}" for key in sorted(known)])
        return known[name] + rest

    def define_dataset(self, id: str, dataset: Dataset, *, source: Any = _PRIMARY,
                       origin: str | None = None) -> str:
        """Keep ``dataset`` in the session as ``d:<id>`` once the call succeeds; its address.

        ``source`` is what it was derived from: by default the call's document; another
        document's handle; a :class:`~.datasets.DatasetEntry` (a dataset derived from a
        dataset, which inherits its source); or ``None`` (no document: never stale).  The
        source's version is taken when the call ends, so a changing call defines a dataset
        from the state it leaves.  ``origin``: the address in the source document it was
        derived from (``t:sales``), which ``describe`` lists as ``d3/t:sales``; a dataset
        derived from a dataset has that dataset as its origin unless given.  An existing id
        is replaced.  ``limit`` when the dataset is
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
            return self.session.dataset(address, field=field)
        except ToolError as error:
            error.valid_options = list(dict.fromkeys(
                error.valid_options + [DATASET_PREFIX + key for key in self.context.datasets]))
            raise

    def source(self, value: str, *, field: str | None = None) -> Rows:
        """The rows a source names (:mod:`.sources`): a whole dataset (``d:by_region``) or a
        run of its rows (``d:by_region/#9:#16``, ``/#9:`` to the last, ``/#9``;
        :func:`~.datasets.row_range`), or an address in a document this call holds
        (``d2/t:sales``, ``t:sales``), read now by that document's format.  A row past a
        dataset's last is refused, never cut.  ``limit`` over ``Limits.max_dataset_cells``."""
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
        doc, address = qualify(value, self.session.documents, self.session.formats,
                               field=field)
        entry = self.entries.get(doc)
        if entry is None:
            raise ToolError("refused", f"{doc} is not held by this call: name it in an "
                            "argument the tool declares as a source", field=field)
        fmt = self.session.formats[entry.kind]
        dataset = resolve_rows(fmt, entry.document, address)
        if dataset.cells > self.limits.max_dataset_cells:
            raise ToolError("limit", f"{doc}/{address} has {dataset.cells} values; at most "
                            f"{self.limits.max_dataset_cells}: derive a smaller dataset",
                            field=field)
        return Rows(f"{doc}/{address}", dataset, doc=doc, kind=entry.kind, name=entry.name,
                    version=entry.version, merge=fmt.merge_sources)

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
        batch.  A handler that will work unlocked takes its snapshot first."""
        return bool(self.held) and not self.context.batch and not self.changing

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

        Inside a batch, or in a changing call, the locks stay held (the work is part of an
        undo step or of a run of ops) and this yields ``False``."""
        if self.entry is not None and self.version_used is None:
            self.version_used = self.entry.version
        if not self.may_unlock:
            yield False
            return
        held = sorted(self.held, key=lambda entry: doc_order(entry.doc_id))
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

    def run(self, fn: Callable[..., Any], *args: Any, timeout: float | None = None,
            **kwargs: Any) -> Any:
        """Run ``fn`` in a worker process (or in-process: :mod:`.worker`); ``timeout`` (s)
        defaults to the render deadline."""
        return self.toolbox.pool.run(fn, *args, timeout=timeout or self.limits.render_timeout,
                                     **kwargs)

    def output(self, name: str, format: str, data: bytes, *,
               entry: DocumentEntry | None = None) -> dict[str, Any]:
        """Hand a file to the application, behind the validate gate.

        The document's problems now are compared with those it had when opened; a new one
        refuses the save unless the application allowed it (``allow_new_problems``).  The
        model gets the name, format, size and the report -- never the bytes.
        """
        entry = entry or self.entry
        report: dict[str, Any] | None = None
        if entry is not None:
            fmt = self.session.formats.get(entry.kind)
            if fmt is not None and fmt.problems is not None:
                key = fmt.problem_key
                baseline = {key(problem) for problem in entry.baseline_problems}
                problems = list(fmt.problems(entry.document))
                new = [str(problem) for problem in problems if key(problem) not in baseline]
                now = {key(problem) for problem in problems}
                fixed = [str(problem) for problem in entry.baseline_problems
                         if key(problem) not in now]
                report = {"new": new, "fixed": fixed, "baseline": len(entry.baseline_problems)}
                if new and not self.toolbox.allow_new_problems:
                    raise ToolError("refused", f"saving would add {len(new)} validation "
                                    "problem(s); fix them first", valid_options=new,
                                    details={"validate": report})
        output = Output(name=name, format=format, data=data,
                        doc=entry.doc_id if entry else None, validate=report)
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


class Toolbox:
    """The tools of one or more libraries, ready for a provider and a session."""

    def __init__(self, tools: Iterable[Tool], *, formats: Iterable[DocumentFormat] = (),
                 groups: Iterable[ToolGroup] = (), limits: Limits | None = None,
                 allow_new_problems: bool = False, workers: int = 2,
                 start_method: str = "spawn", log_arguments: bool = False,
                 error_map: Mapping[type[BaseException], Any] | None = None,
                 strict_first: Sequence[str] | None = None,
                 runner: Any = None, font_dirs: Any = None, locale: Any = None) -> None:
        """``workers`` worker processes render and lay out (:class:`~.worker.WorkerPool`).
        In a daemonic process, which may not start multiprocessing children, the pool uses
        ``workers`` subprocess workers instead (:class:`~.worker.SubprocessPool`), by
        itself; ``0`` runs the work in-process, in threads, whose deadlines bound the wait
        but cannot stop the work.  ``runner`` replaces the pool: anything with
        ``run(fn, *args, timeout=..., **kwargs)`` and ``close()`` -- a
        :class:`~.worker.SubprocessPool`, an :class:`~.worker.InProcess`, a
        ``WorkerPool(fallback=...)`` of the application's choice, or its own executor.

        ``font_dirs`` -- the application's own font folders, searched before the operating
        system's for every render and measurement -- is the default of the sessions this
        toolbox makes (:meth:`session` overrides it per session).  ``None`` leaves it to
        the renderers: ``OOXML_FONT_DIRS``, then the system's folders.  It is never a tool
        argument: the model neither sees nor sets it.

        ``locale`` -- the regional settings documents are drawn and edited in, an
        :class:`ooxml_common.locales.Locale` (decimal and thousands separators, date order,
        language, list separator) -- is likewise the default of the sessions this toolbox
        makes, forwarded to every format and to the work run in worker processes.  ``None``
        is en-US (:data:`~.session.DEFAULT_LOCALE`, :data:`ooxml_common.locales.EN_US`'s
        values), always: never the operating system's.  Not a tool argument either."""
        self.tools: dict[str, Tool] = merge_tools(tools)
        self.formats: dict[str, DocumentFormat] = {fmt.kind: fmt for fmt in formats}
        #: Claude's strict priority: the application's order, else each format's in turn.
        self.strict_first: list[str] = list(dict.fromkeys(
            strict_first if strict_first is not None
            else [name for fmt in self.formats.values() for name in fmt.strict_first]))
        self.groups: dict[str, ToolGroup] = {group.name: group for group in groups}
        self.limits = limits or Limits()
        #: The validate gate's override: the application's to set, never the model's.
        self.allow_new_problems = allow_new_problems
        self.log_arguments = log_arguments
        #: The default :attr:`Session.font_dirs` of the sessions this toolbox makes.
        self.font_dirs: tuple[str, ...] | None = font_dirs_of(font_dirs)
        #: The default :attr:`Session.locale` of the sessions this toolbox makes.
        self.locale: Any = locale_of(locale)
        self._workers = workers
        self._start_method = start_method
        self._pool: Any = runner
        self._pool_lock = threading.Lock()
        self._errors: dict[type[BaseException], Any] = {}
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
                font_dirs: Any = _TOOLBOX_DEFAULT, locale: Any = _TOOLBOX_DEFAULT) -> Session:
        """A new session.  ``font_dirs`` replaces the toolbox's :attr:`font_dirs` for this
        session (``None``: the renderers' default; ``[]``: no folders of the
        application's, ``OOXML_FONT_DIRS`` not read either); ``locale`` the toolbox's
        :attr:`locale` (``None``: en-US) -- one user's or client's own."""
        return Session(self.formats.values(), clock=clock, limits=limits or self.limits,
                       on_output=on_output,
                       font_dirs=self.font_dirs if font_dirs is _TOOLBOX_DEFAULT else font_dirs,
                       locale=self.locale if locale is _TOOLBOX_DEFAULT else locale)

    def tool(self, name: str) -> Tool:
        if name not in self.tools:
            raise ToolError("invalid_arguments", f"no tool {name!r}",
                            valid_options=difflib.get_close_matches(name, self.tools, 5, 0.5)
                            or sorted(self.tools))
        return self.tools[name]

    def select(self, groups: str | Iterable[str] | None = None) -> list[Tool]:
        """The tools to send: all (``None`` or ``"all"``), or core plus the named groups."""
        if groups is None or groups == "all":
            return list(self.tools.values())
        wanted = {CORE} | ({groups} if isinstance(groups, str) else set(groups))
        unknown = wanted - {tool.group for tool in self.tools.values()} - {CORE}
        if unknown:
            raise ValueError(f"unknown group(s) {sorted(unknown)}")
        return [tool for tool in self.tools.values() if tool.group in wanted]

    def definitions(self, provider: str = "anthropic", *,
                    groups: str | Iterable[str] | None = None, defer: bool | None = None,
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
        """
        tools = self.select(groups)
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
                      provider: str = "openai-responses") -> dict[str, Any]:
        """An OpenAI ``tool_choice`` limiting calls to the core and ``groups``, while the
        ``tools`` sent (and so the cache) stay the same: for an application that sends
        every definition without tool search and narrows by turn."""
        names = [tool.name for tool in self.select(groups)]
        return adapters.openai_allowed_tools(names, chat=provider == "openai-chat")

    def system_prompt(self, *, extra: str | None = None) -> str:
        """The shared fragment, each format's fragment, then the application's ``extra``."""
        return prompts.system_prompt(*(fmt.prompt for fmt in self.formats.values()), extra=extra)

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
                self._pool = WorkerPool(self._workers, start_method=self._start_method)
            return self._pool

    def close(self) -> None:
        with self._pool_lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.close()

    def __enter__(self) -> "Toolbox":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def dispatch(self, session: Session, name: str, arguments: Any) -> Result:
        """Run one call and return its result; never raises for the model's mistakes."""
        started = time.perf_counter()
        tool: Tool | None = None
        doc_ids: list[str] = []
        primary: DocumentEntry | None = None
        entries: dict[str, DocumentEntry] = {}
        clean: Any = arguments
        notes: list[str] = []       # aliases read: warnings first
        try:
            tool = self.tool(name)
            clean = self._arguments(tool, arguments, notes)
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
                    hint=lambda value: self._source_hint(session, doc_ids, value))
            reading = [doc for doc in found if doc not in doc_ids]
            with session.lock(doc_ids + reading) as locked:
                entries = {entry.doc_id: entry for entry in locked}
                primary = entries[doc_ids[0]] if doc_ids else None
                result = self._run(session, tool, clean, entries, primary, reading)
        except ToolError as error:
            result = Result.failure(error)
        except Exception as exc:  # noqa: BLE001 -- the model gets an error result
            result = Result.failure(self.map_exception(exc))
        result.warnings[:0] = notes
        if primary is not None:
            result.doc = primary.doc_id
            if result.version is None:      # a snapshot's (Call.unlocked), else the latest
                result.version = primary.version
        result.tool = name
        result.duration = time.perf_counter() - started
        self._record(session, name, doc_ids, clean, result, entries)
        return result

    def dispatch_many(self, session: Session, calls: Sequence[tuple[str, Any]], *,
                      max_workers: int = 8) -> list[Result]:
        """Run one turn's calls: per document in the order given, documents concurrently."""
        if not calls:
            return []
        documents = [self._documents_named(session, name, arguments)
                     for name, arguments in calls]
        futures: list[Future[Result]] = []
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(calls)))) as executor:
            for index, (name, arguments) in enumerate(calls):
                earlier = [futures[j] for j in range(index) if documents[j] & documents[index]
                           or not documents[j] or not documents[index]]
                futures.append(executor.submit(self._after, earlier, session, name, arguments))
            return [future.result() for future in futures]

    def _after(self, earlier: list[Future[Result]], session: Session, name: str,
               arguments: Any) -> Result:
        for future in earlier:
            future.exception()  # wait; its outcome is its own
        return self.dispatch(session, name, arguments)

    def _documents_named(self, session: Session, name: str,
                         arguments: Any) -> frozenset[str]:
        """The documents a call will lock, as far as can be told before validating it."""
        try:
            tool = self.tools[name]
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

    def _arguments(self, tool: Tool, arguments: Any,
                   notes: list[str] | None = None) -> dict[str, Any]:
        """The call's arguments, parsed, aliases read (``notes`` gets a line for each) and
        validated: ``invalid_arguments`` otherwise."""
        notes = notes if notes is not None else []
        if isinstance(arguments, (str, bytes)):
            try:
                arguments = json.loads(arguments or "{}")
            except ValueError as exc:
                raise ToolError("invalid_arguments", f"the arguments are not JSON: {exc}") from None
        if arguments is None:
            arguments = {}
        arguments = _aliased(tool, arguments, notes)
        arguments = _json_strings(tool.schema, arguments)
        try:
            clean = validate_call(tool.schema, arguments, notes)
        except CallError as exc:
            message = exc.message
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

    def _run(self, session: Session, tool: Tool, arguments: dict[str, Any],
             entries: dict[str, DocumentEntry], primary: DocumentEntry | None,
             reading: Sequence[str] = ()) -> Result:
        if tool.composite:
            edited = {doc: entry for doc, entry in entries.items() if doc not in reading}
            return self._run_batch(session, arguments, edited, primary,
                                   {doc: entries[doc] for doc in reading})
        handler = self._handler(session, tool, arguments, primary)
        changing = tool.changes(arguments)
        key = arguments.get("key") if changing and isinstance(arguments.get("key"), str) else None
        if key is not None and primary is not None and (tool.name, key) in primary.keys:
            replay = primary.keys[(tool.name, key)]
            return Result(summary=f"already done with key {key!r}: nothing new was made",
                          created=list(replay.created), changed=list(replay.changed),
                          refs=dict(replay.refs), data=replay.data)
        context = CallContext()
        with collect_warnings() as caught:
            arguments = self._with_refs(tool, arguments, entries, primary, context)
            call = Call(self, session, tool, arguments, entries, primary, context)
            self._with_datasets(tool, arguments, call)
            call.held = list(entries.values())   # dispatch() holds each once
            if changing and primary is not None:
                with primary.batch():
                    returned = handler(call, **arguments)
                primary.bump()
            else:
                returned = handler(call, **arguments)
            result = _as_result(returned, call.result)
            if call.version_used is not None:
                result.version = call.version_used
            if changing and result.ok:
                self._keep_refs(entries, context, [primary.doc_id] if primary else [])
                if primary is not None:
                    result.checks = {**self._checks(session, primary, context), **result.checks}
            if result.ok:
                self._keep_datasets(session, context, result)
            else:
                result.datasets = []
        result.warnings.extend(caught)
        if key is not None and primary is not None and result.ok:
            primary.keys[(tool.name, key)] = result
        return result

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
                   context: CallContext) -> dict[str, Any]:
        """``arguments`` with every ``$name`` in a declared target replaced by its address."""
        if not tool.refs or primary is None:
            return arguments
        doc = primary.doc_id
        known = dict(primary.refs)
        known.update(context.refs.get(doc, {}))

        def resolve(value: Any, where: str) -> Any:
            match = _REF_USE.match(value) if isinstance(value, str) else None
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
            _walk(arguments, path.split("."), "",
                  lambda value, where, hinted=hinted: resolve(value, where, hinted))

    @staticmethod
    def _sources(session: Session, tool: Tool, arguments: dict[str, Any], *,
                 hint: Callable[[str], str | None] | None = None,
                 ) -> tuple[dict[str, Any], list[str]]:
        """``arguments`` with every address in a declared source qualified with its document
        (``t:sales`` -> ``d2/t:sales``), and those documents, in order.  Datasets (``d:``)
        are left as they are.  ``hint(value)``: words added to a ``not_found``."""
        if not tool.sources:
            return arguments, []
        found: list[str] = []
        documents = dict(session.documents)

        def resolve(value: Any, where: str) -> Any:
            if not isinstance(value, str) or value.startswith(DATASET_PREFIX):
                return value
            try:
                doc, address = qualify(value, documents, session.formats, field=where)
            except ToolError as error:
                if hint is None or error.code != "not_found":
                    raise
                raise _with_hint(error, hint(value)) from None
            if doc not in found:
                found.append(doc)
            return f"{doc}/{address}"

        for path in tool.sources:
            arguments = _walk(arguments, path.split("."), "", resolve)
        return arguments, found

    @staticmethod
    def _source_hint(session: Session, doc_ids: Sequence[str], value: str) -> str | None:
        """What the format of the call's document (the first of ``doc_ids``) says about a
        source value that names nothing (:attr:`DocumentFormat.source_hint`), or ``None``."""
        entry = session.documents.get(doc_ids[0]) if doc_ids else None
        fmt = session.formats.get(entry.kind) if entry is not None else None
        hook = getattr(fmt, "source_hint", None)
        if hook is None:
            return None
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
            tool = self.tools.get(op.get("tool", "")) if isinstance(op, Mapping) else None
            args = op.get("arguments") if isinstance(op, Mapping) else None
            if tool is None or not tool.sources or not isinstance(args, Mapping):
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
            if entry.version is None and entry.doc is not None and not entry.closed:
                if context.changed_since(entry):
                    # A later op of the batch changed its source: the state it came from
                    # has no version of its own.
                    entry.mark_stale()
                entry.version = entry.source_version
        session.keep_datasets(defined)
        named = {**context.datasets_used, **context.datasets}
        for entry in named.values():
            if entry.stale:
                result.warnings.append(_stale_warning(entry))

    @staticmethod
    def _keep_refs(entries: dict[str, DocumentEntry], context: CallContext,
                   changed: Iterable[str] = ()) -> None:
        """Keep the refs a call defined, and record every changed document's refs under
        its new version (so undo can bring back the refs of the state it returns to)."""
        for doc in set(context.refs) | set(changed):
            if doc in entries:
                entries[doc].keep_refs(context.refs.get(doc, {}))

    def _checks(self, session: Session, entry: DocumentEntry,
                context: CallContext) -> dict[str, Any]:
        fmt = session.formats.get(entry.kind)
        if fmt is None or fmt.checks is None:
            return {}
        return dict(fmt.checks(entry, list(context.touched.get(entry.doc_id, []))))

    # -- batch -------------------------------------------------------------------------------

    def _prepare(self, op: Any, notes: list[str] | None = None) -> tuple[Tool, dict[str, Any]]:
        """One op's tool and its validated arguments (the aliases read added to ``notes``).
        A :class:`ToolError`'s ``field`` is the op's own: ``tool``, or ``arguments`` and the
        path in them."""
        name = op.get("tool", "") if isinstance(op, Mapping) else ""
        if name not in self.tools:
            raise ToolError("invalid_arguments", f"no tool {name!r}", field="tool",
                            valid_options=difflib.get_close_matches(name, self.tools, 5, 0.5)
                            or sorted(t.name for t in self.tools.values() if t.batchable))
        tool = self.tools[name]
        if not tool.batchable or tool.composite:
            raise ToolError("invalid_arguments", f"{name} cannot run inside a batch; call it "
                            "on its own", field="tool")
        try:
            clean = self._arguments(tool, op.get("arguments") or {}, notes)
        except ToolError as error:
            raise ToolError(error.code, error.message, field="arguments" + (
                f".{error.field}" if error.field else ""),
                valid_options=error.valid_options) from None
        return tool, clean

    def _ops(self, arguments: Mapping[str, Any],
             notes: list[str]) -> list[tuple[int, Tool, dict[str, Any]]]:
        """An atomic batch's ops, each validated against its own tool: before anything runs.
        The aliases read go to ``notes``, each naming its op."""
        prepared = []
        for index, op in enumerate(arguments.get("ops") or []):
            where = f"ops[{index}]"
            try:
                read: list[str] = []
                tool, clean = self._prepare(op, read)
                notes += [f"{where}: {note}" for note in read]
            except ToolError as error:
                said = where if error.field == "tool" else f"{where} ({op.get('tool', '')})"
                raise ToolError(error.code, f"{said}: {error.message}",
                                field=f"{where}.{error.field}",
                                valid_options=error.valid_options) from None
            prepared.append((index, tool, clean))
        return prepared

    def _batch_documents(self, arguments: Mapping[str, Any]) -> list[str]:
        """Every document a batch's ops name, before they are validated (for the locks)."""
        found: list[str] = []
        for op in arguments.get("ops") or []:
            tool = self.tools.get(op.get("tool", "")) if isinstance(op, Mapping) else None
            args = op.get("arguments") if isinstance(op, Mapping) else None
            if tool is None or tool.composite or not isinstance(args, Mapping):
                continue
            for doc in tool.doc_ids(args):
                if doc not in found:
                    found.append(doc)
        return found

    def _run_batch(self, session: Session, arguments: dict[str, Any],
                   entries: dict[str, DocumentEntry], primary: DocumentEntry | None,
                   reading: Mapping[str, DocumentEntry] | None = None) -> Result:
        """``entries``: the documents the ops change (one undo step each); ``reading``:
        the documents they only take rows from, locked but left alone."""
        ops = arguments.get("ops") or []
        limits = session.limits
        if len(ops) > limits.max_batch_ops:
            raise ToolError("limit", f"{len(ops)} ops; at most {limits.max_batch_ops} per batch",
                            field="ops")
        if not arguments.get("atomic"):
            return self._run_partial(session, ops, entries, reading or {})
        notes: list[str] = []
        prepared = self._ops(arguments, notes)
        context = CallContext()
        context.batch = True
        started = time.perf_counter()
        done: list[dict[str, Any]] = []
        combined = Result()
        with collect_warnings() as caught:
            try:
                with ExitStack() as stack:
                    for doc in sorted(entries, key=doc_order):
                        stack.enter_context(entries[doc].batch())
                    for index, tool, clean in prepared:
                        context.op = index
                        if time.perf_counter() - started > limits.batch_timeout:
                            raise _OpFailed(index, tool.name, ToolError(
                                "timeout", f"the batch passed its {limits.batch_timeout:g} s "
                                "deadline"))
                        done.append(self._run_op(session, index, tool, clean, entries, context,
                                                 combined, reading or {}))
                        if tool.changes(clean):
                            for doc in tool.doc_ids(clean):
                                context.changed_at[doc] = index
            except _OpFailed as failed:
                error = failed.error
                raise ToolError(
                    error.code, f"ops[{failed.index}] ({failed.tool}) failed, so nothing in the "
                    f"batch was applied: {error.message}",
                    valid_options=error.valid_options,
                    field=f"ops[{failed.index}]" + (f".arguments.{error.field}"
                                                    if error.field else ""),
                    details={"op": failed.index, "tool": failed.tool, "error": error.to_json(),
                             "completed_before": failed.index}) from None
            for doc in sorted(entries, key=doc_order):
                entries[doc].bump()
            self._keep_refs(entries, context, list(entries))
            changing = any(tool.changes(clean) for _, tool, clean in prepared)
            checks = {doc: self._checks(session, entries[doc], context)
                      for doc in sorted(entries, key=doc_order)} if changing else {}
            self._keep_datasets(session, context, combined)
        combined.warnings[:0] = notes
        combined.warnings.extend(caught)
        _set_checks(combined, checks)
        combined.summary = f"Ran {len(done)} op(s) as one step: " + _summaries(done)
        combined.data = {"ops": done}
        return combined

    def _run_partial(self, session: Session, ops: Sequence[Any],
                     entries: dict[str, DocumentEntry],
                     reading: Mapping[str, DocumentEntry]) -> Result:
        """A batch whose ops apply one by one: a failing op is rolled back alone and
        reported, and the ops after it still run.  What applied is one undo step."""
        limits = session.limits
        context = CallContext()
        context.batch = True
        started = time.perf_counter()
        outcomes: list[dict[str, Any]] = []
        errors: dict[int, ToolError] = {}
        changing = False
        combined = Result()
        with collect_warnings() as caught:
            with ExitStack() as stack:
                for doc in sorted(entries, key=doc_order):
                    stack.enter_context(entries[doc].batch())
                for index, op in enumerate(ops):
                    context.op = index
                    name = op.get("tool", "") if isinstance(op, Mapping) else ""
                    try:
                        if time.perf_counter() - started > limits.batch_timeout:
                            raise ToolError("timeout", "not run: the batch passed its "
                                            f"{limits.batch_timeout:g} s deadline")
                        read: list[str] = []
                        tool, clean = self._prepare(op, read)
                        combined.warnings += [f"ops[{index}]: {note}" for note in read]
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
            if errors and len(errors) == len(outcomes):
                raise _none_applied(outcomes, errors)
            for doc in sorted(entries, key=doc_order):
                if doc in context.changed_at:
                    entries[doc].bump()
            self._keep_refs(entries, context, list(entries))
            checks = {doc: self._checks(session, entries[doc], context)
                      for doc in sorted(entries, key=doc_order)} if changing else {}
            self._keep_datasets(session, context, combined)
        combined.warnings.extend(caught)
        _set_checks(combined, checks)
        done = [outcome for outcome in outcomes if "error" not in outcome]
        if errors:
            combined.summary = (f"Ran {len(done)} of {len(outcomes)} ops as one step: "
                                + _summaries(done) + ". Not applied -- fix and resend only "
                                "these: " + _failures(outcomes, errors))
            combined.data = {"applied": len(done), "failed": list(errors), "ops": outcomes}
        else:       # every op applied: the result an all-or-none batch gives
            combined.summary = f"Ran {len(done)} op(s) as one step: " + _summaries(done)
            combined.data = {"ops": outcomes}
        return combined

    def _run_alone(self, session: Session, index: int, tool: Tool, arguments: dict[str, Any],
                   entries: dict[str, DocumentEntry], context: CallContext,
                   combined: Result, reading: Mapping[str, DocumentEntry]) -> dict[str, Any]:
        """:meth:`_run_op` with what the op changes rolled back if it fails: its documents
        to where they were (the format told, ``DocumentFormat.restored``), and the refs,
        datasets and touched scopes it defined."""
        mark = context.mark()
        rolled: list[DocumentEntry] = []
        try:
            with ExitStack() as stack:
                for doc in sorted({d for d in tool.doc_ids(arguments) if d in entries},
                                  key=doc_order):
                    if stack.enter_context(entries[doc].savepoint()):
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
        try:
            for doc in doc_ids:
                if doc not in entries and doc not in reading:
                    session.entry(doc)      # not_found, naming the open documents
                    raise ToolError("refused", f"{doc} was opened after the batch began: "
                                    "call it on its own", field="doc")
            handler = self._handler(session, tool, arguments, primary)
            arguments = self._with_refs(tool, arguments, entries, primary, context)
            arguments, found = self._sources(
                session, tool, arguments,
                hint=lambda value: self._source_hint(session, doc_ids, value))
            held = {**reading, **entries}
            call = Call(self, session, tool, arguments,
                        {doc: held[doc] for doc in [*doc_ids, *found] if doc in held},
                        primary, context)
            self._with_datasets(tool, arguments, call)
            result = _as_result(handler(call, **arguments), call.result)
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
        combined.warnings += [f"ops[{index}]: {w}" for w in result.warnings]
        entry: dict[str, Any] = {"op": index, "tool": tool.name, "summary": result.summary}
        if result.created:
            entry["created"] = result.created
        if result.data not in (None, {}, []):
            entry["data"] = result.data
        return entry

    def map_exception(self, exc: BaseException) -> ToolError:
        """A library exception as an error code, with its valid options when known."""
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
                result: Result, entries: dict[str, DocumentEntry]) -> None:
        loggable = arguments if isinstance(arguments, Mapping) else {"raw": str(arguments)}
        record = CallRecord(
            tool=name, docs=list(doc_ids), digest=digest(loggable), shape=shape(loggable),
            ok=result.ok, code=result.error.code if result.error else None,
            summary=result.summary, duration=result.duration, version=result.version,
            arguments=dict(loggable) if self.log_arguments else None)
        session.log.append(record)
        for entry in entries.values():
            entry.log.append(record)
        if logger.isEnabledFor(logging.INFO):
            log_call(record)


def _aliased(tool: Tool, arguments: Any, notes: list[str]) -> Any:
    """``arguments`` with every name in ``tool.aliases`` renamed to the tool's own, a note
    for each (``from_columns read as columns``).  An alias given with the name it stands for is
    refused: the call says one thing twice."""
    if not tool.aliases or not isinstance(arguments, Mapping):
        return arguments
    renamed = dict(arguments)
    for alias, name in tool.aliases.items():
        if renamed.get(alias) is None:
            continue
        meant = name(renamed) if callable(name) else name
        if meant is None or meant == alias:
            continue
        if renamed.get(meant) is not None:
            raise ToolError("invalid_arguments", f"{alias} and {meant} say the same here; "
                            f"give {meant} only", field=alias, valid_options=[meant])
        renamed[meant] = renamed.pop(alias)
        notes.append(f"{alias} read as {meant}")
    return renamed


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


def _misspelt_ref(value: Any, known: Mapping[str, str]) -> str | None:
    """The known ref a ``$Name`` that is no ref name stands for (``$catChart`` for a ref
    ``catchart`` or ``cat_chart``); ``None`` for anything else, which stays an address."""
    if not isinstance(value, str) or not value.startswith("$"):
        return None
    head = value[1:].split("/", 1)[0]
    folded = head.lower().replace("_", "").replace("-", "")
    found = [name for name in known if name.replace("_", "") == folded]
    return found[0] if len(found) == 1 else None


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


def _none_applied(outcomes: list[dict[str, Any]], errors: Mapping[int, ToolError]) -> ToolError:
    """A batch none of whose ops applied: the first failure's code, field and options, and
    every op's error in ``details``."""
    index, first = next(iter(errors.items()))
    lead = (f"ops[{index}] ({outcomes[index]['tool'] or '?'}) failed, so nothing was "
            f"applied: {first.message}" if len(errors) == 1
            else f"none of the {len(errors)} ops applied: " + _failures(outcomes, errors))
    return ToolError(first.code, lead, valid_options=first.valid_options,
                     field=f"ops[{index}]" + (f".{first.field}" if first.field else ""),
                     details={"applied": 0, "failed": list(errors), "ops": outcomes})


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
            if isinstance(ref, str) and REF.match(ref):
                found.append(ref)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found


def _stale_warning(entry: DatasetEntry) -> str:
    if entry.closed:
        return (f"{entry.address} is stale: {entry.doc} changed after it was derived, "
                "before it was closed")
    if entry.version is not None and entry.source_version is not None \
            and entry.version != entry.source_version:
        return (f"{entry.address} is stale: {entry.doc} has changed since it was derived "
                f"(version {entry.version}, now {entry.source_version}); derive it again "
                "for current values")
    return (f"{entry.address} is stale: {entry.doc} was changed after it was derived in "
            "the same batch; derive it again for current values")


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
