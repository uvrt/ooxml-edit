"""The libraries' refusal of a value they were given: :class:`ArgumentError` and its kin.

Every check ooxml-edit makes of what its caller passes -- a cell reference that is not
one, a legend position it has no word for, the last node of a diagram to remove -- raises
an :class:`ArgumentError`, whose message says what is wrong and what would do.  It is a
:class:`ValueError`, as these refusals always were, so ``except ValueError`` still catches
it.  A value of a type the library does not take raises :class:`ArgumentTypeError`, both
an :class:`ArgumentError` and the :class:`TypeError` it always was.  A number, date or
length outside the range it can take, or not finite, raises :class:`UnitError`: an
:class:`ArgumentError` too.

The tool layer's dispatcher says ``invalid_arguments`` for an :class:`ArgumentError` and
``unit`` for a :class:`UnitError` (:data:`~ooxml_edit.tools.ERROR_CODES`), in every
format; a format's own ``DocumentFormat.errors`` may name a subclass otherwise.  A plain
``ValueError`` -- one Python raises itself, or one of a library's own subclasses that is
none of these -- keeps the dispatcher's fallback, ``unit``.  A format library derives its
own argument errors from these, so a refusal is one class family whichever library raised
it.

What stays a plain ``ValueError``: a broken document (a part the package should have and
does not) and a mistake in the code that uses the library (two tools of one name, a
renderer that returns no PNG) -- neither is the caller's value to change.
"""

from __future__ import annotations


class ArgumentError(ValueError):
    """A value the library refuses; the message says what is wrong and what would do."""


class ArgumentTypeError(ArgumentError, TypeError):
    """A value of a type the library does not take there; the message says which it does."""


class UnitError(ArgumentError):
    """A number, date or length out of the range it can take, or in the wrong unit."""


__all__ = ["ArgumentError", "ArgumentTypeError", "UnitError"]
