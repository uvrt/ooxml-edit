"""The XML helpers: qualified names, attributes, schema-ordered insertion, removal."""

from __future__ import annotations

import pytest
from lxml import etree

from ooxml_edit.xml import (
    NAMESPACES,
    append_in_order,
    child_elements,
    declared_prefixes,
    find,
    findall,
    get_bool,
    get_int,
    insert_in_order,
    iter_descendants,
    local_name,
    make,
    parse_xml,
    prefixed_name,
    qn,
    register_child_order,
    register_namespaces,
    remove,
    replace_choice,
    serialize,
    set_attr,
    set_int,
    subelement,
)

import synthetic

NS = synthetic.NS
REL = synthetic.REL

register_child_order({
    "tst:frame": ("tst:name", "tst:size", ("tst:run", "tst:break"), "tst:end", "tst:extLst"),
    "tst:fill": ("tst:solid", "tst:pattern"),
})


def _parse(body: str) -> etree._Element:
    return parse_xml(f'<tst:frame xmlns:tst="{NS}" xmlns:r="{REL}">{body}</tst:frame>'.encode())


def _names(parent) -> list[str]:
    return [local_name(child) for child in parent if isinstance(child.tag, str)]


def test_qn_spells_prefixed_and_plain_names():
    assert qn("r:id") == "{%s}id" % REL
    assert qn("val") == "val"
    with pytest.raises(KeyError, match="unknown namespace prefix"):
        qn("nope:thing")


def test_a_prefix_cannot_be_rebound():
    register_namespaces({"tst": NS})  # the same binding again is fine
    with pytest.raises(ValueError, match="already bound"):
        register_namespaces({"tst": "urn:something-else"})
    assert NAMESPACES["tst"] == NS


def test_local_and_prefixed_names():
    root = _parse('<tst:name/><!-- note --><other xmlns="urn:unknown"/>')
    name, comment, other = list(root)
    assert local_name(name) == "name" and prefixed_name(name) == "tst:name"
    assert local_name(comment) == "" and prefixed_name(comment) == ""
    assert prefixed_name(other) == "other"
    assert prefixed_name(etree.Element("plain")) == "plain"
    assert set(declared_prefixes(root)) == {"tst", "r"}


def test_find_and_findall_follow_prefixed_paths():
    root = _parse('<tst:size><tst:run val="1"/><tst:run val="2"/></tst:size>')
    assert find(root, "tst:size/tst:run").get("val") == "1"
    assert find(root, "tst:size/tst:none/tst:run") is None
    assert find(None, "tst:size") is None
    assert [r.get("val") for r in findall(root, "tst:size/tst:run")] == ["1", "2"]
    assert findall(root, "tst:none/tst:run") == []
    assert findall(None, "tst:run") == []
    assert len(list(iter_descendants(root, "tst:run"))) == 2


def test_attributes_read_and_write():
    node = make("tst:size", w="12", flag="on", bad="x", r__id="rId3")
    assert node.get(qn("r:id")) == "rId3"
    assert get_int(node, "w") == 12 and get_int(node, "bad", 7) == 7
    assert get_int(node, "missing", 5) == 5 and get_int(None, "w", 1) == 1
    assert get_bool(node, "flag") is True and get_bool(node, "bad") is False
    assert get_bool(node, "missing", True) is True and get_bool(None, "flag") is None
    set_int(node, "w", 40)
    set_attr(node, "r:id", "rId9")
    assert node.get("w") == "40" and node.get(qn("r:id")) == "rId9"
    set_int(node, "w", None)
    set_attr(node, "r:id", None)
    assert node.get("w") is None and node.get(qn("r:id")) is None


def test_children_are_inserted_in_schema_order():
    root = _parse("<tst:run/><tst:end/>")
    append_in_order(root, make("tst:name"))
    append_in_order(root, make("tst:extLst"))
    append_in_order(root, make("tst:size"))
    assert _names(root) == ["name", "size", "run", "end", "extLst"]


def test_a_repeating_choice_appends_after_its_last_member():
    root = _parse("<tst:run/><tst:break/><tst:run/><tst:end/>")
    added = append_in_order(root, make("tst:break", val="new"))
    assert _names(root) == ["run", "break", "run", "break", "end"]
    assert root[3] is added


def test_unknown_children_stay_last_and_unordered_parents_append():
    root = _parse('<tst:name/><ext xmlns="urn:unknown"/>')
    append_in_order(root, make("tst:end"))
    assert _names(root) == ["name", "end", "ext"]
    append_in_order(root, make("tst:unlisted"))
    assert _names(root)[-1] == "unlisted"
    loose = make("tst:loose")
    loose.append(make("tst:b"))
    append_in_order(loose, make("tst:a"))
    assert _names(loose) == ["b", "a"]
    insert_in_order(loose, loose[0])
    assert _names(loose) == ["b", "a"]


def test_subelement_finds_or_creates_in_order():
    root = _parse("<tst:end/>")
    size = subelement(root, "tst:size", w="3")
    assert _names(root) == ["size", "end"] and size.get("w") == "3"
    assert subelement(root, "tst:size", w="9") is size and size.get("w") == "3"


def test_replace_choice_leaves_one_member():
    fill = parse_xml(f'<tst:fill xmlns:tst="{NS}"><tst:solid/><tst:pattern/></tst:fill>'.encode())
    new = replace_choice(fill, ["tst:solid", "tst:pattern"], make("tst:pattern", kind="dots"))
    assert _names(fill) == ["pattern"] and fill[0] is new
    assert replace_choice(fill, ["tst:solid", "tst:pattern"], None) is None
    assert _names(fill) == []


def test_remove_keeps_the_whitespace_around_it():
    root = _parse("\n  <tst:name/>\n  <tst:size/>\n")
    remove(root[1])
    assert serialize(root).endswith(b"<tst:name/>\n  \n</tst:frame>")
    first = _parse("\n  <tst:name/>\n  <tst:size/>\n")
    remove(first[0])
    assert serialize(first).endswith(b"\n  \n  <tst:size/>\n</tst:frame>")
    remove(make("tst:alone"))  # detached: nothing to do


def test_child_elements_filter_by_tag():
    root = _parse("<tst:name/><!-- c --><tst:run/><tst:run/>")
    assert _names(root) == ["name", "run", "run"]
    assert len(child_elements(root)) == 3
    assert len(child_elements(root, ["tst:run"])) == 2


def test_parsing_keeps_whitespace_and_refuses_entity_expansion():
    data = (
        b'<?xml version="1.0"?>\n<!DOCTYPE t [<!ENTITY big "xxxxxxxx">]>'
        b'<t>  &big;  </t>'
    )
    root = parse_xml(data)
    assert "xxxxxxxx" not in etree.tostring(root).decode()


def test_serialize_writes_a_standalone_utf8_declaration():
    root = _parse("<tst:name>é</tst:name>")
    out = serialize(root[0])  # any element serializes its whole tree
    assert out.startswith(b"<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n<tst:frame")
    assert "é".encode() in out
