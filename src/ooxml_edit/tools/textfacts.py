"""Text facts: a source's value written into text by reference, and where it came from.

A figure in a sentence -- "Average 2.2 across 41 items" -- is a value of a dataset or of a
table in another open document, like a table's or a chart's.  Instead of copying it, a
call writes a *reference* where the value goes::

    "Average {{d:by_topic/door/mean}} across {{d:by_topic/door/count}} items"

and the dispatcher replaces each reference with the value, written in the session's
locale, before the handler runs: the tool writes ``Average 2,2 across 41 items`` in nl-BE.
The result lists each value written and its sources (``data.facts``), and the format
library keeps the same as provenance, as it does a table's (:mod:`ooxml_edit.provenance`).
A tool declares the arguments that may hold references (``Tool.facts``, paths as
``Tool.refs`` writes them, an array of arrays of strings too); nothing else changes in its
definition, so a reference costs no parameter.

**A reference** is ``{{`` + a source address that names one value + ``}}``:

* ``d:by_topic/door/mean`` -- dataset ``by_topic``, the row whose key is ``door`` (its
  first column's value as a table cell writes it: ``2024``, ``North``; else one equal case
  aside), column ``mean``; ``d:by_topic/#3/mean`` the third row; ``d:total/value`` a
  one-row dataset's column, ``d:total`` a dataset of one value;
* ``t:checks/N-1/rating`` -- the same in another open document, by its format: the
  format's resolver reads ``t:checks/N-1`` (one row: a row by key or ``#3`` by position,
  as a table format's ``t:`` addresses write it) and the column is picked by name;
  ``d2/t:checks/...`` names the document when several take ``t:``;
* a key or column holding ``/`` is quoted, ``'A/7'``, with ``''`` for a quote, as a table
  format quotes a key.

Only ``{{`` followed by ``d:`` or a prefix a loaded format resolves (``t:``) starts a
reference: ``{{name}}``, a template's placeholder, and any other braces stay as they are.
``\\{{`` writes a reference's text literally (``\\{{d:x}}`` is ``{{d:x}}``).  A reference
to a missing dataset, table, row or column, to a blank, or to more than one value is
refused, naming what there is: never written half.

**Numbers** are written as a table cell gets them (:func:`.sources.label`): ``2.5`` is
``2,5`` in a locale with a decimal comma, ``2024`` stays ``2024``, at most 15 significant
digits; a value of a ``percent`` column as a percent (``0.25`` is ``25%``).  A number
format after the address rounds it, half away from zero as a table format's derive's ``round``
does: ``{{d:by_topic/door/mean:0.0}}`` -- ``0`` whole, ``0.0`` to ``0.000000000`` that many
decimals, ``#,##0...`` grouping thousands with the locale's separator, ``%`` at the end a
percent (``0.0%``).  Nothing else: a source column's own number format is not known to a
dataset, and a derive that rounds (``round``) has its values rounded already.  A suffix
that looks like a format but is none (``{{d:x/#1/avg:0.20}}``) is refused as such, listing
the forms (:func:`bad_format`), unless a column of that whole name (``avg:0.20``) exists.

**Dates** are written as a table cell gets them, in the locale's day order -- or in a date
pattern after the address, in the date letters of Office's number formats (:mod:`ooxml_edit.dates`):
``{{d:events/#1/when:d mmmm yyyy}}`` is ``2 March 2026`` (``2 maart 2026`` in nl-BE),
``{{d:events/#1/when:dd/mm/yyyy}}`` ``02/03/2026``.  The tokens are ``d dd ddd dddd m mm
mmm mmmm mmmmm yy yyyy``; every other character is written as given, so the separators
are the pattern's in every locale (the text is fixed, not a format Office applies).  Month
and day names are the measured ones of the session's locale; a locale without them
refuses a name token, naming the numeric ones.  A date pattern on a value that is no date
is refused, and a number format on a date, each naming the other.  A suffix that looks
like a date pattern but is none (``:DD/MM/YYYY HH:mm``) is refused as such, as a bad
number format is.

**A document's own references.**  A format may resolve references to a thing of the
document the text is written into (``DocumentFormat.own_facts``, ``{prefix: resolve}``):
``{{<prefix><name>}}`` is written as what ``resolve(document, name)`` gives -- ``name`` a
``$ref`` replaced by its address -- and kept as a fact like the others: ``source`` the
prefix and the name the format resolved (never the ``$ref``, which only the session
knows), ``text``, and the fields the format adds.  Keeping it current is the format's
business.  It takes no document handle and no number format.  Every loaded format's prefixes
are known, so a document whose own format does not resolve one refuses it, naming the
formats that do (:func:`not_own`), rather than leaving the braces in its text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import time as _dt_time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Iterable, Mapping, Sequence

from .. import dates, provenance
from .datasets import DATASET_PREFIX
from .results import ToolError
from .sources import QUALIFIED, Rows, label, merged, number, prefixes, what

#: A reference in text, or one escaped (``\\{{``): group 1 the backslash, group 2 the inside.
REFERENCE = re.compile(r"(\\?)\{\{([^{}]{1,500})\}\}")
#: A number format after the address: ``:0.0``, ``:#,##0``, ``:0%``.
FORMAT = re.compile(r":((?:#,##)?0(?:\.(0{1,9}))?(%)?)\s*$")
#: A suffix after the last ``:`` that a model meant as a number format: digits, ``#``,
#: ``.``, ``,``, ``%`` and spaces (``0.20``, ``0,0``, ``##.#``), or a Python-style ``.2f``.
ATTEMPTED_FORMAT = re.compile(r"\s*(?:[#0-9.,%\s]*[#0-9][#0-9.,%\s]*|\.?[0-9]*[fFgGdD%])\s*")
#: The number formats a reference takes, as an error lists them.
FORMS = ("0", "0.0", "0.00", "#,##0", "#,##0.0", "0%", "0.0%")
#: A suffix after the address that a model meant as a date pattern: the date letters
#: (``d``, ``m``, ``y``; ``h``, ``s`` and ``T`` of a time; ``%`` of strftime) and separators.
ATTEMPTED_DATE = re.compile(r"\s*(?=[^dDmMyY]*[dDmMyY])[dDmMyYhHsSTt%/\-.,:\s'’\"\\]+\s*")
#: How many values one argument may reference.
MAX_FACTS = 50


@dataclass
class Fact:
    """One reference written: ``field`` the argument (``items[0].text``), ``start`` and
    ``end`` its value's place in the argument's text as the handler gets it, ``text`` the
    value written, ``address`` the reference as given (without a document handle or its
    format), ``record`` what the document keeps (:func:`record`)."""

    field: str
    start: int
    end: int
    text: str
    address: str
    record: dict[str, Any] = dataclass_field(default_factory=dict)  # (a member is named field)

    def brief(self) -> dict[str, Any]:
        """What a result says of it: the reference, the value written, its sources."""
        out: dict[str, Any] = {"from": self.address, "text": self.text}
        for key in ("sources", "stale"):
            if self.record.get(key):
                out[key] = self.record[key]
        return out


def known_prefixes(formats: Mapping[str, Any]) -> list[str]:
    """``d:``, the prefixes the loaded formats resolve to rows and their own references'
    (:func:`own_prefixes`), longest first."""
    found = {DATASET_PREFIX} | {p for values in prefixes(formats).values() for p in values}
    found |= {p for values in own_prefixes(formats).values() for p in values}
    return sorted(found, key=len, reverse=True)


def own_prefixes(formats: Mapping[str, Any]) -> dict[str, list[str]]:
    """``{kind: [prefix, ...]}``: the prefixes of the references each format resolves in its
    own document (``DocumentFormat.own_facts``)."""
    return {kind: sorted((getattr(fmt, "own_facts", None) or {}), key=len, reverse=True)
            for kind, fmt in formats.items()}


def own_prefix(address: str, own: Iterable[str]) -> str | None:
    """The own-reference prefix ``address`` starts with (after a document handle, if it
    has one), or ``None``."""
    body = address.strip()
    match = QUALIFIED.match(body)
    body = match.group(2) if match else body
    return next((p for p in sorted(own, key=len, reverse=True) if body.startswith(p)), None)


def _inside(text: str, known: Sequence[str]) -> str | None:
    """The address a reference's inside names, or ``None`` when it is no reference."""
    body = text.strip()
    match = QUALIFIED.match(body)
    address = match.group(2) if match else body
    return body if any(address.startswith(p) and len(address) > len(p) for p in known) \
        else None


