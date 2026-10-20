"""Datasets (``d:<id>``): defined by one call, named by later ones on any document, kept
past ``close_document``, and marked stale -- never refreshed -- when their source changes."""

from __future__ import annotations

import datetime as dt
import pickle

import pytest

from ooxml_edit.tools import (DATASET_PREFIX, Dataset, DatasetEntry, Limits, Result, Tool,
                              ToolError, Toolbox, array, dataset_id, obj, shared, string,
                              tool)
from ooxml_edit.tools.registry import merge_tools
from ooxml_edit.tools.schema import SubsetError

import synthetic
import tools_toys

CLOCK = lambda: dt.datetime(2026, 10, 13, 12, 0, tzinfo=dt.timezone.utc)  # noqa: E731


# -- toy producers and consumers -----------------------------------------------------------------


def _titles(document) -> Dataset:
    """A toy deck's pages as a dataset: page number, title, title length; each value's
    source the page it came from."""
    rows, sources = [], []
    for n in range(1, len(document.pages) + 1):
        title = document.title(n) or ""
        rows.append((n, title, len(title)))
        sources.append(((f"page:{n}",),) * 3)
    return Dataset(("page", "title", "length"), rows, sources=sources, keys=1,
                   types=("number", "text", "number"))


@tool("ds_derive", "Derive a dataset from a document's pages, or from a dataset.",
      {"doc": string("Document id."), "id": string("The dataset's id."),
       "from": string("A dataset to derive from instead.", optional=True),
       "fail": string("Fail after defining it.", optional=True),
       "cache": string("The formula caches its values were read from.", optional=True)},
      datasets=("from",))
def ds_derive(call, doc, id, fail=None, cache=None, **rest):
    origin = rest.get("from")
    if origin is not None:
        entry = call.dataset(origin)
        data = entry.dataset
        derived = Dataset(data.columns, data.rows[:1], sources=data.sources[:1], keys=data.keys)
        address = call.define_dataset(id, derived, source=entry)
    else:
        titles = _titles(call.document)
        if cache:
            titles = Dataset(titles.columns, titles.rows, sources=titles.sources,
                             keys=titles.keys, types=titles.types, cache={cache: 2})
        address = call.define_dataset(id, titles)
    if fail:
        raise ToolError("refused", "failed after defining")
    return Result(summary=f"Derived {address}")


@tool("ds_read", "Read a dataset by address.",
      {"from": string("A dataset address, d:<id>.")},
      documents=(), datasets=("from",))
def ds_read(call, **arguments):
    entry = call.dataset(arguments["from"])
    return {"columns": list(entry.dataset.columns), "rows": entry.dataset.records(),
            "stale": entry.stale, "doc": entry.doc, "version": entry.version}


@tool("ds_insert", "Insert a column of datasets into a text document.",
      {"doc": string("Document id."),
       "items": array(obj({"from": string("A dataset."), "column": string("A column.")},
                          "One column."), "Columns to insert.", min_items=1)},
      kind="docx", mutates=True, datasets=("items[].from",))
def ds_insert(call, doc, items):
    for item in items:
        for value in call.dataset(item["from"]).dataset.column(item["column"]):
            call.document.insert(str(value), "end")
    return Result(summary=f"Inserted {len(items)} column(s)")


TOOLS = [ds_derive, ds_read, ds_insert, tools_toys.toy_ppt_set_title, *shared.SESSION_TOOLS]


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
    return toolbox.dispatch(session, name, arguments)


def retitle(toolbox, session, text, page=1):
    result = call(toolbox, session, "toy_ppt_set_title", doc="d1", page=page, text=text)
    assert result.ok, result.to_json()
    return result


# -- the value type ------------------------------------------------------------------------------


def test_a_dataset_is_columns_rows_and_opaque_sources():
    data = Dataset(["k", "v"], [{"k": "a", "v": 1}, ("b", 2)], sources=[["x!1", "y"], [(), ("z",)]],
                   keys=1)
    assert data.columns == ("k", "v") and data.rows == (("a", 1), ("b", 2))
    assert data.sources == ((("x!1",), ("y",)), ((), ("z",)))
    assert data.cells == 4 and data.column("v") == (1, 2)
    assert data.records() == [{"k": "a", "v": 1}, {"k": "b", "v": 2}]
    assert pickle.loads(pickle.dumps(data)) == data      # it may go to a worker process
    with pytest.raises(ToolError) as caught:
        data.column("w")
    assert caught.value.code == "not_found" and caught.value.valid_options == ["k", "v"]


