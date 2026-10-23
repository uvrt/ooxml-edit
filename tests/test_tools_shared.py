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


@tool("t_half", "Adds an item with a ref, then fails.",
      {"doc": string("Document id."), "ref": string("A name.", optional=True)},
      kind="docx", mutates=True)
def t_half(call, doc, ref=None):
    position = call.document.insert("half", "end")
    if ref:
        call.define_ref(ref, f"item:{position}")
    call.touch(f"item:{position}")
    raise ValueError("gave up half way")


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


@tool("t_title", "Set a page's title, naming the page.",
      {"doc": string("Document id."), "page": string("Page number."),
       "text": string("Title."), "ref": string("A name for the page.", optional=True)},
      kind="pptx", mutates=True)
def t_title(call, doc, page, text, ref=None):
    call.document.set_title(int(page), text)
    if ref:
        call.define_ref(ref, f"page:{page}")
    return Result(summary=f"Titled page {page}")


TEXT_TOOLS = [t_add, t_set, t_fail, t_half, t_read, new_text, save_text, *shared.SESSION_TOOLS]
DECK_TOOLS = [tools_toys.toy_ppt_set_title, t_title, save_deck, *shared.SESSION_TOOLS]


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
        "open_document", "new_document", "save_document", "close_document",
        "undo", "describe", "find_text", "replace_text", "render", "check", "edit_chart",
        "edit_smartart", "set_properties", "batch"}
    assert "read_chart" not in shared.SPECS          # edit_chart's read action (T5b)
    assert "list_documents" not in shared.SPECS      # the application names them (post-T4)
    assert shared.CORE_NAMES == ["open_document", "new_document", "save_document", "undo",
                                 "describe", "find_text", "replace_text", "render", "check",
                                 "batch"]


def test_edit_chart_reads_without_changing():
    chart = shared.definition("edit_chart")
    assert chart.canonical["properties"]["action"]["enum"][0] == "read"
    assert chart.mutates and not chart.changes({"action": "read"})
    assert chart.changes({"action": "set_title"})


def test_a_free_form_object_is_refused_in_a_strict_tool():
    with pytest.raises(SubsetError, match="additionalProperties"):
        tool("t", "A tool.", {"x": free_object("Anything.")})(lambda call, x: None)


def test_two_kinds_merge_into_one_tool_and_the_session_tools_are_kept_once(toolbox):
    assert set(toolbox.tools["save_document"].handlers) == {"docx", "pptx"}
    undo = next(t for t in shared.SESSION_TOOLS if t.name == "undo")
    assert toolbox.tools["undo"].handlers == undo.handlers
    names = [t.name for t in toolbox.tools.values()]
    assert len(names) == len(set(names))


def test_a_handler_changes_no_definition_and_an_unknown_name_is_refused():
    with pytest.raises(KeyError):
        shared.handler("no_such_tool", kind="pptx")
    with pytest.raises(ValueError):
        shared.handler("render", kind="odt")
    altered = Tool(name="render", description="Something else.",
                   schema=shared.SPECS["render"].schema, handlers={"docx": lambda c: None})
    with pytest.raises(ValueError, match="two different tools"):
        merge_tools([shared.definition("render"), altered])


def test_workbooks_are_a_kind_and_their_formats_are_in_the_definitions():
    assert shared.KINDS == ("pptx", "docx", "xlsx")
    assert shared.handler("describe", kind="xlsx")(lambda call, doc: None).kinds == ["xlsx"]
    kind = shared.SPECS["new_document"].schema["properties"]["kind"]
    assert kind["enum"] == ["pptx", "docx", "xlsx"] and "xlsx: a workbook" in kind["description"]
    save = shared.SPECS["save_document"].schema["properties"]["format"]
    assert {"xlsx", "xlsm", "xltx"} <= set(save["enum"]) and "xltx" in save["description"]
    assert "Workbooks:" in shared.SPECS["describe"].description
    find = shared.SPECS["find_text"]
    assert "workbook" in find.description
    assert "Workbooks: a sheet, range or table" in find.schema["properties"]["range"]["description"]
    # replace_text's range stays the documents' alone
    replace = shared.SPECS["replace_text"].schema["properties"]["range"]["description"]
    assert "Workbooks" not in replace


