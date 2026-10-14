"""Provenance in the saved file: the record format, the chart part's copy, the format hooks.

The toys: a chart document over the synthetic chart package (``charts_synthetic.py``) whose
``edit_chart`` handler edits real chart parts through :mod:`ooxml_edit.charts` and keeps the
records the format is handed as an attribute of the chart's frame; and a grid whose tables
resolve ``t:<name>`` to rows with a source per cell (``G!B2``).  Every figure is made up.
"""

from __future__ import annotations

import datetime as dt

import pytest
from lxml import etree

from ooxml_edit import provenance
from ooxml_edit.charts import Chart, PROVENANCE_URI, add_chart
from ooxml_edit.charts import provenance as chart_provenance
from ooxml_edit.charts.record import read_record, write_record
from ooxml_edit.tools import (Dataset, DocumentFormat, Result, ToolError, Toolbox, shared,
                              string, strict_problems, tool)
from ooxml_edit.xml import qn

import charts_synthetic as cs

CLOCK = lambda: dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.timezone.utc)  # noqa: E731
C = cs.C

# -- the record format ---------------------------------------------------------------------------

PLACED = {"source": "d2/t:sales", "doc": "d2", "version": 3,
          "categories": {"column": "quarter", "rows": "1-4", "sources": ["G!A2:A5"]},
          "series": [{"name": "north", "column": "north", "rows": "1-4",
                      "sources": ["G!B2:B5"]}]}


def test_a_record_is_the_placement_without_what_only_the_session_knows():
    found = provenance.record(PLACED, document="made-up-budget.ods")
    assert found == {"v": 1, "source": "t:sales", "document": "made-up-budget.ods",
                     "version": 3, "categories": PLACED["categories"],
                     "series": PLACED["series"]}
    assert list(found)[:3] == ["v", "source", "document"]


def test_a_record_leaves_out_none_and_keeps_a_dataset_source_as_it_is():
    found = provenance.record({"source": "d:by_region", "doc": None, "version": None,
                               "stale": True, "header_row": None, "first_row": 0,
                               "columns": [{"col": 0, "column": "k", "rows": "1"}]})
    assert found == {"v": 1, "source": "d:by_region", "stale": True, "first_row": 0,
                     "columns": [{"col": 0, "column": "k", "rows": "1"}]}


def test_dumps_is_compact_and_loads_reads_it_back():
    found = provenance.record(PLACED, document="begroting — made-up.ods")
    text = provenance.dumps(found)
    assert " " not in text.replace("made-up", "").replace("begroting — ", "")
    assert text.startswith('{"v":1,"source":"t:sales","document":"begroting — made-up.ods"')
    assert provenance.loads(text) == found
    assert provenance.loads(text.encode()) == found


@pytest.mark.parametrize("text", ["", "not json", "[1]", '{"source":"t:a"}', '{"v":0}',
                                  '{"v":"1"}', '{"v":true}', '{"v":1.5}'])
def test_loads_refuses_what_is_not_a_record(text):
    with pytest.raises(provenance.RecordError):
        provenance.loads(text)


def test_a_later_version_is_read_as_it_is():
    assert provenance.loads('{"v":2,"x":1}') == {"v": 2, "x": 1}


def test_a_placement_names_its_source():
    with pytest.raises(provenance.RecordError):
        provenance.record({"doc": "d2"})


# -- holders -------------------------------------------------------------------------------------


def _holder(key, kept):
    return provenance.Holder(key, store=lambda found: kept.__setitem__(key, found),
                             read=lambda: kept.get(key))


def test_watch_collects_each_holder_once_and_note_outside_does_nothing():
    kept: dict = {}
    provenance.note(_holder("a", kept))  # nobody watching
    with provenance.watch() as used:
        provenance.note(_holder("a", kept))
        provenance.note(_holder("b", kept))
        provenance.note(_holder("a", kept))
    assert [holder.key for holder in used] == ["a", "b"]


