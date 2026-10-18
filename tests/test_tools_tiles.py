"""Contact images (round 9, N1): render's ``sheet`` options, layout and SVG."""

from __future__ import annotations

import base64
import re
import struct
import zlib

import pytest
from lxml import etree

from ooxml_edit.tools import ToolError, shared, tiles
from ooxml_edit.tools.tiles import (GAP, LABEL_HEIGHT, MAX_TILES, Tiling, check_size, tiled_svg,
                                    tiling_of)

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


def test_a_call_width_out_of_the_tiles_bounds_is_named_as_width():
    with pytest.raises(ToolError) as caught:
        of({}, 10, width=2000)
    assert caught.value.field == "width" and "160-1280" in caught.value.message


def test_an_image_too_wide_says_what_fits():
    with pytest.raises(ToolError) as caught:
        of({"columns": 8, "width": 400}, 16)
    message = caught.value.message
    assert caught.value.code == "limit" and "2576 px" in message
    widest = int(re.search(r"at most (\d+)", message).group(1))
    assert Tiling(8, widest).total_width <= 2576 < Tiling(8, widest + 1).total_width


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