def test_a_toolbox_without_a_workbook_library_refuses_workbook_calls(toolbox, session):
    """The enums are static (one definition whichever libraries are loaded), so a toolbox
    without a kind's library answers a call for it as before for a kind it lacks."""
    made = call(toolbox, session, "new_document", kind="xlsx")
    assert not made.ok and made.error.code == "invalid_arguments"
    assert made.error.field == "kind" and made.error.valid_options == ["docx"]
    assert list(session.documents) == ["d1", "d2"]
    # a deck's or document's library is asked the same as before, and the toolbox's shared
    # definitions are the same objects whichever kinds it serves
    assert toolbox.tools["new_document"].schema == shared.SPECS["new_document"].schema


def test_definitions_meet_each_providers_rules_with_batch_sent_non_strict(toolbox):
    anthropic = toolbox.definitions("anthropic", defer=False)
    assert anthropic_problems(anthropic) == []
    batch = next(d for d in anthropic if d["name"] == "batch")
    assert batch["strict"] is False
    assert batch["input_schema"]["properties"]["ops"]["items"]["properties"]["arguments"][
        "additionalProperties"] is True
    responses = toolbox.definitions("openai-responses", defer=False)
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


def test_a_made_document_is_the_results_doc(toolbox, session):
    """Round 16, F10: new_document and open_document name no document, so the result's
    doc is the one they made -- not None, with the id only in the summary."""
    made = call(toolbox, session, "new_document", kind="docx")
    assert made.doc == made.created[0] and made.to_json()["doc"] == made.doc
    assert made.version == session.entry(made.doc).version
    opened = call(toolbox, session, "open_document", blob=session.add_blob(synthetic.outer_package(), "x.docx"))
    assert opened.doc == opened.created[0]


def test_clean_reaches_only_a_handler_that_takes_it(toolbox, session):
    """Round 16, F11: save_document's clean goes to a kind's handler that takes it; one
    that does not refuses clean: true, naming the field, and never sees clean: false."""
    plain = call(toolbox, session, "save_document", doc="d1", name="a.docx", format="docx", clean=False)
    assert plain.ok
    refused = call(toolbox, session, "save_document", doc="d1", name="a.docx", format="docx", clean=True)
    assert not refused.ok and refused.error.code == "invalid_arguments" and refused.error.field == "clean"


def test_open_document_opens_a_blob_with_the_formats_summary(toolbox, session):
    handle = session.add_blob(synthetic.outer_package(), "more.docx")
    result = call(toolbox, session, "open_document", blob=handle)
    assert result.ok and result.data["doc"] == "d3" and result.data["kind"] == "docx"
    assert result.data["summary"] == {"items": 0}
    unknown = call(toolbox, session, "open_document", blob="b9")
    assert unknown.error.code == "not_found"


@pytest.mark.parametrize("name, data, mime", [
    ("figures.csv", b"region,q1\nNorth,12\n", "text/csv"),
    ("draft.md", "# Draft \u2014 notes\n".encode(), "text/markdown"),
    ("outline.txt", b"Title\n- point\n", None)])
def test_open_document_on_a_text_blob_points_to_read_blob(toolbox, session, name, data, mime):
    handle = session.add_blob(data, name, mime=mime)
    result = call(toolbox, session, "open_document", blob=handle)
    assert result.error.code == "invalid_arguments" and result.error.field == "blob"
    assert result.error.valid_options == ["read_blob"] and "read_blob" in result.error.message
    assert "read_blob" in toolbox.system_prompt()


def test_close_and_undo(toolbox, session):
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
    result = call(toolbox, session, "t_add", doc="d1", items=[{"text": "a", "ref": "Bad Name"}])
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
    assert set(result.data) == {"ops"}          # as an all-or-none batch's: no failures to list
    assert result.data["ops"][3]["data"] == ["A", "b"]
    assert CHECKS == [("d1", ["item:1", "item:2"])]
    assert result.checks["items"] == 2
    assert call(toolbox, session, "undo", doc="d1").ok
    assert entry.document.items() == []


