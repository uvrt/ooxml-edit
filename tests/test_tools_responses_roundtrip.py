"""The OpenAI Responses round trip, offline: a stub client, real dispatch on the toy formats.

THE FIXTURES HERE ARE SYNTHETIC.  They are written by hand, modelled on a live run that a
production user reported in October 2026 -- both agent libraries on Azure OpenAI's Responses
API (the v1 endpoint through the plain OpenAI client), ``definitions("openai-responses")``
as sent by default, ``store=False`` with ``include=["reasoning.encrypted_content"]``, with
``images="output"`` and ``images="message"``; every task completed.  They are not recorded
transcripts: only the facts reported are encoded, in the item shapes OpenAI documents
(developers.openai.com/api/docs/guides/tools-tool-search and .../guides/reasoning):

* a ``function_call`` for a tool in a deferred group carries the bare ``name`` and the group
  in a separate ``namespace`` field;
* hosted tool search answers with a ``tool_search_call`` (``execution: "server"``,
  ``call_id: null``) followed by a ``tool_search_output`` listing the loaded namespace;
* with ``store=False`` every reasoning item carries ``encrypted_content``, and the whole
  output goes back in the next input unchanged.

No network: the client is a stub, the tools are ``tools_toys``.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from ooxml_edit.tools import Toolbox
from ooxml_edit.tools import adapters

import synthetic
import tools_toys

TITLE = "Quarterly review"
INCLUDE = ["reasoning.encrypted_content"]


class Item(dict):
    """An output item as the SDK hands it over: attribute access (``item.type``) as in the
    documented loop, and a plain mapping when it goes back in the next input."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _reasoning(n: int) -> Item:
    return Item(type="reasoning", id=f"rs_{n:04d}", summary=[],
                encrypted_content=f"gAAAAAB-opaque-reasoning-{n}==")


def _call(n: int, name: str, arguments: dict, namespace: str | None = None) -> Item:
    item = Item(type="function_call", id=f"fc_{n:04d}", call_id=f"call_{n:04d}", name=name,
                arguments=json.dumps(arguments), status="completed")
    if namespace is not None:
        item["namespace"] = namespace
    return item


def _namespace(definitions: list[dict], name: str) -> dict:
    return next(d for d in definitions if d.get("type") == "namespace" and d["name"] == name)


def _turns(definitions: list[dict]) -> list[list[Item]]:
    """Three responses: a core call; a server tool search, then two deferred calls by bare
    name with their ``namespace``; a closing message.  Reasoning first in each."""
    render_space = _namespace(definitions, "toy_render")
    read_space = _namespace(definitions, "toy_read")
    return [
        [_reasoning(1),
         _call(1, "toy_ppt_set_title", {"doc": "d1", "page": 1, "text": TITLE, "key": None})],
        [_reasoning(2),
         Item(type="tool_search_call", id="tsc_0001", execution="server", call_id=None,
              status="completed", arguments={"paths": ["toy_render", "toy_read"]}),
         Item(type="tool_search_output", id="tso_0001", execution="server", call_id=None,
              status="completed", tools=[render_space, read_space]),
         _call(2, "toy_ppt_render", {"doc": "d1", "page": 1, "width": 64, "stall": None},
               namespace="toy_render"),
         _call(3, "toy_ppt_find_label", {"doc": "d1", "label": TITLE}, namespace="toy_read")],
        [_reasoning(3),
         Item(type="message", id="msg_0001", role="assistant", status="completed",
              content=[{"type": "output_text", "text": "Done.", "annotations": []}])],
    ]


class StubResponses:
    """``client.responses``: hands out the scripted outputs, keeps a deep copy of each
    request as it was sent."""

    def __init__(self, outputs: list[list[Item]]) -> None:
        self.outputs = list(outputs)
        self.requests: list[dict] = []

    def create(self, **request):
        self.requests.append(copy.deepcopy(request))
        return SimpleNamespace(output=self.outputs.pop(0))


