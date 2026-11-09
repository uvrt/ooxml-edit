"""A package keeps each part it read as its compressed ZIP entry until the part is edited
(ooxml-common's zipparts): reads inflate it, saves copy it through a piece at a time, undo
puts the entry back -- and the bytes are the same as when every part was held inflated."""

from __future__ import annotations

import io
import random
import threading
import zipfile

import pytest

from ooxml_common.zipparts import ZipPart
from ooxml_edit.opc import OpcPackage, pieces_compress_alike
from ooxml_edit.history import History

import synthetic
from synthetic import IMAGE, MAIN, ORPHAN, PAGE1


def _held(data: bytes) -> OpcPackage:
    """The package as it was before: every part read whole into a dict."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = [info for info in archive.infolist() if not info.is_dir()]
        parts = {info.filename.lstrip("/"): archive.read(info) for info in entries}
    return OpcPackage(entries, parts)


def _big_xml(size: int, seed: int = 3) -> bytes:
    rng = random.Random(seed)
    rows = []
    total = 0
    n = 0
    while total < size:
        n += 1
        row = f'<tst:row n="{n}" v="{rng.randrange(10 ** 9)}"/>'
        rows.append(row)
        total += len(row)
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<tst:big xmlns:tst="{synthetic.NS}">{"".join(rows)}</tst:big>').encode()


def _with_big_part(data: bytes, size: int = 3_000_000) -> bytes:
    """``data`` with a large XML part added, deflated, as a sheet of many rows is."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, \
            zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        info = zipfile.ZipInfo("doc/big.xml", date_time=(2024, 5, 6, 7, 8, 10))
        info.compress_type = zipfile.ZIP_DEFLATED
        target.writestr(info, _big_xml(size))
    return buffer.getvalue()


def test_untouched_parts_stay_compressed(data, package):
    for path in package.part_names:
        entry = package.part_entry(path)
        assert isinstance(entry, ZipPart)
        assert package.part_size(path) == entry.size == len(package.read(path))
        assert package.open_part(path).read() == package.read(path)
    assert package.part_entry("nothing") is None and package.open_part("nothing") is None
    assert package.part_size("nothing") is None


@pytest.mark.parametrize("edit", ["none", "tree", "replace", "add", "remove"])
def test_saved_bytes_are_those_of_every_part_held_inflated(data, edit):
    data = _with_big_part(data)
    packages = [OpcPackage.open(data), _held(data)]
    for package in packages:
        if edit == "tree":
            root = package.tree(PAGE1)
            root.set("changed", "1")
            package.mark_dirty(PAGE1)
        elif edit == "replace":
            package.replace_part(IMAGE, b"\x89PNG other")
        elif edit == "add":
            package.add_part("doc/media/image9.png", b"\x89PNG new", "image/png")
        elif edit == "remove":
            package.remove_part(ORPHAN)
    compressed, held = (package.to_bytes() for package in packages)
    assert compressed == held
    if edit == "none":
        assert compressed == OpcPackage.open(data).to_bytes()


@pytest.mark.skipif(not pieces_compress_alike(),
                    reason="this deflate writes other bytes for pieces: parts are saved whole")
def test_an_untouched_part_is_saved_without_being_inflated_whole(data, monkeypatch):
    data = _with_big_part(data)
    package = OpcPackage.open(data)
    expected = _held(data).to_bytes()
    original = ZipPart.read

    def small_only(self):
        assert self.size <= 1 << 20, f"{self.name} read whole"
        return original(self)

    monkeypatch.setattr(ZipPart, "read", small_only)
    assert package.to_bytes() == expected


def test_an_edited_part_is_no_longer_its_entry(package):
    package.tree(PAGE1).set("x", "1")
    assert package.part_entry(PAGE1) is not None   # parsed, not yet marked
    package.mark_dirty(PAGE1)
    assert package.part_entry(PAGE1) is None
    assert package.open_part(PAGE1).read() == package.read(PAGE1)
    assert package.part_size(PAGE1) == len(package.read(PAGE1))
    package.replace_part(IMAGE, b"\x89PNG other")
    assert package.part_entry(IMAGE) is None and package.part_size(IMAGE) == 10


