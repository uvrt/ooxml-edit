"""An application's own tools in the toolbox, run inside ``batch`` (round 7, L6), and an
empty list of work as a no-op with a warning (round 7, L7) -- made-up tools only."""

from __future__ import annotations

import pytest

from ooxml_edit.tools import Result, ToolError, Toolbox, array, obj, shared, string, tool

import synthetic
from test_tools_shared import CHECKS, CLOCK, DECK, DECK_TOOLS, TEXT, TEXT_TOOLS, _ops

#: What the application's note tool was given, in order: its own state, outside documents.
NOTES: list[str] = []


@tool("app_note", "Keep a note for the user.", {"text": string("The note.")}, documents=())
def app_note(call, text):
    if not text.strip():
        raise ToolError("invalid_arguments", "the note is empty: write what to keep",
                        field="text")
    NOTES.append(text)
    return {"notes": len(NOTES)}          # a plain value: the op's data


@tool("app_stamp", "Stamp an item of a document: the application's own edit.",
      {"doc": string("Document id."), "target": string("item:N or $ref.")},
      mutates=True, refs=("target",))
def app_stamp(call, doc, target):
    if not target.startswith("item:"):
        raise ToolError("not_found", f"no item {target!r}", field="target")
    from test_tools_shared import _set_item

    _set_item(call.document, int(target[5:]), "stamped")
    call.touch(target)
    return Result(summary=f"Stamped {target}", changed=[target])


@tool("app_crash", "Fails as an application's tool may.", {"doc": string("Document id.")})
def app_crash(call, doc):
    raise RuntimeError("the application's service is down")


@tool("app_send", "Send the document somewhere: never inside a batch.",
      {"doc": string("Document id.")}, batchable=False)
def app_send(call, doc):
    return Result(summary="Sent")


@tool("app_put", "Add items: an empty list adds nothing.",
      {"doc": string("Document id."),
       "items": array(obj({"text": string("Item text.")}, "One item."), "Items to add.")},
      kind="docx", mutates=True)
def app_put(call, doc, items):
    if not items:
        return Result.nothing_given("added")
    for item in items:
        call.touch(f"item:{call.document.insert(item['text'], 'end')}")
    return Result(summary=f"Added {len(items)} item(s)")


APP_TOOLS = [app_note, app_stamp, app_crash, app_send, app_put]


@pytest.fixture
def toolbox():
    CHECKS.clear()
    NOTES.clear()
    with Toolbox(TEXT_TOOLS + DECK_TOOLS + APP_TOOLS, formats=[DECK, TEXT],
                 groups=shared.GROUPS) as box:
        yield box


@pytest.fixture
def session(toolbox):
    session = toolbox.session(clock=CLOCK)
    session.open(synthetic.outer_package(), "text.docx")      # d1
    return session


def _batch(toolbox, session, ops, **options):
    return toolbox.dispatch(session, "batch", {"ops": _ops(*ops), **options})


def test_a_batch_runs_the_applications_own_tools_with_the_libraries(toolbox, session):
    entry = session.entry("d1")
    result = _batch(toolbox, session, [
        ("t_add", {"doc": "d1", "items": [{"text": "a", "ref": "first"}]}),
        ("app_note", {"text": "added a"}),
        ("app_stamp", {"doc": "d1", "target": "$first"}),       # a ref an earlier op made
        ("functions.app_note", {"text": "stamped it"})])         # a namespace, folded
    assert result.ok, result.to_json()
    assert NOTES == ["added a", "stamped it"]
    assert entry.document.items() == ["stamped"] and entry.version == 1
    assert result.data == {"ops": [
        {"op": 0, "tool": "t_add", "summary": "Added 1 item(s)", "created": ["item:1"]},
        {"op": 1, "tool": "app_note", "summary": "", "data": {"notes": 1}},
        {"op": 2, "tool": "app_stamp", "summary": "Stamped item:1"},
        {"op": 3, "tool": "app_note", "summary": "", "data": {"notes": 2}}]}
    assert result.changed == ["item:1"]
    assert "ops[3]: tool 'functions.app_note' read as 'app_note'" in result.warnings
    assert CHECKS == [("d1", ["item:1"])]                     # checked once, at the end
    assert toolbox.dispatch(session, "undo", {"doc": "d1"}).ok  # one step: both edits
    assert entry.document.items() == []


