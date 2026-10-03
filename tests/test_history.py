"""Undo, redo and batches, over any snapshottable package."""

from __future__ import annotations

import pytest

from ooxml_edit.history import History

from synthetic import MAIN, PAGE2


class Counter:
    """The smallest snapshottable thing: one value."""

    def __init__(self) -> None:
        self.value = 0

    def snapshot(self) -> int:
        return self.value

    def restore(self, snapshot: int) -> None:
        self.value = snapshot


def _step(history: History, counter: Counter, value: int) -> None:
    history.checkpoint()
    counter.value = value


def test_undo_and_redo_walk_the_steps():
    counter = Counter()
    history = History(counter)
    assert not history.can_undo() and not history.undo() and not history.redo()
    _step(history, counter, 1)
    _step(history, counter, 2)
    assert history.undo() and counter.value == 1
    assert history.undo() and counter.value == 0
    assert not history.can_undo() and history.can_redo()
    assert history.redo() and counter.value == 1
    assert history.redo() and counter.value == 2
    assert not history.can_redo()


def test_a_new_step_clears_redo():
    counter = Counter()
    history = History(counter)
    _step(history, counter, 1)
    history.undo()
    _step(history, counter, 5)
    assert not history.can_redo()


def test_history_is_bounded():
    counter = Counter()
    history = History(counter, max_depth=3)
    for value in range(1, 6):
        _step(history, counter, value)
    while history.undo():
        pass
    assert counter.value == 2


def test_nested_batches_are_one_step():
    counter = Counter()
    history = History(counter)
    with history.batch():
        _step(history, counter, 1)
        with history.batch():
            assert history.in_batch
            _step(history, counter, 2)
        _step(history, counter, 3)
    assert not history.in_batch
    assert history.undo() and counter.value == 0
    assert not history.can_undo()


def test_a_failed_batch_rolls_back_and_records_nothing():
    counter = Counter()
    history = History(counter)
    _step(history, counter, 1)
    with pytest.raises(RuntimeError):
        with history.batch():
            _step(history, counter, 2)
            with history.batch():
                _step(history, counter, 3)
                raise RuntimeError("halfway")
    assert counter.value == 1 and not history.in_batch
    assert history.undo() and counter.value == 0
    assert not history.can_undo()


def test_clear_forgets_everything():
    counter = Counter()
    history = History(counter)
    _step(history, counter, 1)
    history.undo()
    history.clear()
    assert not history.can_undo() and not history.can_redo()


def test_undo_over_a_package_is_byte_exact(data, package):
    history = History(package)
    with history.batch():
        package.tree(MAIN).set("a", "1")
        package.mark_dirty(MAIN)
        root = package.tree(PAGE2)
        root.remove(root[0])
        package.mark_dirty(PAGE2)
        package.release(PAGE2, ["rId1"])
    edited = package.to_bytes()
    assert edited != data
    assert history.undo()
    assert package.to_bytes() == data
    assert history.redo()
    assert package.to_bytes() == edited
