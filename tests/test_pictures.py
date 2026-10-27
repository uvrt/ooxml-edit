"""``ooxml_edit.pictures``: an SVG picture's blip extension and the SVG recoloured as
PowerPoint's Graphics Fill and Outline rewrite one (round 17, P6: moved from the PowerPoint
library, whose measured tests run against it there; Word draws the same SVG, its class
resolved against the document's theme, measured 2026-10-26).  Made-up SVGs."""

from __future__ import annotations

import pytest
from lxml import etree

from ooxml_edit import pictures
from ooxml_edit.errors import ArgumentError

SVG = "{http://www.w3.org/2000/svg}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
ICON = (b'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
        b'<circle cx="12" cy="12" r="10" fill="#000000"/>'
        b'<path fill="none" stroke="#111111" stroke-width="2" d="M6 12h12"/></svg>')


def test_parse_refuses_what_is_not_a_safe_svg():
    assert pictures.parse(ICON).tag == SVG + "svg"
    for bad, words in ((b'<!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg"/>',
                        "document type"),
                       (b"<svg", "not well-formed"),
                       (b'<html xmlns="http://www.w3.org/1999/xhtml"/>', "not an SVG")):
        with pytest.raises(pictures.SvgError, match=words):
            pictures.parse(bad)
    assert issubclass(pictures.SvgError, ArgumentError)
    assert pictures.looks_like_svg(b"<?xml version='1.0'?>\n" + ICON)
    assert not pictures.looks_like_svg(b"\x89PNG\r\n")


def test_native_size_and_stroke_units():
    assert pictures.native_size(pictures.parse(ICON)) == (24, 24)
    boxed = pictures.parse(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20"/>')
    assert pictures.native_size(boxed) == (40, 20)
    sized = pictures.parse(b'<svg xmlns="http://www.w3.org/2000/svg" width="18pt" viewBox="0 0 12 6"/>')
    assert pictures.native_size(sized) == (24, 12)
    # 24 units placed over 192 px (8 px a unit): a unit is 1/8 px of 9525 EMU.
    assert pictures.stroke_units(pictures.parse(ICON), 192 * 9525, 192 * 9525) == pytest.approx(1 / (8 * 9525))


def test_the_blip_extension():
    blip = etree.Element(A + "blip")
    assert pictures.svg_blip(blip) is None
    pictures.attach(blip, "rId7")
    found = pictures.svg_blip(blip)
    assert found is not None and found.get(f"{{{pictures.R_NS}}}embed") == "rId7"
    assert found.getparent().get("uri") == pictures.SVG_BLIP_EXT_URI
    assert found.nsmap.get("asvg") == pictures.ASVG_NS
    pictures.attach(blip, "rId8")
    assert len(blip.findall(f"{A}extLst/{A}ext")) == 1 and pictures.svg_blip(blip).get(
        f"{{{pictures.R_NS}}}embed") == "rId8"
    pictures.drop(blip)
    assert pictures.svg_blip(blip) is None and blip.find(A + "extLst") is None


def test_a_theme_fill_writes_the_class_and_the_value():
    assert pictures.theme_class("accent2") == "MsftOfcThm_Accent2_Fill_v2"
    assert pictures.theme_class("tx1", "Stroke") == "MsftOfcThm_Text1_Stroke_v2"
    assert pictures.theme_class("phClr") is None
    root = etree.fromstring(pictures.recolor(ICON, "#E97132", pictures.theme_class("accent2")))
    style = root[0]
    assert style.tag == SVG + "style" and ".MsftOfcThm_Accent2_Fill_v2 {\n fill:#E97132; \n}" in style.text
    for shape in root.iter(SVG + "circle", SVG + "path"):
        assert shape.get("class") == "MsftOfcThm_Accent2_Fill_v2"
        assert (shape.get("fill"), shape.get("fill-opacity")) == ("#E97132", "1")
    # The outline's own stroke stays; the circle, which had none, says none.
    assert root.find(SVG + "path").get("stroke") == "#111111"
    assert root.find(SVG + "circle").get("stroke") == "none"


def test_an_rgb_fill_has_no_class_and_recolouring_again_replaces_it():
    once = pictures.recolor(ICON, "#E97132", pictures.theme_class("accent2"))
    again = etree.fromstring(pictures.recolor(once, "#1F4E79"))
    assert again.find(SVG + "style") is None
    assert {s.get("fill") for s in again.iter(SVG + "circle", SVG + "path")} == {"#1F4E79"}
    assert all(s.get("class") is None for s in again.iter(SVG + "circle", SVG + "path"))


def test_nothing_to_paint_is_refused():
    with pytest.raises(pictures.SvgError, match="no shape to recolour"):
        pictures.recolor(b'<svg xmlns="http://www.w3.org/2000/svg"><g/></svg>', "#000000")
