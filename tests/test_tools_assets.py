"""An application's asset catalogue: the folder-and-index catalogue, the helpers tools use,
and tools a format offers only by its configuration (``DocumentFormat.tools``).

The icons are made up here: a few hand-written SVGs and made-up keywords."""

from __future__ import annotations

import dataclasses
import json

import pytest

from ooxml_edit.tools import (Asset, AssetCatalogue, FolderCatalogue, Limits, Result,
                              ToolError, Toolbox, assets, integer, string, tool)

import synthetic
import tools_toys

SQUARE = (b'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
          b'viewBox="0 0 24 24"><path d="M2 2h20v20H2z"/></svg>')


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "shapes").mkdir()
    for name in ("square", "circle", "star"):
        (tmp_path / "shapes" / f"{name}.svg").write_bytes(SQUARE)
    (tmp_path / "index.json").write_text(json.dumps([
        {"id": "square", "file": "shapes/square.svg", "name": "Plain square",
         "keywords": ["box", "block"]},
        {"file": "shapes/circle.svg", "name": "Round dot", "keywords": "dot; ring, round"},
        {"id": "star-gold", "file": "shapes/star.svg", "keywords": ["award", "favourite"]},
    ]), encoding="utf-8")
    return tmp_path


def test_a_json_index_names_ids_names_and_keywords(folder):
    icons = FolderCatalogue(folder)
    assert len(icons) == 3
    assert isinstance(icons, AssetCatalogue)
    assert icons.search("round", 5) == [Asset("circle", "Round dot", ("dot", "ring", "round"))]
    assert [a.id for a in icons.search("star", 5)] == ["star-gold"]     # the id's words count
    assert icons.search("star-gold", 5)[0].name == "star-gold"          # the name defaults to the id
    assert icons.search("", 5) == [] and icons.search("nothing", 5) == []
    data, mime = icons.get("square")
    assert data == SQUARE and mime == "image/svg+xml"
    with pytest.raises(KeyError):
        icons.get("triangle")


def test_search_ranks_whole_words_then_starts_then_parts_and_needs_every_word(folder):
    icons = FolderCatalogue(folder)
    assert [a.id for a in icons.search("bo", 5)] == ["square"]          # the start of "box"
    assert [a.id for a in icons.search("avour", 5)] == ["star-gold"]    # part of "favourite"
    assert icons.search("ou", 5) == []                                   # parts of three letters or more
    assert icons.search("round box", 5) == []                            # every word must match
    assert [a.id for a in icons.search("d", 5)] == ["circle"]           # "dot"
    assert len(icons.search("o", 1)) <= 1


def test_a_csv_index_and_an_index_named_explicitly(tmp_path):
    (tmp_path / "a.svg").write_bytes(SQUARE)
    (tmp_path / "list.csv").write_text("file,name,keywords,id\na.svg,Arrow up,arrow;up,up\n",
                                       encoding="utf-8")
    icons = FolderCatalogue(tmp_path, "list.csv")
    assert icons.search("arrow", 3) == [Asset("up", "Arrow up", ("arrow", "up"))]


