"""The OPC package: lossless round trips, parts added, removed, copied and reaped, all undoable."""

from __future__ import annotations

import io
import zipfile

import pytest

from ooxml_edit.history import History
from ooxml_edit.opc import (
    CONTENT_TYPES_PART,
    OpcPackage,
    normalize_part_path,
    numbered_template,
    rels_path_for,
    resolve_target,
)
from ooxml_edit.xml import qn

import synthetic
from synthetic import EMBEDDED, IMAGE, MAIN, NOTE1, ORPHAN, PAGE1, PAGE2, PNG, entries, infos


# -- round trip ----------------------------------------------------------------------------


def test_an_unedited_package_saves_to_the_same_bytes(data, package):
    assert package.to_bytes() == data


def test_a_round_trip_keeps_every_part_entry_order_timestamp_and_compression(data, package):
    saved = package.to_bytes()
    assert entries(saved) == entries(data)
    assert infos(saved) == infos(data)


def test_parsing_alone_changes_nothing(data, package):
    for name in package.part_names:
        if name.endswith((".xml", ".rels")):
            package.tree(name)
    assert package.dirty_parts == frozenset()
    assert package.to_bytes() == data


def test_a_part_never_parsed_cannot_be_marked_dirty(package):
    with pytest.raises(KeyError, match="never parsed"):
        package.mark_dirty(PAGE1)


def test_an_edited_part_keeps_its_prefixes_and_ignorable_namespaces(data, package):
    root = package.tree(MAIN)
    root.set("touched", "1")
    package.mark_dirty(MAIN)

    saved = entries(package.to_bytes())
    assert saved[MAIN].startswith(b'<?xml version=\'1.0\' encoding=\'UTF-8\' standalone=\'yes\'?>')
    assert b'mc:Ignorable="ext1"' in saved[MAIN]
    assert b'xmlns:ext1="urn:ooxml-edit:test:ext1"' in saved[MAIN]
    assert b"<ext1:unknown ext1:keep=\"yes\"/>" in saved[MAIN]
    assert b'touched="1"' in saved[MAIN]
    original = entries(data)
    assert {name for name in saved if saved[name] != original[name]} == {MAIN}


def test_the_package_opens_from_a_path_and_a_stream(tmp_path, data):
    path = tmp_path / "package.zip"
    path.write_bytes(data)
    assert OpcPackage.open(path).to_bytes() == data
    assert OpcPackage.open(str(path)).to_bytes() == data
    assert OpcPackage.open(io.BytesIO(data)).to_bytes() == data
    target = tmp_path / "saved.zip"
    OpcPackage.open(path).save(target)
    assert target.read_bytes() == data


# -- reading -------------------------------------------------------------------------------


def test_relationships_resolve_relative_absolute_and_external_targets(package):
    relationships = package.relationships(PAGE1)
    assert relationships["rId1"].target_part == NOTE1
    assert relationships["rId2"].target_part == IMAGE
    assert relationships["rId3"].target_part == EMBEDDED
    assert relationships["rId4"].is_external and relationships["rId4"].target_part is None
    assert package.related_part(PAGE1, "rId2") == IMAGE
    assert package.related_part(PAGE1, "rId4") is None
    assert package.related_parts_of_type(MAIN, synthetic.REL_PAGE) == [PAGE1, PAGE2]
    assert package.main_document_part() == MAIN


def test_content_types_come_from_an_override_or_the_extension_default(package):
    assert package.content_type(PAGE1) == synthetic.CT_PAGE
    assert package.content_type(ORPHAN) == "application/xml"
    assert package.content_type(EMBEDDED) == "application/octet-stream"
    assert package.content_type(IMAGE) is None


def test_relationship_sources_and_unreachable_parts(package):
    sources = {(owner, rel.id) for owner, rel in package.relationship_sources(IMAGE)}
    assert sources == {(PAGE1, "rId2"), (PAGE2, "rId1")}
    assert package.unreachable_parts() == {ORPHAN}


# -- adding parts --------------------------------------------------------------------------


