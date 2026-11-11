"""Keys compared loosely: one rule every tool that matches keys shares (round 52).

A key -- an id in a table's first column, a code in a dataset -- is often spelled two ways by
two sources: ``K7-12.3`` in one source, ``k7-12-03`` in another.  :func:`loose_key` turns a
key into a canonical form, the same for both spellings, by this rule and no other:

* the key is read as **runs**: a run of letters, a run of digits, or one other character;
* a **letter run** is compared with its case folded (``K`` = ``k``; accents count: ``é`` is
  not ``e``);
* a **digit run** (the digits 0-9) is compared as a number: ``05`` = ``5``, ``007`` = ``7``;
* **white space** (any, the no-break space too), the **invisible characters** (zero-width
  space, zero-width joiner and non-joiner, word joiner, byte order mark, soft hyphen) and the
  **separators** ``.`` ``-`` ``_`` ``/`` between two runs are one boundary whatever they are:
  ``K7-12`` = ``k7 12`` = ``K7_12`` = ``K7 - 12``; a boundary is kept, so ``03-1`` is not
  ``031`` and ``AB`` is not ``A B`` (between a letter run and a digit run there is a boundary
  anyway: ``K7`` = ``K-7``);
* white space and invisible characters at either end are ignored; a separator at either end
  counts (``K7-`` is not ``K7``), as does every other character (``#``, ``(``, ``+``, ``,``,
  ``:``, digits other than 0-9).

Nothing else is guessed: no spelling corrections, no reordering, no dropping of prefixes.
Two keys **match loosely** when their :func:`loose_key` values are equal; an empty key
(white space only) has ``()``.  A caller matches exactly first and loosely only what is
left, and refuses a key that loosely matches more than one key on the other side.

This is key matching.  Text matching -- a filter's ``loose`` that ignores white space and
case in a value -- is a different, smaller rule, and stays as each tool documents it.
"""

from __future__ import annotations

__all__ = ["INVISIBLE", "SEPARATORS", "loose_key"]

#: The separators between two runs that are one boundary, whatever they are.
SEPARATORS = frozenset(".-_/")

#: Characters that print nothing, read as white space.
INVISIBLE = frozenset("\u200b\u200c\u200d\u2060\ufeff\u00ad")

_LETTERS, _DIGITS, _OTHER = 0, 1, 2


def _gap(char: str) -> bool:
    return char.isspace() or char in INVISIBLE


def loose_key(text: str) -> tuple:
    """``text``'s canonical form for loose key matching (see the module doc): a tuple of runs
    -- ``(0, letters casefolded)``, ``(1, length, digits)`` (the number's digits without
    leading zeros, so numbers compare by value however long), ``(2, character)`` -- equal
    for two keys that match loosely, and ordered as natural sort orders keys."""
    out: list[tuple] = []
    n = len(text)
    i = 0
    while i < n:
        char = text[i]
        if _gap(char) or char in SEPARATORS:
            j = i
            while j < n and (_gap(text[j]) or text[j] in SEPARATORS):
                j += 1
            if j == n or not out:
                # At either end: white space ignored, separators count (in their order).
                out.extend((_OTHER, c) for c in text[i:j] if c in SEPARATORS)
            # else: one boundary between two runs, whatever it is made of
            i = j
            continue
        if "0" <= char <= "9":
            j = i
            while j < n and "0" <= text[j] <= "9":
                j += 1
            digits = text[i:j].lstrip("0") or "0"
            out.append((_DIGITS, len(digits), digits))
        elif char.isalpha():
            j = i
            while j < n and text[j].isalpha():
                j += 1
            out.append((_LETTERS, text[i:j].casefold()))
        else:
            j = i + 1
            out.append((_OTHER, char))
        i = j
    return tuple(out)
