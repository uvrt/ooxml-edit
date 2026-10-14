"""``describe`` lists the session's datasets (round 5, T11): whichever document it describes,
every kind's result gets ``datasets`` -- id, columns, type labels, row count and where each
came from -- and never their values."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from ooxml_edit.tools import (Dataset, DatasetEntry, Result, Toolbox, shared, string, tool)
from ooxml_edit.tools.datasets import BRIEF_COLUMNS, BRIEF_DATASETS

import synthetic
import tools_toys

CLOCK = lambda: dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.timezone.utc)  # noqa: E731

TITLES = Dataset(("page", "title"), [(1, "Plan"), (2, "Costs")], types=("number", None))


@tool("dd_derive", "Keep a dataset of a document's titles.",
      {"doc": string("Document id."), "id": string("The dataset's id."),
       "at": string("The address it came from.", optional=True),
       "from": string("A dataset to derive from instead.", optional=True)},
      datasets=("from",))
def dd_derive(call, doc, id, at=None, **rest):
    if rest.get("from"):
        entry = call.dataset(rest["from"])
        address = call.define_dataset(id, Dataset(("page",), [(1,)]), source=entry)
    else:
        address = call.define_dataset(id, TITLES, origin=at)
    return Result(summary=f"Kept {address}")


@shared.handler("describe", kind="pptx")
def deck_describe(call, doc):
    return Result(summary="A deck", data={"doc": doc, "pages": 2})


@shared.handler("describe", kind="docx")
def text_describe(call, doc):
    return {"doc": doc}


TOOLS = [dd_derive, deck_describe, text_describe, tools_toys.toy_ppt_set_title,
         *shared.SESSION_TOOLS]


@pytest.fixture
def toolbox():
    with Toolbox(TOOLS, formats=tools_toys.FORMATS, groups=shared.GROUPS) as box:
        yield box


@pytest.fixture
def session(toolbox):
    session = toolbox.session(clock=CLOCK)
    session.open(synthetic.outer_package(), "deck.pptx")      # d1
    session.open(synthetic.outer_package(), "text.docx")      # d2
    return session


def call(toolbox, session, name, **arguments):
    result = toolbox.dispatch(session, name, arguments)
    assert result.ok, result.to_json()
    return result


def test_without_datasets_describe_is_as_before(toolbox, session):
    result = call(toolbox, session, "describe", doc="d1")
    assert result.data == {"doc": "d1", "pages": 2}


def test_describe_of_any_document_lists_every_dataset_and_where_it_came_from(toolbox, session):
    call(toolbox, session, "dd_derive", doc="d1", id="titles", at="t:sales")
    call(toolbox, session, "dd_derive", doc="d1", id="plain")
    call(toolbox, session, "dd_derive", doc="d2", id="first", **{"from": "d:titles"})
    session.keep_datasets([DatasetEntry("given", Dataset(("name",), [("x",)]))])
    for doc in ("d1", "d2"):                     # a deck's handler and a document's
        listed = call(toolbox, session, "describe", doc=doc).data["datasets"]
        assert listed == [
            {"id": "d:titles", "columns": ["page", "title"], "types": {"page": "number"},
             "rows": 2, "from": "d1/t:sales"},
            {"id": "d:plain", "columns": ["page", "title"], "types": {"page": "number"},
             "rows": 2, "from": "d1"},
            {"id": "d:first", "columns": ["page"], "rows": 1, "from": "d:titles"},
            {"id": "d:given", "columns": ["name"], "rows": 1, "from": "application"}]
    text = json.dumps(listed)
    assert "Plan" not in text and "Costs" not in text      # never a value


def test_a_stale_or_closed_source_is_said(toolbox, session):
    call(toolbox, session, "dd_derive", doc="d1", id="titles", at="t:sales")
    call(toolbox, session, "toy_ppt_set_title", doc="d1", page=1, text="Changed")
    listed = call(toolbox, session, "describe", doc="d2").data["datasets"]
    assert listed[0]["stale"] is True and "closed" not in listed[0]
    call(toolbox, session, "close_document", doc="d1")
    listed = call(toolbox, session, "describe", doc="d2").data["datasets"]
    assert listed[0]["stale"] is True and listed[0]["closed"] is True


def test_a_batch_describes_the_datasets_its_earlier_ops_defined(toolbox, session):
    result = call(toolbox, session, "batch", ops=[
        {"tool": "dd_derive", "arguments": {"doc": "d1", "id": "titles"}},
        {"tool": "describe", "arguments": {"doc": "d2"}}])
    described = result.data["ops"][1]["data"]
    assert described["datasets"] == [{"id": "d:titles", "columns": ["page", "title"],
                                      "types": {"page": "number"}, "rows": 2, "from": "d1"}]
    result = call(toolbox, session, "batch", ops=[
        {"tool": "dd_derive", "arguments": {"doc": "d1", "id": "titles"}},
        {"tool": "toy_ppt_set_title", "arguments": {"doc": "d1", "page": 1, "text": "X"}},
        {"tool": "describe", "arguments": {"doc": "d2"}}])
    assert result.data["ops"][2]["data"]["datasets"][0]["stale"] is True


def test_columns_and_datasets_are_capped(toolbox, session):
    wide = Dataset(tuple(f"c{n}" for n in range(BRIEF_COLUMNS + 5)), [])
    session.keep_datasets([DatasetEntry(f"n{n}", wide) for n in range(BRIEF_DATASETS + 3)])
    data = call(toolbox, session, "describe", doc="d1").data
    assert len(data["datasets"]) == BRIEF_DATASETS and data["more_datasets"] == 3
    assert data["datasets"][0]["id"] == "d:n3"          # the latest are listed
    first = data["datasets"][0]
    assert len(first["columns"]) == BRIEF_COLUMNS and first["more_columns"] == 5
    assert first["rows"] == 0