def test_an_inner_watch_collects_for_itself_and_for_the_outer_one():
    kept: dict = {}
    with provenance.watch() as outer:
        provenance.note(_holder("a", kept))
        with provenance.watch() as inner:
            provenance.note(_holder("b", kept))
    assert [h.key for h in inner] == ["b"]
    assert [h.key for h in outer] == ["a", "b"]


# -- the chart part's copy -----------------------------------------------------------------------

RECORD = {"v": 1, "source": "t:sales", "document": "made-up-budget.ods", "version": 1}


def _space(ext_list: str = "") -> etree._Element:
    return etree.fromstring(
        f'<c:chartSpace xmlns:c="{C}"><c:chart/><c:externalData/>{ext_list}</c:chartSpace>')


def _uris(root) -> list[str]:
    return [ext.get("uri") for ext in root.iter(f"{{{C}}}ext")]


def test_a_record_goes_last_in_a_new_extension_list():
    root = _space()
    assert write_record(root, RECORD)
    assert [etree.QName(child).localname for child in root] == ["chart", "externalData",
                                                                "extLst"]
    assert _uris(root) == [PROVENANCE_URI]
    assert read_record(root) == RECORD


def test_a_record_follows_the_extensions_there_and_is_replaced_where_it_stands():
    root = _space('<c:extLst><c:ext uri="{00000000-0000-4000-8000-0000000000A1}"/>'
                  '</c:extLst>')
    write_record(root, RECORD)
    assert _uris(root) == ["{00000000-0000-4000-8000-0000000000A1}", PROVENANCE_URI]
    root.find(f"{{{C}}}extLst").append(etree.fromstring(
        f'<c:ext xmlns:c="{C}" uri="{{00000000-0000-4000-8000-0000000000A2}}"/>'))
    assert write_record(root, {**RECORD, "version": 2})
    assert _uris(root) == ["{00000000-0000-4000-8000-0000000000A1}", PROVENANCE_URI,
                           "{00000000-0000-4000-8000-0000000000A2}"]
    assert read_record(root)["version"] == 2
    assert not write_record(root, {**RECORD, "version": 2})  # the same record: no change


def test_the_record_element_is_in_its_own_namespace_with_the_json_in_an_attribute():
    root = _space()
    write_record(root, RECORD)
    node = next(root.iter(f"{{{provenance_ns()}}}record"))
    assert provenance.loads(node.get("json")) == RECORD
    assert node.nsmap.get("uvp") == provenance_ns()


def provenance_ns() -> str:
    from ooxml_edit.charts.record import PROVENANCE_NS

    return PROVENANCE_NS


def test_removing_the_record_drops_a_list_it_leaves_empty():
    root = _space()
    write_record(root, RECORD)
    assert write_record(root, None)
    assert root.find(f"{{{C}}}extLst") is None
    assert not write_record(root, None)
    kept = _space('<c:extLst><c:ext uri="{00000000-0000-4000-8000-0000000000A1}"/>'
                  '</c:extLst>')
    write_record(kept, RECORD)
    write_record(kept, None)
    assert _uris(kept) == ["{00000000-0000-4000-8000-0000000000A1}"]


def test_a_record_that_cannot_be_read_is_none():
    root = _space(f'<c:extLst><c:ext uri="{PROVENANCE_URI}"><uvp:record '
                  f'xmlns:uvp="{provenance_ns()}" json="{{oops"/></c:ext></c:extLst>')
    assert read_record(root) is None


def test_provenance_reads_a_chart_a_part_and_a_package():
    opened = cs.Opened(cs.package(cs.charts()[:2], diagrams=False))
    chart = opened.chart("bar")
    assert chart_provenance(chart) is None
    with opened.history.batch():
        from ooxml_edit.charts import store_provenance
        store_provenance(opened.package, chart.part, RECORD)
    assert chart_provenance(chart) == RECORD
    assert chart_provenance(opened.package, chart.part) == RECORD
    assert chart_provenance(opened.package) == {chart.part: RECORD}
    assert chart_provenance(opened) == {chart.part: RECORD}  # anything with a package
    opened.history.undo()
    assert chart_provenance(chart) is None
    with pytest.raises(TypeError):
        chart_provenance(42)


