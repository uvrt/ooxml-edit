"""The plumbing of an agent tool layer over document editors: optional, format-neutral.

An application imports a format library's tools (each library ships its own, built on
this), puts them in a :class:`Toolbox`, and runs a model's calls through it::

    from ooxml_edit.tools import Toolbox

    toolbox = Toolbox(LIBRARY_TOOLS, formats=[LIBRARY_FORMAT])
    session = toolbox.session(clock=my_clock)
    d1 = session.open(document_bytes, name="report")      # -> "d1"
    b1 = session.add_blob(logo_bytes, name="logo.png")     # -> "b1"

    tools = toolbox.definitions("anthropic")                # or "openai-responses"
    result = toolbox.dispatch(session, name, arguments)     # -> Result
    block = toolbox.render_result("anthropic", result, tool_use_id)

    for out in session.take_outputs():                      # files saved by a tool
        store(out.name, out.format, out.data)

What is here:

* :mod:`.registry` -- :class:`Tool`, :class:`ToolGroup`, the :func:`tool` decorator and the
  parameter helpers that build each tool's canonical schema;
* :mod:`.schema` -- the common strict subset both providers accept, its checker, and the
  call validator (no jsonschema dependency);
* :mod:`.adapters` -- definitions and results for the Anthropic Messages API, the OpenAI
  Responses API and Chat Completions, as plain dicts; no provider SDK is imported;
* :mod:`.results` -- the result envelope, error codes with ``valid_options``, image token
  estimates, truncation and paging;
* :mod:`.session` -- documents, blobs and outputs, all in memory and under handles; locks,
  versions and caches;
* :mod:`.limits` -- size limits, magic-byte checks and the zip-bomb guard;
* :mod:`.worker` -- a process pool whose deadlines are kept by killing the worker;
* :mod:`.logs` -- call records with argument digests, not content;
* :mod:`.prompts` -- the shared system-prompt fragment: mechanics, no house style.

Nothing here reads or writes a file: documents and inputs come in as bytes, saved files go
out as bytes to the application, and no tool may take a path.  Like :mod:`ooxml_edit.charts`
this subpackage is optional, and the core never imports it.
"""

from .dispatch import Call, Toolbox, collect_warnings
from .limits import LimitError, Limits
from .logs import CallRecord
from .prompts import SYSTEM, system_prompt
from .registry import (CORE, Param, Tool, ToolGroup, array, boolean, build_schema, integer,
                       number, obj, string, tool)
from .results import (ERROR_CODES, Image, Result, ToolError, anthropic_image_tokens,
                      openai_image_tokens, page_list, page_text, truncate)
from .schema import CallError, SubsetError, canonical, check_subset, validate_call
from .session import Blob, DocumentEntry, DocumentFormat, Output, Session, doc_order
from .worker import WorkerPool

__all__ = [
    "Blob", "CORE", "Call", "CallError", "CallRecord", "DocumentEntry", "DocumentFormat",
    "ERROR_CODES", "Image", "LimitError", "Limits", "Output", "Param", "Result", "SYSTEM",
    "Session", "SubsetError", "Tool", "ToolError", "ToolGroup", "Toolbox", "WorkerPool",
    "anthropic_image_tokens", "array", "boolean", "build_schema", "canonical", "check_subset",
    "collect_warnings", "doc_order", "integer", "number", "obj", "openai_image_tokens",
    "page_list", "page_text", "string", "system_prompt", "tool", "truncate", "validate_call",
]