def test_undo_puts_the_entry_back_not_its_bytes(data):
    package = OpcPackage.open(data)
    entry = package.part_entry(IMAGE)
    before = package.snapshot()
    package.replace_part(IMAGE, b"\x89PNG other")
    package.remove_part(ORPHAN)
    after = package.snapshot()
    assert package.changed_between(before, after) >= {IMAGE, ORPHAN}
    package.restore(before)
    assert package.part_entry(IMAGE) is entry and package.part_entry(ORPHAN) is not None
    assert package.to_bytes() == data
    package.restore(after)
    assert package.read(IMAGE) == b"\x89PNG other" and not package.has_part(ORPHAN)
    package.restore(before)
    # A snapshot of a part put back holds the entry, and two snapshots of it compare
    # without inflating it.
    package.replace_part(IMAGE, b"x")
    package.restore(before)
    again = package.snapshot()
    assert package.changed_between(before, again) == frozenset()


def test_a_history_round_trip_keeps_parts_compressed(data):
    package = OpcPackage.open(data)
    history = History(package)
    history.checkpoint()
    package.replace_part(IMAGE, b"\x89PNG other")
    assert package.part_entry(IMAGE) is None
    history.undo()
    assert isinstance(package.part_entry(IMAGE), ZipPart)
    history.redo()
    assert package.read(IMAGE) == b"\x89PNG other"


def test_opened_shares_the_entries(package):
    package.replace_part(IMAGE, b"\x89PNG other")
    opened = package.opened()
    assert opened.part_entry(IMAGE) is not None
    assert opened.part_entry(MAIN) is package.part_entry(MAIN)


def test_duplicates_and_orphans_are_found_by_size_first(package, monkeypatch):
    inflated = []
    original = ZipPart.read

    def counted(self):
        inflated.append(self.name)
        return original(self)

    monkeypatch.setattr(ZipPart, "read", counted)
    assert package.find_part_with_bytes(b"\x00" * 7, "doc") is None
    assert inflated == []
    image = original(package.part_entry(IMAGE))
    assert package.find_part_with_bytes(image, "doc/media") == IMAGE
    removed = package.drop_orphans()
    assert removed and all(size == len(original(package._original.stored(path)))
                           for path, size in removed if package._original.stored(path))


def test_a_package_from_held_parts_still_works(data):
    package = _held(data)
    assert package.part_entry(MAIN) is None
    assert package.open_part(MAIN).read() == package.read(MAIN)
    assert package.to_bytes() == OpcPackage.open(data).to_bytes()


def test_threads_read_one_package_at_once(data):
    data = _with_big_part(data, 1_500_000)
    package = OpcPackage.open(data)
    expected = _big_xml(1_500_000)
    errors = []

    def work():
        try:
            for _ in range(3):
                assert package.read("doc/big.xml") == expected
                assert package.open_part("doc/big.xml").read() == expected
        except BaseException as exc:   # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def test_a_large_part_is_parsed_from_its_stream_to_the_same_tree(data, monkeypatch):
    from ooxml_edit import opc
    from ooxml_edit.xml import serialize

    data = _with_big_part(data, 1_200_000)
    monkeypatch.setattr(opc, "PARSE_STREAMED", 1 << 40)
    whole = {path: serialize(OpcPackage.open(data).tree(path))
             for path in OpcPackage.open(data).part_names if path.endswith((".xml", ".rels"))}
    monkeypatch.setattr(opc, "PARSE_STREAMED", 0)
    package = OpcPackage.open(data)
    monkeypatch.setattr(ZipPart, "read", lambda self: pytest.fail(f"{self.name} read whole"))
    for path, expected in whole.items():
        assert serialize(package.tree(path)) == expected
    root = package.tree("doc/big.xml")
    root.set("changed", "1")
    package.mark_dirty("doc/big.xml")
    assert b'changed="1"' in package.read("doc/big.xml")


@pytest.mark.parametrize("alike", [True, False])
def test_saved_bytes_are_the_same_whichever_way_a_part_is_compressed(data, monkeypatch, alike):
    """Where pieces compress otherwise (zlib-ng), a part is inflated whole and written as
    before; either way the bytes are those of every part held inflated."""
    from ooxml_edit import opc

    if alike and not pieces_compress_alike():
        pytest.skip("this deflate writes other bytes for pieces (zlib-ng)")
    data = _with_big_part(data)
    monkeypatch.setattr(opc, "_ALIKE", alike)
    assert OpcPackage.open(data).to_bytes() == _held(data).to_bytes()
