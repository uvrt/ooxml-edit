# Changelog

Versions are those in `pyproject.toml`; none is published to PyPI yet.

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
  (en-BE `[$-409]`, de-BE `[$-407]`, es-MX `[$-C0A]`). Tool definitions unchanged.

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