def test_a_chart_notes_its_part_and_add_chart_the_new_one():
    opened = cs.Opened(cs.package(cs.charts()[:2], diagrams=False))
    with provenance.watch() as used:
        opened.chart("bar").categories
    assert len(used) == 1 and used[0].key[1] == opened.chart("bar").part
    with provenance.watch() as used, opened.history.batch():
        made = add_chart(opened.package, cs.MAIN, "column", ["A"], [{"name": "s",
                                                                     "values": [1]}])
    assert [holder.key[1] for holder in used] == [made.part]


# -- the toys: a chart document and a grid -------------------------------------------------------


class ChartBook:
    """The synthetic chart package as a document: frames by name, one undo step a batch."""

    def __init__(self) -> None:
        self.opened = cs.Opened(cs.package(cs.charts()[:1], diagrams=False))
        self.package = self.opened.package
        self.history = self.opened.history
        self.added = 0

    def batch(self):
        return self.history.batch()

    def frames(self) -> list:
        return list(self.package.tree(cs.MAIN).iter(qn("tst:frame")))

    def frame(self, name: str):
        found = [frame for frame in self.frames() if frame.get("name") == name]
        if not found:
            raise ToolError("not_found", f"no chart {name}",
                            valid_options=[f.get("name") for f in self.frames()])
        return found[0]

    def chart(self, name: str) -> Chart:
        self.frame(name)
        return self.opened.chart(name)


def _store(book: ChartBook, address: str, found) -> None:
    frame = book.frame(address)
    if found is None:
        frame.attrib.pop("prov", None)
    else:
        frame.set("prov", provenance.dumps(found))
    book.package.mark_dirty(cs.MAIN)


def _read(book: ChartBook) -> dict:
    return {frame.get("name"): provenance.loads(frame.get("prov"))
            for frame in book.frames() if frame.get("prov")}


BOOK = DocumentFormat(kind="pptx", open=lambda data: ChartBook(), detect=lambda d, n: False,
                      store_provenance=_store, read_provenance=_read)
PLAIN = DocumentFormat(kind="docx", open=lambda data: ChartBook(), detect=lambda d, n: False)


def _chart_json(chart: Chart) -> dict:
    return {"categories": [str(c) for c in chart.categories],
            "series": [{"name": s.name, "values": list(s.values)} for s in chart.series]}


def toy_chart(call, doc, target, action, **arguments):
    book: ChartBook = call.document
    if action == "add":
        made = add_chart(book.package, cs.MAIN, arguments["chart_type"],
                         arguments["categories"], arguments["data"])
        book.added += 1
        name = f"new{book.added}"
        frame = etree.SubElement(book.package.tree(cs.MAIN), qn("tst:frame"), name=name)
        frame.append(made.graphic())
        book.package.mark_dirty(cs.MAIN)
        return Result(summary=f"Added {name}", created=["p:before", name],
                      data=_chart_json(book.chart(name)))
    chart = book.chart(target)
    if action == "read":
        return Result(summary="Read", data=_chart_json(chart))

    def series():
        which = arguments["series"]
        names = [s.name for s in chart.series]
        return chart.series[int(which)] if which not in names else chart.series_named(which)

    if action == "set_values":
        series().set_values(arguments["values"])
    elif action == "set_value":
        series().set_value(int(arguments["category"]), arguments["value"])
    elif action == "add_series":
        chart.add_series(arguments["text"], arguments["values"])
    elif action == "rename_series":
        series().set_name(arguments["text"])
    elif action == "remove_series":
        chart.remove_series(series().index)
    elif action == "add_category":
        chart.add_category(arguments["text"], arguments["values"])
    elif action == "rename_category":
        chart.set_category(int(arguments["category"]), arguments["text"])
    elif action == "set_title":
        chart.set_title(arguments.get("text"))
    return Result(summary=action, changed=[target], data=_chart_json(book.chart(target)))


