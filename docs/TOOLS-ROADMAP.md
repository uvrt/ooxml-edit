# Roadmap: agent tool layer (T-phases)

This is the working plan for the **tool layer** over pptx-agent and docx-agent. The tool layer is a Python module that the user's own application imports. It provides tool definitions for Claude (with an adapter for GPT-6), plus a dispatcher that runs the calls. The model never runs Python and never writes raw XML; everything it can do is a tool. The plan is written so someone can pick it up cold, in the style of the libraries' own roadmaps: every phase says what it covers, what it needs from the libraries, and how to know it is done.

**Effort key:** S ≈ half a day · M ≈ 1–3 days · L ≈ 1–2 weeks · XL ≈ 3+ weeks.

**Next step: T1 and T2** (PowerPoint and Word core). T0, the plumbing, is in ooxml-edit 0.3.0.

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
| D9 | Trial 3 models | **Sonnet 5 (`claude-sonnet-5`) only**, 2 runs per task. A GPT-6 trial on the same harness is possible later. |
| D11 | Main route for graphics (G1, first round) | **Shape calls**, decided by the spike. SVG authoring is parked, and G1 stays open for the arm-C test only. |
| D12 | Where the compact SVG read view lives (was O2) | **A pptx2svg mode (LR3).** pptx2svg already resolves geometry and theme colours, and may change to serve this. |
| D13 | Layout help | **General tools** (`ppt_layout`, `ppt_copy`, align/distribute, `ppt_design_facts`), each backed by a library call. |

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
- **Praised:** `content_area(slide)` (on the spike branch only).

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

