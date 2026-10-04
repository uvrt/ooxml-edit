"""DrawingML text rewritten in place, keeping mixed formatting."""

from __future__ import annotations

from lxml import etree

from ooxml_edit.charts.dmltext import paragraph_text, replace_body_text, runs
from ooxml_edit.xml import local_name, parse_xml

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"


def body(paragraphs: str) -> etree._Element:
    return parse_xml(f'<c:rich xmlns:c="{C}" xmlns:a="{A}"><a:bodyPr/><a:lstStyle/>'
                     f"{paragraphs}</c:rich>".encode())


def texts(element) -> list[str]:
    return [paragraph_text(p) for p in element.findall("{%s}p" % A)]


def runs_of(element) -> list[tuple[str, str | None]]:
    """``(text, b)`` for every run of every paragraph."""
    return [(r.find("{%s}t" % A).text, r.find("{%s}rPr" % A).get("b")
             if r.find("{%s}rPr" % A) is not None else None)
            for p in element.findall("{%s}p" % A) for r in runs(p)]


def test_a_changed_figure_keeps_its_formatting():
    rich = body('<a:p><a:r><a:rPr lang="en-US"/><a:t>Revenue grew </a:t></a:r>'
                '<a:r><a:rPr lang="en-US" b="1"/><a:t>12%</a:t></a:r></a:p>')
    replace_body_text(rich, "Revenue grew 15%")
    assert texts(rich) == ["Revenue grew 15%"]
    assert runs_of(rich) == [("Revenue grew ", None), ("15%", "1")]


def test_an_unchanged_paragraph_is_not_touched():
    rich = body('<a:p><a:r><a:t>One</a:t></a:r></a:p><a:p><a:r><a:t>Two</a:t></a:r></a:p>')
    first = rich.findall("{%s}p" % A)[0]
    first_run = runs(first)[0]
    replace_body_text(rich, "One\nThree")
    assert texts(rich) == ["One", "Three"]
    assert runs(rich.findall("{%s}p" % A)[0])[0] is first_run


def test_new_paragraphs_continue_the_formatting_of_the_last():
    rich = body('<a:p><a:pPr algn="ctr"/><a:r><a:rPr b="1"/><a:t>Title</a:t></a:r>'
                '<a:endParaRPr b="1"/></a:p>')
    replace_body_text(rich, "Title\nSubtitle\nMore")
    assert texts(rich) == ["Title", "Subtitle", "More"]
    for paragraph in rich.findall("{%s}p" % A):
        assert paragraph.find("{%s}pPr" % A).get("algn") == "ctr"
        assert [local_name(child) for child in paragraph] == ["pPr", "r", "endParaRPr"]
    assert all(b == "1" for _, b in runs_of(rich))


def test_a_line_break_is_a_vertical_tab():
    rich = body('<a:p><a:r><a:t>a</a:t></a:r></a:p>')
    replace_body_text(rich, "first\vsecond")
    paragraph = rich.find("{%s}p" % A)
    assert [local_name(child) for child in paragraph] == ["r", "br", "r"]
    assert paragraph_text(paragraph) == "first\vsecond"


def test_an_untouched_field_stays_a_field():
    rich = body('<a:p><a:r><a:t>As of </a:t></a:r><a:fld id="{1}" type="datetime1">'
                '<a:t>2024</a:t></a:fld><a:r><a:t>, ten</a:t></a:r></a:p>')
    replace_body_text(rich, "As of 2024, 10")
    paragraph = rich.find("{%s}p" % A)
    assert paragraph.find("{%s}fld" % A) is not None
    assert paragraph_text(paragraph) == "As of 2024, 10"


def test_an_empty_body_gets_its_properties_in_order():
    rich = parse_xml(f'<c:rich xmlns:c="{C}" xmlns:a="{A}"/>'.encode())
    replace_body_text(rich, "Hello")
    assert [local_name(child) for child in rich] == ["bodyPr", "lstStyle", "p"]
    assert texts(rich) == ["Hello"]


def test_removed_paragraphs_go():
    rich = body("".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in "abc"))
    replace_body_text(rich, "a\nc")
    assert texts(rich) == ["a", "c"]
