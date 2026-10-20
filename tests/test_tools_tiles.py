"""Contact images (round 9, N1): render's ``sheet`` options, layout and SVG."""

from __future__ import annotations

import base64
import re
import struct
import zlib

import pytest
from lxml import etree

from ooxml_edit.tools import ToolError, shared, tiles
from ooxml_edit.tools.tiles import (GAP, LABEL_HEIGHT, MAX_TILES, MIN_WIDTH, Tiling, check_size, fit,
                                    tiled_size, tiled_svg, tiling_of)

SVG = "{http://www.w3.org/2000/svg}"


def _png(width: int, height: int) -> bytes:
    """A grey PNG of this size, made here (no image library)."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))
    rows = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def of(options, count, **kwargs):
    return tiling_of(options, count, name="sheet", what="slides", **kwargs)


def test_the_defaults_fit_the_standard_tier():
    tiling = of({}, 14)
    assert tiling == Tiling(4, 360)
    assert tiling.total_width == 4 * 360 + 5 * GAP <= 1568


def test_columns_are_no_more_than_the_tiles_and_the_call_width_is_the_tiles():
    assert of(None, 2) == Tiling(2, 360)
    assert of({"columns": 3}, 7, width=480) == Tiling(3, 480)
    assert of({"width": 200}, 7, width=480) == Tiling(4, 200)


def test_the_cap_names_itself_and_what_to_do():
    with pytest.raises(ToolError) as caught:
        of({}, MAX_TILES + 1)
    assert caught.value.code == "limit" and caught.value.field == "slides"
    assert f"one sheet holds at most {MAX_TILES} slides" in caught.value.message
    assert "another call" in caught.value.message
    with pytest.raises(ToolError) as caught:
        tiling_of({}, 40, name="sheet", what="pages")
    assert caught.value.field == "pages" and "pages" in caught.value.message


@pytest.mark.parametrize("options, field, bound", [
    ({"columns": 0}, "sheet.columns", "1-8"), ({"columns": 9}, "sheet.columns", "1-8"),
    ({"width": 100}, "sheet.width", "160-1280"), ({"width": 2000}, "sheet.width", "160-1280")])
def test_bounds_are_named(options, field, bound):
    with pytest.raises(ToolError) as caught:
        of(options, 10)
    assert caught.value.field == field and bound in caught.value.message


def test_a_call_width_over_a_tile_s_is_taken_as_the_widest_tile_and_said():
    """Round 10, O4: ``width`` standing in for the tile's is at most 1,280 px."""
    tiling = of({"columns": 1}, 10, width=2000)
    assert (tiling.columns, tiling.width) == (1, 1280)
    assert tiling.shrunk == {"from": 2000, "why": "width 2000 is over a tile's 1280 px"}
    assert of({"columns": 1}, 10, width=1280).shrunk is None


def test_tiles_too_wide_side_by_side_are_narrowed_to_the_widest_that_fit():
    """Round 10, O4: two columns of the call's 1,280 px are 2,584 px; 1,276 px fit."""
    tiling = of({"columns": 2}, 4, width=1280)
    assert tiling.width == 1276 and tiling.total_width <= 2576 < Tiling(2, 1277).total_width
    assert tiling.shrunk["from"] == 1280
    assert "2584 px wide, over the 2576 px limit" in tiling.shrunk["why"]
    wide = of({"columns": 8, "width": 400}, 16)
    assert wide.total_width <= 2576 < Tiling(8, wide.width + 1).total_width
    assert wide.shrunk["from"] == 400
    # Narrowed from what the call asked, both reasons kept.
    both = of({"columns": 2}, 4, width=2000)
    assert both.width == 1276 and both.shrunk["from"] == 2000
    assert "over a tile's 1280 px; then 2 columns of 1280 px" in both.shrunk["why"]


def test_columns_that_do_not_fit_even_at_the_narrowest_tile_are_refused():
    with pytest.raises(ToolError) as caught:
        of({"columns": 8}, 16, max_edge=1000)
    assert caught.value.code == "limit" and caught.value.field == "sheet.columns"
    assert f"even at a tile width of {MIN_WIDTH}" in caught.value.message
    assert "at most 5 columns" in caught.value.message


def test_tiles_too_tall_are_narrowed_to_the_widest_that_fit():
    """Round 10, O4: 8 rows of Letter pages at 360 px are too tall; the widest that fit."""
    tiling = of({}, 30)
    ratios = [11 / 8.5] * 30
    narrowed = fit(tiling, ratios, name="sheet", what="slides")
    heights = lambda width: [-(-width * 22 // 17)] * 30  # noqa: E731
    assert tiled_size(narrowed, heights(narrowed.width))[1] <= 2576
    assert tiled_size(Tiling(4, narrowed.width + 1), heights(narrowed.width + 1))[1] > 2576
    assert narrowed.shrunk["from"] == 360 and "px tall, over the 2576 px limit" in narrowed.shrunk["why"]
    assert fit(of({}, 8), [9 / 16] * 8, name="sheet") == of({}, 8)        # fits: as it was
    with pytest.raises(ToolError) as caught:
        fit(of({"columns": 1}, 30), [2.0] * 30, name="sheet", what="pages")
    assert caught.value.field == "pages" and "at most 7 pages" in caught.value.message


def test_an_image_too_tall_says_how_many_fit():
    tiling = of({"columns": 1, "width": 640}, 10)
    with pytest.raises(ToolError) as caught:
        check_size(tiling, [360] * 10, name="sheet", what="slides")
    assert "at most 6 slides" in caught.value.message      # 6 rows of 360 + 20 + 8
    assert check_size(tiling, [360] * 6, name="sheet")[1] <= 2576


def test_the_svg_places_each_png_at_its_own_size_with_its_label():
    tiling = of({"columns": 2, "width": 160}, 3)
    views = [(_png(160, 90), 160, 90, "1 · s:256"), (_png(160, 120), 160, 120, "2 · s:257 · <hidden>"),
             (_png(160, 90), 160, 90, "L2 A layout name far too long for one tile")]
    svg, width, height = tiled_svg(views, tiling)
    assert (width, height) == (2 * 160 + 3 * GAP, 120 + 90 + 2 * LABEL_HEIGHT + 3 * GAP)
    root = etree.fromstring(svg.encode())
    images = root.findall(f"{SVG}image")
    assert [(i.get("x"), i.get("y"), i.get("width"), i.get("height")) for i in images] == [
        ("8", "8", "160", "90"), ("176", "8", "160", "120"), ("8", str(8 + 120 + 20 + 8), "160", "90")]
    assert base64.b64decode(images[1].get("href").split(",", 1)[1]) == views[1][0]
    labels = [t.text for t in root.iter(f"{SVG}text")]
    assert labels[:2] == ["1 · s:256", "2 · s:257 · <hidden>"]
    assert labels[2].endswith("…") and len(labels[2]) < len(views[2][3])


def test_render_takes_a_sheet():
    schema = shared.SPECS["render"].schema
    sheet = schema["properties"]["sheet"]
    assert set(sheet["properties"]) == {"columns", "width"}
    assert "sheet" not in schema.get("required", [])
    assert tiles.MAX_TILES == 30 and "Up to 30" in sheet["description"]