def test_an_atomic_batch_rolls_back_whole_when_an_op_fails_and_names_it(toolbox, session):
    entry = session.entry("d1")
    ops = _ops(("t_add", {"doc": "d1", "items": [{"text": "a", "ref": "x"}]}),
               ("t_fail", {"doc": "d1"}))
    result = call(toolbox, session, "batch", ops=ops, atomic=True)
    assert not result.ok and result.error.code == "refused"
    assert result.error.details["op"] == 1 and result.error.details["tool"] == "t_fail"
    assert result.error.valid_options == ["a", "b"]
    assert result.error.message == ("ops[1] (t_fail) failed, so nothing in the batch was "
                                    "applied: not this way")
    assert result.error.details["completed_before"] == 1
    assert entry.document.items() == [] and entry.refs == {} and entry.version == 0
    assert CHECKS == []


def test_a_failing_op_is_skipped_and_the_others_apply_as_one_step(toolbox, session):
    # T1: by default one bad op no longer voids the batch; the model resends only it.
    entry = session.entry("d1")
    ops = _ops(("t_add", {"doc": "d1", "items": [{"text": "a", "ref": "x"}]}),
               ("t_fail", {"doc": "d1"}),
               ("t_add", {"doc": "d1", "items": [{"text": "b", "ref": "y"}]}),
               ("t_read", {"doc": "d1"}))
    result = call(toolbox, session, "batch", ops=ops)
    assert result.ok, result.to_json()
    assert entry.document.items() == ["a", "b"] and entry.version == 1
    assert result.refs == entry.refs == {"x": "item:1", "y": "item:2"}
    assert result.data["applied"] == 3 and result.data["failed"] == [1]
    assert [op["op"] for op in result.data["ops"]] == [0, 1, 2, 3]
    assert result.data["ops"][1] == {"op": 1, "tool": "t_fail", "error": {
        "code": "refused", "message": "not this way", "valid_options": ["a", "b"]}}
    assert result.data["ops"][3]["data"] == ["a", "b"]
    assert result.summary == ("Ran 3 of 4 ops as one step: Added 1 item(s); Added 1 item(s). "
                              "Not applied -- fix and resend only these: ops[1] (t_fail): "
                              "not this way")
    assert CHECKS == [("d1", ["item:1", "item:2"])]
    assert call(toolbox, session, "undo", doc="d1").ok      # one step takes back all of it
    assert entry.document.items() == [] and entry.refs == {} and entry.version == 0


def test_an_op_that_fails_half_way_is_rolled_back_alone(toolbox, session):
    entry = session.entry("d1")
    ops = _ops(("t_add", {"doc": "d1", "items": [{"text": "a"}]}),
               ("t_half", {"doc": "d1", "ref": "h"}),
               ("t_add", {"doc": "d1", "items": [{"text": "b"}]}))
    result = call(toolbox, session, "batch", ops=ops)
    assert result.ok and result.data["failed"] == [1]
    assert result.data["ops"][1]["error"] == {"code": "unit", "message": "gave up half way"}
    assert entry.document.items() == ["a", "b"]           # "half" went with its op
    assert entry.refs == {} and result.refs == {}
    assert CHECKS == [("d1", ["item:1", "item:2"])]       # what it touched is forgotten


def test_a_ref_a_failed_op_would_have_made_names_that_op(toolbox, session):
    ops = _ops(("t_half", {"doc": "d1", "ref": "chart1"}),
               ("t_set", {"doc": "d1", "target": "$chart1", "text": "A"}),
               ("t_add", {"doc": "d1", "items": [{"text": "b"}]}))
    result = call(toolbox, session, "batch", ops=ops)
    assert result.ok and result.data["failed"] == [0, 1]
    assert result.data["ops"][1]["error"] == {
        "code": "not_found", "field": "arguments.target", "details": {"op": 0},
        "message": "$chart1 was to be made by ops[0], which failed: fix that op and resend "
                   "both"}
    # A ref in a schema-invalid op counts too; a ref nobody asked for is the usual error.
    ops = _ops(("t_add", {"doc": "d1", "items": [{"text": 5, "ref": "z"}]}),
               ("t_set", {"doc": "d1", "target": "$z", "text": "A"}),
               ("t_set", {"doc": "d1", "target": "$nobody", "text": "A"}))
    result = call(toolbox, session, "batch", ops=ops)
    assert not result.ok and result.error.details["failed"] == [0, 1, 2]
    errors = [op["error"] for op in result.error.details["ops"]]
    assert errors[1]["message"].startswith("$z was to be made by ops[0]")
    assert errors[2]["message"] == "no ref $nobody in d1"