def references(text: str, known: Sequence[str]) -> list[tuple[re.Match, str]]:
    """The references in ``text`` that are not escaped: each match and its address."""
    out = []
    for match in REFERENCE.finditer(text):
        address = _inside(match.group(2), known)
        if address is not None and not match.group(1):
            out.append((match, address))
    return out


def qualify_text(text: str, known: Sequence[str],
                 qualify: Callable[[str], str], own: Iterable[str] = ()) -> str:
    """``text`` with each reference's address through ``qualify`` (``t:x/...`` ->
    ``d2/t:x/...``); datasets, a document's own references (prefixes ``own``) and escaped
    references as they are."""
    own = tuple(own)

    def replace(match: re.Match) -> str:
        address = _inside(match.group(2), known)
        if address is None or match.group(1) or address.startswith(DATASET_PREFIX) or \
                own_prefix(address, own) is not None:
            return match.group(0)
        return "{{" + qualify(address) + "}}"

    return REFERENCE.sub(replace, text)


# -- expanding -----------------------------------------------------------------------------------


def expand(text: str, where: str, known: Sequence[str],
           resolve: Callable[[str, str], tuple[str, dict[str, Any]]]) -> tuple[str, list[Fact]]:
    """``text`` with every reference replaced by ``resolve(address, where)``'s text, and
    escaped ones unescaped; the :class:`Fact` of each, its place in the new text."""
    out: list[str] = []
    facts: list[Fact] = []
    at = 0
    length = 0
    count = 0
    for match in REFERENCE.finditer(text):
        address = _inside(match.group(2), known)
        if address is None:
            continue
        before = text[at:match.start()]
        out.append(before)
        length += len(before)
        at = match.end()
        if match.group(1):                       # \{{...}}: the text, without the backslash
            literal = match.group(0)[1:]
            out.append(literal)
            length += len(literal)
            continue
        count += 1
        if count > MAX_FACTS:
            raise ToolError("limit", f"more than {MAX_FACTS} references in one text",
                            field=where)
        written, found = resolve(address, where)
        shown = QUALIFIED.match(address)
        given = shown.group(2) if shown else address
        given = strip_format(given)
        facts.append(Fact(where, length, length + len(written), written, given, found))
        out.append(written)
        length += len(written)
    out.append(text[at:])
    return "".join(out), facts


