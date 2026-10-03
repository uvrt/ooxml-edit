"""Small OPC packages built in the tests, so the suite needs no real document.

The package is format-neutral on purpose -- its own namespace, its own content types -- but
it has every shape the editing layer has to get right: a main document related from the
package root, two pages, a note and a page that relate to each other (a cycle), a picture
two parts share, a package embedded in the package, a part nothing relates to, a
pretty-printed content-types part, an ``mc:Ignorable`` prefix, and entries that differ in
compression and timestamp.
"""

from __future__ import annotations

import io
import zipfile

NS = "urn:ooxml-edit:test"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_DOCUMENT = REL + "/officeDocument"
REL_IMAGE = REL + "/image"
REL_PAGE = "urn:ooxml-edit:test/relationships/page"
REL_NOTE = "urn:ooxml-edit:test/relationships/note"
REL_PACKAGE = REL + "/package"
REL_LINK = REL + "/hyperlink"

CT_MAIN = "application/vnd.ooxml-edit.test.main+xml"
CT_PAGE = "application/vnd.ooxml-edit.test.page+xml"
CT_NOTE = "application/vnd.ooxml-edit.test.note+xml"
CT_RELS = "application/vnd.openxmlformats-package.relationships+xml"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

MAIN = "doc/main.xml"
PAGE1 = "doc/pages/page1.xml"
PAGE2 = "doc/pages/page2.xml"
NOTE1 = "doc/notes/note1.xml"
IMAGE = "doc/media/image1.png"
EMBEDDED = "doc/embeddings/inner1.bin"
ORPHAN = "doc/orphan.xml"


def _xml(body: str) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' + body).encode()


def _rels(*relationships: tuple[str, str, str] | tuple[str, str, str, str]) -> bytes:
    nodes = []
    for relationship in relationships:
        rel_id, rel_type, target, *mode = relationship
        extra = f' TargetMode="{mode[0]}"' if mode else ""
        nodes.append(f'<Relationship Id="{rel_id}" Type="{rel_type}" Target="{target}"{extra}/>')
    return _xml(
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(nodes) + "</Relationships>"
    )


def _zip(entries: list[tuple[str, bytes, int, tuple[int, ...]]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data, compression, stamp in entries:
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = compression
            archive.writestr(info, data)
    return buffer.getvalue()


def inner_package() -> bytes:
    """A package to embed: a main part and one more, which the tests edit."""
    deflated, stamp = zipfile.ZIP_DEFLATED, (2024, 5, 6, 7, 8, 10)
    return _zip([
        ("[Content_Types].xml", _xml(
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            f'<Default Extension="rels" ContentType="{CT_RELS}"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/inner/main.xml" ContentType="{CT_MAIN}"/></Types>'),
         deflated, stamp),
        ("_rels/.rels", _rels(("rId1", REL_DOCUMENT, "inner/main.xml")), deflated, stamp),
        ("inner/main.xml", _xml(f'<tst:doc xmlns:tst="{NS}"><tst:item val="1"/></tst:doc>'),
         deflated, stamp),
        ("inner/values.xml", _xml(f'<tst:values xmlns:tst="{NS}" count="2"/>'), deflated, stamp),
    ])


def content_types() -> bytes:
    """Pretty-printed, as some producers write it, so layout upkeep is visible."""
    rows = [
        f'<Default Extension="rels" ContentType="{CT_RELS}"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Default Extension="bin" ContentType="application/octet-stream"/>',
        f'<Override PartName="/{MAIN}" ContentType="{CT_MAIN}"/>',
        f'<Override PartName="/{PAGE1}" ContentType="{CT_PAGE}"/>',
        f'<Override PartName="/{PAGE2}" ContentType="{CT_PAGE}"/>',
        f'<Override PartName="/{NOTE1}" ContentType="{CT_NOTE}"/>',
    ]
    return _xml(
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\r\n  '
        + "\r\n  ".join(rows) + "\r\n</Types>"
    )


def outer_package() -> bytes:
    deflated, stored = zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED
    early, late = (2020, 1, 2, 3, 4, 6), (2023, 11, 12, 13, 14, 16)
    main = _xml(
        f'<tst:doc xmlns:tst="{NS}" xmlns:r="{REL}"'
        ' xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
        ' xmlns:ext1="urn:ooxml-edit:test:ext1" mc:Ignorable="ext1">'
        '<tst:pages><tst:page r:id="rId1"/><tst:page r:id="rId2"/></tst:pages>'
        '<ext1:unknown ext1:keep="yes"/></tst:doc>'
    )
    page1 = _xml(
        f'<tst:page xmlns:tst="{NS}" xmlns:r="{REL}">'
        '<tst:picture r:embed="rId2"/><tst:object r:id="rId3"/></tst:page>'
    )
    page2 = _xml(f'<tst:page xmlns:tst="{NS}" xmlns:r="{REL}"><tst:picture r:embed="rId1"/></tst:page>')
    note1 = _xml(f'<tst:note xmlns:tst="{NS}"/>')
    return _zip([
        ("[Content_Types].xml", content_types(), deflated, early),
        ("_rels/.rels", _rels(("rId1", REL_DOCUMENT, MAIN)), deflated, early),
        (MAIN, main, deflated, early),
        ("doc/_rels/main.xml.rels", _rels(
            ("rId1", REL_PAGE, "pages/page1.xml"), ("rId2", REL_PAGE, "pages/page2.xml")),
         deflated, early),
        (PAGE1, page1, deflated, late),
        ("doc/pages/_rels/page1.xml.rels", _rels(
            ("rId1", REL_NOTE, "../notes/note1.xml"), ("rId2", REL_IMAGE, "../media/image1.png"),
            ("rId3", REL_PACKAGE, "/doc/embeddings/inner1.bin"),
            ("rId4", REL_LINK, "https://example.org/", "External")),
         deflated, late),
        (PAGE2, page2, deflated, late),
        ("doc/pages/_rels/page2.xml.rels", _rels(("rId1", REL_IMAGE, "../media/image1.png")),
         deflated, late),
        (NOTE1, note1, deflated, late),
        ("doc/notes/_rels/note1.xml.rels", _rels(("rId1", REL_PAGE, "../pages/page1.xml")),
         deflated, late),
        (IMAGE, PNG, stored, early),
        (EMBEDDED, inner_package(), stored, late),
        (ORPHAN, _xml(f'<tst:orphan xmlns:tst="{NS}"/>'), deflated, late),
    ])


def entries(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def infos(data: bytes) -> list[tuple[str, tuple[int, ...], int]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return [(i.filename, i.date_time, i.compress_type) for i in archive.infolist()]