EDIT_CHART = shared.handler("edit_chart", kind="pptx")(toy_chart)
EDIT_CHART_PLAIN = shared.handler("edit_chart", kind="docx")(toy_chart)


class Grid:
    def __init__(self, tables: dict) -> None:
        self.tables = tables


def _table(grid: Grid, address: str) -> Dataset:
    header, rows = grid.tables[address[2:]]
    sources = [[(f"G!{chr(65 + c)}{r + 2}",) for c in range(len(header))]
               for r in range(len(rows))]
    return Dataset(header, rows, sources=sources, keys=1)


def _merge(addresses):
    out: list[list] = []
    for address in addresses:
        sheet, cell = address.split("!")
        column, row = cell[0], int(cell[1:])
        last = out[-1] if out else None
        if last and last[1] == column and last[3] == row - 1:
            last[3] = row
        else:
            out.append([sheet, column, row, row])
    return [f"{s}!{c}{a}" if a == b else f"{s}!{c}{a}:{c}{b}" for s, c, a, b in out]


GRID = DocumentFormat(kind="xlsx", open=lambda data: Grid({}), detect=lambda d, n: False,
                      datasets={"t:": _table}, merge_sources=_merge)

SALES = (("quarter", "north", "east"),
         [("Q1", 1, 5), ("Q2", 2, 6), ("Q3", 3, 7), ("Q4", 4, 8)])


@tool("toy_place", "Place a made-up table's rows (a format's own table tool).",
      {"doc": string("Document."), "target": string("Chart."), "from": string("Rows.")},
      mutates=True, sources=("from",))
def toy_place(call, doc, target, **given):
    rows = call.source(given["from"], field="from")
    placed = {**rows.describe(), "first_row": 0,
              "columns": [{"col": 0, **rows.provenance(0, [0, 1])}]}
    found = call.keep_provenance(target, rows, placed)
    return Result(summary="placed", changed=[target], data={"from": placed, "record": found})


@tool("toy_peek", "Read (a reading tool).", {"doc": string("Document."),
                                              "from": string("Rows.")}, sources=("from",))
def toy_peek(call, doc, **given):
    rows = call.source(given["from"], field="from")
    call.keep_provenance("bar", rows, rows.describe())


TOOLS = [EDIT_CHART, EDIT_CHART_PLAIN, toy_place, toy_peek, *shared.SESSION_TOOLS]


@pytest.fixture
def toolbox():
    with Toolbox(TOOLS, formats=[BOOK, PLAIN, GRID], groups=shared.GROUPS, workers=0) as box:
        yield box


@pytest.fixture
def session(toolbox):
    session = toolbox.session(clock=CLOCK)
    session.adopt(ChartBook(), "pptx", "charts.toy")                          # d1
    session.adopt(Grid({"sales": SALES}), "xlsx", "made-up-budget.ods")       # d2
    return session


def call(toolbox, session, name, **arguments):
    return toolbox.dispatch(session, name, arguments)


def ok(result):
    assert result.ok, result.to_json()
    return result


def book(session, doc="d1") -> ChartBook:
    return session.entry(doc).document


# -- edit_chart keeps its placement --------------------------------------------------------------


def test_add_from_keeps_the_record_in_the_chart_part_and_with_the_format(toolbox, session):
    result = ok(call(toolbox, session, "edit_chart", doc="d1", target="p:x", action="add",
                     chart_type="column", **{"from": "t:sales"}))
    expected = provenance.record(result.data["from"], document="made-up-budget.ods")
    assert expected == {
        "v": 1, "source": "t:sales", "document": "made-up-budget.ods", "version": 0,
        "categories": {"column": "quarter", "rows": "1-4", "sources": ["G!A2:A5"]},
        "series": [{"name": "north", "column": "north", "rows": "1-4", "sources": ["G!B2:B5"]},
                   {"name": "east", "column": "east", "rows": "1-4", "sources": ["G!C2:C5"]}]}
    chart = book(session).chart("new1")
    assert chart_provenance(chart) == expected
    assert session.provenance("d1") == {"new1": expected}
    # The result's from is unchanged: the session's document handle, not the record.
    assert result.data["from"]["doc"] == "d2" and result.data["from"]["source"] == "d2/t:sales"