def _suffix_at(address: str) -> int | None:
    """Where the first ``:`` after the source's prefix (``d:``, ``t:``) is, outside quotes
    -- the start of a date pattern's suffix -- in ``address``; ``None`` without one."""
    match = QUALIFIED.match(address)
    colon = address.find(":", match.start(2) if match else 0)
    if colon < 0:
        return None
    quoted = False
    for at in range(colon + 1, len(address)):
        char = address[at]
        if char == "'":
            quoted = not quoted
        elif char == ":" and not quoted:
            return at
    return None


def date_suffix(address: str) -> tuple[str, str] | None:
    """``(address without it, pattern)`` when ``address`` ends in a date pattern
    (:func:`ooxml_edit.dates.parse` takes everything after the first ``:`` past the source's
    prefix); ``None`` otherwise -- a number format, a column's own colon."""
    at = _suffix_at(address)
    if at is None:
        return None
    pattern = address[at + 1:].strip()
    if FORMAT.fullmatch(":" + pattern):
        return None
    try:
        dates.parse(pattern)
    except dates.PatternError:
        return None
    return address[:at], pattern


def is_date_pattern(fmt: str | None) -> bool:
    """Whether a reference's format (:func:`split`'s) is a date pattern, not a number format."""
    return fmt is not None and FORMAT.fullmatch(":" + fmt) is None


def strip_format(address: str) -> str:
    """``address`` without its number format or date pattern."""
    found = date_suffix(address)
    return found[0] if found else FORMAT.sub("", address)


