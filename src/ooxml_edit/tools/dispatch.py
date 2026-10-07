"""The toolbox: definitions out, calls in, results back.

:class:`Toolbox` holds the tools of one or more format libraries and the formats they
describe, makes sessions, emits provider definitions, and runs calls.  A call goes:

1. the tool is looked up (an unknown name is ``invalid_arguments``, with the near names);
2. the arguments -- a dict, or the JSON string OpenAI sends -- are validated against the
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

**Checks.**  Handlers say what they touched (``call.touch(page)``); after a changing call
the dispatcher asks the document's format for the facts (``DocumentFormat.checks``) once.

**Batch.**  The shared ``batch`` tool runs other calls (*ops*) in order under every lock they
need, inside one undo step per document: all of them or none.  Each op is validated against
its own tool's schema -- ``batch`` itself is never sent strict, since its ``arguments`` are
free-form objects -- so a model's malformed op is caught by the dispatcher, not by
constrained decoding.  That is the trade-off: one round trip instead of forty, at the price
of strict decoding for the ops.  Checks run once, at the end.
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

from . import adapters, prompts
from .limits import LimitError, Limits
from .logs import CallRecord, digest, log_call, logger, shape
from .registry import CORE, Tool, ToolGroup, merge_tools
from .results import Image, Result, ToolError
from .schema import CallError, validate_call
from .session import Clock, DocumentEntry, DocumentFormat, Output, Session, doc_order
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
            raise ToolError("invalid_arguments", f"ref {name!r} must match {REF.pattern}",
                            field="ref")
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
            raise ToolError("not_found", f"no ref ${name} in {doc or self.entry.doc_id}",
                            field=field, valid_options=[f"${key}" for key in sorted(known)])
        return known[name] + rest

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


class _OpFailed(Exception):
    """An op of a batch failed: which one, and its error."""

    def __init__(self, index: int, tool: str, error: ToolError) -> None:
        super().__init__(error.message)
        self.index = index
        self.tool = tool
        self.error = error


class Toolbox:
    """The tools of one or more libraries, ready for a provider and a session."""

    def __init__(self, tools: Iterable[Tool], *, formats: Iterable[DocumentFormat] = (),
                 groups: Iterable[ToolGroup] = (), limits: Limits | None = None,
                 allow_new_problems: bool = False, workers: int = 2,
                 start_method: str = "spawn", log_arguments: bool = False,
                 error_map: Mapping[type[BaseException], Any] | None = None,
                 strict_first: Sequence[str] | None = None) -> None:
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
            doc_ids = (self._batch_documents(clean) if tool.composite
                       else tool.doc_ids(clean))
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
            if not isinstance(parsed, Mapping):
                return frozenset()
            if tool.composite:
                return frozenset(self._batch_documents(parsed))
            return frozenset(tool.doc_ids(parsed))
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
        if tool.composite:
            return self._run_batch(session, arguments, entries, primary)
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
            if changing and primary is not None:
                with primary.batch():
                    returned = handler(call, **arguments)
                primary.bump()
            else:
                returned = handler(call, **arguments)
            result = _as_result(returned, call.result)
            if changing and result.ok:
                self._keep_refs(entries, context, [primary.doc_id] if primary else [])
                if primary is not None:
                    result.checks = {**self._checks(session, primary, context), **result.checks}
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
                return value
            name, rest = match.group(1), match.group(2) or ""
            if name not in known:
                raise ToolError("not_found", f"no ref ${name} in {doc}", field=where,
                                valid_options=[f"${key}" for key in sorted(known)])
            return known[name] + rest

        def walk(node: Any, parts: list[str], where: str) -> Any:
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
                    node[name] = [walk(item, rest, f"{path}[{i}]")
                                  for i, item in enumerate(node[name])]
                else:
                    node[name] = [resolve(item, f"{path}[{i}]")
                                  for i, item in enumerate(node[name])]
            else:
                node[name] = walk(node[name], rest, path)
            return node

        for path in tool.refs:
            arguments = walk(arguments, path.split("."), "")
        return arguments

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

    def _ops(self, arguments: Mapping[str, Any]) -> list[tuple[int, Tool, dict[str, Any]]]:
        """A batch's ops, each validated against its own tool: before anything runs."""
        prepared = []
        for index, op in enumerate(arguments.get("ops") or []):
            name = op.get("tool", "")
            where = f"ops[{index}]"
            if name not in self.tools:
                raise ToolError("invalid_arguments", f"{where}: no tool {name!r}",
                                field=f"{where}.tool",
                                valid_options=difflib.get_close_matches(name, self.tools, 5, 0.5)
                                or sorted(t.name for t in self.tools.values() if t.batchable))
            tool = self.tools[name]
            if not tool.batchable or tool.composite:
                raise ToolError("invalid_arguments", f"{where}: {name} cannot run inside a "
                                "batch; call it on its own", field=f"{where}.tool")
            try:
                clean = self._arguments(tool, op.get("arguments") or {})
            except ToolError as error:
                inner = f"{where}.arguments" + (f".{error.field}" if error.field else "")
                raise ToolError(error.code, f"{where} ({name}): {error.message}", field=inner,
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
                   entries: dict[str, DocumentEntry], primary: DocumentEntry | None) -> Result:
        ops = arguments.get("ops") or []
        limits = session.limits
        if len(ops) > limits.max_batch_ops:
            raise ToolError("limit", f"{len(ops)} ops; at most {limits.max_batch_ops} per batch",
                            field="ops")
        prepared = self._ops(arguments)
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
                        if time.perf_counter() - started > limits.batch_timeout:
                            raise _OpFailed(index, tool.name, ToolError(
                                "timeout", f"the batch passed its {limits.batch_timeout:g} s "
                                "deadline"))
                        done.append(self._run_op(session, index, tool, clean, entries, context,
                                                 combined))
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
        combined.warnings.extend(caught)
        if len(checks) == 1:
            combined.checks = next(iter(checks.values()))
        elif checks:
            combined.checks = checks
        combined.summary = f"Ran {len(done)} op(s) as one step: " + "; ".join(
            op["summary"] for op in done[:5] if op.get("summary")) + (
            f"; and {len(done) - 5} more" if len(done) > 5 else "")
        combined.data = {"ops": done}
        return combined

    def _run_op(self, session: Session, index: int, tool: Tool, arguments: dict[str, Any],
                entries: dict[str, DocumentEntry], context: CallContext,
                combined: Result) -> dict[str, Any]:
        doc_ids = tool.doc_ids(arguments)
        primary = entries.get(doc_ids[0]) if doc_ids else None
        try:
            handler = self._handler(session, tool, arguments, primary)
            arguments = self._with_refs(tool, arguments, entries, primary, context)
            call = Call(self, session, tool, arguments,
                        {doc: entries[doc] for doc in doc_ids if doc in entries}, primary,
                        context)
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


def _message(exc: BaseException) -> str:
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc) or type(exc).__name__


def _as_result(returned: Any, built: Result) -> Result:
    if isinstance(returned, Result):
        returned.images = built.images + returned.images
        returned.warnings = built.warnings + returned.warnings
        returned.refs = {**built.refs, **returned.refs}
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
