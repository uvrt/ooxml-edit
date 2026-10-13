"""The application's locale: a toolbox default, a session's own, on every document and into
the worker processes -- en-US when not given, never the machine's, and never in a tool's
definition.  ooxml-edit imports nothing of ooxml-common: it keeps the object it is given."""

from __future__ import annotations

import dataclasses
import json
import os
import pickle
from types import SimpleNamespace

import pytest

from ooxml_edit.tools import (DEFAULT_LOCALE, LOCALE_FIELDS, DocumentFormat, InProcess, Result,
                              Session, Toolbox, locale_of, string, tool)

import synthetic
import tools_toys


@dataclasses.dataclass(frozen=True)
class Dutch:
    """A locale as an application builds one; any object with the fields will do."""

    decimal_separator: str = ","
    thousands_separator: str = "."
    date_order: str = "dmy"
    language: str = "nl-NL"
    list_separator: str = ";"


CONFIGURED: list[tuple[object, object]] = []


@tool("toy_locale", "Report the locale a render would use.",
      {"doc": string("Document id, e.g. d1.")}, kind="pptx")
def toy_locale(call, doc):
    pid, seen = call.run(tools_toys.locale_seen, call.locale)
    return Result(summary="locale", data={"pid": pid, "seen": seen, "entry": call.entry.locale,
                                          "document": getattr(call.document, "locale", None)})


def _configure(document, session) -> None:
    document.locale = session.locale
    CONFIGURED.append((document, session.locale))


FORMAT = DocumentFormat(kind="pptx", open=tools_toys.ToyDocument, detect=tools_toys._is(".pptx"),
                        configure=_configure)


def _seen(box: Toolbox, **session_options) -> dict:
    session = box.session(**session_options)
    session.open(synthetic.outer_package(), "deck.pptx")
    result = box.dispatch(session, "toy_locale", {"doc": "d1"})
    assert result.ok, result.error
    return result.data


def test_the_default_is_en_us_whatever_the_machine(monkeypatch):
    for name in ("LANG", "LC_ALL", "LC_NUMERIC", "LC_TIME"):
        monkeypatch.setenv(name, "nl_BE.UTF-8")
    with Toolbox([toy_locale], formats=[FORMAT], workers=0) as box:
        assert box.locale is DEFAULT_LOCALE
        data = _seen(box)
    assert data["seen"] is DEFAULT_LOCALE and data["entry"] is DEFAULT_LOCALE
    assert data["document"] is DEFAULT_LOCALE
    assert (DEFAULT_LOCALE.decimal_separator, DEFAULT_LOCALE.thousands_separator,
            DEFAULT_LOCALE.date_order, DEFAULT_LOCALE.language,
            DEFAULT_LOCALE.list_separator) == (".", ",", "mdy", "en-US", ",")
    assert Session().locale is DEFAULT_LOCALE


def test_the_default_matches_ooxml_commons_en_us():
    locales = pytest.importorskip("ooxml_common.locales")
    for name in LOCALE_FIELDS:
        assert getattr(DEFAULT_LOCALE, name) == getattr(locales.EN_US, name)
    assert DEFAULT_LOCALE.primary_language == locales.EN_US.primary_language
    for text in ("1,234.5", "0.25", "12.5%", "-3"):
        assert DEFAULT_LOCALE.number_text(text) == locales.EN_US.number_text(text)


def test_the_toolbox_default_reaches_a_worker_process():
    with Toolbox([toy_locale], formats=[FORMAT], workers=1, locale=Dutch()) as box:
        assert box.locale == Dutch()
        data = _seen(box)
    assert data["pid"] != os.getpid()
    assert data["seen"] == Dutch() and data["entry"] == Dutch() and data["document"] == Dutch()


def test_the_default_crosses_into_a_worker_too():
    with Toolbox([toy_locale], formats=[FORMAT], workers=1) as box:
        data = _seen(box)
    assert data["pid"] != os.getpid() and data["seen"] == DEFAULT_LOCALE
    assert pickle.loads(pickle.dumps(DEFAULT_LOCALE)) == DEFAULT_LOCALE


def test_an_ooxml_common_locale_is_kept_as_given():
    locales = pytest.importorskip("ooxml_common.locales")
    dutch = locales.Locale(decimal_separator=",", thousands_separator=".", date_order="dmy",
                           language="nl-NL", list_separator=";")
    with Toolbox([toy_locale], formats=[FORMAT], workers=1, locale=dutch) as box:
        assert box.locale is dutch
        data = _seen(box)
    assert data["pid"] != os.getpid()
    assert data["seen"] == dutch and type(data["seen"]) is locales.Locale
    assert data["entry"] is dutch


def test_a_session_overrides_the_toolbox():
    english = Dutch(decimal_separator=".", thousands_separator=",", language="en-GB",
                    list_separator=",")
    with Toolbox([toy_locale], formats=[FORMAT], workers=0, locale=Dutch()) as box:
        assert _seen(box, locale=english)["seen"] == english
        # None: en-US, not the toolbox's.
        assert _seen(box, locale=None)["seen"] is DEFAULT_LOCALE
        # Not given: the toolbox's.
        assert _seen(box)["seen"] == Dutch()


def test_in_process_and_an_applications_runner_see_the_same():
    for box in (Toolbox([toy_locale], formats=[FORMAT], workers=0, locale=Dutch()),
                Toolbox([toy_locale], formats=[FORMAT], runner=InProcess(1), locale=Dutch())):
        with box:
            data = _seen(box)
        assert data["pid"] == os.getpid() and data["seen"] == Dutch()


def test_adopted_documents_are_configured_too():
    with Toolbox([toy_locale], formats=[FORMAT], workers=0) as box:
        session = box.session(locale=Dutch())
        document = tools_toys.ToyDocument(synthetic.outer_package())
        doc_id = session.adopt(document, "pptx", "new.pptx")
        assert document.locale == Dutch()
        assert session.entry(doc_id).locale == Dutch()
        assert CONFIGURED[-1] == (document, Dutch())


@pytest.mark.parametrize("bad", [
    "nl-NL", 3, object(),
    SimpleNamespace(decimal_separator=",", thousands_separator=".", date_order="dmy",
                    language="nl-NL"),
    SimpleNamespace(decimal_separator=",", thousands_separator=".", date_order="dmy",
                    language=None, list_separator=";")])
def test_a_locale_is_an_object_with_the_fields(bad):
    with pytest.raises(TypeError):
        Toolbox([toy_locale], formats=[FORMAT], locale=bad)
    with pytest.raises(TypeError):
        locale_of(bad)


def test_the_definitions_do_not_change():
    """Application configuration, not a model argument: zero tokens."""
    plain = Toolbox(tools_toys.TOOLS, formats=tools_toys.FORMATS, groups=tools_toys.GROUPS)
    dutch = Toolbox(tools_toys.TOOLS, formats=tools_toys.FORMATS, groups=tools_toys.GROUPS,
                    locale=Dutch())
    with plain, dutch:
        for provider in ("anthropic", "openai-responses", "openai-chat"):
            options = {"groups": "all"} if provider == "openai-chat" else {}
            assert json.dumps(plain.definitions(provider, **options)) == json.dumps(
                dutch.definitions(provider, **options))
        assert plain.system_prompt() == dutch.system_prompt()
        assert "locale" not in json.dumps(dutch.definitions("anthropic")).lower()