def test_a_batch_in_which_no_op_applies_is_an_error_and_no_step(toolbox, session):
    entry = session.entry("d1")
    ops = _ops(("t_fail", {"doc": "d1"}),
               ("t_half", {"doc": "d1"}),
               ("t_nope", {"doc": "d1"}))
    result = call(toolbox, session, "batch", ops=ops)
    assert not result.ok and result.error.code == "refused"
    assert result.error.field == "ops[0]" and result.error.valid_options == ["a", "b"]
    assert result.error.message.startswith("none of the 3 ops applied: ops[0] (t_fail): not "
                                           "this way; ops[1] (t_half): gave up half way; "
                                           "ops[2] (t_nope): no tool 't_nope'")
    assert result.error.details["applied"] == 0
    assert [op["error"]["code"] for op in result.error.details["ops"]] == [
        "refused", "unit", "invalid_arguments"]
    assert entry.document.items() == [] and entry.version == 0 and CHECKS == []
    assert not entry.history.can_undo()
    one = call(toolbox, session, "batch", ops=_ops(("t_fail", {"doc": "d1"})))
    assert one.error.message == "ops[0] (t_fail) failed, so nothing was applied: not this way"


def test_the_format_is_told_when_a_failed_op_is_rolled_back(toolbox, session):
    tools_toys.RESTORED.clear()
    entry = session.entry("d2")
    ops = _ops(("toy_ppt_set_title", {"doc": "d2", "page": 1, "text": "Q3"}),
               ("toy_ppt_set_title", {"doc": "d2", "page": 9, "text": "Q4"}))
    result = call(toolbox, session, "batch", ops=ops)
    assert result.ok and result.data["failed"] == [1]
    assert result.data["ops"][1]["error"]["code"] == "not_found"
    assert tools_toys.RESTORED == [id(entry.document)]      # it drops what it caches
    assert entry.document.title(1) == "Q3" and entry.version == 1


def test_an_op_naming_a_document_that_is_not_open_fails_alone(toolbox, session):
    ops = _ops(("t_add", {"doc": "d9", "items": [{"text": "a"}]}),
               ("t_add", {"doc": "d1", "items": [{"text": "b"}]}))
    result = call(toolbox, session, "batch", ops=ops)
    assert result.ok and result.data["failed"] == [0]
    assert result.data["ops"][0]["error"]["field"] == "arguments.doc"
    assert result.data["ops"][0]["error"]["valid_options"] == ["d1", "d2"]
    assert session.entry("d1").document.items() == ["b"]
    refused = call(toolbox, session, "batch", ops=ops, atomic=True)
    assert refused.error.code == "not_found" and session.entry("d1").version == 1


def test_ops_written_as_a_json_string_are_parsed(toolbox, session):
    # Trial 3: the model sometimes sent batch's ops as a string holding the JSON array.
    ops = _ops(("t_add", {"doc": "d1", "items": [{"text": "a", "ref": "x"}]}),
               ("t_set", {"doc": "d1", "target": "$x", "text": "A"}))
    result = call(toolbox, session, "batch", ops="\n" + json.dumps(ops, indent=1))
    assert result.ok, result.to_json()
    assert session.entry("d1").document.items() == ["A"]
    # A line break written \v inside the string (no JSON escape) is read as U+000B; an
    # escaped backslash before a v is left alone.  (The fix round's p8 lost two turns to it.)
    items = '[{"text": "SteerCo 1:\\vsavings"}, {"text": "a\\\\v"}]'
    from ooxml_edit.tools.dispatch import _json_strings

    schema = toolbox.tool("t_add").schema
    assert _json_strings(schema, {"doc": "d1", "items": items})["items"] == [
        {"text": "SteerCo 1:\vsavings"}, {"text": "a\\v"}]
    # A string that is not a JSON array is still refused, naming the field.
    for wrong in ("t_add", '{"tool": "t_add"}', "[1, 2"):
        refused = call(toolbox, session, "batch", ops=wrong)
        assert refused.error.code == "invalid_arguments" and refused.error.field == "ops"


def test_an_atomic_batchs_ops_are_validated_before_anything_runs(toolbox, session):
    ops = _ops(("t_add", {"doc": "d1", "items": [{"text": "a"}]}),
               ("t_set", {"doc": "d1", "target": "item:1"}))
    result = call(toolbox, session, "batch", ops=ops, atomic=True)
    assert result.error.code == "invalid_arguments"
    assert result.error.field == "ops[1].arguments.text"
    assert result.error.message.startswith("ops[1] (t_set): ")
    assert session.entry("d1").document.items() == []


