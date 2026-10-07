"""The toolbox end to end on the toy tools: validation, errors, warnings, versions, outputs."""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import warnings
import zipfile
from pathlib import Path

import pytest

from ooxml_edit.tools import (ERROR_CODES, LimitError, Limits, Result, SYSTEM, ToolError,
                              Toolbox, page_list, page_text, string, tool)

import synthetic
import tools_toys

CLOCK = lambda: dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.timezone.utc)  # noqa: E731


@pytest.fixture
def toolbox():
    with Toolbox(tools_toys.TOOLS, formats=tools_toys.FORMATS, groups=tools_toys.GROUPS) as box:
        yield box


@pytest.fixture
def session(toolbox):
    session = toolbox.session(clock=CLOCK)
    session.open(synthetic.outer_package(), "deck.pptx")
    session.open(synthetic.outer_package(), "text.docx")
    return session


def test_a_call_runs_and_reports_doc_and_version(toolbox, session):
    result = toolbox.dispatch(session, "toy_ppt_set_title", {"doc": "d1", "page": 1, "text": "Q3"})
    assert result.ok and result.doc == "d1" and result.version == 1
    assert result.changed == ["page:1"]
    assert session.entry("d1").document.title(1) == "Q3"
    body = json.loads(toolbox.render_result("anthropic", result, "t")["content"][0]["text"])
    assert body["ok"] is True and body["version"] == 1 and body["summary"]


def test_openai_arguments_as_a_json_string_with_nulls(toolbox, session):
    result = toolbox.dispatch(session, "toy_word_read",
                              '{"doc": "d2", "cursor": null, "page_chars": null}')
    assert result.ok and result.data == ""


def test_every_error_code_is_documented():
    assert set(ERROR_CODES) >= {"not_found", "label_not_found", "ambiguous", "refused", "unit",
                                "invalid_arguments", "timeout", "limit"}
    with pytest.raises(ValueError):
        ToolError("oops", "not a code")


@pytest.mark.parametrize("name, arguments, code, field, options", [
    ("toy_nope", {}, "invalid_arguments", None, None),
    ("toy_ppt_set_title", "{not json", "invalid_arguments", None, []),
    ("toy_ppt_set_title", {"doc": "d1", "page": 0, "text": "x"}, "invalid_arguments", "page", []),
    ("toy_word_insert", {"doc": "d2", "text": "x", "at": "middle"}, "invalid_arguments", "at",
     ["start", "end"]),
    ("toy_ppt_set_title", {"doc": "d7", "page": 1, "text": "x"}, "not_found", "doc", ["d1", "d2"]),
    ("toy_ppt_set_title", {"doc": "d1", "page": 9, "text": "x"}, "not_found", None, []),
    ("toy_ppt_set_title", {"doc": "d2", "page": 1, "text": "x"}, "invalid_arguments", "doc",
     ["d1 (pptx)"]),
    ("toy_ppt_find_label", {"doc": "d1", "label": "Q9"}, "label_not_found", None,
     ["untitled 1", "untitled 2"]),
    ("toy_word_read", {"doc": "d2", "cursor": "zzz"}, "invalid_arguments", "cursor", []),
])
def test_errors_carry_a_code_a_field_and_the_valid_options(toolbox, session, name, arguments,
                                                           code, field, options):
    result = toolbox.dispatch(session, name, arguments)
    assert not result.ok and result.error.code == code
    assert result.error.field == field
    if options is not None:
        assert result.error.valid_options == options
    block = toolbox.render_result("anthropic", result, "t")
    assert block["is_error"] is True


def test_an_unknown_tool_suggests_near_names(toolbox, session):
    result = toolbox.dispatch(session, "toy_ppt_set_titel", {})
    assert result.error.valid_options[0] == "toy_ppt_set_title"


def test_a_failed_call_changes_nothing_and_keeps_the_version(toolbox, session):
    toolbox.dispatch(session, "toy_ppt_set_title", {"doc": "d1", "page": 1, "text": "A"})
    before = session.entry("d1").document.to_bytes()
    result = toolbox.dispatch(session, "toy_ppt_set_title", {"doc": "d1", "page": 5, "text": "B"})
    assert not result.ok and result.version == 1
    assert session.entry("d1").document.to_bytes() == before