@pytest.mark.parametrize("kwargs", [
    {"columns": [], "rows": []},
    {"columns": ["a", "a"], "rows": []},
    {"columns": ["a"], "rows": [(1, 2)]},
    {"columns": ["a"], "rows": [{"b": 1}]},
    {"columns": ["a"], "rows": [(1,)], "sources": [[("s",)], [("t",)]]},
    {"columns": ["a"], "rows": [(1,)], "sources": [[(1,)]]},
    {"columns": ["a"], "rows": [], "keys": 2},
    {"columns": ["a"], "rows": [], "types": ("x", "y")},
])
def test_a_malformed_dataset_is_refused(kwargs):
    with pytest.raises(ValueError):
        Dataset(**kwargs)


def test_a_dataset_counts_the_formula_caches_its_values_came_from():
    data = Dataset(["k"], [(1,)], cache={"external": 1, "invalidated": 0,
                                         "cached_unverified": 2})
    assert data.cache == {"cached_unverified": 2, "external": 1}    # in order, zeros dropped
    assert not data.invalidated and Dataset(["k"], [(1,)], cache={}).cache is None
    assert Dataset(["k"], [(1,)], cache={"invalidated": 1}).invalidated
    assert data == Dataset(["k"], [(1,)])                 # not compared, as detail is not
    assert data.row_slice(1).cache == data.cache
    for bad in ({"fresh": 1}, {"absent": -1}, {"absent": True}, {"absent": 1.5}, [1]):
        with pytest.raises(ValueError):
            Dataset(["k"], [(1,)], cache=bad)


def test_a_dataset_from_invalidated_caches_is_stale_from_the_start_and_stays_so(toolbox,
                                                                               session):
    """Its values are as last saved, after an edit asked for a recalculation: no version
    of the source has current ones, so neither undo nor redo makes it current."""
    result = call(toolbox, session, "ds_derive", doc="d1", id="titles", cache="invalidated")
    assert result.ok and result.warnings == [
        "d:titles is stale: it was computed from formula values whose cache is invalidated "
        "(2 cell(s)): they are as last saved, and nothing here recalculates; recalculate the "
        "source in its application, then derive it again"]
    entry = session.dataset("d:titles")
    assert entry.stale and entry.brief()["stale"] is True
    read = call(toolbox, session, "ds_read", **{"from": "d:titles"})
    assert read.data["stale"] and read.warnings
    fresh = call(toolbox, session, "ds_derive", doc="d1", id="other", cache="cached_unverified")
    assert fresh.ok and not fresh.warnings and not session.dataset("d:other").stale


def test_an_address_names_its_id_and_the_rest_is_the_formats():
    assert dataset_id("d:by_region") == "by_region"
    assert dataset_id("d:by_region[total]") == "by_region"
    assert dataset_id("d:by_region/North/total") == "by_region"
    assert dataset_id("d1") is None and dataset_id("t:Products") is None


def test_a_tool_declares_its_dataset_arguments_as_it_declares_refs():
    with pytest.raises(SubsetError, match="datasets names 'nope'"):
        Tool(name="bad", description="Bad.", schema={"type": "object", "properties": {},
                                                     "additionalProperties": False},
             datasets=("nope",))
    plain = Tool(name="same", description="Same.", schema=ds_read.schema)
    declared = Tool(name="same", description="Same.", schema=ds_read.schema,
                    datasets=("from",))
    assert not plain.same_definition(declared)
    with pytest.raises(ValueError, match="two different tools"):
        merge_tools([plain, declared])


def test_no_shared_definition_takes_a_dataset_yet():
    """O2 adds the store, not consumers: every deck and document definition is unchanged."""
    assert all(not spec.datasets for spec in shared.SPECS.values())


# -- defining and naming -------------------------------------------------------------------------


def test_a_dataset_is_named_by_a_later_call_without_a_document(toolbox, session):
    made = call(toolbox, session, "ds_derive", doc="d1", id="titles")
    assert made.ok and made.to_json()["datasets"] == ["d:titles"]
    read = call(toolbox, session, "ds_read", **{"from": "d:titles[title]"})
    assert read.ok, read.to_json()
    assert read.data["columns"] == ["page", "title", "length"]
    assert read.data["doc"] == "d1" and read.data["version"] == 0 and not read.data["stale"]
    assert not read.warnings and "datasets" not in read.to_json()