def test_a_schema_invalid_op_in_the_middle_no_longer_blocks_the_rest(toolbox, session):
    ops = _ops(("t_add", {"doc": "d1", "items": [{"text": "a"}]}),
               ("t_set", {"doc": "d1", "target": "item:1"}),
               ("t_set", {"doc": "d1", "target": "item:1", "text": "A"}))
    result = call(toolbox, session, "batch", ops=ops)
    assert result.ok and result.data["applied"] == 2 and result.data["failed"] == [1]
    error = result.data["ops"][1]["error"]
    assert error["code"] == "invalid_arguments" and error["field"] == "arguments.text"
    assert session.entry("d1").document.items() == ["A"]


@pytest.mark.parametrize("name", ["batch", "save_document", "undo", "new_document"])
def test_some_tools_cannot_run_inside_a_batch(toolbox, session, name):
    for atomic in (False, True):
        result = call(toolbox, session, "batch", ops=_ops((name, {"doc": "d1"})), atomic=atomic)
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
    assert [op["error"]["message"] for op in slow.error.details["ops"]] == [
        "not run: the batch passed its 0 s deadline"] * 2
    slow = call(toolbox, session, "batch", atomic=True,
                ops=_ops(("t_add", {"doc": "d1", "items": [{"text": "a"}]})))
    assert slow.error.code == "timeout" and "nothing in the batch" in slow.error.message


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


# -- undo by scope: one writer per page ------------------------------------------------------------


def _titles(session, doc="d2"):
    document = session.entry(doc).document
    return [document.title(n) for n in (1, 2)]


def test_a_scoped_undo_takes_back_one_pages_latest_change_only(toolbox, session):
    tools_toys.RESTORED.clear()
    entry = session.entry("d2")
    assert call(toolbox, session, "t_title", doc="d2", page="1", text="A", ref="one").ok
    assert call(toolbox, session, "t_title", doc="d2", page="2", text="B", ref="two").ok
    assert call(toolbox, session, "t_title", doc="d2", page="1", text="C").ok
    undone = call(toolbox, session, "undo", doc="d2", scope="2")
    assert undone.ok and undone.data == {"steps": 1, "version": entry.version, "scope": "2"}
    assert _titles(session) == ["C", None] and tools_toys.RESTORED == [id(entry.document)]
    assert entry.refs == {"one": "page:1"}                      # page 2's ref went with it
    nothing = call(toolbox, session, "undo", doc="d2", scope="2")
    assert nothing.error.code == "refused" and "nothing of 2" in nothing.error.message
    redone = call(toolbox, session, "undo", doc="d2", scope="2", redo=True)
    assert redone.ok and _titles(session) == ["C", "B"]
    assert entry.refs == {"one": "page:1", "two": "page:2"}
    # Without scope, undo is as it was: the latest change, whoever made it.
    assert call(toolbox, session, "undo", doc="d2").ok and _titles(session) == ["C", None]
    two = call(toolbox, session, "undo", doc="d2", scope="1", steps=5)
    assert two.ok and two.data["steps"] == 2 and _titles(session) == [None, None]
    assert two.warnings and entry.refs == {}


def test_a_scoped_undo_is_refused_when_entangled_and_where_there_are_no_scopes(toolbox, session):
    entry = session.entry("d2")
    with entry.batch():                       # a change to page 1 and the main part together
        entry.document.set_title(1, "A")
        entry.document.insert("x", "end")
    entry.document.insert("y", "end")         # another writer: the main part again
    before = entry.document.to_bytes()
    refused = call(toolbox, session, "undo", doc="d2", scope="1")
    assert refused.error.code == "entangled" and refused.error.field == "scope"
    assert refused.error.details["shared"] == [synthetic.MAIN]
    assert entry.document.to_bytes() == before
    unknown = call(toolbox, session, "undo", doc="d2", scope="9")
    assert unknown.error.code == "not_found" and unknown.error.valid_options == ["1", "2"]
    call(toolbox, session, "t_add", doc="d1", items=[{"text": "a"}])
    word = call(toolbox, session, "undo", doc="d1", scope="1")
    assert word.error.code == "invalid_arguments" and "document-wide" in word.error.message