def test_a_handler_error_after_the_edit_rolls_the_whole_call_back(session):
    @tool("toy_half", "Edits, then fails.", {"doc": string("Doc.")}, mutates=True)
    def toy_half(call, doc):
        call.document.set_title(1, "half")
        raise ToolError("refused", "changed my mind")

    with Toolbox([toy_half], formats=tools_toys.FORMATS) as box:
        own = box.session()
        own.open(synthetic.outer_package(), "deck.pptx")
        result = box.dispatch(own, "toy_half", {"doc": "d1"})
    assert result.error.code == "refused" and result.version == 0
    assert own.entry("d1").document.title(1) is None


def test_a_changing_tools_reading_mode_runs_as_a_read(session):
    @tool("toy_title", "Read or set a page's title.",
          {"doc": string("Doc."), "action": string("What to do.", enum=["read", "set"]),
           "text": string("set: the title.", optional=True)},
          kind="pptx", mutates=True, reads=lambda arguments: arguments.get("action") == "read")
    def toy_title(call, doc, action, text=None):
        assert call.changing == (action == "set")
        if action == "set":
            call.document.set_title(1, text)
        return Result(summary="title", data=call.document.title(1))

    with Toolbox([toy_title], formats=tools_toys.FORMATS) as box:
        own = box.session()
        own.open(synthetic.outer_package(), "deck.pptx")
        read = box.dispatch(own, "toy_title", {"doc": "d1", "action": "read"})
        assert read.ok and read.version == 0 and read.checks == {}
        assert box.dispatch(own, "toy_title", {"doc": "d1", "action": "set", "text": "A"}).version == 1
        assert box.dispatch(own, "toy_title", {"doc": "d1", "action": "read"}).version == 1
        assert own.undo("d1", 5) == 1          # the reads made no undo steps
        assert box.tools["toy_title"].changes({"action": "set"})
        assert not box.tools["toy_title"].changes({"action": "read"})


def test_undo_and_redo_restore_bytes_and_version(toolbox, session):
    original = session.entry("d1").document.to_bytes()
    toolbox.dispatch(session, "toy_ppt_set_title", {"doc": "d1", "page": 1, "text": "A"})
    toolbox.dispatch(session, "toy_ppt_set_title", {"doc": "d1", "page": 2, "text": "B"})
    entry = session.entry("d1")
    assert entry.version == 2
    assert session.undo("d1", 5) == 2
    assert entry.version == 0 and entry.document.to_bytes() == original
    assert session.redo("d1") == 1 and entry.version == 1
    result = toolbox.dispatch(session, "toy_ppt_set_title", {"doc": "d1", "page": 1, "text": "C"})
    assert result.version == 3   # never reused


def test_warnings_are_collected_not_printed(toolbox, session):
    with warnings.catch_warnings(record=True) as escaped:
        warnings.simplefilter("always")
        for _ in range(2):  # every time, not once per location
            result = toolbox.dispatch(session, "toy_ppt_set_title",
                                      {"doc": "d1", "page": 1, "text": "x" * 60})
            assert result.warnings == ["ToyWarning: the title of page 1 is 60 characters"]
    assert escaped == []


def test_a_format_warning_repeats_without_any_filter_of_the_apps(toolbox, session):
    for _ in range(2):  # the toolbox's own "always" filter for the format's categories
        result = toolbox.dispatch(session, "toy_ppt_set_title",
                                  {"doc": "d1", "page": 2, "text": "y" * 50})
        assert len(result.warnings) == 1


def test_an_idempotence_key_makes_a_retry_safe(toolbox, session):
    args = {"doc": "d1", "page": 1, "text": "A", "key": "k1"}
    first = toolbox.dispatch(session, "toy_ppt_set_title", args)
    second = toolbox.dispatch(session, "toy_ppt_set_title", args)
    assert first.version == second.version == 1
    assert second.changed == first.changed and "already done" in second.summary


def test_render_caches_by_version_and_counts_images(toolbox, session):
    first = toolbox.dispatch(session, "toy_ppt_render", {"doc": "d1", "page": 1, "width": 64})
    assert first.ok and len(first.images) == 1 and first.images[0].width == 64
    assert first.images[0].data.startswith(b"\x89PNG")
    entry = session.entry("d1")
    assert len(entry.render_cache) == 1
    toolbox.dispatch(session, "toy_ppt_render", {"doc": "d1", "page": 1, "width": 64})
    assert len(entry.render_cache) == 1          # a hit
    toolbox.dispatch(session, "toy_ppt_set_title", {"doc": "d1", "page": 1, "text": "A"})
    toolbox.dispatch(session, "toy_ppt_render", {"doc": "d1", "page": 1, "width": 64})
    assert len(entry.render_cache) == 2          # a new version, a new entry
    assert session.images_used == 3


