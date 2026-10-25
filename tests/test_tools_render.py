"""The shared render pipeline (``ooxml_edit.tools.render``), with a toy renderer.

The formats' agents render through it (pptx-agent's slides, docx-agent's pages, each with
its own :class:`Renderer` adapter); these tests hold what it does whatever the renderer:
images kept by content and found again after an edit, both shapes of work, the state
handed over and the facts handed back, a failed chunk keeping what the others drew, every
PNG checked, and nothing of any format's renderer imported."""

from __future__ import annotations

import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from ooxml_edit.tools import InProcess, SubprocessPool, Toolbox, WorkerPool, render
from ooxml_edit.tools.worker import task_reference
from ooxml_edit.tools.render import (PNG_SIGNATURE, Layout, Renderer, View, checked_png,
                                     fingerprint, png_size, raster_key)

import synthetic
import tools_toys


@pytest.fixture
def toolbox():
    with Toolbox([tools_toys.toy_ppt_set_title, tools_toys.toy_draw, tools_toys.toy_tiles],
                 formats=tools_toys.FORMATS, groups=tools_toys.GROUPS,
                 runner=InProcess(2)) as box:
        yield box


def _open(box):
    session = box.session()
    return session, session.open(synthetic.outer_package(), "deck.pptx")


def _draw(box, session, doc, pages, **more):
    result = box.dispatch(session, "toy_draw", {"doc": doc, "pages": pages, **more})
    assert result.ok, result.error and result.error.to_json()
    return result


@pytest.mark.parametrize("shape", ["alone", "whole"])
def test_a_view_an_edit_left_as_it_was_is_found_again(toolbox, shape):
    session, doc = _open(toolbox)
    first = _draw(toolbox, session, doc, [1, 2], shape=shape)
    assert first.data["drawn"] == [1, 2] and first.data["cached"] == []
    assert [image.label for image in first.images] == ["page 1", "page 2"]
    assert first.data["meta"] == {1: {"page": 1}, 2: {"page": 2}}
    again = _draw(toolbox, session, doc, [2, 1], shape=shape)
    assert again.data["cached"] == [1, 2] and again.data["facts"] == []   # no task ran
    toolbox.dispatch(session, "toy_ppt_set_title", {"doc": doc, "page": 1, "text": "Changed"})
    edited = _draw(toolbox, session, doc, [1, 2], shape=shape)
    assert edited.data["cached"] == [2]                # page 2's SVG did not change
    assert edited.data["meta"][2] == {"page": 2}       # its meta kept with its image
    assert [image.data for image in edited.images][1] == first.images[1].data


def test_the_two_shapes_key_and_draw_alike_under_their_own_names(toolbox):
    session, doc = _open(toolbox)
    alone = _draw(toolbox, session, doc, [1, 2], shape="alone")
    session, doc = _open(toolbox)                      # the same bytes, in another session
    assert _draw(toolbox, session, doc, [1, 2], shape="alone").data["cached"] == [1, 2]
    session, doc = _open(toolbox)
    whole = _draw(toolbox, session, doc, [1, 2], shape="whole")
    assert whole.data["cached"] == []                  # another renderer: other keys
    assert [i.data for i in alone.images] == [i.data for i in whole.images]


def test_an_item_the_document_lacks_is_left_out(toolbox):
    session, doc = _open(toolbox)
    for shape in ("alone", "whole"):
        assert _draw(toolbox, session, doc, [2, 9], shape=shape).data["drawn"] == [2]


def test_the_state_goes_to_the_workers_and_their_facts_come_back(toolbox):
    session, doc = _open(toolbox)
    whole = _draw(toolbox, session, doc, [1, 2], shape="whole")
    assert whole.data["facts"] == ["kept"] and whole.data["seen"] == [("kept", False)]
    session, doc = _open(toolbox)
    toolbox.dispatch(session, "toy_ppt_set_title", {"doc": doc, "page": 1, "text": "Another"})
    toolbox.dispatch(session, "toy_ppt_set_title", {"doc": doc, "page": 2, "text": "Version"})
    alone = _draw(toolbox, session, doc, [1, 2], first_alone=True)
    # The first chunk alone, with the state given; the other with what on_facts returned.
    assert alone.data["seen"] == [("kept", True), ("kept", False), ("handed", False)]
    assert alone.data["facts"] == ["kept", "handed"]


def test_a_chunk_that_fails_keeps_what_the_others_drew(toolbox, monkeypatch):
    monkeypatch.setattr(tools_toys, "FAILING_PAGE", 2)
    for shape in ("alone", "whole"):
        session, doc = _open(toolbox)
        toolbox.dispatch(session, "toy_ppt_set_title", {"doc": doc, "page": 1, "text": shape})
        failed = toolbox.dispatch(session, "toy_draw", {"doc": doc, "pages": [1, 2],
                                                        "shape": shape})
        assert not failed.ok
        monkeypatch.setattr(tools_toys, "FAILING_PAGE", None)
        kept = _draw(toolbox, session, doc, [1, 2], shape=shape)
        assert kept.data["cached"] == [1], shape
        monkeypatch.setattr(tools_toys, "FAILING_PAGE", 2)