def test_a_scoped_undo_refuses_a_change_to_what_every_page_shares():
    deck = dataclasses.replace(DECK, undo_shared=lambda document: [synthetic.MAIN])
    with Toolbox(TEXT_TOOLS + DECK_TOOLS, formats=[deck, TEXT], groups=shared.GROUPS) as box:
        session = box.session(clock=CLOCK)
        session.open(synthetic.outer_package(), "deck.pptx")      # d1
        entry = session.entry("d1")
        with entry.batch():                       # page 1 and the shared part, last of all
            entry.document.set_title(1, "A")
            entry.document.insert("x", "end")
        refused = call(box, session, "undo", doc="d1", scope="1")
        assert refused.error.code == "entangled" and "other pages show" in refused.error.message
        assert refused.error.details["shared"] == [synthetic.MAIN]
        assert call(box, session, "undo", doc="d1").ok          # plain undo takes it back
        assert call(box, session, "t_title", doc="d1", page="2", text="B").ok
        assert call(box, session, "undo", doc="d1", scope="2").ok


def test_stories_take_a_decks_layouts_and_masters_too():
    for name in ("find_text", "replace_text"):
        stories = shared.SPECS[name].params["stories"]
        assert stories.schema["enum"] == ["body", "all", "layouts", "masters"]
        assert stories.optional


# -- reading an input's text ---------------------------------------------------------------------


def test_read_blob_reads_a_csv_a_page_at_a_time(toolbox):
    session = toolbox.session(clock=CLOCK, limits=Limits(max_result_chars=3000))
    rows = "\n".join(f"{i},Category {i},{i * 1.5}" for i in range(400))
    handle = session.add_blob(("﻿id,name,value\r\n" + rows).encode("utf-8"), "spend.csv")
    first = toolbox.dispatch(session, "read_blob", {"blob": handle})
    assert first.ok, first.to_json()
    assert first.data["mime"] == "text/csv" and first.data["text"].startswith("id,name,value\n")
    assert first.data["lines"] == 401 and first.next_cursor
    assert "\r" not in first.data["text"] and len(first.data["text"]) <= 1000
    text, cursor = first.data["text"], first.next_cursor
    while cursor:
        page = toolbox.dispatch(session, "read_blob", {"blob": handle, "cursor": cursor})
        assert page.ok and page.data["from"] == len(text)
        text, cursor = text + page.data["text"], page.next_cursor
    assert text == "id,name,value\n" + rows
    assert json.loads(first.to_text())["data"]["chars"] == len(text)


def test_read_blob_refuses_images_packages_and_binary(toolbox, session):
    png = session.add_blob(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                           b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\xd7c\xf8"
                           b"\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00"
                           b"IEND\xaeB`\x82", "dot.png")
    image = call(toolbox, session, "read_blob", blob=png)
    assert image.error.code == "invalid_arguments" and "image" in image.error.message
    package = call(toolbox, session, "read_blob",
                   blob=session.add_blob(synthetic.outer_package(), "more.docx"))
    assert package.error.code == "invalid_arguments" and "open_document" in package.error.message
    blob = session.add_blob(b"\xff\xfe\x00binary", "data.bin")
    binary = call(toolbox, session, "read_blob", blob=blob)
    assert binary.error.code == "invalid_arguments"
    assert call(toolbox, session, "read_blob", blob="b99").error.code == "not_found"
    markdown = session.add_blob(b"# Notes\n\nPlain *text*.\n", "notes.md")
    read = call(toolbox, session, "read_blob", blob=markdown)
    assert read.ok and read.data["text"] == "# Notes\n\nPlain *text*.\n" and read.data["lines"] == 3


def test_read_blob_is_a_session_tool_outside_batch(toolbox, session):
    handle = session.add_blob(b"a,b\n1,2\n", "t.csv")
    tool = toolbox.tools["read_blob"]
    assert tool.group == shared.MISC and not tool.batchable and not tool.mutates
    assert tool.strict and tool.handlers == shared.SESSION_TOOLS[-1].handlers
    refused = call(toolbox, session, "batch",
                   ops=[{"tool": "read_blob", "arguments": {"blob": handle}}])
    assert refused.error.code == "invalid_arguments"


