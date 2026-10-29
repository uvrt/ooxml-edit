# `ooxml_edit.tools`

The shared plumbing of an agent tool layer: an application imports a format library's
tools, and a model edits documents only through them -- no code execution, no raw XML, no
file system. Format-neutral and held to the core's rules by `tests/test_neutrality.py`;
standard library only, and no provider SDK. The plan it implements is
[TOOLS-ROADMAP.md](TOOLS-ROADMAP.md) (phase T0).

| Module | What it is |
| --- | --- |
| `tools.registry` | `Tool`, `ToolGroup`, the `@tool` decorator, and the parameter helpers (`string`, `integer`, `number`, `boolean`, `array`, `obj`) that build each tool's schema; a definition outside the common strict subset fails when the tool is made; the roles (`ROLES`: `review`, `resolve`, `edit`) a tool and each of its calls belong to |
| `tools.schema` | the common strict subset both providers accept, its checker, and the call validator, which also enforces what the subset cannot say (numeric bounds, lengths, "exactly one of") |
| `tools.adapters` | definitions for the Anthropic Messages API (strict on as many tools as the per-request limits allow, writing tools first; optional deferred loading and caching), the OpenAI Responses API (all required, optional ones nullable; optional namespaces and tool search) and Chat Completions; results as `tool_result` blocks, `function_call_output` items (images inside them, or in a user message after them), or tool messages plus a user message with the images; checkers for each provider's documented rules. Plain dicts |
| `tools.results` | the result envelope, error codes with `valid_options`, image token estimates, truncation and paging |
| `tools.session` | `Session`: documents opened from bytes (`d1`), inputs registered as blobs (`b1`), outputs handed to the application as bytes; a re-entrant lock per document, taken in a fixed order across documents; versions (`History.version`) and version-keyed caches; an injected clock |
| `tools.datasets` | `Dataset` (columns, rows, opaque sources per value) and `DatasetEntry` (its id, source document and version, `stale`): what a call keeps for the session under `d:<id>` (below) |
| `tools.sources` | `Rows` (a source's `Dataset`, where it came from, `stale`) and the helpers that qualify, resolve and report sources: rows a tool takes from a dataset or another open document's table (below) |
| `tools.limits` | size limits, magic-byte checks, image sizes read from headers, and a zip-bomb guard |
| `tools.worker` | worker processes for rendering and layout whose deadlines are kept: a worker past its deadline is killed and the call reports `timeout`; multiprocessing workers by default, subprocess workers where multiprocessing may not start children, in-process on request (below) |
| `tools.render` | one render pipeline for every format (review, S5, 0.65.0): a format brings a `Renderer` adapter -- how its bytes become each view's SVG (`lay_out`), what the pixels depend on besides the SVG (`fingerprint`), how an SVG becomes a PNG (`rasterise`, `rasterise_tiles`), what a new worker warms (`warm_up`) -- and the rest is shared: `draw` (the images a version already has, the snapshot, the chunks over the workers with the locks let go, what came back kept by content key and by version), the worker tasks (`worker_task`), the keys (`fingerprint`, `raster_key`), and every PNG checked (`png_size`, `checked_png`). Imports no renderer: the format passes its adapter |
| `tools.tiles` | contact sheets, format-neutral: `render`'s `sheet` options checked and narrowed to fit (`tiling_of`, `fit`, `check_size`) and the SVG that tiles rendered PNGs with their labels (`tiled_svg`), which a format rasterises |
| `tools.logs`, `tools.prompts` | call records with argument digests, not content; the shared system-prompt fragment (mechanics only, no house style) and a reviewer's role line |
| `tools.shared` | the one definition of each tool every format shares (open, new, save, close, undo, find, replace, render, check, charts, SmartArt, properties, reading an input's text) and the generic `batch`; a library adds its handler with `@shared.handler("render", kind=...)`, and lists `shared.SESSION_TOOLS`, whose handlers serve every kind. A parameter that only some kinds take is named in the spec's `kind_fields`, an enum value in its `kind_values`, and a parameter whose description names every kind's case has one per kind in `kind_descriptions` (`Tool.kind_fields`, `kind_values`, `kind_descriptions`, 0.70.0): a toolbox is sent the tool without the parameters and values of kinds its handlers do not serve, a one-kind toolbox each parameter described for its kind -- a Word toolbox's `check` has no `slides`, `boxes`, `spacing` or `contrast` and offers `include` `validate`, `reflow`, `fields`, `layout`, `app`; its `find_text`, `replace_text` and `render` no `slides`, and `stories` only `body` and `all`; a deck toolbox's no `pages`, `min_size`, `styles` or `range`; a workbook's `find_text` no `slides` or `stories`; `save_document`'s `clean` a document's alone; `undo`'s `scope` a deck's and a workbook's (a tool whose handler serves every kind, as `undo`'s does, serves the kinds its toolbox's libraries serve: `Tool.kinds_served`); a toolbox of several kinds all of them -- while a call is still validated against them all, so one that passes another kind's reaches the handler and its answer, as before. The one module that names formats, in its descriptions only |

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

**What a call changed (round 13, R2, 0.57.0).** Every result carries `result.touched`, for
the application and never in what the model reads (`to_json`, `to_text`): what the call
changed, one entry per thing, each with its `"doc"` and the rest in the format's own
addresses. It is derived from the parts whose bytes changed -- the history's own snapshots,
compared part by part (`History.watch`, a `history.Change`) -- so every tool is covered,
undo and redo included, and a tool written tomorrow without saying anything; addresses a
call only read never appear. A format maps the parts to its addresses with
`DocumentFormat.touched(document, change)` (decks: slides and shape ids, layouts, masters
and the slides built on them; pptx-agent's `pptx_agent.edit.touched`); without one each
changed part is `{"doc": "d1", "part": "word/document.xml"}` (`tools.part_entries`). A
`batch` reports the union in `touched` and each op's in `result.touched_by_op` (`{op index:
entries}`; an op that changed nothing, or failed and was rolled back, has no key); an
error, a read and a repeat that changed no bytes report `[]`. A live view re-renders what
`touched` names after each call.

```python
result = toolbox.dispatch(session, "ppt_set_text", {"doc": "d1", "items": [...]})
result.touched     # [{"doc": "d1", "slide": "s:258", "shapes": ["258.7"]}]
```

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
first failing op rolls the batch back and is named, with its valid options. An op may name
any tool the toolbox has that is not marked `batchable=False` -- the application's own too,
when it is registered with the libraries' (`Toolbox(TOOLS + [add_note])`, made with `tool`):
its schema is checked, its `refs` resolved, its result and errors reported like any op's.
A tool the application keeps outside the toolbox, dispatching it itself, cannot run inside a
batch; with no tool name near it, the op's error says to call it on its own. An atomic
batch rolls back documents only: what an application's tool did elsewhere stays done. The rollback of
one op is `DocumentEntry.savepoint()` -- the document's `savepoint()`, else its history's
(`History.savepoint`) -- after which the format's `restored` hook is called; a document
with neither keeps what a failing op did. Its ops' arguments are free-form objects, so
`batch` is the one tool never sent strict: each op is validated by the dispatcher against
its own tool's schema instead of by constrained decoding.

*Refs outside a call* (0.70.0, round 23, V5). The application reads a document's refs with
`session.refs("d1")` -- `{name: address}`, a copy taken under the document's lock, as they
are now (an undo takes back the refs of what it undoes) -- and resolves one with
`session.resolve_ref("d1", "$intro")` (`$name/rest` too; any other value as given;
`not_found`, the document's refs as `valid_options`, for a name it has not), as a handler
does with `call.resolve_ref`. From 0.71.0 (round 24, W5) it folds names as a call does: a
camelCase or hyphenated `$recNow` is `$rec_now` when that ref exists (silently: outside a
call no warning can be given), and `$REC_NOW` for a ref `rec_now` is `not_found` asking
whether `$rec_now` was meant. `fold_ref` is importable from `ooxml_edit.tools`. A view
(`session.view(...)`) reads the same refs.

**Aliases and refusals.** What a model naturally writes for an argument or a value is read
as the tool's own before validation, never sent, so it costs no tokens: a string's
`aliases` (`string(enum=[...], aliases={"shrink": "normal"})`, the validation schema's
`valueAliases`, matched ignoring case) and a tool's `aliases` for argument names
(`{"from_columns": "columns"}`, or a function of the arguments where the alias means the
name only in some calls; inside an object argument a path, `{"items[].top":
"space_before"}`). Each one read adds a line to the result's `warnings`
(`frame.autofit 'shrink' read as 'normal'`, `slide read as target`; in a batch,
`ops[i]: ...`); an alias given together with its name is refused. Only where the meaning is
the same. A refusal says what would do: an enum value or an argument name close to an
allowed one adds "did you mean ...?" (`valid_options` still lists them all), a `key` on a
changing tool without one says that keys are for creating tools and this one is safe to
repeat, and a ref name that is not one says the rule in words with a name that would do
(`cat chart`: `'cat_chart'`); `$Name` for a known ref, capitalised, says which it means.
An unknown key that is a property one level up or down says where it belongs
(`items[4].box.preset_name`: "preset_name belongs one level up, on items[4], not in box";
`items[0].w`: "w belongs one level down: items[0].box.w") instead of "did you mean".

Round 6 reads three more of a model's small mistakes, each with a warning. A ref name in
camelCase or with hyphens (`recNow`, `scale-note`) is kept as the name it folds to
(`rec_now`, `scale_note`; `ooxml_edit.tools.fold_ref`), and `$recNow` in a target, or in
`call.resolve_ref`, is read as `$rec_now` when that ref exists -- a name with no lowercase
letter (`$A1`) stays an address. A folded name that already names another address is
refused rather than moved. A tool name with a namespace before it
(`functions.ppt_set_text`, `charts.edit_chart`: no tool name has a dot) is read as the
tool's, in a call and in a batch's `ops[i].tool` alike (`Toolbox.tool_named`).

Round 9 (0.47.0). *A batch op's comment* (N4): an op may carry `comment` or `note` beside
`tool` and `arguments` (`{"tool": "...", "arguments": {...}, "comment": "fix title"}`) --
the model's words about the op. Neither runs nor reaches the tool; a value that is not a
string is kept as its JSON. They are not in the definition (the dispatcher takes them out
before the batch's schema is checked), so they cost no tokens, and the op's result does not
echo them. The call's `CallRecord.comments` keeps them, `[{"op": 3, "comment": "fix
title"}]` (at most 500 characters each), whatever `log_arguments` says; the logger's line
never has them. Outside an op, `comment` is still an unknown argument.

*What a name that is no tool means* (N5): instead of every tool's name, the error names the
tool, and the action of it, that does what the name says -- "no tool 'x_add_chart': use
edit_chart with action add" -- with those tools first in `valid_options` (at most five:
then the names close to it), in a call and in a batch's op alike (`hints.tool_hints`). The
name's words are read against each tool's name and the values of its `action` enum: a verb
for another (`insert`, `create`, `remove`, `update`, `get`, `copy`...) as the one the tool
uses, a plural as its singular, a format's prefix (the first word of three tools or more,
or another before the rest) as no word of the thing. The best of: the formats' own table, a
tool whose name says the same (`x_insert_picture`: `x_add_picture`), an action whose words
are in the name with a word of the tool's left over (`x_remove_pages`: `x_pages` `delete`),
an action of two words that is the whole name (`x_add_row`: `insert_row`), a tool whose name
starts the name (`x_read_cells`: `x_read`), an action only one tool has. A format adds what
that rule misses as data, `DocumentFormat.tool_hints` (`{"x_delete_box": "x_arrange
delete"}`), a key standing for its other spellings too. A hint outside the session's roles
is named with the role it needs ("... does that, and needs the accept role; this session has
review") and is not in `valid_options`; inside a batch, a tool batch cannot run is to be
called on its own. An `action` a tool lacks gets the same reading: the tool's own action
with the verb read (`add_row`: "did you mean 'insert_row'?"), else the other tool whose
action it is ("use x_changes with action accept"). Wording only: the call still fails, and
no definition changes.

*A contact sheet* (N1, 0.49.0): `render` with `sheet` (`{"columns": 4, "width": 480}`, or
`{}` for the defaults) returns one image of up to 30 slides or pages (`tiles.MAX_TILES`),
tiled in order, row by row, each labelled under it with what the other tools call it (a
format chooses the label: `3 · s:258`, `page 3`). `sheet.width` is each tile's width, 160-1280
px, default 360 (the call's `width` stands in when the sheet gives none); `columns` is 1-8,
default 4, and never more than the tiles. With the defaults the image is 1,480 px wide,
within the standard tier's 1,568 px, so no provider scales it down. Each tile's PNG is
placed at its own pixel size, so the format renders at the tile width (its cache serving
repeats) and rasterises `tiles.tiled_svg`'s document once. Refused, naming the bound and
what to do: more than 30 ("render the first 30, and the rest in another call"). Round 10,
O4 (0.55.0): tiles that would make the image over `Limits.max_image_edge` wide or tall are
drawn narrower instead -- the widest that fit those columns (`tiling_of`) and, for the
tiles' shapes, that height (`tiles.fit`) -- and the result's `sheet.shrunk` (`{"from",
"why"}`) and summary say from what width and why; only tiles that would not fit even at
160 px are refused (how many columns or tiles would). A call's `width` over 1,280 px
standing in for the tile's is taken as 1,280, said the same way. A sheet is one image in the per-call and session image counts. The
definition grows by 64 estimated tokens (`sheet`: two optional integers); the module
`tools.tiles` is format-neutral and stdlib only.

*One render pipeline* (review, S5, 0.65.0). pptx-agent and docx-agent each had their own
copy of the plumbing around their renderer -- the worker tasks, the cache keys, the code
stamp, the warm-up, the contact image's rasterising, the PNG size -- and the copies had
drifted (one checked the PNG signature, the other did not). `tools.render` is the one copy;
a format says only what is its own, in a `Renderer`: `name` (the keys' namespace),
`whole_document`, `lay_out(data, items, width, font_dirs, locale, state)` returning a
`Layout` (the views' SVGs, the raster fingerprint, the raster arguments, the format's
facts), `key_text` (the part of an SVG the pixels depend on), `rasterise(views, raster)`,
`facts(layout)`, `rasterise_tiles(svg, font_dirs)`, `warm_up()`; `RendererBase` has the
defaults. The adapter is a module-level object handed to the worker tasks as an
argument, so subprocess workers run it too. Two shapes of work: views that stand alone
(a slide) are laid out and drawn per chunk, one chunk per worker (`render_views`); a
document laid out whole (flowing text) is laid out once (`lay_out_views`) and its views
rasterised over the workers at once (`rasterise_views`). What a format keeps between calls
goes to the workers as `state` and comes back as each task's facts (`draw`'s `on_facts`):
the decoded embedded fonts of a deck, the layout of a document. Another format's renderer plugs in the same way.

**The libraries' refusals and their codes** (0.66.0, review S9; `ooxml_edit.errors`). A
value a library refuses -- the last node of a diagram to remove, a cell reference that is
not one, a legend position it has no word for -- raises an `ArgumentError` (a
`ValueError`); a value of the wrong type an `ArgumentTypeError` (an `ArgumentError` and a
`TypeError`); a number, date or length out of its range, or not finite, a `UnitError` (an
`ArgumentError`). The dispatcher gives the model `invalid_arguments` for an
`ArgumentError` and `unit` for a `UnitError` (`BASE_ERRORS`), in every format; a format's
`DocumentFormat.errors`, or the application's `error_map`, may give one of their
subclasses another code, the closest class in the exception's MRO winning. A format
library derives its own refusals from these (pptx-agent's `ArgumentError` is one), so a
refusal is one class family whichever library raised it. A plain `ValueError` -- Python's
own, a broken document's, or a library subclass outside the family -- is still `unit`, as
before: the formats' unmapped `ValueError` subclasses keep their code.

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
read as dates on its own. A labelled column's ISO text -- what a workbook's date cells
(`t:`) and a derive's dates are -- is placed as the Python value it names is
(`sources.label(value, locale, type=...)`, `Rows.label(row, column, locale)`), so one deck
or document never mixes `1/3/2026` and `2026-01-20`; unlabelled ISO text, and text in a
date column that is not ISO 8601, is written as it is. A chart's categories are matched to
a date column's rows by the date, whether they were written ISO (an older file) or in the
session's locale (`sources.moment_of`). A provenance record names sources, never values,
so it is unchanged.

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
| its values read from invalidated formula caches (`Dataset.cache`) | stale from the start, whatever the versions say; no undo makes it current |

**Formula caches** (0.53.0). A producer whose values come from formula cells -- a
workbook's cached results -- counts the caches it read in `Dataset.cache`, by status
(`CACHE_STATUSES`: `cached_unverified`, as last saved and never checked to be current;
`invalidated`, an input changed since or the file asks for a recalculation; `absent`, no
cached value) and how many refer to another file (`external`). `Rows.describe()` -- the
`from` of every placed value -- carries the counts, and a `t:` source read from an
invalidated cache is `stale` too, so the deck and document tools pass both on unchanged.

A dataset derived from another (`define_dataset(..., source=entry)`) inherits that one's
source and version, and goes stale with it. `Limits.max_datasets` (50) bounds a session and
`Limits.max_dataset_cells` (50,000 values; sources not counted) one dataset; the
application may forget one with `session.drop_dataset(address)` (no tool does). Datasets
stay in the session and are never sent to worker processes; a handler that needs one there
passes the `Dataset`, which pickles when its `detail` does.

`describe` lists the session's datasets, whichever document it describes (datasets are the
session's, and a figure in a deck placed from one is then seen to have a source; there is
no separate session-level describe). Every kind's result gets `datasets` -- each one's id,
columns (the first 20, the rest counted in `more_columns`), the type labels it has
(`types`, by column), its row count and `from`, and `stale` or `closed` when true -- and
`more_datasets` past the latest 20; never a value. `from` is the document address it was
derived from (`d3/t:sales`: `define_dataset(..., origin="t:sales")`), the dataset it was
derived from (`d:by_region`), the document alone when the producer gave no address (`d3`),
or `application` for one the application kept itself (`session.keep_datasets`). A batch's
`describe` lists those its earlier ops defined. No key when there are none, so a session
without datasets describes as before.

**Sources: charts from rows.** A tool that places values may take them from a *source*
instead of literal arrays (`Tool.sources`, paths as for `Tool.refs`): a dataset, or an
address in another open document that the document's format turns into rows -- a table
`t:sales`, or `d2/t:sales` naming the document when several could. The dispatcher qualifies
such an address with its document (`t:sales` -> `d2/t:sales`; with several candidates it is
`ambiguous`, the qualified forms its `valid_options`) and holds that document's lock for the
call, for reading only: no undo step, no version, no checks, even inside a `batch`. The
handler reads either with `call.source(value)`, a `sources.Rows`: the `Dataset`, the source
as resolved, its document and version, and `stale` for a dataset (and for an address whose
values came from invalidated formula caches). A dataset's source may be
a run of its rows, written as the format libraries write a row by position:
`d:by_region/#9:#16` (rows 9 to 16, from 1, the last included), `d:by_region/#9:` (to the
last row), `d:by_region/#9` (one row); each value keeps its own sources, and a row past the
last is refused with the run there is, never cut (`row_range(address)`,
`Dataset.row_slice(first, last)`). A format's own addresses take such runs as its grammar
does (xlsx-agent: `t:sales/#9:#16`).

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

**Text facts.** A figure in a sentence comes from a source too. A text argument a tool
declares (`Tool.facts`: `ppt_set_text`'s texts and runs, a table's cells, `replace_text`'s
`replace`, Word's text and Markdown) may hold *references*, `{{` an address of one value
`}}`, which the dispatcher writes as the value before the handler runs:

```text
"Average {{d:by_topic/door/mean:0.0}} across {{d:by_topic/door/count}} items"
-> "Average 2,2 across 41 items"                           (session locale nl-BE)
```

- The address: a dataset, the row (its first column's value, as a table cell writes it, or
  `#3` by position) and the column (`d:by_topic/door/mean`); `d:total/value` for a one-row
  dataset, `d:total` for one value; `t:checks/N-1/rating` in another open document, the row
  read by its format's resolver (`t:checks/N-1`), `d2/t:...` naming the document. A key or
  column holding `/` is quoted, `'A/7'`.
- The text: as a table cell gets the value (`sources.label`: the locale's decimal
  separator, at most 15 significant digits, a date in the locale's day order), a `percent`
  column's value as a percent; or through a number format after the address -- `:0`,
  `:0.0` (up to nine decimals), `:#,##0.0` (the locale's thousands separator), `:0.0%` --
  rounded half away from zero on the decimal shown. Datasets carry no number format; a
  derive that rounds gives rounded values already. A suffix after the last `:` that looks
  like a format but is none (`{{d:x/#1/avg:0.20}}`, `:0,0`, `:.2f`) is refused as that --
  "'0.20' is not a number format a reference takes", the forms, the reference with the
  nearest one (`:0.00`), the forms as `valid_options` -- not as "no column 'avg:0.20'";
  a column whose whole name is `avg:0.20` still resolves, quoted or not.
- A date through a date pattern after the address (0.38.0, round 6, R9):
  `{{d:events/#1/when:d mmmm yyyy}}` is `2 March 2026` (`2 maart 2026` in nl-BE),
  `{{d:events/#1/when:dd/mm/yyyy}}` `02/03/2026`. See "Date patterns" below. The pattern
  is everything after the first `:` past the source's prefix, so it may hold `/`. A date
  pattern on a value that is no date ("a date pattern (:d mmmm yyyy) is for dates") and a
  number format on a date ("... for a date write a date pattern instead, of d dd (day),
  ...") are refused, each naming the other; a suffix that looks like a date pattern but is
  none (`:DD/MM/YYYY HH:mm`, `:%d-%m-%Y`) is refused as that, with the tokens and the
  reference written with `:d mmmm yyyy` and `:dd/mm/yyyy`. The number format refusal lists
  the date tokens too. Without a pattern a date is written as before, in the locale's day
  order.
- Only `{{d:` and a loaded format's prefix (`{{t:`) start a reference; `{{name}}` stays as
  it is, and `\{{d:x}}` writes `{{d:x}}`. On by default, no flag. A reference that names
  no dataset, table, row or column, a blank, or more than one value refuses the call with
  the argument as `field` and what there is as `valid_options`: nothing is written half.
- The result: `data.facts`, each `{"from", "text", "sources"}` (and `stale`). The handler
  has `call.facts` (each a `textfacts.Fact`: argument, place in its text, record) and keeps
  them where its format keeps provenance: a fact is a record's fields without `v`, plus its
  place `p` (paragraph) and `at` (character). `provenance.facts_after_edit(facts, old, new)`
  keeps the facts an edit leaves true (moved with the text; a touched or bordering one is
  dropped) and `provenance.facts_found(facts, text)` the ones still where they say -- what a
  reader checks, since another application may have edited the text since.
- A document's own references (0.37.0): a format may resolve a prefix in the call's own
  document (`DocumentFormat.own_facts`, `{prefix: resolve}`) -- a deck's
  `{{slide:s:262}}`, that slide's number. `resolve(document, name)` gives the text and the
  fields the fact adds; `name` comes with a `$ref` replaced (`{{slide:$intro}}`), and the
  fact's `source` is the prefix and that address. No document handle (`{{d1/slide:...}}`)
  and no number format. Keeping the text current is the format's (a deck renumbers its
  slide facts as slides move). Every loaded format's prefixes are known, so a document
  whose format does not take one refuses it: "slide: references are for pptx documents; a
  docx takes none" -- or in its own words when its `own_facts` names the prefix with a
  text instead of a function (Word's "slide references are for decks"), which works with
  no deck format loaded too.

**Date patterns** (0.38.0, round 6, R9; `ooxml_edit.dates`). A text fact, a table placed
with `from` (the format libraries' `date_format`) and a chart added with `from`
(`edit_chart`'s `date_format`) take a pattern in Excel's date letters:

| token | writes | token | writes |
|---|---|---|---|
| `d` / `dd` | 2 / 02 | `m` / `mm` | 3 / 03 |
| `ddd` / `dddd` | Mon / Monday | `mmm` / `mmmm` | Mar / March |
| `yy` / `yyyy` | 26 / 2026 | `mmmmm` | M (the name's first letter) |

Letters in either case; every other character is written as given, text in double quotes
(`d "de" mmmm "de" yyyy`) or one character after `\`; any other letter, or a run that is
no token (`ddddd`, `yyy`), is refused with the tokens. Month and day names are measured
in Excel for Mac, 2026-10-16, with an explicit locale code in the format (`[$-413]mmmm`),
for en-US, en-GB, nl-NL, nl-BE, de-DE, fr-FR, es-ES and it-IT (`dates.NAMES`, which is
`ooxml_common.dates.NAMES` from 0.64.0, as `LANGUAGES` and `LCIDS` are): en-US and
en-GB alike (`Mar`, `Mon`), nl `mrt`/`ma`, de `Mär`/`Mo`, fr `mars`/`janv.`/`lun.`, es
`mar.`/`lu.`, it `mar`/`lun`; `mmmmm` is the full name's first letter (lower case in nl,
fr, es and it). **A region shows its language's names** (0.44.0, round 7, L5; measured in
Excel for Mac, 2026-10-17: en-BE and en-IE named a date as en-US, fr-BE and fr-CH as
fr-FR, de-BE and de-CH as de-DE): a session whose tag is not listed takes its language's
names (`dates.LANGUAGES`: en, nl, de, fr, it -> en-US, nl-NL, de-DE, fr-FR, it-IT; it-CH
measured too), any region; the locale's separators and day order stay its own. Spanish is
not one set (measured on Office for Mac's charts, 2026-10-17): es-MX abbreviates days
`lun. mar. mié.`, `es` months `ene ... set` and days `lun mar mié`, es-ES `lu. ma. mi.`, so
es-ES, es-MX and es are listed each and another Spanish region is refused. A session in
another language refuses a name token ("no month or day names are measured for pt-BR
(they are for the languages en, nl, de, fr and it, in any region, and for es-ES, es-MX and
es); write the date with numbers"), never guessing; the numeric tokens work in every
locale.

- **Text** (`dates.format_date`): fixed text, its separators exactly the pattern's in every
  locale -- `dd/mm/yyyy` is `02/03/2026` in de-DE too.
- **A chart** (`dates.format_code`, `dates.DateCategory`): the categories are kept as dates
  -- serials of the 1900 date system in a `c:numRef` -- in a number format code as Excel
  stores it, the locale's id first and spaces and commas escaped:
  `[$-413]d\ mmmm\ yyyy` (`dates.LCIDS`, each measured accepted and honoured: 409, 809,
  413, 813, 407, 40C, C0A, 410; and what Excel stored for `[$-fr-BE]` 80C, `[$-en-IE]`
  1809, `[$-fr-CH]` 100C, `[$-en]` 9, `[$-nl]` 13, `[$-de]` 7, `[$-fr]` C; on re-saving a
  chart `[$-es-MX]` 80A, `[$-it-CH]` 810; `[$-A]` and `[$-10]` honoured). A region with
  no measured id takes that of the tag whose names it shows: en-BE `[$-409]`, de-BE and
  de-CH `[$-407]` (Excel itself stores `[$-en-BE]` as `[$]` plus an `x16r2:formatCode16`
  `[$-en-BE]`, and a chart re-saved by Office as an `mc:AlternateContent` whose `c16r2`
  Choice's `formatcode2` holds the tag and whose Fallback is `[$]`; an id avoids both, and
  the prefix changes the names only, which are the same). A chart read back takes such a
  Choice's code, else the Fallback's. The workbook's category cells hold the same serials in
  that format; the category axis shows them source-linked and stays a text axis (`c:auto`
  0), one band per date. **Measured caveat** (Excel for Mac, 2026-10-16, switching the
  macOS region): Office shows a `/` in a kept code as the viewer's date separator
  (`dd/mm/yyyy` reads `02-03-2026` in a Dutch (Netherlands) region, `02.03.2026` in a
  German one; a `-` stays `-`), and the `[$-LCID]` prefix changes the names only, never the
  separator or the order. The built-in short date (number format 14) follows the viewer's
  region (`2/03/2026` nl-BE, `02/03/2026` en-GB, `02-03-2026` nl-NL, `02.03.26` de-DE,
  `02/03/2026` fr-FR, `2/3/26` es-ES, `02/03/26` it-IT, `3/2/26` en-US), so no pattern
  stands for "the locale's short date" and none is offered. A chart read back
  (`action: "read"`) lists such categories as ISO dates (`2026-03-02`) with the code in
  `category_format` (0.39.0, round 6; `Chart.category_dates`, `dates.is_date_code`, which
  is `ooxml_common.numfmt.is_date_code` from 0.64.0: the code's first section decides): a
  model reads the date without decoding a serial or a viewer's separator, and names a
  category by it (`category: "2026-03-02"`). pptx2svg and docx2svg draw them as Office
  does (ooxml-common 0.17.0: `5 januari 2026` for `[$-413]d\ mmmm\ yyyy`).
- Without a pattern nothing changes: dates are written as text in the locale's day order.

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

**Roles: who may change what** (0.45.0, round 7). A toolbox or a session loads *roles*,
additive toolsets, in any combination; the default, all four, is the toolbox as it was
without roles -- the same definitions byte for byte, the same strict set, the same prompt.

| Role | Its calls |
| --- | --- |
| `review` | everything that does not change a document: describe, read, find, render, check, compare, inspect, a chart's `read`, a mixed tool's `list`; adding comments and replying to them |
| `resolve` | resolving, reopening and deleting comments (a reply with `resolve: true` too) |
| `accept` | accepting and rejecting tracked changes (`word_changes` accept and reject; listing them is `review`'s) |
| `edit` | every other change |

Every role set has the session's documents -- `open_document`, `save_document`,
`close_document`, `read_blob` -- and `batch`: a reviewer saves the copy it commented.
Editing a comment's text is every role's too (the user's decision, 2026-10-17: who wrote a
comment is not known, and keeping a model from rewording one is not the library's aim), so
every role set that has a comments tool can edit comment text.
`new_document` and `undo` are `edit`'s (undo steps back through any role's changes).

```python
reviewer = Toolbox(TOOLS, formats=[FORMAT], groups=GROUPS, roles=["review"])
tools = reviewer.definitions("anthropic")             # the review tools only
system = reviewer.system_prompt(extra=HOUSE_RULES)    # with the reviewer's role line
session = reviewer.session()                          # a call outside review is refused

# or one toolbox (one worker pool) for every agent, roles per session:
session = toolbox.session(roles=["review", "resolve"])
tools = toolbox.definitions("anthropic", roles=["review", "resolve"])
```

Four typical setups:
- **Builder** -- `roles=None` (all) or `["review", "edit"]`: reads and changes the document;
  without `resolve` it leaves the reviewers' threads as they are.
- **First-pass reviewer** -- `["review"]`: reads, checks, compares and renders; reports each
  finding with the call that would fix it (tool and arguments) for a builder to apply in one
  `batch`, and may add comments and replies; saves the commented copy.
- **Second-pass reviewer** -- `["review", "resolve"]`: checks the builder's fixes, resolves
  the threads they settle, reopens the others.
- **Editor-in-chief** -- `["review", "accept"]` (with `"resolve"` to close the threads too):
  reads the tracked changes and accepts or rejects them, changing nothing else.

*Enforced by the dispatcher.* A call outside the session's roles (`Session.roles`) is refused
with `refused`, saying which role it needs and what to do instead (`ppt_set_text needs the
edit role; this session has review. Do not make the change: report it with the call that would
make it (tool and arguments).`), `details: {"role", "roles"}`; a tool in none of the roles'
toolsets is refused before its arguments are checked. The role is per call, not per tool:
`Tool.action_roles` gives a mixed tool's actions their role (`edit_chart`'s `read` is
`review`'s, the rest `edit`'s), `Tool.call_role` a call whose arguments decide it, and for an
action of the wrong role `valid_options` lists the actions the session may call. Inside
`batch` each op is held to the roles: a refused op fails alone, as any failing op does, and
an `atomic` batch with one runs nothing; `batch` itself is in every role set.

*What is sent.* `definitions(roles=...)` sends only the roles' tools, and a mixed tool's
`action` enum only their actions (a reviewer's `edit_chart` offers `read` alone, its
comments tool `list`, `add`, `reply`, `edit`), without the parameters only other actions take
(`Tool.action_fields`: a reviewer's tracked changes, `list` alone, have no `ids` or `all`):
definitions differ per role set, each stable for the cache. The core is the roles' core tools; a role set without `edit` also loads its
reviewing tools there (`Tool.review_core`: the comments tool, a deck's slide comparison,
Word's tracked changes) when it has one of their own actions (not only a comment's text
edit), which would otherwise wait in their groups. `allowed_tools(roles=...)` and
`system_prompt(roles=...)` follow: a role set without `edit` gets one role line after the
formats' fragments (`Role: review. Do not change the document; ... Report each finding with
the call that would fix it (tool and arguments). You may add comments and reply to them.`;
the last sentence only where a tool places comments; `You may also resolve, reopen and
delete them.` with `resolve`, `You may also accept and reject tracked changes.` with
`accept` where a tool settles them; a set without `review` is told only what it may do),
one with `edit` none. The libraries list each format's role sets' token counts.

*The prompt follows the tools sent* (0.46.0, round 8, M2). Whether the role line says a
reviewer may comment, and may accept and reject tracked changes, is decided over the
tools the application sends: `system_prompt(roles=..., tools=[...])` takes them as names,
`Tool` objects or the definitions themselves (any provider's, tool search and namespaces
included; a name the toolbox does not have says nothing), or `groups=` -- the same as the
`definitions(groups=...)` call's, with the same `roles` (one or the other). Without either
it is decided over the whole toolbox, as before.

```python
sent = toolbox.definitions("anthropic", groups="core", roles=["review"])
system = toolbox.system_prompt(roles=["review"], tools=sent)    # or groups="core"
```

*Several agents on one session* (0.46.0, round 8, M1). Agents working on the same
documents at once share one `Session` -- documents, undo history, refs, datasets,
outputs -- and each gets a *view* of it with roles of its own:

```python
session = toolbox.session()                          # every role: the most any agent gets
reviewer = session.view(roles=["review"], context={"agent": "reviewer-1"})
builder = session.view(roles=["review", "edit"], context={"agent": "builder-1"})

toolbox.dispatch(reviewer, "ppt_set_text", args)     # refused: needs the edit role
toolbox.dispatch(builder, "ppt_set_text", args)      # runs, on the same deck
toolbox.dispatch(session, "ppt_set_text", args, roles=["review"],
                 context={"agent": "reviewer-1"})    # the same, per call
```

- A `SessionView` is the session: every attribute and method but `roles` and `context` is
  the session's own, read through, so it goes wherever a session goes (`dispatch`,
  `dispatch_many`, `session.open`, `take_outputs`); nothing is set on it. Views of views
  narrow further.
- *A view narrows, never widens*: its roles are some of its session's, else `ValueError`
  -- as are `dispatch(..., roles=...)` outside the view's or session's roles. The session's
  roles are the most the application allows in the conversation; whoever holds a view (an
  agent's loop, a component it is handed to) can only take roles away, never give itself
  one. Make the session with every role its agents need (all four, the default) and narrow
  per agent. A view's roles are fixed when it is made; a session narrowed later refuses
  the view's call (`ValueError`).
- *Context* is the application's, never the model's: a read-only mapping, empty by
  default, that a view carries and `dispatch(..., context=...)` adds to (its keys win).
  Every handler of the call sees it as `call.caller` -- an application's own tool in the
  toolbox (one that keeps a note needs to know which agent called), every op of a `batch`
  alike -- and `call.roles` is what the call was checked against. The library's own tools
  read neither; results, warnings and the definitions are as without views. The call
  log names the agent: a `CallRecord` has `roles` (when narrower than the session's) and
  `context` (when there is one), in `to_json()` only then.
- *Concurrency*: a view holds no lock and no state of its own, so threads (or an async
  application's executor) may use views of one session at once exactly as they use the
  session: a call locks its documents, in a fixed order, whichever view it came through;
  calls on one document wait for each other, calls on different ones do not.
- *Batch*: each op is held to the view's roles, as to a session's.
- *Undo* is `edit`'s. Through a view with a context (or a call's `context=`) it takes back
  the caller's own latest change, never another agent's (round 9, 0.50.0; see "Undo with
  several writers"); through the session itself, or a view with no context, the latest
  change to the document, whoever made it, as before. A reviewer has no undo.

A tool's role by default: `edit` if it changes the document (`mutates`), `review` if not --
an application's own tools included; `role=` overrides it (`ANY_ROLE` for every role set,
in `action_roles` too: a comment's text edit).
A changing tool's reading mode that is not an action (`ppt_add_shape`'s `measure`) stays
with its tool, `edit`.

**Where the work runs: three runners.** Rendering and layout run in worker processes so
that a deadline can be kept by killing one, and so that work holding the GIL -- resvg-py's
native rasterise holds it for all of its 0.7 s on a slide -- does not stop the threads
serving other calls. There are three runners, all with `run(fn, *args, timeout=...,
**kwargs)` and `close()`:

| Runner | Used | A deadline |
| --- | --- | --- |
| `WorkerPool(n)`: `n` multiprocessing workers (`spawn`) | the default (`Toolbox(workers=None)`: the CPUs the process may use, its container's CPU quota included, at most 8; warmed before their first task, the warm-up no call's time) | kills the worker and replaces it; the call reports `timeout` |
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

**A worker that exits as it starts** (round 13, R6, 0.58.0). Under `spawn` a worker first
imports the program's main module again, so a script with no `if __name__ == "__main__":`
guard ran its work once more in every worker -- its renders failing there, its work after
them going on -- while the parent waited the whole `worker_start_timeout` for each: 120 s a
call, 240 s for the first (measured: it waited for the two workers being warmed, then
started its own). Now a starting worker is waited for on its pipe and its process at once,
so an exit ends the wait, and the error names the cause: its exit code, or the signal that
stopped it. A worker that reaches a `WorkerPool.run` or a `Toolbox.dispatch` while it is
still starting stops at once (exit code 86, `worker.UNGUARDED_EXIT`) and says why on stderr;
the parent's error says: put that work under the guard, or use `workers=0` or a
`SubprocessPool`, whose workers never import the main module. Measured with a guardless
script rendering a deck three times: 240, 120 and 120 s, each `"the worker process did not
start within 120 s"`, before; 1.5, 0.0 and 0.0 s after, rendered by subprocess workers.

An exit with a code as a worker starts -- that one, an import error, a `sys.exit` in the
main module -- would happen to every worker, so the pool does not try again: a
`WorkerPool` falls back as in a daemonic process (subprocess workers, then in-process; the
warning names the cause), and with `fallback=False` every later call fails at once with the
same error (`pool.failed`). A worker stopped by a signal as it starts is not remembered: the
next call starts another. A worker that is alive keeps its whole start timeout, however
slowly it starts and warms. A failure met while warming in the background (`warm()`, the
toolbox's prewarm) is the one the call needing a worker gets. A `SubprocessPool` worker
that exits before it is ready is remembered the same way.

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
keys its caches with. ooxml-edit keeps the object it is given (a `Locale` is frozen and
pickles into a worker), and checks only that it has the five fields as strings
(`tools.locale_of`, `tools.LOCALE_FIELDS`; `TypeError` otherwise -- a bare language tag is
not a locale). The default is `tools.DEFAULT_LOCALE`, which is `ooxml_common.locales.EN_US`
itself (0.64.0; before, ooxml-edit kept a copy of its values, `tools.DefaultLocale`, now an
alias of `Locale` for one release). A new chart's
language falls back to the locale's (`add_chart(..., locale=call.locale)`) when the
format names none; with the default the chart is byte-identical to before.

**Asset catalogues.** An application's own pictures -- a brand icon set of a few thousand
SVGs with a keyword index -- are searched and placed by id, never by path: the catalogue is
application configuration that a format takes in its settings (`deck_format(assets=...)`),
and only then does the format offer a search tool and an `asset` argument, through
`DocumentFormat.tools`: tools the toolbox adds after its own, one named like a toolbox tool
replacing it in its place (it must serve that format's kind alone). A toolbox without a
catalogue sends no definition for it. Any object with `search(query, limit)` (best first,
`Asset`s or `{id, name, keywords}` mappings) and `get(id) -> (bytes, MIME type)` (`KeyError`
when unknown) is a catalogue (`tools.AssetCatalogue`); `tools.FolderCatalogue(folder,
index=None)` is a simple one over a folder and a JSON or CSV index:

```text
index.json  [{"id": "arrow-up", "file": "arrows/arrow-up.svg", "name": "Arrow up",
              "keywords": ["arrow", "up", "increase"]}, ...]      # or {"assets": [...]}
index.csv   id,file,name,keywords
            arrow-up,arrows/arrow-up.svg,Arrow up,arrow;up;increase
```

`file` is required and stays inside the folder (no `..`, no absolute path, no link out of
it); `id` defaults to the file's name without its extension, `name` to the id; keywords are
a list or one string separated by `;` (or `,` in JSON). Search compares each word of the
query with the keywords and the words of the name and id -- whole word 3, start of a word
2, part of one (three letters or more) 1 -- and keeps the assets that match every word,
highest first. A format's tools use `assets.find(catalogue, query, limit)` (at most
`assets.MAX_RESULTS`, a failing catalogue answered `refused`) and `assets.fetch(catalogue,
id, limits, search_tool=...)` (`not_found` naming the search tool, an image only, within the
session's blob and image limits).

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

**Undo with several writers.** `undo` steps back through one history per document: on the
session itself, the latest change, whoever made it. An application that runs a loop per
slide on one deck, in parallel, can have one loop's undo revert another's edit. Four ways
out:

- **Undo by caller** (0.50.0). Give each loop a view with a context of its own
  (`session.view(roles=[...], context={"agent": "builder-3"})`). Its `undo` takes back its
  own latest change to the document, never another's:
  - the document's latest change, if it is that: a plain undo, exact;
  - else, in a format with undo scopes (decks), the change is taken out from under the
    later ones as a scoped undo is (below), when none of them touched a part it touched
    and it changed no part every slide shares (a layout, master or theme);
  - else -- and always in a Word document, whose one body every change shares, so a later
    change may stand on any of it -- it is refused as `entangled`, nothing changed. The
    message names the later change, its version and who made it ("t_add by
    agent=builder-2", "a change made outside the tools" for one made through the
    library's API), and says to change it back with an edit or have that caller undo
    first; `details.later` lists each later change in the way (`version`, `tool`,
    `context`).

  `scope` narrows it to the caller's own changes there; `steps` stop at the first refusal,
  with a warning; `redo` brings back the caller's own latest undo (refused while another
  caller's undo is the next one to redo, or once its parts changed). Two callers are the
  same when their contexts are equal, so keep per-call extras (a request id) out of the
  context a loop undoes by: they would make every call a caller of its own, and its undo
  refused. Who made each step is `DocumentEntry.owners` (`StepOwner`: context and tool, by
  the version the step made). Why taking out only that much is sound: undo restores
  snapshots of parts, not inverse operations, so a change can be taken out of the middle
  only where no later change touched its parts; a deck's slide parts are each slide's own,
  a Word document's body is everyone's. The definition is unchanged: the refusals say
  what the model needs.

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