@pytest.fixture
def toolbox():
    with Toolbox(tools_toys.TOOLS, formats=tools_toys.FORMATS, groups=tools_toys.GROUPS,
                 workers=0) as box:
        yield box


def _session(toolbox):
    session = toolbox.session()
    session.open(synthetic.outer_package(), "deck.pptx")
    return session


def _run(toolbox, session, client, *, images=None, detail=None):
    """The loop in pptx-agent's and docx-agent's ``tools/README.md``, with the request
    options production used; ``dispatched`` records every local call."""
    tools = toolbox.definitions("openai-responses")
    items = [{"role": "user", "content": "Title the first page, render it, find it."}]
    dispatched = []
    while True:
        response = client.responses.create(model="gpt-6", instructions="system", tools=tools,
                                           input=items, store=False, include=INCLUDE)
        items += response.output
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            break
        dispatched += [c.name for c in calls]
        results = [(c.call_id, toolbox.dispatch(session, c.name, c.arguments)) for c in calls]
        items += toolbox.render_results("openai-responses", results, images=images,
                                        detail=detail)
    return items, dispatched


# -- 1. deferred calls by bare name -----------------------------------------------------------


def test_a_deferred_call_arrives_by_bare_name_and_dispatches(toolbox):
    definitions = toolbox.definitions("openai-responses")
    space = _namespace(definitions, "toy_render")
    assert [t["name"] for t in space["tools"]] == ["toy_ppt_render"]   # bare, as sent
    session = _session(toolbox)
    call = _call(7, "toy_ppt_render", {"doc": "d1", "page": 1, "width": 32, "stall": None},
                 namespace="toy_render")
    result = toolbox.dispatch(session, call.name, call.arguments)
    assert result.ok and result.images, result.error
    item = toolbox.render_result("openai-responses", result, call.call_id)
    assert item["type"] == "function_call_output" and item["call_id"] == "call_0007"
    assert "namespace" not in item and "name" not in item
    assert adapters.openai_input_problems([call, item]) == []


def test_a_qualified_name_is_not_needed_and_is_answered_with_the_bare_one(toolbox):
    session = _session(toolbox)
    for name in ("toy_render.toy_ppt_render", "toy_render__toy_ppt_render"):
        result = toolbox.dispatch(session, name, {"doc": "d1", "page": 1, "width": 32})
        assert not result.ok and result.error.code == "invalid_arguments"
        assert result.error.valid_options[0] == "toy_ppt_render"


# -- 2. server-side tool search ---------------------------------------------------------------


def test_server_tool_search_items_pass_back_unchanged_and_are_never_dispatched(toolbox):
    session = _session(toolbox)
    definitions = toolbox.definitions("openai-responses")
    turns = _turns(definitions)
    sent = copy.deepcopy(turns)
    client = SimpleNamespace(responses=StubResponses(turns))
    items, dispatched = _run(toolbox, session, client)

    assert dispatched == ["toy_ppt_set_title", "toy_ppt_render", "toy_ppt_find_label"]
    search = [item for item in items if item.get("type", "").startswith("tool_search")]
    assert search == [sent[1][1], sent[1][2]]
    assert [item["type"] for item in search] == ["tool_search_call", "tool_search_output"]
    assert all(item["execution"] == "server" and item["call_id"] is None for item in search)
    # Our items answer exactly the function calls, in order.
    answers = [item["call_id"] for item in items if item.get("type") == "function_call_output"]
    assert answers == ["call_0001", "call_0002", "call_0003"]
    assert adapters.openai_input_problems(items) == []


# -- 3. images ----------------------------------------------------------------------------------