def split(address: str) -> tuple[str, list[tuple[str, bool]], str | None]:
    """``(source, segments, format)`` of a reference's address: the source with its
    document handle (``d2/t:x``), the segments after it (row, column) each unquoted and
    whether it was quoted, the number format or date pattern (:func:`is_date_pattern`)."""
    pattern = date_suffix(address)
    fmt: str | None
    if pattern is not None:
        body, fmt = pattern
    else:
        found = FORMAT.search(address)
        fmt = found.group(1) if found else None
        body = address[:found.start()] if found else address
    parts = _segments(body.strip())
    if len(parts) > 1 and re.fullmatch(r"d[0-9]+", parts[0][0]) and not parts[0][1]:
        parts = [(parts[0][0] + "/" + parts[1][0], False)] + parts[2:]
    return parts[0][0], parts[1:], fmt


def bad_format(address: str) -> str | None:
    """The suffix of ``address`` when it looks like a number format or a date pattern and is
    neither: after the last ``:`` of the last segment, one that looks like a number format
    (:data:`ATTEMPTED_FORMAT`) and is not one :data:`FORMAT` takes (``0.20`` of
    ``d:x/#1/avg:0.20``); else after the first ``:`` past the source's prefix, one that
    looks like a date pattern (:data:`ATTEMPTED_DATE`: ``DD/MM/YYYY HH:mm``, ``%d-%m-%Y``)
    and is not one.  ``None`` otherwise, or when that segment is quoted (``'avg:0.20'``
    names a column)."""
    if FORMAT.search(address) or date_suffix(address) is not None:
        return None
    parts = _segments(address.strip())
    last = parts[-1]
    source = len(parts) == 1 or (len(parts) == 2 and re.fullmatch(r"d[0-9]+", parts[0][0]))
    if not last[1] and last[0].count(":") >= (2 if source else 1):    # d:2024 is a source
        suffix = last[0].rpartition(":")[2]
        if suffix.strip() and ATTEMPTED_FORMAT.fullmatch(suffix):
            return suffix
    at = _suffix_at(address.strip())
    attempt = address.strip()[at + 1:] if at is not None else ""
    return attempt if attempt.strip() and ATTEMPTED_DATE.fullmatch(attempt) else None


def format_error(address: str, suffix: str, field: str | None) -> ToolError:
    """The refusal of a reference whose suffix :func:`bad_format` found: what it is not, the
    forms there are -- number formats and date tokens -- and the reference written with the
    nearest one."""
    body = address.strip()
    if not ATTEMPTED_FORMAT.fullmatch(suffix):
        at = _suffix_at(body)
        body = body[:at] if at is not None else body
        try:
            dates.parse(suffix.strip())
            why = ""
        except dates.PatternError as error:
            why = str(error).split(": ", 1)[-1]
        return ToolError("invalid_arguments", f"{{{{{address}}}}}: {suffix.strip()!r} is not a "
                         f"date pattern a reference takes{': ' + why if why else ''}. A date "
                         f"pattern is the date letters of a number format -- {dates.TOKENS_TEXT} -- with "
                         "anything else written as given; e.g. "
                         f"{{{{{body}:d mmmm yyyy}}}} or {{{{{body}:dd/mm/yyyy}}}}",
                         field=field, valid_options=list(dates.TOKENS))
    body = body[:body.rfind(":")]
    grouped = "#," in suffix
    point = "." if "." in suffix or grouped else ","          # 0,00: a decimal comma
    decimals = re.search(re.escape(point) + r"([0-9#]+)", suffix)
    count = min(len(decimals.group(1)), 9) if decimals else 0
    python = re.fullmatch(r"\s*\.([0-9])[fF%]\s*", suffix)
    if python:
        count = int(python.group(1))
    near = ("#,##0" if grouped else "0") + ("." + "0" * count if count else "") + \
        ("%" if "%" in suffix else "")
    return ToolError("invalid_arguments", f"{{{{{address}}}}}: {suffix.strip()!r} is not a "
                     "number format a reference takes. Write :0 (a whole number), :0.0 to "
                     ":0.000000000 (that many decimals, one 0 each), :#,##0 or :#,##0.0 "
                     "(thousands grouped), with % at the end for a percent (:0.0%); a "
                     f"date takes a date pattern of {' '.join(dates.TOKENS)} "
                     f"(:d mmmm yyyy, :dd/mm/yyyy); e.g. {{{{{body}:{near}}}}}", field=field,
                     valid_options=list(FORMS))