def test_the_record_is_part_of_the_calls_one_undo_step(toolbox, session):
    before = session.entry("d1").version
    ok(call(toolbox, session, "edit_chart", doc="d1", target="p:x", action="add",
            chart_type="column", **{"from": "t:sales"}))
    assert session.entry("d1").version == before + 1
    session.undo("d1")
    assert chart_provenance(book(session).package) == {}
    assert session.provenance("d1") == {}


def _place(toolbox, session, action, columns, source="t:sales", **arguments):
    return ok(call(toolbox, session, "edit_chart", doc="d1", target="bar", action=action,
                   columns=columns, **{"from": source}, **arguments))


def _edit(toolbox, session, action, **arguments):
    return ok(call(toolbox, session, "edit_chart", doc="d1", target="bar", action=action,
                   **arguments))


def _bar(session):
    return chart_provenance(book(session).chart("bar"))


NORTH = {"name": "North", "column": "north", "rows": "1-4", "sources": ["G!B2:B5"]}
EAST = {"name": "east", "column": "east", "rows": "1-4", "sources": ["G!C2:C5"]}
QUARTERS = {"column": "quarter", "rows": "1-4", "sources": ["G!A2:A5"]}


def test_add_series_adds_its_entry_and_keeps_the_others(toolbox, session):
    _place(toolbox, session, "set_values", ["quarter", "north"], series="North")
    _place(toolbox, session, "add_series", ["quarter", "east"])
    found = _bar(session)
    assert found["categories"] == QUARTERS and found["series"] == [NORTH, EAST]
    assert found["source"] == "t:sales"
    root = book(session).package.tree(book(session).chart("bar").part)
    assert _uris(root).count(PROVENANCE_URI) == 1
    assert session.provenance("d1")["bar"] == found      # the format's copy is the same
    assert book(session).chart("bar").series_named("east").values == [5, 6, 7, 8]


def test_set_values_again_replaces_that_series_entry_where_it_stands(toolbox, session):
    _place(toolbox, session, "set_values", ["quarter", "north"], series="North")
    _place(toolbox, session, "set_values", ["quarter", "east"], series="South")
    _place(toolbox, session, "set_values", ["quarter", "east"], series="North")
    assert [entry["name"] for entry in _bar(session)["series"]] == ["North", "South"]
    assert _bar(session)["series"][0]["column"] == "east"


def _with_dataset(toolbox_session, run):
    @tool("ds_keep", "Keep a made-up dataset.", {"id": string("Its id.")}, documents=())
    def ds_keep(call, id):
        call.define_dataset(id, Dataset(("quarter", "west"), [("Q1", 9), ("Q2", 9),
                                                              ("Q3", 9), ("Q4", 9)], keys=1),
                            source="d2")
        return Result(summary="kept")

    with Toolbox([*TOOLS, ds_keep], formats=[BOOK, PLAIN, GRID], groups=shared.GROUPS,
                 workers=0) as box:
        ok(call(box, toolbox_session, "ds_keep", id="w"))
        run(box)


def test_an_entry_from_another_source_names_its_own(session):
    def run(box):
        _place(box, session, "set_values", ["quarter", "north"], series="North")
        _place(box, session, "add_series", ["quarter", "west"], source="d:w")
        found = _bar(session)
        assert found["source"] == "t:sales" and found["series"][0] == NORTH
        assert found["series"][1] == {"name": "west", "source": "d:w",
                                      "document": "made-up-budget.ods", "version": 0,
                                      "column": "west", "rows": "1-4"}
        # set_values from the dataset: its origin becomes the record's, and the entry from
        # the table now names the table.
        _place(box, session, "set_values", ["quarter", "west"], source="d:w", series="South")
        found = _bar(session)
        assert found["source"] == "d:w" and "sources" not in found["categories"]
        assert found["series"][0] == {"name": "North", "source": "t:sales",
                                      "document": "made-up-budget.ods", "version": 0,
                                      **{k: v for k, v in NORTH.items() if k != "name"}}
        assert [e["name"] for e in found["series"]] == ["North", "west", "South"]
        assert "source" not in found["series"][1] and "source" not in found["series"][2]

    _with_dataset(session, run)