**Current Claude models [verified T0]:** Fable 5.1 (`claude-fable-5-1`), Opus 5.5 (`claude-opus-5-5`), Sonnet 5.5 (`claude-sonnet-5-5`) and Haiku 4.5 (`claude-haiku-4-5`, retiring no sooner than 2026-10-15) (https://platform.claude.com/docs/en/models/overview). Sonnet 5 (`claude-sonnet-5`) is now a **legacy** model: still available, retiring no sooner than 2027-06-30 (https://platform.claude.com/docs/en/models/sonnet-5/overview). Trial 3 uses Sonnet 5 (D9).

### Anthropic Messages API

- **Tool shape [verified T0]:** `{name, description, input_schema, strict?, defer_loading?, cache_control?, input_examples?, allowed_callers?}`. The name must match `^[a-zA-Z0-9_-]{1,128}$` (https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools).
- **Strict tool use is GA**, with `strict: true` per tool (https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use, https://platform.claude.com/docs/en/build-with-claude/structured-outputs).
  - **Supported:** basic types, `enum`, `const`, `anyOf`, `allOf` (but not with `$ref`), internal `$ref`/`$defs`, `required`, `default`, and `additionalProperties: false`. `format` supports date-time, date, time, duration, email, hostname, uri, ipv4, ipv6 and uuid. Simple `pattern`. `minItems` 0/1.
  - **Unsupported:** recursion, `minimum`/`maximum`/`multipleOf`, `minLength`/`maxLength`, `maxItems`, `minItems > 1`, `oneOf`.
  - **Per-request limits [verified T0]:** 20 strict tools, 24 optional parameters, 16 union-typed parameters (`anyOf` or type arrays). "These limits apply to the combined total across all strict schemas in a single request"; non-strict tools don't count (https://platform.claude.com/docs/en/build-with-claude/structured-outputs#explicit-limits).
  - Beyond those, an undocumented grammar-size limit returns a 400 ("Schema is too complex for compilation"), and schema compilation times out after 180 s.
  - `additionalProperties: false` is required on every object; `oneOf` is in neither the supported nor the unsupported list, so it is treated as unsupported. Numeric bounds are still unsupported.
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
| `description` on every property | recursion and `$ref` (inline everything; nesting at most 4 levels) |
| `format: "date"` and `"date-time"` only | `format: "uri"` (OpenAI lacks it), `pattern` (kept out for simplicity) |
| names matching `^[a-z][a-z0-9_]{0,63}$` | `default` (both providers: say it in the description) |
| | free-form objects (`additionalProperties: true`): every key is named |

The canonical form marks each property *required* or *optional*; it does not use type unions. Where a tool takes one of two shapes (plain `text` or a `paragraphs` spec), they are two optional properties, and the dispatcher requires exactly one. Each adapter then handles its own provider's rules.

**Anthropic adapter (used now)**
- Optional properties are left out of `required`.
- `strict: true` is set on a tool **only while the request stays under the strict limits [verified T0: 20 tools, 24 optional parameters, 16 unions, summed over the strict tools]** (D4):
  - Writing tools are strict first, in a fixed priority order.
  - The rest are sent `strict: false`, and the dispatcher validates them; it validates every call anyway. Option-heavy tools (`ppt_layout`, `ppt_copy`, `ppt_set_shape`, `word_format`) will usually fall in this group.
  - Union-typed parameters are never generated, so the union limit cannot bite.
- `defer_loading` on non-core tools, plus `tool_search_tool_bm25`.
- `cache_control` on the last core tool.
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

1. **One describe tool per document,** listing everything editable with ids.
   - `ppt_describe`: slides with their titles, layouts and their placeholders, each slide's content area (pt), theme colours, fonts, roles and tints, slide size, sections, and the baseline problems.
   - `word_describe`: the heading tree with ids, sections, stories, styles in use, comments, revision and field counts, tables, drawings, charts, page count, and the baseline problems.
   - Reading the content itself is a second tool with paging (`ppt_read_slides`, `word_read`).
2. **One address grammar,** written once in the system prompt and accepted by every tool.
   - **PowerPoint:** `s:256` (slide), `256.5` (shape; `256.5#2` when the deck numbered a shape twice), `256.5/p1/r0`, `256.5/cell2,1`, `256/notes`, `layout:Title and Content`. Another open deck's shapes are qualified by its doc id: `d2:256.5`.
   - **Word:** `p:3B212964`, `t:…` (table), `c:…`, `rev:…`, `fn:…`, `d:…`, `s:body` (section), `p:X@19:29` (characters), `p:A..t:B` (a span), and story names (`header1`).
   - **Inputs:** blob handles `b1`, `b2`…
   - Every address a read tool prints, including the `data-id`s in the SVG read view, is accepted verbatim by every write tool. Nothing is converted in the tool layer.
   - Text targets go through `find_text`, or a `find` argument that must match exactly once (`AmbiguousAnchor` → an error listing every candidate's address).
3. **Coarse first, plus one general setter per format.**
   - Structural tools do a whole job: `word_move`, `ppt_draft_slides`, `ppt_layout` (arrange many shapes in one call) and `ppt_copy` (build one exemplar, then repeat it with new text).
   - `ppt_add_shape` and `ppt_set_text` take a **declarative text spec**: paragraphs, runs and bullets in one call.
   - `ppt_set_shape` and `word_format` take **every** settable property as a named, optional field. That fixes trial 2's undiscoverable `format(**Any)` keys (p8).
4. **One length unit: points** (D3).
   - Every length in every tool is points, as a number (2 decimals), for both formats. pptx EMU is converted at the boundary (12,700 per pt). The SVG read view's user units are points too.
   - Font sizes are points, colours are theme strings (`accent1 lumMod=75%`) or `#RRGGBB`, and dates are `YYYY-MM-DD`.
   - This removes trial 2's N5 class of error (EMU versus points) by construction.
5. **One measuring model.** `ppt_measure_text` takes the same text spec as `ppt_add_shape`/`ppt_set_text`, and the target shape's (or the new shape's preset's) default insets, font and wrap. What is measured is what is built; the spike's two drift bugs (missing default insets, an unnoticed `bold`) cannot recur.
6. **Facts in every result.** Each mutating call returns `checks`, which hold facts only:
   - **pptx:** `overflows()` for the slides touched (text, overlap/collisions including box overlap, off_slide), and `near_wrap` lines. `check` adds the full slide facts (LP15); `ppt_design_facts` adds the design facts (LP24).
   - **docx:** `EditResult.warnings`/`unknown`, and stale fields (gap **LW2**).
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

---

## Complex diagrams: layout, copy and design facts

Shape calls are the main route. What the spike showed is missing is not a different authoring format but **layout help** and **facts about the design**: both arms spent most of their effort computing coordinates, and every grade gap came from dead space, palettes and missing legends. This section covers the general tools that close that gap. None of them knows a diagram type.

### The text spec (used by building and measuring)

One shape, in `ppt_add_shape`, `ppt_set_text` and `ppt_measure_text` alike:

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

### Layout (`ppt_layout`)

| Action | What it does | Parameters |
|---|---|---|
| `stack` | place shapes in a row, left to right, in the order given | `targets`, `box?` (default: the slide's content area), `gap` (pt), `align` (top/middle/bottom), `justify?` (start/center/end/spread) |
| `column` | the same, top to bottom | as `stack`, with `align` left/center/right |
| `grid` | place shapes into an n×m arrangement within a box | `targets`, `box?`, `rows`, `columns`, `gutter` {x, y}, `order` (rows/columns), `fit` (keep size and centre in the cell / resize to the cell) |
| `place_labels` | put each label beside its anchor shape or point, avoiding collisions with other labels and other shapes | `labels[]` {label, anchor (address) or point {x, y}}, `sides` (preference order: right, left, above, below), `distance` (pt), `leader` (none/line), `avoid?` (addresses that may be overlapped, e.g. a background band) |

- Groups move as one; rotated shapes use `drawn_bounds`.
- `place_labels` measures each label with `measure_text`, tries the preferred sides in order, then nearby offsets, and reports any label it could not place without a collision (as a fact, not an error). Leader lines are connectors glued to the label and counted in the collision check.
- Returns: the moved shapes with their new boxes, unplaced labels, and the collisions afterwards.
- One call is one undo step.
- Library: **LP21** (`stack`, `column`, `grid`), **LP22** (`place_labels`), **LP17** (`content_area`).

**Align and distribute** stay as their own tools (`ppt_align`, `ppt_distribute`; **LP1**), matching PowerPoint's commands.

### Copy and repeat (`ppt_copy`)

Build one exemplar (say a step: a chevron, a label and a box beneath, grouped), then copy it, instead of placing every part again.

- **Source:** one or more shapes, or a group (`source[]` addresses). A set of shapes is copied as a set, keeping relative positions.
- **Target:** the same slide, another slide, or another open deck (`to_slide`, with a `d2:` prefix for another deck).
- **Where:** `at` {x, y} (the top-left of the set's bounds), or `repeat` {count, dx, dy}, or `cells` {box, rows, columns, gutter} to put one copy in each grid cell.
- **Per-copy text:** `texts[]` of {`copy` (index), `target` (the source address within the copied set, or a placeholder token like `{{step}}` written in the exemplar's text), `text` or `paragraphs`}.
- **Returns:** per copy, a map from each source address to the new address, plus collisions and off-slide facts.
- One call is one undo step; `key` makes a retry safe.

**What pptx-agent has today:**
- `Shape.duplicate(dx, dy)`: copies a shape or group into the same container on the same slide, with a fresh top-level id and stamped ids stripped.
- `Document.duplicate_slide(slide, index=, notes=)` and `Slide.duplicate`: a whole slide within one deck, with charts, diagrams and notes copied as per-slide parts.
- `Slide.group`, and connectors glued by `add_connector`.
- **Not there:** copying shapes to another slide, copying anything (shapes or slides) between decks, repeat placement, and text replacement in copies.

**Library gaps: LP23 `Slide.copy_shapes(shapes, to_slide, at=)`** (L):
- the same deck or another deck; relationship-bearing content is copied with its parts: pictures and media, hyperlinks, charts with their embedded workbooks, SmartArt;
- fresh ids for **every** shape in the copy, nested group members included (today `duplicate` renumbers only the top-level shape; verify and fix as part of LP23);
- connectors glued inside the copied set are re-glued to the copies; glue to shapes outside the set is dropped and reported;
- a placeholder becomes a plain shape with its effective geometry and text formatting written out;
- theme colours stay theme references; when the target deck's theme differs, the result says which colours changed appearance (a fact).
- Repeat, cells and text replacement are composed in the tool layer (TL) over LP23, LP21's grid and the text spec.
- **`repeat` and `cells` are conditional on arm C (G1).** If model-written SVG wins, repetition goes through `ppt_draw` and these two options are dropped; the plain copy (to a position or another slide or deck, with text replacements) stays either way: it is how an agent extends an existing diagram or reuses template elements that SVG cannot reproduce.

### Design facts (`ppt_design_facts`)

**How it works without hardcoding judgement.** The library measures; it never decides. `slide.design_facts()` (**LP24**, M–L) returns, for a slide or a region of it:

| Fact | What is reported |
|---|---|
| Palette in use | distinct fill hues (lightness variants of one hue grouped), each with its shape count and its theme role (`accent2`, `accent2` tint 40%, or "not a theme colour") |
| Colour-coded groups | groups of like shapes (same preset, similar size, same row or column band), the hues used within each group, and whether a legend-like group exists (small swatches beside short text whose fills match the group's hues), with the hues it covers |
| Empty regions | the largest unused rectangles in the content area, with their area as a share of it |
| Alignment | clusters of shared left/centre/right/top/middle/bottom lines, and near-misses: edges or centres that differ by more than 0 and at most `within` pt (a parameter), with the offset |
| Shape vocabulary | presets in use with counts; corner styles (square, rounded with radius); line dashes and connector kinds |
| Text sizes | the effective sizes in use, with counts and the shapes at each size |
| Z-order notes | a line or connector drawn in front of text, with both addresses (a marker line over bars counts; see LP20) |

The only parameters are tolerances (`within`, a hue tolerance, a minimum empty-region size), never thresholds of taste. The tool returns the facts as data, with addresses. **The app's thinking layer decides** what they mean: its prompt, or a critique pass over the render plus these facts, applies its house rules.

An app-side rule written against these facts, for example in the app's critique pass:

```python
for group in facts.color_groups:
    if len(group.accent_hues) > 2 and not group.legend:
        findings.append(Finding(group.shapes,
            "more than 2 accent hues on like shapes: recolour to one accent and its tints"))
```

The same rule could be one line of the app's guidance instead ("if `ppt_design_facts` shows more than 2 accent hues on like shapes, recolour"). Either way the rule lives in the app; the libraries and tools ship none.

### The SVG read view (an option, tested in trial 3)

`ppt_read_slides(detail="svg")` returns a per-slide SVG rendered by pptx2svg in a compact agent mode (gap **LR3**, D12):
- every shape is an element or `g` with `data-id="256.5"`;
- geometry is in slide points;
- fills give the hex plus a `data-fill` theme token;
- text is boxed `text` with runs, with no glyph outlines and no embedded fonts;
- pictures, charts and tables are placeholders (`rect data-kind="picture"`) carrying their address.

It is the model's geometry view as an alternative to `detail="geometry"`: a per-shape JSON dump of kind, bounds, drawn bounds, z-index, fill (theme + hex), line, text, effective sizes, autofit and placeholder, composed in the tool layer (TL). Trial 3 compares the two on the graphics tasks (G2). The read view is output only: nothing applies it back.

### SVG authoring (parked)

The authoring profile (`ppt_draw`, gap **LP16**) is **parked pending the arm-C test (model-written SVG without code)**. The spike's arm B had agents generate SVG from Python; arm C has the model write SVG directly through an apply tool, with `&` escaping fixed. G1 is re-decided by arm C's result against the same pre-registered rule (+1 grade, or equal grade at ≤ 75% of the tokens). If arm C wins, `ppt_draw` returns as an optional route beside the shape calls; nothing in T0–T3 waits for it. The converter is on pptx-agent's local branch `svg-profile-spike`.

---

## PowerPoint tools

**Gap ids:**
- **LP**: a pptx-agent work item;
- **LE**: ooxml-edit;
- **LR**: a renderer;
- **TL**: composed in the tool layer from existing calls; no library change.

The ids of removed items (LP2–LP6, LW4) are retired, not reused; P14 and LP16 are held for `ppt_draw` while G1 is open. Lengths are in points at the tool boundary.

### Shared-shape tools (one definition each, dispatched by the document's kind)

| # | Tool | Purpose | Key parameters | Returns | Wraps | Gap |
|---|---|---|---|---|---|---|
| S1 | `open_document` | open a registered blob as a deck or document | `blob`; `as_template_source` | `doc`, kind, a short describe | `Document.open(bytes)` | warns `TemplateOpenedWarning` |
| S2 | `new_document` | a new deck or document | `kind`, `template_blob?`, `size?`/`page?`, `title?`, `author?` | `doc` | `Document.new(template=bytes…)` | — |
| S3 | `save_document` | hand the file to the app | `doc`, `name`, `format` (pptx/potx/docx/dotx/markdown/outline) | name, format, size, validate report (no bytes) | `to_bytes`, `validate(target=)`, `to_markdown`/`to_outline` → `session.outputs` | validate gate (see Safety) |
| S4 | `list_documents` / `close_document` | session housekeeping | `doc` | documents (ids, names, versions) and blobs (handles, names, mime) | session | — |
| S5 | `undo` / `redo` | step through history | `doc`, `steps` | version, summary of what was restored | `Document.undo/redo` | LE1 version |
| S6 | `find_text` | every match, with addresses | `doc`, `text`, `regex`, `scope` (slides, or a range/stories) | matches: address, kind, context | pptx `find_text`, docx `find` | — |
| S7 | `replace_text` | replace everywhere, or exactly once | `doc`, `find`, `replace`, `scope`, `expect` (`one`/`all`), `regex` | count, addresses | docx `replace`/`anchor().replace`; pptx `find_text` + `resolve().text` | pptx: TL now, **LP11** `deck.replace()` later |
| S8 | `render` | PNG of slides or pages | `doc`, `slides`/`pages`, `width` (≤2576) | images + sizes + token estimate | `render_png` | worker deadline; **LR4**, **LR5** |
| S9 | `check` | facts "as Office shows it" | `doc`, `scope`, `include` (fit, collisions, facts, design, validate, reflow, fields), `boxes?` (box-overlap mode) | a report with addresses; no verdicts | `overflows`, `collisions(boxes=)`, slide facts, design facts, `validate`, `layout().compare` | LP15, LP19, LP24, LW1 |
| S10 | `edit_chart` | data, titles, legend | `target`, `action` (set_values, set_value, add_category, remove_category, rename_category, add_series, remove_series, rename_series, set_title, set_axis_title, set_legend), `series?`, `category?`, `values?`, `text?`, `position?` | the chart's state after the edit | `ooxml_edit.charts.Chart` (both libraries adapt it) | — |
| S11 | `read_chart` | what is drawn and what Edit Data holds | `target` | type, categories, series, number formats, workbook values | `Chart.data`, `workbook_values`, `number_formats` | — |
| S12 | `edit_smartart` | node text, add or remove nodes | `target`, `action`, `node`, `text`, `parent?` | nodes | `Diagram.set_text/add_node/remove_node/add_child` | — |
| S13 | `set_properties` | metadata | `title?`, `author?`, `language?`, `subject?` | properties | pptx `title`/`author`/`language`; docx `set_properties` | — |

### PowerPoint-specific (prefix `ppt_`)

| # | Tool | Purpose | Key parameters | Returns | Wraps | Gap |
|---|---|---|---|---|---|---|
| P1 | `ppt_describe` | the deck at a glance | `doc` | slides (id, n, title, layout, shape count, content area in pt), size, sections, theme colours, fonts, roles, tint ramps, layouts with placeholders (type, idx, bounds in pt), baseline problems | `slides`, `slide.title`, `layouts`, `Layout.placeholders`, `theme.colors/fonts/roles/ramps`, `validate` | LP17 (content area) |
| P2 | `ppt_read_slides` | slide content with ids | `doc`, `slides`, `detail` (outline/geometry/svg) | outline Markdown with ids; a per-shape geometry dump; or the per-slide SVG read view | `to_outline`; shape properties; pptx2svg agent view | `geometry`: TL; `svg`: **LR3** |
| P3 | `ppt_measure_text` | size text before building, with the same spec | `paragraphs` (text spec) or `text` + `size` + `bold?`; `width`; `like?` (a shape address: its font, insets, wrap) or `preset?` (a new shape's defaults) | lines, text height, **box height** (insets included: `fit_box`), widest line, `margin_to_wrap`, `near_wrap` | `measure_text`, `fit_height` | **LP18** |
| P4 | `ppt_set_text` | replace a shape's, cell's or notes' text | `target`, `text` (`\n` paragraphs, `\v` line breaks; keeps formatting) or `paragraphs` (text spec) | text_fit | `resolve(addr).text`, `set_text` | LP18 (spec) |
| P5 | `ppt_format_text` | run, paragraph and frame formatting | `target`; run: bold, italic, underline, strike, size, font, color, hyperlink; paragraph: alignment, level, bullet (none/bullet/number), space_before/after (pt), line_spacing; frame: autofit, font_scale, insets, anchor, wrap | text_fit | `Run.format`, `Paragraph.*`, `set_bullet`, `TextFrame.*` | — |
| P6 | `ppt_set_notes` | speaker notes | `slide`, `text` | notes | `slide.notes` | — |
| P7 | `ppt_add_shape` | an autoshape or text box | `slide`, `preset` (enum of common presets + `other` with `preset_name`), `box` {x,y,w,h pt}, `text?` or `paragraphs?` (text spec), `fill?`, `line?`, `name?`, `key?` | address, text_fit, collisions | `add_shape`, `add_textbox(autofit="none")` | LP18 (spec), LP20 (preset list) |
| P8 | `ppt_set_shape` | **general setter** | `target`, any of: x, y, w, h, rotation, flip_h, flip_v, fill, gradient, line{color, width, dash, start, end}, preset, adjustments, name, autofit, insets, anchor, wrap | the shape after the edit | Shape properties, `LineFormat`, `Adjustments` | — |
| P9 | `ppt_add_picture` | insert or replace an image | `slide` or `target`, `image` (blob handle), `box?`, `keep` (frame/height/width/none), `anchor?` | address, native size | `add_picture(bytes)`, `replace_image`, `image_size` | — |
| P10 | `ppt_add_connector` | a line that stays attached | `kind` (straight/elbow/curved), `from` {shape, side}, `to` {shape, side} or points, `line` {color, width, dash, start, end} | address, route, collisions | `add_connector`, `connection_site` | — |
| P11 | `ppt_arrange` | z-order, group, duplicate, delete | `targets`, `action` (front, back, forward, backward, group, ungroup, duplicate, delete), `dx?`, `dy?` | new addresses, collisions | `bring_to_front`…`send_to_back`, `group`, `ungroup`, `duplicate`, `delete` | — |
| P12 | `ppt_align` | PowerPoint's Align | `targets`, `edge` (left, center, right, top, middle, bottom), `to` (selection/slide) | moved shapes | — | **LP1** |
| P13 | `ppt_distribute` | PowerPoint's Distribute | `targets`, `axis` (horizontal/vertical), `to` (selection/slide), `gap?` (pt; omitted = equal spread) | moved shapes | — | **LP1** |
| P15 | `ppt_edit_table` | cells, rows, columns, merges | `target`, `action` (set_cells, insert_row, delete_row, insert_column, delete_column, merge, split, set_widths, set_heights), `cells[]` {row/col or row_label/col_label, text}, `like?` | the table after the edit, by labels | `Table.*`, `cell_by_label` | — |
| P16 | `ppt_format_table` | fills, borders, cell text formatting | `target`, `region`, `fill?`, `borders?` {side, width, color}, `text?` (as P5) | — | `TableCell.fill`, `set_border`, text API | — |
| P17 | `ppt_add_table` | a new table | `slide`, `box`, `rows`, `columns`, `data[][]`, `header_row` | address | `add_table` + cells | — |
| P18 | `ppt_add_chart` | a new chart from data | `slide`, `box`, `type`, `categories`, `series[]` | address | — | **LE3 + LP7** |
| P19 | `ppt_format_chart` | series colours, data labels, number format, axis bounds | `target`, … | — | — | **LP8** (in `ooxml_edit.charts`) |
| P20 | `ppt_add_slide` | add from a layout, optionally filled | `layout`, `at?`, `title?`, `body?` (Markdown list), `notes?` | slide id, placeholders | `add_slide`, placeholders, `notes` | — |
| P21 | `ppt_draft_slides` | slides from a Markdown outline | `markdown`, `at?`, `layout_map?` | slide ids, `OutlineWarning`s, overflows | `insert_outline(images=blob mapping)` | — |
| P22 | `ppt_manage_slides` | duplicate, move, delete, find by title | `action`, `slide`, `to?`, `notes?`, `title?` | ids in order | `duplicate_slide(notes=)`, `move_slide`, `delete_slide`, `slide_titled` | — |
| P23 | `ppt_set_theme` | brand the theme | `colors?` {slot: hex}, `fonts?` {major, minor} | the theme, roles | `theme.set_colors`, `set_fonts` | layout/master editing **LP9** (later, D8) |
| P24 | `ppt_layout` | arrange shapes: row, column, grid, labels | `action` (stack, column, grid, place_labels), `targets` or `labels[]`, `box?`, `gap?`, `align?`, `justify?`, `rows?`, `columns?`, `gutter?`, `order?`, `fit?`, `sides?`, `distance?`, `leader?`, `avoid?` | moved shapes and boxes, unplaced labels, collisions | — | **LP17, LP21, LP22** |
| P25 | `ppt_copy` | copy or repeat shapes or a group, with new text | `source[]`, `to_slide?`, `at?`, `repeat?` {count, dx, dy}, `cells?` {box, rows, columns, gutter}, `texts[]?` {copy, target or token, text or paragraphs}, `key?` | per copy: source → new address map; collisions, off-slide | `Shape.duplicate` (same slide) | **LP23**; repeat/cells/texts TL |
| P26 | `ppt_design_facts` | measurable design facts of a slide | `slide`, `region?` (box), `within?` (pt), `include?` (palette, groups, empty, alignment, vocabulary, text_sizes, z_order) | the facts, with addresses; no verdicts | — | **LP24** |

With the shared tools, that is 25 + 13 = **38 definitions** reachable for a deck (39 if G1 brings back `ppt_draw`). Twelve of them are "core" (see Model guidance), and the rest load through tool search.

**PowerPoint library gaps (work items):**
- **LP1 `pptx_agent.edit.arrange`: `align(shapes, edge, to)` and `distribute(shapes, axis, to, gap=None)`** (M).
  - These match PowerPoint's Align and Distribute: relative to the selection or the slide. They use `drawn_bounds` for rotated shapes and move groups as one; the whole call is one undo step.
- **LP7 `slide.add_chart(type, categories, series, box)`,** on LE3 (M).
- **LP8 chart formatting in `ooxml_edit.charts`** (M–L): series fill/line by theme colour, data labels on/off with a number format, the value axis's min/max/major unit, and the gap width.
- **LP9 layout and master editing** (L). Trial 2's N17: template branding beyond the theme. Deferred (D8).
- **LP11 `Document.replace(find, replace, slides=, regex=)`** (S): parity with docx.
- **LP14 `LabelError.candidates`** (S): structured candidates for `valid_options`.
- **LP15 `slide.facts()`** (M). The problem facts, no rules or thresholds of taste:
  - overflow, collisions and off-slide shapes (existing `overflows`/`collisions`, plus LP19's box mode);
  - fills, lines and text colours that are not theme colours (from `Color.resolve`);
  - wrap margins (`margin_to_wrap`/`near_wrap`) for titles and text boxes.
  - (Near-alignment and text sizes moved to LP24.)
- **LP16 the SVG authoring converter:** parked (G1, arm C).
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
- **LP21 `pptx_agent.edit.layout`: `stack`, `column`, `grid`** (M): positions for given shapes in a box, with gap, gutter and alignment; groups move as one; one undo step.
- **LP22 `place_labels(labels, sides, distance, leader, avoid)`** (M–L): greedy placement by side preference, then nearby offsets, measured with `measure_text`, checked with `collisions(boxes=True)`; returns unplaced labels.
- **LP23 `Slide.copy_shapes(shapes, to_slide, at=)`** across slides and decks (L): see "Copy and repeat".
- **LP24 `slide.design_facts(region=, within=)`** (M–L): see "Design facts".
- **Later:** importing a whole slide from another deck (`Document.import_slide`). No trial task needs it; `ppt_copy` covers shapes.

---

## Word tools (prefix `word_`; plus S1–S13)

| # | Tool | Purpose | Key parameters | Returns | Wraps | Gap |
|---|---|---|---|---|---|---|
| W1 | `word_describe` | the document at a glance | `doc` | headings tree (id, level, numbered list/text, block count), sections (id, page setup, header/footer stories), styles in use, comment and revision counts by author, fields by type, tables, drawings, charts, pages, compatibility mode, baseline problems | `paragraphs`, `sections()`, `styles`/`usage`, `comments()`, `changes()`, `fields()`, `charts()`, `layout().page_count` | TL (heading tree); `doc.outline()` **LW6** later |
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
| W13 | `word_changes` | list grouped changes | `author?`, `kind?`, `within?`, `detail` (grouped/records) | changes with ids and old/new text | `changes()`, `revisions()` | — |
| W14 | `word_review_changes` | accept or reject | `action`, `ids?`, `within?`, `author?`, `all?` | count; what remains | `accept`/`reject`/`accept_all`/`reject_all`, `Change.accept/reject` | — |
| W15 | `word_comments` | comment threads | `action` (list, add, reply, resolve, reopen, edit, delete), `target`/`find`, `comment?`, `text?` | thread(s) | `CommentOps`, `comments()` | — |
| W16 | `word_sections` | breaks and page setup | `action` (insert_break, remove_break, set), `after?`, `kind?`, `section`, orientation, margins (pt), columns, page size | sections | `insert_section_break`, `remove_section_break`, `set_section` | — |
| W17 | `word_headers_footers` | header and footer content | `section`, `which` (header/footer), `type` (default/first/even), `action` (set, remove, link, unlink), `text?`, `page_number` (none/after_text/`Page X of Y`) | story ids | `add_header`/`add_footer`, `remove_*`, `link_to_previous`, `insert_page_number`, `set_even_and_odd_headers` | — |
| W18 | `word_fields` | TOC, captions, cross-references, page fields, update | `action` (list, insert_toc, insert_caption, insert_cross_reference, insert_date, insert_field, update), `at`, `levels?`, `bookmark?`, `label?` | fields; unknown | `FieldOps`, `insert_cross_reference`, `update_fields` | stale detection **LW2**; an empty-TOC warning (trial N8) if not already shipped |
| W19 | `word_notes` | footnotes and endnotes | `action` (insert, edit, delete, move), `at`/`note`, `kind`, `text` | note id | `NoteOps` | — |
| W20 | `word_links` | hyperlinks and bookmarks | `action` (add_hyperlink, add_bookmark, rename_bookmark, remove_bookmark, list), `target`/`find`, `url?`, `bookmark?` | ids | `LinkOps` | — |
| W21 | `word_styles` | the style sheet | `action` (list, describe, add, modify, remove, purge_unused), `name`, `kind`, `based_on?`, formatting fields as in W9, `replacement?` | styles with usage | `Styles.find/add/modify/remove/usage/purge_unused`, `resolve` | — |
| W22 | `word_insert_table` | a new table | `at` (after/before id), `rows`, `columns`, `data[][]`, `header_rows`, `style?`, `widths?` (pt or %) | table id | `insert_table` | — |
| W23 | `word_edit_table` | structure and cell text | `table`, `action` (set_cells, insert_row, delete_row, insert_column, delete_column, merge, split), `cells[]` {row, col, text}, `at?`, `span?` | table as GFM + ids | `TableOps`, cell paragraphs `set_text` | cells by label: TL (from header row/column) |
| W24 | `word_format_table` | table, row and cell formatting | `table`, `scope` (table/row/cell/column), `index?`, width, alignment, look, shading, borders, vertical_alignment, height, height_rule, repeat_header | — | `set_table`, `set_row`, `format_cell`, `set_column_width` | — |
| W25 | `word_insert_picture` | inline or floating picture | `at`, `image` (blob handle), `width` (pt), `alt_text`, `float?` {wrap, x, y, from} | drawing id | `insert_picture(bytes)`, `float_drawing`, `set_drawing` | — |
| W26 | `word_drawings` | move, resize, wrap, z-order, text boxes | `target`, `action` (move, resize, float, inline, set, insert_text_box, insert_shape), … | — | `DrawingOps` | — |
| W27 | `word_controls` | content controls | `action` (list, insert, fill, remove), `at`, `type`, `items?`, `value?` | ids | `ControlOps`, `content_controls()` | — |
| W28 | `word_insert_chart` | a new chart from data | `at`, `type`, `categories`, `series[]`, `width?` | drawing id | — | **LE3 + LW3** |
| W29 | `word_template` | template work | `action` (apply_styles_from, save_as_template, upgrade_to_modern), `template_blob?` | reflow pages | `Document.new(template=)` (S2), `save_as_template`, `upgrade_to_modern` | "apply a template's styles to an open document": **LW5** |

That is 29 + 13 = **42 definitions** reachable for a document.

**Word library gaps:**
- **LW1 `doc.problems()`: layout facts** (M–L), from `layout()`:
  - a table wider than its text column;
  - a picture past the margins;
  - a heading last on its page despite `keep_with_next`;
  - an empty TOC (trial 2's N8);
  - a REF to a missing bookmark;
  - text under 8 pt.
- **LW2 field staleness** (S–M): a field whose inputs changed since its last update (headings for a TOC, bookmarks for a REF, pagination for PAGEREF) is marked `stale` in `fields()`. The tool result then says "run `word_fields update`".
- **LW3 `doc.insert_chart(at, type, categories, series)`,** hosted as a drawing, on LE3 (M).
- **LW5 `doc.apply_template(bytes, styles=True, headers=False)`** (M): bring a template's styles into an open document. Today only `Document.new(template=)` and `copy_blocks` exist.
- **LW6 `doc.outline()`** (S): the heading tree as data. It can be composed in the tool layer from `paragraphs()` and outline levels, but is cleaner in the library.

**ooxml-edit gaps:**
- **LE1 `History.version`:** monotonic, restored on undo/redo (S).
- **LE2: `ooxml_edit.tools` itself** (T0).
- **LE3 a new chart from data:** the chart part (bar, column, line and pie at least), with an embedded workbook written to match and a `GraphicHost` insertion hook, so pptx (LP7) and docx (LW3) each add only the frame (L).

**Renderer items:**
- **LR1:** docx2svg still styles TOC entries as links (trial #18).
- **LR2:** pptx2svg character spacing (trial 2's N16).
- **LR3: pptx2svg compact read mode** (M; must be ready for T6): a compact per-slide SVG with `data-id` addresses on every shape, geometry in points, `data-fill` theme tokens, boxed text without glyph outlines or embedded fonts, and placeholders for pictures, charts and tables. Exit: every `data-id` resolves through `resolve()`; tokens per slide are measured against the geometry dump on the trial decks.
- **LR4: pptx2svg bullet "blob"** (S): `set_bullet()` bullets rendered as large dark blobs in the library's render (the spike, p7 A2), while PowerPoint draws them correctly. It misleads every render-based check.
- **LR5: pptx2svg custom-geometry outlines** (S): outlines of custom-geometry shapes are not drawn in the library's render; PowerPoint draws them (the spike).

LR1 and LR2 do not block the tools. LR4 and LR5 should land before T6, since agents trust the render.

### Tools shared in shape across both formats

S1–S13 are single definitions that dispatch by the document's kind. Each library declares the same definition with its own handler; `Toolbox` merges same-named tools whose definitions are identical into one tool with a handler per kind, and refuses two different definitions under one name.
- Their parameters are the union, and each says which format it applies to (`slides` for pptx, `pages`/`range` for docx). The dispatcher returns `invalid_arguments` naming the right field.
- `edit_chart`, `read_chart` and `edit_smartart` wrap the same `ooxml_edit.charts` classes in both formats.

These pairs stay format-specific, because their parameters differ too much:

| Purpose | PowerPoint | Word |
|---|---|---|
| describe | `ppt_describe` | `word_describe` |
| read | `ppt_read_slides` | `word_read` |
| set text | `ppt_set_text` | `word_set_text` |
| general setter | `ppt_set_shape`/`ppt_format_text` | `word_format` |
| add a picture | `ppt_add_picture` | `word_insert_picture` |
| new table | `ppt_add_table` | `word_insert_table` |
| new chart | `ppt_add_chart` | `word_insert_chart` |

---

## Model guidance

**A system-prompt fragment of about 600 tokens, shipped as `prompts.SYSTEM`**, plus one fragment per format of about 300 tokens. The app concatenates them, followed by its own guidance (see "The thinking layer"). The shipped fragments are mechanics only, with no style rules.

1. **Plan before editing:**
   - Call `*_describe` once, then read only what the task touches.
   - Before building a graphic, read the theme, the content area and the layout placeholders, and size text with `ppt_measure_text` using the same text spec you will build with.
2. **Address by id:**
   - Copy addresses exactly from read results, including `data-id`s in the SVG view.
   - Never paste Markdown from `ppt_read_slides`/`word_read` into a set-text tool; outline text is escaped.
   - Use `find_text` when you know the words but not the id.
3. **Prefer coarse tools:**
   - for a repeated element, build one exemplar, group it, and `ppt_copy` it with `repeat` or `cells` and per-copy `texts`;
   - `ppt_layout` to place shapes in a row, column or grid, and to place labels; `ppt_align`/`ppt_distribute` instead of computing coordinates;
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

**Keeping descriptions short:**
- Each tool description is 1–3 sentences: the purpose, when to use it instead of its neighbour, and what it returns.
- Each property description is at most 15 words, with units and the default stated.
- Enums replace prose.
- The text spec is described once (in `ppt_add_shape`) and referred to by the others.
- No `input_examples` at first. Trial 3 checks whether one `ppt_copy` example earns its tokens on Claude.
- **Budget, checked by a test:** the core group is ≤ 4,000 tokens per format, and all definitions are ≤ 12,000 tokens per format. These are counted with Anthropic's count-tokens endpoint in CI (marked online). OpenAI counts are estimated offline until a GPT-6 trial is planned.

**Tool groups and deferred loading:**
- **Core, never deferred (12 per format):**
  - shared: `open_document`, `new_document`, `save_document`, `undo`, `find_text`, `replace_text`, `render`, `check`;
  - pptx: `ppt_describe`, `ppt_read_slides`, `ppt_set_text`, `ppt_set_shape`;
  - Word: `word_describe`, `word_read`, `word_set_text`, `word_format`.
- **Groups** (every tool is in exactly one; each has fewer than 10, as OpenAI recommends for namespaces):

  | Group | Tools |
  |---|---|
  | `shared_misc` | S4, S10–S13 |
  | `ppt_text` | P3, P5, P6 |
  | `ppt_graphics` | P7, P10, P11, P25 |
  | `ppt_layout` | P12, P13, P24, P26 |
  | `ppt_objects` | P9, P15–P19 |
  | `ppt_slides` | P20–P23 |
  | `word_text` | W3, W6, W7, W8 |
  | `word_review` | W4, W13–W15 |
  | `word_structure` | W11, W12, W16–W20 |
  | `word_objects` | W22–W28 |
  | `word_style` | W10, W21, W29 |

- **Anthropic:** non-core tools get `defer_loading: true` and the BM25 tool search is added; `cache_control` goes on the last core tool.
- **OpenAI Responses (adapter):** each group is a `namespace` with `defer_loading`, plus `{type:"tool_search"}`.
- **Without tool search** (the Chat Completions fallback, or older models), the app picks groups up front with `toolbox.definitions(groups=[…])`. `allowed_tools` (OpenAI) narrows calls without changing the cached `tools`.

---

## The thinking layer: what the app supplies (S)

The libraries and tools ship **no** house style, palette rules, legend or gridline rules, density limits or grid. Design judgement belongs to the application. The tools supply facts (`check`, `ppt_design_facts`); the app supplies the rules. The toolbox offers two hooks and documents them:

- **Guidance.** The app appends its own text after the shipped fragments, through `toolbox.system_prompt(extra=…)`. Examples are a house style, a brand palette, or "if `ppt_design_facts` shows more than 2 accent hues on like shapes, recolour; if colour carries meaning, add a legend". The docs show how to write such guidance; no defaults are shipped.
- **Critique pass.** The app may register `critique(doc, slides, renders, facts) -> list[Finding]`, where `facts` include the design facts (LP24) and the problem facts (LP15/LW1). It typically makes its own model call over the render plus the facts, or runs rules like the example in "Design facts". `check(include=[…, "app"])` runs it and returns its findings under `app_findings`, labelled as coming from the app. The app may run it after the agent finishes instead.

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
   - **one measuring model:** for a set of text specs and presets, `ppt_measure_text`'s box height equals the `text_fit` height after `ppt_add_shape` with the same spec;
   - `stack`/`column`/`grid` place shapes exactly (to 0.01 pt), with groups and rotated shapes;
   - `place_labels` leaves no collisions on the p8 milestone, o1 and m1 label sets, or reports the unplaced ones;
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
7. **Trial 3 (Sonnet 5 only, D9):**
   - **Tasks:** the trial-2 tasks (p1–p8, and w1–w6 with w3 and w5 restored: 14 tasks) plus the spike's o1 and m1, so graphics are not judged on p7/p8 alone: 16 tasks. Each is run **twice**, using only the tools: no Python and no file access.
   - **Read-view comparison (G2):** the four graphics tasks (p7, p8, o1, m1) run twice more with `detail="svg"` available instead of `detail="geometry"`, everything else equal. The rule is pre-registered before the first run: the SVG read view becomes the default geometry view if its mean grade is at least the geometry arm's and its tokens are no higher; otherwise `geometry` stays the default and `svg` stays optional or is dropped.
   - **Harness:** a small `harness/run_trial.py` in the tool-layer repository.
     - It runs the Anthropic loop (all results in one user message), with a per-run session, a fixed clock and a token and tool-call meter, and writes `transcript.jsonl` and the output bytes.
     - Its provider loop is pluggable, so a later GPT-6 trial (Responses API, `previous_response_id`) reuses it unchanged.
     - Grading reuses trial 2's grading unchanged: the per-task checks, the Office oracles run serially, and blind Sonnet 5 visual graders that do not see the transcript.
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

Status: `ooxml_edit.tools` and LE1 are implemented, with toy tools in the tests. Every exit item below passes offline; the online Anthropic test is written (`pytest -m provider`) and waits for an `ANTHROPIC_API_KEY`, and with it the live image round trip.

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

### T1 — PowerPoint core (L)

- **Scope:**
  - S1–S9 and S13 for pptx; P1, P2 (`outline` and `geometry`), P3–P11, P15–P17, P20–P23; the per-edit checks; paging; the core and group definitions;
  - **LP18** one measuring model and the text spec (P3, P4, P7); **LP17** `content_area`; **LP19** box-overlap collisions; **LP20** small fixes; **LP14**.
- **Exit:**
  - golden transcripts for p1–p6 replay to passing checks, with Office opening every output;
  - the measure/build agreement test passes;
  - `check(boxes=true)` reports the spike's p8 A1 label-box overlap;
  - the core definitions are ≤ 4k tokens.

### T2 — Word core (L)

- **Scope:** S1–S9 for docx; W1–W27 and W29 (the `apply_styles_from` action waits for LW5); tracking mode; the validate gate.
- **Exit:**
  - golden transcripts for w1–w6 replay to passing checks, and Word opens every output;
  - a reflow check on `sample-long.docx` stays within its 30 s deadline.

### T3 — layout and complex diagrams (L–XL)

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

### T4 — charts and the remaining gaps (L)

- **Scope:** LE3, LP7, LP8, LW1, LW2, LW3, LW5, LW6, LP11; tools P18, P19 and W28; `word_template` complete.
- **Exit:**
  - a new chart opens in PowerPoint and Word with Edit Data matching (oracle);
  - `check` on Word reports stale fields and an empty TOC.

### T5 — guidance and loading (M)

- **Scope:** the system-prompt fragments (mechanics only), the description pass, tool groups, deferred loading, token budgets in CI, `allowed_tools`, and the thinking-layer docs with one example app guidance.
- **Exit:**
  - a scripted Anthropic session discovers a deferred tool through tool search and calls it, and the OpenAI namespace and `tool_search` shapes pass offline;
  - the budgets hold.

### T6 — trial 3 (M; Sonnet 5 only)

- **Scope:** the harness, 40 runs (including the read-view comparison), grading, and a report in the trial-2 format, written by the main session.
- **Exit:**
  - the report is written, and G2 is decided by its pre-registered rule;
  - every gap it finds is filed against T1–T4 or the libraries.

```
T0 ─┬─▶ T1 ─┬─▶ T3 (layout, copy, design facts) ──┐
    │       │                                     │
    └─▶ T2 ─┴─▶ T4 ───────────────────────────────┴─▶ T5 ─▶ T6
LR3, LR4, LR5 (pptx2svg; any time) ───────────────────────▶ T6
```

- T0 is next. T1 and T2 are independent after it.
- T3 needs T1 (the text spec and `content_area` come first).
- T5 can start once T1 lands, but its budgets are only final after T3 and T4.
- Trial 3 runs last, but a smoke run of the graphics tasks after T3 is cheap and worth doing.
- If arm C re-opens SVG authoring (G1), `ppt_draw` and LP16 become an optional addition after T3; nothing above waits for it.

---

## Open decisions

| # | Question | Options | Recommendation |
|---|---|---|---|
| G1 | SVG authoring (`ppt_draw`) as an optional route | parked · add beside shape calls | **Re-decide on the arm-C test** (model-written SVG without code) with the spike's rule: +1 grade, or equal grade at ≤ 75% of the tokens. Shape calls stay the main route either way. |
| G2 | The model's geometry view | `detail="geometry"` (JSON dump) · `detail="svg"` (LR3 read view) | **Decide in trial 3** by its pre-registered rule; `geometry` is the default until then. |
| O3 | Graphics tools in the core set | core stays 12 · add `ppt_add_shape`/`ppt_layout`/`ppt_copy` to the pptx core | **Keep 12** and load the graphics groups by tool search; revisit after the T3 smoke run if graphics tasks always load them. |

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
- **Later:** SVG authoring profile: tested in the spike (shape calls 8.50 vs SVG 8.13, +5% tokens); the converter is parked on pptx-agent's local branch `svg-profile-spike`; parked pending the arm-C test (model-written SVG without code), and otherwise revisited only if a model shows a clear advantage.
- **Later:** importing whole slides between decks; layout and master editing (LP9, D8).
```