def test_the_image_budget_is_a_limit(toolbox):
    session = toolbox.session(limits=Limits(image_budget=1))
    session.open(synthetic.outer_package(), "deck.pptx")
    assert toolbox.dispatch(session, "toy_ppt_render", {"doc": "d1", "page": 1, "width": 32}).ok
    again = toolbox.dispatch(session, "toy_ppt_render", {"doc": "d1", "page": 1, "width": 32})
    assert again.error.code == "limit"


def test_paging_through_a_long_read(toolbox, session):
    for n in range(30):
        toolbox.dispatch(session, "toy_word_insert", {"doc": "d2", "text": f"item {n:02}", "at": "end"})
    pages, cursor = [], None
    while True:
        result = toolbox.dispatch(session, "toy_word_read",
                                  {"doc": "d2", "cursor": cursor, "page_chars": 100})
        pages.append(result.data)
        cursor = result.next_cursor
        if cursor is None:
            break
    assert len(pages) > 3 and all(len(page) <= 100 for page in pages)
    assert "".join(pages).splitlines()[-1] == "30. item 29"


def test_page_helpers():
    items, total, cursor = page_list(list(range(120)), limit=50)
    assert (len(items), total, cursor) == (50, 120, "c50")
    items, total, cursor = page_list(list(range(120)), cursor="c100", limit=50)
    assert (items[0], cursor) == (100, None)
    text, cursor = page_text("abc\n" * 10, limit=10)
    assert text.endswith("\n") and cursor


# -- outputs and the validate gate -------------------------------------------------------------


def test_saving_hands_bytes_to_the_app_never_the_model(toolbox, session):
    result = toolbox.dispatch(session, "toy_save", {"doc": "d1", "name": "out.pptx",
                                                    "format": "pptx"})
    assert result.ok and result.data["size"] > 0
    text = json.dumps(result.to_json())
    assert "PK" not in text and len(text) < 1000
    outputs = session.take_outputs()
    assert [o.name for o in outputs] == ["out.pptx"] and outputs[0].data.startswith(b"PK")
    assert session.take_outputs() == []
    pushed = []
    own = toolbox.session(on_output=pushed.append)
    own.open(synthetic.outer_package(), "t.docx")
    assert toolbox.dispatch(own, "toy_save", {"doc": "d1", "name": "t.docx", "format": "docx"}).ok
    assert [o.name for o in pushed] == ["t.docx"] and own.outputs == []


def test_the_validate_gate_refuses_new_problems(toolbox, session):
    toolbox.dispatch(session, "toy_ppt_set_title", {"doc": "d1", "page": 2, "text": ""})
    result = toolbox.dispatch(session, "toy_save", {"doc": "d1", "name": "o.pptx", "format": "pptx"})
    assert result.error.code == "refused"
    assert result.error.valid_options == ["page 2 has an empty title"]
    assert session.take_outputs() == []


def test_only_the_app_can_override_the_gate(session):
    with Toolbox(tools_toys.TOOLS, formats=tools_toys.FORMATS, allow_new_problems=True) as box:
        box.dispatch(session, "toy_ppt_set_title", {"doc": "d1", "page": 2, "text": ""})
        result = box.dispatch(session, "toy_save", {"doc": "d1", "name": "o.pptx", "format": "pptx"})
    assert result.ok and result.data["validate"]["new"] == ["page 2 has an empty title"]
    for item in tools_toys.TOOLS:  # no tool offers the override
        assert not any("problem" in name or "force" in name
                       for name in item.canonical["properties"])


def test_a_shared_tool_dispatches_by_kind(toolbox, session):
    wrong = toolbox.dispatch(session, "toy_save", {"doc": "d2", "name": "x", "format": "pptx"})
    assert wrong.error.valid_options == ["docx"]
    assert toolbox.dispatch(session, "toy_save", {"doc": "d2", "name": "x.docx", "format": "docx"}).ok


# -- sessions: bytes in, handles, limits -------------------------------------------------------