def _segments(body: str) -> list[tuple[str, bool]]:
    """``body`` split at ``/`` outside quotes: each segment and whether it was quoted."""
    out: list[tuple[str, bool]] = []
    current: list[str] = []
    quoted = False
    i = 0
    while i < len(body):
        char = body[i]
        if char == "'" and not current:
            end = i + 1
            text = []
            while end < len(body):
                if body[end] == "'":
                    if body[end + 1:end + 2] == "'":
                        text.append("'")
                        end += 2
                        continue
                    break
                text.append(body[end])
                end += 1
            current.extend(text)
            quoted = True
            i = end + 1
            continue
        if char == "/":
            out.append(("".join(current), quoted))
            current, quoted = [], False
        else:
            current.append(char)
        i += 1
    out.append(("".join(current), quoted))
    return out


def quote(part: str) -> str:
    """A key or column as a reference writes it: quoted when it holds ``/`` or ``'``."""
    return "'" + part.replace("'", "''") + "'" if ("/" in part or "'" in part or part == "*"
                                                   or part.startswith(("#", "{"))) else part


def pick(rows: Rows, segments: Sequence[tuple[str, bool]], address: str, *,
         field: str | None, locale: Any = None,
         row_selected: bool = False) -> tuple[int, int]:
    """The one value ``segments`` (:func:`split`'s) names in ``rows``: ``(row, column)``.
    ``row_selected``: the source gave the row already (a format's ``t:x/key``), so one
    segment is a column."""
    dataset = rows.dataset
    columns = list(dataset.columns)
    count = len(dataset.rows)
    base = rows.address
    if row_selected:
        row_part, column_part = None, segments[0] if segments else None
    elif len(segments) == 2:
        row_part, column_part = segments
    elif len(segments) == 1:
        row_part, column_part = None, segments[0]
    elif not segments:
        row_part = column_part = None
    else:
        raise _not_one(address, f"{address} has {len(segments) + 1} parts", base, columns,
                       field)
    if row_part is None:
        if count != 1:
            raise _not_one(address, f"{base} has {count} rows", base, columns, field,
                           example=_example(rows, locale))
        row = 0
    else:
        row = _row(rows, row_part[0], base, field, locale, quoted=row_part[1])
    if column_part is None:
        if len(columns) != 1:
            raise _not_one(address, f"{base} has {len(columns)} columns", base, columns,
                           field, example=_example(rows, locale, row))
        column = 0
    else:
        column = rows.column(column_part[0], field=field)
    return row, column


def _example(rows: Rows, locale: Any, row: int | None = None) -> str | None:
    dataset = rows.dataset
    if not dataset.rows or not dataset.columns:
        return None
    row = 0 if row is None else row
    key = label(dataset.rows[row][0], locale, type=rows.type(0)) or f"#{row + 1}"
    column = dataset.columns[-1] if len(dataset.columns) > 1 else dataset.columns[0]
    return f"{rows.address}/{quote(key)}/{quote(column)}"


def _not_one(address: str, said: str, base: str, columns: Sequence[str],
             field: str | None, example: str | None = None) -> ToolError:
    shown = f"{{{{{example}}}}}" if example else f"{{{{{base}/<key>/<column>}}}}"
    return ToolError("invalid_arguments", f"{{{{{address}}}}} is not one value ({said}): a "
                     f"reference names one, e.g. {shown}; a table or chart takes rows with "
                     "from", field=field, valid_options=[f"{base}/<key>/{quote(c)}"
                                                         for c in columns[:10]])


def _row(rows: Rows, part: str, base: str, field: str | None, locale: Any, *,
         quoted: bool = False) -> int:
    dataset = rows.dataset
    count = len(dataset.rows)
    position = None if quoted else re.fullmatch(r"#([1-9][0-9]{0,6})", part)
    if position:
        index = int(position.group(1))
        if index > count:
            raise ToolError("not_found", f"{base} has {count} rows; there is no row "
                            f"#{index}", field=field)
        return index - 1
    if not dataset.columns:
        raise ToolError("not_found", f"{base} has no columns", field=field)
    keys = [label(values[0], locale, type=rows.type(0)) for values in dataset.rows]
    plain = [str(values[0]) if values[0] is not None else None for values in dataset.rows]
    found = [i for i in range(count) if part in (keys[i], plain[i])]
    if not found:
        folded = part.casefold()
        found = [i for i, key in enumerate(keys) if key is not None
                 and key.casefold() == folded]
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise ToolError("ambiguous", f"{len(found)} rows of {base} have the key {part!r}: "
                        "name the row by position", field=field,
                        valid_options=[f"{base}/#{i + 1}" for i in found[:10]])
    shown = [key for key in keys if key is not None]
    raise ToolError("not_found", f"{base} has no row whose key ({dataset.columns[0]!r}) is "
                    f"{part!r}", field=field,
                    valid_options=[f"{base}/{quote(key)}" for key in shown[:20]])


