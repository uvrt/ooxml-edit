"""Round 39: fast renders (cheaper, approximate pixels of the same SVG, kept apart from the
exact images and said so) and the render lane (rasterising yields to other work, its
deadline counts from when a worker takes it, a worker past it finishes the image)."""

from __future__ import annotations

import threading
import time

import pytest

from ooxml_edit.errors import ArgumentError
from ooxml_edit.tools import InProcess, SubprocessPool, Toolbox, WorkerPool, render
from ooxml_edit.tools.results import ToolError
from ooxml_edit.tools.worker import RENDER_LANE, run_all, takes_lane

import synthetic
import tools_toys


@pytest.fixture
def toolbox():
    with Toolbox([tools_toys.toy_ppt_set_title, tools_toys.toy_draw],
                 formats=tools_toys.FORMATS, groups=tools_toys.GROUPS,
                 runner=InProcess(2)) as box:
        yield box


def _open(box):
    session = box.session()
    doc = session.open(synthetic.outer_package(), "deck.pptx")
    for page, text in ((1, "First"), (2, "Second")):      # two pages, two SVGs
        box.dispatch(session, "toy_ppt_set_title", {"doc": doc, "page": page, "text": text})
    return session, doc


def _draw(box, session, doc, pages, **more):
    result = box.dispatch(session, "toy_draw", {"doc": doc, "pages": pages, **more})
    assert result.ok, result.error and result.error.to_json()
    return result


@pytest.mark.parametrize("shape", ["fast", "fast-whole"])
def test_a_fast_image_is_approximate_kept_apart_and_never_preferred_to_an_exact_one(toolbox, shape):
    session, doc = _open(toolbox)
    fast = _draw(toolbox, session, doc, [1, 2], shape=shape, fast=True)
    assert fast.data["approximate"] == [1, 2] and fast.data["cached"] == []
    assert fast.data["meta"][1] == {"page": 1, "fast": True}
    again = _draw(toolbox, session, doc, [1], shape=shape, fast=True)
    assert again.data["cached"] == [1] and again.data["approximate"] == [1]
    exact = _draw(toolbox, session, doc, [1], shape=shape)
    assert exact.data["cached"] == [] and exact.data["approximate"] == []   # drawn exactly
    assert exact.images[0].data != fast.images[0].data
    # An exact image held is what a fast render gets: never a worse one than the store has.
    both = _draw(toolbox, session, doc, [1, 2], shape=shape, fast=True)
    assert both.data["cached"] == [1, 2] and both.data["approximate"] == [2]
    assert both.images[0].data == exact.images[0].data
    # In another session, by content: the same.
    other, copy = _open(toolbox)
    assert _draw(toolbox, other, copy, [1, 2], shape=shape, fast=True).data["approximate"] == [2]


@pytest.mark.parametrize("shape", ["alone", "whole"])
def test_a_renderer_without_fast_pixels_draws_exactly(toolbox, shape):
    session, doc = _open(toolbox)
    fast = _draw(toolbox, session, doc, [1, 2], shape=shape, fast=True)
    assert fast.data["approximate"] == []
    assert _draw(toolbox, session, doc, [1, 2], shape=shape).data["cached"] == [1, 2]


def test_the_base_renderer_has_no_fast_pixels():
    assert render.RendererBase.fast is False
    assert tools_toys.RENDERERS["fast"].fast is True
    assert render.fast_key(tools_toys.RENDERERS["fast"], "<svg/>", "f") != render.raster_key(
        tools_toys.RENDERERS["fast"], "<svg/>", "f")


POOLS = [pytest.param(lambda size: WorkerPool(size), id="processes"),
         pytest.param(lambda size: SubprocessPool(size), id="subprocesses")]


@pytest.mark.parametrize("make", POOLS)
def test_a_render_task_yields_to_other_work_waiting_for_a_worker(make):
    with make(1) as pool:
        pool.warm(wait=True)
        done: dict = {}

        def run(name, lane, start):
            time.sleep(start)
            pool.run(tools_toys.keep_value, name, 1, 0.2, timeout=60, lane=lane)
            done[name] = time.monotonic()

        threads = [threading.Thread(target=run, args=("busy", None, 0.0)),
                   threading.Thread(target=run, args=("image", RENDER_LANE, 0.05)),
                   threading.Thread(target=run, args=("layout", None, 0.15))]
        busy, image, layout = threads
        for thread in threads:
            thread.start()
        busy.join()
        # The busy one first; then the layout that came after the image takes the worker.
        for thread in threads:
            thread.join()
        assert done["busy"] < done["layout"] < done["image"]