def test_an_applications_tool_that_fails_fails_alone(toolbox, session):
    result = _batch(toolbox, session, [
        ("app_note", {"text": " "}),                                  # its own refusal
        ("app_crash", {"doc": "d1"}),                                 # an exception
        ("app_stamp", {"doc": "d1", "target": "$later"}),             # a ref nobody made
        ("t_add", {"doc": "d1", "items": [{"text": "a"}]})])
    assert result.ok and result.data["applied"] == 1 and result.data["failed"] == [0, 1, 2]
    errors = [op.get("error") for op in result.data["ops"]]
    assert errors[0] == {"code": "invalid_arguments", "field": "arguments.text",
                         "message": "the note is empty: write what to keep"}
    assert errors[1] == {"code": "internal",
                         "message": "RuntimeError: the application's service is down"}
    assert errors[2]["code"] == "not_found" and errors[2]["field"] == "arguments.target"
    assert session.entry("d1").document.items() == ["a"] and NOTES == []
    # Its schema is checked as a library tool's is, before it runs.
    wrong = _batch(toolbox, session, [("app_note", {"words": "x"})])
    assert not wrong.ok and wrong.error.field == "ops[0].arguments.words"
    assert "unknown argument 'words'" in wrong.error.message


def test_an_atomic_batch_rolls_back_documents_not_the_applications_own_state(toolbox, session):
    result = _batch(toolbox, session, [
        ("app_note", {"text": "kept"}),
        ("t_add", {"doc": "d1", "items": [{"text": "a"}]}),
        ("app_crash", {"doc": "d1"})], atomic=True)
    assert not result.ok and result.error.details["tool"] == "app_crash"
    assert session.entry("d1").document.items() == []
    assert NOTES == ["kept"]          # what the application did outside documents stays


def test_what_cannot_run_inside_a_batch_says_so(toolbox, session):
    for name in ("app_send", "batch"):
        result = _batch(toolbox, session, [(name, {"doc": "d1"})])
        assert result.error.field == "ops[0].tool"
        assert f"{name} cannot run inside a batch; call it on its own" in result.error.message
    # A tool the toolbox does not have -- one the application runs itself -- is named so;
    # with no name near it, valid_options lists what a batch can run.
    other = _batch(toolbox, session, [("notify", {"text": "x"})])
    assert "app_note" in other.error.valid_options and "batch" not in other.error.valid_options
    outside = _batch(toolbox, session, [("keep_minutes", {"text": "x"})])
    message = outside.error.details["ops"][0]["error"]["message"]
    assert message == ("no tool 'keep_minutes' that batch can run: it runs only the tools in "
                       "valid_options; call keep_minutes on its own")
    assert outside.error.valid_options == ["app_note"]           # the near names, as before
    near = _batch(toolbox, session, [("app_nte", {"text": "x"})])   # a slip: the near names
    assert near.error.details["ops"][0]["error"]["message"] == "no tool 'app_nte'"
    assert near.error.valid_options[0] == "app_note"


def test_an_empty_items_list_is_a_no_op_with_a_warning(toolbox, session):
    entry = session.entry("d1")
    alone = toolbox.dispatch(session, "app_put", {"doc": "d1", "items": []})
    assert alone.ok and alone.summary == "Nothing added: items is empty"
    assert alone.warnings == ["items is empty: nothing was added"]
    assert entry.version == 0 and not entry.history.can_undo()
    result = _batch(toolbox, session, [
        ("app_put", {"doc": "d1", "items": []}),
        ("app_put", {"doc": "d1", "items": [{"text": "a"}]})])
    assert result.ok and set(result.data) == {"ops"}            # both applied
    assert "ops[0]: items is empty: nothing was added" in result.warnings
    assert entry.document.items() == ["a"] and entry.version == 1