# -- what a call changed: Result.touched (round 13, R2) --------------------------------------------


def test_a_changing_call_says_which_parts_it_changed_for_the_application_not_the_model(
        toolbox, session):
    added = call(toolbox, session, "t_add", doc="d1", items=[{"text": "a"}])
    assert added.touched == [{"doc": "d1", "part": synthetic.MAIN}]
    assert "touched" not in added.to_json() and synthetic.MAIN not in added.to_text()
    assert call(toolbox, session, "t_read", doc="d1").touched == []
    assert call(toolbox, session, "t_fail", doc="d1").touched == []
    undone = call(toolbox, session, "undo", doc="d1")
    assert undone.touched == [{"doc": "d1", "part": synthetic.MAIN}]
    assert call(toolbox, session, "undo", doc="d1", redo=True).touched == undone.touched


def test_a_batch_reports_the_union_and_what_each_applied_op_changed(toolbox, session):
    call(toolbox, session, "t_add", doc="d1", items=[{"text": "a"}])
    ops = _ops(("t_set", {"doc": "d1", "target": "item:1", "text": "A"}),
               ("t_read", {"doc": "d1"}),
               ("t_fail", {"doc": "d1"}),
               ("t_title", {"doc": "d2", "page": "2", "text": "Two"}),
               ("t_set", {"doc": "d1", "target": "item:1", "text": "B"}))
    result = call(toolbox, session, "batch", ops=ops)
    assert result.ok and result.data["failed"] == [2]
    assert result.touched == [{"doc": "d1", "part": synthetic.MAIN},
                              {"doc": "d2", "part": synthetic.PAGE2}]
    assert result.touched_by_op == {0: [{"doc": "d1", "part": synthetic.MAIN}],
                                    3: [{"doc": "d2", "part": synthetic.PAGE2}],
                                    4: [{"doc": "d1", "part": synthetic.MAIN}]}
    atomic = call(toolbox, session, "batch", atomic=True, ops=_ops(
        ("t_title", {"doc": "d2", "page": "1", "text": "One"}),
        ("t_set", {"doc": "d1", "target": "item:1", "text": "C"})))
    assert atomic.ok and atomic.touched == [{"doc": "d1", "part": synthetic.MAIN},
                                            {"doc": "d2", "part": synthetic.PAGE1}]
    assert atomic.touched_by_op == {0: [{"doc": "d2", "part": synthetic.PAGE1}],
                                    1: [{"doc": "d1", "part": synthetic.MAIN}]}
    refused = call(toolbox, session, "batch", atomic=True, ops=_ops(
        ("t_set", {"doc": "d1", "target": "item:1", "text": "D"}), ("t_fail", {"doc": "d1"})))
    assert not refused.ok and refused.touched == [] and refused.touched_by_op == {}


def test_a_format_says_what_changed_in_its_own_addresses_and_parts_stand_in_if_it_fails(
        session):
    seen = []

    def pages(document, change):
        seen.append(sorted(change.parts))
        return [{"page": str(document.pages.index(part) + 1)} for part in sorted(change.parts)]

    deck = dataclasses.replace(DECK, touched=pages)
    with Toolbox(TEXT_TOOLS + DECK_TOOLS, formats=[deck, TEXT], groups=shared.GROUPS) as box:
        own = box.session(clock=CLOCK)
        own.open(synthetic.outer_package(), "deck.pptx")
        result = box.dispatch(own, "t_title", {"doc": "d1", "page": "2", "text": "Two"})
        assert result.touched == [{"doc": "d1", "page": "2"}] and seen == [[synthetic.PAGE2]]
        assert box.dispatch(own, "t_title", {"doc": "d1", "page": "2", "text": "Two"}).touched \
            == []                               # the same title: no bytes changed

    def broken(document, change):
        raise RuntimeError("a bug in the mapping")

    deck = dataclasses.replace(DECK, touched=broken)
    with Toolbox(TEXT_TOOLS + DECK_TOOLS, formats=[deck, TEXT], groups=shared.GROUPS) as box:
        own = box.session(clock=CLOCK)
        own.open(synthetic.outer_package(), "deck.pptx")
        result = box.dispatch(own, "t_title", {"doc": "d1", "page": "1", "text": "One"})
        assert result.ok and result.touched == [{"doc": "d1", "part": synthetic.PAGE1}]
