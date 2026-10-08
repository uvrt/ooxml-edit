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


# -- scoped undo: several writers on one package -----------------------------------------------


from ooxml_edit.history import Entangled  # noqa: E402
from ooxml_edit.opc import OpcPackage  # noqa: E402

from synthetic import IMAGE, PAGE1, REL_IMAGE, outer_package  # noqa: E402


def _title(package: OpcPackage, part: str, text: str, history: History) -> None:
    history.checkpoint()
    package.tree(part).set("title", text)
    package.mark_dirty(part)


def _page(package: OpcPackage, part: str) -> list[str]:
    return package.reachable_parts(part, lambda rel: False)


def _titles(package: OpcPackage) -> tuple:
    return tuple(package.tree(part).get("title") for part in (PAGE1, PAGE2))


def test_a_scoped_undo_reverts_the_latest_step_in_scope_and_keeps_the_others():
    package = OpcPackage.open(outer_package())
    original = package.to_bytes()
    history = History(package)
    _title(package, PAGE1, "A", history)        # v1
    _title(package, PAGE2, "B", history)        # v2
    _title(package, PAGE1, "C", history)        # v3
    step = history.undo_in(_page(package, PAGE2))
    assert step.parts == {PAGE2} and (step.before, step.after) == (1, 2)
    assert _titles(package) == ("C", None)
    assert history.version not in (0, 1, 2, 3) and step.renumbered[3] == history.version
    # The history is the two page-1 steps now: undo walks them, then nothing.
    assert history.undo() and _titles(package) == ("A", None)
    assert history.undo() and _titles(package) == (None, None)
    assert not history.undo() and package.to_bytes() == original
    assert history.redo() and history.redo() and _titles(package) == ("C", None)
    # Scoped redo brings page 2's step back as a new step.
    assert history.redo_in(_page(package, PAGE2)).parts == {PAGE2}
    assert _titles(package) == ("C", "B")
    assert history.undo() and _titles(package) == ("C", None)


def test_a_scoped_undo_of_the_latest_step_is_a_plain_undo():
    package = OpcPackage.open(outer_package())
    history = History(package)
    _title(package, PAGE1, "A", history)
    _title(package, PAGE2, "B", history)
    step = history.undo_in(_page(package, PAGE2))
    assert history.version == 1 and step.renumbered == {2: 1}
    assert _titles(package) == ("A", None)
    assert history.undo_in(_page(package, PAGE2)) is None       # nothing left in scope
    assert history.can_undo_in(_page(package, PAGE1)) and not history.can_undo_in([IMAGE])


def test_a_step_sharing_a_part_with_a_later_one_is_entangled_and_nothing_changes():
    package = OpcPackage.open(outer_package())
    history = History(package)
    with history.batch():                        # page 1 and the main part, as adding a page does
        _title(package, PAGE1, "A", history)
        _title(package, MAIN, "m1", history)
    _title(package, MAIN, "m2", history)         # another writer, the shared part
    before = package.to_bytes(), history.version
    with pytest.raises(Entangled) as caught:
        history.undo_in(_page(package, PAGE1))
    assert caught.value.shared == [MAIN] and set(caught.value.parts) == {PAGE1, MAIN}
    assert (package.to_bytes(), history.version) == before
    assert history.undo() and history.undo_in(_page(package, PAGE1))   # untangled
    assert _titles(package) == (None, None) and package.tree(MAIN).get("title") is None


def test_a_scoped_redo_is_refused_once_its_parts_changed_again():
    package = OpcPackage.open(outer_package())
    history = History(package)
    _title(package, PAGE1, "A", history)
    _title(package, PAGE2, "B", history)
    history.undo_in(_page(package, PAGE1))
    _title(package, PAGE1, "Z", history)
    with pytest.raises(Entangled):
        history.redo_in(_page(package, PAGE1))
    assert _titles(package) == ("Z", "B")
    assert history.redo_in(_page(package, PAGE2)) is None


def test_a_scoped_undo_puts_back_an_added_part_and_its_relationship():
    package = OpcPackage.open(outer_package())
    original = package.to_bytes()
    history = History(package)
    with history.batch():
        package.add_part("doc/media/image9.png", b"\x89PNG\r\n\x1a\n9", "image/png")
        package.add_relationship(PAGE1, REL_IMAGE, "doc/media/image9.png")
    _title(package, PAGE2, "B", history)
    scope = _page(package, PAGE1)
    assert "doc/media/image9.png" not in scope       # follow nothing: the page and its rels
    step = history.undo_in(scope)
    assert "doc/media/image9.png" in step.parts and not package.has_part("doc/media/image9.png")
    assert history.undo() and package.to_bytes() == original


def test_scoped_undo_needs_a_package_that_compares_parts():
    with pytest.raises(TypeError):
        History(Counter()).undo_in(["x"])
