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
from os import PathLike
from typing import Any, Callable, Hashable, Iterable, Iterator, Sequence

from .limits import LimitError, Limits, check_declared_type, check_image, check_package
from .logs import CallRecord
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


@dataclass
class DocumentEntry:
    """One open document and what the session keeps beside it."""

    doc_id: str
    kind: str
    name: str
    source: str
    document: Any
    size: int = 0
    baseline_problems: list[Any] = field(default_factory=list)
    tracking: dict[str, Any] | None = None
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    render_cache: LRU = field(default_factory=lambda: LRU(16), repr=False)
    check_cache: LRU = field(default_factory=lambda: LRU(16), repr=False)
    log: list[CallRecord] = field(default_factory=list, repr=False)
    #: Idempotence keys of creating calls: ``(tool, key) -> result data``.
    keys: dict[tuple[str, str], Any] = field(default_factory=dict, repr=False)
    #: Refs: names a creating call gave its objects, ``{name: address}``.  ``$name`` in a
    #: target argument of a later call is replaced by the address.
    refs: dict[str, str] = field(default_factory=dict, repr=False)
    #: The refs as they were at each version: undo and redo bring them back with the
    #: content, so a ref never outlives what it names (ids are reused once freed).
    ref_states: dict[int, dict[str, str]] = field(default_factory=dict, repr=False)
    _own_version: int = field(default=0, repr=False)

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
    def batch(self) -> Iterator[None]:
        """One undo step around a call: the document's ``batch()``, or its history's."""
        batch = getattr(self.document, "batch", None)
        if batch is None and self.history is not None:
            batch = getattr(self.history, "batch", None)
        if batch is None:
            yield
            return
        with batch():
            yield

    def keep_refs(self, defined: dict[str, str]) -> None:
        """Add refs a call defined, and remember the set as this version's."""
        self.refs.update(defined)
        self.ref_states[self.version] = dict(self.refs)

    def restore_refs(self) -> None:
        """The refs of the version the document is at now (after an undo or redo)."""
        self.refs = dict(self.ref_states.get(self.version, {}))

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


class Session:
    """The documents, blobs and outputs of one conversation."""

    def __init__(self, formats: Iterable[DocumentFormat] = (), *, clock: Clock | None = None,
                 limits: Limits | None = None, on_output: Callable[[Output], None] | None = None,
                 session_id: str | None = None) -> None:
        self.id = session_id or uuid.uuid4().hex[:12]
        self.clock: Clock = clock or utc_now
        self.created_at = self.clock()
        self.limits = limits or Limits()
        self.on_output = on_output
        self.formats: dict[str, DocumentFormat] = {fmt.kind: fmt for fmt in formats}
        self.documents: dict[str, DocumentEntry] = {}
        self.blobs: dict[str, Blob] = {}
        self.outputs: list[Output] = []
        self.log: list[CallRecord] = []
        self.images_used = 0
        self._lock = threading.RLock()
        self._doc_numbers = itertools.count(1)
        self._blob_numbers = itertools.count(1)

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
        baseline = list(fmt.problems(document)) if fmt.problems else []
        with self._lock:
            self._room_for_document(size)
            doc_id = f"d{next(self._doc_numbers)}"
            self.documents[doc_id] = DocumentEntry(
                doc_id=doc_id, kind=kind, name=name, source=source, document=document,
                size=size, baseline_problems=baseline,
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

    def close(self, doc_id: str) -> None:
        entry = self.entry(doc_id)
        with entry.lock, self._lock:
            self.documents.pop(doc_id, None)

    @contextmanager
    def lock(self, doc_ids: Iterable[str]) -> Iterator[list[DocumentEntry]]:
        """Hold the locks of several documents, taken in :func:`doc_order`."""
        ordered = sorted(set(doc_ids), key=doc_order)
        entries = [self.entry(doc_id) for doc_id in ordered]
        with ExitStack() as stack:
            for entry in entries:
                stack.enter_context(entry.lock)
            yield entries

    def undo(self, doc_id: str, steps: int = 1) -> int:
        """Undo up to ``steps`` calls; the number undone."""
        return self._step(doc_id, steps, "undo")

    def redo(self, doc_id: str, steps: int = 1) -> int:
        return self._step(doc_id, steps, "redo")

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

    def reserve_images(self, count: int) -> None:
        """Count ``count`` images against the session's budget, or refuse with ``limit``."""
        with self._lock:
            if self.images_used + count > self.limits.image_budget:
                raise ToolError("limit", f"the session's image budget is "
                                f"{self.limits.image_budget} and {self.images_used} are used",
                                details={"remaining": self.limits.image_budget - self.images_used})
            self.images_used += count

    def now(self) -> _dt.datetime:
        """The session clock's time: every date a tool writes comes from here."""
        return self.clock()

    def describe(self) -> dict[str, Any]:
        with self._lock:
            return {"documents": [e.describe() for e in
                                  sorted(self.documents.values(), key=lambda e: doc_order(e.doc_id))],
                    "blobs": [blob.describe() for blob in self.blobs.values()]}

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