def test_rename_series_renames_its_entry_by_name_or_position(toolbox, session):
    _place(toolbox, session, "set_values", ["quarter", "north"], series="North")
    _edit(toolbox, session, "rename_series", series="North", text="Noord")
    assert _bar(session)["series"] == [{**NORTH, "name": "Noord"}]
    _edit(toolbox, session, "rename_series", series="0", text="N")
    assert _bar(session)["series"] == [{**NORTH, "name": "N"}]
    assert session.provenance("d1")["bar"]["series"][0]["name"] == "N"


def test_remove_series_drops_its_entry(toolbox, session):
    _place(toolbox, session, "set_values", ["quarter", "north"], series="North")
    _place(toolbox, session, "add_series", ["quarter", "east"])
    _edit(toolbox, session, "remove_series", series="North")
    assert _bar(session)["series"] == [EAST]
    assert session.provenance("d1")["bar"]["series"] == [EAST]


def test_values_set_without_from_drop_the_series_entry(toolbox, session):
    _place(toolbox, session, "set_values", ["quarter", "north"], series="North")
    _place(toolbox, session, "add_series", ["quarter", "east"])
    _edit(toolbox, session, "set_value", series="east", category="0", value=42)
    assert _bar(session)["series"] == [NORTH]
    _edit(toolbox, session, "set_values", series="0", values=[1, 1, 1, 1])
    # The categories still came from the table: the record says that much.
    assert _bar(session) == {"v": 1, "source": "t:sales", "document": "made-up-budget.ods",
                             "version": 0, "categories": QUARTERS}
    assert session.provenance("d1")["bar"] == _bar(session)


def test_category_edits_without_from_drop_what_they_make_stale(toolbox, session):
    _place(toolbox, session, "set_values", ["quarter", "north"], series="North")
    _edit(toolbox, session, "rename_category", category="0", text="Jan")
    assert "categories" not in _bar(session) and _bar(session)["series"] == [NORTH]
    _edit(toolbox, session, "add_category", text="Q5", values=[1, 2])
    assert _bar(session) is None
    assert session.provenance("d1") == {}
    session.undo("d1")
    assert _bar(session)["series"] == [NORTH]


def test_add_replaces_the_whole_record(toolbox, session):
    result = ok(call(toolbox, session, "edit_chart", doc="d1", target="p:x", action="add",
                     chart_type="column", columns=["quarter", "east"], **{"from": "t:sales"}))
    name = result.created[-1]
    assert chart_provenance(book(session).chart(name))["series"] == [
        {**EAST, "name": "east"}]


def test_read_returns_the_chart_parts_record_as_from(toolbox, session):
    plain = ok(call(toolbox, session, "edit_chart", doc="d1", target="bar", action="read"))
    assert "from" not in plain.data
    ok(call(toolbox, session, "edit_chart", doc="d1", target="bar", action="set_values",
            series="North", columns=["quarter", "north"], **{"from": "t:sales"}))
    read = ok(call(toolbox, session, "edit_chart", doc="d1", target="bar", action="read"))
    assert read.data["from"] == chart_provenance(book(session).chart("bar"))
    assert read.data["from"]["v"] == 1 and "doc" not in read.data["from"]


def test_an_edit_without_from_leaves_the_record(toolbox, session):
    ok(call(toolbox, session, "edit_chart", doc="d1", target="bar", action="set_values",
            series="North", columns=["quarter", "north"], **{"from": "t:sales"}))
    kept = chart_provenance(book(session).chart("bar"))
    ok(call(toolbox, session, "edit_chart", doc="d1", target="bar", action="set_title",
            text="Made up"))
    assert chart_provenance(book(session).chart("bar")) == kept


