"""Document properties, core and custom, on a made-up package (ooxml_edit.properties)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from lxml import etree

from ooxml_edit.history import History
from ooxml_edit.opc import OpcPackage
from ooxml_edit.properties import (CORE_PART, CT_CORE, CT_CUSTOM, CUSTOM_FMTID, CUSTOM_PART,
                                   REL_CORE, REL_CUSTOM, CustomProperty, PropertyError,
                                   check_core, core_part, custom_part, read_core, read_custom,
                                   w3cdtf, write_core, write_custom)

from synthetic import outer_package

CORE = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
        'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms='
        '"http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi='
        '"http://www.w3.org/2001/XMLSchema-instance"><dc:title>Stock list</dc:title>'
        '<dc:creator>Bea</dc:creator><cp:lastModifiedBy>Bea</cp:lastModifiedBy>'
        '<dcterms:created xsi:type="dcterms:W3CDTF">2026-03-01T09:00:00Z</dcterms:created>'
        '<dcterms:modified xsi:type="dcterms:W3CDTF">2026-03-02T10:30:00Z</dcterms:modified>'
        '</cp:coreProperties>').encode()
CUSTOM = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
          '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-'
          'properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/'
          f'docPropsVTypes"><property fmtid="{CUSTOM_FMTID}" pid="2" name="Project">'
          '<vt:lpwstr>Alpha</vt:lpwstr></property>'
          f'<property fmtid="{CUSTOM_FMTID}" pid="3" name="Count"><vt:i4>42</vt:i4></property>'
          f'<property fmtid="{CUSTOM_FMTID}" pid="4" name="Ratio"><vt:r8>2.5</vt:r8></property>'
          f'<property fmtid="{CUSTOM_FMTID}" pid="5" name="Checked"><vt:bool>true</vt:bool>'
          '</property>'
          f'<property fmtid="{CUSTOM_FMTID}" pid="6" name="Due"><vt:filetime>2026-03-14T00:00:'
          '00Z</vt:filetime></property>'
          f'<property fmtid="{CUSTOM_FMTID}" pid="7" name="Total" linkTarget="TotalCell">'
          '<vt:r8>12</vt:r8></property>'
          f'<property fmtid="{CUSTOM_FMTID}" pid="8" name="Blob"><vt:blob>AAAA</vt:blob>'
          '</property></Properties>').encode()


def bare() -> OpcPackage:
    return OpcPackage.open(outer_package())


def with_parts() -> OpcPackage:
    package = bare()
    package.add_part(CORE_PART, CORE, CT_CORE, override=True)
    package.add_relationship("", REL_CORE, CORE_PART)
    package.add_part(CUSTOM_PART, CUSTOM, CT_CUSTOM, override=True)
    package.add_relationship("", REL_CUSTOM, CUSTOM_PART)
    return OpcPackage.open(package.to_bytes())


def test_core_properties_read_typed():
    core = read_core(with_parts())
    assert core == {"title": "Stock list", "creator": "Bea", "last_modified_by": "Bea",
                    "created": datetime(2026, 3, 1, 9, tzinfo=timezone.utc),
                    "modified": datetime(2026, 3, 2, 10, 30, tzinfo=timezone.utc)}
    assert read_core(bare()) == {} and read_custom(bare()) == {}


def test_custom_properties_read_with_their_types():
    custom = read_custom(with_parts())
    assert list(custom) == ["Project", "Count", "Ratio", "Checked", "Due", "Total", "Blob"]
    assert custom["Project"] == CustomProperty("Alpha", "text")
    assert custom["Count"] == CustomProperty(42, "integer")
    assert custom["Ratio"] == CustomProperty(2.5, "number")
    assert custom["Checked"] == CustomProperty(True, "bool")
    assert custom["Due"] == CustomProperty(datetime(2026, 3, 14, tzinfo=timezone.utc), "date")
    assert custom["Total"].linked == "TotalCell"
    assert custom["Blob"] == CustomProperty(None, "blob")
    assert custom["Due"].as_dict() == {"value": "2026-03-14T00:00:00Z", "type": "date"}


def test_core_written_in_place_and_in_order():
    package = with_parts()
    changed = write_core(package, {"subject": "Counts", "category": "Lists", "keywords": "a; b",
                                   "title": "Stock", "lastModifiedBy": None})
    assert sorted(changed) == ["category", "keywords", "last_modified_by", "subject", "title"]
    root = package.tree(core_part(package))
    order = [etree.QName(child).localname for child in root]
    assert order == ["title", "subject", "creator", "keywords", "created", "modified",
                     "category"]
    # the modified date is not touched unless asked
    assert read_core(package)["modified"] == datetime(2026, 3, 2, 10, 30, tzinfo=timezone.utc)
    assert write_core(package, {"title": "Stock"}) == []          # already so: no change


def test_dates_and_aliases():
    package = with_parts()
    write_core(package, {"modified": "2026-11-09T08:30:00+01:00", "author": "Cas",
                         "last_printed": date(2026, 11, 1)})
    core = read_core(package)
    assert w3cdtf(core["modified"]) == "2026-11-09T07:30:00Z"
    assert core["creator"] == "Cas" and w3cdtf(core["last_printed"]) == "2026-11-01T00:00:00Z"
    text = etree.tostring(
        package.tree(core_part(package))).decode()
    assert 'xsi:type="dcterms:W3CDTF">2026-11-09T07:30:00Z' in text
    assert "<cp:lastPrinted>2026-11-01T00:00:00Z</cp:lastPrinted>" in text


def test_every_problem_is_listed_and_nothing_changes():
    package = with_parts()
    before = package.to_bytes()
    with pytest.raises(PropertyError) as raised:
        write_core(package, {"titel": "x", "created": "yesterday", "revision": -1,
                             "subject": 5, "keywords": "a\x01b"})
    names = [name for name, _ in raised.value.problems]
    assert names == ["titel", "created", "revision", "subject", "keywords"]
    assert "one of title, subject" in str(raised.value)
    assert package.to_bytes() == before
    with pytest.raises(PropertyError):
        check_core({"author": "a", "creator": "b"})               # one property, twice


def test_a_package_without_the_parts_gets_them():
    package = bare()
    assert write_core(package, {"title": None}) == []              # nothing to remove
    assert core_part(package) is None
    assert write_core(package, {"title": "Orders"}) == ["title"]
    assert core_part(package) == CORE_PART
    assert package.content_type(CORE_PART) == CT_CORE
    assert write_custom(package, {"Project": "Beta"}) == ["Project"]
    assert custom_part(package) == CUSTOM_PART
    assert package.content_type(CUSTOM_PART) == CT_CUSTOM
    again = OpcPackage.open(package.to_bytes())
    assert read_core(again) == {"title": "Orders"}
    assert read_custom(again) == {"Project": CustomProperty("Beta", "text")}


def test_custom_set_replaced_and_removed():
    package = with_parts()
    changed = write_custom(package, {"project": "Gamma", "Count": 43, "Ratio": 2.5,
                                     "Big": 2 ** 40, "When": datetime(2026, 5, 1, 12),
                                     "Flag": False})
    assert changed == ["Project", "Count", "Big", "When", "Flag"]      # Ratio already so
    custom = read_custom(package)
    assert custom["Project"].value == "Gamma"                         # case aside: the same
    assert custom["Big"] == CustomProperty(float(2 ** 40), "number")
    assert custom["Flag"] == CustomProperty(False, "bool")
    text = etree.tostring(package.tree(custom_part(package))).decode()
    assert "<vt:r8>1099511627776</vt:r8>" in text and "<vt:r8>2.5</vt:r8>" in text
    root = package.tree(custom_part(package))
    pids = {node.get("name"): node.get("pid") for node in root}
    assert pids["Project"] == "2" and pids["Big"] == "9" and pids["Flag"] == "11"
    assert write_custom(package, {"Count": None, "Nothing": None}) == ["Count"]
    assert "Count" not in read_custom(package)


def test_custom_refusals():
    package = with_parts()
    with pytest.raises(PropertyError) as raised:
        write_custom(package, {"Total": 5, "": 1, "Odd": object(), "Inf": float("inf"),
                               "x" * 256: 1})
    names = [name for name, _ in raised.value.problems]
    assert names == ["Total", "", "Odd", "Inf", "x" * 256]
    assert "linked" in raised.value.problems[0][1]
    assert write_custom(package, {"Total": None}) == ["Total"]        # a linked one removed


def test_the_last_custom_property_removed_takes_the_part():
    package = bare()
    write_custom(package, {"Only": "one"})
    write_custom(package, {"Only": None})
    assert custom_part(package) is None and not package.has_part(CUSTOM_PART)
    assert all(rel.type != REL_CUSTOM for rel in package.relationships("").values())


def test_undo_gives_the_bytes_back():
    package = with_parts()
    history = History(package)
    before = package.to_bytes()
    with history.batch():
        write_core(package, {"title": "New", "subject": "S"})
        write_custom(package, {"Extra": 1, "Project": None})
    assert package.to_bytes() != before
    history.undo()
    assert package.to_bytes() == before