def test_an_added_part_is_stored_typed_and_related(package):
    media = package.add_part("doc/media/image2.png", PNG + b"2", "image/png")
    rel_id = package.add_relationship(PAGE2, synthetic.REL_IMAGE, media)

    reread = OpcPackage.open(package.to_bytes())
    assert reread.read(media) == PNG + b"2"
    assert reread.content_type(media) == "image/png"
    assert reread.related_part(PAGE2, rel_id) == media
    assert reread.relationships(PAGE2)[rel_id].target == "../media/image2.png"


def test_a_new_extension_gets_a_default_and_a_known_one_an_override(package):
    package.add_part("doc/media/image2.png", PNG, "image/png")
    package.add_part("doc/pages/page3.xml", b"<x/>", synthetic.CT_PAGE)
    types = package.tree(CONTENT_TYPES_PART)
    defaults = [n.get("Extension") for n in types if n.tag.endswith("Default")]
    overrides = [n.get("PartName") for n in types if n.tag.endswith("Override")]
    assert defaults == ["rels", "xml", "bin", "png"]
    assert overrides[-1] == "/doc/pages/page3.xml"


def test_a_forced_override_is_written_even_when_the_default_agrees(package):
    package.add_part("doc/extra.xml", b"<x/>", "application/xml", override=True)
    types = package.tree(CONTENT_TYPES_PART)
    assert "/doc/extra.xml" in [n.get("PartName") for n in types]


def test_new_content_types_are_indented_like_their_siblings(package):
    package.add_part("doc/pages/page3.xml", b"<x/>", synthetic.CT_PAGE)
    text = package.read(CONTENT_TYPES_PART).decode()
    assert '\n  <Override PartName="/doc/pages/page3.xml"' in text
    assert text.endswith("/>\n</Types>")


def test_an_existing_part_cannot_be_added_again(package):
    with pytest.raises(ValueError, match="already exists"):
        package.add_part(PAGE1, b"<x/>")


def test_a_relationship_is_reused_not_duplicated(package):
    first = package.add_relationship(PAGE2, synthetic.REL_IMAGE, IMAGE)
    assert first == "rId1"
    assert package.add_relationship(PAGE2, synthetic.REL_IMAGE, IMAGE) == first
    assert package.dirty_parts == frozenset()


def test_a_part_without_relationships_gets_a_rels_part(package):
    rel_id = package.add_relationship(ORPHAN, synthetic.REL_IMAGE, IMAGE)
    reread = OpcPackage.open(package.to_bytes())
    assert reread.related_part(ORPHAN, rel_id) == IMAGE
    assert reread.relationships(ORPHAN)[rel_id].target == "media/image1.png"
    assert reread.content_type("doc/_rels/orphan.xml.rels") is not None


def test_an_external_relationship_is_reused(package):
    first = package.add_external_relationship(PAGE2, synthetic.REL_LINK, "https://example.com/")
    assert package.add_external_relationship(PAGE2, synthetic.REL_LINK, "https://example.com/") == first
    assert package.relationships(PAGE2)[first].is_external
    assert package.add_external_relationship(PAGE1, synthetic.REL_LINK, "https://example.org/") == "rId4"


def test_next_rel_id_and_unused_part_name(package):
    assert package.next_rel_id(PAGE1) == "rId5"
    assert package.next_rel_id(ORPHAN) == "rId1"
    assert package.unused_part_name("doc/pages/page{n}.xml") == "doc/pages/page3.xml"


def test_find_part_with_bytes_finds_a_duplicate_under_a_directory(package):
    assert package.find_part_with_bytes(PNG, "doc/media") == IMAGE
    assert package.find_part_with_bytes(PNG, "doc/pages") is None
    assert package.find_part_with_bytes(PNG + b"x", "doc/media") is None


def test_undo_removes_added_parts_byte_for_byte(data, package):
    history = History(package)
    history.checkpoint()
    media = package.add_part("doc/media/image2.png", PNG + b"2", "image/png")
    package.add_relationship(PAGE2, synthetic.REL_IMAGE, media)
    assert set(entries(package.to_bytes())) > set(entries(data))

    assert history.undo()
    assert package.to_bytes() == data
    assert history.redo()
    assert entries(package.to_bytes())[media] == PNG + b"2"
    assert history.undo()
    assert package.to_bytes() == data