def test_a_format_without_the_hook_still_gets_the_chart_parts_copy(toolbox, session):
    session.adopt(ChartBook(), "docx", "plain.toy")                         # d3
    ok(call(toolbox, session, "edit_chart", doc="d3", target="bar", action="set_values",
            series="North", columns=["quarter", "north"], **{"from": "t:sales"}))
    assert chart_provenance(book(session, "d3").chart("bar"))["source"] == "t:sales"
    assert session.provenance("d3") == {}


def test_a_refused_placement_keeps_nothing(toolbox, session):
    result = call(toolbox, session, "edit_chart", doc="d1", target="bar", action="set_values",
                  series="Nobody", columns=["quarter", "north"], **{"from": "t:sales"})
    assert not result.ok
    assert chart_provenance(book(session).package) == {}
    assert session.provenance("d1") == {}


def test_a_batch_keeps_the_record_in_its_one_step(toolbox, session):
    before = session.entry("d1").version
    ok(call(toolbox, session, "batch", ops=[
        {"tool": "edit_chart", "arguments": {"doc": "d1", "target": "bar",
                                             "action": "set_values", "series": "North",
                                             "from": "t:sales",
                                             "columns": ["quarter", "north"]}},
        {"tool": "edit_chart", "arguments": {"doc": "d1", "target": "bar",
                                             "action": "set_title", "text": "Made up"}}]))
    assert session.entry("d1").version == before + 1
    assert session.provenance("d1")["bar"]["source"] == "t:sales"
    session.undo("d1")
    assert session.provenance("d1") == {}
    assert chart_provenance(book(session).package) == {}


def test_a_dataset_placement_names_the_dataset_and_its_document(toolbox, session):
    from ooxml_edit.tools import DatasetEntry  # noqa: F401  (the dataset path)

    @tool("ds_keep", "Keep a made-up dataset.", {"id": string("Its id.")}, documents=())
    def ds_keep(call, id):
        header, rows = SALES
        call.define_dataset(id, Dataset(header, rows, keys=1), source="d2")
        return Result(summary="kept")

    with Toolbox([*TOOLS, ds_keep], formats=[BOOK, PLAIN, GRID], groups=shared.GROUPS,
                 workers=0) as box:
        ok(call(box, session, "ds_keep", id="q"))
        ok(call(box, session, "edit_chart", doc="d1", target="bar", action="set_values",
                series="North", columns=["quarter", "north"], **{"from": "d:q"}))
    found = session.provenance("d1")["bar"]
    assert found["source"] == "d:q" and found["document"] == "made-up-budget.ods"
    assert "sources" not in found["categories"] or found["categories"]["sources"] == []


# -- a format's own tool -------------------------------------------------------------------------


def test_keep_provenance_hands_the_record_to_the_format(toolbox, session):
    result = ok(call(toolbox, session, "toy_place", doc="d1", target="bar",
                     **{"from": "t:sales"}))
    assert result.data["record"] == {"v": 1, "source": "t:sales",
                                     "document": "made-up-budget.ods", "version": 0,
                                     "first_row": 0,
                                     "columns": [{"col": 0, "column": "quarter",
                                                  "rows": "1-2", "sources": ["G!A2:A3"]}]}
    assert session.provenance("d1") == {"bar": result.data["record"]}
    session.undo("d1")
    assert session.provenance("d1") == {}


def test_keep_provenance_is_for_a_changing_call(toolbox, session):
    result = call(toolbox, session, "toy_peek", doc="d1", **{"from": "t:sales"})
    assert not result.ok and result.error.code == "internal"


# -- definitions ---------------------------------------------------------------------------------


def test_edit_chart_definition_is_unchanged_by_reading_the_record():
    chart = shared.definition("edit_chart")
    assert "provenance" not in str(chart.schema).lower()
    assert strict_problems([chart]) == []
