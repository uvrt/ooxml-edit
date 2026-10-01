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
a single step.
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
        self._undo: list[Any] = []
        self._redo: list[Any] = []
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
        self._undo.append(snapshot)
        if len(self._undo) > self._max_depth:
            del self._undo[0]
        self._redo.clear()

    # -- batching --------------------------------------------------------------------------

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Collapse everything inside into a single undo step.  Nestable."""
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
            self._push(self._batch_snapshot)
            self._batch_snapshot = None

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
        self._redo.append(self._package.snapshot())
        self._package.restore(self._undo.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._package.snapshot())
        self._package.restore(self._redo.pop())
        return True

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
