"""The toolbox: definitions out, calls in, results back.

:class:`Toolbox` holds the tools of one or more format libraries and the formats they
describe, makes sessions, emits provider definitions, and runs calls.  A call goes:

1. the tool is looked up (an unknown name is ``invalid_arguments``, with the near names);
2. the arguments -- a dict, or the JSON string OpenAI sends -- are validated against the
   tool's schema, bounds and either/or rules included, whatever the provider's strict mode
   did (``invalid_arguments`` names the field and the allowed values);
3. the documents the call names are locked, in a fixed order;
4. the handler runs; a mutating call runs inside the document's batch, so it is one undo
   step and an exception rolls it back whole;
5. warnings the libraries raise are collected into the result, never printed; exceptions
   become error results with a code and, where there is a closed set, ``valid_options``.

:meth:`Toolbox.dispatch_many` runs one assistant turn's parallel calls: calls on the same
document serially in the order the model emitted them, calls on different documents at
the same time, results in call order.
"""

from __future__ import annotations

import difflib
import json
import logging
import threading
import time
import warnings
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from . import adapters, prompts
from .limits import LimitError, Limits
from .logs import CallRecord, digest, log_call, logger, shape
from .registry import CORE, Tool, ToolGroup, merge_tools
from .results import Image, Result, ToolError
from .schema import CallError, validate_call
from .session import Clock, DocumentEntry, DocumentFormat, Output, Session
from .worker import WorkerPool


class Call:
    """What a handler gets: the session, the documents it names, and the means to answer."""

    def __init__(self, toolbox: "Toolbox", session: Session, tool: Tool,
                 arguments: Mapping[str, Any], entries: dict[str, DocumentEntry],
                 primary: DocumentEntry | None) -> None:
        self.toolbox = toolbox
        self.session = session
        self.tool = tool
        self.arguments = arguments
        self.entries = entries
        #: The document the call edits or reads first (the first document argument).
        self.entry = primary
        self.result = Result()

    @property
    def document(self) -> Any:
        if self.entry is None:
            raise ToolError("invalid_arguments", f"{self.tool.name} names no document")
        return self.entry.document

    @property
    def limits(self) -> Limits:
        return self.session.limits

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

    def run(self, fn: Callable[..., Any], *args: Any, timeout: float | None = None,
            **kwargs: Any) -> Any:
        """Run ``fn`` in a worker process; ``timeout`` (s) defaults to the render deadline."""
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


