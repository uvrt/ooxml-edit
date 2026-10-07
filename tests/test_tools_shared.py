"""The shared tools (S1-S13), refs, central checks and the generic batch, on toy formats."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json

import pytest

from ooxml_edit.tools import (Limits, Result, SubsetError, Tool, ToolError, Toolbox, array, free_object,
                              check_subset, obj, shared, string, tool)
from ooxml_edit.tools.adapters import anthropic_problems, openai_problems
from ooxml_edit.tools.registry import merge_tools
from ooxml_edit.xml import qn

import synthetic
import tools_toys

CLOCK = lambda: dt.datetime(2026, 10, 7, 12, 0, tzinfo=dt.timezone.utc)  # noqa: E731

# -- a toy format with refs and checks -----------------------------------------------------------

CHECKS: list[tuple[str, list]] = []


def _toy_checks(entry, touched):
    CHECKS.append((entry.doc_id, list(touched)))
    return {"touched": list(touched), "items": len(entry.document.items())}


TEXT = dataclasses.replace(tools_toys.TEXT, checks=_toy_checks,
                           summary=lambda document: {"items": len(document.items())})
DECK = dataclasses.replace(tools_toys.DECK, checks=_toy_checks)


def _set_item(document, position: int, text: str) -> None:
    root = document.package.tree(synthetic.MAIN)
    items = list(root.iter(qn("tst:item")))
    if not 1 <= position <= len(items):
        raise KeyError(f"no item {position}; there are {len(items)}")
    document.history.checkpoint()
    items[position - 1].set("val", text)
    document.package.mark_dirty(synthetic.MAIN)


@tool("t_add", "Add items at the end; each may carry a ref.",
      {"doc": string("Document id."),
       "items": array(obj({"text": string("Item text."),
                           "ref": string("A name for the item.", optional=True)},
                          "One item."), "Items to add.", min_items=1)},
      kind="docx", mutates=True)
def t_add(call, doc, items):
    made = []
    for item in items:
        position = call.document.insert(item["text"], "end")
        address = f"item:{position}"
        if "ref" in item:
            call.define_ref(item["ref"], address)
        call.touch(address)
        made.append(address)
    return Result(summary=f"Added {len(made)} item(s)", created=made)


@tool("t_set", "Set an item's text.",
      {"doc": string("Document id."), "target": string("item:N or $ref."),
       "text": string("New text.")},
      kind="docx", mutates=True, refs=("target",))
def t_set(call, doc, target, text):
    if not target.startswith("item:"):
        raise ToolError("not_found", f"no item {target!r}", field="target")
    _set_item(call.document, int(target[5:]), text)
    call.touch(target)
    return Result(summary=f"Set {target}", changed=[target])


@tool("t_fail", "Always refuses.", {"doc": string("Document id.")}, kind="docx", mutates=True)
def t_fail(call, doc):
    raise ToolError("refused", "not this way", valid_options=["a", "b"])


@tool("t_read", "Read the items.", {"doc": string("Document id.")}, kind="docx")
def t_read(call, doc):
    return call.document.items()


@shared.handler("new_document", kind="docx")
def new_text(call, kind, template_blob=None, size=None, title=None, author=None, name=None):
    document = tools_toys.ToyDocument(synthetic.outer_package())
    doc_id = call.session.adopt(document, "docx", name or "new.docx")
    return Result(summary=f"Made {doc_id}", created=[doc_id], data={"doc": doc_id})


@shared.handler("save_document", kind="docx")
def save_text(call, doc, name, format):
    return Result(summary=f"Saved {name}", data=call.output(name, format, call.document.to_bytes()))


@shared.handler("save_document", kind="pptx")
def save_deck(call, doc, name, format):
    return Result(summary=f"Saved {name}", data=call.output(name, format, call.document.to_bytes()))


TEXT_TOOLS = [t_add, t_set, t_fail, t_read, new_text, save_text, *shared.SESSION_TOOLS]
DECK_TOOLS = [tools_toys.toy_ppt_set_title, save_deck, *shared.SESSION_TOOLS]


@pytest.fixture
def toolbox():
    CHECKS.clear()
    with Toolbox(TEXT_TOOLS + DECK_TOOLS, formats=[DECK, TEXT], groups=shared.GROUPS) as box:
        yield box


@pytest.fixture
def session(toolbox):
    session = toolbox.session(clock=CLOCK)
    session.open(synthetic.outer_package(), "text.docx")      # d1
    session.open(synthetic.outer_package(), "deck.pptx")      # d2
    return session


def call(toolbox, session, tool_name, **arguments):
    return toolbox.dispatch(session, tool_name, arguments)


# -- definitions ---------------------------------------------------------------------------------


def test_every_shared_definition_is_in_the_subset_and_batch_alone_is_not_strict():
    for name, spec in shared.SPECS.items():
        definition = shared.definition(name)
        if name == "batch":
            assert not definition.strict and definition.composite
            with pytest.raises(SubsetError):
                check_subset(definition.canonical)
        else:
            assert definition.strict
            check_subset(definition.canonical)
    assert set(shared.SPECS) >= {
        "open_document", "new_document", "save_document", "list_documents", "close_document",
        "undo", "find_text", "replace_text", "render", "check", "edit_chart", "read_chart",
        "edit_smartart", "set_properties", "batch"}
    assert shared.CORE_NAMES == ["open_document", "new_document", "save_document", "undo",
                                 "find_text", "replace_text", "render", "check", "batch"]


def test_a_free_form_object_is_refused_in_a_strict_tool():
    with pytest.raises(SubsetError, match="additionalProperties"):
        tool("t", "A tool.", {"x": free_object("Anything.")})(lambda call, x: None)


def test_two_kinds_merge_into_one_tool_and_the_session_tools_are_kept_once(toolbox):
    assert set(toolbox.tools["save_document"].handlers) == {"docx", "pptx"}
    assert toolbox.tools["undo"].handlers == shared.SESSION_TOOLS[3].handlers
    names = [t.name for t in toolbox.tools.values()]
    assert len(names) == len(set(names))


def test_a_handler_changes_no_definition_and_an_unknown_name_is_refused():
    with pytest.raises(KeyError):
        shared.handler("no_such_tool", kind="pptx")
    with pytest.raises(ValueError):
        shared.handler("render", kind="xlsx")
    altered = Tool(name="render", description="Something else.",
                   schema=shared.SPECS["render"].schema, handlers={"docx": lambda c: None})
    with pytest.raises(ValueError, match="two different tools"):
        merge_tools([shared.definition("render"), altered])


def test_definitions_meet_each_providers_rules_with_batch_sent_non_strict(toolbox):
    anthropic = toolbox.definitions("anthropic")
    assert anthropic_problems(anthropic) == []
    batch = next(d for d in anthropic if d["name"] == "batch")
    assert batch["strict"] is False
    assert batch["input_schema"]["properties"]["ops"]["items"]["properties"]["arguments"][
        "additionalProperties"] is True
    responses = toolbox.definitions("openai-responses")
    assert openai_problems(responses) == []
    batch = next(d for d in responses if d["name"] == "batch")
    assert batch["strict"] is False and batch["parameters"]["required"] == ["ops"]
    assert all(d["strict"] is True for d in responses if d.get("name") != "batch")
    chat = toolbox.definitions("openai-chat")
    assert openai_problems(chat, chat=True) == []


# -- the session tools ---------------------------------------------------------------------------


def test_new_document_runs_the_handler_of_the_kind_it_names(toolbox, session):
    result = call(toolbox, session, "new_document", kind="docx", name="notes.docx")
    assert result.ok and result.created == ["d3"]
    assert session.entry("d3").name == "notes.docx"
    missing = call(toolbox, session, "new_document", kind="pptx")
    assert not missing.ok and missing.error.code == "invalid_arguments"
    assert missing.error.field == "kind" and missing.error.valid_options == ["docx"]


def test_open_document_opens_a_blob_with_the_formats_summary(toolbox, session):
    handle = session.add_blob(synthetic.outer_package(), "more.docx")
    result = call(toolbox, session, "open_document", blob=handle)
    assert result.ok and result.data["doc"] == "d3" and result.data["kind"] == "docx"
    assert result.data["summary"] == {"items": 0}
    unknown = call(toolbox, session, "open_document", blob="b9")
    assert unknown.error.code == "not_found"


def test_list_close_and_undo(toolbox, session):
    listed = call(toolbox, session, "list_documents")
    assert [d["doc"] for d in listed.data["documents"]] == ["d1", "d2"]
    call(toolbox, session, "t_add", doc="d1", items=[{"text": "a"}])
    undone = call(toolbox, session, "undo", doc="d1")
    assert undone.ok and undone.data["steps"] == 1 and session.entry("d1").document.items() == []
    redone = call(toolbox, session, "undo", doc="d1", redo=True)
    assert redone.ok and session.entry("d1").document.items() == ["a"]
    nothing = call(toolbox, session, "undo", doc="d2")
    assert nothing.error.code == "refused"
    assert call(toolbox, session, "close_document", doc="d2").ok
    assert list(session.documents) == ["d1"]


# -- refs and checks -----------------------------------------------------------------------------


def test_a_ref_names_an_object_for_later_calls_and_is_listed(toolbox, session):
    added = call(toolbox, session, "t_add", doc="d1", items=[{"text": "a", "ref": "first"},
                                                             {"text": "b"}])
    assert added.refs == {"first": "item:1"}
    assert json.loads(added.to_text())["refs"] == {"first": "item:1"}
    assert call(toolbox, session, "t_set", doc="d1", target="$first", text="A").ok
    assert session.entry("d1").document.items() == ["A", "b"]
    unknown = call(toolbox, session, "t_set", doc="d1", target="$second", text="B")
    assert unknown.error.code == "not_found" and unknown.error.valid_options == ["$first"]
    assert unknown.error.field == "target"


def test_a_bad_ref_name_rolls_the_call_back(toolbox, session):
    result = call(toolbox, session, "t_add", doc="d1", items=[{"text": "a", "ref": "Bad-Name"}])
    assert result.error.code == "invalid_arguments"
    assert session.entry("d1").document.items() == [] and session.entry("d1").refs == {}


def test_checks_run_once_after_a_changing_call_with_what_it_touched(toolbox, session):
    result = call(toolbox, session, "t_add", doc="d1", items=[{"text": "a"}, {"text": "b"}])
    assert result.checks == {"touched": ["item:1", "item:2"], "items": 2}
    assert CHECKS == [("d1", ["item:1", "item:2"])]
    call(toolbox, session, "t_read", doc="d1")
    assert len(CHECKS) == 1


# -- batch ---------------------------------------------------------------------------------------


def _ops(*pairs):
    return [{"tool": name, "arguments": arguments} for name, arguments in pairs]


def test_a_batch_runs_its_ops_in_order_as_one_undo_step_with_refs(toolbox, session):
    entry = session.entry("d1")
    before = entry.version
    ops = _ops(("t_add", {"doc": "d1", "items": [{"text": "a", "ref": "x"}]}),
               ("t_add", {"doc": "d1", "items": [{"text": "b", "ref": "y"}]}),
               ("t_set", {"doc": "d1", "target": "$x", "text": "A"}),
               ("t_read", {"doc": "d1"}))
    result = call(toolbox, session, "batch", ops=ops)
    assert result.ok, result.to_json()
    assert entry.document.items() == ["A", "b"]
    assert entry.version == before + 1
    assert result.refs == {"x": "item:1", "y": "item:2"} and entry.refs == result.refs
    assert [op["tool"] for op in result.data["ops"]] == ["t_add", "t_add", "t_set", "t_read"]
    assert result.data["ops"][3]["data"] == ["A", "b"]
    assert CHECKS == [("d1", ["item:1", "item:2"])]
    assert result.checks["items"] == 2
    assert call(toolbox, session, "undo", doc="d1").ok
    assert entry.document.items() == []


def test_a_failing_op_rolls_the_whole_batch_back_and_names_itself(toolbox, session):
    entry = session.entry("d1")
    ops = _ops(("t_add", {"doc": "d1", "items": [{"text": "a", "ref": "x"}]}),
               ("t_fail", {"doc": "d1"}))
    result = call(toolbox, session, "batch", ops=ops)
    assert not result.ok and result.error.code == "refused"
    assert result.error.details["op"] == 1 and result.error.details["tool"] == "t_fail"
    assert result.error.valid_options == ["a", "b"]
    assert "ops[1] (t_fail)" in result.error.message
    assert entry.document.items() == [] and entry.refs == {} and entry.version == 0
    assert CHECKS == []


def test_an_ops_arguments_are_validated_before_anything_runs(toolbox, session):
    ops = _ops(("t_add", {"doc": "d1", "items": [{"text": "a"}]}),
               ("t_set", {"doc": "d1", "target": "item:1"}))
    result = call(toolbox, session, "batch", ops=ops)
    assert result.error.code == "invalid_arguments"
    assert result.error.field == "ops[1].arguments.text"
    assert session.entry("d1").document.items() == []


@pytest.mark.parametrize("name", ["batch", "save_document", "undo", "new_document"])
def test_some_tools_cannot_run_inside_a_batch(toolbox, session, name):
    result = call(toolbox, session, "batch", ops=_ops((name, {"doc": "d1"})))
    assert result.error.code == "invalid_arguments" and result.error.field == "ops[0].tool"


def test_an_unknown_op_tool_lists_near_names(toolbox, session):
    result = call(toolbox, session, "batch", ops=_ops(("t_ad", {"doc": "d1"})))
    assert result.error.code == "invalid_arguments" and "t_add" in result.error.valid_options


def test_a_batch_has_a_size_limit_and_a_deadline(toolbox):
    session = toolbox.session(clock=CLOCK, limits=Limits(max_batch_ops=2, batch_timeout=0.0))
    session.open(synthetic.outer_package(), "text.docx")
    ops = _ops(*[("t_read", {"doc": "d1"})] * 3)
    assert call(toolbox, session, "batch", ops=ops).error.code == "limit"
    slow = call(toolbox, session, "batch", ops=_ops(("t_add", {"doc": "d1",
                                                                "items": [{"text": "a"}]}),
                                                     ("t_read", {"doc": "d1"})))
    assert slow.error.code == "timeout" and session.entry("d1").document.items() == []


def test_a_batch_over_two_documents_is_one_step_in_each(toolbox, session):
    ops = _ops(("t_add", {"doc": "d1", "items": [{"text": "a"}]}),
               ("toy_ppt_set_title", {"doc": "d2", "page": 1, "text": "Q3"}),
               ("t_add", {"doc": "d1", "items": [{"text": "b"}]}))
    result = call(toolbox, session, "batch", ops=ops)
    assert result.ok, result.to_json()
    assert set(result.checks) == {"d1", "d2"}
    assert session.entry("d1").version == 1 and session.entry("d2").version == 1
    assert session.entry("d1").document.items() == ["a", "b"]
    assert session.entry("d2").document.title(1) == "Q3"


def test_a_batch_as_openai_sends_it_a_json_string_with_nulls(toolbox, session):
    arguments = json.dumps({"ops": [{"tool": "t_add", "arguments": {
        "doc": "d1", "items": [{"text": "a", "ref": None}]}}]})
    result = toolbox.dispatch(session, "batch", arguments)
    assert result.ok and session.entry("d1").document.items() == ["a"] and result.refs == {}


def test_parallel_batches_on_one_document_run_in_order(toolbox, session):
    first = ("batch", {"ops": _ops(("t_add", {"doc": "d1", "items": [{"text": "a"}]}))})
    second = ("batch", {"ops": _ops(("t_add", {"doc": "d1", "items": [{"text": "b"}]}))})
    results = toolbox.dispatch_many(session, [first, second])
    assert all(r.ok for r in results)
    assert session.entry("d1").document.items() == ["a", "b"]


def test_undo_takes_back_the_refs_of_what_it_undoes(toolbox, session):
    entry = session.entry("d1")
    call(toolbox, session, "t_add", doc="d1", items=[{"text": "a", "ref": "x"}])
    ops = _ops(("t_add", {"doc": "d1", "items": [{"text": "b", "ref": "y"}]}))
    call(toolbox, session, "batch", ops=ops)
    assert entry.refs == {"x": "item:1", "y": "item:2"}
    call(toolbox, session, "undo", doc="d1")
    assert entry.refs == {"x": "item:1"}
    call(toolbox, session, "undo", doc="d1")
    assert entry.refs == {}
    call(toolbox, session, "undo", doc="d1", redo=True, steps=2)
    assert entry.refs == {"x": "item:1", "y": "item:2"}
    call(toolbox, session, "undo", doc="d1")
    call(toolbox, session, "t_add", doc="d1", items=[{"text": "c"}])
    assert entry.refs == {"x": "item:1"}
    stale = call(toolbox, session, "t_set", doc="d1", target="$y", text="B")
    assert stale.error.code == "not_found"