def test_a_dataset_from_one_document_fills_another(toolbox, session):
    retitle(toolbox, session, "Revenue")
    call(toolbox, session, "ds_derive", doc="d1", id="titles")
    before = len(session.entry("d2").document.items())
    result = call(toolbox, session, "ds_insert", doc="d2",
                  items=[{"from": "d:titles", "column": "title"}])
    assert result.ok, result.to_json()
    assert session.entry("d2").document.items()[before] == "Revenue"


def test_an_unknown_dataset_is_refused_with_the_known_ids(toolbox, session):
    call(toolbox, session, "ds_derive", doc="d1", id="titles")
    call(toolbox, session, "ds_derive", doc="d2", id="items")
    result = call(toolbox, session, "ds_insert", doc="d2",
                  items=[{"from": "d:titles", "column": "title"},
                         {"from": "d:title", "column": "title"}])
    error = result.error
    assert error.code == "not_found" and error.field == "items[1].from"
    assert error.valid_options == ["d:titles", "d:items"]
    assert session.entry("d2").version == 0             # refused before the handler ran


def test_a_value_that_is_not_a_dataset_address_is_left_to_the_handler(toolbox, session):
    result = call(toolbox, session, "ds_read", **{"from": "t:Products"})
    assert result.error.code == "not_found" and result.error.valid_options == []


@pytest.mark.parametrize("bad", ["", "1st", "has space", "a/b", "x" * 65])
def test_a_dataset_id_has_a_shape(toolbox, session, bad):
    result = call(toolbox, session, "ds_derive", doc="d1", id=bad)
    assert result.error.code == "invalid_arguments" and result.error.field == "id"
    assert not session.datasets


def test_redefining_an_id_replaces_it(toolbox, session):
    call(toolbox, session, "ds_derive", doc="d1", id="t")
    call(toolbox, session, "ds_derive", doc="d2", id="t")
    assert list(session.datasets) == ["t"] and session.dataset("d:t").doc == "d2"


def test_a_failed_call_defines_nothing(toolbox, session):
    result = call(toolbox, session, "ds_derive", doc="d1", id="t", fail="yes")
    assert not result.ok and "datasets" not in result.to_json()
    assert not session.datasets


def test_a_dataset_derived_from_a_dataset_keeps_its_origins_source(toolbox, session):
    call(toolbox, session, "ds_derive", doc="d1", id="titles")
    made = call(toolbox, session, "ds_derive", doc="d2", id="first", **{"from": "d:titles"})
    assert made.ok, made.to_json()
    first = session.dataset("d:first")
    assert first.doc == "d1" and first.version == 0 and len(first.dataset.rows) == 1
    retitle(toolbox, session, "Changed")
    assert first.stale and session.dataset("d:titles").stale


def test_the_session_lists_its_datasets_without_their_values(toolbox, session):
    assert "datasets" not in session.describe()
    call(toolbox, session, "ds_derive", doc="d1", id="titles")
    listed = session.describe()["datasets"]
    assert listed == [{"id": "d:titles", "columns": ["page", "title", "length"], "rows": 2,
                       "doc": "d1", "version": 0, "stale": False}]


# -- stale, never refreshed ----------------------------------------------------------------------


def test_a_change_to_the_source_makes_it_stale_and_naming_it_warns(toolbox, session):
    call(toolbox, session, "ds_derive", doc="d1", id="titles")
    entry = session.dataset("d:titles")
    rows = entry.dataset.rows
    retitle(toolbox, session, "New title")
    assert entry.stale and entry.dataset.rows == rows          # never refreshed
    read = call(toolbox, session, "ds_read", **{"from": "d:titles"})
    assert read.data["stale"]
    assert read.warnings == ["d:titles is stale: d1 has changed since it was derived "
                             "(version 0, now 1); derive it again for current values"]


def test_a_change_to_another_document_leaves_it_current(toolbox, session):
    call(toolbox, session, "ds_derive", doc="d1", id="titles")
    call(toolbox, session, "ds_insert", doc="d2", items=[{"from": "d:titles", "column": "page"}])
    assert not session.dataset("d:titles").stale


def test_undoing_the_change_makes_it_current_again_and_redo_stale(toolbox, session):
    """Equal versions are equal content (the history's rule): back at the version it was
    derived from, the dataset is current -- nothing was recomputed."""
    retitle(toolbox, session, "First")                         # version 1
    call(toolbox, session, "ds_derive", doc="d1", id="titles")
    entry = session.dataset("d:titles")
    assert entry.version == 1
    retitle(toolbox, session, "Second")                        # version 2
    assert entry.stale
    assert call(toolbox, session, "undo", doc="d1").ok
    assert not entry.stale and session.entry("d1").version == 1
    assert call(toolbox, session, "undo", doc="d1", redo=True).ok
    assert entry.stale
    assert call(toolbox, session, "undo", doc="d1", steps=2).ok   # before it was derived
    assert entry.stale and session.entry("d1").version == 0