def test_an_image_that_is_not_a_png_is_the_renderers_fault(toolbox):
    session, doc = _open(toolbox)
    result = toolbox.dispatch(session, "toy_draw", {"doc": doc, "pages": [1], "shape": "broken"})
    assert not result.ok and result.error.code == "internal"
    with pytest.raises(RuntimeError, match="toy-broken renderer returned an image that is not a PNG"):
        render.rasterise_views(tools_toys.RENDERERS["broken"], [("<svg/>", None)], 64)


def test_a_contact_image_is_rasterised_by_the_renderer_with_its_warnings_left_out(toolbox, recwarn):
    session, doc = _open(toolbox)
    result = toolbox.dispatch(session, "toy_tiles", {"doc": doc, "pages": [1, 2]})
    assert result.ok and png_size(result.images[0].data) == (8, 8)
    assert not [w for w in recwarn if issubclass(w.category, tools_toys.ToyWarning)]


def test_warm_up_stamps_the_code_and_leaves_the_warnings_out(recwarn):
    tools_toys.toy_warm_up()
    assert not [w for w in recwarn if issubclass(w.category, tools_toys.ToyWarning)]


def test_png_size_reads_the_header_and_refuses_what_is_not_a_png():
    assert png_size(tools_toys.png(7, 3)) == (7, 3)
    assert tools_toys.png(1, 1).startswith(PNG_SIGNATURE)
    for data in (b"", PNG_SIGNATURE, b"GIF89a" + bytes(30), PNG_SIGNATURE + bytes(16)):
        with pytest.raises(ValueError, match="not a PNG"):
            png_size(data)
    with pytest.raises(RuntimeError, match="returned str"):
        checked_png("png", tools_toys.RENDERERS["alone"])
    assert checked_png(bytearray(tools_toys.png(2, 2))) == tools_toys.png(2, 2)


def test_a_key_is_the_svg_the_pixels_depend_on_and_the_fingerprint():
    toy = tools_toys.RENDERERS["alone"]
    fp = fingerprint(toy, ["fonts"])
    assert fp != fingerprint(toy, ["other fonts"])
    assert fp != fingerprint(tools_toys.RENDERERS["whole"], ["fonts"])     # its own namespace
    assert raster_key(toy, "<svg/>", fp) != raster_key(toy, "<svg />", fp)
    assert raster_key(toy, "<svg/>", fp) != raster_key(toy, "<svg/>", "another")
    # key_text: what the rasteriser never reads is not in the key.
    assert (raster_key(toy, '<svg data-toy-id="1"/>', fp)
            == raster_key(toy, '<svg data-toy-id="2"/>', fp))


def test_a_renderer_is_the_protocol_and_pickles():
    toy = tools_toys.RENDERERS["whole"]
    assert isinstance(toy, Renderer)
    assert pickle.loads(pickle.dumps(toy)) == toy and toy != tools_toys.RENDERERS["alone"]
    layout = Layout([View(1, "<svg/>")], "fp")
    assert layout.raster is None and layout.facts is None and layout.views[0].info is None


def test_the_tasks_are_worker_tasks_a_subprocess_worker_runs():
    for task in (render.lay_out_views, render.rasterise_views, render.render_views,
                 render.rasterise_tiles):
        assert task_reference(task) == f"ooxml_edit.tools.render:{task.__name__}"


def test_the_pipeline_imports_no_renderer():
    code = ("import sys\n"
            "import ooxml_edit.tools.render\n"
            "loaded = sorted(m for m in sys.modules if m.split('.')[0] in "
            "('pptx2svg', 'docx2svg', 'resvg_py', 'cairosvg', 'pptx_agent', 'docx_agent', "
            "'xlsx_agent'))\n"
            "assert not loaded, loaded\n")
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                               cwd=str(Path(__file__).parents[1] / "src"))
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("make", [lambda: WorkerPool(2), lambda: SubprocessPool(2)],
                         ids=["worker-pool", "subprocess-pool"])
@pytest.mark.parametrize("shape", ["alone", "whole"])
def test_the_renderer_crosses_to_worker_processes(make, shape):
    with Toolbox([tools_toys.toy_draw], formats=tools_toys.FORMATS, groups=tools_toys.GROUPS,
                 runner=make()) as box:
        session, doc = _open(box)
        result = _draw(box, session, doc, [1, 2], shape=shape)
        assert result.data["drawn"] == [1, 2] and len(result.images) == 2
        assert all(f == "kept" for f in result.data["facts"])