# -- writing a value -----------------------------------------------------------------------------


def value_text(value: Any, locale: Any = None, *, type: str | None = None,
               format: str | None = None, where: str = "the value",
               field: str | None = None) -> str:
    """A value as a text fact writes it (see the module doc): a blank or anything that is
    not text, a number, a logical value or a date is refused, naming ``where``; a format on
    anything but a number too."""
    if value is None or value == "":
        raise ToolError("invalid_arguments", f"{where} is blank: a reference writes a value",
                        field=field)
    if format is not None and is_date_pattern(format):
        day = _date_value(value, type)
        if day is None:
            other = (" -- for a number write :0, :0.0 or :#,##0 --" if number(value) else "")
            raise ToolError("invalid_arguments", f"{where} is {what(value)}: a date pattern "
                            f"(:{format}) is for dates{other}; leave it out", field=field)
        try:
            return dates.format_date(day, format, locale)
        except dates.PatternError as error:
            raise ToolError("invalid_arguments", str(error), field=field,
                            valid_options=list(dates.TOKENS)) from None
    if format is not None:
        if not number(value):
            date = _date_value(value, type) is not None
            instead = (f"; for a date write a date pattern instead, of {dates.TOKENS_TEXT}: "
                       ":d mmmm yyyy, :dd/mm/yyyy" if date else "; leave it out")
            raise ToolError("invalid_arguments", f"{where} is {what(value)}: a number format "
                            f"(:{format}) is for numbers{instead}", field=field)
        return number_text(value, format, locale)
    if type == "percent" and number(value):
        return _general(Decimal(repr(value)) * 100, locale) + "%"
    text = label(value, locale, type=type)
    if text is None:
        raise ToolError("invalid_arguments", f"{where} is {what(value)}: a reference writes "
                        "text, a number, a logical value or a date", field=field)
    return text


def _date_value(value: Any, type: str | None) -> Any:
    """The date a value is or names, for a date pattern: a Python ``date`` or ``datetime``,
    ISO 8601 date text (labelled or not: the pattern says it is a date); ``None`` for a
    time of day alone or anything else."""
    if isinstance(value, _dt_time):
        return None
    return dates.date_of(value)


def number_text(value: float, format: str, locale: Any = None) -> str:
    """``value`` in a number format :data:`FORMAT` takes (``0.0``, ``#,##0``, ``0%``),
    rounded half away from zero on the decimal Python shows for it, separators the
    locale's."""
    found = FORMAT.match(":" + format)
    if found is None:
        raise ValueError(f"not a number format a reference takes: {format!r}")
    decimals = len(found.group(2) or "")
    percent = bool(found.group(3))
    exact = Decimal(repr(value)) * (100 if percent else 1)
    rounded = exact.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = abs(rounded)
    sign = "-" if rounded < 0 else ""
    whole, _, fraction = f"{abs(rounded):f}".partition(".")
    if format.startswith("#,##"):
        group = getattr(locale, "thousands_separator", ",")
        whole = f"{int(whole):,}".replace(",", group)
    decimal = getattr(locale, "decimal_separator", ".")
    return sign + whole + (decimal + fraction if decimals else "") + ("%" if percent else "")


def _general(value: Decimal, locale: Any) -> str:
    text = format(value.normalize(), "f") if value == value.to_integral() else \
        format(float(value), ".15g")
    if "e" in text or "E" in text:
        text = format(value, "f")
    text = text.rstrip("0").rstrip(".") if "." in text else text
    decimal = getattr(locale, "decimal_separator", ".")
    return text.replace(".", decimal)


