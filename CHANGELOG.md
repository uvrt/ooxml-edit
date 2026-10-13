# Changelog

Versions are those in `pyproject.toml`; none is published to PyPI yet.

## 0.22.0 -- unreleased
- Sources (O3): the shared `edit_chart` takes its categories and series from a dataset or
  from a table of another open document, by column name, and says where every placed value
  came from.
  - `edit_chart` gains `from` (`d:<id>`, `t:<name>`, or `d2/t:<name>` naming the document)
    and `columns` (the category column -- a scatter chart's x values -- then a column per
    series for `add`, or the one column of values for `set_values` and `add_series`, whose
    rows are matched to the chart's categories by label, a number by value; every category
    needs exactly one row and every row a category). `from` is refused with the other
    actions, with `categories`, `data` or `values`, and for a bubble chart's sizes; `add_series`
    is named by its column unless `text` says otherwise.
  - Series values must be numbers: text, a logical value, a blank or a non-finite number
    is refused, naming the cell by its sources (never its text); nothing is converted.
    Category labels are text as given, or a number written as the General format does in
    the session's locale; a blank category is refused.
  - The result's `from`: the source as resolved, its document and version (`stale` for a
    stale dataset), and per category and series column the rows placed (from 1, in the
    order placed, runs as `1-3,5`) and their sources, merged. Nothing is written into the
    saved chart yet.
  - Every kind's handler is wrapped once, here (`Spec(wrap=...)`): it gets literal
    `categories`, `data` and `values` and never sees `from`, so the deck and Word libraries
    need no change.
  - The resolver hook: `DocumentFormat.datasets` (`{prefix: resolve(document, address) ->
    Dataset}`) and `DocumentFormat.merge_sources` (a run of the format's addresses written
    compactly, optional). `Tool.sources` (and `@tool(sources=...)`, `Spec(sources=...)`)
    declares the arguments that take a source; the dispatcher qualifies an address with its
    document (several candidates: `ambiguous`, the qualified forms as `valid_options`; none:
    `not_found`), checks a dataset as for `Tool.datasets`, and locks the document for
    reading -- no undo step, no version, no checks, in a `batch` too -- and
    `dispatch_many` orders calls by it. `call.source(value)` returns `tools.Rows`; a
    resolved address is held to `Limits.max_dataset_cells`.
  - Definitions: `from` and `columns` add about 50 tokens to every definition, none to the
    cores (deferred `edit_chart`); `edit_chart`'s description and its `values` and `value`
    texts were shortened to keep the all-definitions guards: deck 11,844 -> 11,897
    estimated (guard 11,900), Word 11,880 -> 11,933 (guard 11,950). Cores unchanged: deck
    3,504 (about 5,186 by the 1.48 proxy), Word 2,535 (about 3,752), workbook 3,452 (about
    5,109). `strict_problems` stays empty for each.

## 0.21.0 -- unreleased
- `tools.strict_problems(definitions)`: OpenAI's documented strict-mode rules over a whole
  `tools` list -- this library's definitions and an application's own, mixed -- before the
  first call, since one bad definition makes the API refuse every request. Takes `Tool`
  objects (checked as the Responses adapter sends them), Responses items and Chat
  Completions definitions; each `StrictProblem` names the tool, a JSON path into the list
  (`$[3].parameters.properties.at`) and the problem. Exported from `ooxml_edit.tools`;
  documented in docs/TOOLS.md, "Checking definitions before the first call".
- `adapters.openai_problems` is now that check as text, and finds more: `anyOf`, `oneOf`,
  `allOf`, `enum` or `not` at the root (it found `anyOf` only), `oneOf` anywhere, problems
  inside `anyOf` branches and `$defs`, and names the properties missing from `required`
  and an `additionalProperties` that is missing. Every adapter's output still passes.