def test_a_scoped_undo_leaves_it_stale(toolbox, session):
    """A scoped undo makes a state that never existed: a new version, so stale."""
    retitle(toolbox, session, "One", page=1)
    retitle(toolbox, session, "Two", page=2)
    call(toolbox, session, "ds_derive", doc="d1", id="titles")
    entry = session.dataset("d:titles")
    assert not entry.stale
    assert call(toolbox, session, "undo", doc="d1", scope="1").ok
    assert entry.stale


def test_a_dataset_outlives_closing_its_source_as_it_was(toolbox, session):
    call(toolbox, session, "ds_derive", doc="d1", id="current")
    call(toolbox, session, "ds_derive", doc="d1", id="old")
    session.dataset("d:old").version = -5          # as if derived from an earlier state
    closed = call(toolbox, session, "close_document", doc="d1")
    assert closed.ok and closed.data == {"datasets": ["d:current", "d:old"]}
    assert "stay" in closed.summary
    current, old = session.dataset("d:current"), session.dataset("d:old")
    assert current.closed and not current.stale and current.source_version is None
    assert old.closed and old.stale
    read = call(toolbox, session, "ds_read", **{"from": "d:old"})
    assert read.ok and read.data["rows"][0]["page"] == 1
    assert read.warnings == ["d:old is stale: d1 changed after it was derived, before it "
                             "was closed"]
    assert session.describe()["datasets"][0]["closed"] is True
    # A new document gets a new handle: nothing ties it to the closed one's datasets.
    session.open(synthetic.outer_package(), "deck.pptx")
    assert "d1" not in session.documents and not current.stale


# -- batches -------------------------------------------------------------------------------------


def test_a_batch_names_a_dataset_an_earlier_op_defined(toolbox, session):
    result = call(toolbox, session, "batch", ops=[
        {"tool": "ds_derive", "arguments": {"doc": "d1", "id": "titles"}},
        {"tool": "ds_insert", "arguments": {"doc": "d2",
                                            "items": [{"from": "d:titles", "column": "title"}]}},
    ])
    assert result.ok, result.to_json()
    assert result.to_json()["datasets"] == ["d:titles"]
    assert not session.dataset("d:titles").stale


def test_a_dataset_derived_before_a_later_op_changed_its_source_is_stale_from_the_start(
        toolbox, session):
    result = call(toolbox, session, "batch", ops=[
        {"tool": "ds_derive", "arguments": {"doc": "d1", "id": "before"}},
        {"tool": "toy_ppt_set_title", "arguments": {"doc": "d1", "page": 1, "text": "X"}},
        {"tool": "ds_derive", "arguments": {"doc": "d1", "id": "after"}},
    ])
    assert result.ok, result.to_json()
    before, after = session.dataset("d:before"), session.dataset("d:after")
    assert before.stale and not after.stale
    assert before.version == after.version == session.entry("d1").version == 1
    assert result.warnings == ["d:before is stale: d1 was changed after it was derived in "
                               "the same batch; derive it again for current values"]
    assert call(toolbox, session, "undo", doc="d1").ok
    assert before.stale and after.stale       # the state it came from never had a version


def test_a_failed_atomic_batch_defines_nothing(toolbox, session):
    ops = [{"tool": "ds_derive", "arguments": {"doc": "d1", "id": "titles"}},
           {"tool": "ds_insert", "arguments": {"doc": "d2",
                                               "items": [{"from": "d:nope", "column": "title"}]}}]
    result = call(toolbox, session, "batch", ops=ops, atomic=True)
    assert result.error.code == "not_found"
    assert result.error.field == "ops[1].arguments.items[0].from"
    assert result.error.valid_options == ["d:titles"]
    assert not session.datasets
    # By default the op that applied keeps its dataset; the failing one names what exists.
    result = call(toolbox, session, "batch", ops=ops)
    assert result.ok and result.data["failed"] == [1]
    assert result.data["ops"][1]["error"]["field"] == "arguments.items[0].from"
    assert result.data["ops"][1]["error"]["valid_options"] == ["d:titles"]
    assert [entry.address for entry in session.datasets.values()] == ["d:titles"]


