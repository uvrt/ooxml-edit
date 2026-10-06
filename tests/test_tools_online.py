"""Online checks against the Anthropic Messages API (marked ``provider``).

Skipped unless ``ANTHROPIC_API_KEY`` is set; they spend a few thousand tokens.  Run with
``python -m pytest -m provider``.  ``ANTHROPIC_MODEL`` picks the model (default
``claude-sonnet-5``, the trial model).  No SDK: the request is a plain HTTPS POST, which is
also a check that the adapters' dicts are all the API needs.  The OpenAI equivalent is
written when a GPT-6 trial is approved; until then nothing here calls OpenAI.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

from ooxml_edit.tools import Toolbox
from ooxml_edit.tools.adapters import anthropic_problems

import synthetic
import tools_toys

pytestmark = [
    pytest.mark.provider,
    pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"),
                       reason="ANTHROPIC_API_KEY is not set"),
]

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
API = "https://api.anthropic.com/v1"


def _post(path: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{API}/{path}", data=json.dumps(body).encode(), method="POST",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        pytest.fail(f"{error.code}: {error.read().decode(errors='replace')}")


@pytest.fixture(scope="module")
def toolbox():
    with Toolbox(tools_toys.TOOLS, formats=tools_toys.FORMATS, groups=tools_toys.GROUPS) as box:
        yield box


def test_all_definitions_are_accepted_with_strict_as_the_adapter_sets_it(toolbox):
    tools = toolbox.definitions("anthropic")
    assert anthropic_problems(tools) == []
    reply = _post("messages", {"model": MODEL, "max_tokens": 32, "tools": tools,
                               "tool_choice": {"type": "auto"},
                               "messages": [{"role": "user", "content": "Reply with: ready"}]})
    assert reply["type"] == "message"
    counted = _post("messages/count_tokens", {"model": MODEL, "tools": tools, "messages": [
        {"role": "user", "content": "Reply with: ready"}]})
    print(f"\n{MODEL}: {len(tools)} tool definitions, {counted['input_tokens']} input tokens")


def test_an_image_result_round_trips_in_a_tool_result(toolbox):
    session = toolbox.session()
    session.open(synthetic.outer_package(), "deck.pptx")
    result = toolbox.dispatch(session, "toy_ppt_render", {"doc": "d1", "page": 1, "width": 256})
    assert result.ok and result.images
    arguments = {"doc": "d1", "page": 1, "width": 256}
    messages = [
        {"role": "user", "content": "Render page 1 of d1, then tell me its main colour in one word."},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_t0_render",
                                           "name": "toy_ppt_render", "input": arguments}]},
        toolbox.render_results("anthropic", [("toolu_t0_render", result)]),
    ]
    reply = _post("messages", {"model": MODEL, "max_tokens": 64,
                               "tools": toolbox.definitions("anthropic"), "messages": messages})
    text = " ".join(block.get("text", "") for block in reply["content"]).lower()
    assert reply["stop_reason"] in ("end_turn", "max_tokens") and "blue" in text
