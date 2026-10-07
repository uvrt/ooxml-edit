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


# -- version (LE1) -----------------------------------------------------------------------------


def test_version_starts_at_zero_and_counts_steps():
    counter = Counter()
    history = History(counter)
    assert history.version == 0
    _step(history, counter, 1)
    _step(history, counter, 2)
    assert history.version == 2


def test_undo_and_redo_restore_the_version_of_the_state_they_bring_back():
    counter = Counter()
    history = History(counter)
    _step(history, counter, 1)
    _step(history, counter, 2)
    history.undo()
    assert (history.version, counter.value) == (1, 1)
    history.undo()
    assert (history.version, counter.value) == (0, 0)
    history.redo()
    assert (history.version, counter.value) == (1, 1)
    history.redo()
    assert (history.version, counter.value) == (2, 2)


def test_a_step_after_undo_never_reuses_a_version():
    """Equal versions mean equal content: a cache keyed by version is never stale."""
    counter = Counter()
    history = History(counter)
    _step(history, counter, 1)
    _step(history, counter, 2)
    history.undo()
    _step(history, counter, 7)
    assert history.version == 3
    seen = {}
    for _ in range(3):
        seen.setdefault(history.version, counter.value)
        assert seen[history.version] == counter.value
        history.undo()


def test_a_batch_is_one_version_and_a_failed_batch_none():
    counter = Counter()
    history = History(counter)
    with history.batch():
        _step(history, counter, 1)
        with history.batch():
            _step(history, counter, 2)
    assert history.version == 1
    with pytest.raises(RuntimeError):
        with history.batch():
            _step(history, counter, 3)
            raise RuntimeError("refused")
    assert (history.version, counter.value) == (1, 2)


def test_version_survives_the_depth_bound_and_clear():
    counter = Counter()
    history = History(counter, max_depth=2)
    for value in range(1, 6):
        _step(history, counter, value)
    assert history.version == 5
    history.undo()
    history.undo()
    assert history.version == 3 and not history.can_undo()
    history.clear()
    assert history.version == 3
    _step(history, counter, 9)
    assert history.version == 6


def test_a_batch_that_changes_nothing_is_no_step_and_keeps_the_version():
    counter = Counter()
    history = History(counter)
    _step(history, counter, 1)
    with history.batch():
        pass
    with history.batch():
        _step(history, counter, 2)
        _step(history, counter, 1)          # back where it began
    assert history.version == 1 and history.undo() and counter.value == 0
    assert not history.can_undo()


def test_an_empty_batch_on_a_package_records_nothing(tmp_path):
    from ooxml_edit.opc import OpcPackage
    import synthetic

    package = OpcPackage.open(synthetic.outer_package())
    history = History(package)
    with history.batch():
        package.tree(MAIN)                  # read, not changed
    assert history.version == 0 and not history.can_undo()
    with history.batch():
        history.checkpoint()
        package.tree(MAIN).set("seen", "1")
        package.mark_dirty(MAIN)
    assert history.version == 1 and history.can_undo()
