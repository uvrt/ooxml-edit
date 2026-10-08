"""Undo/redo over package snapshots.

Format-neutral: it needs only something that can :meth:`~Snapshottable.snapshot` and
:meth:`~Snapshottable.restore` itself, which :class:`~.opc.OpcPackage` does.

The approach is to snapshot the XML of the parts that have actually been modified, not the
whole package.  Everything else is still the bytes that were read from disk and cannot have
changed, so a checkpoint costs one serialization per edited part -- tens of kilobytes for a
typical part -- and restoring is exact rather than an attempt to
invert each operation.

Batches collapse several edits into one undo step.  They nest, and only the outermost pair has
an effect, so a compound operation built from primitives that each checkpoint still ends up as
a single step.  A batch that leaves the content as it found it records no step at all.

Every state the history can return to has a :attr:`History.version`.  A new step gets a number
no earlier state ever had, and undo and redo bring back the number of the state they restore,
so equal versions mean equal content and a cache keyed by version never serves a stale entry.

**Scoped undo.**  When several writers share one document -- one agent loop per page, say --
plain undo reverts whoever edited last.  :meth:`History.undo_in` takes a *scope*, a set of
part names (a page and the parts it owns), and reverts the latest step that touched the
scope, wherever it is in the history, leaving the later steps in place.  What a step touched
is found by comparing its snapshot with the next, part by part, so it needs a package that
can (:meth:`~.opc.OpcPackage.changed_between`, :meth:`~.opc.OpcPackage.snapshot_with`).  A
step may only be taken out if no later step touched any part it touched -- the same page,
but also a part both share, such as the main part when both added pages, or
``[Content_Types].xml`` when both added a new kind of part -- otherwise :class:`Entangled`
is raised and nothing changes.  The later steps are rewritten without the step (their
snapshots of its parts put back as they were before it), and get new versions.  A scoped
undo is not a step itself: :meth:`redo_in` with the same scope brings it back, as a new step,
while its parts are still as the undo left them.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Collection, Iterator, Protocol


class Snapshottable(Protocol):
    def snapshot(self) -> Any: ...

    def restore(self, snapshot: Any) -> None: ...


class Entangled(Exception):
    """The latest step in a scope touched parts a later step touched too, so it cannot be
    taken out alone.  ``parts`` are the step's, ``shared`` the ones later steps also touched."""

    def __init__(self, message: str, parts: Collection[str], shared: Collection[str]) -> None:
        super().__init__(message)
        self.parts = sorted(parts)
        self.shared = sorted(shared)


@dataclass(frozen=True)
class ScopedStep:
    """What a scoped undo or redo did: the step's parts and the versions around it.

    ``before`` and ``after`` are the versions the step went from and to when it was made;
    ``renumbered`` maps each rewritten state's old version to its new one (undo), and
    ``version`` is the document's version now."""

    parts: frozenset[str]
    before: int
    after: int
    version: int
    renumbered: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class _Undone:
    parts: frozenset[str]
    before_snapshot: Any
    after_snapshot: Any
    before: int
    after: int


