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
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Protocol


class Snapshottable(Protocol):
    def snapshot(self) -> Any: ...

    def restore(self, snapshot: Any) -> None: ...


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
