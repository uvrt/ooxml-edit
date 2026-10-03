"""Ids frozen into an extension list."""

from __future__ import annotations

from ooxml_edit.stamp import ExtensionStamp
from ooxml_edit.xml import local_name, parse_xml, register_child_order, serialize

import synthetic

register_child_order({"tst:props": ("tst:name", "tst:extLst")})

STAMP = ExtensionStamp(
    ext_list="tst:extLst", ext="tst:ext", uri="{00000000-0000-0000-0000-000000000001}",
    value="tst:id",
)


def _props(body: str = ""):
    return parse_xml(f'<tst:props xmlns:tst="{synthetic.NS}">{body}</tst:props>'.encode())


def test_an_unstamped_owner_reads_nothing():
    assert STAMP.read(_props()) is None
    assert STAMP.read(_props("<tst:extLst/>")) is None


def test_a_written_stamp_reads_back_and_sits_in_schema_order():
    owner = _props("<tst:name/>")
    STAMP.write(owner, "abc")
    assert STAMP.read(owner) == "abc"
    assert [local_name(child) for child in owner] == ["name", "extLst"]
    STAMP.write(owner, "def")
    assert STAMP.read(owner) == "def"
    assert len(owner[1]) == 1


def test_restamping_the_same_value_changes_nothing():
    owner = _props()
    STAMP.write(owner, "abc")
    before = serialize(owner)
    STAMP.write(owner, "abc")
    assert serialize(owner) == before


def test_other_extensions_are_kept():
    owner = _props(
        '<tst:extLst><tst:ext uri="{other}"><tst:id val="theirs"/></tst:ext></tst:extLst>'
    )
    assert STAMP.read(owner) is None
    STAMP.write(owner, "mine")
    assert STAMP.read(owner) == "mine"
    uris = [ext.get("uri") for ext in owner[0]]
    assert uris == ["{other}", STAMP.uri]


def test_an_empty_value_is_not_a_stamp():
    owner = _props(f'<tst:extLst><tst:ext uri="{STAMP.uri}"><tst:id val=""/></tst:ext></tst:extLst>')
    assert STAMP.read(owner) is None
    STAMP.write(owner, "x")
    assert STAMP.read(owner) == "x" and len(owner[0]) == 1