class History:
    def __init__(self, package: Snapshottable, max_depth: int = 50) -> None:
        self._package = package
        self._max_depth = max_depth
        #: Each entry pairs a snapshot with the version of the state it holds.
        self._undo: list[tuple[Any, int]] = []
        self._redo: list[tuple[Any, int]] = []
        self._version = 0
        self._last_version = 0
        self._batch_depth = 0
        #: State as of the start of the outermost open batch.
        self._batch_snapshot: Any | None = None
        #: Steps taken out by :meth:`undo_in`, latest last, for :meth:`redo_in`.
        self._undone: list[_Undone] = []

    # -- checkpoints -----------------------------------------------------------------------

    def checkpoint(self) -> None:
        """Record the state *before* a mutation.  Called by mutators, not by callers."""
        if self._batch_depth:
            # Inside a batch the only checkpoint that matters is the one taken on entry.
            return
        self._push(self._package.snapshot())

    def _push(self, snapshot: Any) -> None:
        self._undo.append((snapshot, self._version))
        if len(self._undo) > self._max_depth:
            del self._undo[0]
        self._redo.clear()
        self._last_version += 1
        self._version = self._last_version

    @property
    def version(self) -> int:
        """The current state's number: 0 when opened, a new one for every step recorded.

        Monotonic in the sense that a step never reuses a number, even after undo; undo and
        redo restore the number of the state they bring back.  A batch counts once, when the
        outermost one closes, and a batch that fails leaves the version as it was.
        """
        return self._version

    # -- batching --------------------------------------------------------------------------

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Collapse everything inside into a single undo step.  Nestable.

        A batch whose content ends as it began (a read, or edits that cancel out) records no
        step and keeps the version; a failed one rolls back and records none."""
        if self._batch_depth == 0:
            self._batch_snapshot = self._package.snapshot()
        self._batch_depth += 1
        try:
            yield
        except Exception:
            # A failed batch rolls back rather than leaving a half-applied edit behind.
            if self._batch_depth == 1 and self._batch_snapshot is not None:
                self._package.restore(self._batch_snapshot)
                self._batch_snapshot = None
            self._batch_depth -= 1
            raise
        self._batch_depth -= 1
        if self._batch_depth == 0 and self._batch_snapshot is not None:
            snapshot, self._batch_snapshot = self._batch_snapshot, None
            # A batch that changed nothing is no step: undo would otherwise undo nothing,
            # and the version would claim a new state with the old content.
            if self._package.snapshot() != snapshot:
                self._push(snapshot)

    @property
    def in_batch(self) -> bool:
        return self._batch_depth > 0

    # -- moving through history ------------------------------------------------------------

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        snapshot, version = self._undo.pop()
        self._redo.append((self._package.snapshot(), self._version))
        self._package.restore(snapshot)
        self._version = version
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        snapshot, version = self._redo.pop()
        self._undo.append((self._package.snapshot(), self._version))
        self._package.restore(snapshot)
        self._version = version
        return True

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._undone.clear()

    # -- scoped undo -----------------------------------------------------------------------

    def _parts_api(self) -> Any:
        package = self._package
        if not (hasattr(package, "changed_between") and hasattr(package, "snapshot_with")):
            raise TypeError("a scoped undo needs a package that compares snapshots part by "
                            "part (changed_between, snapshot_with)")
        if self._batch_depth:
            raise RuntimeError("no scoped undo inside a batch")
        return package

    def undo_in(self, scope: Collection[str]) -> ScopedStep | None:
        """Undo the latest step that touched a part in ``scope``; ``None`` if none did.

        Raises :class:`Entangled`, changing nothing, when a later step touched a part that
        step touched.  The global redo history is dropped, as after any change."""
        package = self._parts_api()
        scope = set(scope)
        current = package.snapshot()
        states = [snapshot for snapshot, _ in self._undo] + [current]
        touched = [package.changed_between(states[k], states[k + 1])
                   for k in range(len(self._undo))]
        index = next((k for k in reversed(range(len(touched))) if touched[k] & scope), None)
        if index is None:
            return None
        parts = touched[index]
        shared = set().union(*touched[index + 1:]) & parts if index + 1 < len(touched) else set()
        if shared:
            raise Entangled(f"the latest step in scope also changed {', '.join(sorted(shared))}, "
                            "which later steps changed too", parts, shared)
        before_snapshot, before = self._undo[index]
        after = self._undo[index + 1][1] if index + 1 < len(self._undo) else self._version
        renumbered: dict[int, int] = {}
        # Each later state without the step.  The first is the step's own "before" content,
        # so it keeps that version; the others are states that never existed: new versions.
        rewritten: list[tuple[Any, int]] = []
        for snapshot, version in self._undo[index + 1:] + [(current, self._version)]:
            snapshot = package.snapshot_with(snapshot, before_snapshot, parts)
            if not rewritten:
                new = before
            else:
                self._last_version += 1
                new = self._last_version
            renumbered[version] = new
            rewritten.append((snapshot, new))
        new_current, new_version = rewritten.pop()
        package.restore(new_current)
        self._undo = self._undo[:index] + rewritten
        self._version = new_version
        self._redo.clear()
        self._undone.append(_Undone(parts, before_snapshot, states[index + 1], before, after))
        del self._undone[:-self._max_depth]
        return ScopedStep(parts, before, after, new_version, renumbered)

    def redo_in(self, scope: Collection[str]) -> ScopedStep | None:
        """Bring back the latest step :meth:`undo_in` took out that touched ``scope``, as a
        new step; ``None`` if there is none.  Raises :class:`Entangled`, changing nothing,
        when its parts changed since it was taken out."""
        package = self._parts_api()
        scope = set(scope)
        index = next((k for k in reversed(range(len(self._undone)))
                      if self._undone[k].parts & scope), None)
        if index is None:
            return None
        undone = self._undone[index]
        current = package.snapshot()
        moved = package.changed_between(
            current, package.snapshot_with(current, undone.before_snapshot, undone.parts))
        if moved:
            raise Entangled(f"{', '.join(sorted(moved))} changed since the undo", undone.parts,
                            moved)
        del self._undone[index]
        self._push(current)
        package.restore(package.snapshot_with(current, undone.after_snapshot, undone.parts))
        return ScopedStep(undone.parts, undone.before, undone.after, self._version)

    def can_undo_in(self, scope: Collection[str]) -> bool:
        """Whether some step in the history touched ``scope`` (entangled or not)."""
        package = self._parts_api()
        scope = set(scope)
        states = [snapshot for snapshot, _ in self._undo] + [package.snapshot()]
        return any(package.changed_between(states[k], states[k + 1]) & scope
                   for k in range(len(self._undo)))