# -- limits --------------------------------------------------------------------------------------


def test_a_dataset_over_the_cell_limit_is_refused(toolbox):
    session = toolbox.session(limits=Limits(max_dataset_cells=5))
    session.open(synthetic.outer_package(), "deck.pptx")
    result = call(toolbox, session, "ds_derive", doc="d1", id="titles")     # 2 x 3 = 6
    assert result.error.code == "limit" and not session.datasets


def test_the_session_keeps_at_most_max_datasets_and_an_id_may_be_reused(toolbox):
    session = toolbox.session(limits=Limits(max_datasets=2))
    session.open(synthetic.outer_package(), "deck.pptx")
    assert call(toolbox, session, "ds_derive", doc="d1", id="a").ok
    assert call(toolbox, session, "ds_derive", doc="d1", id="b").ok
    full = call(toolbox, session, "ds_derive", doc="d1", id="c")
    assert full.error.code == "limit" and full.error.valid_options == ["d:a", "d:b"]
    assert call(toolbox, session, "ds_derive", doc="d1", id="a").ok
    session.drop_dataset("d:a")
    assert call(toolbox, session, "ds_derive", doc="d1", id="c").ok
    assert session.dataset_ids() == ["d:b", "d:c"]


# -- concurrency ---------------------------------------------------------------------------------


def test_parallel_calls_define_and_name_datasets(toolbox, session):
    results = toolbox.dispatch_many(session, [
        ("ds_derive", {"doc": "d1", "id": "deck"}),
        ("ds_derive", {"doc": "d2", "id": "text"}),
        ("toy_ppt_set_title", {"doc": "d1", "page": 1, "text": "After"}),
        ("ds_read", {"from": "d:deck"}),
    ])
    assert all(result.ok for result in results), [r.to_json() for r in results]
    assert session.dataset("d:deck").stale and not session.dataset("d:text").stale
    assert results[3].data["stale"]


def test_a_dataset_entry_names_its_address():
    entry = DatasetEntry("x", Dataset(["a"], [(1,)]))
    assert entry.address == DATASET_PREFIX + "x" and not entry.stale and not entry.closed


# -- from_records ----------------------------------------------------------------------------------


def test_from_records_reads_mappings_by_name_in_the_order_first_met():
    data = Dataset.from_records([{"region": "North", "opened": dt.date(2026, 3, 1)},
                                 {"opened": "2026-04-15", "total": 0.13}],
                                types={"opened": "date"})
    assert data.columns == ("region", "opened", "total")
    assert data.rows == (("North", dt.date(2026, 3, 1), None), (None, "2026-04-15", 0.13))
    assert data.types == (None, "date", None) and data.sources is None and data.keys == 0


def test_from_records_takes_columns_and_a_label_per_column():
    data = Dataset.from_records([("North", 3), ("South", 5)], ("text", "number"),
                                columns=("region", "units"), keys=1)
    assert data.rows == (("North", 3), ("South", 5)) and data.keys == 1
    assert data.types == ("text", "number")
    picked = Dataset.from_records([{"a": 1, "b": 2, "c": 3}], columns=["c", "a"])
    assert picked.columns == ("c", "a") and picked.rows == ((3, 1),)


@pytest.mark.parametrize("rows, options, words", [
    ([("North", 3)], {}, "need columns"),
    ([{"a": 1}], {"columns": ["a", "b"]}, "no record has 'b'"),
    ([{"a": 1}], {"types": {"b": "date"}}, "'b', which is no column"),
    ([{"a": 1}], {"types": {"a": "Date"}}, "'Date' is not a type label"),
    ([{"a": 1}], {"types": ("date", "text")}, "2 type labels for 1 columns"),
    ([{"a": 1}], {"types": "date"}, "types is a mapping"),
    ([("North",)], {"columns": ["a", "b"]}, "1 values for 2 columns"),
    ([], {}, "give columns"),
])
def test_from_records_refuses_what_it_cannot_read(rows, options, words):
    with pytest.raises(ValueError, match=words):
        Dataset.from_records(rows, **options)


def test_the_type_labels():
    from ooxml_edit.tools import TYPE_LABELS
    assert TYPE_LABELS == ("number", "percent", "date", "time", "datetime", "text", "bool",
                           "error")
    data = Dataset.from_records([{"when": dt.datetime(2026, 3, 1, 14, 5)}])
    assert pickle.loads(pickle.dumps(data)) == data
