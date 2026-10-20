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
  parameter helpers that build each tool's canonical schema; the roles (:data:`ROLES`:
  ``review``, ``resolve``, ``accept``, ``edit``), additive toolsets a toolbox or session loads;
* :mod:`.schema` -- the common strict subset both providers accept, its checker, and the
  call validator (no jsonschema dependency);
* :mod:`.adapters` -- definitions and results for the Anthropic Messages API, the OpenAI
  Responses API and Chat Completions, as plain dicts; no provider SDK is imported;
  :func:`strict_problems` checks any OpenAI definitions, an application's own too, against
  the documented strict-mode rules before the first call;
* :mod:`.results` -- the result envelope, error codes with ``valid_options``, image token
  estimates, truncation and paging;
* :mod:`.session` -- documents, blobs and outputs, all in memory and under handles; locks,
  versions and caches; :class:`SessionView`, one agent's roles and context on a shared
  session;
* :mod:`.datasets` -- :class:`Dataset`, a table of values with the sources of each, kept in
  the session under ``d:<id>`` and named by later calls on any document;
* :mod:`.sources` -- :class:`Rows`: what a tool takes rows from, a dataset or another open
  document's address its format resolves (``t:sales``), with where each value came from;
* :mod:`.textfacts` -- references to one value in text (``{{d:by_topic/door/mean}}``),
  written as the value in the session's locale by the dispatcher, each a fact kept as
  provenance;
* :mod:`.limits` -- size limits, magic-byte checks and the zip-bomb guard;
* :mod:`.worker` -- worker processes whose deadlines are kept by killing the worker
  (subprocess workers in a process that may not start multiprocessing children), as many
  by default as the process's CPUs and container quota allow, warmed before their first
  task, several tasks at once (:func:`run_all`);
* :mod:`.rendercache` -- rendered images (and decoded embedded fonts) by content key, in
  memory, sized in bytes (:class:`ContentStore`);
* :mod:`.logs` -- call records with argument digests, not content;
* :mod:`.hints` -- what a name that is no tool means: a tool, and an action of it;
* :mod:`.tiles` -- contact images: many rendered views tiled into one labelled image
  (``render``'s option), the options, layout and SVG; a format rasterises it;
* :mod:`.prompts` -- the shared system-prompt fragment: mechanics, no house style;
* :mod:`.assets` -- an application's asset catalogue (brand icons...) that a format's
  configured tools search and place by id: the :class:`AssetCatalogue` protocol and
  :class:`FolderCatalogue`, a folder with a JSON or CSV index;
* :mod:`.shared` -- the definitions of the tools every format shares (open, save, undo,
  describe, find, replace, render, check, charts, SmartArt, properties, and the generic
  ``batch``),
  one each; libraries add a handler for their kind.

Nothing here reads or writes a file: documents and inputs come in as bytes, saved files go
out as bytes to the application, and no tool may take a path.  Like :mod:`ooxml_edit.charts`
this subpackage is optional, and the core never imports it.
"""

from . import assets, hints, rendercache, shared, textfacts, tiles
from .assets import Asset, AssetCatalogue, FolderCatalogue
from .adapters import StrictProblem, strict_problems
from .datasets import (DATASET_ID, DATASET_PREFIX, TYPE_LABELS, Dataset, DatasetEntry,
                       dataset_id, row_range)
from .dispatch import REF, Call, CallContext, Toolbox, collect_warnings
from .limits import LimitError, Limits
from .logs import CallRecord
from .prompts import SYSTEM, system_prompt
from .registry import (ACCEPT, ANY_ROLE, CORE, EDIT, RESOLVE, REVIEW, ROLES, Param, Tool,
                       ToolGroup, array, boolean, build_schema, free_object, integer, names_as,
                       number, obj, role_set, string, tool)
from .results import (ERROR_CODES, Image, Result, ToolError, anthropic_image_tokens,
                      openai_image_tokens, page_list, page_text, truncate)
from .schema import CallError, SubsetError, canonical, check_subset, validate_call
from .sources import Rows
from .session import (DEFAULT_LOCALE, LOCALE_FIELDS, Blob, DefaultLocale, DocumentEntry,
                      DocumentFormat, Output, Session, SessionView, StepOwner, doc_order,
                      locale_of)
from .rendercache import ContentStore, content_key
from .worker import (InProcess, SubprocessPool, WorkerPool, available_cpus, default_workers,
                     run_all, worker_task)

__all__ = [
    "ACCEPT", "ANY_ROLE", "EDIT", "RESOLVE", "REVIEW", "ROLES", "role_set",
    "Asset", "AssetCatalogue", "Blob", "FolderCatalogue", "assets", "CORE", "Call", "CallContext", "CallError", "CallRecord", "DATASET_ID",
    "DATASET_PREFIX", "DEFAULT_LOCALE", "Dataset", "DatasetEntry", "DefaultLocale", "DocumentEntry", "DocumentFormat",
    "ERROR_CODES", "LOCALE_FIELDS", "REF", "Image", "LimitError", "Limits", "Output", "Param",
    "Result", "Rows", "SYSTEM", "TYPE_LABELS",
    "InProcess", "Session", "SessionView", "StepOwner", "StrictProblem", "SubprocessPool", "SubsetError", "Tool", "ToolError", "ToolGroup", "Toolbox",
    "WorkerPool", "ContentStore", "available_cpus", "content_key", "default_workers",
    "rendercache", "run_all",
    "anthropic_image_tokens", "array", "boolean", "build_schema", "canonical", "check_subset",
    "collect_warnings", "dataset_id", "doc_order", "free_object", "integer", "locale_of",
    "row_range",
    "names_as", "number", "obj", "openai_image_tokens",
    "page_list", "page_text", "hints", "shared", "textfacts", "strict_problems", "string", "system_prompt", "tool", "truncate", "validate_call",
    "worker_task",
]