def test_a_session_takes_bytes_never_a_path(toolbox, tmp_path):
    session = toolbox.session()
    target = tmp_path / "deck.pptx"
    target.write_bytes(synthetic.outer_package())
    for wrong in (str(target), target, Path("deck.pptx")):
        with pytest.raises(TypeError, match="bytes"):
            session.open(wrong, "deck.pptx")
        with pytest.raises(TypeError, match="bytes"):
            session.add_blob(wrong, "deck.pptx")


def test_blobs_are_checked_and_handled(toolbox):
    session = toolbox.session()
    png = tools_toys.png(8, 4)
    assert session.add_blob(png, "logo.png") == "b1"
    assert session.blob("b1").mime == "image/png"
    assert session.add_blob(b"# Title\n", "notes.md") == "b2"
    assert session.blob("b2").mime == "text/markdown"
    with pytest.raises(LimitError, match="declared"):
        session.add_blob(b"MZ\x90\x00", "logo.png", mime="image/png")
    with pytest.raises(ToolError) as caught:
        session.blob("b9")
    assert caught.value.code == "not_found"
    assert caught.value.valid_options == ["b1 (logo.png)", "b2 (notes.md)"]
    doc = session.open_blob(session.add_blob(synthetic.outer_package(), "in.pptx"))
    assert session.entry(doc).source == "b3"


def test_an_image_over_the_pixel_limit_is_refused_from_its_header(toolbox):
    session = toolbox.session(limits=Limits(max_image_pixels=100))
    with pytest.raises(LimitError, match="pixels"):
        session.add_blob(tools_toys.png(20, 20), "big.png")


def _zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def test_a_zip_bomb_is_refused_before_opening(toolbox):
    session = toolbox.session(limits=Limits(max_uncompressed_bytes=5 * 1024 * 1024))
    bomb = _zip([("a.xml", b"\x00" * (8 * 1024 * 1024))])
    with pytest.raises(LimitError, match="expands"):
        session.open(bomb, "bomb.pptx")
    ratio = toolbox.session()
    with pytest.raises(LimitError, match="ratio"):
        ratio.open(_zip([("a.xml", b"\x00" * (4 * 1024 * 1024))]), "ratio.pptx")
    parts = toolbox.session(limits=Limits(max_parts=3))
    with pytest.raises(LimitError, match="parts"):
        parts.open(synthetic.outer_package(), "many.pptx")


def test_the_number_of_open_documents_is_limited(toolbox):
    session = toolbox.session(limits=Limits(max_documents=1))
    session.open(synthetic.outer_package(), "a.pptx")
    with pytest.raises(LimitError, match="open"):
        session.open(synthetic.outer_package(), "b.pptx")


def test_an_unknown_format_is_refused(toolbox):
    with pytest.raises(ValueError, match="registered format"):
        toolbox.session().open(synthetic.outer_package(), "data.bin")


# -- logs and prompts --------------------------------------------------------------------------


def test_calls_are_logged_with_a_digest_not_content(toolbox, session, caplog):
    secret = "the merger closes on Friday"
    with caplog.at_level(logging.INFO, logger="ooxml_edit.tools"):
        toolbox.dispatch(session, "toy_word_insert", {"doc": "d2", "text": secret, "at": "end"})
    record = session.log[-1]
    assert record.tool == "toy_word_insert" and record.docs == ["d2"] and record.ok
    assert record.shape == {"doc": "str[2]", "text": f"str[{len(secret)}]", "at": "str[3]"}
    assert record.arguments is None and len(record.digest) == 16
    assert session.entry("d2").log[-1] is record
    assert secret not in caplog.text and record.digest in caplog.text
    assert secret not in json.dumps(record.to_json())


def test_the_system_prompt_is_mechanics_and_the_app_appends(toolbox):
    prompt = toolbox.system_prompt(extra="House style: one accent colour.")
    assert prompt.startswith(SYSTEM.strip()[:40])
    assert "Toy deck" in prompt and prompt.rstrip().endswith("House style: one accent colour.")
    lowered = SYSTEM.lower()
    for word in ("palette", "legend", "house style", "density", "font family"):
        assert word not in lowered
    assert len(SYSTEM) < 3000   # about 600 tokens


def test_the_clock_is_the_sessions(toolbox):
    session = toolbox.session(clock=CLOCK)
    assert session.now() == CLOCK() and session.created_at == CLOCK()