## 0.20.0 -- unreleased
- Datasets (`tools.datasets`): a session-level store of tables of values a call derived,
  named `d:<id>` by later calls on any document of any format, so no figure is retyped
  between documents. Format-neutral: the first producer is a workbook library's derive
  tool; the consumers (charts, tables) come later.
  - `Dataset(columns, rows, sources=None, keys=0, types=None, detail=None)`: an immutable
    value -- rows as tuples in column order (mappings are taken too), and per value its
    sources, opaque strings the tool layer stores and never reads; `keys` the leading
    columns that name a row; `types` a free label per column; `detail` whatever the
    producer keeps for itself. Pickles when `detail` does.
  - `call.define_dataset(id, dataset, source=...)` keeps it once the call succeeds (a
    failed call or batch defines none; a reading call may define one); an existing id is
    replaced. `source` is the call's document by default, another document, a dataset (it
    inherits that one's source and version), or `None`. The result lists it
    (`Result.datasets`, `"datasets": ["d:by_region"]`).
  - `Tool.datasets` (and `@tool(datasets=...)`, `Spec(datasets=...)`) declares the
    argument paths that may hold `d:<id>`, as `Tool.refs` declares ref targets; the
    dispatcher checks the id before the handler runs -- unknown is `not_found` with the
    known ids as `valid_options` and the argument's path as `field` -- and leaves the value
    as written: what follows the id (`[column]`, `/key/column`) is the format's to read.
    The handler gets the entry with `call.dataset(value)`.
  - `session.datasets` (by id), `session.dataset(address)`, `session.dataset_ids()`,
    `session.drop_dataset(address)` (the application's; no tool), and `datasets` in
    `session.describe()` when there are any.
  - Each `DatasetEntry` records its source document's handle, kind, name and version (the
    version as the defining call left it), outlives `close_document` (whose result lists
    the datasets that stay), and is `stale` -- never refreshed -- once its source changes.
    Stale is a version comparison while the source is open, so undoing the change makes it
    current again and redo stale again (a history gives equal versions to equal content
    only); a scoped undo leaves it stale (a new version); a document without a history only
    counts up. Closing the source freezes it as it is. A dataset derived in a `batch`
    before a later op changed its source is stale for good. A call that names a stale
    dataset gets a warning saying so.
  - `Limits.max_datasets` (50 per session) and `Limits.max_dataset_cells` (50,000 values
    per dataset; sources not counted), each a `limit` error.
  - Datasets stay in the session: nothing sends them to worker processes.
  - No shared definition takes a dataset yet, and the system prompt is unchanged: the deck
    and Word cores are byte-identical to 0.19.0's (deck 3,504 estimated, about 5,186 by the
    1.48 proxy; Word 2,535, about 3,752).
- The neutrality test also forbids "spreadsheet" and "sheet" in the core and the tool layer.

## 0.19.0 -- unreleased
- The application's locale: `Toolbox(..., locale=...)` and `toolbox.session(locale=...)`
  take an `ooxml_common.locales.Locale` -- decimal and thousands separators, date order,
  language, list separator -- per user or per client, threaded as `font_dirs` is: kept as
  `session.locale`, copied to each document's entry (`entry.locale`), given to
  `DocumentFormat.configure`, and offered to handlers as `call.locale` for the work they
  run in worker processes (multiprocessing, subprocess and in-process alike; a `Locale`
  pickles). Not given, it is en-US (`tools.DEFAULT_LOCALE`), always: never the machine's.
  - No dependency is added: ooxml-edit stays lxml-only and imports nothing of
    ooxml-common. It keeps the object it is given and checks only that it has the five
    fields as strings (`tools.locale_of`, `tools.LOCALE_FIELDS`); anything else is a
    `TypeError`. `tools.DEFAULT_LOCALE` has `ooxml_common.locales.EN_US`'s values and the
    part of its interface the renderers read (`primary_language`, `number_text`).
  - Application configuration, not a tool argument: the tool definitions and the system
    prompt are unchanged, so the token budgets are too.
- `charts.create.add_chart` and `chart_space` take `locale=`: a new chart's language, when
  the caller passes no `lang`, is `locale.language`, else en-US (`create.DEFAULT_LANG`
  marks `lang` not given; `create.locale_language`). The caller's `lang` -- the document's
  -- still wins, and `lang=None` still writes no `lang` on the text, `c:lang` now taking
  the locale's language there too. Without a locale, or with en-US, the chart is
  byte-identical to before.

## 0.18.0
- Workbooks are a shared-tool kind: `shared.KINDS` is `("pptx", "docx", "xlsx")`, so a
  workbook library registers its handlers with `shared.handler(name, kind="xlsx")`.
  - `describe` says what a workbook returns (sheets, tables and regions with key columns,
    column types and fill, sample rows, flags); `find_text` finds in a workbook's cells,
    names and comments, and its `range` takes a sheet, range or table. `replace_text`'s
    `range` is unchanged (documents only), so it costs nothing.
  - `save_document`'s `format` gains `xlsx`, `xlsm` and `xltx` (`xltx` a template);
    `new_document`'s `kind` gains `xlsx`.
  - The enums stay static, like every shared definition: one definition whichever libraries
    are loaded, so that two libraries' tools still merge. A toolbox without a workbook
    library answers `new_document` with `kind: "xlsx"` as it answers any kind it lacks
    (`invalid_arguments`, the kinds it has), and a deck's or document's `save_document`
    handler refuses a workbook format.
  - Cost to the deck and Word cores, by the siblings' offline proxy (compact JSON / 3.5):
    64 estimated tokens each (about 95 counted), all in core tools; of these about 15 (22
    counted) are the enum values and their notes, the rest the descriptions.

