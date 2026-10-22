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
snapshots of its parts put back as they were before it), and get new versions.  Some parts
belong to every scope at once -- templates every page is drawn from, say -- and a step that
changed one is entangled with every other scope by its nature:
``undo_in(scope, shared=...)`` refuses it whatever came after.  A scoped
undo is not a step itself: :meth:`redo_in` with the same scope brings it back, as a new step,
while its parts are still as the undo left them.

**One step by its version.**  :meth:`History.steps` lists the steps undo can reach, each by
the versions it went from and to (and the parts it touched), and :meth:`History.undo_step`
takes out the one that made a given version -- under the same rule as :meth:`undo_in`, so an
application that knows who made each step (the tool layer's views) can take back one
writer's latest step and refuse when a later step depends on it.  :meth:`redo_step` brings
it back; :attr:`History.next_redo` is the version a plain :meth:`redo` would restore.

**What changed.**  :meth:`History.watch` reports what the package's content did while it
was open: each state change the history makes -- an outermost batch closing, an undo, a
redo, a scoped undo or redo, a single checkpointed edit -- is reported to every open watch,
which keeps the state before the first and after the last (:class:`Change`).  The snapshots
are the ones the history takes anyway, so a watch costs nothing until it is asked which
parts differ (:attr:`Change.parts`); only an edit checkpointed outside a batch, whose
after-state no one took, costs one snapshot when the watch closes.  The tool layer watches
every call, so its results say what each changed (``Result.touched``), whatever the tool.
:meth:`History.mark` is the state now, for comparing the steps inside an open batch.
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
    taken out alone.  ``parts`` are the step's, ``shared`` the ones later steps also touched;
    ``later`` the versions the later steps that touched them made, oldest first (empty when
    the step is refused for a part every scope shares)."""

    def __init__(self, message: str, parts: Collection[str], shared: Collection[str],
                 later: Collection[int] = ()) -> None:
        super().__init__(message)
        self.parts = sorted(parts)
        self.shared = sorted(shared)
        self.later = list(later)


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
class Step:
    """A step undo can reach (:meth:`History.steps`): the versions it went from and to, and
    the parts it touched -- ``None`` for a package that cannot compare snapshots."""

    before: int
    after: int
    parts: frozenset[str] | None = None


class Change:
    """What the package's content did while a :meth:`History.watch` was open: the states
    before and after, as the package's snapshots, and the parts whose bytes differ.

    ``before``/``after`` read one part's bytes in either state (``None``: the part did not
    exist then), for a format to say what in a part changed -- the objects of a page, the
    values of a table.  A watch over which nothing changed has ``parts`` empty; so has one
    whose changes cancel out (an edit and its undo)."""

    def __init__(self, package: Any) -> None:
        self._package = package
        self._before: Any = None
        self._after: Any = None
        self._live = False
        self._parts: frozenset[str] | None = None

    @property
    def changed(self) -> bool:
        """Whether any state change was reported (its parts may still be none)."""
        return self._before is not None

    @property
    def parts(self) -> frozenset[str]:
        """The parts whose bytes differ between before and after; empty when nothing
        changed, or the package cannot compare snapshots part by part."""
        if self._parts is None:
            if self._before is None or self._after is None \
                    or not hasattr(self._package, "changed_between"):
                self._parts = frozenset()
            else:
                self._parts = frozenset(self._package.changed_between(self._before,
                                                                      self._after))
        return self._parts

    def before(self, path: str) -> bytes | None:
        """``path``'s bytes before; ``None`` when it did not exist then."""
        return self._package.snapshot_value(self._before, path)

    def after(self, path: str) -> bytes | None:
        """``path``'s bytes after; ``None`` when it does not exist then."""
        return self._package.snapshot_value(self._after, path)

    def _record(self, before: Any, after: Any) -> None:
        if self._before is None:
            self._before = before
        # ``None``: the live state, taken when the watch closes (an edit checkpointed
        # outside a batch, which is still to happen when it is reported).
        self._after, self._live = after, after is None
        self._parts = None

    def _close(self) -> None:
        if self._live:
            self._after, self._live = self._package.snapshot(), False


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
        #: The open watches (:meth:`watch`), told of every state change.
        self._watches: list[Change] = []

    # -- checkpoints -----------------------------------------------------------------------

    def checkpoint(self) -> None:
        """Record the state *before* a mutation.  Called by mutators, not by callers."""
        if self._batch_depth:
            # Inside a batch the only checkpoint that matters is the one taken on entry.
            return
        snapshot = self._package.snapshot()
        self._push(snapshot)
        self._changed(snapshot, None)       # after: once the mutation is done

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

    # -- watching --------------------------------------------------------------------------

    @contextmanager
    def watch(self) -> Iterator[Change]:
        """A :class:`Change` that says what the content did while the block ran: every
        state change the history makes in it, from the state before the first to the state
        after the last.  Nestable; free until :attr:`Change.parts` is asked.  A batch still
        open when the block ends is not in it: it changes the content when it closes."""
        change = Change(self._package)
        self._watches.append(change)
        try:
            yield change
        finally:
            self._watches.remove(change)
            change._close()

    def _changed(self, before: Any, after: Any) -> None:
        for change in self._watches:
            change._record(before, after)

    def mark(self) -> Any:
        """The state now (the package's snapshot): inside a batch, where no step is made
        until it closes, what to compare one stretch of it against the next with
        (:meth:`change`), or to hand :meth:`savepoint` as its start."""
        return self._package.snapshot()

    @property
    def compares(self) -> bool:
        """Whether the package compares snapshots part by part (``changed_between``), so
        a :class:`Change` can say which parts changed."""
        return hasattr(self._package, "changed_between")

    def change(self, before: Any, after: Any) -> Change:
        """The :class:`Change` from one state to another (:meth:`mark`, or a watch's)."""
        change = Change(self._package)
        change._record(before, after)
        return change

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
            after = self._package.snapshot()
            if after != snapshot:
                self._push(snapshot)
                self._changed(snapshot, after)

    @contextmanager
    def savepoint(self, start: Any = None) -> Iterator[None]:
        """Inside a batch: if the code inside raises, roll back to here and re-raise, and
        the batch stays open -- what came before in it is kept, as one step when it closes.
        A batch nested inside rolls nothing back by itself; this is the inner rollback a
        batch of independent edits needs.  Outside a batch it is a :meth:`batch`.
        ``start``: the state now, when the caller has just taken it (:meth:`mark`), so it
        is not taken twice."""
        if not self._batch_depth:
            with self.batch():
                yield
            return
        snapshot = start if start is not None else self._package.snapshot()
        try:
            yield
        except Exception:
            self._package.restore(snapshot)
            raise

    @property
    def in_batch(self) -> bool:
        return self._batch_depth > 0

    # -- moving through history ------------------------------------------------------------

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def next_redo(self) -> int | None:
        """The version a plain :meth:`redo` would bring back; ``None`` when there is none."""
        return self._redo[-1][1] if self._redo else None

    def undo(self) -> bool:
        if not self._undo:
            return False
        snapshot, version = self._undo.pop()
        current = self._package.snapshot()
        self._redo.append((current, self._version))
        self._package.restore(snapshot)
        self._version = version
        self._changed(current, snapshot)
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        snapshot, version = self._redo.pop()
        current = self._package.snapshot()
        self._undo.append((current, self._version))
        self._package.restore(snapshot)
        self._version = version
        self._changed(current, snapshot)
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

    def _touched(self, package: Any) -> tuple[list[Any], list[frozenset[str]]]:
        """Every state undo can reach, oldest first, the current one last, and the parts
        each step changed (step ``k`` goes from state ``k`` to state ``k + 1``)."""
        states = [snapshot for snapshot, _ in self._undo] + [package.snapshot()]
        return states, [frozenset(package.changed_between(states[k], states[k + 1]))
                        for k in range(len(self._undo))]

    def _after_of(self, index: int) -> int:
        """The version step ``index`` made."""
        return self._undo[index + 1][1] if index + 1 < len(self._undo) else self._version

    def steps(self) -> list[Step]:
        """The steps :meth:`undo` can take back, oldest first: the version each went from
        and to, and the parts it touched when the package compares snapshots part by part
        (``None`` otherwise)."""
        versions = [version for _, version in self._undo] + [self._version]
        package = self._package
        if (self._batch_depth or not hasattr(package, "changed_between")
                or not hasattr(package, "snapshot_with")):
            return [Step(versions[k], versions[k + 1]) for k in range(len(self._undo))]
        _, touched = self._touched(package)
        return [Step(versions[k], versions[k + 1], touched[k]) for k in range(len(touched))]

    def undo_in(self, scope: Collection[str], *, shared: Collection[str] = ()
                ) -> ScopedStep | None:
        """Undo the latest step that touched a part in ``scope``; ``None`` if none did.

        Raises :class:`Entangled`, changing nothing, when a later step touched a part that
        step touched -- or when the step touched a part in ``shared``: a part every scope
        depends on (a template every page is drawn from), whose change no one scope owns.  The
        global redo history is dropped, as after any change."""
        package = self._parts_api()
        scope = set(scope)
        states, touched = self._touched(package)
        index = next((k for k in reversed(range(len(touched))) if touched[k] & scope), None)
        if index is None:
            return None
        return self._take_out(package, index, states, touched, set(shared))

    def undo_step(self, after: int, *, shared: Collection[str] = ()) -> ScopedStep:
        """Take out the step that made version ``after`` (one of :meth:`steps`), wherever it
        is in the history, as :meth:`undo_in` takes out a scope's latest: the later steps
        stay, without it.  Raises :class:`Entangled`, changing nothing, when a later step
        touched a part it touched or it touched a part in ``shared``; ``LookupError`` when
        no step undo can reach made ``after``."""
        package = self._parts_api()
        states, touched = self._touched(package)
        index = next((k for k in range(len(touched)) if self._after_of(k) == after), None)
        if index is None:
            raise LookupError(f"no step in the history made version {after}")
        return self._take_out(package, index, states, touched, set(shared))

    def _take_out(self, package: Any, index: int, states: list[Any],
                  touched: list[frozenset[str]], shared: set[str]) -> ScopedStep:
        parts = touched[index]
        common = parts & shared
        if common:
            raise Entangled(f"the latest step in scope changed {', '.join(sorted(common))}, "
                            "which every scope shares", parts, common)
        later = [k for k in range(index + 1, len(touched)) if touched[k] & parts]
        overlap = set().union(*(touched[k] for k in later)) & parts
        if overlap:
            raise Entangled(f"the latest step in scope also changed "
                            f"{', '.join(sorted(overlap))}, which later steps changed too",
                            parts, overlap, [self._after_of(k) for k in later])
        current = states[-1]
        before_snapshot, before = self._undo[index]
        after = self._after_of(index)
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
        self._changed(current, new_current)
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
        return self._bring_back(package, index)

    def redo_step(self, after: int) -> ScopedStep | None:
        """Bring back the step that made version ``after``, taken out by :meth:`undo_step`
        or :meth:`undo_in`, as a new step; ``None`` if no taken-out step made it.  Raises
        :class:`Entangled`, changing nothing, when its parts changed since."""
        package = self._parts_api()
        index = next((k for k in reversed(range(len(self._undone)))
                      if self._undone[k].after == after), None)
        if index is None:
            return None
        return self._bring_back(package, index)

    def _bring_back(self, package: Any, index: int) -> ScopedStep:
        undone = self._undone[index]
        current = package.snapshot()
        moved = package.changed_between(
            current, package.snapshot_with(current, undone.before_snapshot, undone.parts))
        if moved:
            raise Entangled(f"{', '.join(sorted(moved))} changed since the undo", undone.parts,
                            moved)
        del self._undone[index]
        self._push(current)
        restored = package.snapshot_with(current, undone.after_snapshot, undone.parts)
        package.restore(restored)
        self._changed(current, restored)
        return ScopedStep(undone.parts, undone.before, undone.after, self._version)

    def can_undo_in(self, scope: Collection[str]) -> bool:
        """Whether some step in the history touched ``scope`` (entangled or not)."""
        package = self._parts_api()
        scope = set(scope)
        states = [snapshot for snapshot, _ in self._undo] + [package.snapshot()]
        return any(package.changed_between(states[k], states[k + 1]) & scope
                   for k in range(len(self._undo)))
