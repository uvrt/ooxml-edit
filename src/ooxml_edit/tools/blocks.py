"""Edits of different blocks of one document at the same time (round 45, lane B; R64 stage 3).

A document is one tree, changed in place, so a changing call holds the document's lock while it
runs (:mod:`.session`).  Where a format says how (:attr:`~.session.DocumentFormat.blocks`, a
:class:`BlockEdits`), a changing call it admits runs on a **draft** instead -- an editable copy
of the last committed version, made from that version's bytes (the format's
:attr:`~.session.DocumentFormat.read_copy` holds what the copy needs) -- with no lock of the
document held, so calls changing other blocks run beside it, each on a draft of its own.  Then,
under the document's lock and briefly:

1. **check** -- the format compares what the call read and changed on the draft with the
   document as it is now (:meth:`BlockEdits.check`): the blocks it changed and the blocks
   around them, the blocks it named, the facts about the whole document it used, and every
   other part, which the changes counted since its version (:meth:`~.session.DocumentEntry.
   parts_changed_since`) must have left alone; the dispatcher checks what it read of the
   session -- refs, a retry key, the datasets it named.  Anything another call changed first
   is a **conflict** (:class:`BlockConflict`).
2. **apply** -- inside the document's undo step, as the call's own step (its version, its
   owner), the patch is applied: the blocks it changed replace the same blocks in the document,
   other parts it changed replace theirs, and the ids it made are made again where the
   document's sequence of ids is now (:meth:`BlockEdits.apply`): the document is then what the
   call would have made of it had it run there, and its result says the ids the document has
   (:meth:`BlockEdits.rename`).
3. **settle** -- after the step: the format's bookkeeping of the new version
   (:meth:`BlockEdits.settle`), the call's refs and retry key kept, the version committed;
   then its checks run on that version's copy (:class:`~.dispatch.After`), and a batch's reads
   after its last change read it.

A conflict prepares the call again on the version committed now, at most
:attr:`~.limits.Limits.block_tries` times in all; then it runs on the document under its lock
for its whole run, as a call the format does not admit always does -- as do whole-document
calls (styles, fields, sections...), reads of other documents, session tools, and a call whose
draft did something the format cannot apply as a patch (:meth:`BlockEdits.prepared` returns
``None``).

**Serializable.**  A committed call's result and change are those of the same call run alone
on the document at the moment it committed: the commit order is a serial order of the
changing calls.  A call that failed, or changed nothing, on its draft is of the version the
draft was of.  A read is of the version it read (:meth:`~.session.Session.read_lock`).

**When.**  ``Toolbox(block_edits="auto")`` (the default) prepares a call on a draft only while
another call names the same document (:meth:`~.session.DocumentEntry.enter`): one caller alone
runs every call as before, on the document under its lock, so its results, its bytes and its
CPU are what they were.  ``"always"`` drafts every admitted call (tests); ``"off"`` none.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Protocol


class BlockConflict(Exception):
    """A patch prepared on a draft no longer applies: another call changed what it read or
    changed first.  The message says what; the call is prepared again."""


class BlockEdits(Protocol):
    """What a format provides for block-level edits (:attr:`DocumentFormat.blocks
    <.session.DocumentFormat.blocks>`).  Every method but :meth:`admits`, :meth:`draft`,
    :meth:`prepared`, :meth:`rename` and :meth:`release` runs under the document's lock."""

    def admits(self, tool: str, arguments: Mapping[str, Any]) -> bool:
        """Whether a validated changing call of ``tool`` may run on a draft.  Unknown tools:
        ``False`` (they keep the document's lock)."""

    def draft(self, document: Any, committed: Any) -> Any:
        """An editable copy of ``document`` at ``committed``'s version
        (:class:`~.session.Committed`), recording what a call does on it; its ``document``
        attribute is the copy.  ``None``: there can be none (the call takes the lock)."""

    def prepared(self, draft: Any, result: Any, arguments: Mapping[str, Any]) -> Any:
        """After a call ran on ``draft`` (its ``result``; ``arguments`` as validated): its
        change as a patch, or ``None`` when it cannot be applied as one."""

    def check(self, document: Any, patch: Any, changed: frozenset[str] | None) -> Any:
        """Whether ``patch`` applies to ``document`` as it is now: a plan for :meth:`apply`,
        or :class:`BlockConflict`.  ``changed``: the parts changed since the draft's version
        (``None``: not known)."""

    def replay_changes(self, document: Any, plan: Any, op_changes: list[tuple[int, str, Any]]
                       ) -> list[tuple[int, str, Any]] | None:
        """Optional, for a batch: each op's change on the draft (``(op, doc, change)``, a
        :class:`~ooxml_edit.history.Change`) as the document's -- so what each op changed
        (``Result.touched_by_op``) is read from the document, as a batch run on it reads
        it; ``None``: the draft's are the document's (nothing moved).  Before
        :meth:`apply`; :class:`BlockConflict` when they cannot be."""

    def apply(self, document: Any, plan: Any) -> Mapping[str, str]:
        """Apply the plan, inside the document's undo step; the ids the call made that the
        document has under other names now (``old -> new``), for :meth:`rename`."""

    def settle(self, document: Any, plan: Any, *, touched: list[Any], before: bytes) -> None:
        """After the undo step: the format's bookkeeping of the new version; ``touched`` (the
        call's :meth:`~.dispatch.Call.touch` scopes, its checks read them) made of the
        document, ``before`` the document's bytes before the patch."""

    def rename(self, value: Any, renames: Mapping[str, str]) -> Any:
        """``value`` (a result's text, a list or dict of them) with the ids renamed."""

    def release(self, draft: Any, *, current: Any = None) -> None:
        """Done with ``draft``; ``current``: the committed version (:class:`~.session.
        Committed`) the draft now is, its change applied with nothing between -- it may be
        kept to draft the next call of that version -- else ``None``."""


def renamed(value: Any, rename: Callable[[str], str]) -> Any:
    """``value`` with ``rename`` applied to every string in it, dict keys included."""
    if isinstance(value, str):
        return rename(value)
    if isinstance(value, list):
        return [renamed(item, rename) for item in value]
    if isinstance(value, tuple):
        return tuple(renamed(item, rename) for item in value)
    if isinstance(value, dict):
        return {renamed(key, rename): renamed(item, rename) for key, item in value.items()}
    return value


def token_renamer(renames: Mapping[str, str], *, letters: str = "0-9A-Fa-f") -> Callable[[str], str]:
    """A function replacing each whole token of ``renames`` in a string -- one not run into a
    character of ``letters`` on either side -- by its new name."""
    if not renames:
        return lambda text: text
    pattern = re.compile(f"(?<![{letters}])(" + "|".join(sorted(map(re.escape, renames), key=len,
                                                                  reverse=True))
                         + f")(?![{letters}])")
    return lambda text: pattern.sub(lambda match: renames[match.group(1)], text) \
        if isinstance(text, str) else text


__all__ = ["BlockConflict", "BlockEdits", "renamed", "token_renamer"]