## 0.17.0
- Subprocess workers (`tools.SubprocessPool`): where multiprocessing may not start children
  -- a daemonic process such as a Celery prefork worker's child -- `WorkerPool` now hands the
  work to long-lived workers started with `subprocess` (`python -m
  ooxml_edit.tools.subworker`) instead of running it in-process. Renders that hold the GIL
  (resvg-py's rasterise) run in parallel again, an edit does not wait for one, and a
  deadline kills the worker and replaces it. Measured in a daemonic process with 1 s of
  GIL-holding work: two at once 1.08 s (in-process 2.08 s), an edit during a render 8 ms
  (in-process 831 ms).
  - Length-prefixed pickles over the worker's stdin and stdout, which it moves to private
    descriptors first, so a library printing to stdout cannot break the protocol (it goes to
    stderr). The worker gets the parent's `sys.path` and environment at start and nothing
    later: arguments carry the rest (font folders, as before).
  - A worker runs only functions registered with `@worker_task` (`tools.worker_task`),
    named by module and qualified name, checked before the arguments are unpickled; an
    unregistered function runs in-process, with a warning once. Exceptions come back as
    themselves, or as a `RuntimeError` with their text when they do not unpickle.
  - Workers stop at `close()`, at the parent's exit, and when the parent dies (an idle one
    sees its input end; a busy one notices within 0.5 s on POSIX, at once on Windows).
  - `WorkerPool(fallback=...)` takes `True`/`"subprocess"` (the new default behaviour),
    `"in-process"` (the behaviour before) or `False` (no fallback); `pool.runner` says what
    runs the work. `workers=0` and `InProcess` are unchanged.
  - Libraries register their worker functions with `@worker_task` (pptx-agent's and
    docx-agent's renderers do).

## 0.16.0
- Undo by scope and parts every scope shares (production feedback round 3, G5: a deck's
  slide masters and layouts, which every slide on them shows): `History.undo_in(scope,
  shared=...)` raises `Entangled` when the latest step in scope changed a shared part,
  whatever followed it, and `DocumentFormat.undo_shared(document)` names those parts, so a
  scoped `undo` refuses such a change (`entangled`, "other pages show it too") while a plain
  `undo` takes it back.
- `find_text` and `replace_text`: `stories` takes `layouts` and `masters` too ("Decks: body is
  the slides; layouts, masters, or all"), so a deck's handler can reach the text on its
  layouts and masters. An enum and its description only: no new parameter, so the strict
  set's counts are unchanged.

## 0.15.0
- Chart formatting (production feedback round 3, G1-G4), on `Chart` from the new
  `charts.styling` (with `charts.format`, `charts.actions` and `charts.errors`):
  - `set_axis_scale(axis, minimum=, maximum=, major_unit=, minor_unit=, number_format=,
    reverse=)` -- left out: kept, `None`: automatic again; `axis` is `category`/`value` or
    `x`/`y` (the horizontal and vertical axis). `reverse=True` writes `c:orientation
    val="maxMin"` and crosses the other axis at its maximum (`c:crosses val="max"`): measured
    in PowerPoint 16, `maxMin` alone moves the value axis to the top and PowerPoint rewrites
    its `axPos`. `axis_scale(axis)`, `scales`, `axis_names()` read them back.
  - `set_gridlines(axis, major=, minor=)` in Office's style (major 15%, minor 5% of the text
    colour, measured).
  - `format_series(series, fill=, line=, line_width=, dash=, marker=, marker_size=,
    marker_fill=, marker_line=)` and `format_point(series, category, ...)` (a `c:dPt` that
    starts from the series' look, kept on its point as categories move), with the colour
    specifications a deck's shapes take (`tx2`, `accent1 lumMod=60% lumOff=40%`, `1F4E79`;
    `None` none); `series_format()` reads them.
  - `set_series_type(series, "line" | "column", secondary_axis=False)`: a column series
    drawn as a line on the same axes (or on a secondary value axis, built as PowerPoint's
    "Clustered Column - Line on Secondary Axis", measured), and back; horizontal bars are
    refused (PowerPoint cannot combine them with a line).
  - `set_text_style(part=None | "title" | "axis_titles" | "axis_labels" | "data_labels" |
    "legend", font=, size=, bold=, color=)` and `text_style(part)`.
  - `set_point_labels(series, labels, position=, show_value=False, cells=False)`: one
    `c:dLbl` with its own text per point; `cells=True` writes the labels into a workbook
    column after the data and links them as PowerPoint's "Value From Cells" writes them
    (`c15:datalabelsRange`, `CELLRANGE` fields, `c15:xForSave`; measured by a PowerPoint
    round trip), kept in step when categories are added or removed. `point_labels()` reads
    them; positions are checked against what each chart type takes.
- `add_chart`: the kinds `radar_filled` and `bubble` (measured on Insert > Chart in
  PowerPoint and Word: a filled radar's series filled, no line or marker; a bubble's at 75%
  opacity with no line, `bubbleScale` 100, the workbook x | y | size), a column series'
  `"type": "line"` (Office's combination chart, measured: gap width 150, no overlap, the line
  plot on the same axes, no `c:crosses`), `category_order="reversed"`, `gridlines=False` and
  `bubble_scale`. The workbook's shared-string `count` now counts every reference.
- `edit_chart`: the actions `set_axis_scale`, `set_gridlines`, `format_series`,
  `format_point`, `set_series_type`, `set_text_style` and `set_point_labels`; the types
  `radar_filled` and `bubble`; the parameters `scale`, `gridlines`, `style`, `part`,
  `labels`, `cells` and `secondary`; label positions in `position`; `add` takes `axis` with
  `scale` and `gridlines`, and `data` a series' `type` and `size`. `read` adds `types`,
  `scales`, `series_format` and `point_labels`. The tool stays deferred and non-strict; the
  deck's and the document's every-definition estimates grow by 646 tokens, the cores not.

## 0.14.0 -- unreleased
- `render` and `check` let the document's lock go for their slow part: the handler takes a
  snapshot under the lock (bytes, version, ids), the worker rasterises or measures it with
  the lock released, and the lock is taken back to cache the result under the snapshot's
  version, which the result reports. An edit to another slide of the same deck no longer
  waits for a render (production feedback: parallel loops serialised on renders; the
  `a6` repro's edit went from waiting for the whole render to about its own time with
  worker processes). New `Call.unlocked()`, `Call.may_unlock`, `Call.run_unlocked()` and
  `Call.version_used`; inside a batch and in changing calls the lock stays held. Worker
  pool and in-process fallback alike (`tests/test_tools_unlocked.py`); in-process, a
  rasteriser that holds the GIL still delays other threads (`docs/TOOLS.md`).
- `render`'s and `check`'s `slides` take slide ids and refs (`s:257`, `$intro`) as well
  as numbers, as every other deck address does: the items are strings to the provider
  and the dispatcher still takes a number, through the validation-only keyword
  `orInteger` (`string(or_integer=1)`). The tools declare `slides[]` as a ref target.
  Pages stay numbers. The core grows by 12 estimated tokens per format (deck 3,398, about
  5,029 counted; Word 2,429), within the 5,500 budget.
- Python 3.14 and 3.15: CI runs the suite on both, on Linux, macOS and Windows, and the
  classifiers declare them. `requires-python` stays `>=3.10`. Python 3.14 made
  `forkserver` Linux's default start method (`fork` before); the worker pool names `spawn`
  itself, so nothing changes, and a test now runs the pool and a toolbox -- a render, the
  font folders handed to a worker, an exception -- under every start method the platform
  has.

## 0.13.0 -- 2026-10-10
- The application's font folders: `Toolbox(font_dirs=[...])` is the default of its
  sessions, `toolbox.session(font_dirs=...)` overrides it per session (`None`: the
  renderers' default, `OOXML_FONT_DIRS` then the system's folders; `[]`: none, the
  variable not read). It is `Session.font_dirs`, copied to every `DocumentEntry`
  (`entry.font_dirs`, for a format's `checks`) and readable as `call.font_dirs`, for the
  work a handler sends to a worker process. `DocumentFormat.configure(document, session)`
  is called when a document joins a session (opened, created or adopted), so a format can
  apply the setting to it. Application configuration only: no tool definition, schema or
  prompt changes.
- OpenAI Responses: an offline round-trip test (`tests/test_tools_responses_roundtrip.py`)
  with synthetic fixtures modelled on a production user's live Azure OpenAI run (every
  task completed): deferred calls by bare `name` with a `namespace` field, server-side
  `tool_search_call` / `tool_search_output` passed back unchanged and never dispatched,
  reasoning items with `encrypted_content` under `store=False`, both image placements and
  each `detail`, over a three-turn loop with real dispatch (`docs/TOOLS.md`).
- `adapters.openai_input_problems` takes the whole next input: the model's own items
  (`reasoning`, `function_call`, `tool_search_call`, `tool_search_output`), a message
  whose content is a string, an assistant's `output_text` and SDK objects pass, where
  before they were reported (or, for a string content, raised).  An image outside a user
  message is still a problem.

## 0.12.0 -- 2026-10-08
- Rendering works in a daemonic process (a Celery prefork worker's child), which may not
  start the worker processes: the pool runs the work in-process there, by itself, and logs
  once. `Toolbox(workers=0)` asks for that anywhere, and `Toolbox(runner=...)` takes any
  object with `run(fn, *args, timeout=...)` and `close()` (`tools.InProcess`, or the
  application's own). In-process, a deadline bounds the wait, not the work: a thread cannot
  be killed, so a task past its deadline runs on in its slot (`docs/TOOLS.md`).
- OpenAI Responses: `images="message"` puts a turn's images in a user message after the
  `function_call_output` items (as Chat Completions does) instead of inside them, and
  `detail=` sets `input_image`'s detail (`auto`, `low`, `high`, `original`), on
  `render_result`, `render_results` and `adapters.openai_responses_items`. Defaults
  unchanged. `adapters.openai_input_problems` checks result items offline.
- `undo` takes an optional `scope`: a format that has scopes (decks: a slide) undoes only
  the latest change that touched it, leaving later changes elsewhere in place, and refuses
  with the new error code `entangled` when that change shares a part with a later one.
  `redo` with the same scope brings it back. Without `scope` nothing changed. Core:
  `History.undo_in` / `redo_in`, `OpcPackage.changed_between`, `snapshot_with`,
  `reachable_parts`; `DocumentFormat.undo_scope` and `restored`.
- `Limits.image_budget_per_round` with `Session.new_round()`: an image budget that starts
  again each round; `image_budget=None` lifts the session's cap (default still 40).
  `Session.images_remaining()`; a `limit` error says which budget ran out.

## 0.11.0 -- 2026-10-08
- `add_chart` makes a radar chart (`kind="radar"`, `edit_chart`'s `chart_type`), as
  PowerPoint and Word insert one (measured): lines in the accents, the column chart's axes.
- `legend="default"` (now the default) puts the legend where Office does: at the bottom,
  a radar chart's at the top.

## 0.10.0 -- 2026-10-08
- SmartArt model ids are made from the edit, so the same additions give the same bytes.
- `list_documents` removed from the shared tools.

## 0.9.0 -- 2026-10-07
- `charts.create.add_chart`: a new chart from data, written with a matching embedded workbook.
- `edit_chart` gains add, show/hide data labels and gap width; `GraphicHost.look` gives new
  titles and data labels the application's measured sizes.

## 0.8.0 -- 2026-10-07
- Required properties listed first, so strict decoding cannot drop an optional one.
- An array argument written as a JSON string is parsed (batch's ops); `\v` inside a JSON
  string reads as a line break.
- `open_document` on a text input points to `read_blob`.

## 0.7.0 -- 2026-10-07
- `Tool.reads`: a changing tool's reading mode runs as a read.
- Online checks for the workspace header and deferred tools found by tool search.

## 0.6.0 -- 2026-10-07
- Non-core tools deferred by default; strict on writing tools first; `allowed_tools`.
- Shorter shared descriptions, and a system prompt covering planning and batching.

## 0.5.0 -- 2026-10-07
- `read_blob`: an input's text (CSV, Markdown, plain text) a page at a time.
- A batch that changes nothing records no undo step and keeps the version.

## 0.4.0 -- 2026-10-07
- Shared tool definitions, refs (`$name`) and a generic `batch` tool.

## 0.3.0 -- 2026-10-06
- `ooxml_edit.tools`, the plumbing of an agent tool layer; `History.version`.

## 0.2.2 -- 2026-10-06
- `Chart.workbook_values()` reads a chart's workbook back; `content_types_with()` writes a
  copy with a part retyped.

## 0.2.1 -- 2026-10-04
- A new title keeps its language; a new series gets a `c16:uniqueId`.

## 0.2.0 -- 2026-10-04
- The optional `ooxml_edit.charts` subpackage: charts, embedded workbooks and SmartArt.

## 0.1.1 -- 2026-10-04
- `[Content_Types].xml` kept as Word writes it; an emptied relationships part is removed;
  `insert_in_order` attaches a detached child where no order is known.

## 0.1.0 -- 2026-10-03
- First release: the format-neutral core extracted from pptx-agent with its history --
  lossless OPC packages, adding, copying, removing and reaping parts, embedded packages,
  schema-ordered insertion, undo and redo, extension-list stamps. CI on three systems.