def record(rows: Rows, row: int, column: int, address: str, text: str,
           fmt: str | None) -> dict[str, Any]:
    """What a document keeps of one value written (an :mod:`ooxml_edit.provenance` record's
    fields, without ``v``): the reference as given (no document handle, no format), its
    format, the text written, the document it came from and its version, its sources."""
    given = strip_format(address)
    match = QUALIFIED.match(given)
    placed: dict[str, Any] = {"source": match.group(2) if match else given,
                              "doc": rows.doc, "version": rows.version}
    if fmt:
        placed["format"] = fmt
    placed["text"] = text
    if rows.dataset.sources is not None:
        placed["sources"] = merged(rows.dataset.sources[row][column], rows.merge)
    if rows.stale:
        placed["stale"] = True
    found = provenance.record(placed, document=rows.name)
    found.pop("v", None)
    return found


def walk_texts(node: Any, parts: list[str], where: str,
               visit: Callable[[str, str], str]) -> Any:
    """``node`` with ``visit(text, where)`` applied to every string at the argument path
    ``parts`` (``items[].text`` split at the dots) -- in a list of lists too."""
    if not parts:
        return _leaf(node, where, visit)
    part, rest = parts[0], parts[1:]
    many = part.endswith("[]")
    name = part[:-2] if many else part
    if not isinstance(node, Mapping) or name not in node:
        return node
    node = dict(node)
    path = f"{where}.{name}" if where else name
    value = node[name]
    if many and isinstance(value, list):
        node[name] = [walk_texts(item, rest, f"{path}[{i}]", visit) if rest
                      else _leaf(item, f"{path}[{i}]", visit) for i, item in enumerate(value)]
    else:
        node[name] = walk_texts(value, rest, path, visit)
    return node


def _leaf(value: Any, where: str, visit: Callable[[str, str], str]) -> Any:
    if isinstance(value, str):
        return visit(value, where)
    if isinstance(value, list):
        return [_leaf(item, f"{where}[{i}]", visit) for i, item in enumerate(value)]
    return value


# -- a document's own references -----------------------------------------------------------------


def own_name(address: str, prefix: str, field: str | None = None) -> str:
    """The name a reference to the document's own ``prefix`` gives (``7`` of ``{{n:7}}``,
    ``n:`` the prefix), refused with the form when it carries a document handle or a number
    format."""
    body = address.strip()
    qualified = QUALIFIED.match(body)
    if qualified:
        raise ToolError("invalid_arguments", f"{{{{{body}}}}}: a {prefix} reference names a "
                        "thing of the document it is written into; leave the document "
                        f"out: {{{{{qualified.group(2)}}}}}", field=field)
    name = body[len(prefix):].strip()
    found = FORMAT.search(name)
    if found:
        raise ToolError("invalid_arguments", f"{{{{{body}}}}}: a {prefix} reference takes no "
                        f"number format; write {{{{{prefix}{name[:found.start()]}}}}}",
                        field=field)
    return name


def own_record(prefix: str, name: str, text: str,
               fields: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """What a document keeps of an own reference written: ``source`` (the prefix and the
    name resolved), ``text``, and the format's ``fields``."""
    return {"source": prefix + name, "text": text, **dict(fields or {})}


def not_own(address: str, prefix: str, kind: str | None, takers: Sequence[str],
            field: str | None, said: str | None = None) -> ToolError:
    """The refusal of an own reference in a document whose format does not resolve its
    prefix: in the format's words (``said``, its ``own_facts`` entry for the prefix), else
    naming the kinds whose documents do."""
    why = said.strip().rstrip(".") if said else (
        f"{prefix} references are for {' and '.join(takers) or 'other'} documents; a "
        f"{kind or 'document like this'} takes none")
    return ToolError("invalid_arguments", f"{{{{{address.strip()}}}}}: {why}. Write the "
                     "text itself, or \\{{ for the braces", field=field)


def briefs(facts: Iterable[Fact]) -> list[dict[str, Any]]:
    return [fact.brief() for fact in facts]


__all__ = ["ATTEMPTED_DATE", "ATTEMPTED_FORMAT", "FORMAT", "FORMS", "Fact", "MAX_FACTS", "REFERENCE",
           "bad_format", "briefs", "date_suffix", "expand", "format_error", "is_date_pattern",
           "known_prefixes", "not_own", "number_text", "strip_format",
           "own_name", "own_prefix", "own_prefixes", "own_record", "pick", "quote",
           "qualify_text", "record", "references", "split", "value_text", "walk_texts"]