def test_replace_part_writes_raw_bytes_and_undo_puts_back_the_original(data, package):
    history = History(package)
    history.checkpoint()
    assert package.tree("doc/media/missing.xml") is None
    package.replace_part(IMAGE, PNG + b"new")
    assert package.read(IMAGE) == PNG + b"new"
    assert IMAGE in package.changed_parts()
    assert history.undo()
    assert package.to_bytes() == data


# -- removing, copying and reaping ---------------------------------------------------------


def test_a_removed_part_takes_its_rels_and_override_and_comes_back_in_place(data, package):
    history = History(package)
    history.checkpoint()
    package.remove_part(PAGE2)
    assert not package.has_part(PAGE2)
    assert not package.has_part(rels_path_for(PAGE2))
    assert package.content_type(PAGE2) == "application/xml"  # only the Default is left
    text = package.read(CONTENT_TYPES_PART).decode()
    assert "page2" not in text and "\n  \n" not in text and text.endswith("/>\n</Types>")

    assert history.undo()
    assert package.to_bytes() == data
    assert infos(package.to_bytes()) == infos(data)
    assert history.redo()
    assert not package.has_part(PAGE2)


def test_removing_a_missing_part_or_relationship_is_refused(package):
    with pytest.raises(KeyError):
        package.remove_part("doc/nothing.xml")
    with pytest.raises(KeyError):
        package.remove_relationship(PAGE1, "rId99")
    with pytest.raises(KeyError):
        package.remove_relationship(ORPHAN, "rId1")


def test_reap_removes_a_cycle_nothing_else_reaches(package):
    """A page and its note point at each other; with the main part's link gone, both go."""
    package.remove_relationship(MAIN, "rId1")
    removed = package.reap([PAGE1])
    assert removed == sorted([PAGE1, NOTE1, EMBEDDED])
    assert package.has_part(IMAGE)  # the other page still shows it
    assert not package.has_part(rels_path_for(PAGE1))
    assert not package.has_part(rels_path_for(NOTE1))


def test_reap_keeps_a_part_anything_else_relates_to(package):
    assert package.reap([PAGE1]) == []
    assert package.reap([CONTENT_TYPES_PART, "doc/nothing.xml"]) == []


def test_reap_counts_relationships_from_unreachable_parts(package):
    package.add_relationship(ORPHAN, synthetic.REL_PAGE, PAGE2)
    package.remove_relationship(MAIN, "rId2")
    assert package.reap([PAGE2]) == []


def test_release_keeps_a_relationship_still_referenced(package):
    root = package.tree(PAGE2)
    assert package.release(PAGE2, ["rId1", "rId7"]) == []  # r:embed still names rId1
    assert "rId1" in package.relationships(PAGE2)

    root.remove(root[0])
    package.mark_dirty(PAGE2)
    assert package.release(PAGE2, ["rId1"]) == []  # gone from page 2, still page 1's
    assert "rId1" not in package.relationships(PAGE2)
    assert package.has_part(IMAGE)


def test_release_reaps_what_nothing_else_uses(data, package):
    history = History(package)
    history.checkpoint()
    root = package.tree(PAGE1)
    root.remove(root.find(qn("tst:object")))
    package.mark_dirty(PAGE1)
    assert package.release(PAGE1, ["rId3"]) == [EMBEDDED]
    assert not package.has_part(EMBEDDED)
    assert history.undo()
    assert package.to_bytes() == data


def test_copy_part_shares_or_copies_each_relationship(package):
    copy = package.copy_part(PAGE1, share=lambda rel: rel.type == synthetic.REL_IMAGE)
    assert copy == "doc/pages/page3.xml"
    assert package.read(copy) == package.read(PAGE1)
    assert package.content_type(copy) == synthetic.CT_PAGE
    old, new = package.relationships(PAGE1), package.relationships(copy)
    assert set(old) == set(new)
    assert new["rId2"].target_part == IMAGE  # shared
    assert new["rId4"].is_external and new["rId4"].target == old["rId4"].target
    note = new["rId1"].target_part
    assert note == "doc/notes/note2.xml"  # copied, and pointed back at the copy
    assert package.related_part(note, "rId1") == copy
    assert new["rId3"].target_part == "doc/embeddings/inner2.bin"
    assert package.read(new["rId3"].target_part) == package.read(EMBEDDED)