class Toolbox:
    """The tools of one or more libraries, ready for a provider and a session."""

    def __init__(self, tools: Iterable[Tool], *, formats: Iterable[DocumentFormat] = (),
                 groups: Iterable[ToolGroup] = (), limits: Limits | None = None,
                 allow_new_problems: bool = False, workers: int = 2,
                 start_method: str = "spawn", log_arguments: bool = False,
                 error_map: Mapping[type[BaseException], Any] | None = None) -> None:
        self.tools: dict[str, Tool] = merge_tools(tools)
        self.formats: dict[str, DocumentFormat] = {fmt.kind: fmt for fmt in formats}
        self.groups: dict[str, ToolGroup] = {group.name: group for group in groups}
        self.limits = limits or Limits()
        #: The validate gate's override: the application's to set, never the model's.
        self.allow_new_problems = allow_new_problems
        self.log_arguments = log_arguments
        self._workers = workers
        self._start_method = start_method
        self._pool: WorkerPool | None = None
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
                on_output: Callable[[Output], None] | None = None) -> Session:
        return Session(self.formats.values(), clock=clock, limits=limits or self.limits,
                       on_output=on_output)

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
                    groups: str | Iterable[str] | None = None, **options: Any) -> list[dict[str, Any]]:
        """Provider-ready tool definitions: ``anthropic``, ``openai-responses`` or
        ``openai-chat``.  Options go to the adapter (``defer``, ``cache``, ``namespaces``)."""
        tools = self.select(groups)
        if provider == "openai-responses":
            options.setdefault("groups", self.groups.values())
        return adapters.definitions_for(provider, tools, **options)

    def system_prompt(self, *, extra: str | None = None) -> str:
        """The shared fragment, each format's fragment, then the application's ``extra``."""
        return prompts.system_prompt(*(fmt.prompt for fmt in self.formats.values()), extra=extra)

    def render_result(self, provider: str, result: Result, call_id: str) -> Any:
        """One result as the provider wants it (for Chat Completions: a list of messages)."""
        limit = self.limits.max_result_chars
        if provider == "anthropic":
            return adapters.anthropic_tool_result(result, call_id, limit=limit)
        if provider == "openai-responses":
            return adapters.openai_function_call_output(result, call_id, limit=limit)
        return adapters.results_for(provider, [(call_id, result)], limit=limit)

    def render_results(self, provider: str, results: Sequence[tuple[str, Result]]) -> Any:
        """One assistant turn's results, in call order, as the provider wants them."""
        return adapters.results_for(provider, results, limit=self.limits.max_result_chars)

    # -- running calls -----------------------------------------------------------------------

    @property
    def pool(self) -> WorkerPool:
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
        try:
            tool = self.tool(name)
            clean = self._arguments(tool, arguments)
            doc_ids = tool.doc_ids(clean)
            with session.lock(doc_ids) as locked:
                entries = {entry.doc_id: entry for entry in locked}
                primary = entries[doc_ids[0]] if doc_ids else None
                result = self._run(session, tool, clean, entries, primary)
        except ToolError as error:
            result = Result.failure(error)
        except Exception as exc:  # noqa: BLE001 -- the model gets an error result
            result = Result.failure(self.map_exception(exc))
        if primary is not None:
            result.doc = primary.doc_id
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
        documents = [self._documents_named(name, arguments) for name, arguments in calls]
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

    def _documents_named(self, name: str, arguments: Any) -> frozenset[str]:
        """The documents a call will lock, as far as can be told before validating it."""
        try:
            tool = self.tools[name]
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
            return frozenset(tool.doc_ids(parsed)) if isinstance(parsed, Mapping) else frozenset()
        except Exception:  # noqa: BLE001 -- the call will fail on its own, in order
            return frozenset()

    def _arguments(self, tool: Tool, arguments: Any) -> dict[str, Any]:
        if isinstance(arguments, (str, bytes)):
            try:
                arguments = json.loads(arguments or "{}")
            except ValueError as exc:
                raise ToolError("invalid_arguments", f"the arguments are not JSON: {exc}") from None
        if arguments is None:
            arguments = {}
        try:
            clean = validate_call(tool.schema, arguments)
        except CallError as exc:
            raise ToolError("invalid_arguments", exc.message, field=exc.field,
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
             entries: dict[str, DocumentEntry], primary: DocumentEntry | None) -> Result:
        kind = primary.kind if primary else None
        handler = tool.handler_for(kind)
        if handler is None:
            raise ToolError("invalid_arguments", f"{tool.name} does not work on a {kind} document",
                            field=tool.documents[0] if not callable(tool.documents) else None,
                            valid_options=[f"{e.doc_id} ({e.kind})" for e in
                                           session.documents.values()
                                           if tool.handler_for(e.kind) is not None])
        key = arguments.get("key") if tool.mutates and isinstance(arguments.get("key"), str) else None
        if key is not None and primary is not None and (tool.name, key) in primary.keys:
            replay = primary.keys[(tool.name, key)]
            return Result(summary=f"already done with key {key!r}: nothing new was made",
                          created=list(replay.created), changed=list(replay.changed),
                          data=replay.data)
        call = Call(self, session, tool, arguments, entries, primary)
        with collect_warnings() as caught:
            if tool.mutates and primary is not None:
                with primary.batch():
                    returned = handler(call, **arguments)
                primary.bump()
            else:
                returned = handler(call, **arguments)
        result = _as_result(returned, call.result)
        result.warnings.extend(caught)
        if key is not None and primary is not None and result.ok:
            primary.keys[(tool.name, key)] = result
        return result

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


def _message(exc: BaseException) -> str:
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc) or type(exc).__name__


def _as_result(returned: Any, built: Result) -> Result:
    if isinstance(returned, Result):
        returned.images = built.images + returned.images
        returned.warnings = built.warnings + returned.warnings
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