@pytest.mark.parametrize("images", [None, "output", "message"])
@pytest.mark.parametrize("detail", [None, "low", "high", "original"])
def test_both_image_placements_give_input_the_checker_accepts(toolbox, images, detail):
    session = _session(toolbox)
    client = SimpleNamespace(responses=StubResponses(_turns(toolbox.definitions("openai-responses"))))
    items, _ = _run(toolbox, session, client, images=images, detail=detail)
    for request in client.responses.requests:
        assert adapters.openai_input_problems(request["input"]) == []
    json.dumps(items)                             # what the client serialises

    (render,) = [i for i in items if i.get("call_id") == "call_0002"
                 and i.get("type") == "function_call_output"]
    if images == "message":
        assert isinstance(render["output"], str) and "1 image(s)" in render["output"]
        (message,) = [i for i in items if i.get("role") == "user" and isinstance(
            i.get("content"), list)]
        # The user message comes after every output of its turn.
        assert items.index(message) > max(
            items.index(i) for i in items if i.get("type") == "function_call_output")
        parts = message["content"]
        assert parts[0] == {"type": "input_text", "text": "Images from tool call call_0002:"}
        image = parts[1]
    else:
        assert [p["type"] for p in render["output"]] == ["input_text", "input_image"]
        image = render["output"][1]
    assert image["type"] == "input_image"
    assert image["image_url"].startswith("data:image/png;base64,")
    assert image["detail"] == (detail or "auto")


def test_the_checker_takes_sdk_objects_and_still_finds_a_bad_detail():
    class Model:
        def __init__(self, **fields):
            self.fields = fields

        def model_dump(self, exclude_none=False):
            return {k: v for k, v in self.fields.items() if not (exclude_none and v is None)}

    good = [Model(type="reasoning", id="rs_1", summary=[], encrypted_content="x", content=None),
            Model(type="function_call", call_id="c", name="n", arguments="{}", namespace="g")]
    assert adapters.openai_input_problems(good) == []
    bad = {"type": "function_call_output", "call_id": "c", "output": [
        {"type": "input_image", "image_url": "data:image/png;base64,AA==", "detail": "medium"}]}
    assert len(adapters.openai_input_problems(good + [bad])) == 1


# -- 4. store=False: reasoning goes back as it came -------------------------------------------


def test_reasoning_items_with_encrypted_content_go_back_unchanged(toolbox):
    session = _session(toolbox)
    turns = _turns(toolbox.definitions("openai-responses"))
    sent = copy.deepcopy(turns)
    client = SimpleNamespace(responses=StubResponses(turns))
    _run(toolbox, session, client, images="message", detail="high")

    requests = client.responses.requests
    assert len(requests) == 3
    assert all(r["store"] is False and r["include"] == INCLUDE for r in requests)
    for n, request in enumerate(requests):
        reasoning = [i for i in request["input"] if i.get("type") == "reasoning"]
        assert reasoning == [sent[t][0] for t in range(n)]   # every earlier one, as sent
        assert all(set(i) == {"type", "id", "summary", "encrypted_content"} for i in reasoning)


# -- 5. the whole loop --------------------------------------------------------------------------


def test_a_three_turn_loop_runs_real_dispatch_against_the_stub(toolbox):
    session = _session(toolbox)
    tools = toolbox.definitions("openai-responses")
    turns = _turns(tools)
    sent = copy.deepcopy(turns)
    client = SimpleNamespace(responses=StubResponses(turns))
    items, dispatched = _run(toolbox, session, client)

    requests = client.responses.requests
    assert [len(r["input"]) for r in requests] == [1, 1 + 2 + 1, 4 + 5 + 2]
    assert all(r["tools"] == tools for r in requests)          # the cached list, unchanged
    assert any(t == {"type": "tool_search"} for t in tools)
    # Each request is the previous one, the previous output as it came, and our answers.
    for before, after, output in zip(requests, requests[1:], sent):
        assert after["input"][:len(before["input"])] == before["input"]
        assert after["input"][len(before["input"]):][:len(output)] == output
    # The edits happened: the title is set, and the deferred lookup found it.
    answers = {i["call_id"]: i for i in items if i.get("type") == "function_call_output"}
    assert json.loads(answers["call_0001"]["output"])["ok"] is True
    found = json.loads(answers["call_0003"]["output"])
    assert found["ok"] is True and found["data"] == {"page": 1}, found
    assert items[-1] == sent[2][1] and dispatched[-1] == "toy_ppt_find_label"
