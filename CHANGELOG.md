# Changelog

Versions are those in `pyproject.toml`; none is published to PyPI yet. Each version that
reaches `main` is tagged `v<version>` on the commit that brought it, from 0.62.0; the
earlier ones were never tagged.

## 0.102.0 -- unreleased
- **"changed while this call read it" only when the change touched what the read covered**
  (round 47, the Word team's question). A handler that lets the locks go may say what of
  the document its result is of (`Call.read_scope`, in its format's addresses: a ranged
  check's blocks); the warning then comes only when a change made meanwhile touched it, as
  the format's optional `BlockEdits.touches(change, scope)` tells. A read of the whole
  document (no scope), a format that cannot tell, and changes no longer kept still warn on
  any change. Each document keeps its latest changes (`DocumentEntry.changes_kept`, 32;
  `note_change` takes the `Change`; `changes_between(used, now)`, the way from one version
  to the other). tests/test_tools_read_scope.py.

## 0.101.0 -- unreleased
- **A save copies untouched parts as their compressed entries** (round 46; the user's
  choice, 2026-11-09). `write`, `save` and `to_bytes` write a part that is still what was
  read as its entry's compressed data, as it is -- never inflated or compressed again --
  under a header made anew: the entry's method (stored, deflated, bzip2, LZMA: ooxml-common
  0.45.0's `PartStore.compressed`), sizes and CRC-32 in it, no data descriptor, no extra
  field (ZIP64's where a size or offset needs it), the flags that describe the data, the
  name's UTF-8 flag. An edited, added or replaced part is compressed as before. Saving a
  workbook with a 534 MB sheet (70 MB file) no longer inflates and deflates every part:
  one small part edited, CPU seconds per save, before -> after: 22.6 -> 0.02 (the file
  50.6 -> 70.3 MB: the input was compressed at a lower level than zlib's default and stays
  so); a 31 MB workbook 5.7 -> 0.01 s (22.0 -> 31.0 MB); a 55 MB one 21.3 -> 0.02 s
  (40.2 -> 55.1 MB); a 64-page report and a 25-slide deck 0.02 s either way (same sizes);
  an Office-made 14 MB document 0.03 -> 0.02 s (14,165,909 -> 14,166,424 bytes) and
  deck 0.01 -> 0.00 s (640,147 -> 645,371 bytes). Excel, Word and PowerPoint for Mac open
  such saves of their own files, edited (and Excel one rewritten first with data
  descriptors and extra fields). The archive differs from before where the file read was compressed otherwise
  than zlib compresses (Office's own deflate): every part reads back the same, and
  zipfile's `testzip` and Info-ZIP's `unzip -t` pass (`tests/test_opc_compressed.py`, with
  data descriptors, extra fields, a ZIP64 local header, stored and bzip2 entries and a
  UTF-8 name). `recompress=True` (a host's choice; no tool sets it) writes as before
  0.101.0: every part compressed again, the bytes of before. Requires ooxml-common 0.45.0.
## 0.100.0 -- unreleased
- **Edits of different blocks of one document run at the same time** (production round 45,
  R64 stage 3; `ooxml_edit.tools.blocks`). A format may say how
  (`DocumentFormat.blocks`, a `BlockEdits`: docx-agent's Word documents): a changing call it
  admits runs on a *draft* -- an editable copy of the last committed version -- with no lock
  of the document held, so calls on other blocks run beside it; then, under the document's
  lock and briefly, the format checks that what the call read and changed is as it was
  (`BlockEdits.check`: its blocks and those around them, the facts of the whole document it
  used, every other part unchanged since -- each change the dispatcher makes is counted by
  its parts, `DocumentEntry.note_change`, `parts_changed_since`), the dispatcher checks what
  it read of the session (refs, a retry key, its datasets, tracking), and the patch is
  applied as the call's own undo step and version (`BlockEdits.apply`, `settle`), its ids
  renamed where another call drew them first. Another call's change first is a conflict: the
  call is prepared again, at most `Limits.block_tries` (3) times -- once only after a
  preparation of more than `Limits.block_redo_cpu` (2 s) of its thread's CPU, so a long
  call costs at most one run more -- then runs under the lock
  for its whole run, as calls a format does not admit (whole-document calls, session tools,
  reads of other documents) always do. Such a call -- any that takes the document's lock
  -- first waits for each draft in flight for longer than `block_redo_cpu` seconds to be
  applied (or to take the lock, or fail), so a fields update does not make a long fill run
  again (`DocumentEntry.wait_for_long_drafts`, `draft_began`, `draft_ended`; never for a
  draft begun meanwhile, nor when its thread holds the lock already: bounded, no
  deadlock); `Result.block` says `waited`. Serializable: a committed call's result and change are
  those of the same call run alone at the moment it committed. A batch's reads after its last
  change read the version it made; its checks run on it (`After`); what it changed
  (`touched`) and what each op changed (`touched_by_op`, `BlockEdits.replay_changes`) are
  read from the document once applied, never from the draft (the document keeps what it
  read of its earlier states, so only the blocks changed are read again).
  `Toolbox(block_edits="auto")` -- the default -- drafts a call only while another call
  edits the same document (its change not yet made: `DocumentEntry.begin_edit`, `editing`;
  reads and checks never wait for an edit, so a draft beside them alone spares nothing),
  or when the format says the call is worth a draft alone (`BlockEdits.alone`: one that
  may run long, docx-agent's fills from a dataset); otherwise one caller's calls run
  exactly as before. `"always"` (tests), `"off"`. `Result.block` (for the application, never the model) says how a call ran on a
  draft: its tries, its drafts' CPU, each conflict's reason, why it took the lock; a result with
  `data.timing` says `redo` there. `OpcPackage.set_tree` installs a part's new tree.
  tests/test_tools_block_edits.py (a toy format: commit order, conflicts and tries, the
  lock past them, refusals of the committed version, retry keys, datasets made on a draft,
  the change count, many callers).
- A source argument a strict decoder filled with `""` (a table cell's `from` beside its
  text) is no source: the dispatcher no longer refuses it as an address (round 45, lane D).

## 0.99.0 -- unreleased
- **Parts stay compressed until they are edited** (production round 46: a workbook with a
  534 MB sheet in a 70 MB file took 1.25 GB open). An
  `OpcPackage` keeps every part it read as its compressed ZIP entry (ooxml-common 0.44.0's
  `zipparts.PartStore` and `ZipPart`) until the part is replaced or its tree edited: `read`
  inflates it at each call (the same bytes as before), `open_part(path)` gives a binary
  file over a part's current bytes inflated a piece at a time, `part_size(path)` its size
  without inflating, and `part_entry(path)` its `ZipPart` while it is untouched (to read
  more than once, or from an offset). `tree` parses a part over 1 MB
  (`opc.PARSE_STREAMED`) as it is inflated -- the same tree, never the part's bytes held
  whole beside it -- and `xml.parse_xml` takes a binary file for that. `write` copies an
  untouched part over 1 MB (`opc.SAVE_STREAMED`) through 64 KB at a time -- the size set
  first, as `writestr` sets it -- where this Python's deflate writes the same bytes for
  pieces as for the whole (zlib; `opc.pieces_compress_alike`, tried once); with zlib-ng
  (CPython's on Windows from 3.14, known by its version strings), which does not, and for smaller parts, the part is
  inflated whole and written as before. The bytes saved are those of before either way
  (checked against every part held inflated, `tests/test_opc_compressed.py`). Undo, redo, `opened`
  and the scoped snapshots keep the entries (`PackageSnapshot.raw` may hold a `ZipPart`;
  `changed_between` compares two of the same entry without inflating it); duplicates and
  orphans are sized without inflating. A part's data is checked as it is inflated
  (`zipparts.PartError`, a `zipfile.BadZipFile`, for a part that inflates past or short of
  its declared size or to another CRC-32), so a damaged part is refused when it is read,
  not at open; the package's limits are checked against the declared sizes before open, as
  before. Requires ooxml-common 0.44.0.
## 0.98.0 -- unreleased
- **`column_index` takes only a column's letters** (production round 46, R77): one to three
  of `A` to `Z` (case aside) on the sheet's grid, `A` to `XFD`. Anything else -- `""`, a
  cell (`"A1"`, which gave 11), a `$`, spaces, other letters, a column past `XFD` (which
  gave numbers past `MAX_COLUMN`) -- is `ArgumentError` (a `ValueError`) quoting the text;
  not text, `ArgumentTypeError`. `Cell.parse` (and so `Area.parse`) refuses a cell past
  `XFD` the same way, quoting the cell, where it made a `Cell` off the grid. Every caller in
  ooxml-edit, xlsx-agent, docx-agent and pptx-agent passes only letters it matched
  (xlsx-agent counts a column past the grid itself to say why it refuses it).

## 0.97.0 -- unreleased
- **Reads never wait for an edit in flight; an edit holds a document only while it changes
  it** (production round 45, R64 stages 1 and 2; twelve agents on one document waited for
  each other most of their run). A format may copy its documents
  (`DocumentFormat.read_copy(document) -> make`, docx-agent's Word documents): a changing
  call then keeps the version it leaves as committed before it lets the lock go
  (`DocumentEntry.commit`: the version's bytes, refs, tracking and owners, and what the
  format's copy needs), and a reading call -- a reading call of a tool of no role of its
  own, or a `read_batch` -- works on a copy of the last committed version under the
  copy's own lock (`Session.read_lock`, `DocumentEntry.reader`). It never waits for an edit
  in flight, which changes the document, never the copy, and no edit waits for it; each
  caller's own change is committed before its call returns, so its next read sees it. The
  copy is made once per version, when a read first asks, and shares the document's caches
  (keyed by version) and call log. A changing call's checks (`DocumentFormat.checks`) run
  on the copy of the version it made once its locks are let go (`dispatch.After`), so the
  next edit no longer waits for them; a result's `version` is taken under the lock. `LRU`
  is safe to share between threads. Results, ids, versions and bytes are those of the same calls one after another
  (tests/test_tools_committed_reads.py; docx-agent compares both ways). A format without
  `read_copy` reads and checks under the lock, as before.

## 0.96.0 -- unreleased
- **Document properties, one implementation for every library** (production round 44, R62;
  `ooxml_edit.properties`): `read_core`/`write_core` and `read_custom`/`write_custom` on any
  `OpcPackage`. Core properties (ECMA-376 Part 2, §11) by name -- title, subject, creator,
  keywords, description, last_modified_by, revision, created, modified, category,
  content_status, language, identifier, version, last_printed; the element's own name and
  `author`, `status`, `comments` taken too -- written in the order the applications write
  them (measured from Excel for Mac 16.106: title, subject, creator, keywords, description,
  lastModifiedBy, created, modified, category), dates in UTC, whole seconds; `None` removes
  one. Custom properties (Part 1, §22.3) as name -> `CustomProperty(value, type, linked)`,
  read from every value type (text, integers, numbers, bool, dates; others kept with their
  type's name), written as `vt:lpwstr`, `vt:i4` (a whole number within 32 bits), `vt:r8`
  (others; a whole one without ".0", as Excel wrote 2**40 back), `vt:bool` or
  `vt:filetime`, each with the user-defined format id and the next `pid`; a name differing
  only in case is the same property; a linked property is not given a value. A package
  without either part gets it, related from the root; the custom part goes with its last
  property. `check_core`/`check_custom` list every problem first (`PropertyError`), and
  nothing changes the modified date unless asked. docx-agent and pptx-agent each have
  their own today (`edit/properties.py`); they can move onto this one
  (`tests/test_properties.py`).

## 0.95.1 -- unreleased
- **The cell writer on a sheet of many rows** (production round 44, xlsx-agent lane P: one
  write on a sheet of 50,000 rows took seconds, a thousand new strings half a minute). Each
  new shared string no longer counts every item of the table twice (lxml's `len` walks
  them) nor searches it for its `extLst`: the index is checked at its end (its last item
  still the table's last) and found again only when that changed under it or the tree was
  replaced. A cell's column is looked up from its letters rather than matched (the same
  column `Cell.parse` gives, the same refusal for a reference that is none); the sheet is
  measured for its `dimension` in one pass, and a write inside the `dimension` as last
  written leaves it. Nothing changes its bytes: tests/test_sheets_speed.py holds each
  shortcut to the long way round.

## 0.95.0 -- unreleased
- **A cell's format and a row's height, set on their own** (production round 44, R53; for
  xlsx-agent's formatting). `Worksheet.set_style(cell, style)` gives a cell a format with
  its value and formula untouched: a position without a cell gets a formatted blank
  (`<c r s/>`), format 0 on a blank that holds nothing else removes it. `row_height(row)`
  reads a row's own height, `set_row_height(row, points)` writes `ht` with `customHeight`
  (0 to 409 points; a row without cells is written holding only its height) and `None`
  takes it away (a row left with nothing is removed). `row_style(row)` and
  `set_row_style(row, style)` read and write a row's own format (`s` with `customFormat`),
  and `cells_in(area)` gives the positions an area holds a cell at, making none.
  `Styles.add_format(xf)` finds an
  identical cell format or appends it, through the same index `with_number_format` and
  `with_wrap_text` use, so a format made by the caller is reused like theirs. Additive:
  nothing that exists changes its bytes.

## 0.94.0 -- unreleased
- **A copy's numbered lists stated to run on** (production round 43). `check`'s `copies`
  items take `lists`: `run_on` where the caller says one numbered list counts on through
  the copies of a sample (1-2 in the first copy, 3-4 in the next), `sample` (the default)
  where each numbers as its sample shows. Never inferred -- the sample cannot show it --
  so the caller states it; docx-agent 0.1.70 compares a run's later copies against the copy
  before them. `runs_on`, `run on`, `run-on` and `continue` are read as `run_on` with a
  warning; any other value is refused naming both. A document's parameter alone, as
  `copies` is; check is a reading tool, never strict, so Claude's strict set is unchanged.

## 0.93.1 -- unreleased
- **No lock warning from lxml 7 when a cell gets rich text** (production round 41). On lxml
  7 (7.0.0b1; Python 3.15t and 3.15 alike) writing rich text into a cell printed "Exception
  ignored in: 'lxml.etree.RWLock.unlock_write' ... AssertionError" for each marked run whose
  cell font came from the stylesheet: lxml 7 gives a document its parser's lock, and its
  `extend()` releases the target's lock early, then a second time, when the first element's
  document shares that lock and a later one's does not (here a new `b` before copies of the
  stylesheet's `sz` and `name`). A run's properties are appended one at a time instead, and
  an inline string's runs are passed to `extend()` as a list (lxml 7 warns when it
  truth-tests an element). The same bytes as before, on every Python. The free-threading
  workflow's suite now fails on an unraisable exception
  (`-W error::pytest.PytestUnraisableExceptionWarning`).

## 0.93.0 -- unreleased
- **A tool's name, however it is written** (production round 40, R44). A `batch`'s and a
  `read_batch`'s ops read a tool's name as a direct call does, and both read more:
  besides a namespace (`functions.x_set_text`), a name that is another spelling of one
  tool's -- another verb for the tool's, a plural, the format's prefix added or left out
  (`x_replace_text` for the shared `replace_text`, `x_insert_picture` for `x_add_picture`)
  -- runs that tool, with a warning ("tool 'x_replace_text' read as 'replace_text'"), where
  it was refused with a hint (`hints.tool_alias`; `Toolbox.tool_named(..., aliases=)`).
  Only when exactly one tool matches and the format's `tool_hints` table does not read the
  name otherwise: a table entry stays a hint. An atomic batch's refusal of such an op names
  the tool it was read as. A name that is no tool, with no hint, names the nearest tools in
  its message ("; the nearest: x_add, x_read, x_half"), not only in `valid_options`. No
  definition changes: 0 tokens.

## 0.92.0 -- unreleased
- **The render lane** (production round 39). Rasterising competed with layouts and checks
  for a pool of two workers: on an 11-writer report renders queued past their deadline ("no
  worker was free within 20 s") or were stopped half drawn, and each stopped worker started
  again and lost the layouts it held. `run(..., lane="render")` (`worker.RENDER_LANE`, on
  every runner, `Call.run`, `Call.run_all`; an application's runner with `takes_lane`) marks
  work that draws: it takes a worker only while no other task waits for one, prefers workers
  holding no affinity key, and at most `Limits.render_workers` (default `None`: all) draw at
  once. Its wait for a worker is bounded on its own (`worker_queue_timeout`), its deadline
  counting from when a worker takes it, and past it the worker is not stopped: the call
  reports `timeout`, the worker finishes the work for at most as long again
  (`worker.OVERRUN`), its result handed to `on_late` (the pipeline keeps the images in the
  store), and only one still busy then is stopped (`pool.overruns`). The render pipeline
  runs all its drawing in the lane, a render's layout task queued and finished the same way
  (`Call.run(..., queued=True)`, `run(..., overrun=True)`: its worker holds the layouts), and a whole document's views a view a task (up to 4). Other
  work's deadlines are unchanged.
- **`render`'s `fast`** (documents): the render pipeline's `draw(..., fast=True)` asks for
  cheaper pixels of the same SVG where the renderer has them (`Renderer.fast`,
  `Renderer.rasterise_fast`), kept under their own key (`render.fast_key`); an exact image
  held is served instead; `draw`'s values gain `approximate`. The worker tasks' tuples are
  unchanged (the approximate views are listed beside them). A library that does not take
  `fast` draws exactly. +40 tokens to a Word toolbox's core (docx-agent's estimate).

## 0.90.1 -- unreleased
- A scale's `show: label` check: a text holding several steps' labels, one per line, and no
  value kept -- a table cell merged with another, its records dropped -- is listed under
  `unrecorded` (`label_of` lists their steps), no longer skipped as "not one number, nor a
  step's label"; `style_by_value` skips it saying the one value to write. Nothing else changes.

## 0.90.0 -- unreleased
- **Affinity in the worker pools** (production round 38). A worker serves task after task, so
  what a task leaves in its process -- a format's layout of a document, what its text
  measured -- is there for the next task in that worker only. `run(fn, ..., affinity=key,
  affinity_wait=seconds)` (`WorkerPool`, `SubprocessPool`; `Call.run`; the render pipeline's
  `draw(..., affinity=, affinity_wait=)` for its layout task) sends a task to the worker
  that took the last task with the same key: at once when it is idle; while it is busy the
  task waits for it at most `affinity_wait` seconds -- the caller's rule, the time the work
  would take done afresh elsewhere, so waiting never costs more than going elsewhere; `None`
  (the default) as long as the deadline allows, `0` not at all -- and then takes any idle
  worker, or a new one (it counts against the deadline as any wait for a worker does).
  Tasks waiting for one holder take it in the order they came, and no other task takes a
  worker that tasks are waiting for. A key no worker holds goes to any idle worker; work without a key prefers
  the idle workers that hold no key (or held one longest ago), so the rasterising of a
  render goes beside a layout rather than in front of it. A worker that is killed at a
  deadline, dies or is stopped takes its keys with it; at most `AFFINITY_KEYS` (256) keys
  are remembered. The pool only chooses the worker: a task must give the same result
  anywhere, making what it does not find. `affinity_counts` (hit, waited, elsewhere,
  unheld) and `holder(key)` say where keyed tasks went. `InProcess` and `ThreadRunner` take
  the keywords and need them not (one process); an application's own runner is handed them
  only when it has `takes_affinity = True` (`ooxml_edit.tools.worker.takes_affinity`).
  `affinity` and `affinity_wait` are now `run`'s own keywords, as `queue_timeout` is, not a
  task's.
## 0.89.0 -- unreleased
- **A document's state kept beside its bytes** (production round 38). A format may keep what
  its file cannot hold without changing what Office reads (docx-agent: a Word document's
  retired ids): `DocumentFormat.state(document) -> str` and `open_with_state(data, state)`.
  `Session.save(doc)` gives a `SavedDocument` -- `data` and `state` of one version, taken
  under the document's lock, with `doc`, `name`, `kind` and `version`; a save tool's output
  carries the state when its handler says the file is the document itself
  (`Call.output(..., state=True)`, `Output.state`; never in what the model reads); and
  `Session.open(data, name, doc_id=..., state=...)` (and `open_blob`) hands it back. A state
  the format refuses (not these bytes', damaged) or a state for a format that keeps none is
  a `StateRefused` (an `ArgumentError`) saying what to pass instead; `None` or an empty value
  opens the bytes alone, as before. Nothing is written anywhere: the application stores the
  pair where it keeps its run.
- `History.continue_from(version, last)`: a reopened document's states numbered after the
  earlier history's (opened at `version`, the next step `last + 1`), only before the first
  step; `History.last_version`.

## 0.87.0 -- unreleased
- **A step's label in place of its value** (production round 38; `ooxml_edit.tools.scales`).
  A scale takes `"show": "label"` (default `value`): `style_by_value` writes each value's
  step's `label` where the value was -- the text then reads `Low` in the step's colours, not
  `1,4` -- and the library keeps, where the label is, the value it stands for and its
  reference (`scales.label_record`; a library reads it back as `ScaleValue.kept`, and gives
  a value's reference as `ScaleValue.source`). Every step needs a label, no two the same;
  refused otherwise with the way out. The value a label stands for: the text where it reads
  as a value (typed, or written by reference; also over an earlier label), else its record's
  `source` read again now when the session can (a dataset, or a document's table the call
  holds: `style_by_value` and `check` with such a scale hold them, as `check`'s numbers
  does), else the value recorded. `check(scale=...)` adds `labels` -- texts that are not
  their value's label, `why`: it shows the value, a value typed over its label, the value
  changed (`recorded 1,4, now 2,7`), the label edited (`written 'Low'`), the steps changed --
  and `unrecorded` (a step's label with no value kept: said, never guessed); `fix` writes
  them all again in one call. Exact text steps (`value` texts) work as before.
- `ooxml_edit.provenance`: **labels** -- `labels_after_edit` (a label outlives an edit of its
  own text: stretched over it, a whole text staying whole; an edit past its edge drops it)
  and `labels_found` (`whole`: a holder whose label is all its text, read as it is now).
- `check` and `style_by_value` definitions: the scale's `show` (+1 optional parameter each,
  enum `value`/`label`).

## 0.85.0 -- unreleased
- **Values styled by a scale given once** (production round 36, R31, "apply once";
  `ooxml_edit.tools.scales`). A new shared tool, `style_by_value(doc, scale, preview)`
  (deferred, in `shared_misc`): a scale is `steps` -- each a `fill` (`RRGGBB`), a `text`
  colour (`white`, `black` or `auto`: whichever has the higher WCAG 2 contrast ratio with the
  fill, a tie to black) and a `label`, taking either a `min` (to the next step's; a `max`
  makes a gap, which is then meant) or an exact `value` (a number or a text) -- with its
  `targets` and a `name`. Steps out of order, overlapping, mixing min and value, or whole
  values with one skipped (1, 2, 4) are refused with the way out and an example scale; a
  step whose colours fall below 4.5:1 is said. A value is its target's text read as one
  number in the session's locale (`2,5`; `85 %` is 85; `3/5` is 3) or, for text steps, its
  text. Each library reads its targets (`DocumentFormat.scale_values`, returning
  `ScaleValue`s: the value, the step it is styled with now, whether exactly) and writes the
  steps; the engine chooses, reports each target (value, step, `was`, `unchanged`, or
  `skipped` with why; at most 40, `left_out`), one undo step, `preview` writes nothing (a
  read). `check(scale=...)` (both formats) lists the values styled with another step than
  their value's (`wrong`, with `differs` where the step is right but its colours are not)
  or none (`none`), scoped by `slides`/`pages`/`range`, at most 20 (`left_out`), skipped
  ones apart, and `fix`: the one `style_by_value` call that restyles them. Nothing is kept
  in the document: the caller passes the scale again; the scale alone asks only for its
  fact. Targets: a table's column `<table>/col<n>` (body cells) and `{{...}}` references
  (`*` one part, `{{*}}` all) are read by every library the same way
  (`scales.column_target`, `scales.reference_matches`).
- `ooxml_edit.contrast`: WCAG 2's `luminance`, `ratio` and `readable_text` (white or black
  for a fill), one reading for every library (pptx-agent's contrast check uses it).
- Tokens (offline estimate): `check`'s `scale` (its nested fields undescribed) +158 in a
  deck's and a document's core; `style_by_value` about +464 to every definition of a library
  that offers it. `style_by_value` is strict only where Claude's limits leave room (a
  library lists it last); OpenAI strict schemas valid.

## 0.84.1 -- unreleased
- `check`'s numbers (production round 36, R28 follow-up): a percentage's value (a
  `percent` column, a `%` number format: a fraction) matches only a number written as a
  percentage (`%`, `‰`, a percent word), as its fraction; a number written without one
  matches plain values only. A bare `0,3` no longer matches a percentage column's 0.27 or
  0.31, nor a bare `30,0` its 0.3 times 100; `85%` no longer matches a percentage column's
  stored 85 as points. A percentage still matches a plain value as a fraction (0.85) or as
  points (85). A figure derived from values stays unverified (documented). No definition
  changed: 0 tokens.

## 0.84.0 -- unreleased
- `check`'s include enum gains `numbers` for documents too (`kind_values`, production
  round 36, R28): docx-agent gives a document's texts (`DocumentFormat.number_texts`).
  +3 estimated tokens in a document's core and every definition (docx-agent's guard); a
  deck's and a workbook's definitions unchanged. No parameter added: strict sets and
  optional counts unchanged.
- A value of a document's table not keyed by its first column (`Dataset.keys` 0: a
  workbook table with no one key column) is named by its row's position
  (`t:Scores/#1/mean`), as the table's resolver reads it; by key, the reference and its
  fix were refused when written.

## 0.83.0 -- unreleased
- **Numbers in text no value holds** (production round 36, R28): `check`'s `include` takes
  `numbers` (opt in, never a default; `ooxml_edit.tools.numbers`). Per paragraph, shape or
  table cell, its numbers that no value of the session's datasets or of its open
  documents' listed tables matches within the rounding the text shows (2.66 shown as `2,7`
  matches) are listed as `unverified`, with the two nearest values, their references and
  sources; numbers a value matches but that were typed are listed as `typed`, with the
  `replace_text` call that writes them by reference when it writes the same number.
  Decimals and percentages only by default; dates, times, years, list numbers, ids and
  numbers named by the word before them (section 4.2, version 2.1) are left aside and
  counted. Locale-aware (`1.234,5` / `1,234.5`, `85 %` as 0.85 or 85 points, `‰`, scale
  words). Capped (`texts`, `per_text`; `texts_left_out`, `more`).
- `NumberRules` (`ignore`, `whole_numbers`, `texts`, `per_text`): the application's, on
  `Toolbox(numbers=...)` and `toolbox.session(numbers=...)`; `Session.numbers`.
- `DocumentFormat.number_texts(call, scope)` (the texts a check reads, as `NumberText`) and
  `DocumentFormat.value_sources(document)` (a document's tables whose values text may
  quote); `Dataset.formats` (each column's number format, kept by `row_slice`);
  `Tool.value_reads` and `Spec(value_reads=...)`: the dispatcher holds every document that
  lists values for a call that reads them (a lone call, `batch`, `read_batch`).
- `check`'s include enum gains `numbers` for decks (`kind_values`): +3 estimated tokens in
  a deck's core and every definition; a document's and a workbook's definitions unchanged.
  No parameter added: strict sets and optional counts unchanged.

## 0.82.0 -- unreleased
- **Reopening a document under its earlier id** (production round 35, R23):
  `Session.open(data, name, doc_id="d1")`, and `doc_id` on `Session.adopt` and
  `Session.open_blob`, for an application resuming a run whose saved conversation names
  the ids it had. Refused (`ArgumentError`) when the id is open -- the message names the
  document holding it and the way out (`session.close(...)`, or open without `doc_id`) --
  or malformed (the accepted form: `d` and a whole number from 1, no leading zero).
  Closing frees an id; the counter never hands out an id given explicitly (after
  `doc_id="d3"` it counts `d1`, `d2`, `d4`) nor, as before, one it gave already. The check
  and the claim happen under the session's lock: two opens of one id at once give it to
  one of them.
- The `open_document` tool is unchanged (no `doc_id`): ids are the application's to give
  when it resumes. Tool definitions unchanged: no token cost.

## 0.81.0 -- unreleased
- **`check` and `render` scoped to blocks; copies against their reference** (production
  round 35, R18 and R19): the shared definitions of what docx-agent 0.1.41 serves.
  - `check` `range` and `render` `range`: block ranges as a document's read tools take
    them (blocks, spans, sections); one string is taken as a list of one.
  - `check` `copies`: `[{blocks, reference: {doc?, block}}]`. `check`'s documents are
    `doc` and every other document a reference names (`shared._checked_documents`), so
    those are locked too: a `read_batch` or a lone call holds them all.
  - All three are a document's parameters (`kind_fields` docx): a deck's or a workbook's
    toolbox is not sent them, and a handler that does not take them refuses one given
    (`invalid_arguments` naming it, "leave it out here"), never seeing them left out.
  - `render(range=...)` runs in `read_batch` as any render (0.80.0).
- **`orSingle`** (`array(..., or_single=True)`), a validation keyword: one item in an
  array's place is read as a one-item array. Never sent: no token cost.
- Offline tokens (characters / 3.5): a Word toolbox's `check` 238 -> 428, `render`
  220 -> 260; docx-agent's core 2,642 -> 2,871, every definition 13,939 -> 14,168, a
  reviewer's core 2,452 -> 2,682. Decks and workbooks: unchanged. `check` and `render`
  are not in docx-agent's strict set (unchanged: 6 tools, 23 optional parameters).

## 0.80.0 -- unreleased
- **`render` inside `read_batch`** (production round 35, R20): a reviewer's round -- pages
  and slides drawn, checks, reads -- in one call. `Tool.read_batchable` (`tool(...,
  read_batchable=True)`, `Spec`) lets `read_batch` run a tool `batch` refuses; the shared
  `render` has it, so the libraries need no change. A render op runs as alone (image
  budgets, its locks let go while it draws); its images follow the reply's text in op
  order, each with `Image.caption` (`ops[2] d1 page 3`), which every adapter sends just
  before the image (a lone call's images have none: unchanged). Images count toward
  `max_read_batch_chars` as their tokens would (`dispatch.image_chars`, 4 characters a
  token: a 1280 x 720 image 4,784); a render that does not fit is in `not_returned` /
  `resend` and its images go back to the session's budget (`Session.release_images`).
  `batch` refusing render now names `read_batch` (`valid_options`). `read_batch`'s
  description names render among its examples, at the same length (185 characters: "read"
  left out, "render" in, "with" -> "in"): offline tokens unchanged (194), since the
  agents' guards had 1-10 tokens of headroom; no other definition changes.
- **Fix: a hang between batches over two documents.** An op of a `read_batch` (or a
  batch's read after its last change, 0.79.0) that let its locks go let its own document's
  go only and took it back while the batch held another's: two `read_batch`es checking
  `d1` and `d2` in opposite orders hung for good, as did a batch ending with checks of both
  beside such a `read_batch` (on 0.79.0 with any check that lays out unlocked, as
  docx-agent's). Now every lock the batch holds is let go and taken back in document order
  (`docs/TOOLS.md`, "No layout under the lock").

## 0.79.0 -- unreleased
- **No layout under the lock** (production round 33, R16c). A check inside a batch, and
  Word's field update, laid the document out holding its lock: a one-paragraph read waited
  minutes behind them on a long report. Two mechanisms, `docs/TOOLS.md` ("No layout under
  the lock"):
  - A batch's reading ops after its last change run once its undo step is closed, its
    checks done and its refs and datasets kept, each as the same call alone (as
    `read_batch` runs them): `Call.may_unlock` is true there, so a `check` at the end lets
    the locks go while it lays out, and answers of the batch's version. Reads before the
    last change run inside the batch as before. **Semantic change:** a failing read after
    the last change cannot undo the batch, atomic or not; it is reported as its op's error
    (`data.failed`, `data.applied`, the summary's "resend only these on their own"). A read
    that starts after another call moved the document on says its `version` in its outcome;
    the batch's `version` is its own. A batch of reads only says `Read:`.
  - `tool(..., ahead=fn)` / `Tool.aheads` / `Tool.ahead_for(kind)`: a changing call's work
    ahead of its change, run before its undo step with `Call.may_unlock` true (outside a
    batch; `Call.ahead`), so slow reading -- a layout -- runs with the locks let go. After
    it the call goes on as if it had just taken its locks. Not part of the definition: no
    tool definition changes.
- Every tool definition is unchanged (offline token counts: no change).

## 0.78.0 -- unreleased
- **`read_batch`: many reads in one call, each with its own budget** (production round 32,
  R15). Builders read one record at a time, a model turn each, and put no reads in `batch`:
  its description reads as an edit tool's, it changes documents (`mutates`), so a reader is
  not given it, and its reply shares one `max_result_chars`, so several large reads were cut
  as one. `shared.READ_BATCH` takes `ops` as `batch` does, every one a reading call of any
  library's (describe, find, check, read, query, trace, a reading action), and answers each
  as the call alone: no undo step, no version, no checks, cut to `max_result_chars` as
  alone. The answers together fit the new `Limits.max_read_batch_chars` (96,000), which the
  new `Result.budget` carries past a sender's per-call limit; one that does not fit, and any
  op past `batch_timeout`, is listed in `data.not_returned` with `data.resend`, the call
  that asks for them, and a paged answer has `next`, its next page's call. A changing op is
  refused alone with the way out (`batch`); an unknown op's options are the reading tools.
  It is `review`'s, `mutates` false, not strict (free-form ops: no strict parameters), in
  the core, and not in `SESSION_TOOLS` (a library lists it, so the agents' definitions
  change only when they do). The shared prompt fragment gets one line naming it where it is
  sent (`prompts.READS`, `system_prompt(reads=...)`). `batch` is unchanged.

## 0.77.0 -- unreleased
- **Another kind's parameter at its default is ignored with a warning, not refused**
  (production round 31, R5e). A strict decoder writes every optional field, and a toolbox
  of several kinds is sent every kind's (`Tool.kind_fields`), so `find_text` on a workbook
  came with `"stories": "body"` and was refused. Now a `kind_fields` parameter that does
  not apply to the document's kind and asks nothing of it -- `false`, `""`, `[]`, `{}`, or
  a value of the spec's new `kind_neutral` -- is taken out before the handler runs, with a
  warning naming it (`stories ignored: it applies to pptx and docx, not to this xlsx
  document`), in a call and in a batch's op alike (`Tool.ignored_for`). Any other value
  reaches the handler and its refusal, as before: `slides: [2]` on a document, `boxes:
  true`, `stories: "layouts"` on a workbook, and every number (`spacing`, `contrast`,
  `min_size`: `0` is a measure, not "none"). The fields handled: `find_text`'s `slides`
  (`[]`), `range` (`""`) and `stories` (`""`, and `body` or `all` on a workbook, whose
  search is all its text: `kind_neutral`); `replace_text`'s `slides`, `range` and
  `stories` (empty); `render`'s `slides` and `pages` (`[]`); `check`'s `slides`, `pages`
  and `styles` (`[]`) and `boxes` (`false`); `save_document`'s `clean` (`false`, which
  was already dropped silently and now says so); `undo`'s `scope` (`""`). `edit_chart` leaves
  out an empty `columns` or `date_format` with a warning (an empty `from` is still not a
  source). No definition changed.

## 0.76.0 -- unreleased
- **An opt-in thread runner for a free-threaded Python** (free threading, step 3; the
  production team asked): `Toolbox(runner="threads")` runs the work in `ThreadRunner`, threads
  of this process side by side, where the GIL is off (`python3.15t`, `PYTHON_GIL=0`, lxml 7;
  `tools.gil_enabled()`); with the GIL on it uses the default worker pool instead, with a
  warning once. The same `run(fn, *args, timeout=..., queue_timeout=...)`, `warm` and
  `close` as `WorkerPool`, and `on_snapshot`'s queue bound and slots as with the pool. Warm-up
  runs once per process (`worker.warm_once`), not per worker. Each task gets its own copy of
  the arguments that could change (pickled, as a worker process gets them); bytes, strings
  and numbers go as they are. A thread cannot be stopped: a task past its deadline is
  reported as `timeout`, its result thrown away, and it keeps its slot until it ends; while
  `max_abandoned` of them (default: the runner's size; a toolbox's from
  `Limits.max_abandoned_threads`) are still running, `run` refuses new work at once with a
  `limit` error saying why. The process pool stays the default.
  `InProcess` is unchanged (it and `ThreadRunner` share `worker._Threads`).
  `Toolbox(runner="processes")` names the default; any other name is refused.
- New `tests/test_tools_threads.py`; `tests/test_tools_snapshot.py` runs its cases with a
  thread runner too.

## 0.75.0 -- unreleased
- **Blank chart points** (production round 29, C1). `edit_chart` from a source takes a blank
  cell (nothing, or only spaces) as a blank point (`None`) instead of refusing it. Text and
  other non-numbers are still refused, and so is a blank category or scatter x value. The
  result's `blank_points` says per series how many and in which source rows ("1 blank point
  (row 14)"), never a value. Values by position take `null` for a blank: `values`, and
  `data`'s `values`. New `blanks` (`gap`, `zero`, `span`) on `add`, `set_values`,
  `set_value`, `add_series` and `add_category` writes `c:dispBlanksAs` in the call's undo
  step; the handlers never see it. Every chart result with a blank point says `blanks` and
  `blank_points` by category position; `read` always says `blanks`. `Chart.blanks` reads the
  setting: a chart that does not say draws zero, measured in PowerPoint and Word.
  `Chart.set_blanks` and `Chart.blank_points` are new. `tools.sources.blank_text` is new.
  The validation keyword `orNull` (`number(or_null=True)`) is new, and so is
  `openai_strict_schema(schema, nulls)`, which makes such items nullable.
  `provenance.Holder.target` holds the chart a part's holder was noted by. `edit_chart`
  costs 49 more offline tokens in every pptx and docx definition. It is not sent strict, so
  the strict sets are unchanged.
- **`ToolError(valid_options=None)` means no options**, as `()` does (production round 29,
  W11: a refusal with nothing smaller to offer crashed as an internal error).

## 0.74.0 -- unreleased
- **Slow reads on a snapshot in the worker pool, side by side** (production round 28, P2:
  checks of one deck took turns). `Call.on_snapshot(fn, *args, timeout=...)` takes the
  primary document's bytes under its lock (`DocumentEntry.snapshot`, once per version, shared
  by every reader of that version), lets the call's locks go and runs `fn(data, *args)` in a
  worker process; the result reports the snapshot's version. Each worker opens its own copy
  and drops it with the task, so copies are bounded by the pool. Such tasks hold all the
  pool's workers but one (`Toolbox.snapshot_slots`; `Limits.snapshot_workers` sets how
  many), so a render does not queue behind a turn of checks. The task's `timeout` counts
  from when a worker takes it; the wait for one has its own bound,
  `Limits.worker_queue_timeout` (300 s), so a busy pool does not time out work that never
  started (`WorkerPool.run`, `SubprocessPool.run` and `InProcess.run` take
  `queue_timeout`; without it the wait counts against `timeout`, as before). In process (`workers=0`, an `InProcess` runner, the fallback) `fn` runs in
  the calling thread, unlocked and without a deadline, as before. Inside a batch or a
  changing call the locks stay held.
- **A read that let its lock go says when the document moved on**: the result keeps the
  version it read and warns "d1 changed while this call read it: the result is of version 5,
  the document is at version 6 now; call again for the latest" (`dispatch.moved_on`). For
  every unlocked read: renders and checks of decks and Word documents alike. Not when the
  change came from a later call of the same `dispatch_many` turn alone: the turn's order
  already says the read sees the document before it.
- **`dispatch_many` runs one turn's reads of a document side by side.** A call that only
  reads (`Toolbox._side_by_side`: a reading tool with no role of its own -- not `undo`,
  `close_document`, `save_document` -- taking no datasets, sources or text facts) waits for
  the turn's earlier edits of its documents, not its earlier reads; an edit waits for the
  earlier edits to end and for the earlier reads to hold their locks, so it runs while a read
  works on its snapshot. Every result is still the one the calls in order would give.
  Writes still serialize.

## 0.73.0 -- unreleased
- **`Call.defer(work)`: work for a call's result once the document is as the call leaves
  it** (round 28, W8: docx-agent's batched table ops). A call alone runs `work(result)` as
  its handler returns; a batch runs every op's after its last op, before the checks, in op
  order, each with its op's own result -- warnings it adds are put in their place among the
  batch's (`ops[k]: ...`). For what reads the whole document (a layout): done after every
  op, a batch cost the document's size per op; deferred, the ops after the first find it
  done. Work that fails is a warning, never the op's error (the op applied).
  `Toolbox.batch_defers = False` runs each op's work right after it, for comparing.
- **A batch past its deadline says what was not run** (W8). With ops applied one by one, the
  ops after the deadline are not run (each still listed with its `timeout` error), and
  `data.not_run` has their `count`, the `first` and `last` index, how many `ran` and in how
  many `seconds`, and `batch_size`: the batch that fits 80 % of the deadline at the rate the
  ops ran. The summary says it -- `Not run: 86 op(s), ops[38]..ops[123], after the batch
  passed its 120 s deadline (38 op(s) ran in 121.0 s); resend them as batches of at most 30
  ops` -- and lists only the ops that failed as `fix and resend only these`. An all-or-none
  batch's timeout says the same in its message and `details.not_run`.

## 0.72.0 -- unreleased
- **A dataset that read several sources goes stale when any of them changes** (round 25,
  lane K: xlsx-agent's join). `Call.define_dataset(..., also=[...])` names the other
  sources a dataset read besides its own -- document handles, or `DatasetEntry`s (another
  dataset, whose source and fate it inherits) -- kept as `DatasetEntry.also`, each with its
  own version, taken when the call ends as the source's is. `stale` is true when the
  dataset's own source or any of them is; undoing the change makes it current again, as for
  the source. Closing a document keeps the staleness of every source that is that
  document, its own or one it also read (`DatasetEntry.document_closed`), for good. A
  dataset derived from it inherits them. The stale warning names the source that changed,
  with "(a source it also read)".

## 0.71.0 -- unreleased
- **`Session.resolve_ref` folds ref names as a call does** (round 24, W5): `$recNow` and
  `$rec-now` are `$rec_now` when the document has that ref (silently: outside a call there is
  no result to warn in), and a `$Name` that is no ref name but stands for one (`$REC_NOW`)
  is `not_found` with "did you mean $rec_now?", as in a call's targets. `fold_ref` (and the
  helpers both use) moved from `dispatch` to `session`, and `fold_ref` is exported from
  `ooxml_edit.tools`; `ooxml_edit.tools.dispatch.fold_ref` still imports.

## 0.70.0 -- unreleased
- **Each kind's toolbox is sent the shared tools with its own parameters only** (round 23,
  V3). `Tool.kind_fields`, `Tool.kind_values` and `Tool.kind_descriptions` (and the same on
  `shared.Spec`): the optional parameters of a shared tool that apply to some kinds only
  (`{"boxes": ("pptx",)}`), the enum values that do (`{"include": {"fit": ("pptx",)}}`, an
  enum or an array's items' enum), and a parameter's description for a tool of one kind
  (`{"include": {"docx": "Default: validate, reflow, fields."}}`). One definition stays in
  `shared.SPECS`; a tool whose handlers serve none of a parameter's (a value's) kinds leaves
  it out of `Tool.canonical`, so of every provider's definition and of
  `optional_parameters()`. A toolbox of several kinds, or a definition without handlers
  (`shared.definition`), is sent the whole definition, so the libraries still merge.
  Validation is unchanged: a call is checked against the whole schema, so one that still
  passes another kind's parameter or value (an older prompt) gets that handler's answer, as
  before. Applied, each checked against the handlers:
  - `check`: `slides`, `boxes`, `spacing`, `contrast` a deck's; `pages`, `min_size`,
    `styles` a document's; `include` `fit`, `collisions`, `facts`, `design` a deck's
    (a document's handler refuses them), `reflow`, `fields` a document's (a deck's notes
    them "documents only"), `validate`, `layout`, `app` both's; its default described per
    kind. `contrast` stays a deck's: Word text against shading or a page colour is not
    measured (docx-agent refuses it).
  - `find_text`: `slides` a deck's; `range` a document's and a workbook's (described per
    kind); `stories` a deck's and a document's, its `layouts` and `masters` a deck's (a
    document reads any other value as its body), described per kind.
  - `replace_text`: `slides` a deck's; `range` a document's; `stories` as `find_text`'s.
  - `render`: `slides` a deck's, `pages` a document's.
  - `save_document`: `clean` a document's (another kind's handler refuses `clean: true`).
  - `undo`: `scope` a deck's and a workbook's (their formats' `undo_scope`; a document's
    undo refuses it, document-wide), described per kind. `undo`'s handler serves every
    kind, so the toolbox gives its own copy the kinds its libraries' tools serve
    (`Tool.kinds_served`, not part of the definition; the libraries loaded, not the formats
    configured, so configuring a format changes no definition); outside a toolbox, and in
    a toolbox of every kind, it is whole.

  Offline (characters / 3.5), old -> new: a deck toolbox's core 3,822 -> 3,545, every
  definition 13,442 -> 13,166; a Word toolbox's core 2,816 -> 2,436, every definition
  13,194 -> 12,814; a workbook toolbox's core 4,272 -> 4,122, every definition 5,013 ->
  4,862. `replace_text` is strict: one optional parameter fewer for a deck and for a
  document (24 -> 23 over each strict set).
- **`replace_text`'s `regex` is its last parameter** (round 23). With a deck's `slides` no
  longer sent to a document, `regex` came straight after the required `expect`, and
  Claude (Sonnet 5.5, strict, effort low) left it out of a Word toolbox's `replace_text` in
  6 of 6 calls asked for every field (docx-agent's online strict test, 3 of 3 runs); the
  same definition with `slides` after `regex` kept it in 4 of 4, and with `regex` last in 4
  of 4 probes and 3 of 3 runs of that test. Strict decoding writes keys in schema order;
  the order of the other parameters is unchanged and no description changes.
- **`Session.refs(doc)` and `Session.resolve_ref(doc, value)`** (round 23, V5): a
  document's refs outside a call, `{name: address}` (a copy, read under the document's
  lock), and one `$name` or `$name/rest` resolved as a call resolves it (`not_found`
  naming the document's refs). The application read them from `DocumentEntry.refs` before,
  which is internal. `REF` and the use pattern `REF_USE` now live in `tools.session`;
  `tools.dispatch` imports them (`ooxml_edit.tools.REF` is unchanged). No tool definition
  changes.

## 0.69.0 -- unreleased
- **`Tool.action_fields`** (round 17; `tool(..., action_fields=...)`): the optional
  parameters that only some actions take, `{"ids": ("accept", "reject")}`. A role set
  offered none of a parameter's actions is sent the definition without it, as its `action`
  enum already lists only its actions -- a reviewer's Word tracked changes (`list` alone)
  no longer offer `ids` and `all`, which the model was asked for in the online strict test
  and left out. Checked when the tool is made (an optional parameter, actions of the
  tool's); part of `same_definition`. No tool's definitions change until it names some.

## 0.68.0 -- unreleased
- **`ooxml_edit.pictures`** (round 17, P6), new and optional, held format-neutral as
  `charts` is (`tests/test_pictures_neutrality.py`): an SVG picture's DrawingML and its
  recolouring, moved from pptx-agent's `edit/svgpicture.py` so docx-agent tints an icon the
  same way -- `looks_like_svg`, `parse` (untrusted: no document type, no entities, no
  network, at most `SVG_LIMIT` bytes; `SvgError`, an `ArgumentError`), `native_size`,
  `svg_blip` / `attach` / `drop` (the blip's `{96DAC541-...}` extension), `recolor`
  (PowerPoint's Graphics Fill and Outline, as pptx-agent measured them in PowerPoint for
  Mac on 2026-10-15), `theme_class`, `THEME_SLOTS`, `stroke_units`. Unchanged in what it
  writes; pptx-agent's module re-exports the names. Word draws such an SVG (not its PNG
  fallback) and resolves the `MsftOfcThm_..._v2` class against the document's theme
  (measured in Word for Mac on 2026-10-26, docx-agent's round 17).
- `docs/USED-BY.md`: docx-agent now imports `ooxml_edit.tools.assets` (`fetch`, `find`,
  `MAX_RESULTS`) and `FolderCatalogue` too, for its own asset catalogue setting.

## 0.67.0 -- unreleased
- **`edit_chart` and `edit_smartart` behave the same in a deck and a document** (review,
  S10). The two agents' handlers did the same work twice and differed in six places; the
  user chose one behaviour for each, and the work is now here once, in
  `charts.actions`: `apply_data_action` (`set_values` to `set_gap_width`, `DATA_ACTIONS`),
  `need` (what a missing argument is said to be, one message set), `add_plan` and
  `finish_add` (`add`'s checks before anything is made, then its scale, minor gridlines
  and look inside the same undo step; `required` names what a format needs besides, a
  deck's `box`), `legend_position`, `apply_smartart_action` (`SMARTART_ACTIONS`), and
  the results: `chart_json` / `chart_read_json` (the chart under the format's key,
  `address` or `id`; type, title, categories as they are with dates as ISO dates,
  series, legend, `number_formats`, the dates' format, the gap width where there are
  bars; read adds each axis' title, the look and the workbook, `"unreadable: ..."` when
  it cannot be read) and `smartart_json` (every node as `{n, text, level}`). A format
  supplies only finding the chart or diagram and its key. What each format sees change
  is in the agents' changelogs; here: `add_category`'s label is `text` (as the shared
  definition says), `add_series`' and `add_category`'s `values` may be left out (blanks),
  `set_legend` without `position` puts it on the right and refuses a label position
  (`center`...) as `add` does, `hide_data_labels` takes `number_format` as
  `show_data_labels` does (and so refuses it: a format is for labels shown), SmartArt
  `set_text` takes `""` and no text as empty text. No tool definition changed.
- `Chart.number_formats` (from pptx-agent's `Chart`, which now uses it): what decides how
  numbers look, `{"values", "data_labels", "value_axis"}`.
- `charts.cases`: the table both agents run through their own tools (`CHART`,
  `CHART_CASES`, `SMARTART_CASES`, `pick`, `problems`), and here through the actions
  (`tests/test_chart_cases.py`). Listed in [docs/USED-BY.md](docs/USED-BY.md) and
  `tests/test_public_api.py` with the new actions; `apply_add_style`, `category_labels`,
  `resolve_node` and `scale_axis` leave both lists, since no agent imports them any more
  (they stay, used here).

## 0.66.0 -- unreleased
- **Typed refusals: `unit` means a unit** (review, S9). The dispatcher gave every plain
  `ValueError` the code `unit`, and the libraries' own refusals of an argument were plain
  `ValueError`s, so the model read `unit` for things that have nothing to do with one:
  removing a diagram's last node ("a diagram keeps at least one node") was `unit` in every
  format. New module `ooxml_edit.errors`: `ArgumentError` (a `ValueError`) for a value the
  library refuses, `ArgumentTypeError` (an `ArgumentError` and a `TypeError`) for a value of
  a type it does not take, and `UnitError` (an `ArgumentError`) for a number, date or length
  out of its range or not finite -- what `ERROR_CODES["unit"]` says. The dispatcher maps
  `ArgumentError` to `invalid_arguments` and `UnitError` to `unit` (`BASE_ERRORS`, under
  the formats' maps and the application's `error_map`, so a format may map a subclass
  otherwise; the closest class in the exception's MRO wins). A plain `ValueError` stays
  `unit` and a plain `TypeError` `internal`: the formats' own `ValueError` subclasses that
  their maps do not name (xlsx-agent's `RuleError`, `RedactError`, `ShiftError`; this
  package's `ChartDataError`, `ChartModelError`, `RecordError`, `PatternError`) relied on
  that, and keep their code.
  Every `raise ValueError(` / `TypeError(` in the package (113) was classified: 83 refuse a
  caller's value and are now `ArgumentError` / `ArgumentTypeError` (the datasets, the
  asset catalogue, the adapters' options, roles, views and contexts, the worker pools'
  sizes, a cell or range reference, a formula, rich text, a cell's style index and value
  type, a date's time zone, a chart's legend position and axis, the last node of a
  diagram, the drawing policy, a number format id or code, a `DateCategory`); 8 are a
  value out of its range and are `UnitError` (a column or cell outside the sheet, a date
  before a date system's first day, a number that is not finite, text longer than a cell
  holds) -- `unit` as before; 22 stay plain: a broken document (a part or
  `[Content_Types].xml` missing, a diagram with no document point, a workbook with no main
  part) or a mistake in the code using the library (a part added twice, two tools of one
  name, a group's or kind's name, a renderer's non-PNG, an unknown error code, a format's
  resolver returning no `Dataset`, a package that cannot scope an undo), and one the schema
  check raises and catches itself. Each new class is the
  one it was before, so every `except ValueError` and `except TypeError` still catches it.
  What a model sees change: `edit_smartart` removing the last node is `invalid_arguments`,
  not `unit`, in a deck and a Word document; any other typed refusal that reaches the
  dispatcher unmapped is `invalid_arguments` where it was `unit` (or, for a type,
  `internal`) -- the tools' schemas and the agents' own checks keep the rest from reaching
  it. Messages are unchanged; no tool definition changes. Contract-tested
  (`tests/test_errors.py`); `ooxml_edit.errors` is in the siblings' surface
  (`tests/test_public_api.py`, docs/USED-BY.md).

## 0.65.1 -- unreleased
- **ruff and mypy in CI; `py.typed` shipped** (review, S8). The package carries `py.typed`
  (PEP 561, in the wheel and the sdist), so the agents' type checkers read its annotations.
  CI's build job runs ruff (bug-finding rules only: pyflakes, syntax errors, mutable and
  call defaults, `return` in `finally`, a comparison as a statement, pylint's errors; no
  formatting) and mypy (non-strict; lxml untyped), both pinned in the `dev` extra
  (`pyproject.toml` has the configuration). What they found is fixed: unused imports
  (`ooxml_edit.opc.REL_IMAGE` and `ooxml_edit.tools.tiles` stay, re-exported explicitly),
  `xml.Element` a type alias, `ChartStyling` declaring what it uses of `Chart`, a
  `Workbook` rich-string index annotated with the key it has, `Holder.store` taking the
  `None` that removes a record (as it was called), `png_size` taking a `bytearray` (as
  `checked_png` passes one), the text-fact dataclass's `field` member no longer shadowing
  `dataclasses.field` for readers, names reused for another type split in two, and
  `Optional`s the code knew to be set made explicit (an `assert` where an invariant holds,
  a check where it reads the same). Two paths that would have raised `AttributeError` or
  `TypeError` now word their error instead: a chart text style asked of no part, and a
  missing ref with no document. Behaviour is otherwise unchanged.

## 0.65.0 -- unreleased
- **`ooxml_edit.tools.render`: one render pipeline for every format** (review, S5). The
  agents' render plumbing was written twice, in pptx-agent and docx-agent, and the copies
  differed. Now a format brings a `Renderer` adapter (a protocol; `RendererBase` has the
  defaults): `name`, `whole_document`, `lay_out` (a `Layout` of `View`s: each view's SVG,
  the raster fingerprint, the raster arguments, the format's facts), `key_text`,
  `rasterise`, `facts`, `rasterise_tiles`, `warm_up`, `code_stamp`. The rest is here, the
  same for each: `draw` (images this version already has, the snapshot, the chunks over the
  workers with the locks let go, what came back kept by content key and by version, what
  one chunk drew kept when another fails), `draw_tiles` (a contact image), the worker tasks
  (`worker_task`) `render_views` (views that stand alone: laid out and drawn per chunk),
  `lay_out_views` and `rasterise_views` (a document laid out whole, once; its views
  rasterised over the workers at once) and `rasterise_tiles`, the keys (`fingerprint`,
  `raster_key`, `system_fonts`), `warm_up`, and the PNG checks: `png_size` refuses bytes
  that are not a PNG (signature and header), and every image a renderer returns is checked
  (`checked_png`; a renderer's non-PNG is an internal error naming it). The module imports
  no renderer: the format passes its adapter, an argument the tasks pickle, so subprocess
  workers run it too. Contact images' and warm-up warnings are left out in the worker for
  every format (the worker's stderr is nobody's to read). Public: `tests/test_public_api.py`
  and docs/USED-BY.md list what the agents import; `tests/test_tools_render.py` holds the
  pipeline with a toy renderer, both shapes, in-process and in worker and subprocess pools.
  docs/TOOLS.md says how a format plugs in.

## 0.64.0 -- unreleased
- **Dependencies: ooxml-edit now requires ooxml-common** (`ooxml-common>=0.29.0`, beside
  lxml; review, S2). ooxml-common is standard library only and never imports ooxml-edit;
  ooxml-edit may import it ([docs/DESIGN.md](docs/DESIGN.md)). Neither is on PyPI: install
  ooxml-common from its repository first (README, "Install"). `tests/test_neutrality.py`
  and `tests/test_charts_neutrality.py` allow it beside lxml; the format-neutral rules are
  unchanged.
- ooxml-edit's copies of ooxml-common's logic are gone, and the names stay, re-exported:
  `dates.NAMES`, `LANGUAGES`, `LCIDS` and `DateNames` are `ooxml_common.dates`' (the same
  measured tables), `dates.names_tag` reads `ooxml_common.dates.names_tag`,
  `dates.is_date_code` is `ooxml_common.numfmt.is_date_code` (which `sheets.Styles.is_date`
  and `Chart.category_dates` now read), `tools.DEFAULT_LOCALE` is
  `ooxml_common.locales.EN_US` itself, and `tools.DefaultLocale`, the copy's class, is an
  alias of `ooxml_common.locales.Locale` for one release. `opc.CONTENT_TYPES_PART`,
  `REL_OFFICE_DOCUMENT`, `REL_IMAGE` and `normalize_part_path`, `charts.create.REL_CHART`
  and `R_NS`, and the chart line widths' points-to-EMU factor are ooxml-common's too (the
  same values and rule). A session still keeps any object with a locale's five fields as
  given (`tools.locale_of`).
- What changes: a number format code is read as Excel reads it (measured in Excel for Mac
  16.106, 2026-10-25, `ooxml_common.numfmt.MEASURED`). **Its first section decides**:
  `0;[Red]d`, `0;0;d` and `0.00E+00;d` were dates and are not (Excel showed 46027.5 as
  `46028`, `46028` and `4,60E+04`). **Dates and times it did not know** are now:
  `bbbb` (Excel: `2569`), `e` and `ggge` (`2026`), `[$-411]e` (`8`), `[$-ja-JP]ggge`
  (`令和8`), the Thai `ว/ด/ปปปป` (`๕/๑/๒๕๖๙`), `[$-th-TH]ว/ด/ปปปป` and `[$-41E]ว/ด/ปปปป`
  (`5/1/2026`), `ช:นน` (`๑๒:๐๐`), `[$-th-TH]ช:นน` (`12:00`), `[ช]:นน:ทท`
  (`๑๑๐๔๖๖๐:๐๐:๐๐`), `上午/下午` (`下午`) and `a/p` (`p`) alone, and the system's formats
  `[$-x-sysdate]`, `[$-F800]0.00` and `[$-F800]General` (`maandag 5 januari 2026`),
  `[$-F400]` and `[$-x-systime]` (`12:00:00`). These 21 codes are all that changed in the
  measured table, and every one now agrees with Excel; ooxml-common's
  `tests/test_numfmt.py` checks the two agree on all of it. A chart whose categories are
  kept in one of the new date codes now reads back as dates (`category_dates`), and
  `Styles.is_date` follows the same reading. On 60,000 generated codes the old and new
  readings differ only in those two ways: a date or time in a later section only, and the
  letters, markers and system ids above.
- Docs: README, CONTRIBUTING, DESIGN ("How it relates to ooxml-common") and TOOLS say the
  rule; CI installs ooxml-common before ooxml-edit (it installed it for the tests since
  0.61.0).

## 0.63.0 -- unreleased
- One lookup for what `edit_chart`'s `series` and `category`, and `edit_smartart`'s `node`,
  name (review, N4): `charts.actions.resolve_series`, `resolve_category` and
  `resolve_node`, which both agents' handlers now call instead of their own. A name wins
  (exactly, whitespace collapsed; failing that, full-width forms and case ignored); a
  number from 0 is a position only when nothing has that name, so a chart whose series
  are called "1" and "2" means the same in a deck and a document (docx-agent's took the
  position first). Refusals: `invalid_arguments` when the action needs one and none is
  given, `label_not_found` for a name nothing has or two share, `not_found` for a
  position past the end, each with `field` and the names (`valid_options`, at most 50);
  a date category's refusal says they are named as YYYY-MM-DD. Listed in
  [docs/USED-BY.md](docs/USED-BY.md) and `tests/test_public_api.py`; `choices` leaves
  both lists, since no agent imports it any more (it stays, used here).

## 0.62.0 -- unreleased
- Each version is tagged (review, N3): a tag workflow (`.github/workflows/tag.yml`)
  tags the commit that brings a version to `main` `v<version>`, this one first, and
  never touches another tag; CI's `build` job checks that `pyproject.toml`'s version is
  the one this file's top section names, and `ooxml_edit.__version__` the same
  (`.github/check_version.py`), so a sibling's floor on this package names a tag. No
  code change.
- The names the siblings import are declared and held (review, N2):
  [docs/USED-BY.md](docs/USED-BY.md) lists every name the agents and ooxml-common's tests
  import from here (228 names in 30 modules, from an AST scan of their `src`, `tests` and
  `tools`), and `tests/test_public_api.py` imports each one, so a rename fails this
  repository's own CI rather than a sibling's next run; the test also holds the page and
  its list to the same names, and no listed name may start with an underscore. The private
  names the agents imported get public ones: `opc.part_for_rels` (was `_part_for_rels`),
  `xml.child_ranks` (was `_ranks`), `charts.chart.series_of` (was `_series_of`),
  `charts.create.check_options` (was `_options`) and `charts.diagram.DiagramModel` (was
  `_Model`); the old names stay as aliases for one release, as `xml._prefixed_name` does.
  No behaviour change.

## 0.61.0 -- unreleased
- CI runs pptx-agent's, docx-agent's and xlsx-agent's suites (their main) against
  every change here, in a `downstream` job (Linux, Python 3.12), so a change that breaks
  an agent fails on its own pull request; installs ooxml-common (test only) so the tests
  that hold the en-US defaults to `ooxml_common.locales` run instead of skipping; and
  runs nightly (02:17 UTC) and on demand. No code change.
- The shared `check` takes `layout` in `include`, and `min_size` and `styles` (round 16,
  F8): a document's layout facts, measured from the layout the document is drawn with
  (docx-agent's handler) -- text below `min_size` points by kind, direct formatting over
  the style with the call that clears it, styles not in `styles`, empty paragraphs used as
  spacing, keep-with-next paragraphs left at a page's foot, rows split across pages and
  header rows that do not repeat, pictures and tables past the margins, and the pages the
  layout only approximated or drew placeholders on. Either measure asks for the facts. Each
  is one type a strict schema carries: a number, a list of names. They are handed only to
  a kind's handler that takes them (`_with_layout_options`, as `save_document`'s `clean`);
  another kind's refuses either when given (`invalid_arguments`, the field named) and
  never sees them left out. The definition grows in every kind's core.

## 0.60.0 -- unreleased
- A call that names no document and makes one -- `new_document`, `open_document` -- has
  that document as its result's `doc` (and its version as `version`), as every later call
  has the one it names; it was `None`, the id only in the summary and `created` (round 16,
  F10). For every kind: the dispatcher sets it when the call made exactly one open document.
- The shared `save_document` takes `clean` (round 16, F11): a boolean, handed only to a
  kind's handler that takes it (docx-agent's: orphaned parts, rsids, pasted web divs and an
  external template link taken out of the file saved, reported as `cleaned`); another
  kind's refuses `clean: true` (`invalid_arguments`, `field: "clean"`) and never sees
  `clean: false`, so pptx-agent and xlsx-agent need no change. `OpcPackage.orphaned_parts()`
  (unreachable parts, and stray entries no content type is declared for that nothing
  targets, such as `[trash]/0000.dat`) and `drop_orphans()` (removes them with their
  relationships parts and overrides, an unused extension's `Default` too; returns
  `(part, bytes)`), for every format. The definition grows by the `clean` property in
  every kind's core.

## 0.59.0 -- unreleased
- The shared `check` takes `contrast` (round 15, T2): decks report text whose contrast with
  what is behind it is below that ratio (`"contrast": 4.5`, WCAG 2's for text; large text,
  18 pt or 14 pt bold, is held to two thirds of it), measured, lowest first (pptx-agent's
  handler; a document's refuses it, as it does `spacing`). A number, not `true | ratio`:
  one type, which a strict schema can carry. Bounded 1-21 by the call validator.

## 0.58.0 -- unreleased
- A worker that exits as it starts fails the call at once, naming the cause (round 13, R6).
  A starting multiprocessing worker is waited for on its pipe and its process together
  (`multiprocessing.connection.wait`), so an exit is seen at once, not at
  `start_timeout`; the error gives the exit code or the signal.  The case production met: a
  script with no `if __name__ == "__main__":` guard, which spawn imports again in each
  worker, so the script's work ran there too while the parent waited 120 s a call (240 s
  for the first: the warming workers' 120 s, then its own).  A worker that reaches
  `WorkerPool.run` or `Toolbox.dispatch` while still starting
  (`worker.bootstrapping_worker()`) now stops at once with exit code 86
  (`worker.UNGUARDED_EXIT`), saying so on stderr, and the parent's error says to add the
  guard or use `workers=0` or a `SubprocessPool`.  An exit with a code as a worker starts
  is lasting: a `WorkerPool` falls back as in a daemonic process (subprocess workers, which
  never import the main module; then in-process), with `fallback=False` later calls raise
  the same error at once (`pool.failed`), and `warm()` stops trying; a signal (a kill) is
  not remembered.  A `SubprocessPool` worker that exits before it is ready is remembered
  the same way.  A live worker still gets its whole `start_timeout`.  Tool definitions
  unchanged; the only new text a model may read is the error of a call whose worker
  could not start.

## 0.57.0 -- unreleased
- Every result says what the call changed, for the application (round 13, R2):
  `Result.touched`, one entry per thing changed with its `"doc"`, in the format's own
  addresses (`DocumentFormat.touched(document, change)`; without one,
  `{"doc", "part"}` per changed part, `tools.part_entries`), and for a `batch` the union
  plus `Result.touched_by_op` (`{op: entries}`).  Never in `to_json`/`to_text`: the model
  reads not one token more.  Derived from the bytes, not the arguments: the dispatcher
  watches the call's document (`DocumentEntry.watch`, over `History.watch`), so every
  tool, `undo` and `redo` included, is covered, and an address only read (a `like`, a
  source) never appears; an error or a call that changed no bytes reports `[]`.  A format
  whose mapping raises falls back to the parts, logged.
- `History.watch()` yields a `history.Change` -- the state before the first and after the
  last state change made while it is open (an outermost batch closing, undo, redo, a
  scoped undo or redo, a checkpointed edit), `parts` (the part names whose bytes differ,
  computed when asked) and `before(path)`/`after(path)` (the bytes either side).  Free:
  the snapshots are the ones the history takes anyway.  `History.mark()` (the state now,
  to compare stretches of an open batch: `History.change(a, b)`), `History.compares`,
  and `History.savepoint(start)`, which takes a mark as its start instead of taking the
  snapshot again; `DocumentEntry.watch`, `mark`, `savepoint(start)`.  A batch's ops each
  take one mark per document they change (a partial batch's savepoint reuses it).

## 0.56.0 -- unreleased
- The shared `check` takes `spacing` (round 12, Q1): decks report the gaps, padding and
  frame clearances below that many points (`"spacing": 8`), measured as drawn and nearest
  first (pptx-agent's handler; a document's refuses it, as it does `boxes`). A number, not
  an object with `below`: 35 estimated tokens in every format's core rather than 64.
  Bounded 0-200 pt by the call validator.

## 0.55.0 -- unreleased
- Contact sheets narrow their tiles instead of refusing (round 10, O4): tiles too wide for
  the image's 2,576 px edge side by side are drawn at the widest that fit
  (`tiles.tiling_of`; two columns of 1,280 px, 2,584 px, are 1,276 px each), and tiles too
  tall at the widest that fit for their shapes (`tiles.fit`: each tile's height over its
  width, a tile `ceil(width * ratio)` px tall as resvg sizes it); a call's own `width` over
  a tile's 1,280 px stands in as 1,280.  `Tiling.shrunk` (`{"from", "why"}`) and
  `tiles.narrowed_note` say from what width and why, for the result; only tiles that would
  not fit even at 160 px are refused, naming how many columns or views would.
- The shared `undo`'s `scope` says what each kind takes: a slide's (`256`) or a sheet's
  (`Sales`) changes; a document takes none (refused, as before).  One or two estimated
  tokens more in every format's definitions.

## 0.54.0 -- unreleased
- `ooxml_edit.markdown` (core, lane X, X3b): a small Markdown subset read into runs with
  their marks and written back, without a Markdown library -- `**bold**`, `*italic*` /
  `_italic_`, `~~strike~~`, backslash escapes and code spans (literal text, `code` set) by
  CommonMark 0.31.2's delimiter-run rules (flanking, intraword underscores, the rule of
  three) and markdown-it-py's strikethrough (the parser the format libraries use; the
  tests check random text against it when it is installed, now a dev dependency).
  Links, images, HTML, entities and every other block stay text.  `parse` reads lines --
  each line its own, not CommonMark's joined paragraphs; one empty line kept between
  lines -- and list items (`-`, `*`, `+`, `1.`, `1)`) nested by CommonMark's content
  columns and renumbered from a list's first number; `parse_inline` one line;
  `inline_markdown`, `to_markdown` and `escape` write runs and lines back so that they
  read back the same (a line start that would be an item escaped; marks Markdown cannot
  place dropped, never text).
- `ooxml_edit.sheets.RichText`: a cell's text as runs (`Worksheet.write`), a shared
  string of `r` runs (an identical item reused) or an inline string without a table.
  Each run's `rPr` is the cell's whole font with bold, italic and strikethrough added,
  in the order Excel writes run properties: measured in Excel for Mac 16.106
  (2026-10-20), a run with only `<b/>` shows in Excel's default font, not the cell's.
  Plain text written over rich text makes it plain.  `Worksheet.runs(cell)` and
  `rich_runs(item)` read a rich item's runs; `Workbook.shared_item(index)`,
  `add_rich_string(item)`; `Worksheet.custom_height(row)` (a row Excel does not fit to
  its text: measured, a row without `customHeight` grows to its wrapped lines on open).
- `Styles.font(index)`, `Styles.wraps_text(index)` and `Styles.with_wrap_text(index)`
  (the cell's format with `alignment wrapText="1"`, reused or appended: measured, a line
  break shows only in a cell that wraps its text).

## 0.53.0 -- unreleased
- Formula freshness for datasets and sources (lane X, X3): `Dataset.cache`, how many of the
  formula cells a dataset's values were read from have each of `CACHE_STATUSES` --
  `cached_unverified` (as last saved, no known invalidation, never checked to be current),
  `invalidated` (an input changed since, or the file asks for a recalculation), `absent`
  (no cached value) -- and how many refer to another file (`CACHE_EXTERNAL`, `external`);
  zeros dropped, `None` when no value came from a formula, not compared (as `detail`), kept
  by `row_slice`. `Dataset.invalidated` says whether any cache was invalidated.
- A dataset computed from invalidated caches is stale from the start, whatever the
  versions say (`DatasetEntry.stale`, `CallContext.pending_stale`), and naming it warns
  with the reason (recalculate in the application, then derive again); undo cannot make
  it current. A `t:` source read from invalidated caches is `Rows.stale` too, so a placed
  record says `stale`. `Rows.describe()` (every placed value's `from`) carries `cache`
  when the values came from formula cells: the deck and document tools that take rows
  from a workbook pass the counts on without a change of their own.
- `ooxml_edit.sheets`: `Worksheet.shared_group(cell)` (`SharedGroup`: `si`, `master`, its
  `text` and `ref`, every member in sheet order) and `Worksheet.release_shared(cell,
  translate)`, which takes a shared group's master out of its group so that it can be
  written, every other member keeping its formula: the next member becomes the master
  (its formula moved by `translate(text, rows, columns)`, the caller's formula lexer; the
  group's `ref` the remaining members' rectangle), or, where that rectangle does not start
  at it, every member gets its own formula; every translation is made before anything
  changes, so a refused one leaves the sheet as it was. `written=`: the group's cells the
  same step writes over too, which are given nothing (none becomes the master). The
  calculation chain is dropped.
- `Worksheet.style(cell, inherited=True)`: for a position with no cell, the format a cell
  made there takes -- its row's (`s` with `customFormat`), else its column's (`col
  style`), else 0 (ECMA-376 Part 1, 18.3.1.73, 18.3.1.13); `write` still gives a new cell
  no format unless asked, so chart outputs are unchanged.
- Definitions are unchanged.

## 0.52.0 -- unreleased
- Render work over every worker at once (round 10, O1): `Call.run_all(fn, calls,
  timeouts=...)` (`tools.worker.run_all`) runs a call's chunks at once over the pool, the
  results in order, a chunk's exception in its place; `Call.parallelism` (the pool's size,
  1 in-process) says how many chunks to make. A format splits its views into that many
  chunks, not one per view (a task's fixed cost is a parse of the document).
- Workers by the CPUs the process may use: `Toolbox(workers=None)`, now the default
  (was 2), is `default_workers()` -- `available_cpus()`, the fewest of
  `os.process_cpu_count()` / `sched_getaffinity` and the container's CPU quota
  (`cgroup_cpu_quota()`: cgroup v2 `cpu.max`, else v1 `cpu.cfs_quota_us` over
  `cpu.cfs_period_us`, the smallest along the cgroup and those above it, rounded up),
  never `os.cpu_count()` alone -- at most `MAX_DEFAULT_WORKERS` (8). `workers=N` still
  sets it.
- Workers warmed, and their start no call's time: a new worker first runs the pool's
  `warm_up` tasks (`DocumentFormat.warm_up`: a format's imports, font index, the
  rasteriser's system-font load), bounded by `Limits.worker_start_timeout` (120 s);
  starting and warming one, and waiting while workers start, never count against a task's
  deadline, so a cold pool or a replaced worker cannot time a call out. `Toolbox(prewarm=True)`
  (the default) starts every worker in the background when the pool is first needed;
  `Toolbox.warm(wait=False)` and `WorkerPool.warm()` start them now (at the application's
  start). A warm-up that raises is logged and the worker serves. `Limits.render_timeout_for(n)`:
  `render_timeout` per 4 views begun, a task's deadline by its size.
- Images kept by their content (round 10, O3): `ContentStore` (`tools.rendercache`), bytes
  under a content key (`content_key(...)`) with a small meta, least recently used dropped
  past a byte budget, thread-safe. A toolbox has two, shared by its sessions and views:
  `Toolbox.images` (`Limits.image_cache_bytes`, 128 MB) and `Toolbox.decoded_fonts` (fonts
  a worker decoded from a document's embedded ones, handed to the next task;
  `Limits.font_cache_bytes`, 32 MB). Memory only: nothing is written to disk. Separate
  from the per-document `render_cache` (SVG reads, contact images), which keeps its 16
  entries. `DocumentEntry.render_keys` remembers each view's key by version (4,096 keys),
  `font_keys` its decoded fonts' digests; `code_stamp(*modules)` names the code that draws
  (versions and a digest of the files) for a key.
- `DocumentEntry.snapshot()`: the document's bytes made once per version (equal versions
  are equal content), so renders and checks of one version serialise it once.
- `Session.check_images(n)` / `Call.check_images(n)`: the per-call limit and the session's
  and round's image budgets asked before rendering, counting nothing, so a call that will
  be refused costs no render. `reserve_images` uses it.
- Definitions are unchanged.

## 0.51.0 -- unreleased
- `ooxml_edit.sheets` (lane X, O4): the lossless cell writer that edited a chart's embedded
  workbook, lifted into a public, optional subpackage for whole workbooks -- moved with its
  history (`git log --follow src/ooxml_edit/sheets/workbook.py`); `charts.workbook`
  re-exports it under the old names, and every chart edit writes the same bytes as before
  (checked by recording every package the ooxml-edit, pptx-agent and docx-agent suites
  write, before and after) -- except in an embedded workbook whose rows or cells have no
  `r`, which now keeps that spelling instead of being given one everywhere. Like `charts`, it is imported explicitly, imports only the core
  and lxml, never `charts` or `tools`, and registers nothing (`tests/test_neutrality.py`).
- `Worksheet.write(cell, value, style=None)`: text (always text -- `"=A1"` is stored as
  those characters), numbers, booleans, `date`/`datetime`/`time` as the serial number of
  the workbook's date system (`Workbook.date1904`; `date_serial`, ECMA-376 Part 1,
  18.17.4.1; a time-zone-aware value refused, not silently converted), a `Formula(text,
  value=None)` with its cached result or none, or `None` -- a blank that keeps the cell's
  format. Returns whether anything changed: writing what a cell holds is a no-op, and a
  workbook with nothing changed is written back as the bytes it was read from. Refused
  before anything is touched: text over 32,767 characters (`MAX_TEXT`), a number that is
  not finite, a position off the grid, a type not listed, and the master of a shared
  formula (`SharedFormulaError`, with the master, its range and index). A formula written
  or replaced removes the calculation chain with its relationship and content-type
  override, as a part change undo restores; `Workbook.request_full_calculation()` sets
  `calcPr fullCalcOnLoad="1"`, adding `calcPr` where CT_Workbook puts it (ECMA-376 Part 1,
  18.2.27) when there is none.
- `Worksheet.content(cell)`: what a cell holds as stored (`Content`: kind, value, formula
  text and type, format), for a caller that compares before it writes; a formula whose
  cached result is `""` is text, and `is_empty` stays "no value and no formula".
  `Worksheet.style(cell)`, `Worksheet.protected`.
- Built for a whole workbook: the shared strings indexed once (an appended item lands
  before the table's `extLst`); rows and cells without `r` positioned as the format says,
  and only what is written gets an `r`; rows found through an index built once per tree; the
  `dimension` grown with each cell written instead of recomputed from every cell; the
  shared-string count cached per sheet. The objects follow the live trees, so they keep
  working after an undo or a failed batch restores the package.
- `Styles` (`Workbook.styles`): a cell format's `number_format` (`NumberFormat(id,
  code)`), `is_date` (its own code read by its tokens, the built-in date and time ids of
  ECMA-376 Part 1, 18.8.30's all-languages table, `None` for the language-dependent ids),
  and `with_number_format(index, id_or_code)`: the cell's own format with only the number
  format changed, reusing an identical `xf` and `numFmt`, appending them otherwise (a new
  code from id 164 on).
- `outside_footprint(before, after, cells=None)`: the writer's mutation footprint written
  down (`sheets.footprint`'s docstring: the cells written, a row's `spans`, the
  `dimension`, shared strings and cell formats appended with their counts, the
  calculation chain removed with its relationship and override, `fullCalcOnLoad`) and
  checked: every difference between two packages outside it, by part and cell address,
  never a cell's text. An edited part is written by lxml, so its XML declaration and the
  order of a start tag's namespace declarations and attributes may differ from what was
  read; the check compares those as XML.
- `tests/sheets_synthetic.py`: a made-up corner shop's stock-list template built in code
  (title rows, headers on row 9, typed input rows with formula ids, a shared formula group,
  a hidden lookup sheet, cross-sheet formulas, defined names, standard and `x14`
  validations, conditional formats, merges, print settings with a printer-settings part,
  `mc:AlternateContent`, `x14ac` attributes and a made-up extension in `extLst`), with
  variants without `r`, with inline strings and in the 1904 date system.

## 0.50.0 -- unreleased
- Undo by caller (round 9): an `undo` whose call has a context -- through a view
  (`Session.view(context=...)`) or `dispatch(..., context=...)` -- takes back that
  caller's own latest change to the document, never another's. When it is the document's
  latest change, a plain undo, exact; else, in a format with undo scopes (decks), it is
  taken out from under the later changes as a scoped undo is, when none of them touched a
  part it touched and it changed no part every page shares; otherwise -- and always in a
  format without scopes (Word documents: one body every change shares) -- it is refused
  as `entangled`, nothing changed, the message naming the later change, its version and
  who made it ("t_add by agent=builder-2"), and saying to change it back with an edit or
  have that caller undo first; `details.later` lists them (`version`, `tool`,
  `context`). `scope` narrows it to the caller's changes there; `steps` stop at the first
  refusal, with a warning. Redo follows the caller too: its own latest undo, refused while
  another caller's undo is the next to redo. Two callers are the same when their contexts
  are equal; a call with no context (the session itself, or a view made without one)
  undoes as before, the latest change whoever made it. The result's `data` has
  `own: true`, and "nothing of yours to undo" says undo takes back the caller's own
  changes only. Undo is per document already (`doc` is required), so naming a document
  needs nothing new. Definitions are unchanged (no token cost).
- `DocumentEntry.owners` keeps who made each step (`StepOwner`: the call's context and
  tool, by the version it made; a batch is `batch`; a change made through the library's
  API directly has none), renumbered with a scoped undo; `DocumentEntry.batch(owner)`
  records it. `Session.undo` and `redo` take `caller=`; `SessionView.undo` and `redo` pass
  the view's context.
- `History.steps()` (each step's versions and parts), `History.undo_step(after)` and
  `redo_step(after)` (one step by the version it made, under `undo_in`'s rule), and
  `History.next_redo`; `Entangled.later` names the versions of the later steps in the way.

## 0.49.0 -- unreleased
- A contact sheet (round 9, N1): `render` takes `sheet` (`{"columns", "width"}`, `{}` for
  the defaults): up to 30 slides or pages as one image, tiled in order, each labelled.
  `sheet.width` is each tile's (160-1280 px, default 360; the call's `width` stands in when
  it is left out), `columns` 1-8, default 4 -- 1,480 px wide, under the standard tier's
  1,568. `tools.tiles` is the format-neutral half: `tiling_of` checks the options and
  refuses, naming the bound and what to do, more than `MAX_TILES` or an image over
  `Limits.max_image_edge` wide; `check_size` refuses one too tall, naming how many fit;
  `tiled_svg` places each tile's PNG at its own size with its label, for the format to
  rasterise once. +64 estimated tokens to every format's core (`render` is core); render is
  sent strict to OpenAI only, as before, `sheet` one more object there.

## 0.48.0 -- unreleased
- `edit_chart`'s scale without an axis (round 9, N2): `set_axis_scale` (and a format's
  `add` with `scale`) takes the value axis when it is the one axis of the chart that takes
  bounds and units -- a column, bar, line, radar or dot chart's
  (`charts.actions.scale_axis`). Where more than one could -- a scatter's x and y, a date
  axis, a secondary value axis -- or the scale holds only `reverse` or `number_format`,
  which any axis takes, it is still refused, now naming the axes it could be.
- Every "needs" refusal of the formatting actions names what to give: the series or
  categories (`charts.actions.choices`: the first 12 names, how many more, and the
  positions from 0 the tool takes too), the chart's axes, the gridlines or scale keys;
  `add` from rows without a series column names the source's columns.
  `charts.actions.category_labels` is the categories as a model names them (ISO dates for
  dates), for both formats' handlers. Definitions are unchanged.

## 0.47.0 -- unreleased
- A batch op's `comment` and `note` (round 9, N4): the model's words about an op, accepted
  beside `tool` and `arguments` and never run (a non-string as its JSON). Not in the
  definition -- the dispatcher takes them out before the schema check -- so no token cost;
  not echoed in the op's result. Kept in the call log: `CallRecord.comments`
  (`[{"op": 3, "comment": "..."}]`, 500 characters each at most; in `to_json` when there
  are any), whatever `log_arguments` says; never in the logger's line.
- What a name that is no tool means (round 9, N5): the error names the tool and action that
  do what the name says -- "no tool 'x_add_chart': use edit_chart with action add" -- with
  the best few in `valid_options` instead of every tool, at the top level and in a batch's
  op. `hints.tool_hints` reads the name's words against the tools' names and `action`
  enums (verbs read as the tool's, plurals, a format's prefix); a format adds the cases it
  misses as data, `DocumentFormat.tool_hints` (`{name: "tool"}` or `{name: "tool
  action"}`; `Toolbox.tool_hints` merges them). A hint outside the session's roles is named
  with the role it needs and kept out of `valid_options`; a tool batch cannot run is to be
  called on its own. An `action` a tool lacks: the tool's own action with the verb read
  ("did you mean 'insert_row'?"), else the other tool that has it
  (`hints.action_hints`). Definitions, the strict set and results of correct calls are
  unchanged.

## 0.46.0 -- unreleased
- Several agents on one shared session (round 8, M1). `Session.view(roles=...,
  context=...)` gives an agent a `SessionView`: the same session -- documents, undo
  history, refs, datasets, outputs, log and locks, read through -- with roles of its own
  and the application's context for its calls (`{"agent": "reviewer-1"}`).
  `Toolbox.dispatch` and `dispatch_many` take a view wherever they take a session, and
  `roles=` and `context=` per call. A view narrows, never widens: roles outside the
  session's (or the view's, for a view of a view or a call) are a `ValueError`, so whoever
  holds a view cannot give itself a role. Handlers read the context as `Call.caller` (a
  read-only mapping, empty by default; `call.context` stays the dispatch's internal
  `CallContext`) and the roles as `Call.roles`; every op of a batch sees both, and is
  held to the view's roles. `CallRecord` gains `roles` and `context`, set (and in
  `to_json`) only for a narrower or a described call. Undo still takes back the latest
  change whoever made it (documented). Definitions, the prompt, results and a session's
  own calls are unchanged.
- The role line follows the tools sent (round 8, M2): `Toolbox.system_prompt(tools=...)`
  -- names, `Tool` objects or provider definitions -- or `groups=` (as `definitions`')
  decides whether a reviewer is told it may comment, and accept tracked changes, over
  those tools rather than the whole toolbox; a reviewer sent no comments tool is no longer
  told it may comment. Without either, as before.

## 0.45.0 -- unreleased
- Roles (round 7, item 9; proposal P1, decided by the user 2026-10-17): additive toolsets
  a toolbox or session loads in any combination -- `review` (everything that does not
  change a document, plus adding and replying to comments), `resolve` (resolving,
  reopening and deleting comments), `accept` (accepting and rejecting tracked changes) and
  `edit` (every other change). Editing a comment's text is every role's (`ANY_ROLE` in
  `action_roles`; the user's decision: authorship is not known, and preventing it is not
  the library's aim).
  `Toolbox(roles=...)` sets the default of its sessions, `definitions`, `allowed_tools`
  and `system_prompt`, each of which takes `roles=` too (`Toolbox.session(roles=...)`,
  `Session.roles`). The default, all four, changes nothing: every definition, the strict
  set and the prompt are byte-identical to 0.44.0's (held by a snapshot of every
  provider's definitions for the three libraries, and by tests).
- Enforced in dispatch: a call outside the session's roles is `refused`, naming the role
  it needs and what to do instead, `details: {"role", "roles"}`; for a mixed tool's
  action of another role, `field: "action"` and the session's actions in
  `valid_options`. Inside `batch` each op is checked: a refused op fails alone (an
  atomic batch runs nothing); `batch` is in every role set.
- The role is per call: `Tool.role` (default `edit` for a changing tool, `review` for a
  reading one; `ANY_ROLE` for every set), `Tool.action_roles` (per action),
  `Tool.call_role` (a function of the arguments), with `Tool.roles`, `role_of`,
  `actions_for` and `for_roles`; `registry.role_set` reads a role set. The shared tools:
  `open_document`, `save_document`, `close_document`, `read_blob` and `batch` in every
  set (a reviewer saves the copy it commented), `new_document` and `undo` `edit`'s,
  `edit_chart`'s `read` `review`'s. `shared.COMMENT_ACTION_ROLES` and
  `shared.comment_role` give both comments tools their roles (a reply with
  `resolve: true` needs `resolve`; editing a comment's text any role).
- What a role set is sent: only its tools, a mixed tool's `action` enum only its actions;
  the core is the roles' core tools, and for a set without `edit` the tools marked
  `Tool.review_core` too (the comments tools, a deck's slide comparison). A set without
  `edit` gets a role line in the prompt (`prompts.role_line`; that it may comment only
  where a tool places comments, accept tracked changes only where a tool settles them);
  one with `edit` none. A `review_core` tool is promoted only for a set with one of its
  own actions, not only a comment's text edit.
  docs/TOOLS.md, "Roles", has the three typical setups.

## 0.44.0 -- unreleased
- Month and day names for a region of a measured language (round 7, L5). A date pattern's
  name tokens (`mmmm`, `ddd` ...) were refused for any locale not among the eight measured
  (`{{d:x/#1/day:d mmmm yyyy}}` in en-BE: "no month or day names are measured for
  en-BE"). Measured in Excel for Mac, 2026-10-17, Office names a region's dates in its
  language: en-BE and en-IE as en-US, fr-BE and fr-CH as fr-FR, de-BE and de-CH as de-DE.
  So a locale whose tag `dates.NAMES` does not list now takes its language's names (new
  `dates.LANGUAGES`: en, nl, de, fr, es, it; new `dates.names_tag`), in text facts, tables'
  and charts' `date_format` alike; its separators and day order stay its own, and no
  warning is added (it is what Office shows). Another language still refuses a name token,
  now naming the languages that have names. A chart's code takes the region's own id where
  Excel was seen storing it (`dates.LCIDS` gains en-IE 1809, fr-BE 80C, fr-CH 100C and the
  language-only en 9, nl 13, de 7, fr C), else the id of the tag whose names it shows
  (en-BE `[$-409]`, de-BE `[$-407]`, it-SM `[$-410]`). Tool definitions unchanged.
- Spanish is not one set of names (measured on Office for Mac's charts, 2026-10-17): es-MX
  (`[$-80A]`) abbreviates days `lun. mar. mié.`, the language-only `es` (`[$-A]`) months
  `ene feb mar ... set` and days `lun mar mié`, es-ES `lu. ma. mi.`; full names alike. So
  `dates.NAMES` lists es-MX and es, `LANGUAGES` has no es, and another Spanish region
  (es-AR) refuses name tokens, the message naming es-ES, es-MX and es. it-CH (`[$-810]`)
  and it (`[$-10]`) showed it-IT's names, as the rule says; `LCIDS` gains 80A, A, 810, 10.
- A chart Office re-saved reads its date code back (`Chart.category_format`,
  `category_dates`): measured, a code with a language tag (`[$-en-BE]...`) is re-saved as an
  `mc:AlternateContent` in the cache -- a `c16r2` Choice's `formatcode2` holding the tag,
  a Fallback `c:formatCode` of `[$]...` -- which read as no code; now the Choice's, else
  the Fallback's.

## 0.43.0 -- unreleased
- `batch` runs the application's own tools (round 7, L6). It always ran any batchable tool
  in the toolbox, so a tool the application registers there with the libraries'
  (`Toolbox(TOOLS + [add_note])`) runs inside a batch like theirs: validated against its
  schema, its `$refs` resolved, its summary and data (or its error, failing alone) in the
  op's outcome; `batchable=False` keeps one out, saying to call it on its own, as for
  `batch` itself. New tests hold that with made-up application tools. What changes is the
  refusal of a tool the toolbox does not have -- one the application dispatches itself:
  unless a name is close to it (a slip, which keeps its near names), the op's error now
  says `no tool 'x' that batch can run: it runs only the tools in valid_options; call x on
  its own`. docs/TOOLS.md says how to register such a tool, and that an atomic batch's
  rollback leaves what an application's tool did outside documents.
- `Result.nothing_given(done, field="items")` (round 7, L7): the result of a call given an
  empty list of work -- nothing changes, no undo step, and a warning `items is empty:
  nothing was added`. The format libraries return it for an empty `items` instead of
  refusing it. Tool definitions unchanged.

## 0.42.0 -- unreleased
- `add_series` on a scatter chart keeps a marker-only scatter line-less (round 6, item
  12): a template series whose line is off (`c:spPr/a:ln/a:noFill`, as `add_chart`'s
  scatter writes it) lost its `c:spPr` with the rest of its own look, so Office drew the
  new series' line (a scatter's `lineMarker` style). The new series now gets the line
  off (`a:ln w="19050"`, a new scatter's) and keeps the template's marker symbol and size
  in its own colour; a template with no `c:marker` of its own (Office's automatic marker)
  stays marker-automatic and line-less. A scatter with lines still drops the template's
  look and draws Office's automatic line. Previously a scatter series with its own
  marker went through 0.40's dot path and came back a 5 pt circle whatever its symbol,
  at a line chart's 2.25 pt (unfilled) width. Tool definitions unchanged.

## 0.41.0 -- unreleased
- `render` takes a deck's layouts and masters (round 6, R11): its `slides` description
  says so (`; layouts L2, masters M0`, the addresses ppt_edit_master and the add tools
  already take), and pptx-agent draws each as a new slide on it starts, its placeholders
  outlined and tagged. `slides` and `pages` no longer repeat "at most 4", which the
  tool's description states, so the definitions stay as long: pptx's core +1 estimated
  token (3,560 -> 3,561), every definition unchanged (12,592), docx's and xlsx's
  unchanged. render stays strict; its schema's types are unchanged.

## 0.40.0 -- unreleased
- A dot chart (round 6, R10): `add_chart(..., "dot", ...)` and `edit_chart` `chart_type:
  "dot"`, one marker per category on the value axis -- averages on a fixed 1-5 scale with
  `axis: "value"`, `scale: {"min": 1, "max": 5}` and `data_labels` in the one `add`. A
  native line chart, so Office edits it as one: each series' line off (`a:ln/a:noFill`)
  and a marker on, the group's `c:marker` 1. The marker is the one Office gives a new
  scatter chart's series (measured): a 5 pt circle in the series colour; `colors` colour
  the markers. Categories along the bottom: the schema has no horizontal line chart, and a
  scatter chart has no category labels. `Chart.is_dot` reads any line chart whose series
  all have no line and a marker as one, and the format libraries' `edit_chart read` reports
  its type as `dot`; `add_series` on such a chart keeps the new series a dot (its line off,
  its marker in the next colour). The `chart_type` enum gains `dot`; its description
  is unchanged.

## 0.39.0 -- unreleased
- A chart's dates read back as dates (round 6): `Chart.category_format` (the categories'
  `c:numCache/c:formatCode`, `None` for text categories) and `Chart.category_dates` (ISO
  dates, `2026-03-02`, where the categories are serials under a date code; `None`
  otherwise), the same two in `chart_model` (`Chart.data`) for such charts only, and
  `dates.is_date_code` to tell a date code from a number one. The format
  libraries' `edit_chart read` lists such categories as those dates, with the code, rather
  than as serials (`46083`), and takes one as a category's name. ISO rather than the
  code's own text: a model reads it whatever the locale and the viewer's separator, and
  it is the form `from` columns and Python dates already use. `chart.categories` is
  unchanged (the serials), as are the workbook's values. No definition changes.
  ooxml-common 0.17.0 keeps a copy of `dates.NAMES` and `LCIDS` to draw such categories;
  its tests check the two agree.

## 0.38.0 -- unreleased
- Date patterns (round 6, R9): `ooxml_edit.dates`, a pattern in Excel's date tokens (`d dd
  ddd dddd m mm mmm mmmm mmmmm yy yyyy`, either case; every other character literal, text
  in double quotes, other letters refused with the tokens) written as fixed text
  (`format_date`) or as a number format code as Excel stores it (`format_code`:
  `[$-413]d\ mmmm\ yyyy`, the locale's `[$-LCID]` from `LCIDS`, spaces and commas escaped,
  `/ - .` not). Month and day names (`NAMES`) measured in Excel for Mac, 2026-10-16, for
  en-US, en-GB, nl-NL, nl-BE, de-DE, fr-FR, es-ES and it-IT; a locale without them refuses
  a name token ("no month or day names are measured for en-AU ...") and takes the numeric
  ones. The data lives here, in the core (stdlib only), and nowhere else: ooxml-common
  needs no names until its chart drawing formats date codes. `serial` / `from_serial`
  (the 1900 date system), `date_of`, `DateCategory` (a date with its code).
- Text facts take a date pattern after the address: `{{d:x/#1/when:d mmmm yyyy}}`,
  `{{d:x/#1/when:dd/mm/yyyy}}` -- the pattern is what follows the first `:` past the
  source's prefix, so it may hold `/`. The fact's record keeps it as `format`. A date pattern
  on a value that is no date, and a number format on a date, are refused naming the
  other; a suffix that looks like a date pattern but is none (`:DD/MM/YYYY HH:mm`) is
  refused as that, with the tokens (`valid_options`) and the reference with
  `:d mmmm yyyy`; R5's number format refusal lists the date tokens too.
  `textfacts.date_suffix`, `is_date_pattern`, `strip_format`, `ATTEMPTED_DATE`.
- `sources.label(..., date_format=)` and `Rows.label(..., date_format=)` write a date (a
  Python date or datetime, or a `date`/`datetime` column's ISO text) in a pattern, anything
  else as before; `sources.date_pattern` checks one for the locale, `sources.no_dates`
  refuses a pattern for columns without a date, `Rows.date`, `sources.date_value` -- for the
  format libraries' tables placed `from`.
- `edit_chart` takes `date_format` (add from only): the category column's dates go to the
  kind's handler as `DateCategory` values in the pattern's code for the session's locale,
  and `charts.add_chart` keeps them as serials in a `c:numRef` whose `formatCode` is the
  code, the workbook's category cells in that number format (`numFmt` 165 beside a values
  format), the category axis source-linked with `c:auto` 0 (a text axis, one band per
  date). Office shows a `/` of such a code as the viewer's date separator and follows the
  prefix for names only (measured, docs/TOOLS.md). `set_values`/`add_series` from match a
  date column's rows to categories kept as serials. Refused: `date_format` without from,
  on set_values or add_series, on a scatter or bubble chart, or on a column with a value
  that is no date. Definitions: +29 estimated tokens to every definition that loads
  `edit_chart` (pptx-agent 12,506 -> 12,535, docx-agent 12,228 -> 12,256 before their own
  changes); no core change; `edit_chart` stays non-strict.

## 0.37.0 -- unreleased
- A document's own references in text (round 6, R8): `DocumentFormat.own_facts`,
  `{prefix: resolve}`, lets a format resolve `{{<prefix><name>}}` in the call's own
  document -- pptx-agent's `{{slide:s:262}}`, that slide's number. `resolve(document,
  name) -> (text, fields)`; a `$ref` in the name is replaced first (`{{slide:$intro}}`), and
  the fact's `source` is the prefix with the address (`slide:s:262`), never the ref. The
  fact is a `textfacts.Fact` like a dataset value's (`call.facts`, `data.facts`), so a format
  keeps it where it keeps the others. A document handle (`{{d1/slide:...}}`) and a number
  format are refused with the form; a document whose format does not take the prefix
  refuses it naming the formats that do ("slide: references are for pptx documents; a
  docx takes none"), since every loaded format's prefixes are known; a format may name a
  prefix with a text instead of a function to refuse it in its own words even when no
  format that takes it is loaded (docx-agent: "slide references are for decks"). Own
  references are
  never qualified as sources (`qualify_text`'s `own`). `textfacts.own_prefixes`,
  `own_prefix`, `own_name`, `own_record`, `not_own`. No change to any definition.

## 0.36.0 -- unreleased
- A text fact's bad number format says so (round 6, R5): `{{d:x/#1/avg:0.20}}` was "no
  column 'avg:0.20'". When the call fails and the part after the last `:` of the last
  segment looks like an attempted format (digits, `#`, `.`, `,`, `%`, spaces; or `.2f`) but
  is none `FORMAT` takes, the refusal is `invalid_arguments`: "'0.20' is not a number
  format a reference takes. Write :0 ..., :0.0 to :0.000000000 ..., :#,##0 or :#,##0.0
  ..., with % at the end for a percent (:0.0%); e.g. {{d:x/#1/avg:0.00}}", the forms
  (`textfacts.FORMS`) as `valid_options`. Only when the reference fails: a column named
  `avg:0.20` keeps resolving; a quoted last segment (`'avg:0.20'`) and a source's own
  colon (`d:2024`) are never read as a format. `textfacts.bad_format`,
  `textfacts.format_error`. No change to any definition.
- `find_text`'s and `replace_text`'s `slides` take slide ids as `render`'s and `check`'s do
  (round 6, R6): a number from 1, `s:<id>` or a `$ref` (strings with `orInteger`, "or ids
  (s:256, $ref)"; `refs` on `slides[]`); a deck format's handler reads them. Definitions:
  +6 estimated tokens per tool (pptx-agent's core 3,542 -> 3,560 with its own change,
  docx-agent's 2,558 -> 2,570, xlsx-agent's 3,490 -> 3,496 with find_text alone). The
  strict set changes: replace_text's `slides` items are strings. `shared._slides` is gone.

## 0.35.0 -- unreleased
- Forgiving the model's small mistakes (round 6, R2, R3, R7), at no cost in tool
  definitions: each is read with a line in the result's `warnings` (in a batch,
  `ops[i]: ...`), never guessed.
  - Ref names in camelCase or with hyphens (`recNow`, `scale-note`) were refused. A ref
    defined so is kept as the name it folds to (`rec_now`, `scale_note`; `HTTPLink`:
    `http_link`), "ref 'recNow' read as 'rec_now': write $rec_now"; `$recNow` in a
    declared target, or through `call.resolve_ref`, is read as `$rec_now` when that ref
    exists (or a failed op of the batch would have made it, which the refusal then says).
    `dispatch.fold_ref`. A folded name that already names another address is refused
    ("give ref 'rec_now' to point it here instead, or a name of its own"); the same address
    again is not. A name with spaces or other characters is refused as before, with the
    rule in words; one without a lowercase letter (`$A1`, `$A$1`) is never folded: it stays
    an address.
  - A tool name with a namespace before it, `functions.ppt_set_text` or a group's
    `charts.edit_chart`, is read as the tool (no tool name has a dot):
    `Toolbox.tool_named`, used for a call's name and for a batch's `ops[i].tool` alike
    (and for the documents a batch locks). Before, a batch op so named was "no tool"; at
    the top level a provider strips the prefix, and the dispatcher now reads it too.
  - An unknown key that is a property of the enclosing object, or of an object one level
    down, says where it belongs instead of "did you mean": "unknown argument
    'items[4].box.preset_name'; preset_name belongs one level up, on items[4], not in box",
    "x belongs one level down: items[0].box.x" (matched ignoring case and `-`/`_`).
    `valid_options` is unchanged.
  - `Tool.aliases` may name an argument inside an object by its path, `refs`-style:
    `{"items[].top": "space_before"}` reads `top` in each item as `space_before`
    ("items[11].top read as space_before"); a function alias gets that object. The
    definition check names a path that is in no object parameter.

## 0.34.0 -- unreleased
- Asset catalogues (round 5, T20): production shipped two tools of its own over a brand
  icon set (about 2,800 SVGs with a keyword index) to search and insert icons. A catalogue
  is now application configuration a format takes in its settings, and the tools it brings
  cost nothing in a toolbox without one.
  - `DocumentFormat.tools`: tools a format offers only by its configuration. The `Toolbox`
    adds each after its own tools; one named like a toolbox tool replaces it in its place
    (the same tool with an `asset` argument), which must serve that format's kind alone
    (`ValueError` otherwise).
  - `ooxml_edit.tools.assets`: the `AssetCatalogue` protocol (`search(query, limit)`,
    `get(id) -> (bytes, MIME type)`), `Asset`, and `FolderCatalogue(folder, index=None)`
    over a folder and a JSON or CSV index (`file` required and inside the folder; `id`,
    `name`, `keywords` optional; documented in the module and `docs/TOOLS.md`); `find` and
    `fetch` answer a tool's search and placement with tool errors (`not_found` naming the
    search tool, `refused` for a failing catalogue, `limit` over the session's limits).
  - Definitions: unchanged without a catalogue.

## 0.33.0 -- unreleased
- Text facts (round 5, T4), at no cost in tool definitions: tables and charts kept where
  their values came from, text did not -- "Average 2.2 across 41 items" in a title had no
  record of the 2.2, and builders expanded their own `{{...}}` placeholders before calling
  the tools.
  - A *reference* in a text argument, `{{d:by_topic/door/mean}}` (dataset, row by key or
    `#3` by position, column; `{{t:checks/N-1/rating}}` in another open document, its row
    read by the format's resolver), is written as the value before the handler runs: in the
    session's locale as a table cell gets it (`2,2` in nl-BE; a `percent` column as `25%`),
    or through a number format after it, `:0`, `:0.0`, `:#,##0.0`, `:0.0%` (half away from
    zero, the locale's separators). `\{{` writes one literally; `{{name}}` and other braces
    stay as they are (only `{{d:` and a loaded format's prefix start a reference). A
    reference to a missing dataset, table, row or column, to a blank, or to more than one
    value is refused with the field and what there is, so nothing is written half.
  - Declared per tool: `Tool.facts` (and `tool(facts=...)`, `Spec(facts=...)`) lists the
    text arguments, paths as `refs` writes them, an array of arrays of strings too
    (`data[]`). The dispatcher qualifies `t:` references with their document and holds its
    lock (as for `sources`), in a batch's ops too; `call.facts` (`call.facts_at(field)`)
    says what was written where, each a `textfacts.Fact` with its record; `call.fact(address)`
    resolves one. The result's `data.facts` lists each value: `from`, `text`, `sources`
    (and `stale`). `replace_text` declares `replace`.
  - `ooxml_edit.provenance`: a fact is a record's fields without `v` (`source` the reference
    as given, `format`, `text`, `document`, `version`, `sources`) and its place, `p` (the
    paragraph) and `at` (the character); `place`, `offset`, `facts_found` (the facts still
    where they say -- a reader's check) and `facts_after_edit(facts, old, new)` (the facts an
    edit leaves true, moved; a touched or bordering one is dropped), `edit_span`.
  - Chosen over a `from` per text run: a reference costs no parameter in any definition
    (a run-level `from` would add one to every text spec, in the core's `ppt_set_text`
    too, and a model would have to split its sentence into runs to use it), works in every
    text a tool declares, and says the value's place by itself. On by default: `{{d:` or
    `{{t:` in ordinary text is next to impossible, and `\{{` covers it.
  - Definitions: unchanged (the format libraries add a sentence to their prompts).

## 0.32.0 -- unreleased
- A chart added with its look in one call (round 5, T2): styling one new chart took a
  builder six turns (scale, data labels, series colours, size, text twice). `edit_chart`'s
  `add` took the scale, gridlines, legend position, number format and frame already; now
  also:
  - `data_labels: true` -- `show_data_labels` on every series (their format follows the
    values', `number_format`); `colors` -- each series' colour in order, as `format_series`
    takes it, filling a bar, area or bubble series, lining a line or radar series (a column
    chart's line series too) and colouring a scatter series' markers, a pie's slices one
    `format_point` each; the rest keep Office's; `style` with `part` -- `set_text_style`
    (font, size, bold, colour; all the chart's text without `part`).
  - The chart is the one `add` and those calls write, in that order (labels before text,
    which `set_text_style` needs), byte for byte, tested in both format libraries; one undo
    step. `charts.actions.add_style_actions` turns the arguments into the calls (and
    refuses a list longer than the series, a colour that is not one, a `style` key that is
    not the text's, `part` without `style` before anything is made); `apply_add_style` runs
    them on the new chart.
  - Chosen over a generic list of actions on one call (`then: [{action, arguments}]`):
    that costs 98 estimated tokens in every definition and needs a free object, so
    `edit_chart` could never be strict; this costs 62, and a call names a colour list
    rather than a list of calls. Several other changes go in one `batch`, which the
    description now says.
  - Definitions: `data_labels` and `colors` (alias `colours`) on `edit_chart` (deferred in
    both formats), "add" in `style`'s and `part`'s descriptions, "Batch several changes."
    -- 62 or 63 estimated tokens in every definition (pptx 12,403 -> 12,465, docx 12,153 ->
    12,216), none in the core. The strict set is unchanged: `edit_chart` is not in it.

## 0.31.0 -- unreleased
- A run of a dataset's rows as a source (round 5, T3), at no cost in tool definitions: a
  builder who wanted rows 1-4 of a 42-row dataset on one slide had no way to name them.
  - `call.source("d:by_region/#9:#16")` gives rows 9 to 16 (from 1, the last included),
    `d:by_region/#9:` row 9 to the last, `d:by_region/#9` one row; a run given the other
    way round is the same rows. Each value keeps its sources; `Rows.address` is the run
    (`d:by_region/#9:#16`) and a result's `from` rows count from its first row. A row past
    the last is refused, `invalid_arguments` with the run there is as `valid_options`
    (nothing is cut); any other part of a dataset is refused as before, now naming the
    forms that are sources.
  - `datasets.row_range(address)` (also `ooxml_edit.tools.row_range`): `("by_region", 9,
    16)`, `(..., 9, None)` for `#9:`, `None` for anything else; `datasets.rows_text`.
    `Dataset.row_slice(first, last=None)`: the rows with their sources, keys and types.
  - `sources.first_rows(address, count)`: a source's first rows as a row range
    (`t:sales/#1:#9`; `d:x/#10:#14` of `d:x/#10:`), `None` for a column or a key set: what
    the format libraries' tables offer when a source gives more rows than there is room
    for.

## 0.30.0 -- unreleased
- `describe` lists the session's datasets (round 5, T11), at no cost in tool definitions:
  a reviewer could not see where a figure placed from `d:<id>` came from.
  - Every kind's `describe` result gets `datasets` (and `more_datasets` past the latest
    20): each one's id, columns (at most 20, then `more_columns`), `types` (the labels it
    has, by column), `rows` (a count), `from` and, when true, `stale` and `closed`. Never a
    value. Whichever document is described, all of the session's are listed, those an
    earlier op of a batch defined too; a session without datasets describes as before.
  - `from`: `d3/t:sales` (the document and the address it was derived from), `d:base`
    (derived from a dataset), `d3` (no address given) or `application` (kept by the
    application with `session.keep_datasets`).
  - `Call.define_dataset(..., origin=None)`: the address in the source it was derived
    from; `DatasetEntry.origin`, `DatasetEntry.brief()`, `datasets.briefs()`,
    `Session.dataset_entries()`.

## 0.29.0 -- unreleased
- Dates placed in the locale wherever they come from (round 5, T18b), at no cost in tool
  definitions.
  - `sources.label(value, locale=None, *, type=None)`: in a `date`, `datetime` or `time`
    column, ISO 8601 text (`2026-03-01`, `2026-03-01T14:05:00`, `14:05:00.250`) is written
    as the Python value it names is (`3/1/2026` en-US, `1/3/2026` nl-BE, `3/1/2026 14:05`,
    `14:05`), so a workbook's date column (`t:`), a derive's dates and a dataset labelled
    `date` read like a Python date. Text that is not ISO 8601 (an elapsed time,
    `PT25H30M0S`; no such day) and unlabelled ISO text are written as they are.
  - `Rows.type(column)` and `Rows.label(row, column, locale)`: a source value's label by
    its column's type; `edit_chart`'s categories from a source use it, and the format
    libraries' tables from a source should.
  - `sources.moment_of(value, locale=None, *, written=False)`: the date or time a value is
    or names, ISO 8601 text, and with `written` text as `moment_text` writes it in the
    locale; `MOMENT_TYPES`.
  - `edit_chart` `set_values` / `add_series` from a date column match a category written
    ISO (an older file) or in the session's locale to the same row; a text column's
    categories match as text, as before.
  - Provenance records hold sources, not values: unchanged.

## 0.28.0 -- unreleased
- Dates in datasets (round 5, T18), at no cost in tool definitions.
  - `Dataset.from_records(rows, types=None, columns=None, *, keys=0)`: a dataset made in
    Python, of mappings (a missing name a blank; columns in the order first met) or of
    sequences with `columns`; `types` a mapping by column name or a label per column. A
    column no record has, an unknown label or a label for no column is refused.
  - `TYPE_LABELS`: the type labels the format libraries read -- `number`, `percent`,
    `date`, `time`, `datetime`, `text`, `bool`, `error` -- documented in `Dataset`, in
    `from_records` and in docs/TOOLS.md. The `Dataset` constructor still takes any label.
  - `sources.label` writes a Python `date`, `datetime` or `time` instead of giving `None`
    (a table cell refused it, a chart category called it blank): the day, month and
    four-digit year in the session locale's `date_order` joined by `/` (`3/1/2026` en-US,
    `1/3/2026` nl-BE), ISO 8601 for `ymd`, a time on the 24-hour clock (`14:05`, seconds
    when not 0), a datetime both; the wall-clock time of an aware one
    (`sources.moment_text`). Not Office's short-date format, which is not measured. A
    chart's categories match a date row by that text. ISO text is written as it is.

## 0.27.0 -- unreleased
- Aliases and refusals that say what would do (round 5, T6, T7, T8), at no cost in tool
  definitions: what follows is never sent to a provider.
  - Value aliases: `string(..., aliases={"shrink": "normal"})` writes `valueAliases` into
    the validation schema (`schema.BOUND_KEYWORDS`, stripped by `canonical`); the
    validator reads an alias, ignoring case, as its value, and the result's `warnings`
    say so (`frame.autofit 'shrink' read as 'normal'`). `validate_call(schema, arguments,
    notes)` collects those lines. An alias must read as an enum value and not be one.
  - Argument-name aliases: `Tool.aliases` (`tool(..., aliases=)`, `Spec(aliases=)`),
    `{alias: name}` or `{alias: function(arguments) -> name or None}`; the dispatcher
    renames before validating, with a warning (`from_columns read as columns`), in a
    batch's ops too (`ops[1]: ...`). An alias and its name given together are refused. A
    function may take one of the tool's own names whose value can only mean another (a
    list of column names given as a count): `registry.names_as(alias, name)` makes one for
    a parameter that takes no names. `edit_chart` reads `slide` as `target` when
    adding, and `from_columns` as `columns`.
  - "Did you mean": an enum value or an unknown argument name close to an allowed one
    adds "; did you mean 'x'?" to the message; `valid_options` is unchanged.
    `schema.close_match` ranks: the same ignoring case and `-`/`_`/spaces; a prefix either
    way, the longest shared one first (`rectangle`: `rect`, not difflib's `triangle`); an
    abbreviation, the option's letters in order (`rndrect`: `roundRect`); then
    `difflib`'s nearest at 0.6 (`nomral`: `normal`).
  - `key` on a changing tool that takes none says why: keys are for the tools that
    create something; this one is safe to repeat as it is.
  - A ref name that is not one says the rule in words -- a lowercase letter, then up to
    31 lowercase letters, digits or `_` -- and a name that would do (`catChart`:
    `'cat_chart'`; `dispatch.ref_rule`), instead of the pattern. A `$Name` in a target
    that is no ref name but folds to a known ref (`$catChart` for `$cat_chart`) is
    `not_found`, "did you mean $cat_chart?"; anything else (`$A$1`) stays an address.
  - Chart colours: Office's names for the theme colours are read as the scheme names --
    PowerPoint's `text1`, `text2`, `background1`, `background2` (`tx1`...), Word's
    `dark1`...`light2`, `hyperlink`, `followedHyperlink` (`dk1`..., `hlink`,
    `folHlink`), any case, and with a space before the number (`"Accent 1"`, `"Text 2
    lumMod=75%"`). `charts.format.scheme_color`, `split_color`, `THEME_COLOR_ALIASES`; the
    refusal still lists the scheme names, and mentions the Office ones.

## 0.26.0 -- unreleased
- `batch` applies its ops one by one by default (round 5, T1; TOOLS-ROADMAP D14, amended):
  an op that fails -- an unknown tool, arguments its schema refuses, an address, ref or
  document that names nothing, an error while it runs -- is rolled back alone and reported
  with its error, and the ops after it still run. What applied is one undo step and one
  version, and the checks run once on the result, as before. Schema-invalid ops no longer
  block the rest.
  - The result: when an op failed, `data` is `{"applied": n, "failed": [indexes], "ops":
    [...]}`, an outcome per op in order -- `op`, `tool`, and `summary`, `created`, `data`, or `error` (`code`,
    `message`; `field` as the op's own path, `tool` or `arguments.<path>`;
    `valid_options` and `details` when there are any). The summary names the failed ops
    to fix and resend. `ok` is true when at least one op applied; when none did, the
    result is an error with the first failure's code, field (`ops[i].<path>`) and valid
    options, and every outcome in `details` -- nothing changed, no version. When every op
    applied, the result is the one an all-or-none batch gave (`data: {"ops": [...]}`), so
    recorded transcripts of successful batches replay unchanged.
  - A `$ref` that a failed op would have made (any `ref` in its arguments) fails naming
    that op: "$chart1 was to be made by ops[3], which failed: fix that op and resend
    both", `details.op`.
  - `atomic: true` keeps the all-or-none batch exactly as it was: every op validated
    before any runs, the first failure rolls everything back.
  - An op naming a document that is not open fails alone (it no longer refuses the whole
    batch before it starts); an op past the batch deadline is reported "not run".
  - The rollback of one op: `History.savepoint()` (inside a batch, roll back to here if
    the code inside raises, the batch staying open; outside one, a batch) and
    `DocumentEntry.savepoint()` (the document's `savepoint()`, else its history's), after
    which the format's `DocumentFormat.restored` hook is called, so it drops its caches.
  - Definitions: `batch` gains the optional `atomic` and a reworded description, +22
    estimated tokens on the loaded core and on every definition (pptx-agent core 3,504 ->
    3,526, all 12,096 -> 12,119; docx-agent core 2,535 -> 2,558, all 12,131 -> 12,153).
    The strict set is unchanged: `batch` is never strict. The system prompt's batch line
    says the same.

## 0.25.0 -- unreleased
- Source addresses in the saved file: where values placed from a source came from is now
  kept in the document, not only reported in the result's `from`.
  - The record format, `ooxml_edit.provenance`: the result's `from` without the session's
    document handle (`doc`), its source without the handle (`t:sales`, not `d2/t:sales`),
    and the source document's name as `document`; versioned (`"v": 1`), compact JSON,
    keys that are `null` left out. `record(placed, document=...)`, `dumps`, `loads`
    (`RecordError` for what is not a record; a later version is returned as it is).
  - The chart part's copy: `edit_chart` with `from` writes the record into the chart part,
    an own `c:ext` (`urn:uvrt:ooxml-edit:provenance:v1`) at the end of
    `c:chartSpace/c:extLst`, holding `<uvp:record json="..."/>`, replaced where it
    stands so Office's own extensions keep their order. The record is merged: `add`
    replaces it; `set_values` from a source replaces the categories and that series'
    entry, `add_series` adds or replaces that series' entry (by name), the others stay,
    and an entry from another source names its own `source`, `document`, `version`.
    Without `from`, `rename_series` renames the entry, `remove_series`, `set_values` and
    `set_value` drop it, `rename_category` drops the categories' entry, and
    `add_category`/`remove_category` drop the record: it never names a series the chart
    no longer has, nor values no longer from their source. PowerPoint and Word for Mac keep an
    own `c:ext` there through open, edit and save (measured 2026-10-14).
    `ooxml_edit.charts.provenance(chart | package[, part])` reads it back, and
    `charts.store_provenance(package, part, record)` writes it. The wrapper finds the chart
    without knowing the format: a `Chart` notes its part, and `add_chart` the new one, as
    a `provenance.Holder` while a placement watches (`provenance.watch`).
  - The format's copy: `DocumentFormat.store_provenance(document, address, record)`
    (`None`: drop it), called inside the call's undo step after the placement succeeded -- by `edit_chart` with the
    chart's address (`add`: the last of `created`; otherwise the first of `changed`, else
    `target`), and by a format's own table tool through the new
    `Call.keep_provenance(address, rows, placed)`, which builds the record, hands it over
    and returns it (a reading call may not); `Call.store_provenance(address, record)`
    hands a record as it is, or `None` to drop one. `DocumentFormat.read_provenance(document) ->
    {address: record}` reads them back, through `Session.provenance(doc)`.
  - `edit_chart` with `action: "read"` returns the chart part's record as `from` when there
    is one. Definitions unchanged: no tool and no parameter added (0 tokens); every
    `strict_problems` stays empty.
  - Documented in docs/TOOLS.md, "Source addresses in the saved file".

## 0.24.0 -- unreleased
- `edit_chart` with `from` and no `columns`, as the table tools take `from` without
  `from_columns`: `add` places every column, the first as the categories (a scatter
  chart's x values) and each following one as a series; `set_values` and `add_series` take
  a source of exactly two columns (categories, then values) and still need `columns`
  otherwise. A defaulted column that is not all numbers is refused as before, naming the
  cell, with `field: "from"` and a message suggesting `columns`. An empty `columns` is now
  the default rather than a refusal. The `columns` description gains "default all" (+4
  estimated tokens on the all-loaded definitions). A minor version, not a patch: calls that
  were refused now succeed, and the shared definition text changes.

## 0.23.0 -- unreleased
- A format's hint for a source that names nothing: `DocumentFormat.source_hint(document,
  value) -> str | None`. When a declared source (`Tool.sources`) is a `d:` that is no
  dataset, or a `t:` (or `d2/t:`) address no open document takes, the dispatcher asks the
  format of the call's document (its first document argument) and appends the hint to the
  `not_found` message (`no dataset d:3; ...`) -- in a `batch` too, where the op's error and
  the batch's message both carry it. Code, field and `valid_options` are unchanged; the
  hook is asked only for such a refusal, never changes what resolves, and one that returns
  nothing or raises leaves the message as it was. For the Word library, whose drawings and
  charts are `d:<digits>`. Definitions unchanged (no tokens added).

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