@pytest.mark.parametrize("entry, message", [
    ({"file": "../outside.svg"}, "inside the folder"),
    ({"file": "/etc/hosts"}, "inside the folder"),
    ({"name": "x"}, "no 'file'"),
    ({"file": "a.svg", "keywords": [1]}, "text"),
])
def test_an_index_entry_that_leaves_the_folder_or_lacks_a_file_is_refused(tmp_path, entry,
                                                                         message):
    (tmp_path / "index.json").write_text(json.dumps([entry]), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        FolderCatalogue(tmp_path)


def test_duplicate_ids_and_a_missing_index_are_refused(tmp_path):
    with pytest.raises(ValueError, match="no index"):
        FolderCatalogue(tmp_path)
    (tmp_path / "index.json").write_text(json.dumps([{"file": "a.svg"}, {"file": "b/a.svg"}]),
                                         encoding="utf-8")
    with pytest.raises(ValueError, match="used twice"):
        FolderCatalogue(tmp_path)


def test_a_link_out_of_the_folder_is_not_read(tmp_path):
    inside = tmp_path / "icons"
    inside.mkdir()
    (tmp_path / "secret.svg").write_bytes(SQUARE)
    try:
        (inside / "link.svg").symlink_to(tmp_path / "secret.svg")
    except OSError:
        pytest.skip("no symbolic links here")
    (inside / "index.json").write_text(json.dumps([{"file": "link.svg"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="outside"):
        FolderCatalogue(inside).get("link")


class Broken:
    def search(self, query, limit):
        raise RuntimeError("index offline")

    def get(self, asset_id):
        raise RuntimeError("store offline")


class Mapped:
    """An application's own catalogue answering mappings."""

    def search(self, query, limit):
        return [{"id": f"i{n}", "keywords": ("k",)} for n in range(100)]

    def get(self, asset_id):
        return {"pdf": (b"%PDF-1.7", "application/pdf"),
                "big": (b"x" * 11, "image/svg+xml")}.get(asset_id) or (SQUARE,
                                                                       "image/svg+xml")


def test_find_and_fetch_answer_tool_errors(folder):
    assert assets.find(Mapped(), "k", 200)[:1] == [{"id": "i0", "name": "i0", "keywords": ["k"]}]
    assert len(assets.find(Mapped(), "k", 200)) == assets.MAX_RESULTS
    with pytest.raises(ToolError) as error:
        assets.find(Broken(), "k", 5)
    assert error.value.code == "refused" and "index offline" in str(error.value)
    with pytest.raises(ToolError) as error:
        assets.fetch(FolderCatalogue(folder), "nope", search_tool="toy_find_asset")
    assert error.value.code == "not_found" and "toy_find_asset" in str(error.value)
    with pytest.raises(ToolError) as error:
        assets.fetch(Mapped(), "pdf")
    assert error.value.code == "invalid_arguments"
    with pytest.raises(ToolError) as error:
        assets.fetch(Mapped(), "big", Limits(max_blob_bytes=10))
    assert error.value.code == "limit"
    with pytest.raises(ToolError) as error:
        assets.fetch(Broken(), "x")
    assert error.value.code == "refused"
    assert assets.fetch(Mapped(), "any") == (SQUARE, "image/svg+xml")


# -- tools a format offers by its configuration ---------------------------------------------


def _configured(catalogue):
    @tool("toy_find_asset", "Search the asset catalogue.",
          {"query": string("Words."), "limit": integer("At most.", optional=True)},
          group="toy_render", documents=())
    def toy_find_asset(call, query, limit=10):
        return Result(summary="found", data={"assets": assets.find(catalogue, query, limit)})

    @tool("toy_ppt_set_title", "Set a page's title, or to an asset's name.",
          {"doc": string("Document id, e.g. d1."), "page": integer("Page.", minimum=1),
           "text": string("Title.", optional=True),
           "asset": string("Asset id.", optional=True)},
          kind="pptx", mutates=True, exactly_one=[("text", "asset")])
    def with_asset(call, doc, page, text=None, asset=None):
        if asset is not None:
            assets.fetch(catalogue, asset, search_tool="toy_find_asset")
            text = asset
        call.document.set_title(page, text)
        return Result(summary="titled")

    return dataclasses.replace(tools_toys.DECK, tools=(toy_find_asset, with_asset))


def test_a_formats_configured_tools_are_offered_only_with_it(folder):
    deck = _configured(FolderCatalogue(folder))
    with Toolbox(tools_toys.TOOLS, formats=[tools_toys.DECK, tools_toys.TEXT],
                 groups=tools_toys.GROUPS, workers=0) as plain:
        assert "toy_find_asset" not in plain.tools
    with Toolbox(tools_toys.TOOLS, formats=[deck, tools_toys.TEXT], groups=tools_toys.GROUPS,
                 workers=0) as box:
        names = list(box.tools)
        assert names[-1] == "toy_find_asset"                   # added after the others
        assert names.index("toy_ppt_set_title") == 0           # replaced in its place
        assert "asset" in box.tools["toy_ppt_set_title"].schema["properties"]
        session = box.session()
        session.open(synthetic.outer_package(), "deck.pptx")
        found = box.dispatch(session, "toy_find_asset", {"query": "award"})
        assert found.ok and found.data == {"assets": [
            {"id": "star-gold", "name": "star-gold", "keywords": ["award", "favourite"]}]}
        result = box.dispatch(session, "toy_ppt_set_title",
                              {"doc": "d1", "page": 1, "asset": "square"})
        assert result.ok and session.documents["d1"].document.title(1) == "square"
        missing = box.dispatch(session, "toy_ppt_set_title",
                               {"doc": "d1", "page": 1, "asset": "hexagon"})
        assert missing.error.code == "not_found" and "toy_find_asset" in missing.error.message


def test_a_configured_tool_may_not_replace_one_other_kinds_serve(folder):
    shared_save = next(t for t in tools_toys.TOOLS if len(t.handlers) > 1)
    clash = dataclasses.replace(tools_toys.DECK, tools=(shared_save,))
    with pytest.raises(ValueError, match="other kinds serve"):
        Toolbox(tools_toys.TOOLS, formats=[clash, tools_toys.TEXT], workers=0)
