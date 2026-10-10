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
| `tools.limits` | size limits, magic-byte checks, image sizes read from headers, and a zip-bomb guard |
| `tools.worker` | a process pool for rendering and layout whose deadlines are kept: a worker past its deadline is killed and the call reports `timeout`; in-process where processes cannot be started (below) |
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
in. The `batch` tool runs many calls in one round trip -- in order, all or none, one undo
step per document, the facts (`checks`) computed once at the end -- and names the failing op
and its valid options when one fails. Its ops' arguments are free-form objects, so `batch`
is the one tool never sent strict: each op is validated by the dispatcher against its own
tool's schema instead of by constrained decoding.

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

**Rendering where processes cannot be started.** Rendering and layout run in worker
processes so a deadline can be kept by killing one. A daemonic process may not start
children -- a Celery prefork worker's child is one, and `Process.start` fails there with
"daemonic processes are not allowed to have children". The pool notices
(`multiprocessing.current_process().daemon`, or that failure on the first start), logs a
warning once and runs the work in-process instead; `Toolbox(workers=0)` asks for that
anywhere, and `Toolbox(runner=...)` takes the application's own executor (anything with
`run(fn, *args, timeout=..., **kwargs)` and `close()`; `tools.InProcess(size)` is the
built-in one). In-process the deadline still holds for the *caller*: the call returns
`timeout` when it passes. It does not hold for the *work*: a thread cannot be killed, so
the task runs on to its end in the background, keeping its slot (at most `size` run at a
time, abandoned ones included), and pure-Python work shares the GIL with the calls being
served. A runaway render therefore delays later ones, each of which still times out on its
own deadline. Where that matters, run the toolbox in a process that may start children --
Celery's `--pool=threads` or `solo`, or a non-daemonic service -- and keep the pool.

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
documents alike).

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
