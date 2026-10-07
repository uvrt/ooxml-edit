# Roadmap: agent tool layer (T-phases)

This is the working plan for the **tool layer** over pptx-agent and docx-agent. The tool layer is a Python module that the user's own application imports. It provides tool definitions for Claude (with an adapter for GPT-6), plus a dispatcher that runs the calls. The model never runs Python and never writes raw XML; everything it can do is a tool. The plan is written so someone can pick it up cold, in the style of the libraries' own roadmaps: every phase says what it covers, what it needs from the libraries, and how to know it is done.

**Effort key:** S ≈ half a day · M ≈ 1–3 days · L ≈ 1–2 weeks · XL ≈ 3+ weeks.

**T4 is done as charts only** (ooxml-edit 0.9.0): a new chart from data, in both formats, and the chart formatting the T4 tasks use. LW1 and LW2 are dropped (see T4). Trial 3 (T6) has run on Sonnet 5.5, and its fixes (T6b) are in ooxml-edit 0.8.0. T0, the plumbing, is in ooxml-edit 0.3.0 (0.4.0: shared definitions, refs, batch; 0.6.0: T5's loading and strict rules; 0.7.0: T5's rationalisation, with the shared `describe`, `edit_chart`'s read action and `Tool.reads`; 0.8.0: trial 3's fixes, among them required properties first in every schema; 0.9.0: T4's charts); T1 (PowerPoint core), T2 (Word core), T3 (layout and complex diagrams), T5 (guidance, loading and the rationalisation) T4 (charts) and T6 (trial 3) are done. LW5, LW6 and LP11 stay open, outside T4.

**The post-T4 round is done** (ooxml-edit 0.10.0; see "Post-T4" under Phases): PowerPoint's comments (`ppt_comments`), the layout tools and `ppt_draw` out of the tool set (their library calls stay), per-edit layout facts with exact fixes, a decision for every tool no model called, and `GUIDANCE.md` and `SUPPORTED.md` beside each format's tools.

---

## Decisions already made

These are settled; nothing below reopens them.

- **No MCP.** The app imports the tools directly. `toolbox.definitions(provider=...)` returns provider-ready tool lists, and `toolbox.dispatch(session, call)` runs one call.
- **No code-execution escape hatch.** Anything an agent needed Python for in the trials must be a tool, or follow from one. Anthropic's programmatic tool calling is not used: it is incompatible with `strict` and rejects image results (https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling).
- **Two target model families, one set of definitions.**
  - Claude, through the Anthropic Messages API: used now.
  - GPT-6, through the OpenAI Responses API. The adapter is built and schema-tested offline, but nothing calls OpenAI yet (see D9).
  - Chat Completions is supported only as a fallback adapter.
- **The `.pptx`/`.docx` is the truth.** The tools wrap the libraries' semantic APIs and inherit their guarantees: lossless round trip, stable ids, exact undo, and `validate()`.
- **Shape calls are the main route for graphics** (the spike; see "Where we start from"). The model builds graphics from native shapes, text, connectors and groups, one tool call per object or per layout operation.
- **General layout help, no template builders.** There are no timeline, Gantt, process, chevron or legend builders: they would fit the benchmark, and the aim is complex graphics in general. Geometry help is general and knows no diagram type:
  - PowerPoint's own Align and Distribute;
  - `ppt_layout`: stack or column, grid, and label placement;
  - `ppt_copy`: copy or repeat shapes and groups, with text replacements.
  - **Post-T4:** these tools (with `ppt_scale` and `place`) left the tool set: no model called them in about forty trial runs, and a layout-first run gained no grade at about +3,300 tokens a request. The library calls stay. Layout help now comes as **facts after each edit** (`checks.layout`, with the exact fix), not as tools.
- **Facts, not judgement.** The libraries and tools report what Office would show (overflow, collisions, off-slide shapes, wrap margins, colours that are not theme colours) and measurable design facts (palette, colour groups, empty regions, alignment, shape vocabulary, text sizes). House style and design critique belong to the application's thinking layer (see "The thinking layer"). No design rules ship in the libraries or the tools.

| # | Decision | Outcome |
|---|---|---|
| D1 | Where the shared plumbing lives | **`ooxml_edit.tools`**: an optional subpackage that uses only lxml and the standard library. Provider churn is isolated in `adapters.py`. |
| D2 | Where the format tools live | **Inside the libraries** (`pptx_agent.tools`, `docx_agent.tools`), so API drift breaks their tests in the same CI run. |
| D3 | Length unit at the tool boundary | **Points everywhere**, for both formats (trial 2's N5). |
| D4 | Strict mode on Claude, given its per-request limits | **A strict priority list** (writing tools first), and the dispatcher always validates. |
| D5 | Files and paths | **Everything in memory.** Documents are opened from bytes and saved to bytes, which go to the app, never the model. Inputs are in-memory blobs under handles. There are no paths, no FileStore and no sandbox. |
| D6 | Overriding the validate gate | **App-level override only**, never by the model. |
| D7 | Full-state SVG/XML editing | **No.** Raw full-state XML/SVG editing (`apply_svg`) is not exposed. The SVG read view is output only. SVG authoring is parked (G1). |
| D8 | Layout and master editing (LP9) | **Later.** No trial task needs it; theme setters cover rebranding. |
| D9 | Trial 3 models | **Sonnet 5.5 (`claude-sonnet-5-5`) only**, 2 runs per task. A GPT-6 trial on the same harness is possible later. |
| D11 | Main route for graphics (G1) | **Shape calls**, decided by the spike and confirmed by arm C (hand-written SVG: 8.00 vs 8.375, 2.6% fewer tokens, 4 structural failures vs 1). SVG authoring shipped only as the experimental `ppt_draw`, outside the default definitions, for trial 3's comparison; **removed post-T4** (it never beat shape calls); the converter stays in the library. |
| D12 | Where the compact SVG read view lives (was O2) | **A pptx2svg mode (LR3).** pptx2svg already resolves geometry and theme colours, and may change to serve this. |
| D13 | Layout help | **General tools** (`ppt_layout`, `ppt_copy`, align/distribute, `ppt_scale`, `ppt_design_facts`), each backed by a library call. **Post-T4:** only `ppt_design_facts` stays a tool; the rest are library calls, and per-edit layout facts report near-alignment, uneven gaps, outlier sizes and far labels with their fixes. |
| D14 | Many objects per call | High-volume pptx tools take `items[]`, always a list. Creating items may carry `ref` (`^[a-z][a-z0-9_]{0,31}$`) and targets accept `$ref`; refs are kept per document for the session and restored with undo/redo. A generic `batch` tool runs ops in order, all or nothing, one undo step per document, checks once at the end, at most 200 ops with a deadline. `batch` is never strict; the dispatcher validates each op. |
| D15 | Data positions (from arm C) | **Scales, declared once.** `ppt_scale` keeps a linear, date or band scale as a ref (`$time`); shape items and connector ends give `place` in data units (strings: a number, a date or a band) and the tool computes the points. No chart or timeline builders: a Gantt, a scatter or a matrix is shapes composed against scales. **Post-T4:** `ppt_scale` and `place` left the tool set (0 calls; models computed exact positions in `items[]`); `pptx_agent.edit.scales` stays. |

The ids D10 (the needs-human comment flag, dropped) and O1/O2 are retired, not reused.

---

## Where we start from

Two trials (24 runs each) used the libraries as Python black boxes (the trial 1/2 reports).

- **Trial 2:** every re-run task succeeds (19/20 strictly, 20/20 by Office). Word was 8/8, and every output opened in Office.
- **Where trial 2 lost points:** the design tasks (p7 1/2, p8 0/2). All four runs passed every structural check, but scored 6–8/10 on:
  - colour discipline (a four-hue "rainbow", off-palette tints);
  - missing legends and timeline gridlines;
  - z-order (a dashed line drawn over bar text);
  - title line breaks.
- **Cost:** p7/p8 averaged 125k tokens against 85k for the other tasks. Most of it went into build → check → fix loops of hand-written geometry, one shape per call.

**The spike** (the spike report) then tested SVG authoring against shape calls on four graphics tasks (p7 process, p8 Gantt, o1 org chart, m1 2×2 matrix), 2 arms × 2 runs, against a pre-registered decision rule:

| | Shape calls (A) | SVG authoring (B) |
|---|---|---|
| Mean visual grade | **8.50** | 8.13 |
| Mean tokens per run | **129k** | 136k (+5%) |
| Mean minutes | **15.6** | 19.6 |
| Tool calls | 39.1 | **34.9** |
| Editability (scripted) | 1.00 | 0.99 (1.00 reviewed) |
| Structural failures | 1 (two label boxes overlap) | **0** |

- **Verdict:** shape calls stay the main route. SVG needed +1 grade, or equal grade at −25% tokens; it had neither.
- **Why SVG saved nothing:** agents generated SVG from Python loops rather than writing it, so both arms were code. About 12k tokens per run went into reading the library docs, measuring and verifying cost the same either way, and SVG added escaping, inset bookkeeping and whole-graphic rebuilds.
- **Where both arms lost points:** layout and design, not the API: dead space, rainbow palettes, no legend, no focus. Neither the API nor the profile helps with layout, and no check catches these.
- **Shape-call friction:** sizing from `measure_text` without the shape's default insets (14 overflows in one run); `bold=` on `measure_text` visible only in its signature; a library-render bullet "blob"; no preset-name list; `overflows()` comparing visible text, not boxes, so two overlapping label boxes went unseen (the one structural failure).
- **Praised:** `content_area(slide)` (on the spike branch only; public since T1, LP17).

**Arm C** then had the model write the SVG by hand, with no code, through a small command-line tool over the fixed converter, under the same rule:
- **Result:** C averaged 8.00 against A's re-graded 8.375, used 2.6% fewer tokens (125.9k vs 129.3k) and had 4 structural failures against A's 1. Editability was equal. **Shape calls stay the main route.**
- **Why:** without code the model became a calculator. Proportional placement (dates, scores) and label distances failed: m1 labels 1.03–1.21 in from their bubbles where 1.0 was the limit, and both p8 runs split the marker line into pieces to clear a collision, so it no longer spanned the rows.
- **What it asked for:** declarative layout instead of coordinates -- data-to-position scales first, then stack/grid/equal columns, label placement with a stated distance rule, and the measure → height loop that already worked; checks that explain themselves; design facts.
- **The converter itself was reliable:** every first apply was valid, half as many apply cycles as before, every connector glued.

Most of what the trial reports asked of the libraries has since landed in pptx-agent (README "What works today"):
- `collisions()`;
- `measure_text()`, `fit_height()`, and `margin_to_wrap`/`near_wrap`;
- text-frame insets, anchor, wrap and autofit;
- bullets that hang;
- `theme.roles`, `tints()`, `ramps`, `set_colors`, `set_fonts`;
- `slide.title`, `slide_titled`;
- `drawn_bounds`, and connector `route`;
- `Shape.duplicate(dx, dy)` and `duplicate_slide` within one deck.

docx-agent landed:
- `section_blocks`/`move_blocks`;
- grouped `changes()`;
- `copy_blocks(style_map=, unmapped=)`;
- `Styles.remove`/`purge_unused`;
- `stories="all"`;
- fields marked in the markup view;
- `fields()`;
- `chart.workbook_values()`.

The trial reports' "Implications for an agent-facing tool layer", with the spike's findings, are the requirements here:
1. one read call per document, showing ids;
2. one address grammar;
3. coarse structural operations;
4. "as Office shows it" checks by default;
5. change summaries and `validate()` deltas;
6. judgement hooks: here, the app's thinking layer;
7. a describe-template/theme tool;
8. measurement before building, with **one measuring model** shared with building;
9. layout primitives: align, distribute, stack/column, grid, label placement, and copy/repeat;
10. design-system guidance as data: here, supplied by the app, not shipped; the tools supply design facts;
11. uniform units.

---

## Provider facts this design rests on

These come from the earlier research against the official docs, re-checked online in T0 (2026-10-06). Items marked **[verified T0]** were confirmed or corrected then, with the page each comes from.

**Current Claude models [verified T0]:** Fable 5.1 (`claude-fable-5-1`), Opus 5.5 (`claude-opus-5-5`), Sonnet 5.5 (`claude-sonnet-5-5`) and Haiku 4.5 (`claude-haiku-4-5`, retiring no sooner than 2026-10-15) (https://platform.claude.com/docs/en/models/overview). Sonnet 5 (`claude-sonnet-5`) is now a **legacy** model: still available, retiring no sooner than 2027-06-30 (https://platform.claude.com/docs/en/models/sonnet-5/overview). Trial 3 uses Sonnet 5.5 (D9).

### Anthropic Messages API

- **Tool shape [verified T0]:** `{name, description, input_schema, strict?, defer_loading?, cache_control?, input_examples?, allowed_callers?}`. The name must match `^[a-zA-Z0-9_-]{1,128}$` (https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools).
- **Strict tool use is GA**, with `strict: true` per tool (https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use, https://platform.claude.com/docs/en/build-with-claude/structured-outputs).
  - **Supported:** basic types, `enum`, `const`, `anyOf`, `allOf` (but not with `$ref`), internal `$ref`/`$defs`, `required`, `default`, and `additionalProperties: false`. `format` supports date-time, date, time, duration, email, hostname, uri, ipv4, ipv6 and uuid. Simple `pattern`. `minItems` 0/1.
  - **Unsupported:** recursion, `minimum`/`maximum`/`multipleOf`, `minLength`/`maxLength`, `maxItems`, `minItems > 1`, `oneOf`.
  - **Per-request limits [verified T0]:** 20 strict tools, 24 optional parameters, 16 union-typed parameters (`anyOf` or type arrays). "These limits apply to the combined total across all strict schemas in a single request"; non-strict tools don't count (https://platform.claude.com/docs/en/build-with-claude/structured-outputs#explicit-limits).
  - Beyond those, an undocumented grammar-size limit returns a 400 ("Schema is too complex for compilation"), and schema compilation times out after 180 s.
  - **[measured T5, Sonnet 5.5, 2026-10-07]** The grammar limit bites well inside 20/24/16. Twelve strict deck tools with 24 optional parameters and 33 free-text strings compiled. Adding `save_document` (2 more strings) or three plain strings returned "Schema is too complex." Adding three integers or one enum did not. Free-text strings (no enum) are the dimension that counts, but not the only one: one 32-string set also failed. The adapter therefore caps strict tools at 32 free-text strings and sends only **writing** tools strict by default.
  - `additionalProperties: false` is required on every object; `oneOf` is in neither the supported nor the unsupported list, so it is treated as unsupported. Numeric bounds are still unsupported.
  - **[measured trial 3, Sonnet 5.5]** Strict decoding writes an object's keys **in schema order**: once a key is written, no key listed before it can follow. `word_insert_markdown` listed `doc, markdown, blob, at`; the model writes `doc` and `at` first, so `markdown` could no longer be written and the call arrived as `{doc, at, key}`. Three runs looped 17–40 times on it. A probe of three requests each: strict in that order dropped `markdown` 3/3, `strict: false` kept it 3/3, and strict with the required properties first kept it 3/3. The adapters therefore send every object's required properties first (T6b), and `anthropic_problems` flags a strict tool that does not.
- **`tool_result` content** is a string or a list of `text`, `image`, `document` and `search_result` blocks, with `is_error` (https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls).
  - Results must come first in the user message, and all parallel results go in one message (https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use).
- **Images [verified T0]:**
  - cost ⌈w/28⌉×⌈h/28⌉ tokens (one per 28×28 patch);
  - "Claude 4.7 and later models": a long edge up to 2576 px and at most 4784 tokens; all other models (Haiku 4.5) 1568 px and 1568 tokens. Opus 5.5 is in the first tier; Sonnet 5 is not named, but as a later model it falls under "4.7 and later";
  - with more than 20 image and document blocks in a request (images inside `tool_result` and resent history count), keep both sides at 2000 px or less; at most 600 images per request (100 on 200k-context models) (https://platform.claude.com/docs/en/build-with-claude/vision#evaluate-image-size).
- **Parallel tool use** is on by default; `disable_parallel_tool_use` goes in `tool_choice` (not top level) and with `auto` means at most one tool per response (https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use).
- **Forced tool use [verified T0, corrected]:** Opus 5.5, Sonnet 5.5, Fable 5.1 and Mythos 5.1 reject `tool_choice` `any`/`tool` with a 400 "regardless of thinking settings"; **Sonnet 5 accepts them**. On every model, manual extended thinking (`thinking: {type: "enabled"}`) rejects them; adaptive thinking does not (https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools#forcing-tool-use). The adapter uses `auto` on every model, so the tool layer works the same on all of them.
- **Overhead [verified T0, corrected]:** the hidden tool system prompt is 286 tokens on Opus 5.5 and Sonnet 5.5 (`auto`/`none`; they do not support `any`/`tool`), and **354 tokens on Sonnet 5** (474 with `any`/`tool`). Definitions are billed as input (https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview#pricing).
- **Caching:** cache the definitions with `cache_control` on the last non-deferred tool. Changing definitions invalidates the whole cache (https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching).
- **Tool search** is GA (`tool_search_tool_bm25_20251119` / `_regex_`), with `defer_loading: true` per tool (https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool).
  - Full definitions are still sent; deferred ones stay out of the cached prefix.
  - Keep 3–5 tools non-deferred. Selection "degrades once you exceed 30–50 available tools".
  - A deferred tool cannot carry `cache_control` (a 400); at least one tool must not be deferred; strict mode and `defer_loading` work together.
  - **[verified T0]** The compatibility table lists Opus 5.5, Sonnet 5.5 and Haiku 4.5, **not Sonnet 5**. A scripted Sonnet 5 session must confirm tool search before T5 relies on deferred loading for trial 3; without it, the app picks groups up front (`groups=[…]`).
  - **[verified T5]** On Sonnet 5.5, with every deck or Word tool sent and the non-core ones deferred, one request found a deferred tool through BM25 search and called it with valid arguments. The deck request found `ppt_align`, the Word request `word_set_tracking`. Deferred definitions are not billed as input until they are loaded: a request carried about 5.2k cached tokens for the core, not the 20k of every tool.
  - **[measured T5]** count-tokens rejects the tool-search server tool; count without it. It counts tool definitions at about **1.48 times** the offline estimate (compact JSON ÷ 3.5), and Sonnet 5.5 and Opus 5.5 count them the same. A tool's own framing costs about 50 tokens, and the hidden tool prompt 286.

### OpenAI (adapter only; not called until a GPT-6 trial)

- **Models [verified T0]:** `gpt-6-astra`, `gpt-6.1-sol` and `gpt-6-luna`. "GPT-6 Astra and GPT-6.1 Sol support Chat Completions, but tool calling requires Responses" (Luna is not named in that sentence). These models take no `temperature`/`top_p`, and no `none` reasoning effort (https://developers.openai.com/api/docs/guides/latest-model).
- **Responses tool shape:** `{type:"function", name, description, parameters, strict, defer_loading?}`.
- **Chat Completions shape:** `{type:"function", function:{name, description, parameters, strict}}`, with names of 64 characters at most from `[a-zA-Z0-9_-]` (https://developers.openai.com/api/reference/resources/chat.md).
- **Strict mode** (https://developers.openai.com/api/docs/guides/structured-outputs):
  - every property in `required`;
  - optional fields as `["T","null"]`;
  - `additionalProperties: false` everywhere;
  - a root object that is not `anyOf`.
  - **Supported [verified T0]:** `pattern`; `format` date-time, time, date, duration, email, hostname, ipv4, ipv6 and uuid (no `uri`); numeric bounds; `minItems`/`maxItems`; `$defs`/`$ref` and recursion. `minLength`/`maxLength` are not in the supported list.
  - **Unsupported:** `allOf`, `not`, `if/then/else`, and `dependent*`.
  - **Limits:** 5000 properties, 10 levels of nesting, 1000 enum values; 120,000 characters of names and values in all; 15,000 characters for one enum of more than 250 values.
  - **Defaults:** the Responses API "normalizes into strict when possible" if `strict` is omitted; Chat Completions stays non-strict by default.
- **Images in function results [verified T0]:** a Responses `function_call_output.output` is a string or an array of `input_text` `{text}`, `input_image` `{image_url (URL or base64 data URL) | file_id, detail: low|high|auto|original}` and `input_file` (https://developers.openai.com/api/reference/resources/responses/methods/create). **Chat Completions tool messages are text only**: "For tool messages, only type `text` is supported" (https://developers.openai.com/api/reference/resources/chat).
- **Image cost [verified T0]:** ⌈w/32⌉×⌈h/32⌉ patches × the model's multiplier, rounded up: 1.2 for gpt-6-astra (1280×720 → 1,104 tokens). `auto` sizes like `original` (the image's own size); `high` allows up to 2,500 patches; `low` fits 512×512. An image over 30,000 patches is rejected, not resized. gpt-6.1-sol and gpt-6-luna are not in the sizing tables yet (https://developers.openai.com/api/docs/guides/images-vision#patch-based-image-tokenization).
- **Tool choice and grouping:**
  - `parallel_tool_calls`;
  - `tool_choice` with `allowed_tools`, which restricts calls without changing `tools` and so keeps the cache;
  - "Aim for fewer than 20 functions available at the start of a turn".
- **Tool search** (`{type:"tool_search"}` with `defer_loading`, ideally grouped in `namespace`s of fewer than 10 functions) exists in the Responses API only, for gpt-5.4 and later (https://developers.openai.com/api/docs/guides/tools-tool-search).
- **Reasoning items:** pass back reasoning, function_call and function_call_output items with `previous_response_id`, or replay them (https://developers.openai.com/api/docs/guides/reasoning).

### The common strict subset

Every tool's **canonical schema** is written in this subset, and a test enforces it:

| Allowed | Not allowed (enforce in the dispatcher instead) |
|---|---|
| root `type: object`; `properties`; `required`; `additionalProperties: false` on every object | `oneOf`, `allOf`, `not`, `if/then/else`, `patternProperties`, `dependent*` |
| `string`, `integer`, `number`, `boolean`, `array` (`items`), nested `object` | numeric bounds (`minimum`, `maximum`, `multipleOf`): Anthropic lacks them |
| `enum` of strings (at most 50 values per enum, 1000 in total) | `minLength`/`maxLength`, `maxItems`, `minItems > 1` |
| `description` on every top-level property; nested properties and array items only where the name, type and enum do not say it all (T5: every description is sent with every request, about 6 tokens of framing each) | recursion and `$ref` (inline everything; nesting at most 6 levels) |
| `format: "date"` and `"date-time"` only | `format: "uri"` (OpenAI lacks it), `pattern` (kept out for simplicity) |
| names matching `^[a-z][a-z0-9_]{0,63}$` | `default` (both providers: say it in the description) |
| | free-form objects (`additionalProperties: true`): every key is named. One exception: a free-form object in a non-strict tool (`batch.ops[].arguments`), which the dispatcher validates against the named tool |

The canonical form marks each property *required* or *optional*; it does not use type unions. Where a tool takes one of two shapes (plain `text` or a `paragraphs` spec), they are two optional properties, and the dispatcher requires exactly one. Each adapter then handles its own provider's rules.

**Anthropic adapter (used now)**
- Optional properties are left out of `required` and listed after the required ones, at every level (trial 3: strict decoding writes keys in schema order).
- `strict: true` is set on a tool **only while the request stays under the strict limits [verified T0: 20 tools, 24 optional parameters, 16 unions, summed over the strict tools; T5: at most 32 free-text strings]** (D4):
  - Only writing tools are strict (T5: reading tools take an id and a few options, and spending grammar on them pushed a deck request over "Schema is too complex"). They go in each format's `DocumentFormat.strict_first` order, then in registration order.
  - The rest are sent `strict: false`, and the dispatcher validates them; it validates every call anyway. Option-heavy tools (`ppt_layout`, `ppt_copy`, `ppt_set_shape`, `word_format`) will usually fall in this group.
  - Union-typed parameters are never generated, so the union limit cannot bite.
- `defer_loading` on non-core tools, plus `tool_search_tool_bm25`.
- `cache_control` on the last core tool.
- **Default (T5):** `toolbox.definitions("anthropic")` sends every tool, the non-core ones deferred; naming `groups=[…]` loads those groups instead, with no tool search.
- `tool_choice` is always `auto`.
- An image result becomes `[{type:"text"}, {type:"image", source:{type:"base64", media_type:"image/png", data}}]`; an error sets `is_error: true`.

**OpenAI Responses adapter (built, offline-tested)**
- Every property goes into `required`; an optional property becomes `["T","null"]`. `strict: true` on every tool.
- Groups become `namespace`s, with `defer_loading` on non-core tools and `{type:"tool_search"}` added.
- An image result is returned as `[{type:"input_text"}, {type:"input_image", image_url:"data:image/png;base64,…", detail:"auto"}]`.

**OpenAI Chat Completions adapter (fallback, offline-tested)**
- Definitions are nested under `function`, with `strict: true`.
- An image result is text in the tool message ("rendered: see the next message"), followed by a user message carrying the image.
- No tool search. Not usable with GPT-6 Astra or 6.1 Sol.

**Dispatcher (all providers)**
- Validates each call against the canonical schema, including the bounds and either/or rules the subset cannot express, before running it.
- An array or object argument written as a JSON string (a long `items`, `batch`'s `ops`) is parsed first; inside it, a `\v` line break, which JSON has no escape for, is read as U+000B (trial 3 and its fix round).
- A schema miss returns an `invalid_arguments` error naming the field and the allowed values.

**Design consequence: few optional parameters.** Tools keep most parameters required, and put variety in an `action` enum. That is the main reason the tool list below is "about 40 coarse tools", not 120 fine ones.

---

## Architecture

### Package layout

```
ooxml_edit.tools          (new optional subpackage: format-neutral, stdlib + lxml only)
  registry.py   Tool, ToolGroup, @tool decorator, canonical-schema builder + subset checker
  schema.py     the common-subset validator (no jsonschema dependency)
  adapters.py   to_anthropic(), to_openai_responses(), to_openai_chat(); result → content blocks
  session.py    Session, DocumentEntry, Blob, Output, per-document lock, version, render cache
  results.py    Result envelope, ToolError codes, truncation and paging helpers
  limits.py     size limits, magic-byte/MIME checks on blobs, zip-bomb guard
  worker.py     render/measure process pool with deadlines
  dispatch.py   Toolbox and Call: definitions, dispatch, dispatch_many, rendering results
  logs.py       call records: argument digests, never content
  prompts.py    shared system-prompt fragments (planning, verification, units, addresses)
pptx_agent.tools          pptx tool functions + the "ppt" groups + pptx prompt fragment
docx_agent.tools          docx tool functions + the "word" groups + docx prompt fragment
```

The app uses it like this:

```python
from ooxml_edit.tools import Toolbox
from pptx_agent.tools import TOOLS as PPT, FORMAT as PPTX
from docx_agent.tools import TOOLS as WORD, FORMAT as DOCX

toolbox = Toolbox(PPT + WORD, formats=[PPTX, DOCX])   # a DocumentFormat per library
session = toolbox.session(clock=my_clock)
d1 = session.open(deck_bytes, name="q3-review.pptx")       # → "d1"
b1 = session.add_blob(logo_bytes, name="logo.png")          # → "b1"

tools = toolbox.definitions("anthropic", groups="core")      # or "openai-responses"
result = toolbox.dispatch(session, name, arguments)          # → Result
block = toolbox.render_result("anthropic", result, call_id)  # one tool_result block
results = toolbox.dispatch_many(session, calls)              # one turn's parallel calls
message = toolbox.render_results("anthropic", list(zip(call_ids, results)))

for out in session.take_outputs():                           # bytes from save_document
    store(out.name, out.format, out.data, out.validate)
```

Shared plumbing lives in `ooxml_edit.tools` and format tools inside each library (D1, D2).
- The plumbing is format-neutral by construction, and ooxml-edit's neutrality test (`tests/test_neutrality.py`) extends to it. The adapters emit plain dicts and import no provider SDK.
- The subpackage is optional and never imported by the core, exactly like `ooxml_edit.charts`.

### The session

```
Session
  id, created_at, clock            # injected clock: dates written by tools come from here
  blobs: dict[handle, Blob]        # app-registered inputs ("b1"…): name, mime, bytes
  documents: dict[doc_id, DocumentEntry]
  outputs: list[Output]            # save_document results for the app: name, format, bytes, validate
  on_output: callable | None       # optional push instead of take_outputs()
  limits: Limits                   # sizes, timeouts, result budgets

DocumentEntry
  doc_id ("d1", "d2"…), kind ("pptx" | "docx"), name, source ("app" | blob handle | "new")
  document: pptx_agent.Document | docx_agent.Document
  lock: threading.RLock
  version: int                     # +1 per successful mutating call; restored on undo/redo
  baseline_problems: list[Problem] # validate() at open
  tracking: {author, date_mode} | None   # docx only: wraps each edit in doc.tracking()
  render_cache: LRU[(version, unit, width) -> png bytes]
  check_cache:  LRU[(version, scope) -> CheckReport]
  log: list[CallRecord]            # name, args digest, result summary, duration: for transcripts
```

- **Undo and redo** come from the libraries (`ooxml_edit.history.History`, 50 steps by default). Every mutating tool call runs inside `document.batch()`, so:
  - **one tool call is one undo step** (a `ppt_copy` with 12 repeats is one step);
  - an exception rolls the whole call back, since History restores the batch snapshot on error.
  - `undo`/`redo` tools step `version` back and forth with a stack kept beside the History.
- **Cross-document calls.** `ppt_copy` to another deck locks both entries (in `doc_id` order, so two calls cannot deadlock) and is one undo step in the target; the source is unchanged.
- **Version and caches.** Renders and checks are cached by `version`. ooxml-edit gets `History.version` (gap **LE1**), so the tool layer needs no bookkeeping for it. docx-agent already caches `layout()` by document bytes.
- **Tracking mode (Word).** `word_set_tracking` stores the author on the entry. The dispatcher then wraps every Word edit in `with doc.tracking(author=…, date=session.clock())`. A fixed clock in tests makes outputs byte-reproducible.

### Concurrency and thread safety

- **A `Document` is not thread-safe.** These are lxml trees mutated in place, so every call on one document holds that entry's `RLock`, and the session's document and blob maps have their own lock.
- **Calls on different documents run concurrently.** This covers different sessions, and one session with a deck and a document open.
- **Parallel tool calls on the same document run serially, in the order the model emitted them.** Results come back in that order: all in one user message for Anthropic, and one `function_call_output` per `call_id` for OpenAI. If a call fails, the later calls on the same document still run (each is atomic on its own), and each one's result says what happened.
- **Module-level state** in the libraries (namespace and child-order registries) is written at import and only read afterwards. That is safe; a test imports both libraries in threads and compares the registries.
- **Rendering and Word layout are CPU-bound pure Python** (measured: a slide PNG takes 0.63 s; a 36-page document lays out in 2.35 s and renders page 1 in 3.3 s). They run in a **process pool** (`worker.py`):
  - The worker gets `document.to_bytes()`; both libraries already render from bytes.
  - A deadline is enforced by killing the worker.
  - The GIL is not held across sessions.
  - pptx `text_fit`/`overflows`, design facts and label placement stay in-process, scoped to the edited slides (0.68 s for a 4-slide deck, all slides), with a soft deadline.
- **Memory.** Each session caps its open documents (default 4), its total package bytes (default 200 MB) and its blob bytes (default 100 MB). The app's TTL evicts idle sessions.

### Documents in and out (D5: everything in memory)

- **In.** The app opens documents from bytes (`session.open(data, name)` → `d1`) and registers any other input with `session.add_blob(data, name, mime)` → `b1`. Inputs include images, templates, Markdown, and a second document the model may open. Limits and magic-byte checks run at registration.
- **Out.** `save_document` serializes with `to_bytes()` (or `to_markdown`/`to_outline`) behind the validate gate. It puts the bytes in `session.outputs`, or calls `on_output`. The model gets only the name, format, size and validate report; it never sees the bytes.
- **No paths anywhere.** No tool has a path parameter (a schema test enforces this), and the tool layer never gives a library a path.
  - Library calls that accept paths are given bytes or mappings from blob handles instead: `add_picture`, `replace_image`, `Document.new(template=)`, `insert_outline(images=)` (whose default treats sources as paths) and `insert_markdown(images=)`.
  - `insert_markdown(fetch=None)` stays `None`: there are no remote images.
- **Markdown in:** as a string argument, or as a `.md` blob. **Markdown out:** `word_read` returns it to the model, and `save_document(format="markdown")` gives it to the app.

---

## Tool design principles (from the trials and the spike)

1. **One describe tool,** `describe` (shared since T5b: one schema, a handler per kind), listing everything editable with ids.
   - On a deck: slides with their titles, layouts and their placeholders, each slide's content area (pt), theme colours, fonts, roles and tints, slide size, sections, and the baseline problems.
   - On a document: the heading tree with ids, sections, stories, styles in use, comments, revision and field counts, tables, drawings, charts, page count, and the baseline problems.
   - Reading the content itself is a second tool with paging (`ppt_read_slides`, `word_read`).
2. **One address grammar,** written once in the system prompt and accepted by every tool.
   - **PowerPoint:** `s:256` (slide), `256.5` (shape; `256.5#2` when the deck numbered a shape twice), `256.5/p1/r0`, `256.5/cell2,1`, `256/notes`, `layout:Title and Content`. Another open deck's shapes are qualified by its doc id: `d2:256.5`.
   - **Word:** `p:3B212964`, `t:…` (table), `c:…`, `rev:…`, `fn:…`, `d:…`, `s:body` (section), `p:X@19:29` (characters), `p:A..t:B` (a span), and story names (`header1`).
   - **Inputs:** blob handles `b1`, `b2`…
   - Every address a read tool prints, including the `data-id`s in the SVG read view, is accepted verbatim by every write tool. Nothing is converted in the tool layer.
   - Text targets go through `find_text`, or a `find` argument that must match exactly once (`AmbiguousAnchor` → an error listing every candidate's address).
3. **Coarse first, plus one general setter per format.**
   - Structural tools do a whole job: `word_move`, `ppt_draft_slides`, and (until post-T4) `ppt_layout` and `ppt_copy`.
   - `ppt_add_shape` and `ppt_set_text` take a **declarative text spec**: paragraphs, runs and bullets in one call.
   - `ppt_set_shape` and `word_format` take **every** settable property as a named, optional field. That fixes trial 2's undiscoverable `format(**Any)` keys (p8).
4. **One length unit: points** (D3).
   - Every length in every tool is points, as a number (2 decimals), for both formats. pptx EMU is converted at the boundary (12,700 per pt). The SVG read view's user units are points too.
   - Font sizes are points, colours are theme strings (`accent1 lumMod=75%`) or `#RRGGBB`, and dates are `YYYY-MM-DD`.
   - This removes trial 2's N5 class of error (EMU versus points) by construction.
5. **One measuring model.** Measuring is `ppt_add_shape` itself with `measure: true` (since T5b; before, `ppt_measure_text`): the same text spec as `ppt_add_shape`/`ppt_set_text`, and the target shape's (or the new shape's preset's) default insets, font and wrap. What is measured is what is built; the spike's two drift bugs (missing default insets, an unnoticed `bold`) cannot recur.
6. **Facts in every result.** Each mutating call returns `checks`, which hold facts only:
   - **pptx:** `overflows()` for the slides touched (text, overlap/collisions including box overlap, off_slide), and `near_wrap` lines. `check` adds the full slide facts (LP15); `ppt_design_facts` adds the design facts (LP24).
   - **docx:** `EditResult.warnings`/`unknown`. (Stale fields, LW2, were dropped in T4: no task needed them.)
   - **Both:** the `validate()` delta against the baseline: `new` and `fixed` problems. pptx `validate()` takes 0.04 s and docx's 0.18 s on the largest fixture, so it runs every time.
   - **Word reflow** (pages changed, from `layout().compare`) costs seconds on long documents. It is included when the layout is cached or the document has 20 pages or fewer; otherwise `checks.reflow = "stale: call check"`.
7. **Render on demand, never automatically.**
   - `render` returns one or more PNGs within a budget. The default is slides at 1280 px wide: 1,196 Claude tokens (⌈1280/28⌉×⌈720/28⌉ = 46×26) and 1,104 on gpt-6-astra [verified T0].
   - Word pages default to 1000 px wide: 1,692 Claude tokens for a Letter page (1000×1294) and 1,836 for A4 (1000×1414); 1,575 and 1,728 on gpt-6-astra [verified T0].
   - There are at most 4 images per call and at most 2576 px on the long edge, and a session-level image budget (default 40 images).
   - The result states the pixel size and estimated tokens.
   - Word renders show the final view only (docx-agent decision 8). Review markup through `word_read(view="markup")`.
8. **Errors name the valid options.** Library exceptions map to error codes, each with `valid_options`:

   | Code | Raised when | `valid_options` |
   |---|---|---|
   | `not_found` | `KeyError`/`IndexError`, unknown blob handle | the nearest addresses or handles, e.g. the shapes on that slide |
   | `label_not_found` | `LabelError`, pptx | the labels; the library gives them only in the message: gap **LP14** |
   | `ambiguous` | `AmbiguousAnchor` | the candidates' addresses and context |
   | `refused` | `ChartDataError`, `EditError`, `FullStateError`; a layout that cannot fit its box | — (for layout: the space needed and the space available) |
   | `unit` | `UnitWarning`/`ValueError` | — |
   | `invalid_arguments` | schema miss, or an unknown preset name | the allowed values (for presets: the names from LP20) |
   | `timeout` | deadline passed | — |
   | `limit` | a size limit | — |
   | `internal` | an unexpected exception (logged with its traceback for the app) | — |

   Warnings (`MarkdownEscapeWarning`, `TemplateOpenedWarning`, `ChartDataWarning`, `OutlineWarning`, `TemplateOpened`, `MacrosDropped`) are caught and returned in `warnings`, never printed.
9. **Idempotence and determinism.**
   - Setters are idempotent by nature.
   - Creating tools (including `ppt_copy`) take an optional `key`. A second call with the same key on the same document returns the first call's objects instead of creating duplicates, which makes a retry after a timeout safe.
   - Dates come from the session clock, and lists are returned in document order.
   - Two runs with the same calls give byte-identical files. The trials and the spike already saw this (16/16 re-runs byte-identical); golden transcripts hold it.
10. **Result size limits and paging.**
    - Text results are capped at about 6,000 tokens (24,000 characters), with `next_cursor`.
    - Lists are capped at 50 items, with `total`.
    - Reads page by slide range (pptx) or by block count and range (docx). Measured: a 36-page document's Markdown is 37k characters, so it pages.
11. **The result envelope** (JSON, rendered as one text block plus optional images):

```json
{"ok": true, "doc": "d1", "version": 7, "summary": "Set text of 257.3 (title)",
 "changed": ["257.3"], "created": [], "removed": [], "renamed": {},
 "warnings": [], "checks": {"overflows": [], "collisions": [],
 "validate": {"new": [], "fixed": [], "baseline": 3}, "reflow": null},
 "data": {}, "next_cursor": null}
```

12. **Batch, don't loop.** Build a whole graphic in one `items[]` call, chaining with refs; mix tools with `batch` (D14). Not allowed in a batch: render, save_document and the session tools. (Data scales and `place`, D15, left the tools post-T4.)

---

## Complex diagrams: layout, copy and design facts

**Post-T4:** the layout, scale and copy tools below and `ppt_draw` were removed from the tool set; the subsections describe the library calls that remain (`pptx_agent.edit.layout`, `.arrange`, `.scales`, `Slide.copy_shapes`, `.svgprofile`). What replaced them for a model is per-edit layout facts (see "Post-T4").

Shape calls are the main route. What the spike showed is missing is not a different authoring format but **layout help** and **facts about the design**: both arms spent most of their effort computing coordinates, and every grade gap came from dead space, palettes and missing legends. This section covers the general tools that close that gap. None of them knows a diagram type.

### The text spec (used by building and measuring)

One shape, in `ppt_add_shape` (building or, with `measure: true`, measuring) and `ppt_set_text` alike:

```json
{"paragraphs": [
   {"runs": [{"text": "Discover", "bold": true, "size": 14}], "align": "center"},
   {"runs": [{"text": "Interviews, data audit"}], "bullet": "bullet", "level": 1,
    "space_before": 3}],
 "frame": {"insets": [7.2, 3.6, 7.2, 3.6], "anchor": "middle", "wrap": "square"}}
```

- Run fields: `text`, `bold`, `italic`, `underline`, `size`, `font`, `color`, `hyperlink`.
- Paragraph fields: `runs`, `align`, `bullet` (none/bullet/number), `level`, `space_before`, `space_after`, `line_spacing`.
- `frame` is optional; omitted values are the target's (or the new preset's) defaults, and the measure uses those same defaults.
- Plain `text` (`\n` paragraphs, `\v` line breaks) remains for the simple case.
- Library: **LP18** (a `TextSpec` type shared by `set_text` and `measure_text`).

### Layout (`ppt_layout`, `ppt_align`) -- built in T3; tools removed post-T4

| Action | What it does | Parameters |
|---|---|---|
| `stack` | place shapes in a row, left to right, in the order given | `targets`, `box?` or `inside?` (a shape's box; default: the slide's content area), `gap` (pt), `align` (top/middle/bottom/keep), `justify` (start/center/end/spread), `equal?` (equal widths filling the box), `fit_text?` (heights fit the text first) |
| `column` | the same, top to bottom | as `stack`, with `align` left/center/right/keep and `equal` giving equal heights |
| `grid` | place shapes into rows × columns within a box | `targets`, `box?`, `rows?`, `columns?` (one may follow from the other), `gutter` {x, y}, `order` (rows/columns), `fit` (keep size and centre in the cell / resize to the cell) |
| `place_labels` | put each label beside its anchor shape or point, avoiding collisions with other labels, shapes and lines | `labels[]` {`label` (an existing text shape) or `text` (a new text box, sized by `measure_text`), `anchor` (address) or `point` {x, y}}, `sides` (preference order), `distance` (pt), `max_center` or `max_edge` (the distance rule, pt), `leader` (none/line/auto), `avoid?` (shapes that may be overlapped), `box?` |
| `place` | move existing shapes to data positions | `items[]` {target, `place`} (see "Data positions") |

- Groups move as one; rotated shapes are placed by what is drawn (`drawn_bounds`), as PowerPoint aligns them.
- `place_labels` tries each side in order of preference -- its base spot `distance` from the anchor's edge, centred on it, then slid along that side and pushed further out -- before the next side, within the distance rule. A spot is taken when it stays in the box, overlaps no other shape or placed label, and crosses no line; a filled shape without text that the label lies wholly on (a quadrant panel) is allowed, as `collisions()` allows it. Labels it cannot place are returned with the reason and the shapes most in the way (a fact, not an error); the slide's `collisions(boxes=True)` that involve a label are returned too. New labels align their text toward the anchor.
- A layout that cannot fit its box is `refused`, with `needed` and `available`.
- One call is one undo step.
- `ppt_align` is PowerPoint's Align and Distribute (one tool since T5b): `edge` (left/center/right/top/middle/bottom; `to`: selection, slide, content area, or the first shape, which stays) or `distribute` (horizontal/vertical; `to`: selection, slide, content; or a fixed `gap`), exactly one; distribute orders shapes by where they stand, as PowerPoint does.
- Library: **LP1** `pptx_agent.edit.arrange` (`align`, `distribute`, `place`), **LP21** and **LP22** `pptx_agent.edit.layout` (`stack`, `column`, `grid`, `place_labels`), **LP17** `content_area`.

### Data positions: scales (`ppt_scale`, `place`) -- new in T3, from arm C; tools removed post-T4

A scale is declared once and the tools compute positions from it; the model never does the proportion arithmetic arm C failed at.

- **Kinds:** `linear` (`min`, `max`), `date` (`start`, `end`, whole days **inclusive**, optional `exclude[]` ranges that close up, e.g. a holiday break) and `band` (named categories of equal width, `gap` between them and `padding` at the ends). Each maps onto `from` → `to` in slide points; a reversed range runs upwards (a y axis). The scale knows no axis.
- **Kept as a ref:** `ppt_scale(name="time", …)` keeps `$time` with the document's refs, so it lasts for the session, is listed in results and follows undo like any ref. Defining one changes no content and records no undo step.
- **Ticks:** `ppt_scale` returns tick positions and labels on request: round numbers (or a `step`), days, weeks, months, quarters or years (each with its start, centre and end, clipped to the scale), or the bands. Month headers and gridlines then come from one `items[]` call.
- **`place` on shape items** (`ppt_add_shape`, `ppt_layout action=place`): `scale_x`/`scale_y` name the scales; `x`, `x2`, `cx` and `y`, `y2`, `cy` are edges and centres as strings (a number, a date or a band name; without a scale, points); `w`/`h` in points; `dx`/`dy` shift. Two facts per axis fix it; one fact takes the other from `box`. A date or a band has a start, a centre and an end: `x` means the start of its day or band, `x2` the end, `cx` the centre, so `x` and `x2` of the same date cover the whole day.
- **`place` on connector ends:** `x`, `y` with `at_x`/`at_y` (start, center, end; default centre) -- a marker line from the first band's start to the last band's end spans every row.
- **Strict-schema compatible:** values are strings, so there are no type unions; the dispatcher validates them against the scale and names the field.
- **Library:** `pptx_agent.edit.scales` (`LinearScale`, `DateScale`, `BandScale`, `scale_from`), shared with the experimental `ppt_draw`.

### Copy and repeat (`ppt_copy`) -- built in T3; tool removed post-T4

Build one exemplar (say a step: a chevron, its activities and a box beneath), then copy it, instead of placing every part again.

- **Source:** one or more shapes, or a group (`source[]` addresses or refs), all on one slide; copied as a set, keeping relative positions.
- **Target:** the same slide, another slide (`to_slide`), or another open deck (`to_doc` + `to_slide`; both decks are locked, and each gets its own undo step).
- **Where:** `at` {x, y} (the top-left of the set's bounds), `repeat` {count, dx, dy}, or `cells` {x, y, w, h, rows, columns, gutter, count} with one copy centred in each cell. Arm C kept shape calls as the main route, so `repeat` and `cells` are included.
- **Per-copy text:** `texts[]` of {`copy`, `target` (a source address or ref within the set) or `token` (a `{{token}}` written in the exemplar's text), `text`}; formatting is kept.
- **Returns:** per copy, a map from each source address (nested members included) to the new address, its box, dropped glue and theme-colour changes; for a copy into another deck, that deck's fit and collision facts.
- **Library, LP23 `Slide.copy_shapes(shapes, to_slide=, at=, dx=, dy=)`:** fresh ids for every shape, nested group members included; connectors glued inside the set are re-glued to the copies, glue to shapes outside it is dropped and reported; pictures and media (shared within a deck, imported once by content into another), hyperlinks, charts with their workbooks and SmartArt are copied with their parts; a placeholder becomes a plain shape with its frame and run sizes written out; theme colours stay theme references, and a target theme that draws one differently is reported. A link to another slide copied into another deck, and slide-structure relationships, are refused.
- One call is one undo step per deck; `key` makes a retry safe.

### Design facts (`ppt_design_facts`) -- built in T3

**How it works without hardcoding judgement.** The library measures; it never decides. `slide.design_facts(region=, within=)` (**LP24**) returns:

| Fact | What is reported |
|---|---|
| Palette in use | colour families (a theme slot, a hue bucket or grey), each with its count, whether it is an accent, and its variants (the colour as written, hex, where used: fill, line or text, and the shapes); non-theme colours listed apart |
| Colour-coded sets | sets of like shapes (same preset, similar size; bars of one height count), their fills, hues and accent hues, whether they are colour-coded, and a **legend** if one exists (small swatches beside short text in a row or column), with the fills it covers and misses |
| Empty regions | the largest empty rectangles of the content area (text boxes count by their text), each with its share; large text-less panels with their fill and lightness |
| Alignment | shared left/centre/right/top/middle/bottom lines, and near-misses within `within` pt, with the offset |
| Shape vocabulary | presets with counts; square and rounded corners (with the radius); dashes; connector kinds |
| Text sizes | each effective size with its count and shapes |
| Lines over text | a line or connector crossing a text-bearing shape's text: both addresses, the length crossed, both z-indexes, whether the line is in front and whether the shape is opaque |

`slide.facts()` (**LP15**) adds the problem facts `check(include=["facts"])` reports: colours that are not theme colours, and wrap margins for titles and text close to wrapping. `check(include=["facts"])` returns them with a compact design section; `"design"` returns the design facts alone.

The only parameters are tolerances (`within`, a region), never thresholds of taste. On the spike's outputs they report, as facts, what the graders marked down: the "rainbow" (a set of four like boxes with four accent hues and no legend), dead bands (a full-width empty region of 14–28%), missing quadrant shading or focus (no panels, or panels at lightness 0.95–1.0), colour that carries meaning without a legend, and mixed corners. **The app's thinking layer decides** what they mean.

An app-side rule written against these facts, for example in the app's critique pass:

```python
for group in facts["groups"]["sets"]:
    if len(group["accent_hues"]) > 2 and group["legend"] is None:
        findings.append(Finding(group["shapes"],
            "more than 2 accent hues on like shapes and no legend: recolour or add one"))
```

The same rule could be one line of the app's guidance instead ("if `ppt_design_facts` shows more than 2 accent hues on like shapes, recolour"). Either way the rule lives in the app; the libraries and tools ship none.

### The SVG read view (an option, tested in trial 3) -- built in T3

`ppt_read_slides(detail="svg")` returns a per-slide SVG from pptx2svg's compact agent view (**LR3**, D12; `convert_pptx_to_agent_svg`), with pptx-agent's addresses:
- every shape is an element or `g` with `data-id="256.5"`, accepted verbatim by every write tool; the layout's and master's own shapes come first, marked `data-layer`;
- geometry is in slide points (`viewBox="0 0 960 540"`), numbers rounded to 0.1 pt;
- fills and strokes give the hex plus a `data-fill`/`data-stroke` theme name with its transforms (`accent1 lumMod=75%`);
- text is a `<text>` per shape with paragraph and run `tspan`s (size, weight, colour), no glyphs and no fonts;
- pictures, charts, tables and SmartArt are placeholders (`rect data-kind="picture"`), a table with its cell text and a chart with its type and title.

Measured per slide (whole tool result, chars/3.5): about 340 tokens for a title-only slide (most of it the layout's decoration), 2,600–3,700 for the spike's graphics slides, 2,800–4,100 for a real financial report; `detail="geometry"` is 0.4–1.3 times that and `outline` about a tenth. Trial 3 compares the two geometry views (G2). The read view is output only: nothing applies it back.

### SVG authoring (experimental `ppt_draw`) -- tool removed post-T4

Arm C decided G1 against SVG authoring as the main route (D11). The converter (spike, with arm C's fixes: escaping, element-named errors, insets on the shape, tree-aware connector sides, measuring an element as it will be built) ships as **`ppt_draw`** in the group `ppt_experimental`, which is **not** in the default definitions: an app opts in. It reads the same scales as the shape tools (`<g data-scale-x="$time">` with `x="@2026-11-16"`), so trial 3 can compare the two routes on equal footing. `measure=true` sizes text boxes without changing anything; `replace` redraws one top-level group in place. Its documentation is the profile (pptx-agent `docs/PROFILE.md`). Full-state SVG editing stays excluded (D7).

---

## PowerPoint tools

**Gap ids:**
- **LP**: a pptx-agent work item;
- **LE**: ooxml-edit;
- **LR**: a renderer;
- **TL**: composed in the tool layer from existing calls; no library change.

The ids of removed items (LP2–LP6, LW4) are retired, not reused; P14 and LP16 are the experimental `ppt_draw` and its converter. Lengths are in points at the tool boundary.

### Shared-shape tools (one definition each, dispatched by the document's kind)

| # | Tool | Purpose | Key parameters | Returns | Wraps | Gap |
|---|---|---|---|---|---|---|
| S1 | `open_document` | open a registered blob as a deck or document | `blob`; `as_template_source` | `doc`, kind, a short describe | `Document.open(bytes)` | warns `TemplateOpenedWarning` |
| S2 | `new_document` | a new deck or document | `kind`, `template_blob?`, `size?`/`page?`, `title?`, `author?` | `doc` | `Document.new(template=bytes…)` | — |
| S3 | `save_document` | hand the file to the app | `doc`, `name`, `format` (pptx/potx/docx/dotx/markdown/outline) | name, format, size, validate report (no bytes) | `to_bytes`, `validate(target=)`, `to_markdown`/`to_outline` → `session.outputs` | validate gate (see Safety) |
| S4 | `close_document` (`list_documents` removed post-T4: the application names the documents and inputs) | session housekeeping: the session holds a few documents | `doc` | — | session | golden w10 |
| S5 | `undo` | step through history | `doc`, `steps?`, `redo?` | version, steps taken; the refs of the state it returns to | `Document.undo/redo` | LE1 version |
| S6 | `find_text` | every match, with addresses | `doc`, `text`, `regex`, `scope` (slides, or a range/stories) | matches: address, kind, context | pptx `find_text`, docx `find` | — |
| S7 | `replace_text` | replace everywhere, or exactly once | `doc`, `find`, `replace`, `scope`, `expect` (`one`/`all`), `regex` | count, addresses | docx `replace`/`anchor().replace`; pptx `find_text` + `resolve().text` | pptx: TL now, **LP11** `deck.replace()` later |
| S8 | `render` | PNG of slides or pages | `doc`, `slides`/`pages`, `width` (≤2576) | images + sizes + token estimate | `render_png` | worker deadline; **LR4**, **LR5** |
| S9 | `check` | facts "as Office shows it" | `doc`, `scope`, `include` (fit, collisions, facts, design, validate, reflow, fields), `boxes?` (box-overlap mode) | a report with addresses; no verdicts | `overflows`, `collisions(boxes=)`, slide facts, design facts, `validate`, `layout().compare` | LP15, LP19, LP24, LW1 |
| S10 | `edit_chart` | add a chart from data; read a chart; data, titles, legend, data labels, gap width | `target`, `action` (read, add, set_values, set_value, add_category, remove_category, rename_category, add_series, remove_series, rename_series, set_title, set_axis_title, set_legend, show_data_labels, hide_data_labels, set_gap_width), add: `chart_type`, `categories`, `data[]` {name, values}, `box` (decks) or `width` (documents), `number_format`, `ref`; `series?`, `category?`, `values?`, `value?`, `text?`, `position?` | read: type, categories, series, number formats, workbook values; add: the new chart, its address (decks) or drawing id and paragraph (documents); otherwise the chart's state after the edit | `ooxml_edit.charts.Chart`, `add_chart` (both libraries adapt them); `read` runs as a read (`Tool.reads`) | S11 `read_chart` folded in (T5b); P18, P19 and W28 folded in (T4) |
| S12 | `edit_smartart` | node text, add or remove nodes | `target`, `action`, `node`, `text`, `parent?` | nodes | `Diagram.set_text/add_node/remove_node/add_child` | — |
| S13 | `set_properties` | metadata | `title?`, `author?`, `language?`, `subject?` | properties | pptx `title`/`author`/`language`; docx `set_properties` | — |
| S14 | `batch` | several calls as one: in order, all or none, one undo step | `ops[]` {tool, arguments} | each op's summary and data; checks once at the end | the dispatcher | core; non-strict (D14) |
| S15 | `read_blob` | an input's text (CSV, Markdown, plain text, JSON), a page at a time | `blob`, `cursor?` | the text page, name, type, size, lines | session blobs | ooxml-edit 0.5.0; images and packages refused |
| S16 | `describe` | the document at a glance; call once, first | `doc` | decks: P1's facts; documents: W1's | each library's handler | T5b: `ppt_describe` and `word_describe` had one schema |

### PowerPoint-specific (prefix `ppt_`)

| # | Tool | Purpose | Key parameters | Returns | Wraps | Gap |
|---|---|---|---|---|---|---|
| P1 | `describe` (S16; was `ppt_describe`) | the deck at a glance | `doc` | slides (id, n, title, layout, shape count, content area in pt), size, sections, theme colours, fonts, roles, tint ramps, layouts with placeholders (type, idx, bounds in pt), baseline problems | `slides`, `slide.title`, `layouts`, `Layout.placeholders`, `theme.colors/fonts/roles/ramps`, `validate` | LP17 (content area) |
| P2 | `ppt_read_slides` | slide content with ids | `doc`, `slides`, `detail` (outline/geometry/svg) | outline Markdown with ids; a per-shape geometry dump; or the per-slide SVG read view | `to_outline`; shape properties; pptx2svg agent view | `geometry`: TL; `svg`: **LR3** |
| P3 | (folded into P7, T5b) `ppt_add_shape` with `measure: true` | size text before building, with the same spec | `paragraphs` (text spec) or `text` + `size` + `bold?`; `width`; `height?`; `like?` (a shape address: its font, insets, wrap) or `preset?` (a new shape's defaults; `textbox` included) | lines, text height, **box height** (insets included: `fit_box`), widest line, `margin_to_wrap`, `near_wrap` | `measure_text`, `fit_height` | **LP18** |
| P4 | `ppt_set_text` | replace a shape's, cell's or notes' text | `items[]`: `target`, `text` (`\n` paragraphs, `\v` line breaks; keeps formatting) or `paragraphs` (text spec) | text_fit | `resolve(addr).text`, `set_text` | LP18 (spec) |
| P5 | `ppt_format_text` | run, paragraph and frame formatting | `items[]`: `target`; run: bold, italic, underline, strike, size, font, color, hyperlink; paragraph: alignment, level, bullet (none/bullet/number), space_before/after (pt), line_spacing; frame: autofit, font_scale, insets, anchor, wrap | text_fit | `Run.format`, `Paragraph.*`, `set_bullet`, `TextFrame.*` | — |
| P6 | (dropped, T5b) `ppt_set_text` on `256/notes` | speaker notes | `items[]`: `target` `256/notes`, `text` | text_fit | `notes_frame.set_text` | — |
| P7 | `ppt_add_shape` | autoshapes or text boxes; with `measure: true`, measures their text and adds nothing (P3) | `slide`, `items[]`: `preset` (enum of common presets + `other` with `preset_name`), `box` {x,y,w,h pt} and/or `place` (data units, D15), `text?` or `paragraphs?` (text spec), `fit_height?`, `fill?`, `line?`, `adjustments?`, `like?` (measuring: as this shape), `name?`, `ref?`; `measure?`; `key?` | address, text_fit, collisions; measuring: per item lines, text height, **box height**, widest line, insets, `margin_to_wrap`, `fits` (against `box.h` when over 0) | `add_shape`, `add_textbox(autofit="none")` | LP18 (spec), LP20 (preset list) |
| P8 | `ppt_set_shape` | **general setter** | `items[]`: `target`, any of: x, y, w, h, rotation, flip_h, flip_v, fill, gradient, line{color, width, dash, start, end}, preset, adjustments, name (frame fields stay in P5) | the shape after the edit | Shape properties, `LineFormat`, `Adjustments` | — |
| P9 | `ppt_add_picture` | insert or replace an image | `slide` or `target`, `image` (blob handle), `box?`, `keep` (frame/height/width/none), `anchor?` | address, native size | `add_picture(bytes)`, `replace_image`, `image_size` | — |
| P10 | `ppt_add_connector` | lines that stay attached | `items[]`: `kind` (straight/elbow/curved), `from`/`to` {shape, side} or a point {x, y} or `place` (data units), `line` {color, width, dash, start, end}, `ref?` | address, route, collisions | `add_connector`, `connection_site` | — |
| P11 | `ppt_arrange` | z-order, group, duplicate, delete | `targets` (addresses or `$ref`), `action` (front, back, forward, backward, group, ungroup, duplicate, delete), `dx?`, `dy?` | new addresses, collisions | `bring_to_front`…`send_to_back`, `group`, `ungroup`, `duplicate`, `delete` | — |
| P12 | `ppt_align` | PowerPoint's Align and Distribute (**removed post-T4**; `edit.arrange` stays) | `targets`, `edge` (left, center, right, top, middle, bottom) or `distribute` (horizontal/vertical), `to` (selection/slide/content/first), `gap?` (distribute; pt; omitted = equal spread) | moved shapes | — | **LP1**; P13 `ppt_distribute` folded in (T5b) |
| P15 | `ppt_edit_table` | cells, rows, columns, merges, cell fill and borders | `target`, `action` (set_cells, insert_row, delete_row, insert_column, delete_column, merge, split, set_widths, set_heights, format_cells), `cells[]` {row/col or row_label/col_label, text}, `like?`, `rows?`/`columns?`, `fill?`, `borders?` {side, width, color} | the table after the edit, by labels | `Table.*`, `cell_by_label`, `TableCell.fill`, `set_border` | P16 `ppt_format_table` split (T5b): fill and borders here, cell text through P5 on `256.7/cell1,2` |
| P17 | `ppt_add_table` | a new table | `slide`, `box`, `rows`, `columns`, `data[][]`, `header_row` | address | `add_table` + cells | — |
| P18 | (folded into S10, T4) `edit_chart` `add` | a new chart from data | `target` (the slide), `box`, `chart_type`, `categories`, `data[]` | address | `slide.add_chart` | **LE3 + LP7**, done |
| P19 | (folded into S10, T4) `edit_chart` `show_data_labels`, `hide_data_labels`, `set_gap_width` | data labels with a number format, gap width | `target`, `number_format?`, `value` | the chart | `Chart.set_data_labels`, `set_gap_width` | **LP8**, the part a task uses, done |
| P20 | `ppt_add_slide` | add from a layout, optionally filled | `layout`, `at?`, `title?`, `body?` (Markdown list), `notes?` | slide id, placeholders | `add_slide`, placeholders, `notes` | — |
| P21 | `ppt_draft_slides` | slides from a Markdown outline | `markdown`, `at?`, `layout_map?` | slide ids, `OutlineWarning`s, overflows | `insert_outline(images=blob mapping)` | — |
| P22 | `ppt_manage_slides` | duplicate, move, delete, find by title | `action`, `slide`, `to?`, `notes?`, `title?` | ids in order | `duplicate_slide(notes=)`, `move_slide`, `delete_slide`, `slide_titled` | — |
| P23 | `ppt_set_theme` | brand the theme | `colors?` {slot: hex}, `fonts?` {major, minor} | the theme, roles | `theme.set_colors`, `set_fonts` | layout/master editing **LP9** (later, D8) |
| P24 | `ppt_layout` | arrange shapes: row, column, grid, labels, data positions (**removed post-T4**; `edit.layout` stays) | `action` (stack, column, grid, place_labels, place), `targets`, `labels[]` or `items[]`, `box?`/`inside?`, `gap?`, `align?`, `justify?`, `equal?`, `fit_text?`, `rows?`, `columns?`, `gutter?`, `order?`, `fit?`, `sides?`, `distance?`, `max_center?`/`max_edge?`, `leader?`, `avoid?`, `label_width?` | moved shapes and boxes; placed labels with their distances, unplaced labels with blockers, collisions | `edit.layout`, `edit.arrange` | **LP17, LP21, LP22** |
| P25 | `ppt_copy` | copy or repeat shapes or a group, with new text (**removed post-T4**; `Slide.copy_shapes` stays) | `source[]`, `to_slide?`, `to_doc?`, `at?`, `repeat?` {count, dx, dy}, `cells?` {x, y, w, h, rows, columns, gutter, count}, `texts[]?` {copy, target or token, text}, `ref_prefix?`, `key?` | per copy: source → new address map; dropped glue, theme changes; the target deck's facts | `Slide.copy_shapes` | **LP23**; repeat/cells/texts TL |
| P26 | `ppt_design_facts` | measurable design facts of a slide | `slide`, `region?` (box), `within?` (pt), `include?` (palette, groups, empty, alignment, vocabulary, text_sizes, z_order) | the facts, with addresses; no verdicts | `Slide.design_facts` | **LP24** |
| P27 | `ppt_scale` | declare a data scale once, as `$name`; its ticks (**removed post-T4**, with `place`; `edit.scales` stays) | `name`, `kind?` (linear/date/band; omitted: read an existing scale), `from`, `to` (pt), `min`/`max`, `start`/`end`/`exclude[]`, `bands[]`/`gap`/`padding`, `ticks?` {every, step, format} | the scale, its ticks (positions and labels) | `edit.scales` | D15 |
| P14 | `ppt_draw` (experimental, not in the defaults; **removed post-T4**) | draw a graphic from SVG in the authoring profile as native shapes | `slide`, `svg`, `box?`, `replace?`, `measure?` | SVG id → address, text fits, warnings | `edit.svgprofile` | **LP16** |
| P28 | `ppt_comments` (post-T4) | PowerPoint's modern comments: threads on a slide or a shape | `action` (list, add, reply, resolve, reopen, edit, delete), `items[]` {target, comment, text, resolve, ref}, `author?`, `slides?`, `open_only?`, `cursor?` | threads (`c:` + 8 hex digits), replies; `list` only reads | `Document.add_comment`… (`edit/comments.py`) | **LP25**; golden p10 |

With the shared tools, that is **35 definitions** reachable for a deck after T5b's rationalisation (20 PowerPoint tools and 15 shared ones; 36 with the experimental `ppt_draw`), down from 40; T4 added none (P18 and P19 are actions of `edit_chart`). **Post-T4: 31** (17 PowerPoint tools and 14 shared ones): `ppt_align`, `ppt_layout`, `ppt_copy`, `ppt_scale` and `list_documents` went, `ppt_comments` came, and `ppt_draw` is gone. Thirteen of them are "core" (see Model guidance), and the rest load through tool search.

**PowerPoint library gaps (work items):**
- **LP1 `pptx_agent.edit.arrange`: `align(shapes, edge, to)` and `distribute(shapes, axis, to, gap=None)`** (M). Done in T3.
  - These match PowerPoint's Align and Distribute: relative to the selection or the slide. They use `drawn_bounds` for rotated shapes and move groups as one; the whole call is one undo step.
- **LP7 `slide.add_chart(type, categories, series, left, top, width, height, title=…)`,** on LE3 (M). Done in T4.
- **LP8 chart formatting in `ooxml_edit.charts`** (M–L). Done in T4 for what a task uses: data labels on/off with a number format (`Chart.set_data_labels`), and the gap width (`set_gap_width`; p9 needs it, since at PowerPoint's default 219 its labels are wider than their columns). Left out, because no task uses them: series colour by theme colour (a new chart already takes the theme's accents in order, which is what p9's "theme colours" asks), and the value axis's min, max and major unit.
- **LP9 layout and master editing** (L). Trial 2's N17: template branding beyond the theme. Deferred (D8).
- **LP11 `Document.replace(find, replace, slides=, regex=)`** (S): parity with docx.
- **LP14 `LabelError.candidates`** (S): structured candidates for `valid_options`.
- **LP15 `slide.facts()`** (M; done in T3). The problem facts, no rules or thresholds of taste:
  - overflow, collisions and off-slide shapes (existing `overflows`/`collisions`, plus LP19's box mode);
  - fills, lines and text colours that are not theme colours (from `Color.resolve`);
  - wrap margins (`margin_to_wrap`/`near_wrap`) for titles and text boxes.
  - (Near-alignment and text sizes moved to LP24.)
- **LP16 the SVG authoring converter:** `pptx_agent.edit.svgprofile`, merged from the spike branch in T3 behind the experimental `ppt_draw` (G1 decided).
- **LP17 `content_area(slide)` public** (S): the body placeholder's box, or the slide minus the title band and margins. It exists on the spike branch only.
- **LP18 one measuring model** (M):
  - a `TextSpec` (paragraphs, runs, bullets, frame) accepted by both `Shape.set_text` and `measure_text`;
  - `measure_text(spec, width, like=shape | preset=)` uses the target's, or a new shape's, default insets, font and wrap, so building and measuring cannot drift;
  - `fit_box(spec, width)` returns the box height, insets included;
  - document `bold=` (and every keyword) in prose, not only the signature.
- **LP19 box-overlap mode in `collisions()`** (S): `collisions(boxes=True)` also reports text-bearing shapes whose boxes overlap even when their visible text does not (the spike's one structural miss).
- **LP20 small fixes** (S):
  - a list of preset names (`pptx_agent.PRESETS`), with adjustment names (`adj` vs `adj1`) per preset;
  - unit helpers beside `Pt`: `Inches`, `Cm`, and `to_pt`;
  - document that a marker line drawn in front of bars counts as a collision unless it is sent behind them.
- **LP21 `pptx_agent.edit.layout`: `stack`, `column`, `grid`** (M; done in T3): positions for given shapes in a box, with gap, gutter and alignment; groups move as one; one undo step.
- **LP22 `place_labels(labels, sides, distance, max_center/max_edge, leader, avoid)`** (M–L; done in T3): greedy placement by side preference, then nearby offsets, measured with `measure_text`, checked with `collisions(boxes=True)`; returns unplaced labels.
- **LP23 `Slide.copy_shapes(shapes, to_slide, at=)`** across slides and decks (L; done in T3): see "Copy and repeat".
- **LP24 `slide.design_facts(region=, within=)`** (M–L; done in T3): see "Design facts".
- **LP25 modern comments** (M; done post-T4): `Document.comments`, `add_comment`, `reply_to_comment`, `resolve_comment`, `reopen_comment`, `edit_comment`, `delete_comment` (`edit/comments.py`), for `ppt_comments`.
- **LP26 per-edit layout facts** (M; done post-T4): `pptx_agent.edit.feedback.layout_facts(slide, touched)`, for `checks.layout`.
- **Later:** importing a whole slide from another deck (`Document.import_slide`). No trial task needs it; `ppt_copy` covered shapes (it left the tools post-T4; `Slide.copy_shapes` stays).

---

## Word tools (prefix `word_`; plus S1–S13)

| # | Tool | Purpose | Key parameters | Returns | Wraps | Gap |
|---|---|---|---|---|---|---|
| W1 | `describe` (S16; was `word_describe`) | the document at a glance | `doc` | headings tree (id, level, numbered list/text, block count), sections (id, page setup, header/footer stories), styles in use, comment and revision counts by author, fields by type, tables, drawings, charts, pages, compatibility mode, baseline problems | `paragraphs`, `sections()`, `styles`/`usage`, `comments()`, `changes()`, `fields()`, `charts()`, `layout().page_count` | TL (heading tree); `doc.outline()` **LW6** later |
| W2 | `word_read` | Markdown with ids | `range?`, `view` (final/markup/original), `stories` (body/all), `cursor?` | Markdown page, `next_cursor` | `to_markdown` | paging TL |
| W3 | `word_inspect` | exact formatting of a block | `target`, `layout` (bool) | runs, effective properties, style, list, placements | `state(range, layout=True)` | — |
| W4 | `word_set_tracking` | write edits as tracked changes or not | `on`, `author`, `word_switch?` | the mode | session + `tracking()`, `word_tracks_changes` | — |
| W5 | `word_set_text` | rewrite a paragraph; tracked, only the changed words | `target`, `text` | id, renamed | `Paragraph.set_text` | — |
| W6 | `word_insert_text` | insert at an anchor | `find` or `target` (range), `where` (before/after), `text` | range id | `anchor().insert_before/after`, `insert_text` | — |
| W7 | `word_delete` | delete text or blocks | `target` or `find`, `collapse_space` | removed | `TextRange.delete(collapse_space=)`, `delete_block` | — |
| W8 | `word_insert_markdown` | write Markdown in the document's styles | `markdown`, `at` (end, after:id, before:id, replace:id..id), `style_map?` (a named-key object) | blocks, created, warnings | `insert_markdown(images=blob mapping, fetch=None, html="refuse")` | — |
| W9 | `word_format` | **general setter** for text and paragraphs | `target`; `paragraph_style?`, `character_style?`; run: bold, italic, underline, strike, size, font, color, highlight; paragraph: alignment, space_before/after, line_spacing, indent_left/right/first, keep_with_next, page_break_before; `clear_direct` | result | `set_paragraph_style`, `set_character_style`, `format_range`, `format_paragraph`, `clear_formatting` | — |
| W10 | `word_lists` | list membership | `targets`, `action` (add, remove, level, restart, continue, format), `kind` (bullet/number), `level?`, `start?` | list ids | `ListOps` | — |
| W11 | `word_move` | move a section or blocks | `section_heading` or `range`, `before`/`after` | moved ids | `section_blocks`, `move_blocks` | — |
| W12 | `word_copy_from` | copy blocks from another open document | `source_doc`, `range` or `section_heading`, `at`, `styles` (use_destination/keep_source/merge), `style_map?`, `unmapped` (import/body) | copied map, warnings | `copy_blocks` | — |
| W13 | `word_changes` | list, accept or reject tracked changes | `action` (list, accept, reject), `author?`, `kind?`, `within?`; list: `detail` (grouped/records), `cursor?`; accept, reject: `ids?`, `all?` | list: changes with ids and old/new text (a read: `Tool.reads`); accept, reject: count, what remains | `changes()`, `revisions()`, `accept`/`reject`/`accept_all`/`reject_all`, `Change.accept/reject` | W14 `word_review_changes` merged in (T5b) |
| W15 | `word_comments` | comment threads | `action` (list, add, reply, resolve, reopen, edit, delete), `target`/`find`, `comment?`, `text?` | thread(s) | `CommentOps`, `comments()` | — |
| W16 | `word_sections` | breaks and page setup | `action` (insert_break, remove_break, set), `after?`, `kind?`, `section`, orientation, margins (pt), columns, page size | sections | `insert_section_break`, `remove_section_break`, `set_section` | — |
| W17 | `word_headers_footers` | header and footer content | `section`, `which` (header/footer), `type` (default/first/even), `action` (set, remove, link, unlink), `text?`, `page_number` (none/after_text/`Page X of Y`) | story ids | `add_header`/`add_footer`, `remove_*`, `link_to_previous`, `insert_page_number`, `set_even_and_odd_headers` | — |
| W18 | `word_fields` | TOC, captions, cross-references, page fields, update | `action` (list, insert_toc, insert_caption, insert_cross_reference, insert_date, insert_field, update), `at`, `levels?`, `bookmark?`, `label?` | fields; unknown | `FieldOps`, `insert_cross_reference`, `update_fields` | stale detection **LW2**; an empty-TOC warning (trial N8) if not already shipped |
| W19 | `word_notes` | footnotes and endnotes | `action` (insert, edit, delete, move), `at`/`note`, `kind`, `text` | note id | `NoteOps` | — |
| W20 | `word_links` | hyperlinks and bookmarks | `action` (add_hyperlink, add_bookmark, rename_bookmark, remove_bookmark, list), `target`/`find`, `url?`, `bookmark?` | ids | `LinkOps` | — |
| W21 | `word_styles` | the style sheet | `action` (list, describe, add, modify, remove, purge_unused), `name`, `kind`, `based_on?`, formatting fields as in W9, `replacement?` | styles with usage | `Styles.find/add/modify/remove/usage/purge_unused`, `resolve` | — |
| W22 | `word_insert_table` (**removed post-T4**: a new table is a Markdown table through `word_insert_markdown`; no model called it) | a new table | `at` (after/before id), `rows`, `columns`, `data[][]`, `header_rows`, `style?`, `widths?` (pt or %) | table id | `insert_table` | — |
| W23 | `word_edit_table` | structure and cell text | `table`, `action` (set_cells, insert_row, delete_row, insert_column, delete_column, merge, split), `cells[]` {row, col, text}, `at?`, `span?` | table as GFM + ids | `TableOps`, cell paragraphs `set_text` | cells by label: TL (from header row/column) |
| W24 | `word_format_table` | table, row and cell formatting | `table`, `scope` (table/row/cell/column), `index?`, width, alignment, look, shading, borders, vertical_alignment, height, height_rule, repeat_header | — | `set_table`, `set_row`, `format_cell`, `set_column_width` | — |
| W26 | `word_drawings` | pictures, text boxes and shapes: insert, move, resize, wrap, z-order | `action` (list, insert_picture, insert_text_box, insert_shape, move, resize, float, inline, set), `target?`, `image?` (blob handle), `at?`/`find?`, `width?` (pt; a picture keeps its ratio), `alt_text?` (needed for a picture), `wrap?` (an insert floats with it), `x?`, `y?`, `align?`, `against?` (column/margin/page), `ref?`, `key?`, … | drawing id | `insert_picture(bytes)`, `float_drawing`, `set_drawing`, `DrawingOps` | W25 `word_insert_picture` folded in (T5b) |
| W27 | `word_controls` | content controls | `action` (list, insert, fill, remove), `at`, `type`, `items?`, `value?` | ids | `ControlOps`, `content_controls()` | — |
| W28 | (folded into S10, T4) `edit_chart` `add` | a new chart from data | `target` (the paragraph it follows), `chart_type`, `categories`, `data[]`, `width?` | drawing id, paragraph | `doc.insert_chart` | **LE3 + LW3**, done |
| W29 | `word_template` | template work | `action` (upgrade_to_modern; apply_styles_from in T4) | reflow pages | `upgrade_to_modern`; a template is saved by S3 `save_document(format="dotx")` (its `save_as_template` action dropped in T5b) | "apply a template's styles to an open document": **LW5** |

That is 25 Word tools and 16 shared ones: **41 definitions** reachable for a document after T5b, down from 44; T4 added none (W28 is `edit_chart`'s `add`). **Post-T4: 39** (24 Word tools and 15 shared ones): `word_insert_table` and `list_documents` went. `word_drawings`' `insert_picture` takes `at: "after:<block>"` (or `before:`) for a picture in a paragraph of its own, with `align` as that paragraph's alignment.

**Word library gaps:**
- **LW1 `doc.problems()`: layout facts** (M–L), from `layout()`. **Dropped in T4** by the user's decision: no task needed it -- every Word run in trial 3 scored 10 without it.
  - a table wider than its text column;
  - a picture past the margins;
  - a heading last on its page despite `keep_with_next`;
  - an empty TOC (trial 2's N8);
  - a REF to a missing bookmark;
  - text under 8 pt.
- **LW2 field staleness** (S–M). **Dropped in T4** by the user's decision: no task needed it -- every Word run scored 10, and the models updated fields themselves. a field whose inputs changed since its last update (headings for a TOC, bookmarks for a REF, pagination for PAGEREF) is marked `stale` in `fields()`. The tool result then says "run `word_fields update`".
- **LW3 `doc.insert_chart(at, type, categories, series, width=…)`,** an inline drawing, on LE3 (M). Done in T4.
- **LW5 `doc.apply_template(bytes, styles=True, headers=False)`** (M): bring a template's styles into an open document. Today only `Document.new(template=)` and `copy_blocks` exist.
- **LW6 `doc.outline()`** (S): the heading tree as data. It can be composed in the tool layer from `paragraphs()` and outline levels, but is cleaner in the library.

**ooxml-edit gaps:**
- **LE1 `History.version`:** monotonic, restored on undo/redo (S).
- **LE2: `ooxml_edit.tools` itself** (T0).
- **LE3 a new chart from data:** the chart part (bar, column, line and pie at least), with an embedded workbook written to match and a `GraphicHost` insertion hook, so pptx (LP7) and docx (LW3) each add only the frame (L). Done in T4 (`ooxml_edit.charts.create`).

**Renderer items:**
- **LR1:** docx2svg still styles TOC entries as links (trial #18).
- **LR2:** pptx2svg character spacing (trial 2's N16).
- **LR3: pptx2svg compact read mode** (M; must be ready for T6): a compact per-slide SVG with `data-id` addresses on every shape, geometry in points, `data-fill` theme tokens, boxed text without glyph outlines or embedded fonts, and placeholders for pictures, charts and tables. Exit: every `data-id` resolves through `resolve()`; tokens per slide are measured against the geometry dump on the trial decks.
- **LR4: pptx2svg bullet "blob"** (S): `set_bullet()` bullets rendered as large dark blobs in the library's render (the spike, p7 A2), while PowerPoint draws them correctly. It misleads every render-based check.
- **LR5: pptx2svg custom-geometry outlines** (S): outlines of custom-geometry shapes are not drawn in the library's render; PowerPoint draws them (the spike).

LR1 and LR2 do not block the tools. LR4 and LR5 should land before T6, since agents trust the render.

### Tools shared in shape across both formats

S1–S16 are single definitions that dispatch by the document's kind. Each library declares the same definition with its own handler; `Toolbox` merges same-named tools whose definitions are identical into one tool with a handler per kind, and refuses two different definitions under one name.
- Their parameters are the union, and each says which format it applies to (`slides` for pptx, `pages`/`range` for docx). The dispatcher returns `invalid_arguments` naming the right field.
- `edit_chart` (reading too) and `edit_smartart` wrap the same `ooxml_edit.charts` classes in both formats.
- `describe` (S16) is shared because the two describe tools had one schema, `{doc}`; one description says what each kind returns. It saves about 140 tokens when both formats are loaded and costs nothing when one is.

These pairs stay format-specific, because their parameters differ too much:

| Purpose | PowerPoint | Word |
|---|---|---|
| read | `ppt_read_slides` | `word_read` |
| set text | `ppt_set_text` | `word_set_text` |
| general setter | `ppt_set_shape`/`ppt_format_text` | `word_format` |
| add a picture | `ppt_add_picture` | `word_drawings` (`insert_picture`) |
| new table | `ppt_add_table` | `word_insert_table` |

---

## Model guidance

**A system-prompt fragment of about 600 tokens, shipped as `prompts.SYSTEM`**, plus one fragment per format of about 300–400 tokens (T5: 1,163 and 1,111 counted online, shared plus format). The app concatenates them, followed by its own guidance (see "The thinking layer"). The shipped fragments are mechanics only, with no style rules.

1. **Plan before editing:**
   - Call `describe` once, then read only what the task touches.
   - Before building a graphic, read the theme, the content area and the layout placeholders, and size text by measuring with `ppt_add_shape` (`measure: true`), using the same text spec you will build with.
2. **Address by id:**
   - Copy addresses exactly from read results, including `data-id`s in the SVG view.
   - Never paste Markdown from `ppt_read_slides`/`word_read` into a set-text tool; outline text is escaped.
   - Use `find_text` when you know the words but not the id.
3. **Prefer coarse tools:**
   - (until post-T4) `ppt_copy`, `ppt_layout`, `ppt_align` and `ppt_scale` for repeated elements, rows, labels and data positions; since post-T4 the model computes positions in `items[]` and reads `checks.layout`;
   - `word_move` for sections; `ppt_draft_slides`/`word_insert_markdown` for bulk text.
   - Use the general setter for adjustments.
4. **Read every result's `checks`:**
   - They are facts. Resolve overflows and collisions the task does not intend (split, resize, `font_scale`, send to back).
   - Never count a render's shrunk text as fitting: fit is `checks`, not pixels (trial 1's silent failure).
5. **Render sparingly:** once after building each graphic or changed page region, and once before finishing. Do not render after every edit.
6. **Before finishing:**
   - Run `check` with `include=["fit","collisions","facts","validate"]` for decks, or `["validate","reflow","fields"]` for documents. Update fields if any are stale.
   - Read `ppt_design_facts` for slides with graphics, and act on them as the app's guidance says.
   - Then `save_document`. Saving refuses new validation problems; fix them, don't work around them.
7. **Judgement:** use tracked changes for anything a reviewer must approve. Requests to hide, backdate or misstate are declined, and the decline is stated in the reply (trial W2).
8. **Units and colours:**
   - Points everywhere, dates `YYYY-MM-DD`.
   - Prefer theme colour names to hex, so the result follows the theme when it changes.

**Keeping descriptions short (done in T5):**
- Each tool description is 1–3 sentences: the purpose, when to use it instead of its neighbour, and what it returns.
- Each property description is at most 15 words, with the default stated. Units are not repeated, because the prompt says lengths are points.
- Nested properties whose name says it all go without a description (`bold`, `x`, a run's `font`). An empty `required` list is left out.
- Enums replace prose.
- The text spec is described once (in `ppt_add_shape`) and referred to by the others.
- No `input_examples` at first. Trial 3 checks whether one `ppt_copy` example earns its tokens on Claude.

**Budgets after T5's rationalisation (T5b).** Offline is compact JSON ÷ 3.5. Online is Anthropic's count-tokens on `claude-sonnet-5-5` (Opus 5.5 counts the same, T5), less the empty request; it includes the 286-token hidden tool prompt.

| | Tools | Offline T5 | Offline T5b | Online T5 | Online T5b |
|---|---|---|---|---|---|
| Deck core | 13 | 3,313 | 3,352 | 5,051 | 5,097 |
| All deck tools | 40 → 35 | 13,882 | 12,747 | 20,622 | 18,848 |
| Word core | 13 → 12 | 2,878 | 2,384 | 4,250 | 3,539 |
| All Word tools | 44 → 41 | 11,444 | 11,046 | 16,123 | 15,544 |
| Shared prompt + deck fragment | | 986 | 1,005 | 1,163 | 1,170 |
| Shared prompt + Word fragment | | 968 | 996 | 1,111 | 1,142 |

- The deck core grew by 46 tokens: `describe`'s one description says what both kinds return. `word_format` leaving the Word core took 711 off every Word request.
- **The budget policy (T5b):**
  - **What binds is what a request loads:** the core, at most **5,500 tokens per format as count-tokens counts them**. Each library's `tests/test_tools_online.py` (marked `provider`, run with a key) counts it and sends one request with the definitions as the adapter sends them. Today: decks 5,097, Word 3,539.
  - **An offline proxy in CI:** the core's compact JSON ÷ 3.5, times the measured ratio **1.48**, at most 5,500 (an offline core of at most 3,716).
  - **A regression guard on every definition, offline:** at most 13,000 for decks and 11,300 for Word, the T5b figures plus about 2%. It is a guard, not a budget: deferred definitions cost nothing until tool search loads them.
  - The earlier budgets (core 4,000 and all 12,000 offline; decks 14,000) are retired. 12,000 real tokens for every definition would be about 8,100 offline, out of reach for either format without dropping groups, and it measured what no request pays.

**Tool groups and deferred loading (T5, regrouped in T5b):**
- **Core, never deferred (13 for decks, 12 for Word):**
  - shared: `open_document`, `new_document`, `save_document`, `undo`, `describe`, `find_text`, `replace_text`, `render`, `check`, `batch`;
  - pptx: `ppt_read_slides`, `ppt_set_text`, `ppt_set_shape`;
  - Word: `word_read`, `word_set_text`. `word_format` loads with the style tools.
- **Groups.** Every tool is in exactly one group, and each has fewer than 10 tools, as OpenAI recommends for namespaces:

  | Group | Tools |
  |---|---|
  | `shared_misc` (6) | `list_documents`, `close_document`, `read_blob`, `set_properties`, `edit_chart` (reads too), `edit_smartart` |
  | `ppt_text` (1) | `ppt_format_text` (table cells too) |
  | `ppt_graphics` (4) | `ppt_add_shape` (measures too), `ppt_add_connector`, `ppt_arrange`, `ppt_copy` |
  | `ppt_objects` (3) | `ppt_add_picture`, `ppt_add_table`, `ppt_edit_table` (fill and borders too) |
  | `ppt_slides` (4) | `ppt_add_slide`, `ppt_draft_slides`, `ppt_manage_slides`, `ppt_set_theme` |
  | `ppt_layout` (4) | `ppt_align` (distributes too), `ppt_layout`, `ppt_scale`, `ppt_design_facts` |
  | `ppt_experimental` (opt-in, not in the defaults) | `ppt_draw` |
  | `word_text` (4) | `word_inspect`, `word_insert_text`, `word_delete`, `word_insert_markdown` |
  | `word_review` (3) | `word_set_tracking`, `word_changes` (list, accept, reject), `word_comments` |
  | `word_structure` (7) | `word_move`, `word_copy_from`, `word_sections`, `word_headers_footers`, `word_fields`, `word_notes`, `word_links` |
  | `word_objects` (5) | `word_insert_table`, `word_edit_table`, `word_format_table`, `word_drawings` (inserts pictures too), `word_controls` |
  | `word_style` (4) | `word_format`, `word_lists`, `word_styles`, `word_template` |

  **Post-T4:** `shared_misc` lost `list_documents`; `ppt_graphics` lost `ppt_copy` and took `ppt_design_facts`; the `ppt_layout` and `ppt_experimental` groups are gone; `ppt_review` (1) holds `ppt_comments`; `word_objects` lost `word_insert_table`.

- **How an application picks (the defaults since ooxml-edit 0.6.0):**

  | Call | Sends |
  |---|---|
  | `definitions("anthropic")` | every tool; the non-core ones `defer_loading`, the BM25 tool-search tool first, `cache_control` on the last core tool. For Sonnet 5.5, Opus 5.5 and Haiku 4.5 |
  | `definitions("anthropic", groups=[…])` | the core and the named groups, all loaded, with no tool search: for Sonnet 5 or an application that knows the task |
  | `definitions("openai-responses")` | the core as functions, each other group a `namespace` with `defer_loading`, and `{"type": "tool_search"}` |
  | `definitions("openai-chat", groups=[…])` | the core and the named groups; Chat Completions has no tool search, so asking it to defer is an error |
  | `toolbox.allowed_tools([…], provider=…)` | an OpenAI `tool_choice` (Responses or Chat shape) that narrows a turn's calls without changing the cached `tools` |

  `defer=` overrides the default either way.
- **Strict on Claude (T5; re-tuned in T5b).** Only writing tools are strict, in each format's `strict_first` order, while the request stays under 20 tools, 24 optional parameters and 32 free-text strings:
  - **decks:** `ppt_add_slide`, `edit_chart`, `ppt_align`, `ppt_manage_slides`, `ppt_draft_slides` (5 tools, 23 optional parameters, 21 strings; 28 golden calls). `ppt_align`'s distribute options cost three optional parameters, which `replace_text` (one golden call) gave up; `ppt_set_notes` is gone.
  - **Word:** `replace_text`, `word_changes`, `word_set_tracking`, `edit_chart`, `word_insert_markdown`, `word_template` (6 tools, 24 optional parameters, 23 strings; 23 golden calls, `word_changes`' lists and `edit_chart`'s reads included). `word_set_text` and `word_delete` (one call each) gave way to `edit_chart` (four).
  - The order follows the writing tools the goldens call most. `ppt_set_text` alone would take 20 of the 24 optional parameters, so it, the shape and layout tools and `batch` are validated by the dispatcher instead. Inside `batch`, strict never applies anyway.
  - Both plans were sent in one request per format on Sonnet 5.5 (T5b): 200, no "Schema is too complex".


**System-prompt fragments (T5).**
- `prompts.SYSTEM` (shared, about 600 tokens) covers:
  - planning: describe once, read only what the task touches, search for tools loaded on demand;
  - addresses and `$ref`;
  - fewer, larger calls (`items[]`, `batch`) (the "layout tools rather than arithmetic" line went post-T4);
  - results and their `checks`;
  - rendering sparingly;
  - checking before saving;
  - units;
  - document content as data.
- The deck fragment (`pptx_agent.tools.PROMPT`) covers:
  - the describe-then-read order and the address grammar;
  - the text spec and measuring;
  - layout, copy and scales (until post-T4);
  - what the checks name, `checks.layout` among them (post-T4);
  - the save-time check and design facts.
- The Word fragment (`docx_agent.tools.PROMPT`) covers:
  - the address grammar;
  - Markdown that is escaped;
  - word-level edits;
  - tracking;
  - fields;
  - the save-time check.
- None carries a style rule. `pptx_agent.tools`' documentation shows how an application adds its own house rules over `ppt_design_facts` through `toolbox.system_prompt(extra=…)`.

**The rationalisation (decided by the user; done in T5b, ooxml-edit 0.7.0).** T5 assessed every tool: tokens, group, golden calls, trial evidence and overlap. The user approved these changes:

| Change | Before | After |
|---|---|---|
| Drop a duplicate | `ppt_set_notes` | `ppt_set_text` on `256/notes` |
| Fold a measuring tool into the building one | `ppt_measure_text` | `ppt_add_shape` with `measure: true`: adds nothing, returns each item's lines, text height, `box_height`, insets and, against `box.h`, `fits`; `like` measures as an existing shape. The description and the prompt keep the word "measure" for tool search |
| An action, not a tool | `ppt_distribute` | `ppt_align` with `distribute` (exactly one of `edge`, `distribute`) |
| Split by what it changes | `ppt_format_table` | cell text: `ppt_format_text` on cell addresses; fill and borders: `ppt_edit_table`'s `format_cells` |
| An action, not a tool (both formats) | `read_chart` | `edit_chart` with `action: "read"` |
| Merge | `word_changes`, `word_review_changes` | `word_changes` with `action` list, accept or reject |
| An action, not a tool | `word_insert_picture` | `word_drawings` with `action: "insert_picture"` (`wrap` floats it; `align`, `against`, `x`, `y` place it) |
| Drop an action | `word_template`'s `save_as_template` | `save_document(format="dotx")`; `upgrade_to_modern` stays |
| Out of the core | `word_format` (core) | the `word_style` group, loaded by tool search |
| Share one definition | `ppt_describe`, `word_describe` (one schema, `{doc}`) | `describe` (S16), a handler per kind |

- **Reading modes of changing tools.** `edit_chart`'s read, `ppt_add_shape`'s measure and `word_changes`' list are calls of a changing tool that change nothing. `Tool.reads` (ooxml-edit 0.7.0), a function of the arguments, marks them: such a call runs as a read, with no undo step, no version, no checks and no retry key, alone or in `batch`. `Call.changing` tells a handler; docx-agent's wrapper skips the layout-before and tracking for them.
- **Not approved, so left as is:** merging every per-format pair into shared tools (only `describe`, whose schemas were identical, is shared; the others differ in shape and would carry both formats' options into every request), and collapsing the setters into one `ppt_set` (a 2,500-token tool that is never strict, with properties that apply to some targets and not others).
- **The tool lists after T5b:**
  - **decks (35):** shared `open_document`, `list_documents`, `close_document`, `undo`, `read_blob`, `new_document`, `save_document`, `find_text`, `replace_text`, `render`, `check`, `set_properties`, `describe`, `batch`, `edit_chart`, `edit_smartart`; `ppt_read_slides`, `ppt_set_text`, `ppt_set_shape`, `ppt_format_text`, `ppt_add_shape`, `ppt_add_connector`, `ppt_arrange`, `ppt_add_picture`, `ppt_add_table`, `ppt_edit_table`, `ppt_copy`, `ppt_add_slide`, `ppt_draft_slides`, `ppt_manage_slides`, `ppt_set_theme`, `ppt_align`, `ppt_layout`, `ppt_scale`, `ppt_design_facts`;
  - **Word (41):** the same 16 shared tools; `word_read`, `word_inspect`, `word_set_text`, `word_insert_text`, `word_delete`, `word_insert_markdown`, `word_format`, `word_set_tracking`, `word_changes`, `word_comments`, `word_move`, `word_copy_from`, `word_sections`, `word_headers_footers`, `word_fields`, `word_notes`, `word_links`, `word_insert_table`, `word_edit_table`, `word_format_table`, `word_drawings`, `word_controls`, `word_lists`, `word_styles`, `word_template`.
- **The tool lists after post-T4:**
  - **decks (31):** shared `open_document`, `close_document`, `undo`, `read_blob`, `new_document`, `save_document`, `find_text`, `replace_text`, `render`, `check`, `set_properties`, `describe`, `batch`, `edit_chart`, `edit_smartart`; `ppt_read_slides`, `ppt_set_text`, `ppt_set_shape`, `ppt_format_text`, `ppt_add_shape`, `ppt_add_connector`, `ppt_arrange`, `ppt_add_picture`, `ppt_add_table`, `ppt_edit_table`, `ppt_add_slide`, `ppt_draft_slides`, `ppt_manage_slides`, `ppt_set_theme`, `ppt_design_facts`, `ppt_comments`;
  - **Word (39):** the same 15 shared tools; `word_read`, `word_inspect`, `word_set_text`, `word_insert_text`, `word_delete`, `word_insert_markdown`, `word_format`, `word_set_tracking`, `word_changes`, `word_comments`, `word_move`, `word_copy_from`, `word_sections`, `word_headers_footers`, `word_fields`, `word_notes`, `word_links`, `word_edit_table`, `word_format_table`, `word_drawings`, `word_controls`, `word_lists`, `word_styles`, `word_template`.


---

## The thinking layer: what the app supplies (S)

The libraries and tools ship **no** house style, palette rules, legend or gridline rules, density limits or grid. Design judgement belongs to the application. The tools supply facts (`check`, `ppt_design_facts`); the app supplies the rules. The toolbox offers two hooks and documents them:

- **Guidance.** The app appends its own text after the shipped fragments, through `toolbox.system_prompt(extra=…)`. Examples are a house style, a brand palette, or "if `ppt_design_facts` shows more than 2 accent hues on like shapes, recolour; if colour carries meaning, add a legend". The docs show how to write such guidance; no defaults are shipped.
- **Critique pass.** (Post-T4: the hook is not built; `check`'s `app` include returns nothing. Each format's `GUIDANCE.md` shows the application running the pass itself through `toolbox.dispatch`.) The app may register `critique(doc, slides, renders, facts) -> list[Finding]`, where `facts` include the design facts (LP24) and the problem facts (LP15/LW1). It typically makes its own model call over the render plus the facts, or runs rules like the example in "Design facts". `check(include=[…, "app"])` runs it and returns its findings under `app_findings`, labelled as coming from the app. The app may run it after the agent finishes instead.

The trial harness acts as an app. It supplies each task's brief and nothing more, so trial 3 measures the tools, not a hidden style guide.

---

## Safety and robustness

- **No file system access at all** (D5).
  - There are no paths in any tool. Documents and inputs are in-memory bytes the app registered. Outputs go to the app, never the model.
  - No URL fetching (`insert_markdown(fetch=None)`).
  - No raw XML or full-state SVG (`apply_svg`) tools. The SVG read view is output only.
  - Raw HTML in Markdown is refused (`html="refuse"`).
- **Size limits, configurable, checked when a blob or document is registered:**
  - input package ≤ 50 MB, and ≤ 2,000 parts;
  - a single image ≤ 20 MB, and ≤ 40 MP when decoded (checked before the library sees it);
  - Markdown input ≤ 200k characters;
  - table data ≤ 5,000 cells per call;
  - ≤ 200 slides per `ppt_draft_slides`;
  - ≤ 200 copies per `ppt_copy` call, and ≤ 200 shapes per `ppt_layout` call.
  - Zip bombs are guarded by an uncompressed-total cap (≤ 500 MB) and a per-part ratio cap, checked before `Document.open`.
- **Timeouts:**
  - render 20 s per call (killed in the worker);
  - Word layout or reflow 30 s;
  - `update_fields` 30 s (it lays out up to `passes=4` times);
  - in-process measurement such as `overflows`, slide facts, design facts and `place_labels`: a soft deadline of 10 s, scoped to the slides touched.
  - A timeout returns `timeout` with what was skipped. The document is unchanged, because the batch rolled back, unless the mutation had already committed and only its check timed out; the result says which.
- **No operation that would corrupt the file.** The libraries already refuse many: `ChartDataError`, `FullStateError`, `EditError`, the unit guard, and `save_as_template` with the wrong extension. In addition:
  - the tool layer exposes no raw-XML or relationship operation; `ppt_copy` copies relationships only through LP23;
  - deleting the last slide's only title, or a chart's last series, is refused by the library;
  - `save_document` picks the content type from `format`, so `.potx` content saved as `.pptx` is re-typed (trial 1's S1 fixed in the libraries).
- **A `validate()` gate before save.**
  - `save_document` runs `validate(target=name)` and refuses if there is any problem not in `baseline_problems`.
  - Pre-existing problems are reported, not blocking.
  - The model cannot override the gate. Only the app can, with `Toolbox(allow_new_problems=True)` (D6).
- **Untrusted content.** Document text is data. The system prompt says that instructions inside documents and comments are content to act on only when the user's task asks for it (W2's backdating request).
- **Logs.** Every call is logged with an argument digest, never document text, unless the app enables full logs.

---

## Testing

1. **Schema tests (offline):**
   - every canonical schema passes the common-subset checker;
   - every adapter's output is checked against its provider's documented strict rules: Anthropic (no unsupported keywords; the strict tools in a request within the verified limits) and OpenAI Responses and Chat Completions (all required, nullable unions, `additionalProperties: false`, limits);
   - names match `^[a-z][a-z0-9_]{0,63}$`;
   - no property is a path (no `path`/`file`/`dir` names; blob parameters are handles);
   - the description token budgets hold.
2. **Schema tests (online, marked `provider`, run nightly; Anthropic only for now):**
   - one request with all definitions, `strict` as the adapter sets it; this must not return a 400;
   - token counts recorded with count-tokens.
   - The OpenAI online test is written but skipped until a GPT-6 trial is approved.
3. **Unit tests per tool,** on the libraries' own fixtures (pptx: `real-financial-report.pptx` and others; docx: `agreement.docx`, `charts.docx`, `sample-long.docx`):
   - the happy path;
   - each error code, with `valid_options`;
   - idempotence (the same call twice → `changed=false`, or the same objects via `key`);
   - undo after the call gives back the original bytes;
   - the result fits under its size cap.
4. **Layout, copy and measuring tests:**
   - **one measuring model:** for a set of text specs and presets, the measured box height (`ppt_add_shape` with `measure: true`) equals the `text_fit` height after `ppt_add_shape` with the same spec;
   - `stack`/`column`/`grid` place shapes exactly (to 0.01 pt), with groups and rotated shapes;
   - `place_labels` leaves no collisions on the p8 milestone, o1 and m1 label sets, or reports the unplaced ones;
   - scales: dates inclusive with excluded ranges, bands, reversed ranges, ticks; `place` on shapes and connector ends, inside `batch` and with `$ref`;
   - `ppt_copy` of a grouped exemplar to another slide and to another deck: every id is fresh, inner connectors are glued to the copies, pictures and charts carry their parts, and PowerPoint opens the result (oracle);
   - design facts on the trial-2 p7/p8 and spike outputs report the four-hue group, the unused band and the missing legend as facts, with no verdicts.
5. **Dispatcher tests:**
   - parallel calls on one document run in order, and on two documents concurrently;
   - a cross-deck `ppt_copy` cannot deadlock against a reverse copy;
   - a failed call leaves the version unchanged;
   - timeouts kill the worker;
   - unknown blob handles return `not_found`;
   - outputs reach `session.outputs`/`on_output`, and never the result envelope.
6. **Golden transcripts:**
   - Recorded tool-call sequences, with arguments and expected results normalised (versions, ids), for each trial task's reference solution expressed as tool calls, and for the spike's o1 and m1 references.
   - Replayed offline on every library release. The output file must be byte-identical (fixed session clock), `check` must be clean, and the task's checks must pass.
   - This makes "the tools can do every trial task without Python" a regression test.
7. **Trial 3 (Sonnet 5.5 only, D9):**
   - **Tasks:** the trial-2 tasks (p1–p8, and w1–w6 with w3 and w5 restored: 14 tasks) plus the spike's o1 and m1, so graphics are not judged on p7/p8 alone: 16 tasks. Each is run **twice**, using only the tools: no Python and no file access.
   - **Read-view comparison (G2):** the four graphics tasks (p7, p8, o1, m1) run twice more with `detail="svg"` available instead of `detail="geometry"`, everything else equal. The rule is pre-registered before the first run: the SVG read view becomes the default geometry view if its mean grade is at least the geometry arm's and its tokens are no higher; otherwise `geometry` stays the default and `svg` stays optional or is dropped.
   - **Harness:** a small `harness/run_trial.py` in the tool-layer repository.
     - It runs the Anthropic loop (all results in one user message), with a per-run session, a fixed clock and a token and tool-call meter, and writes `transcript.jsonl` and the output bytes.
     - Its provider loop is pluggable, so a later GPT-6 trial (Responses API, `previous_response_id`) reuses it unchanged.
     - Grading reuses trial 2's grading unchanged: the per-task checks, the Office oracles run serially, and blind Sonnet 5.5 visual graders that do not see the transcript.
   - **Prerequisites:** `ANTHROPIC_API_KEY` and spending approval. Rough estimate: 40 runs (32 + 8 read-view runs) × about 100–150k tokens.
   - **Compared with trial 2 and the spike, per task:**
     - success (strict definition);
     - tokens, split into input, cached and output;
     - tool calls;
     - wall time;
     - visual grade on graphics tasks;
     - tool errors by code;
     - strict-mode fallbacks used;
     - use of `ppt_layout`, `ppt_copy` and `ppt_design_facts`.
   - **Targets:** success ≥ trial 2; graphics grades ≥ 8 in every run; graphics-task tokens ≤ 90k (the spike's shape-call runs averaged 129k, about 12k of it reading library docs that tool definitions replace); and no task needing a capability the tools lack (each such case is logged as a gap).
   - **Later (not planned):** a GPT-6 Astra trial on the same harness, once the user approves an OpenAI key and its spending.

---

## Phases

### T0 — plumbing (L) — done in ooxml-edit 0.3.0

Status: `ooxml_edit.tools` and LE1 are implemented, with toy tools in the tests. Added after the fact, in 0.4.0: shared definitions (`ooxml_edit.tools.shared`), refs, the central checks hook and `batch`. Every exit item below passes offline; the online Anthropic test is written (`pytest -m provider`) and waits for an `ANTHROPIC_API_KEY`, and with it the live image round trip.

- **Scope:**
  - `ooxml_edit.tools`: registry, the subset checker, three adapters, the result envelope and error codes, Session with blobs and outputs (cross-document locking included), limits, the worker pool, logging;
  - **LE1** `History.version`;
  - **verifying the provider facts online:** Claude's strict limits (20/24/16), `tool_choice` behaviour and tool-prompt overhead on Sonnet 5 and Opus 5.5, the image token formulas, and OpenAI's model names, with this roadmap's [verify T0] items updated (now marked [verified T0]);
  - two toy tools per format to exercise it.
- **Exit:**
  - offline schema tests pass for all three adapters, and the online test passes for Anthropic;
  - images round-trip in `tool_result` (Anthropic, live), and the `function_call_output` and Chat Completions shapes are checked offline;
  - a killed render returns `timeout`;
  - the concurrency tests pass;
  - no tool accepts a path.

### T1 — PowerPoint core (L) — done

Status: done. Golden transcripts p1–p6 replay byte for byte to passing checks (17/17, 39/39, 13/13, 19/19, 13/13, 14/14) in 6–9 calls each, and PowerPoint opens every output. Core estimate 3,885 tokens; the online count is pending.

- **Scope:**
  - S1–S9, S10–S12 (charts and SmartArt) and S13 for pptx; `items[]`, refs and `batch` (D14); the duplicate fix (every nested shape renumbered); P1, P2 (`outline` and `geometry`), P3–P11, P15–P17, P20–P23; the per-edit checks; paging; the core and group definitions;
  - **LP18** one measuring model and the text spec (P3, P4, P7); **LP17** `content_area`; **LP19** box-overlap collisions; **LP20** small fixes; **LP14**.
- **Exit:**
  - golden transcripts for p1–p6 replay to passing checks, with Office opening every output;
  - the measure/build agreement test passes;
  - `check(boxes=true)` reports the spike's p8 A1 label-box overlap;
  - the core definitions are ≤ 4k tokens.

### T2 — Word core (L) — done

Status: done. 43 Word definitions, core about 2,940 tokens; golden transcripts w1–w6 pass (25/25, 40/40, 14/14, 17/17, 20/20, 24/24), and Word opens every output. Also landed: an empty-TOC warning, a `Document.converter` hook, and a batch that changes nothing records no undo step (ooxml-edit 0.4.0). Deferred: W28 (LE3/LW3), `apply_styles_from` (LW5), LW1/LW2 (T4); a shared tool to read text blobs was added in T3 (S15).

- **Scope:** S1–S9 for docx; W1–W27 and W29 (the `apply_styles_from` action waits for LW5); tracking mode; the validate gate.
- **Exit:**
  - golden transcripts for w1–w6 replay to passing checks, and Word opens every output;
  - a reflow check on `sample-long.docx` stays within its 30 s deadline.

### T3 — layout and complex diagrams (L–XL) — done

Status: done, apart from the items under "Deferred".
- **Built:** `ppt_align`, `ppt_distribute`, `ppt_layout` (stack, column, grid, place_labels, place), `ppt_scale` and `place` (D15), `ppt_copy`, `ppt_design_facts` and `check(include=["facts"|"design"])`, `ppt_read_slides(detail="svg")` on pptx2svg's agent view (LR3), `read_blob` (S15), and the experimental `ppt_draw` (opt-in).
- **Goldens, tools only:** p7 33/33 (6 calls: describe, measure, one batch with an exemplar repeated by `ppt_copy`, render, check, save), p8 37/37 (5 calls; date and band scales, `place`, `place_labels`; the marker line spans every row), o1 27/27 (5 calls; `stack` with equal widths, `column` and `align` per workstream), m1 22/22 (5 calls; two linear scales, labels within 70 pt of their bubbles' centres). They replay byte for byte, and PowerPoint opens every output. The spike's shape-call runs took 29–53 tool calls.
- **Effort, estimated (3.5 characters per token):** the calls' arguments cost about 1,250 (p7), 3,590 (p8), 2,490 (o1) and 2,300 (m1) output tokens; their results about 3,100–4,200 input tokens, plus one render (1,196). The definitions (core plus the graphics and layout groups) are sent with each turn.
- **Design facts on the spike's outputs** report, as facts, the rainbow, the dead bands, missing shading or focus, colour without a legend and mixed corners.

- **Scope:**
  - **LP1** with `ppt_align`/`ppt_distribute` (P12, P13);
  - **LP21**, **LP22** with `ppt_layout` (P24);
  - **LP23** with `ppt_copy` (P25);
  - **LP24** with `ppt_design_facts` (P26), and **LP15** slide facts in `check`;
  - the thinking-layer hooks (guidance and critique) with the example rule;
  - **LR3** and `ppt_read_slides(detail="svg")`; **LR4**, **LR5**;
  - golden transcripts for p7, p8, o1 and m1 using the T3 tools.
- **Exit:**
  - align and distribute match PowerPoint's results on rotated shapes and groups (oracle);
  - the layout, copy and design-facts tests pass (Testing 4);
  - `check` and `ppt_design_facts` report their fact kinds on the trial-2 p7/p8 outputs, with no verdicts;
  - p7, p8, o1 and m1 references rebuilt with the T3 tools pass 33/33, 37/37, 27/27 and 22/22, in fewer calls than the spike's shape-call runs;
  - the SVG read view meets LR3's exit, ready for T6.
- LR3, LR4 and LR5 are pptx2svg work and can start any time; they must land before T6.

- **Deferred from T3:**
  - align and distribute compared with PowerPoint's own commands on rotated shapes and groups (oracle);
  - the thinking-layer critique hook (`check(include=["app"])`) and the example guidance (T5);
  - LR4 and LR5 (the library render's bullet blob and custom-geometry outlines), before T6;
  - a "highlight on the focus element" fact; a legend drawn as coloured text alone; gradient fills in colour-coded sets;
  - `ppt_copy` texts as a text spec (`paragraphs`); group rotation for members copied out alone;
  - label anchors in data units (a label is anchored to a shape or a point).

### T4 — charts (L) — done in ooxml-edit 0.9.0

- **Scope, as the user narrowed it:** charts only -- LE3, LP7, LW3 and the part of LP8 a task uses. **LW1 and LW2 are dropped**: no task needed them (every Word run in trial 3 scored 10, and the models updated fields themselves). LW5, LW6 and LP11 were not part of this work and stay open.
- **Principle:** every tool, and every action, has a realistic task that uses it.
- **Built:**
  - `ooxml_edit.charts.create.add_chart`: the chart part and its embedded workbook from data (clustered and stacked column and bar, line, pie, scatter; categories, named series, a title, axis titles, the legend position, a number format), the caches and the workbook equal from the start, related to the holder part; the format adds only its frame around `NewChart.graphic()` (the insertion hook). `GraphicHost.look` gives new titles and data labels the application's sizes.
  - `slide.add_chart` (LP7) and `doc.insert_chart` (LW3, inline, in a new paragraph after a block or at a position).
  - `Chart.set_data_labels` (with a number format) and `Chart.set_gap_width` (LP8).
  - **Tools: an action of `edit_chart`, not new tools.** `add`, `show_data_labels`, `hide_data_labels` and `set_gap_width` join the shared definition; decks stay at 35 definitions and documents at 41. Chart work is one tool either way, and tool search on "chart" already finds it. The cost: `edit_chart` has 14 optional parameters and is no longer sent strict (more than half the request's 24); every definition grew by about 350 tokens (decks 12,812 -> 13,158, documents 11,056 -> 11,402, offline estimates), and the core is unchanged (decks 4,961, documents 3,527, counted-proxy).
- **Office's new-chart look, measured** on Office for Mac 16 (Insert > Chart, each type's first subtype, in PowerPoint and Word, saved and read back; facts recorded, nothing of Office's copied):
  - chart style 201 / `c14:style` 102, `roundedCorners` 0, legend at the bottom (none for a one-series scatter), `autoTitleDeleted` 0;
  - series in the theme's accents in order, then Office's colour cycle (the six accents again at lumMod 60%, then 80% + lumOff 20%, ...); pie slices the same, each with a 1.5 pt `lt1` border;
  - columns: gap width 219, overlap -27; bars: gap width 182; lines: 2.25 pt, round caps, no markers; scatter: circle markers, size 5, no line;
  - gridlines and the category axis line 0.75 pt at `tx1` 15%; scatter axis lines at 25%; labels in the minor face at `tx1` 65% (data labels 75%), no tick marks;
  - sizes: PowerPoint title 18.62 pt, axis titles 13.3, labels, legend and data labels 11.97; Word 14, 10 and 9;
  - chart area: PowerPoint no fill and no line; Word `bg1` with a 0.75 pt border at `tx1` 15%; Word's inline default 432 x 252 pt;
  - workbook: categories in column A from row 2, series names in row 1.
  - Not measured: the stacked subtypes (the ribbon gallery is not scriptable); stacking writes overlap 100 and the schema's gap width 150.
- **Goldens, tools only:** p9 (decks: a chart slide of quarterly revenue by region from a CSV, theme colours, data labels in EUR m, a one-line takeaway) 19/19 in 9 calls; w9 (documents: a line chart of monthly volumes from a CSV after a paragraph, axis titles, a caption) 18/18 in 8 calls. Both replay byte for byte.
- **Office:** PowerPoint and Word open both outputs unprompted and draw the charts; re-saved by each, the caches and `workbook_values` still agree. The PDFs' data labels are the workbook's values (w9 checked on a copy with labels shown). At PowerPoint's default gap width p9's labels are wider than their columns, which is why p9 sets it.
- **Renderer findings** (not fixed here): pptx2svg draws data labels without their own number format (`12.4`, not `€12.4m`), so an agent's render does not show the crowding PowerPoint shows; docx2svg starts a value axis at 0 where Word starts it near the data (110-140); both draw tick labels near black, not at `tx1` 65%.

### T5 — guidance and loading (M) — done in ooxml-edit 0.6.0

Status: done; the rationalisation the user then decided is T5b, below.
- **Descriptions:** decks 16,394 → 13,882 offline (23,974 → 20,622 online); Word 12,592 → 11,444 (17,581 → 16,123).
- **Loading:** deferred by default when every tool is sent.
- **Strict:** writing tools only, under a new free-text-string cap.
- **Prompts:** `prompts.SYSTEM` and the two format fragments rewritten.
- **Online checks** (Sonnet 5.5 and Opus 5.5):
  - count-tokens for core and all;
  - a tiny request per format with the definitions as sent: strict, `defer_loading`, the tool-search tool and the system prompt, no 400 on either model;
  - a tool-search session per format that found and called `ppt_align` and `word_set_tracking`.
  - The whole of T5 spent about USD 0.35.
- Found on the way: Claude's undocumented "Schema is too complex" limit (see "Provider facts").

- **Scope:** the system-prompt fragments (mechanics only), the description pass, tool groups, deferred loading, token budgets in CI, `allowed_tools`, and the thinking-layer docs with one example app guidance.
- **Exit:**
  - a scripted Anthropic session discovers a deferred tool through tool search and calls it, and the OpenAI namespace and `tool_search` shapes pass offline;
  - the budgets hold.

### T5b — the rationalisation (S–M) — done in ooxml-edit 0.7.0

- **Scope:** the changes the user approved from T5's assessment (see "Model guidance", "The rationalisation"), the goldens rewritten for them, the prompt fragments, the strict orders and the budget policy.
- **Tools:** decks 40 → 35, Word 44 → 41. Every definition: decks 20,622 → 18,848 counted (13,882 → 12,747 offline), Word 16,123 → 15,544 (11,444 → 11,046). The loaded core: decks 5,051 → 5,097, Word 4,250 → 3,539.
- **Goldens:** p1–p8, o1, m1, w1–w6 and batch-30 call the new tools and replay to passing checks with **byte-identical outputs**: each rewritten call is the same library edit. Two changes in the calls themselves: p4's two measuring calls are one `ppt_add_shape` measure call with two items (7 calls to 6), and the Word goldens' recorded results (summaries, data digests) are unchanged.
- **Online checks** (Sonnet 5.5): count-tokens for core and all per format; one request per format with the definitions as sent (strict plan, `defer_loading`, tool search, system prompt): 200; one tool-search session per format that found a deferred tool and called it with valid arguments: `ppt_align` with `distribute` for decks, `word_format` (now deferred) for Word, and one deck session that found `ppt_add_shape` by "measure" and called it with `measure: true`.
- **Exit:** the suites pass; the provider-marked checks pass; the budgets hold.
- **Trial 3 adds small Word tasks** so that tools no golden calls are exercised: a landscape section for a wide table (`word_sections`), a footer with "page X of Y" (`word_headers_footers`), and a footnote citing a source (`word_notes`).


### T6 — trial 3 (M; Sonnet 5.5 only) — done

- **Scope:** the harness, 40 runs (including the read-view comparison), grading, and a report in the trial-2 format, written by the main session.
- **Small Word tasks for unproven tools** (from T5b): a landscape section for a wide table, a footer with "page X of Y", and a footnote citing a source; and w3 (a table and chart from a CSV, through `read_blob`) restored.
- **Exit:**
  - the report is written, and G2 is decided by its pre-registered rule;
  - every gap it finds is filed against T1–T4 or the libraries.

Status: done. Claude Sonnet 5.5 ran every task through the tools only (the Messages API, the definitions as the adapter sends them: core loaded, the rest deferred behind tool search, prompt caching on).
- **Success** (every required check, Office opens the file unprompted, a blind visual grade of at least 8/10): **36 of 38** main runs. Both misses were m1, on label placement. On the 12 tasks trial 2 also ran: 24/24, against trial 2's 20/24.
- **Effort:** 13.7 tool calls, USD 0.156 and 1.0 minute per main run on average (the Python black-box route of the spike: 39 calls, 15.6 minutes on the graphics tasks; here 11.4 calls, 2.0 minutes). All 46 runs cost USD 8.54.
- **Office:** 28/28 decks and 18/18 documents opened with no prompt.
- **G2 decided:** the SVG read view scored 8.50 against geometry's 9.13 and cost more (306k against 275k processed tokens), so **`geometry` stays the default**.
- **`ppt_draw`** scored 9.50 against 9.13 (within grader noise) with 5% fewer calls but 21% more tokens: **it stays experimental**.
- **Findings,** fixed in T6b:
  1. (S1) strict decoding dropped `word_insert_markdown`'s `markdown`, listed before the required `at`; three runs looped 17–40 times;
  2. `ppt_layout`, `ppt_scale`, `ppt_copy` and `ppt_align` went unused in 16 graphics runs although search loaded them 23 times;
  3. a run saved with collisions its own checks had reported;
  4. `render` on a document lost the first call to `slides` in 10 of 18 Word runs;
  5. `open_document` was tried on text inputs six times;
  6. `word_headers_footers` wrote "Confidential1 of 5" for `page_x_of_y`, and refused `\n` with code `unit`;
  7. `batch` ops sent as a JSON string;
  8. "text or paragraphs, not both" refusals inside `batch`;
  9. docx-agent's layout stopped at w7's auto-fit table.

### T6b — trial 3's fixes (S) — done in ooxml-edit 0.8.0

- **Required properties first (finding 1).** `build_schema` lists an object's required properties before its optional ones, every adapter sends schemas that way, and `anthropic_problems` flags a strict tool that does not. Every strict tool was audited in both formats: of the tools sent strict, only `word_insert_markdown` had a required property (`at`) after an optional one. Several non-strict tools had one too (`ppt_read_slides`, `ppt_add_connector`, `word_copy_from`, `word_insert_table` and others). Now no tool has one, at any depth. The OpenAI adapters send every property required anyway, so nothing could be dropped there; they get the same order.
  - **The probe again,** three requests each: strict as now sent (`doc, at, markdown, blob, …`) kept `markdown` 3/3; strict in trial 3's order still dropped it 3/3.
  - **A provider-marked online check** in each library sends every strict tool one scripted request (`tool_choice` auto) asking for a call with every argument, the optional ones listed first, and asserts each arrives. All 11 strict tools passed (decks: `ppt_add_slide`, `ppt_draft_slides`, `ppt_manage_slides`, `edit_chart`, `ppt_align`; Word: `replace_text`, `edit_chart`, `word_insert_markdown`, `word_set_tracking`, `word_changes`, `word_template`). The free-text-string cap of 32 still holds.
  - **`word_insert_markdown` keeps `markdown` and `blob` as optional alternatives.** With the order fixed, nothing is lost, and the blob form is what lets a long draft go in without the model writing it out (w1 used it in both runs). Requiring `markdown` would force an empty string beside every `blob` call, the same either/or rule in a worse form.
- **The layout tools (finding 2).** The deck prompt now names the cases: labels beside points or bubbles go to `ppt_layout` `place_labels` with `max_center`; positions from dates or scores to a `ppt_scale` and `place`; a repeated element is built once and copied with `ppt_copy`. The four tools' descriptions say the same.
  - **The core:** `ppt_layout` in the deck core would count **6,935** tokens against the 5,500 budget (5,097 without it). It stays deferred (O3).
  - **Measured, and not solved:** the sharper prompt changed nothing in the re-runs (0 layout calls in m1 ×2 and p8, with `ppt_layout` loaded every time). A small A/B with `ppt_layout` in the core gave 0 calls in m1 ×2 too, and both missed the label check by 0.01 in. So neither more prompting nor loading gets the tool used; what would is open (see "Open decisions", O4).
- **Facts at save (finding 3).** A deck's `save_document` result lists the overflows, collisions and off-slide shapes still in the deck (`unresolved`, at most 20 of each) and says so in its summary. These are facts, not a refusal. The deck prompt names collisions beside overflows before saving.
- **`render` and `check` on a document (finding 4)** read `slides` as `pages`, with a warning; given both, `pages` is used. Accepting it was the less surprising of the two fixes.
- **Text inputs (finding 5).** `open_document` on a text blob answers "read it with read_blob" (`valid_options: ["read_blob"]`), and the shared prompt says text inputs are read with `read_blob`.
- **Footers (finding 6).** `page_x_of_y` writes "Page X of Y", after " | " when the text does not end in a space. A `\n` in `text` makes paragraphs. That refusal, and `word_read`'s range-with-every-story one, now use `invalid_arguments`, not `unit`.
- **JSON strings (finding 7).** An array or object argument written as a JSON string is parsed; a `\v` inside it is read as a line break (the re-run's p8 lost two turns to that).
- **`ppt_add_shape` (finding 8)** says "give text or paragraphs, not both".
- **Auto-fit tables (finding 9), in docx2svg.** Word keeps an auto-fit table narrower than its content to the room, sharing it among the columns in proportion to their widest words. Measured on a probe of 11 tables in three compatibility modes and modelled; w7's input now lays out to 2 complete pages, as in Word. The trial's own w7 outputs had already moved the table into a landscape section, where it fits.
- **Re-runs** (the trial-3 harness, unchanged apart from the fixes; success as in T6):

| Task | Success | Checks | Visual | Calls | Errors | Cost (USD) | Trial 3 |
|---|---|---|---|---|---|---|---|
| m1 ×2 | 1/2 | 22/22, 21/22 | 9, 9 | 13, 16 | 1, 0 | 0.30, 0.33 | 0/2; 22/22, 21/22; 7, 10; 10, 16 calls; 1, 2 errors; 0.34, 0.27 |
| w1 ×2 | 2/2 | 25/25 both | 9, 9 | 17, 14 | 1, 1 | 0.15, 0.13 | 2/2; 10, 10; 13, 34 calls; 2, 19 errors; 0.10, 0.24 |
| w5 ×2 | 2/2 | 20/20 both | 10, 10 | 10, 10 | 0, 0 | 0.08, 0.07 | 2/2; 10, 10; 39, 50 calls; 28, 40 errors; 0.21, 0.29 |
| w8 ×1 | 1/1 | 13/13 | 10 | 6 | 0 | 0.04 | 2/2; 10, 10; 13, 11 calls; 3, 2 errors; 0.08, 0.08 |
| p8 ×1 | 1/1 | 37/37 | 9 | 18 | 2 | 0.60 | 2/2; 9, 8; 11, 13 calls; 1, 1 errors; 0.41, 0.34 |
| m1 A/B, `ppt_layout` in the core, ×2 | 0/2 | 21/22 both | 10, 9 | 13, 13 | 3, 1 | 0.34, 0.32 | — |

  - **w5 and w1:** the `word_insert_markdown` loops are gone. w5 went from 34 and 48 turns to 7 and 9, at about a third of the cost.
  - **w8:** the footer came out "Confidential | Page N of 5" on pages 2–5 at the first call, in 6 calls, with no repair.
  - **p8:** it lost two turns to `items` sent as a JSON string with `\v` line breaks, fixed since.
  - **Two calls arrived without their large argument** because the response hit the harness's 8,000-token output limit mid-call. The application should raise `max_tokens` or retry such a call; the tool layer cannot tell a cut-off call from a short one.
  - **Office:** every output opened unprompted, 5/5 documents and 5/5 decks. The fix round spent USD 2.49 of API calls in all: runs, probe and online checks.

```
T0 ─┬─▶ T1 ─┬─▶ T3 (layout, copy, design facts) ──┐
    │       │                                     │
    └─▶ T2 ─┴─▶ T4 ───────────────────────────────┴─▶ T5 ─▶ T6
LR3, LR4, LR5 (pptx2svg; any time) ───────────────────────▶ T6
```

- T0, T1, T2, T3, T4 (charts), T5, T5b (the rationalisation), T6, T6b and the post-T4 round are done.
- T3 needs T1 (the text spec and `content_area` come first).
- T5 can start once T1 lands, but its budgets are only final after T3 and T4.
- Trial 3 runs last, but a smoke run of the graphics tasks after T3 is cheap and worth doing.
- Arm C did not re-open SVG authoring (G1): `ppt_draw` and LP16 are an experimental, opt-in addition for trial 3's comparison; nothing above waits for it.

---

### Post-T4 — comments, removals, per-edit layout facts, unused tools, guidance (M) — done in ooxml-edit 0.10.0

**Why:** trial 3, its fix round and the Haiku 5.5 trial (about 40 graphics and edit runs with the tool layer, 106 transcripts counted) showed which tools models use, and that the T3 layout tools were never chosen. The product goal is a clean tool set: every tool has a realistic task that uses it, or it goes; judgement stays in the application's thinking layer.

**1. PowerPoint comments (`ppt_comments`, LP25).** pptx-agent reads and writes PowerPoint's modern (threaded) comments (`edit/comments.py`): a thread on a slide or a shape, replies, resolved or open. `ppt_comments` takes `word_comments`' actions (list, add, reply, resolve, reopen, edit, delete), many at once in `items`; `list` only reads, and `describe` counts the threads.
- **Measured on PowerPoint for Mac** by having it re-save hand-written and library-written comments (no AppleScript adds a comment, and the UI was not available): it opened every probe unprompted and kept every thread; it renamed the part `ppt/comments/modernComment_<sldId hex>_0.xml`, wrote `created` without zero fractional seconds and kept a `Z`, sorted threads by `created`, put the slide's `commentRel` extension before its `p14:creationId`, wrote the authors back in reverse order, and did not check authors, shapes or creation ids. Parts: `ppt/authors.xml` (`application/vnd.ms-powerpoint.authors+xml`, related from the presentation), one comments part per slide (`application/vnd.ms-powerpoint.comments+xml`, related from the slide and named in its `p:extLst`).
- **Library choices (not measured):** `created` in UTC with `Z`, no `p188:pos`; ids from `uuid5` of the content and the clock, so the same calls give the same bytes; a duplicated slide does not take its original's comments, a deleted one takes them along. The older, non-threaded comments are not read or written.
- **Golden p10** (a reviewer comments on a figure and a slide, and answers and resolves an analyst's thread): 7 calls, 17/17. PowerPoint exported and re-saved it unprompted with all four threads.

**2. Removed from the tool set:** `ppt_layout`, `ppt_scale`, `ppt_copy`, `ppt_align`, the `place` parameter and the experimental `ppt_draw` (with `EXPERIMENTAL_TOOLS`). The library calls stay. Evidence: no model called them in about forty trial runs (search loaded them 23 times in trial 3 alone); a layout-first run (`ppt_layout` in the core) gained no grade at about +3,300 tokens a request; SVG authoring never beat shape calls (spike, arm C, trial 3's draw arm). The goldens p7, p8, o1 and m1 were rewritten as a model computing its own coordinates sends them (each shape's final box, connector end points, labels as text boxes, copies as more items) and still pass 33/33, 37/37, 27/27 and 22/22; PowerPoint exported all four unprompted. `ppt_align` left `STRICT_FIRST`. The shared prompt dropped "let the layout and placement tools position things".

**3. Per-edit layout facts (`checks.layout`).** Changing deck tools name the shapes they touched; when there is something to say, `checks` gains `layout`: at most five facts about those shapes, each with the exact call that resolves it (`{"kind": "near_alignment", "fact": "256.6: top edge 2.0 pt below the line 256.3, 256.4, 256.5 are on", "fix": "ppt_set_shape 256.6 y=150"}`). Measured against the slide's own shapes, no rules of taste (`pptx_agent.edit.feedback`).

| Kind | Fires when (the tuned tolerances) |
|---|---|
| `near_alignment` | a box (a fill or an outline) 1-3 pt off a line that two or more of its column's or row's neighbours are exactly on, one of them a box like it (same preset, width and height within 12%), the nearest within 36 pt, while it is exactly on none of their lines |
| `uneven_gap` | in a row or column of like boxes, one gap that differs by more than 1 pt from the gap the others agree on (four or more boxes, a majority agreeing), or the two gaps of three like boxes with text; never when it differs by more than 12 pt and half the gap (a choice), and only when moving the touched box alone fixes it |
| `text_size` | a paragraph of a touched box whose size differs by 0.5 pt or more from the size every like box (same geometry, size, fill and paragraph count; at least two) has there; not when the touched box states its size and the others inherit theirs (a size just chosen, mid-build); text boxes without fill or outline are not compared |
| `label_distance` | a one-line label more than twice as far from its marker (a small box without text, at most 60 pt; labels and markers matched one to one, nearest first, the marker being the label's own nearest) as the median of the other labels of its kind (one text size and height), and 8 pt more; not when more than a fifth of the kind is that far, or a line's end is nearer than the marker |

- **Evaluation** (`agent-work/post-t4/eval_feedback.py`, `seeded.py`, `growth.py`): every deck output of trial 3, the fix round, the Haiku trial, layout-one, layout-ab, the spike and arm C, the shapes each run added or changed counted as touched (85 runs; 65 graphics slides).
  - **Quiet:** 3 facts in all, 82 of 85 runs silent; none on the 20 deck-edit runs (p2-p6), none on the 20 fixture slides with every shape touched, none on the goldens.
  - **Against the graders:** the graders' written defects of these four kinds were three -- "PMO 0.08 in off" (armC o1 C2: the box is exactly middle-aligned with its neighbour; the grader wanted its top aligned), "vertical gap grows row to row" (armC p7 C1: gaps between rows of different shapes) and "Board update label at the bottom" (armC p8 C1b: a line's label). The facts catch **0 of 3**; each is outside the definitions above, and widening them to catch these raised the false alarms on clean slides. None of the three facts reported is a grader-noted defect (**strict precision 0/3**); inspected in Office's renders, two are visible inconsistencies the graders did not mention (a bubble label 13 pt from its bubble where the others are 3 pt; a label floating 26.5 pt above its bubble where the others are 4 pt) and one is a 1-pt header difference between the PMO box and the workstream boxes.
  - **Against m1's label-distance check** (centre within 1.0 in): 0 of the 8 failing runs. Those labels sit 3-4 pt from their bubbles like the others; the check measures centre to centre, so a wide label beside its bubble fails it. That is the task's rule, not a fact about consistency: it belongs in an application's house rules.
  - **Recall on seeded defects** (one perturbation per slide on the 65 graphics outputs): near-alignment 54/55, uneven gap 37/55 (the misses are three-box rows without text, legend swatches mostly, left out on purpose: data points look the same), text size 41/42, label distance 22/29 (seeds kept only when the moved label stays nearest its own marker).
  - **Token growth**, replaying the trials' own deck calls (135 changing results): 2 results gained facts, +92 tokens in all, **+0.68 tokens per changing result on average**, at most +53 in one result; a fact costs about 40-50 tokens, five at most.
- The deck prompt says: "checks.layout: shapes you touched that almost line up, space or size like their neighbours but not quite, each with the exact fix; apply it unless the difference is intended."

**4. Tools no model called** (106 transcripts: trial 3, its fix round, the Haiku trial, layout-ab, layout-one; `word_delete` and `word_format` had one call each, so they are used):

| Tool | Decision | Why |
|---|---|---|
| `list_documents` | **removed** (ooxml-edit) | the application names every document and input in its message, and `open_document`/`new_document` return ids; its one golden call (w1) only listed what the task said |
| `close_document` | keep; golden **w10** | a board pack from five department reports: the session holds four documents, so each report is closed once its section is copied; the limit's error now names `close_document` |
| `set_properties` | keep; golden **w10** | the board pack's title and author, which Word shows under Info and a PDF takes as its title |
| `edit_smartart` | keep; golden **p11** | the steps of a SmartArt process and a list in a deck PowerPoint made, updated in place (11/11); SmartArt model ids are now made from the edit, so the bytes repeat |
| `ppt_set_theme` | keep; golden **p12** | a new brand palette and fonts applied to the theme so every slide follows (9/9) |
| `word_controls` | keep; golden **w11** | a services agreement template's content controls filled for a new client (11/11) |
| `word_drawings` | keep; golden **w12** | an organisation chart inserted in a paragraph of its own, 12 cm wide, centred, with alternative text (10/10); `at: "after:<block>"` is new for that |
| `word_inspect` | keep; golden **w13** | a pasted paragraph that looks different: inspect shows its direct font, size and colour, `word_format` `clear_direct` removes them (8/8) |
| `word_template` | keep; golden **w13** | the same legacy policy opens in Compatibility Mode; `upgrade_to_modern` brings it to mode 15 |
| `word_insert_text` | keep | its task is already a golden (w2: ", excluding VAT" as a tracked insertion); `replace_text` records the whole match deleted and re-inserted, `word_insert_text` only the new words |
| `word_insert_table` | **removed** | a new table is a Markdown table through `word_insert_markdown` (every w3 run did that), then `word_edit_table`/`word_format_table` |
| `ppt_copy`, `ppt_align` | removed (item 2) | |

Every new golden replays to the same bytes and passes its check; PowerPoint exported p10-p12 unprompted with the edited text in its PDF, and Word opened w10-w13.

**5-6. Guidance and support lists.** `GUIDANCE.md` beside each format's tools, for the developer of the application's thinking layer: the facts and how to use them, a review pass the application runs itself, gating a save on unresolved facts, model routing (Haiku 5.5 for Word and for decks without a designed graphic, at about 5% of Sonnet 5.5's cost; Sonnet 5.5 for designed graphics: 6/8 against 4/8, mean grade 9.13 against 7.75) and house rules in the application's prompt with the documented example. `SUPPORTED.md` beside each: what is supported, by tool and action, and what is not (decks: animations and transitions, SmartArt creation, icons, video and audio, sections, hyperlinks, action buttons, master and layout editing; Word: an overview).

**Budgets after post-T4** (offline: compact JSON ÷ 3.5; counted ≈ ×1.48; nothing was counted online, since this round made no API calls):

| | Tools | All definitions, offline | Core, offline | Core, counted proxy |
|---|---|---|---|---|
| Decks before | 35 | 13,158 | 3,352 | 4,961 (5,097 counted in T5b) |
| Decks after | 31 | 10,221 | 3,352 | 4,961 |
| Word before | 41 | 11,402 | 2,384 | 3,528 (3,539 counted in T5b) |
| Word after | 39 | 11,080 | 2,384 | 3,528 |

- The cores did not change, so the 5,500 budget holds as before. The guards on every definition fell to 10,500 (decks) and 11,400 (Word). The deck system prompt (shared plus deck fragment) fell from about 1,101 to 1,017 offline tokens.
- **Strict sets moved** with the removals: decks `replace_text`, `set_properties`, `ppt_arrange`, `ppt_add_slide`, `ppt_draft_slides`, `ppt_manage_slides`; Word `replace_text`, `word_set_text`, `edit_smartart`, `word_insert_markdown`, `word_set_tracking`, `word_changes`, `word_template`. The provider-marked online test ("every field arrives") was not re-run (no API calls this round); run it before the next trial.


## Open decisions

| # | Question | Options | Recommendation |
|---|---|---|---|
| G1 | SVG authoring (`ppt_draw`) as an optional route | parked · add beside shape calls | **Decided by arm C:** not as a route (8.00 vs 8.375, −2.6% tokens, 4 structural failures vs 1). `ppt_draw` stays experimental, outside the defaults, with the same scales, for trial 3's comparison. |
| G2 | The model's geometry view | `detail="geometry"` (JSON dump) · `detail="svg"` (LR3 read view) | **Decide in trial 3** by its pre-registered rule; `geometry` is the default until then. |
| O3 | Graphics tools in the core set | core stays 12 · add `ppt_add_shape`/`ppt_layout`/`ppt_copy` to the pptx core | **Keep the core as it is.** T6b measured it: `ppt_layout` in the core counts 6,935 tokens against the 5,500 budget, and an m1 A/B with it loaded gave no more calls to it than deferred (0 in 2 runs each). |
| O4 | Getting `place_labels` used | an `input_examples` entry · a label option on `ppt_add_shape` items (place this text box beside that shape) · leave it | **Closed post-T4:** the layout tools left the tool set; a label far from its marker compared with the others of its kind is now a per-edit layout fact with its fix. |

---

## Non-goals and later

- MCP servers.
- A code-execution tool, and programmatic tool calling.
- Raw XML, relationship or package tools; full-state SVG editing; macros and VBA.
- File paths, file stores or any file system access in the tool layer.
- Diagram-specific builders (timeline, Gantt, process, legend), and a slide-wide column grid derived from placeholder margins. (`ppt_layout`'s `grid` places given shapes in a box the model names; it is not a page grid.)
- House style or design rules in the libraries or tools; design facts carry no thresholds of taste.
- Calling OpenAI before a GPT-6 trial is approved.
- Rendering tracked changes and comments visually in Word (docx-agent decision 8).
- Editing a file that is open in Office.
- Pixel-exact claims: fidelity is the renderers' roadmap.
- **Later:** SVG authoring as a route: tested in the spike (shape calls 8.50 vs SVG 8.13, +5% tokens), arm C (hand-written SVG 8.00 vs 8.375, 4 structural failures vs 1) and trial 3's draw arm (no clear advantage); `ppt_draw` was removed post-T4, and the converter stays in the library.
- **Later:** importing whole slides between decks; layout and master editing (LP9, D8).
```