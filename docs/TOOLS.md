# `ooxml_edit.tools`

The shared plumbing of an agent tool layer: an application imports a format library's
tools, and a model edits documents only through them -- no code execution, no raw XML, no
file system. Format-neutral and held to the core's rules by `tests/test_neutrality.py`;
standard library only, and no provider SDK. The plan it implements is
[TOOLS-ROADMAP.md](TOOLS-ROADMAP.md) (phase T0).

| Module | What it is |
| --- | --- |
| `tools.registry` | `Tool`, `ToolGroup`, the `@tool` decorator, and the parameter helpers (`string`, `integer`, `number`, `boolean`, `array`, `obj`) that build each tool's schema; a definition outside the common strict subset fails when the tool is made |
| `tools.schema` | the common strict subset both providers accept, its checker, and the call validator, which also enforces what the subset cannot say (numeric bounds, lengths, "exactly one of") |
| `tools.adapters` | definitions for the Anthropic Messages API (strict on as many tools as the per-request limits allow, writing tools first; optional deferred loading and caching), the OpenAI Responses API (all required, optional ones nullable; optional namespaces and tool search) and Chat Completions; results as `tool_result` blocks, `function_call_output` items (images inside them, or in a user message after them), or tool messages plus a user message with the images; checkers for each provider's documented rules. Plain dicts |
| `tools.results` | the result envelope, error codes with `valid_options`, image token estimates, truncation and paging |
| `tools.session` | `Session`: documents opened from bytes (`d1`), inputs registered as blobs (`b1`), outputs handed to the application as bytes; a re-entrant lock per document, taken in a fixed order across documents; versions (`History.version`) and version-keyed caches; an injected clock |
| `tools.datasets` | `Dataset` (columns, rows, opaque sources per value) and `DatasetEntry` (its id, source document and version, `stale`): what a call keeps for the session under `d:<id>` (below) |
| `tools.sources` | `Rows` (a source's `Dataset`, where it came from, `stale`) and the helpers that qualify, resolve and report sources: rows a tool takes from a dataset or another open document's table (below) |
| `tools.limits` | size limits, magic-byte checks, image sizes read from headers, and a zip-bomb guard |
| `tools.worker` | worker processes for rendering and layout whose deadlines are kept: a worker past its deadline is killed and the call reports `timeout`; multiprocessing workers by default, subprocess workers where multiprocessing may not start children, in-process on request (below) |
| `tools.logs`, `tools.prompts` | call records with argument digests, not content; the shared system-prompt fragment (mechanics only, no house style) |
| `tools.shared` | the one definition of each tool every format shares (open, new, save, close, undo, find, replace, render, check, charts, SmartArt, properties, reading an input's text) and the generic `batch`; a library adds its handler with `@shared.handler("render", kind=...)`, and lists `shared.SESSION_TOOLS`, whose handlers serve every kind. The one module that names formats, in its descriptions only |

```python
from ooxml_edit.tools import Toolbox

toolbox = Toolbox(LIBRARY_TOOLS, formats=[LIBRARY_FORMAT])   # from a format library
session = toolbox.session(clock=fixed_clock)
d1 = session.open(document_bytes, name="report.pptx")        # "d1"
b1 = session.add_blob(logo_bytes, name="logo.png")             # "b1"

tools = toolbox.definitions("anthropic")                       # or "openai-responses"
results = toolbox.dispatch_many(session, calls)                # [(name, arguments), ...]
message = toolbox.render_results("anthropic", list(zip(ids, results)))

for out in session.take_outputs():                             # files a tool saved
    store(out.name, out.data)
```

Calls on one document run one at a time in the order the model emitted them; calls on
different documents run concurrently. A mutating call is one undo step, and a failed one
changes nothing. Saving refuses new validation problems unless the application (never the
model) passes `allow_new_problems=True`.

**Refs and batches.** A creating call may name what it makes (`"ref": "step1"`); a later
call writes `$step1` wherever its tool takes a target, and the dispatcher puts the address
in. The `batch` tool runs many calls in one round trip -- in order, one undo step per
document, the facts (`checks`) computed once at the end. Each op applies on its own: one
that fails (an unknown tool, arguments its schema refuses, an address or document that
names nothing, an error while it runs) is rolled back alone and reported, and the ops after
it still run. What applied is one undo step and one version. The result's `data` is
`{"ops": [...]}` when every op applied, as before, and `{"applied": n, "failed": [indexes],
"ops": [...]}` when one failed: an outcome per op in order -- `op`,
`tool`, and its `summary`, `created` and `data`, or its `error` (`code`, `message`, and
`field` -- `tool` or `arguments.<path>` -- `valid_options` and `details` when it has them);
the summary names the failed ops to fix and resend. A `$ref` a failed op would have made
fails naming that op (`details.op`). Only when no op applied is the result an error, whose
`details` hold the same outcomes (`field` and `valid_options` are the first failure's).
`atomic: true` makes it all or none instead: every op is validated before any runs, and the
first failing op rolls the batch back and is named, with its valid options. The rollback of
one op is `DocumentEntry.savepoint()` -- the document's `savepoint()`, else its history's
(`History.savepoint`) -- after which the format's `restored` hook is called; a document
with neither keeps what a failing op did. Its ops' arguments are free-form objects, so
`batch` is the one tool never sent strict: each op is validated by the dispatcher against
its own tool's schema instead of by constrained decoding.

**Aliases and refusals.** What a model naturally writes for an argument or a value is read
as the tool's own before validation, never sent, so it costs no tokens: a string's
`aliases` (`string(enum=[...], aliases={"shrink": "normal"})`, the validation schema's
`valueAliases`, matched ignoring case) and a tool's `aliases` for argument names
(`{"from_columns": "columns"}`, or a function of the arguments where the alias means the
name only in some calls). Each one read adds a line to the result's `warnings`
(`frame.autofit 'shrink' read as 'normal'`, `slide read as target`; in a batch,
`ops[i]: ...`); an alias given together with its name is refused. Only where the meaning is
the same. A refusal says what would do: an enum value or an argument name close to an
allowed one adds "did you mean ...?" (`valid_options` still lists them all), a `key` on a
changing tool without one says that keys are for creating tools and this one is safe to
repeat, and a ref name that is not one says the rule in words with a name that would do
(`catChart`: `'cat_chart'`); `$Name` for a known ref, capitalised, says which it means.

**Datasets.** A call that derives figures from a document -- counts, sums, groups -- may
keep them for the session as a `Dataset`: columns, rows, and beside every value its
sources, opaque strings (cell addresses, say) that the tool layer stores and hands on but
never reads. Any later call, on any document of any format, names it `d:<id>` wherever its
tool declares a dataset argument (`Tool.datasets`, paths written as for `Tool.refs`). The
dispatcher checks the id first -- an unknown one is `not_found` with the known ids as
`valid_options` -- and leaves the value as written, so `d:by_region[total]` or
`d:by_region/North/total` reach the handler whole, which reads the entry with
`call.dataset(value)`:

```python
from ooxml_edit.tools import Dataset, Result, string, tool

@tool("lib_derive", "...", {"doc": string("..."), "id": string("...")})
def derive(call, doc, id):
    figures = compute(call.document)          # the library's own work
    address = call.define_dataset(id, Dataset(
        ("region", "total"), [("North", 3.68), ("South", 0.13)],
        sources=[[("A2:A3",), ("C2:C3",)], [("A4",), ("C4",)]], keys=1))
    return Result(summary=f"Derived {address}")   # the result lists "datasets": ["d:..."]

@tool("lib_table", "...", {"doc": string("..."), "from": string("A dataset, d:<id>.")},
      mutates=True, datasets=("from",))
def table(call, doc, **arguments):
    entry = call.dataset(arguments["from"])   # entry.dataset, entry.stale, entry.doc
    ...
```

A dataset made in Python -- figures an application computed itself -- comes from records,
its columns labelled where a value alone does not say its type:

```python
from datetime import date
from ooxml_edit.tools import Dataset, DatasetEntry

data = Dataset.from_records(
    [{"region": "North", "opened": date(2026, 3, 1), "total": 3.68},
     {"region": "South", "opened": "2026-04-15", "total": 0.13}],
    types={"opened": "date"})                 # or a label per column: (None, "date", None)
session.keep_datasets([DatasetEntry("openings", data)])     # d:openings, no source document
```

`rows` are mappings (a missing name is a blank) or sequences with `columns`; `columns` picks
and orders them (a name no record has is refused). The type labels (`TYPE_LABELS`) are
`number`, `percent` (a fraction: 0.25 for 25%), `date`, `time`, `datetime`, `text`, `bool`
and `error`. A Python `date`, `datetime` or `time` needs no label: a format library reads it
as one, and a tool that places it in a table or as a category writes it in the session's
locale's day order (`3/1/2026` in en-US, `1/3/2026` in nl-BE, ISO 8601 for `ymd`; a time
on the 24-hour clock, `14:05`) -- the library's own writing, not Office's short-date
format, which is not measured. ISO 8601 text (`"2026-04-15"`) stays text until its column
is labelled `date` (`datetime`, `time`): a column of codes that look like dates is never
read as dates on its own, and a placed ISO text is written as it is.

A dataset is session state, not document content: it outlives `close_document`, undo never
removes it, and defining one is not an undo step (a reading call may define one; a failed
call or batch defines none; an existing id is replaced). It records its source document and
that document's version as the defining call left it, and is never refreshed: once the
source changes it is `stale`, and a call naming it gets a warning. Stale is a comparison of
versions while the source is open -- and a history gives equal versions to equal content
only -- so:

| What happens to the source | The dataset |
| --- | --- |
| a change | stale |
| undo back to the version it was derived from | current again (nothing recomputed: the source is in that state again) |
| redo of the change | stale again |
| undo to before it was derived | stale |
| a scoped undo | stale (the document is in a state that never existed: a new version) |
| a change to a document without a history | stale for good (its count only goes up) |
| closed | frozen as it was at that moment, for good |
| changed by a later op of the batch that derived it | stale for good (the state it came from had no version) |

A dataset derived from another (`define_dataset(..., source=entry)`) inherits that one's
source and version, and goes stale with it. `Limits.max_datasets` (50) bounds a session and
`Limits.max_dataset_cells` (50,000 values; sources not counted) one dataset; the
application may forget one with `session.drop_dataset(address)` (no tool does). Datasets
stay in the session and are never sent to worker processes; a handler that needs one there
passes the `Dataset`, which pickles when its `detail` does.

**Sources: charts from rows.** A tool that places values may take them from a *source*
instead of literal arrays (`Tool.sources`, paths as for `Tool.refs`): a dataset, or an
address in another open document that the document's format turns into rows -- a table
`t:sales`, or `d2/t:sales` naming the document when several could. The dispatcher qualifies
such an address with its document (`t:sales` -> `d2/t:sales`; with several candidates it is
`ambiguous`, the qualified forms its `valid_options`) and holds that document's lock for the
call, for reading only: no undo step, no version, no checks, even inside a `batch`. The
handler reads either with `call.source(value)`, a `sources.Rows`: the `Dataset`, the source
as resolved, its document and version, and `stale` for a dataset.

A format makes its addresses sources with two fields of its `DocumentFormat` (the resolver
hook; nothing else is needed):

```python
def table_rows(document, address):              # "t:sales"
    table = document.table(address[2:])         # the library's own lookup; ToolError not_found
    return Dataset(table.columns, table.rows,    # each value's sources, the format's addresses
                   sources=[[(cell.address,) for cell in row] for row in table.cells])

FORMAT = DocumentFormat(..., datasets={"t:": table_rows},
                        merge_sources=merge_into_ranges)  # ["A2", "A3"] -> ["A2:A3"]; optional
```

`merge_sources` writes a run of the format's addresses compactly for a result (a dataset's
sources are merged by its source document's format); without it they are listed as they
are, each once. A resolved address is held to `Limits.max_dataset_cells`. A format may
also give `source_hint(document, value) -> str | None`: when a source names nothing -- a
`d:3` that is no dataset, a `t:` no open document takes -- the dispatcher asks the format of
the call's document (its first document argument) and appends what it says to the
`not_found` message, in a `batch` too (a library whose drawings are `d:<digits>` can say
`d:3` is one, not a dataset); it only adds wording, never changes what resolves, and a hint
that raises is ignored.

The shared `edit_chart` takes a source for `add`, `set_values` and `add_series`:
`from` names it and `columns` its columns by name -- the category column (a scatter chart's
x values), then a column per series (`add`) or the one column of values (`set_values`,
`add_series`, whose rows are matched to the chart's categories by label, a number by value:
every category needs exactly one row and every row a category, or the call is refused).
Without `columns`, as the table tools without `from_columns`, `add` takes every column,
the first as the categories, and `set_values`/`add_series` take a two-column source; a
defaulted column that is not all numbers is refused, naming the cell and suggesting
`columns`:

```json
{"doc": "d1", "target": "256", "action": "add", "chart_type": "column",
 "box": {"x": 40, "y": 90, "w": 600, "h": 340},
 "from": "d:by_region", "columns": ["region", "total", "count"]}
```

The kind's handler is wrapped once for every library (`Spec(wrap=...)`): it gets literal
`categories`, `data` and `values` as if the model had written them and never sees `from`.
Series values must be numbers: text, a logical value or a blank is refused with the cell's
sources in the message (never its text), and nothing is converted. Category labels are text
as given, or a number written as the General format does in the session's locale (`2,5` in
nl-NL; `2024` without a thousands separator). Columns match exactly, else case aside. A
bubble chart's sizes are not taken from a source. The result's `from` says where every
placed value came from, the column, the rows placed (from 1, in the order placed, runs as
`1-4`) and their sources, merged:

```json
"from": {"source": "d:by_region", "doc": "d2", "version": 4,
         "categories": {"column": "region", "rows": "1-3", "sources": ["Sales!A2:A9"]},
         "series": [{"name": "total", "column": "total", "rows": "1-3",
                     "sources": ["Sales!C2:C4", "Sales!C6:C9"]}]}
```

The sources are also kept in the saved document: see the next paragraphs.

**Source addresses in the saved file.** What a result's `from` says is also written into the
document, so it can be read back after a save, in another session, or after the file has
been through PowerPoint or Word. What is kept is a *record* (`ooxml_edit.provenance`): the
result's `from` without what only the session means -- the source document's handle (`d2`)
-- with the source written without it (`t:sales`, not `d2/t:sales`; a dataset as `d:<id>`)
and the source document's name added. It is compact JSON, versioned with `"v": 1`; a key
whose value is `null` is left out:

```json
{"v":1,"source":"t:sales","document":"made-up-budget.xlsx","version":4,
 "categories":{"column":"region","rows":"1-3","sources":["Sales!A2:A4"]},
 "series":[{"name":"total","column":"total","rows":"1-3","sources":["Sales!C2:C4"]}]}
```

A table tool's record has its result's `from` the same way: `first_row`, `header_row` and
`columns` (`col`, `column`, `rows`, `sources`). `provenance.record(placed, document=name)`
builds one, `provenance.dumps(record)` writes it, and `provenance.loads(text)` reads it,
refusing what is not a record (`RecordError`: not a JSON object, or no whole-number `v` of
1 or more); a record of a later version is returned as it is.

Where it is kept:

- **In the chart part**, by this package, for every chart placed with `edit_chart` and
  `from`: an own extension at the end of the chart space's extension list,
  `c:chartSpace/c:extLst/c:ext uri="urn:uvrt:ooxml-edit:provenance:v1"`, holding
  `<uvp:record json="..."/>` in the namespace `urn:uvrt:ooxml-edit:provenance:v1`. It goes
  wherever the chart goes. PowerPoint and Word for Mac both kept an own `c:ext` there
  through open, edit and save (measured 2026-10-14; they re-serialise the part around it).
  Office's own extensions keep their order, and a record already there is replaced where
  it stands. The chart's record is merged, never left saying what is no longer true:
  - `add` from a source writes a new record;
  - `set_values` from a source replaces the categories' entry and that series' entry, and
    `add_series` from a source adds or replaces that series' entry (by name); the other
    series' entries stay. An entry whose source is not the record's names its own
    (`source`, `document`, `version`, `stale`);
  - without `from`: `rename_series` renames the series' entry; `remove_series` drops it;
    `set_values` and `set_value` drop it (its values no longer come from the source);
    `rename_category` drops the categories' entry; `add_category` and `remove_category`
    drop the record (every series' rows changed). A record left with neither categories
    nor series is dropped. A stale address is worse than none.

  The format's copy gets the same merged record, or `None` when the record is dropped.
- **By the format**, for a table or a chart: `DocumentFormat.store_provenance(document,
  address, record)` -- `record` `None` drops the address' record -- called inside the
  call's undo step after the placement (or the edit) succeeded.
  The shared `edit_chart` calls it with the chart's address -- for `add`, the last of the
  result's `created`; otherwise the first of its `changed`, else `target`. A format's own
  table tool calls it through `call.keep_provenance(address, rows, placed)` (`rows` what
  `call.source` returned, `placed` the result's `from`), which builds the record, hands it
  to the hook and returns it; `call.store_provenance(address, record_or_None)` hands a
  record (or a drop) to the hook as it is. Measured (2026-10-14), PowerPoint keeps a shape's tags part
  (`p:nvPr/p:custDataLst/p:tags r:id`, a `ppt/tags/tagN.xml` part) and Word a customXml item
  related from the document part, keyed by a table's first-row `w14:paraId` or a chart's
  `wp:docPr/@id`; alt text, and a PowerPoint `p:custData` item the presentation part does
  not relate (PowerPoint asks to repair the file), are to be avoided.

Reading it back:

- `edit_chart` with `action: "read"` returns the chart part's record as `from`, when there
  is one -- no tool and no parameter added, so the definitions cost no more tokens.
- `ooxml_edit.charts.provenance(chart)` -> the record or `None`;
  `provenance(package, part)` the same for a chart part; `provenance(package)` (or a
  document with a `package`) -> `{chart part: record}` for every chart part that has one.
- `session.provenance(doc)` -> `{address: record}`, from the format's
  `DocumentFormat.read_provenance(document)`, under the addresses its tools take now; `{}`
  for a format without the hook.

```python
def store(document, address, record):          # inside the call's undo step
    text = provenance.dumps(record)             # the format's own place, e.g. a tag's value
    ...

def read(document):
    return {address: provenance.loads(text) for address, text in ...}

FORMAT = DocumentFormat(..., store_provenance=store, read_provenance=read)

# in a table tool, after the cells are written:
data["from"] = placed                           # as today
call.keep_provenance(frame.id, rows, placed)
```

How the shared `edit_chart` finds the chart part without knowing the format: every
`ooxml_edit.charts.Chart` notes its part, and `add_chart` the part it makes, as a
`provenance.Holder` while a placement watches (`provenance.watch()`); the wrapper writes
the record into the one chart the handler used, and reads it from there for `read`.

**Loading tools and the prompt.** Every tool is in one group; the `core` group is always
loaded, the others on demand. What `definitions()` sends by default:

| Call | Sends |
| --- | --- |
| `definitions("anthropic")` | every tool; the non-core ones `defer_loading`, with the BM25 tool-search tool, and `cache_control` on the last core tool. For models with tool search (Sonnet 5.5, Opus 5.5, Haiku 4.5) |
| `definitions("anthropic", groups=["…"])` | the core and the named groups, all loaded: for a model without tool search, or an application that knows the task |
| `definitions("openai-responses")` | the core as functions, each other group a `namespace` with `defer_loading`, and `{"type": "tool_search"}` |
| `definitions("openai-chat", groups=["…"])` | the core and the named groups (Chat Completions has no tool search); `toolbox.allowed_tools([...], provider=...)` narrows a turn's calls without changing the cached `tools` |

`defer=` overrides the default either way. On Claude, `strict: true` goes to as many tools
as the per-request limits allow (20 tools, 24 optional parameters), in the order each
format lists in `DocumentFormat.strict_first` (or `Toolbox(strict_first=...)`), then
writing tools before reading ones. `toolbox.system_prompt(extra=...)` gives the shared
fragment (`tools.prompts.SYSTEM`: planning, addresses and refs, batching, results and
checks, rendering, saving, units), each format's fragment, then the application's own
guidance: house style and design rules belong there, never in the shipped fragments.

**Where the work runs: three runners.** Rendering and layout run in worker processes so
that a deadline can be kept by killing one, and so that work holding the GIL -- resvg-py's
native rasterise holds it for all of its 0.7 s on a slide -- does not stop the threads
serving other calls. There are three runners, all with `run(fn, *args, timeout=...,
**kwargs)` and `close()`:

| Runner | Used | A deadline |
| --- | --- | --- |
| `WorkerPool(n)`: `n` multiprocessing workers (`spawn`) | the default (`Toolbox(workers=2)`) | kills the worker and replaces it; the call reports `timeout` |
| `SubprocessPool(n)`: `n` workers started with `subprocess` | by itself, in a daemonic process (below); or `Toolbox(runner=SubprocessPool(n))` anywhere | the same: the worker is killed and replaced |
| `InProcess(n)`: threads of this process | `Toolbox(workers=0)`, `runner=InProcess(n)`, `WorkerPool(fallback="in-process")`, or where no subprocess can be started either | bounds the *wait* only: the call reports `timeout`, the task runs on to its end in the background, keeping its slot |

A daemonic process may not start multiprocessing children -- a Celery prefork worker's child
is one, and `Process.start` fails there with "daemonic processes are not allowed to have
children" -- but it may start subprocesses. The pool notices
(`multiprocessing.current_process().daemon`, or that failure on the first start), logs a
warning once and hands the work to a `SubprocessPool` of the same size; `pool.runner` says
which runs it (`"processes"`, `"subprocesses"`, `"in-process"`). `WorkerPool(fallback=...)`
chooses: `True` (the default) or `"subprocess"`, `"in-process"` (the behaviour before
0.17), or `False` (no fallback: the start fails as multiprocessing makes it fail). Measured
in a daemonic process with work that holds the GIL for 1 s: two such renders at once took
1.08 s with subprocess workers and 2.08 s in-process, and an edit asked for during a render
returned in 8 ms with subprocess workers and 831 ms in-process.

A subprocess worker is `python -m ooxml_edit.tools.subworker` (the same `sys.executable`),
long-lived like a multiprocessing worker: starting one costs about 0.25 s to its first
reply, and a renderer's imports and first font scan another 0.6 s -- more than a warm render
(0.33 s on a slide), so a worker serves task after task. It speaks length-prefixed pickles
over its stdin and stdout, which it first moves to private file descriptors: what a library
prints, from Python or native code, goes to stderr (shared with the parent), and nothing
reads the protocol but the worker. The entry point is fixed and takes no code: a message
names a task by module and qualified name, and the worker runs it only if it is registered
with `@worker_task` (`ooxml_edit.tools.worker_task`; the mark is the attribute
`__ooxml_edit_task__ = True`, which a module that should not import the tool layer may set
itself) -- checked before its arguments are even unpickled. The libraries register their render and layout functions; an application's
own unregistered function given to `call.run` runs in-process instead, with a warning once.
Arguments and results follow the multiprocessing pool's rules: a module-level function,
arguments and result that pickle; an exception comes back as itself, or as a
`RuntimeError` with its text when it does not unpickle. The worker gets the parent's
environment and `sys.path` as it starts and nothing later, so everything a task needs that
may change -- the font folders above all, below -- goes in its arguments. It stops at
`Toolbox.close()` (or the pool's `close()`), at the parent interpreter's exit, and when the
parent dies: an idle worker sees its input end; a busy one notices within half a second on
POSIX (its parent pid changes) and at once on Windows (it waits on the parent's handle).

**Rendering without holding the document.** A call holds its documents' locks while its
handler runs, so calls on one document run one at a time. A render used to hold them while
it rasterised -- one to three seconds a slide -- and parallel loops on one deck waited for
each other's renders. Now `render` takes a snapshot under the lock -- the document's bytes,
its version (`History.version`), the ids and numbers it labels the images with -- lets the
lock go while the worker (or, with `workers=0` or in the in-process fallback, a thread)
rasterises those bytes, and takes the lock back to cache each image under the snapshot's
version. `check` does the same for its slow part: a deck's fit and collision facts and
validation are measured on a copy opened from the snapshot's bytes, a Word document's
reflow is laid out from them (its `validate` and `fields` are read under the lock, from
the same version). So an edit to slide 2 does not wait for a render of slide 1. The
result describes the snapshot: its `version` is the one drawn or checked, which an edit
made meanwhile has moved past; a later render or check of the new version draws afresh,
while one of a version undone back to is a cache hit. Undo and history are untouched: a
render or check makes no step. Inside a `batch` the lock stays held (a check there is one
op of the run), as in any changing call. A handler does this with `call.unlocked()` (a
context manager; it yields whether the locks were let go, `call.may_unlock` says so in
advance, and `call.version_used` is the version the result then reports) or
`call.run_unlocked(fn, *args)`, and must read nothing from the live document inside. One
limit is the rasteriser's: resvg-py holds the GIL for the whole of its native rasterise
(measured: no other thread ran for 0.7 s of a 0.8 s slide), so in-process (`workers=0`) an
edit no longer waits for the lock but still waits for that call to return; with worker
processes, multiprocessing or subprocess, it waits for neither.

**Slides by id.** `render`'s and `check`'s `slides` take what every other deck address
takes: a slide number from 1, the slide's id (`s:257`), or a `$ref` to one -- so a loop
that read a slide's id renders that slide even after another loop added or moved slides.
To the provider the items are strings (the subset has no unions; a strict decoder writes a
number as `"2"`, read as a position); the dispatcher still takes a number
(`string(or_integer=1)`, the validation-only keyword `orInteger`). Each image is labelled
with both, `slide 3 (s:257)`, and `check` lists the slides it covered by id. Anything else
-- a shape's address, `256.2` -- is refused (`invalid_arguments` on `slides[0]`, listing the
ids), as is an id the deck does not have (`not_found`). A document's `pages` stay numbers:
`"1"` is refused by the dispatcher, `pages[0] must be a number` (`invalid_arguments`); its
`slides`, read as pages, take numbers (or digit strings) only, and an id there is refused
naming `pages`. The core grew by 12 estimated tokens per format (deck 3,398, about 5,029
counted by the 1.48 proxy, within the 5,500 budget; Word 2,429, about 3,595).

**Fonts.** An application that keeps licensed faces in a folder of its own names it once:
`Toolbox(..., font_dirs=["/srv/app/fonts"])`, the default of every session the toolbox
makes, or `toolbox.session(font_dirs=[...])` for one session. The folders are searched
before the operating system's, for every render and every measurement of text -- `render`,
`check`, the facts `save_document` and each changing call return. It is application
configuration: no tool takes it and no definition or prompt mentions it, so the token
budget is unchanged. `None` (the default) leaves the renderers' own default, which reads
`OOXML_FONT_DIRS` (`os.pathsep`-separated) and then the system's folders; `[]` means no
folders of the application's, the variable not read either. The session keeps it as
`session.font_dirs` (path strings), copies it to each document's entry
(`entry.font_dirs`), and offers it to handlers as `call.font_dirs`: a format passes it to
the work it runs in a worker process -- which does not see the session, nor an environment
variable set after the pool started -- and keys its render and check caches with it. A
format applies it to a document as the document joins the session
(`DocumentFormat.configure(document, session)`, called for opened, created and adopted
documents alike). The renderers walk the folders once per process and check them with one
stat per folder per measurement (ooxml-common 0.10, `refresh_font_dirs()` for what a
folder's time does not show): a slow mount no longer costs a walk per measurement.

**Locale.** The regional settings documents are drawn and edited in -- decimal and
thousands separators, date order, language, list separator -- are named the same way:
`Toolbox(..., locale=Locale(...))` (an `ooxml_common.locales.Locale`, per user or per
client), the default of every session the toolbox makes, or `toolbox.session(locale=...)`
for one session. Not given (`None`), it is en-US, always -- never the operating system's,
so a server draws the same file the same way wherever it runs. Like `font_dirs` it is
application configuration: no tool takes it, no definition or prompt mentions it, the
token budget is unchanged. The session keeps it as `session.locale`, copies it to each
document's entry (`entry.locale`), gives it to `DocumentFormat.configure(document,
session)`, and offers it to handlers as `call.locale`, which a format passes to the work
it runs in a worker process -- a renderer's `ConvertOptions(locale=call.locale)` -- and
keys its caches with. ooxml-edit stays lxml-only: it imports nothing of ooxml-common, keeps
the object it is given (a `Locale` is frozen and pickles into a worker), and checks only
that it has the five fields as strings (`tools.locale_of`, `tools.LOCALE_FIELDS`;
`TypeError` otherwise -- a bare language tag is not a locale). The default is
`tools.DEFAULT_LOCALE`, an object with `ooxml_common.locales.EN_US`'s values and the part
of its interface a renderer reads (`primary_language`, `number_text`). A new chart's
language falls back to the locale's (`add_chart(..., locale=call.locale)`) when the
format names none; with the default the chart is byte-identical to before.

**OpenAI images.** On the Responses API a result's images go inside its
`function_call_output` by default: `output` is then a list of `input_text` and
`input_image`, which OpenAI's function-calling guide documents ("For functions that return
images or files, you can pass an array of image or file objects instead of a string").
`render_results("openai-responses", results, images="message")` keeps every output a string
-- each saying how many images follow -- and adds one user message after them with every
image, labelled with its call, as Chat Completions must; use it where a deployment takes
images only in messages (Azure OpenAI's Responses documentation shows string outputs only
and says nothing about images in them). `detail=` sets each `input_image`'s detail:
`auto` (the default, as in the API), `low`, `high`, or `original` where the model has it.
`adapters.openai_input_problems(items)` checks the items' shapes offline; it takes the
whole next input, letting the model's own items (`reasoning`, `function_call`,
`tool_search_call`, `tool_search_output`) pass.

**Responses status.** A production user ran both agent libraries live on Azure OpenAI's
Responses API (October 2026: the v1 endpoint through the plain OpenAI client, the default
`definitions("openai-responses")`, `store=False` with
`include=["reasoning.encrypted_content"]`, `images="output"` and `images="message"`), and
every task completed. What they reported is in an offline round-trip test
(`tests/test_tools_responses_roundtrip.py`, synthetic fixtures, no network): a deferred
group's `function_call` carries the bare tool `name` plus a separate `namespace`, and
`dispatch` takes the bare name; hosted tool search returns `tool_search_call`
(`execution: "server"`) and `tool_search_output`, which go back in the next input
unchanged and are never dispatched; reasoning items keep their `encrypted_content`. The
documented loop -- `items += response.output`, dispatch only `function_call` items, then
`render_results` -- needs nothing more. This repository's own online tests still run on
Claude only.

**Checking definitions before the first call.** One definition outside OpenAI's strict
subset makes the API refuse the whole request (a 400, whichever tool the model wanted), so
an application that adds tools of its own checks the full list once, before it sends any:

```python
from ooxml_edit.tools import strict_problems

tools = toolbox.definitions("openai-responses") + MY_TOOLS   # ours and theirs, one list
for problem in strict_problems(tools):
    print(problem)    # $[41].parameters (lookup): 'anyOf' at the root; the root must be a plain object
```

`strict_problems(definitions)` takes `Tool` objects (checked as the Responses adapter sends
them), Responses items (`function`, `namespace`, `tool_search`) and Chat Completions
definitions, mixed. Each `StrictProblem` has the `tool`'s name, a JSON `path` into the list
given and a `message`. It reports a root that is not a plain object (`anyOf`, `oneOf`,
`allOf`, `enum` or `not` there); an object with a property missing from `required` or with
`additionalProperties` missing or not `false`; keywords strict mode does not take (`allOf`,
`oneOf`, `not`, `if`/`then`/`else`, `dependent*`, `patternProperties`, `minLength`,
`maxLength`, `unevaluatedProperties`) and formats it does not list; a nullable enum without
`null`; `strict` not stated; a name OpenAI refuses; and more than 5000 properties, 10
levels or 1000 enum values in a tool. `anyOf` branches and `$defs` are checked too; a
function sent `strict: false` (`batch`) is held to its name and an object root only. The
rules are the documented ones, checked offline: a deployment may still refuse something
they allow. `adapters.openai_problems(definitions, chat=...)` is the same check with
plain-text results, which the tests hold every adapter's output to.

**Undo with several writers.** `undo` steps back through one history per document: the
latest change, whoever made it. An application that runs a loop per slide on one deck,
in parallel, can have one loop's undo revert another's edit. Three ways out:

- **One session per unit.** Give each loop its own session (and its own copy of the
  document), and merge the results; each undo is then its loop's own.
- **No undo.** Leave `undo` out of the loops' tools (send a subset: `toolbox.select` or
  `definitions(groups=...)` with your own tool list); a loop fixes its mistakes with the
  editing tools.
- **Scoped undo.** `undo` with `scope` (decks: a slide id, `256` or `s:256`) undoes the
  latest change that touched that slide and what it owns -- its relationships, notes,
  charts and their workbooks, diagrams, media -- wherever it is in the history, and leaves
  later changes elsewhere in place. It is refused with `entangled` (nothing changes) when
  that change touched a part a later change outside the scope touched too: two loops that
  each added a slide share the presentation part, and two that each added the first part of
  a kind share `[Content_Types].xml`. A scoped undo is not itself a step: `redo` with the
  same scope brings it back, while the slide's parts are as the undo left them. A format
  without scopes refuses `scope` (`invalid_arguments`): Word documents undo document-wide,
  since every edit changes the one body part and a scope would always be entangled. The
  parameter costs about 34 offline tokens in the core (deck core 3,386 estimated, about
  5,011 counted by the 1.48 proxy, within the 5,500 budget; Word core 2,417, about 3,577).

**The image budget.** Every image a tool returns counts against `Limits.image_budget`, 40
per session by default (a 1280 px slide is about 1,200 tokens; 40 is about 48,000 of
context), and at most `max_images_per_call` (4) come back from one call. A long
conversation of several rounds can use it up. `Limits(image_budget_per_round=N)` adds a
budget that starts again whenever the application calls `session.new_round()` (at each
user message, say); with it, `image_budget` can be raised or set to `None` (no session cap)
while each round stays bounded. `session.images_remaining()` says what is left, and the
`limit` error names the budget (`details.budget`: `session` or `round`). The default stays
40 per session and no per-round cap: no measurement says what a better number is, and what
a round is (a user message, a task) only the application knows.
