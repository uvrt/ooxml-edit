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
| `tools.adapters` | definitions for the Anthropic Messages API (strict on as many tools as the per-request limits allow, writing tools first; optional deferred loading and caching), the OpenAI Responses API (all required, optional ones nullable; optional namespaces and tool search) and Chat Completions; results as `tool_result` blocks, `function_call_output` items, or tool messages plus a user message with the images; checkers for each provider's documented rules. Plain dicts |
| `tools.results` | the result envelope, error codes with `valid_options`, image token estimates, truncation and paging |
| `tools.session` | `Session`: documents opened from bytes (`d1`), inputs registered as blobs (`b1`), outputs handed to the application as bytes; a re-entrant lock per document, taken in a fixed order across documents; versions (`History.version`) and version-keyed caches; an injected clock |
| `tools.limits` | size limits, magic-byte checks, image sizes read from headers, and a zip-bomb guard |
| `tools.worker` | a process pool for rendering and layout whose deadlines are kept: a worker past its deadline is killed and the call reports `timeout` |
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
