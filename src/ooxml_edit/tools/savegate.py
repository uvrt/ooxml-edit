"""The save gate's comparison, one code path for the gate and for ``check`` (round 53, R109).

``save_document`` hands a file over only when the document has no validation problem it
did not have when opened: its format's ``problems`` now, against the baseline taken at open
(:attr:`~.session.DocumentEntry.baseline_problems`), compared by the format's
``problem_key``.  A long run that checked only some blocks, or asked ``check`` for layout
facts alone, learned of new problems only when the save refused at its end.  So every
``check`` result of a kind with ``problems`` carries ``validation``
(:func:`validation_fact`): the problems the save would count as new, from the very function
the gate calls (:func:`validation`), whatever the check's ``include`` or scope.

* :func:`compare_problems` -- the comparison itself: ``new`` (each problem now whose key the
  baseline lacks, in order), ``fixed`` (each baseline problem whose key is gone) and
  ``baseline`` (how many the document opened with).  A library's own ``validate`` section
  compares through it too, so the two never disagree.
* :func:`problems_now` -- the format's problems of the document, cached per document version
  in the entry's ``check_cache`` (a version is never reused, :attr:`~ooxml_edit.history.
  History.version`): a check, the facts after a changing call and the next check of the same
  version find them made.  Not cached inside a ``batch``, whose ops change the document
  before the version moves.
* :func:`validation` -- the two: what the gate compares.  The gate itself asks ``fresh``:
  the problems found again, never a kept list, and kept for the checks after.
"""

from __future__ import annotations

from typing import Any, Callable, Hashable, Sequence

#: At most this many new problems listed in ``check``'s ``validation`` (rule 4); ``cut``
#: counts the rest, all of which ``save_document``'s refusal lists.
LISTED = 20
#: The key in the entry's ``check_cache`` the problems are kept under (with the version).
_CACHE_KEY = ("validation_problems",)


def compare_problems(baseline: Sequence[Any], problems: Sequence[Any],
                     key: Callable[[Any], Hashable] = str) -> dict[str, Any]:
    """``problems`` against ``baseline``, by ``key``: ``{"new", "fixed", "baseline"}``, the
    problems as text.  What the save gate refuses on is ``new``."""
    before = {key(problem) for problem in baseline}
    now = {key(problem) for problem in problems}
    return {"new": [str(problem) for problem in problems if key(problem) not in before],
            "fixed": [str(problem) for problem in baseline if key(problem) not in now],
            "baseline": len(baseline)}


def problems_now(entry: Any, fmt: Any, *, cache: bool = True,
                 fresh: bool = False) -> list[Any]:
    """The format's problems of ``entry``'s document (``fmt.problems``), kept per version.

    ``cache`` false (inside a batch): found and not kept.  ``fresh``: found again and kept,
    whatever was kept before -- the gate's."""
    if fmt is None or fmt.problems is None:
        return []
    if not cache:
        return list(fmt.problems(entry.document))
    full = (entry.version, _CACHE_KEY)
    if not fresh:
        found = entry.check_cache.get(full)
        if found is not None:
            return found
    found = list(fmt.problems(entry.document))
    entry.check_cache.put(full, found)
    return found


def keep_problems(entry: Any, problems: Sequence[Any]) -> None:
    """Keep ``problems`` as those of ``entry``'s document at its version: the baseline the
    session found at open, so a check before any edit does not find them again."""
    entry.check_cache.put((entry.version, _CACHE_KEY), list(problems))


def validation(entry: Any, fmt: Any, *, problems: Sequence[Any] | None = None,
               cache: bool = True, fresh: bool = False) -> dict[str, Any]:
    """What the save gate compares: the document's problems now (``problems`` when the
    caller found them already, else :func:`problems_now`) against its baseline, by the
    format's ``problem_key`` (:func:`compare_problems`)."""
    if problems is None:
        problems = problems_now(entry, fmt, cache=cache, fresh=fresh)
    key = fmt.problem_key if fmt is not None else str
    return compare_problems(entry.baseline_problems, problems, key)


def validation_fact(report: dict[str, Any]) -> dict[str, Any]:
    """``check``'s ``validation``: the problems saving would refuse as new (at most
    :data:`LISTED`, ``cut`` the rest), the count the document opened with, and those of
    them fixed since (when any)."""
    new = report["new"]
    out: dict[str, Any] = {"new": new[:LISTED], "baseline": report["baseline"]}
    if len(new) > LISTED:
        out["cut"] = len(new) - LISTED
    if report["fixed"]:
        out["fixed"] = report["fixed"][:LISTED]
    return out


def summary_note(report: dict[str, Any]) -> str:
    """The check summary's words when saving would be refused, else ``""``."""
    count = len(report["new"])
    if not count:
        return ""
    return f"; saving would refuse {count} new validation problem(s): see validation"


__all__ = ["LISTED", "compare_problems", "keep_problems", "problems_now", "summary_note", "validation",
           "validation_fact"]