def test_render_workers_bounds_the_workers_drawing_at_once():
    with WorkerPool(2) as pool:
        pool.render_workers = 1
        pool.warm(wait=True)
        began = time.monotonic()
        pids = run_all(pool, tools_toys.keep_value, [("a", 1, 0.5), ("b", 1, 0.5)], [60, 60],
                       [{"lane": RENDER_LANE}, {"lane": RENDER_LANE}])
        assert time.monotonic() - began >= 0.95          # one after the other
        assert all(isinstance(pid, tuple) for pid in pids)
        # Other work still has the second worker meanwhile.
        began = time.monotonic()
        both = run_all(pool, tools_toys.keep_value, [("c", 1, 0.5), ("d", 1, 0.5)], [60, 60],
                       [{"lane": RENDER_LANE}, {}])
        assert time.monotonic() - began < 0.95 and both[0][0] != both[1][0]


@pytest.mark.parametrize("make", POOLS)
def test_a_render_task_past_its_deadline_is_finished_and_its_worker_kept(make):
    with make(1) as pool:
        pool.warm(wait=True)
        before = pool.run(tools_toys.worker_pid, timeout=60)
        late: list = []
        with pytest.raises(ToolError) as caught:
            pool.run(tools_toys.keep_value, "late", 7, 0.9, timeout=0.6, lane=RENDER_LANE,
                     on_late=late.append)
        assert caught.value.code == "timeout" and "finishes it" in caught.value.message
        assert pool.run(tools_toys.worker_pid, timeout=60) == before     # waited for, kept
        deadline = time.monotonic() + 5
        while not late and time.monotonic() < deadline:
            time.sleep(0.05)
        assert late == [(before, None)]
        assert pool.killed == [] and pool.overruns == {"finished": 1, "stopped": 0}


@pytest.mark.parametrize("make", POOLS)
def test_a_render_task_still_busy_as_long_again_is_stopped(make):
    with make(1) as pool:
        pool.warm(wait=True)
        before = pool.run(tools_toys.worker_pid, timeout=60)
        with pytest.raises(ToolError):
            pool.run(tools_toys.keep_value, "stuck", 1, 5.0, timeout=0.3, lane=RENDER_LANE)
        after = pool.run(tools_toys.worker_pid, timeout=60)
        assert after != before and pool.killed == [before]
        assert pool.overruns == {"finished": 0, "stopped": 1}


def test_other_work_is_still_stopped_at_its_deadline():
    with WorkerPool(1) as pool:
        pool.warm(wait=True)
        before = pool.run(tools_toys.worker_pid, timeout=60)
        with pytest.raises(ToolError, match="it was stopped"):
            pool.run(tools_toys.keep_value, "slow", 1, 2.0, timeout=0.3)
        assert pool.killed == [before]


def test_a_lane_is_one_the_pool_knows():
    with WorkerPool(1) as pool:
        with pytest.raises(ArgumentError, match="lane is one of"):
            pool.run(tools_toys.worker_pid, timeout=60, lane="fast")


def test_runners_of_one_process_take_a_lane_and_need_it_not():
    with InProcess(1) as inline:
        assert inline.run(tools_toys.worker_pid, timeout=60, lane=RENDER_LANE, on_late=print)
    assert takes_lane(inline) and not takes_lane(object())


def test_an_application_runner_is_handed_a_lane_only_when_it_says_it_takes_one():
    seen: list = []

    class Plain:
        size = 1

        def run(self, fn, *args, timeout, **kwargs):
            seen.append(sorted(kwargs))
            return fn(*args)

        def close(self):
            pass

    class Lanes(Plain):
        takes_lane = True

    for runner, expected in ((Plain(), []), (Lanes(), ["lane", "on_late"])):
        seen.clear()
        with Toolbox([tools_toys.toy_ppt_set_title, tools_toys.toy_draw],
                     formats=tools_toys.FORMATS, groups=tools_toys.GROUPS, runner=runner) as box:
            session = box.session()
            doc = session.open(synthetic.outer_package(), "deck.pptx")
            assert box.dispatch(session, "toy_draw", {"doc": doc, "pages": [1],
                                                      "shape": "whole"}).ok
        assert seen and seen[-1] == expected


def test_other_work_asked_to_overrun_is_finished_too():
    """A render's layout (``Call.run(..., queued=True)``): its worker holds layouts worth more
    than the deadline, so it is finished, not stopped."""
    with WorkerPool(1) as pool:
        pool.warm(wait=True)
        before = pool.run(tools_toys.worker_pid, timeout=60)
        with pytest.raises(ToolError, match="finishes it"):
            pool.run(tools_toys.keep_value, "layout", 1, 0.9, timeout=0.6, overrun=True)
        assert pool.run(tools_toys.worker_pid, timeout=60) == before and pool.killed == []
