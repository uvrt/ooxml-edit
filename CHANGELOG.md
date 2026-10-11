# Changelog

Versions are those in `pyproject.toml`; none is published to PyPI yet.

## 0.17.0 -- unreleased
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