def test_copy_part_of_a_missing_part_is_refused(package):
    with pytest.raises(KeyError):
        package.copy_part("doc/nothing.xml", share=lambda rel: True)


# -- packages inside packages --------------------------------------------------------------


def test_an_embedded_package_round_trips_untouched(package):
    nested = package.open_embedded(EMBEDDED)
    assert nested.main_document_part() == "inner/main.xml"
    nested.tree("inner/values.xml")  # parsing alone changes nothing
    assert package.replace_embedded(EMBEDDED, nested) is False
    assert package.changed_parts() == frozenset()


def test_an_edited_embedded_package_is_one_undoable_raw_write(data, package):
    history = History(package)
    nested = package.open_embedded(EMBEDDED)
    nested.tree("inner/values.xml").set("count", "999")
    nested.mark_dirty("inner/values.xml")

    history.checkpoint()
    assert package.replace_embedded(EMBEDDED, nested) is True
    inner_before = entries(synthetic.inner_package())
    inner_after = entries(OpcPackage.open(package.to_bytes()).read(EMBEDDED))
    assert b'count="999"' in inner_after["inner/values.xml"]
    assert {n for n in inner_before if inner_before[n] != inner_after[n]} == {"inner/values.xml"}

    assert history.undo()
    assert package.to_bytes() == data


def test_an_embedded_package_that_is_missing_is_refused(package):
    with pytest.raises(KeyError):
        package.open_embedded("doc/embeddings/nothing.bin")


# -- writing with replacements, and what changed -------------------------------------------


def test_a_package_is_written_with_replacements_and_keeps_its_own(data, package):
    written = package.to_bytes({ORPHAN: b"<x/>", "doc/not-a-part.xml": b"<y/>"})
    assert entries(written)[ORPHAN] == b"<x/>"
    assert "doc/not-a-part.xml" not in entries(written)
    assert {k: v for k, v in entries(written).items() if k != ORPHAN} == \
        {k: v for k, v in entries(data).items() if k != ORPHAN}
    assert infos(written) == infos(data)
    assert package.to_bytes() == data


def test_changed_parts_and_the_package_as_opened(data, package):
    history = History(package)
    assert package.changed_parts() == frozenset()
    history.checkpoint()
    media = package.add_part("doc/media/image2.png", PNG + b"2", "image/png")
    package.add_relationship(PAGE2, synthetic.REL_IMAGE, media)
    assert package.changed_parts() == {media, CONTENT_TYPES_PART, rels_path_for(PAGE2)}
    opened = package.opened()
    assert not opened.has_part(media) and package.has_part(media)
    assert opened.to_bytes() == data
    history.undo()
    assert package.changed_parts() == frozenset()


# -- path helpers --------------------------------------------------------------------------


def test_path_helpers():
    assert normalize_part_path("/doc\\pages/../main.xml") == "doc/main.xml"
    assert normalize_part_path("") == ""
    assert rels_path_for("doc/main.xml") == "doc/_rels/main.xml.rels"
    assert rels_path_for("") == "_rels/.rels"
    assert resolve_target("doc/pages", "../media/a.png") == "doc/media/a.png"
    assert resolve_target("doc/pages", "/other/a.png") == "other/a.png"
    assert resolve_target("", "doc/main.xml") == "doc/main.xml"


def test_numbered_template():
    assert numbered_template("doc/items/item12.xml") == "doc/items/item{n}.xml"
    assert numbered_template("a/b.bin") == "a/b{n}.bin"
    assert numbered_template("noext") == "noext{n}"
    assert numbered_template("a/{odd}1.xml").format(n=2) == "a/{odd}2.xml"


def test_a_zip_with_directory_entries_opens(data):
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(buffer, "w") as target:
        target.writestr(zipfile.ZipInfo("doc/"), b"")
        for info in source.infolist():
            target.writestr(info, source.read(info))
    package = OpcPackage.open(buffer.getvalue())
    assert "doc/" not in package.part_names and package.has_part(MAIN)
