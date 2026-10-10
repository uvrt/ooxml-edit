"""The application's font folders: a toolbox default, a session's own, on every document
and into the worker processes -- and never in a tool's definition."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ooxml_edit.tools import DocumentFormat, InProcess, Result, Toolbox, string, tool

import synthetic
import tools_toys

CONFIGURED: list[tuple[object, object]] = []


@tool("toy_fonts", "Report the font folders a render would use.",
      {"doc": string("Document id, e.g. d1.")}, kind="pptx")
def toy_fonts(call, doc):
    pid, seen = call.run(tools_toys.fonts_seen, call.font_dirs)
    return Result(summary="fonts", data={"pid": pid, "seen": seen,
                                         "entry": call.entry.font_dirs,
                                         "document": getattr(call.document, "font_dirs", "unset")})


def _configure(document, session) -> None:
    document.font_dirs = session.font_dirs
    CONFIGURED.append((document, session.font_dirs))


FORMAT = DocumentFormat(kind="pptx", open=tools_toys.ToyDocument, detect=tools_toys._is(".pptx"),
                        configure=_configure)


def _seen(box: Toolbox, **session_options) -> dict:
    session = box.session(**session_options)
    session.open(synthetic.outer_package(), "deck.pptx")
    result = box.dispatch(session, "toy_fonts", {"doc": "d1"})
    assert result.ok, result.error
    return result.data


def test_the_toolbox_default_reaches_a_worker_process(tmp_path):
    folder = tmp_path / "fonts"
    with Toolbox([toy_fonts], formats=[FORMAT], workers=1, font_dirs=[folder]) as box:
        assert box.font_dirs == (str(folder),)
        data = _seen(box)
    assert data["pid"] != os.getpid()
    assert tuple(data["seen"]) == (str(folder),)
    assert data["entry"] == (str(folder),) and data["document"] == (str(folder),)


def test_a_session_overrides_the_toolbox(tmp_path):
    with Toolbox([toy_fonts], formats=[FORMAT], workers=0, font_dirs=[tmp_path / "a"]) as box:
        assert tuple(_seen(box, font_dirs=[tmp_path / "b", str(tmp_path / "c")])["seen"]) == (
            str(tmp_path / "b"), str(tmp_path / "c"))
        # None: not configured -- the renderers' default (OOXML_FONT_DIRS, the system's).
        assert _seen(box, font_dirs=None)["seen"] is None
        # []: configured empty -- the renderers read no environment variable either.
        assert _seen(box, font_dirs=[])["seen"] == ()
        # Not given: the toolbox's.
        assert tuple(_seen(box)["seen"]) == (str(tmp_path / "a"),)


def test_in_process_and_an_applications_runner_see_the_same(tmp_path):
    for box in (Toolbox([toy_fonts], formats=[FORMAT], workers=0, font_dirs=str(tmp_path)),
                Toolbox([toy_fonts], formats=[FORMAT], runner=InProcess(1), font_dirs=tmp_path)):
        with box:
            data = _seen(box)
        assert data["pid"] == os.getpid() and tuple(data["seen"]) == (str(tmp_path),)


def test_adopted_documents_are_configured_too(tmp_path):
    with Toolbox([toy_fonts], formats=[FORMAT], workers=0) as box:
        session = box.session(font_dirs=[tmp_path])
        document = tools_toys.ToyDocument(synthetic.outer_package())
        doc_id = session.adopt(document, "pptx", "new.pptx")
        assert document.font_dirs == (str(tmp_path),)
        assert session.entry(doc_id).font_dirs == (str(tmp_path),)
        assert CONFIGURED[-1] == (document, (str(tmp_path),))


def test_a_path_is_all_it_takes():
    with pytest.raises(TypeError):
        Toolbox([toy_fonts], formats=[FORMAT], font_dirs=[3])


def test_the_definitions_do_not_change(tmp_path):
    """Application configuration, not a model argument: zero tokens."""
    plain = Toolbox(tools_toys.TOOLS, formats=tools_toys.FORMATS, groups=tools_toys.GROUPS)
    fonts = Toolbox(tools_toys.TOOLS, formats=tools_toys.FORMATS, groups=tools_toys.GROUPS,
                    font_dirs=[tmp_path])
    with plain, fonts:
        for provider in ("anthropic", "openai-responses", "openai-chat"):
            options = {"groups": "all"} if provider == "openai-chat" else {}
            assert json.dumps(plain.definitions(provider, **options)) == json.dumps(
                fonts.definitions(provider, **options))
        assert plain.system_prompt() == fonts.system_prompt()
        assert "font" not in json.dumps(fonts.definitions("anthropic")).lower()
