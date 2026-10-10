"""The worker pool under every start method this platform has.

Python 3.14 changed the default on Linux from ``fork`` to ``forkserver``.  The pool names
its own (``spawn``) and does not follow the default; whichever is asked for, a task, its
arguments, the font folders handed to a worker and an exception all cross the boundary by
pickling, so the pool works the same under each.  ``fork`` is Linux only here: macOS'
system libraries are not safe to fork with threads running.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import warnings

import pytest

from ooxml_edit.tools import Toolbox, WorkerPool

import synthetic
import tools_toys

METHODS = [method for method in multiprocessing.get_all_start_methods()
           if method != "fork" or sys.platform.startswith("linux")]


def test_the_pool_names_spawn_whatever_the_platforms_default():
    with WorkerPool(1) as pool:
        assert pool._context.get_start_method() == "spawn"
        assert pool.run(tools_toys.worker_pid, timeout=60) != os.getpid()


@pytest.mark.parametrize("method", METHODS)
def test_a_render_its_font_folders_and_an_error_cross_under(method, tmp_path):
    with warnings.catch_warnings():
        # fork() in a process with threads is a DeprecationWarning since 3.12; the pool
        # does not use fork unless asked, and this test asks.
        warnings.simplefilter("ignore", DeprecationWarning)
        with WorkerPool(1, start_method=method, fallback=False) as pool:
            pid, seen = pool.run(tools_toys.fonts_seen, (str(tmp_path),), timeout=60)
            assert pid != os.getpid() and seen == (str(tmp_path),)
            image, width, height = pool.run(tools_toys.render_page, synthetic.outer_package(),
                                            1, 32, timeout=60)
            assert image and (width, height) == (32, 18)
            with pytest.raises(IndexError, match="no page 9"):
                pool.run(tools_toys.render_page, synthetic.outer_package(), 9, 32, timeout=60)


@pytest.mark.parametrize("method", METHODS)
def test_a_toolbox_renders_in_a_worker_under(method):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        with Toolbox(tools_toys.TOOLS, formats=tools_toys.FORMATS, workers=1,
                     start_method=method) as box:
            session = box.session()
            session.open(synthetic.outer_package(), "deck.pptx")
            result = box.dispatch(session, "toy_ppt_render", {"doc": "d1", "page": 1, "width": 32})
            assert result.ok, result.to_json()
            assert not box.pool.in_process
