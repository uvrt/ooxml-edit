"""Numbers in text that no value holds: ``check``'s ``include: ["numbers"]``.

A figure typed into a sentence -- "rated 2,7/5" -- may be a value of a dataset, or of a
table in another open document, written by hand; or a slip: the data say 2.3.  A check of
numbers reads each paragraph's or shape's own text, finds the numbers in it, and compares
each with every value the session holds:

* the session's datasets (``d:<id>``, :mod:`.datasets`), and
* the tables of its open documents whose format lists them
  (:attr:`~.session.DocumentFormat.value_sources`: a format's ``t:`` tables), read by the
  format's resolver, each column's number format with them (:attr:`.Dataset.formats`).

A number no value matches is **unverified** -- not wrong: a reader may know where it came
from.  It is listed with the nearest values and where they came from.  A number a value
matches but that was typed, not written by reference, is listed under ``typed`` with the
reference that holds it and, when the reference writes the same text, the ``replace_text``
call that writes it as one, so it is kept with its source from then on (:mod:`.textfacts`).

**The match rule: within the rounding the text shows.**  A number written with ``d``
decimals matches a value within half a unit of its last digit: ``2,7`` matches every value
from 2.65 to 2.75 (2.66 among them), ``85%`` every fraction from 0.845 to 0.855 and every
percentage point from 84.5 to 85.5.  A value whose number format shows fewer decimals
also matches as shown: 2.649 in ``0.00`` shows ``2.65``, so a figure rounded again from
what the table shows (``2,7``) is found too.  A sign
written must agree (``-2,7`` never matches 2.66); a number without one matches either (a
fall of 2,7 is the value -2.7).  A scale word after the number (``2,5 mln``, ``1.2bn``)
compares it with the value both scaled and as it is.

**What is read, and what is left aside.**  The text the document's format gives
(:attr:`~.session.DocumentFormat.number_texts`): a paragraph's, a shape's, a table cell's
own text, without what was written by reference (``{{d:...}}``, :mod:`.textfacts`: kept
with its source) and without tables and charts placed with ``from`` (they keep their
provenance).  In that text these are no figures and are left aside, counted by reason:

* ``dates`` and ``times``: ``12/03/2026``, ``2026-03-12``, ``12.03.2026``, ``14:05``,
  ``14.30 uur``, a day next to a month name (``3 March``);
* ``years``: a whole number from 1800 to 2199;
* ``list numbers``: a paragraph's leading ``1.``, ``2)``, ``2.3`` before a capital;
* ``ids``: digits joined to letters (``Q3``, ``CO2``, ``v2``, ``3D``), after ``#`` or
  ``§``, numbers of several dots (``2.1.3``), and a number named by the word before it
  (``section 4.2``, ``page 12``, ``version 2.1``, ``hoofdstuk 3``);
* ``whole``: whole numbers without a percent sign -- counts, item numbers -- unless the
  application asks for them (:attr:`NumberRules.whole_numbers`); with them, ``scales``:
  a whole number that is a score's scale (the ``5`` of ``2,7/5``, ``out of 5``);
* ``ignored``: the application's ignore list (:attr:`NumberRules.ignore`): a number, or a
  phrase whose numbers are no figures (``ISO 9001``, ``Windows 11``).

**Locale.**  Numbers are read in the session's locale (:attr:`.Session.locale`):
``1.234,5`` with a decimal comma, ``1,234.5`` with a decimal point, a thousands separator
only before groups of three digits.  A separator that cannot be the locale's grouping
(``2.7`` with a decimal comma: no group of three follows) is read as the other
convention's decimal, as a reader would.  ``%`` and ``‰`` (and the words ``percent``,
``procent``, ``Prozent``, ``pour cent``) make a percentage; ``−`` (minus) and ``-`` before a
number a sign, between two numbers a range (``2-3`` is two numbers); ``2,7/5`` is two.

**Opt in, scoped, capped.**  Never part of a default check: ``include: ["numbers"]``,
within the check's scope (the arguments that name pages or blocks).  At most
:attr:`NumberRules.texts` texts are listed, each with at most :attr:`NumberRules.per_text`
numbers per list; ``texts_left_out`` and each text's ``more`` count the rest.  The index of
values is kept per session while no dataset or source document changes.
"""

from __future__ import annotations

import bisect
import functools
import re
import threading
import weakref
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, Mapping, Sequence

from ooxml_common.dates import NAMES as _DATE_NAMES

from ..errors import ArgumentError
from .results import Result, ToolError
from .sources import label, number as _is_number, resolve as resolve_rows
from .textfacts import quote

#: ``check``'s ``include`` value that asks for this fact.
INCLUDE = "numbers"

#: What a check says once, when it lists anything: the rule, in a line.
NOTE = ("numbers: a number is unverified when no value of the session's datasets or open "
        "documents' tables rounds to it at the decimals it is written with (2.66 shown "
        "as 2,7 matches); typed: a value matches, but it was typed -- the fix writes it "
        "as a reference. Written by reference and placed with from: left out")


# -- the application's rules ---------------------------------------------------------------------


@dataclass(frozen=True)
class NumberRules:
    """The application's settings for ``check``'s ``numbers`` (never a tool argument):
    ``Toolbox(numbers=NumberRules(...))`` or ``toolbox.session(numbers=...)``.

    ``ignore`` -- numbers and phrases that are no figures: a number (``4.5``, ``"27001"``,
    written with ``.`` for its decimals) is never listed, wherever it is; a phrase
    (``"ISO 9001"``, ``"Windows 11"``) leaves the numbers inside it aside, matched without
    regard to case.  ``whole_numbers`` -- also whole numbers without a percent sign (off: a
    document's counts, item numbers and scales are mostly not a dataset's).  ``texts`` --
    the texts listed at most; ``per_text`` -- the numbers per list of one text at most."""

    ignore: tuple[Any, ...] = ()
    whole_numbers: bool = False
    texts: int = 20
    per_text: int = 5

    def __post_init__(self) -> None:
        items = (self.ignore,) if isinstance(self.ignore, (str, int, float)) else self.ignore
        items = tuple(items)
        for item in items:
            if isinstance(item, bool) or not isinstance(item, (str, int, float)) or \
                    (isinstance(item, str) and not item.strip()):
                raise ArgumentError(f"ignore lists numbers and phrases, not {item!r}")
        object.__setattr__(self, "ignore", items)
        for name in ("texts", "per_text"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ArgumentError(f"{name} is a whole number of at least 1, not {value!r}")

    @functools.cached_property
    def _split(self) -> tuple[frozenset[Decimal], tuple[str, ...]]:
        numbers: set[Decimal] = set()
        phrases: list[str] = []
        for item in self.ignore:
            if isinstance(item, (int, float)):
                numbers.add(Decimal(repr(item)).normalize())
                continue
            try:
                numbers.add(Decimal(item.strip()).normalize())
            except InvalidOperation:
                phrases.append(item.strip().casefold())
        return frozenset(numbers), tuple(phrases)


#: The rules of a session given none.
DEFAULT_RULES = NumberRules()


def rules_of(rules: Any) -> NumberRules:
    """``rules`` as a session keeps them: ``None`` is :data:`DEFAULT_RULES`; a mapping is
    read as :class:`NumberRules`' fields."""
    if rules is None:
        return DEFAULT_RULES
    if isinstance(rules, NumberRules):
        return rules
    if isinstance(rules, Mapping):
        try:
            return NumberRules(**rules)
        except TypeError as error:
            raise ArgumentError(f"numbers takes {', '.join(NumberRules.__dataclass_fields__)}: "
                                f"{error}") from None
    raise ArgumentError(f"numbers takes NumberRules or a mapping of its fields, not "
                        f"{type(rules).__name__}")


# -- what a format gives -------------------------------------------------------------------------


@dataclass(frozen=True)
class NumberText:
    """One text a check of numbers reads, as a format gives it
    (:attr:`~.session.DocumentFormat.number_texts`).

    ``at`` -- its address, as the format's tools take it (a shape ``256.5``, a cell
    ``256.7/cell1,2``, a paragraph's id); ``text`` -- its own text, paragraphs
    joined by ``\\n``; ``written`` -- the ``(start, end)`` spans of ``text`` written by
    reference, left out; ``scope`` -- the ``replace_text`` arguments that name where it is
    (``{"range": <the paragraph's id>}``), for the fix; ``where`` -- what a reader is told
    of its place (``{"page": 3}``)."""

    at: str
    text: str
    written: tuple[tuple[int, int], ...] = ()
    scope: Mapping[str, Any] = field(default_factory=dict)
    where: Mapping[str, Any] = field(default_factory=dict)


# -- reading numbers -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Found:
    """A number in a text: where (``start``, ``end``: the digits, a sign and a percent sign
    or scale word with them), as written (``text``), its magnitude (``value``, never
    negative), ``decimals`` written, ``negative`` (a sign written), ``percent`` (``%``:
    100, ``‰``: 1000, else ``None``), ``scale`` (a scale word's factor), ``grouped`` (a
    thousands separator written), ``digits`` the span of the number alone."""

    start: int
    end: int
    text: str
    value: Decimal
    decimals: int
    negative: bool = False
    percent: int | None = None
    scale: int | None = None
    grouped: bool = False
    digits: tuple[int, int] = (0, 0)
    other: bool = False

    @property
    def whole(self) -> bool:
        return self.decimals == 0 and self.percent is None


_SPACE = "[   ]"
_MINUS = "-−‒–"
_CANDIDATE = re.compile(r"(?<![\w#§№.,])(?P<sign>[" + _MINUS + r"]?)(?P<body>\d+(?:(?:[.,'’]|"
                        + _SPACE + r"(?=\d{3}(?!\d)))\d+)*)")
_PERCENT = re.compile(_SPACE + r"?(?:(%)|(‰)|(?:per\s?cent|percent|procent|prozent|pour\s?cent)"
                      r"(?![\w]))", re.IGNORECASE)
_SCALES = {"k": 1000, "thousand": 1000, "duizend": 1000, "m": 10**6, "mn": 10**6,
           "mln": 10**6, "mio": 10**6, "million": 10**6, "millions": 10**6, "miljoen": 10**6,
           "bn": 10**9, "mld": 10**9, "mrd": 10**9, "billion": 10**9, "billions": 10**9,
           "miljard": 10**9}
_SCALE = re.compile(_SPACE + r"?(" + "|".join(sorted(_SCALES, key=len, reverse=True))
                    + r")\.?(?![\w])", re.IGNORECASE)
_MONTHS = sorted({name.casefold().rstrip(".") for names in _DATE_NAMES.values()
                  for name in (*names.months, *names.short_months) if len(name) >= 3},
                 key=len, reverse=True)
_MONTH = "(?:" + "|".join(re.escape(name) for name in _MONTHS) + r")\.?"
_DATES = [
    re.compile(r"(?<![\d.,])\d{1,4}([./-])\d{1,2}\1\d{1,4}(?![\d])"),
    re.compile(r"(?<![\d.,])\d{4}-\d{2}(?![\d])"),
    re.compile(r"(?<![\d.,:])\d{1,2}:\d{2}(?::\d{2})?(?![\d])"),
    re.compile(r"(?<![\d.,])\d{1,2}[.hu]\d{2}" + _SPACE + r"?(?:u|uur|h|am|pm|a\.m\.|p\.m\.)"
               r"(?![\w])", re.IGNORECASE),
    re.compile(r"(?<![\w.,])\d{1,2}(?:st|nd|rd|th|e|er|de|ste)?" + _SPACE + r"?" + _MONTH
               + r"(?![\w])", re.IGNORECASE),
    re.compile(r"(?<![\w])" + _MONTH + _SPACE + r"?\d{1,2}(?![\d]|[.,]\d)", re.IGNORECASE),
]
#: A word that makes the number after it a name: a section, a page, a version.
_NAMING = re.compile(r"(?:\b(?:sections?|chapters?|pages?|pp?\.|figures?|fig\.|tables?|"
                     r"versions?|v\.|releases?|steps?|items?|no\.|nr\.?|art\.|articles?|"
                     r"annex|appendix|hoofdstuk|paragraaf|pagina|p\.|figuur|tabel|versie|"
                     r"bijlage|artikel|stap|punt|kapitel|seite|abschnitt|chapitre|annexe)"
                     + _SPACE + r"?)$", re.IGNORECASE)
#: What makes a whole number after a figure its scale: ``2,7/5``, ``3.8 out of 5``.
_SCALE_AFTER = re.compile(r"\d" + _SPACE + r"?(?:/|out\s+of|op|van|sur|von)" + _SPACE + r"?$",
                          re.IGNORECASE)
#: A paragraph's own number at its start: ``1.``, ``2)``, ``2.3 Results``, ``4 <tab>``.
_LEADING = re.compile(r"^\s*\(?\d+(?:\.\d+)*(?:[.)](?=\s)|(?=\t)|(?=" + _SPACE + r"+[A-Z]))")


def _spans(text: str, patterns: Iterable[re.Pattern]) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for pattern in patterns for m in pattern.finditer(text)]


def _inside(at: tuple[int, int], spans: Iterable[tuple[int, int]]) -> bool:
    return any(start < at[1] and at[0] < end for start, end in spans)


def _parse(body: str, locale: Any) -> tuple[Decimal, int, bool, bool] | None:
    """``(value, decimals, grouped, other)`` of a run of digits and separators read in
    ``locale``, ``other`` when its decimal is the other convention's; ``None`` when it is
    no number (``2.1.3``)."""
    decimal = getattr(locale, "decimal_separator", ".")
    group = getattr(locale, "thousands_separator", ",")
    spaces = "   "
    seps = [c for c in body if not c.isdigit()]
    if not seps:
        return Decimal(body), 0, False, False
    parts = re.split(r"[^\d]", body)

    def grouping(chars: Sequence[str], pieces: Sequence[str]) -> bool:
        return (all(c == group or (c in spaces and group in spaces) or
                    (c in "'’" and group in "'’") for c in chars)
                and 1 <= len(pieces[0]) <= 3 and all(len(p) == 3 for p in pieces[1:]))

    if seps[-1] == decimal and grouping(seps[:-1], parts[:-1]):
        whole = "".join(parts[:-1])
        return Decimal(whole + "." + parts[-1]), len(parts[-1]), len(seps) > 1, False
    if grouping(seps, parts):
        return Decimal("".join(parts)), 0, True, False
    if len(seps) == 1 and seps[0] in ".,":       # the other convention's decimal
        return Decimal(parts[0] + "." + parts[1]), len(parts[1]), False, True
    return None


def find_numbers(text: str, locale: Any, rules: NumberRules = DEFAULT_RULES,
                 written: Iterable[tuple[int, int]] = ()) -> tuple[list[Found], dict[str, int]]:
    """The numbers of ``text`` a check compares (see the module's notes), and how many were
    left aside, by reason."""
    numbers, phrases = rules._split
    aside: dict[str, int] = {}
    written = list(written)
    dates = _spans(text, _DATES)
    ignored = []
    folded = text.casefold()
    for phrase in phrases:
        at = folded.find(phrase)
        while at >= 0:
            ignored.append((at, at + len(phrase)))
            at = folded.find(phrase, at + 1)
    leading = []
    for line in re.finditer(r"[^\n]+", text):
        match = _LEADING.match(line.group(0))
        if match:
            leading.append((line.start(), line.start() + match.end()))
    out: list[Found] = []

    def aside_for(reason: str) -> None:
        aside[reason] = aside.get(reason, 0) + 1

    for match in _CANDIDATE.finditer(text):
        start, end = match.start("body"), match.end("body")
        sign = match.group("sign")
        before = text[match.start() - 1] if match.start() > 0 else ""
        if sign and before and (before.isdigit() or before.isalpha()):
            sign = ""                                  # a range 2-3, a word's hyphen
        span = (start - len(sign), end)
        if _inside(span, written):
            continue
        after = text[end:end + 1]
        if (after.isalpha() or after == "_") and _SCALE.match(text, end) is None:
            aside_for("ids")                           # Q3, CO2, 3D, 2nd
            continue
        if _inside(span, ignored):
            aside_for("ignored")
            continue
        if _inside(span, dates):
            aside_for("dates")
            continue
        if any(first <= start and end <= last for first, last in leading):
            aside_for("list numbers")
            continue
        parsed = _parse(match.group("body"), locale)
        if parsed is None or _NAMING.search(text, max(0, span[0] - 16), span[0]):
            aside_for("ids")                           # 2.1.3; section 4.2, version 2.1
            continue
        value, decimals, grouped, other = parsed
        stop = end
        percent = None
        found = _PERCENT.match(text, end)
        if found:
            percent = 1000 if found.group(2) else 100
            stop = found.end()
        scale_factor = None
        if percent is None:
            found = _SCALE.match(text, end)
            if found:
                scale_factor = _SCALES[found.group(1).casefold()]
                stop = found.end()
        number = Found(span[0], stop, text[span[0]:stop], value, decimals, bool(sign),
                       percent, scale_factor, grouped, (start, end), other)
        if number.whole and scale_factor is None and \
                1800 <= value <= 2199 and len(match.group("body")) == 4:
            aside_for("years")
            continue
        if value.normalize() in numbers:
            aside_for("ignored")
            continue
        if number.whole and not rules.whole_numbers:
            aside_for("whole")
            continue
        if number.whole and _SCALE_AFTER.search(text, max(0, span[0] - 12), span[0]):
            aside_for("scales")                        # the 5 of 2,7/5, out of 5
            continue
        out.append(number)
    return out, aside


# -- number formats ------------------------------------------------------------------------------


def shown(code: str | None) -> tuple[int, bool, int] | None:
    """What a cell's number format ``code`` shows of a number: ``(decimals, percent,
    divide)`` -- the digits after its decimal point, whether it multiplies by 100 (``%``),
    and by what it divides (each ``,`` right after the last digit placeholder divides by
    1000) -- read from its first section, as ECMA-376 Part 1, 18.8.31 (numFmt) writes
    the codes.  ``None`` for ``General``, a text format, or none."""
    if not code or not isinstance(code, str):
        return None
    section = code.split(";")[0]
    plain = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]|_.|\*.', "", section)
    if plain.strip().casefold() in ("general", "@", "") or not re.search(r"[0#?]", plain):
        return None
    if re.search(r"[eE][+-]", plain):
        return None                                    # scientific: no fixed decimals
    point = plain.find(".")
    decimals = len(re.findall(r"[0#?]", plain[point + 1:])) if point >= 0 else 0
    last = max(plain.rfind(c) for c in "0#?")
    trailing = re.match(r",+", plain[last + 1:])
    divide = 1000 ** len(trailing.group(0)) if trailing else 1
    return decimals, "%" in plain, divide


def _rounded(value: float, decimals: int) -> float:
    exact = Decimal(repr(value)).quantize(Decimal(1).scaleb(-decimals), rounding="ROUND_HALF_UP")
    return float(exact)


# -- the index of values -------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """A dataset or table whose values are indexed: ``address`` as a reference names it
    (``d:by_topic``, ``t:Scores``, ``d2/t:Scores``), ``shown`` its unqualified form,
    ``dataset``."""

    address: str
    shown: str
    dataset: Any


@dataclass(frozen=True)
class Value:
    """One indexed value: its source, row and column, the number, whether it is a
    percentage (a ``percent`` column, a ``%`` format) and its format's reading
    (:func:`shown`)."""

    source: int
    row: int
    column: int
    number: float
    percent: bool
    format: tuple[int, bool, int] | None


class Index:
    """Every number of some datasets and tables, sorted twice: as stored (and as each
    format shows it), and times 100 (a fraction quoted as a percentage)."""

    def __init__(self, sources: Sequence[Source]) -> None:
        self.sources = list(sources)
        self.values: list[Value] = []
        self._keys: dict[int, list[str | None]] = {}
        plain: list[tuple[float, int]] = []
        hundred: list[tuple[float, int]] = []
        for at, source in enumerate(self.sources):
            dataset = source.dataset
            formats = getattr(dataset, "formats", None)
            types = dataset.types
            for r, row in enumerate(dataset.rows):
                for c, item in enumerate(row):
                    if not _is_number(item):
                        continue
                    code = formats[c] if formats is not None and c < len(formats) else None
                    reading = shown(code)
                    percent = (types is not None and types[c] == "percent") or \
                        bool(reading and reading[1])
                    i = len(self.values)
                    self.values.append(Value(at, r, c, float(item), percent, reading))
                    magnitude = abs(float(item))
                    plain.append((magnitude, i))
                    hundred.append((magnitude * 100, i))
                    if reading is not None:
                        decimals, pct, divide = reading
                        display = magnitude * (100 if pct else 1) / divide
                        rounded = _rounded(display, decimals)
                        if rounded != display:
                            (hundred if pct else plain).append((rounded, i))
                        if divide != 1:
                            plain.append((display, i))
        plain.sort()
        hundred.sort()
        self._plain = ([key for key, _ in plain], [i for _, i in plain])
        self._hundred = ([key for key, _ in hundred], [i for _, i in hundred])

    def __len__(self) -> int:
        return len(self.values)

    def keys(self, source: int) -> list[str | None]:
        """The rows' keys of a source: each first column's value as a cell writes it."""
        found = self._keys.get(source)
        if found is None:
            dataset = self.sources[source].dataset
            kind = dataset.types[0] if dataset.types else None
            found = self._keys[source] = [label(row[0], None, type=kind) for row in dataset.rows]
        return found

    def _range(self, table: tuple[list[float], list[int]], low: float,
               high: float) -> list[int]:
        keys, ids = table
        return ids[bisect.bisect_left(keys, low):bisect.bisect_right(keys, high)]

    def targets(self, number: Found) -> list[tuple[str, float, float]]:
        """Where ``number`` is looked for: ``(table, centre, half width)`` -- ``plain``
        (as stored, as shown) or ``hundred`` (times 100)."""
        t = float(number.value)
        half = 0.5 * 10 ** -number.decimals
        out = []
        if number.percent == 100:
            out += [("hundred", t, half), ("plain", t, half)]
        elif number.percent == 1000:
            out += [("plain", t / 1000, half / 1000), ("plain", t, half)]
        else:
            out.append(("plain", t, half))
            out.append(("hundred*", t, half))          # a percentage column quoted bare
            if number.scale:
                out.append(("plain", t * number.scale, half * number.scale))
        return out

    def matches(self, number: Found) -> list[tuple[int, str]]:
        """The values ``number`` matches (see the module's notes), each once, with how:
        ``plain`` or ``hundred``."""
        found: dict[int, str] = {}
        for table, centre, half in self.targets(number):
            slack = half * (1 + 1e-9) + abs(centre) * 1e-12
            keys = self._hundred if table.startswith("hundred") else self._plain
            for i in self._range(keys, centre - slack, centre + slack):
                value = self.values[i]
                if table == "hundred*" and not value.percent:
                    continue
                if number.negative and value.number > 0:
                    continue
                found.setdefault(i, table.rstrip("*"))
        return sorted(found.items())

    def nearest(self, number: Found, count: int = 2) -> list[tuple[int, str, float]]:
        """The ``count`` values nearest ``number`` by relative distance, each once:
        ``(value, how, distance)``."""
        best: dict[int, tuple[float, str]] = {}
        for table, centre, _ in self.targets(number):
            if table == "hundred*":
                continue
            keys, ids = self._hundred if table == "hundred" else self._plain
            at = bisect.bisect_left(keys, centre)
            for j in range(max(0, at - count - 2), min(len(keys), at + count + 2)):
                i = ids[j]
                if number.negative and self.values[i].number > 0:
                    continue
                distance = abs(keys[j] - centre) / max(abs(centre), 1e-9)
                if i not in best or distance < best[i][0]:
                    best[i] = (distance, table)
        ranked = sorted(best.items(), key=lambda item: item[1][0])[:count]
        return [(i, how, distance) for i, (distance, how) in ranked]


# -- the index of a session ----------------------------------------------------------------------


_CACHE_LOCK = threading.Lock()


def _plan(call: Any) -> tuple[list[Any], list[tuple[str, Any, Any]], dict[str, int],
                               list[str], tuple]:
    """What a check compares with, before anything is read: the dataset entries, the held
    documents that list values (``(doc, entry, format)``), how many of those take each
    prefix, what is not held, and the key the index is kept under -- each dataset by
    identity, each document by identity and version (the kept index holds them, so an
    identity is never another's: a document's entry is kept weakly, and a kept index whose
    entry is gone is built again)."""
    session = call.session
    entries = {entry.id: entry for entry in session.dataset_entries()}
    for name, entry in call.context.datasets.items():
        entries[name] = entry
    key: list[Any] = [(entry.address, id(entry.dataset)) for entry in entries.values()]
    notes: list[str] = []
    takers: dict[str, int] = {}
    listed: list[tuple[str, Any, Any]] = []
    for doc, held in session.documents.items():
        fmt = session.formats.get(held.kind)
        if getattr(fmt, "value_sources", None) is None:
            continue
        if doc not in call.entries:
            notes.append(f"{doc} was not read: it is not held by this call")
            continue
        listed.append((doc, held, fmt))
        key.append((doc, id(held), held.version))
        for prefix in getattr(fmt, "datasets", None) or {}:
            takers[prefix] = takers.get(prefix, 0) + 1
    return list(entries.values()), listed, takers, notes, tuple(key)


def _read(entries: Sequence[Any], listed: Sequence[tuple[str, Any, Any]],
          takers: Mapping[str, int], notes: list[str]) -> list[Source]:
    """The datasets, and the tables each listed document's format lists, read by its
    resolver; what could not be read is said in ``notes``."""
    out = [Source(entry.address, entry.address, entry.dataset) for entry in entries]
    for doc, held, fmt in listed:
        try:
            addresses = list(fmt.value_sources(held.document))
        except ToolError as error:
            notes.append(f"{doc}: its tables were not read ({error.message})")
            continue
        for address in addresses:
            try:
                dataset = resolve_rows(fmt, held.document, address)
            except ToolError as error:
                notes.append(f"{doc}/{address} was not read ({error.message})")
                continue
            prefix = next((p for p in takers if address.startswith(p)), "")
            unqualified = takers.get(prefix, 0) == 1
            out.append(Source(f"{doc}/{address}", address if unqualified else f"{doc}/{address}",
                              dataset))
    return out


def index_of(call: Any) -> tuple[Index, list[str]]:
    """The session's index of values (:class:`Index`), read and built once while no
    dataset or listed document changes, and what could not be read."""
    entries, listed, takers, notes, key = _plan(call)
    session = getattr(call.session, "session", call.session)
    with _CACHE_LOCK:
        kept = getattr(session, "_number_index", None)
        if kept is not None and kept[0] == key and all(ref() is not None for ref in kept[3]):
            return kept[1], notes + kept[2]
    unread: list[str] = []
    index = Index(_read(entries, listed, takers, unread))
    with _CACHE_LOCK:
        try:
            session._number_index = (key, index, unread,
                                     [weakref.ref(held) for _, held, _ in listed])
        except AttributeError:         # a session that keeps nothing: built each time
            pass
    return index, notes + unread


# -- writing the fact ----------------------------------------------------------------------------


def _address(index: Index, value: Value) -> str:
    """The reference of one value, as a model writes it: its source, the row by its key
    (the first column's label, when no other row has it) or by position, the column."""
    source = index.sources[value.source]
    dataset = source.dataset
    keys = index.keys(value.source)
    key = keys[value.row]
    unique = key is not None and value.column != 0 and keys.count(key) == 1 and \
        not re.fullmatch(r"#\d+", key)
    part = quote(key) if unique and key is not None else f"#{value.row + 1}"
    return f"{source.shown}/{part}/{quote(dataset.columns[value.column])}"


def _value_text(index: Index, value: Value, how: str, locale: Any) -> str:
    """A value as a reader compares it with the text: as its format shows it (or in
    General), a percentage when quoted as one."""
    from .textfacts import number_text

    if value.format is not None:
        decimals, pct, divide = value.format
        fmt = "0" + ("." + "0" * decimals if decimals else "")
        if pct or how == "hundred":
            return number_text(value.number, fmt + "%", locale)
        return number_text(value.number / divide, fmt, locale)
    if how == "hundred":
        hundred = Decimal(repr(value.number)) * 100
        return (label(float(hundred), locale) or "") + "%"
    return label(value.number, locale) or repr(value.number)


def _sources_of(index: Index, value: Value) -> list[str]:
    sources = index.sources[value.source].dataset.sources
    return list(sources[value.row][value.column][:2]) if sources is not None else []


def _distinct(index: Index, matched: Sequence[tuple[int, str]]) -> list[tuple[int, str]]:
    """``matched`` without a value whose sources an earlier one shares: a dataset's value
    and the table cell it was derived from are one value (the dataset's, first)."""
    out: list[tuple[int, str]] = []
    seen: set[str] = set()
    for i, how in matched:
        value = index.values[i]
        sources = index.sources[value.source].dataset.sources
        own = set(sources[value.row][value.column]) if sources is not None else set()
        own.add(_address(index, value))
        if own & seen:
            continue
        seen |= own
        out.append((i, how))
    return out


def _format_for(number: Found, how: str, value: Value) -> str | None:
    """The number format that writes ``value`` as ``number`` is written, or ``None``."""
    if number.scale or number.percent == 1000:
        return None
    fmt = ("#,##0" if number.grouped else "0") + \
        ("." + "0" * number.decimals if number.decimals else "")
    if number.percent and how == "hundred":
        return fmt + "%"
    if how == "hundred":                     # a percentage column quoted without %
        return None
    return fmt


def _fix(call: Any, item: NumberText, number: Found, address: str, shown: str, fmt: str,
         siblings: Sequence[NumberText], locale: Any) -> dict[str, Any] | None:
    """The ``replace_text`` call that writes ``number`` as the reference ``address:fmt``
    (``shown``: the address as the model writes it), when the reference writes the same
    number -- ``writes`` saying how, when the text wrote it otherwise (``2.7`` where the
    locale writes ``2,7``) -- else ``None`` (a value rounded twice, a sign the text does
    not write)."""
    try:
        written, _ = call.fact(f"{address}:{fmt}")
    except ToolError:
        return None
    percent = fmt.endswith("%")
    start = number.start if number.negative else number.digits[0]
    end = number.end if percent else number.digits[1]
    if written.startswith("-") != number.negative:
        return None
    again, _ = find_numbers(written, locale, NumberRules(whole_numbers=True))
    if len(again) != 1 or again[0].value != number.value or \
            again[0].decimals != number.decimals:
        return None
    text = item.text
    replaced = "{{" + shown + ":" + fmt + "}}"
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end
    for width in (12, 24, 48, 10**6):
        left, right = max(line_start, start - width), min(line_end, end + width)
        while left > line_start and not text[left - 1].isspace():
            left -= 1                                  # whole words: never half a number
        while right < line_end and not text[right].isspace():
            right += 1
        snippet = text[left:right]
        if sum(other.text.count(snippet) for other in siblings) == 1:
            doc = {"doc": call.entry.doc_id} if call.entry is not None else {}
            out: dict[str, Any] = {"tool": "replace_text", "arguments": {
                **doc, "find": snippet, "replace": snippet[:start - left] + replaced
                + snippet[end - left:], "expect": "one", **dict(item.scope)}}
            if written != text[start:end]:
                out["writes"] = written
            return out
    return None


def _scope_key(item: NumberText) -> str:
    return repr(sorted(dict(item.scope).items()))


def report(call: Any, items: Sequence[NumberText], rules: NumberRules,
           narrower: Sequence[str] = ()) -> dict[str, Any]:
    """The fact: per text, its unverified numbers and its typed ones (see the module's
    notes), within :class:`NumberRules`' caps; ``narrower``: the arguments that narrow
    the check, named when something did not fit."""
    locale = call.locale
    index, notes = index_of(call)
    texts: list[dict[str, Any]] = []
    left_out = 0
    unverified_count = typed_count = 0
    aside: dict[str, int] = {}
    by_scope: dict[Any, list[NumberText]] = {}
    for item in items:
        by_scope.setdefault(_scope_key(item), []).append(item)
    for item in items:
        found, skipped = find_numbers(item.text, locale, rules, item.written)
        for reason, count in skipped.items():
            aside[reason] = aside.get(reason, 0) + count
        if not found or not len(index):
            continue
        unverified: list[dict[str, Any]] = []
        typed: list[dict[str, Any]] = []
        for number in found:
            matched = index.matches(number)
            if not matched:
                near = []
                for i, how, _ in index.nearest(number):
                    value = index.values[i]
                    close: dict[str, Any] = {"value": _value_text(index, value, how, locale),
                                             "from": _address(index, value)}
                    sources = _sources_of(index, value)
                    if sources:
                        close["sources"] = sources
                    near.append(close)
                unverified.append({"text": number.text, "near": near})
                continue
            matched = _distinct(index, matched)
            addresses = list(dict.fromkeys(_address(index, index.values[i])
                                           for i, _ in matched))
            entry: dict[str, Any] = {"text": number.text, "from": addresses[0]}
            if len(addresses) > 1:
                entry["from"] = addresses[:3]
                entry["matches"] = len(addresses)
            else:
                i, how = matched[0]
                value = index.values[i]
                sources = _sources_of(index, value)
                if sources:
                    entry["sources"] = sources
                fmt = _format_for(number, how, value)
                source = index.sources[value.source]
                full = source.address + addresses[0][len(source.shown):]
                fix = _fix(call, item, number, full, addresses[0], fmt,
                           by_scope[_scope_key(item)], locale) if fmt else None
                if fix is not None:
                    entry["fix"] = fix
            typed.append(entry)
        unverified_count += len(unverified)
        typed_count += len(typed)
        if not unverified and not typed:
            continue
        if len(texts) >= rules.texts:
            left_out += 1
            continue
        listed: dict[str, Any] = {"at": item.at, **dict(item.where)}
        more = 0
        if unverified:
            listed["unverified"] = unverified[:rules.per_text]
            more += max(0, len(unverified) - rules.per_text)
        if typed:
            listed["typed"] = typed[:rules.per_text]
            more += max(0, len(typed) - rules.per_text)
        if more:
            listed["more"] = more
        texts.append(listed)
    data: dict[str, Any] = {"unverified": unverified_count, "typed": typed_count,
                            "values": len(index), "texts": texts}
    if left_out:
        data["texts_left_out"] = left_out
    if aside:
        data["left_aside"] = dict(sorted(aside.items()))
    if not len(index):
        notes.append("no values to compare with: no dataset is defined and no open "
                     "document lists tables (open the document that holds them, or define a dataset)")
    if notes:
        data["notes"] = notes
    if texts:
        data["note"] = NOTE
    fixes = sum(1 for t in texts for entry in t.get("typed", ()) if "fix" in entry)
    if fixes > 1:
        data["fix_hint"] = (f"{fixes} fixes: send them as one batch (ops of replace_text), one "
                            "undo step")
    if texts and (left_out or any("more" in t for t in texts)):
        data["left_out_hint"] = "check fewer at a time to see the rest" + (
            f" ({', '.join(narrower)})" if narrower else "")
    return data


def texts_of(call: Any, scope: Mapping[str, Any]) -> list[NumberText]:
    """The texts the call's document gives for ``scope``, by its format's
    :attr:`~.session.DocumentFormat.number_texts`; ``invalid_arguments`` when it gives
    none."""
    entry = call.entry
    fmt = call.session.formats.get(entry.kind) if entry is not None else None
    hook = getattr(fmt, "number_texts", None)
    if hook is None:
        kinds = sorted(kind for kind, other in call.session.formats.items()
                       if getattr(other, "number_texts", None) is not None)
        raise ToolError("invalid_arguments", f"numbers are read from "
                        f"{' and '.join(kinds) or 'no'} documents here; a "
                        f"{entry.kind if entry else 'call without a document'} gives no "
                        "texts for them: leave numbers out of include", field="include",
                        valid_options=[k for k in ("validate", "layout")])
    found = list(hook(call, scope))
    for item in found:
        if not isinstance(item, NumberText):
            raise TypeError(f"the {entry.kind} number_texts hook gave "
                            f"{type(item).__name__}, not NumberText")
    return found


def summary(data: Mapping[str, Any]) -> str:
    texts = len(data["texts"]) + data.get("texts_left_out", 0)
    return (f"{data['unverified']} unverified and {data['typed']} typed number(s) in "
            f"{texts} text(s), against {data['values']} value(s)")


def with_numbers(handler: Callable[..., Any], scope: Sequence[str],
                 asks: Sequence[str]) -> Callable[..., Any]:
    """``check``'s handler for a kind, ``include: ["numbers"]`` done here for every kind:
    the fact is added to the kind's result as ``numbers``; the kind's handler never sees
    the value, and is not run when nothing else is asked of it.  ``scope``: the arguments
    that narrow a check, handed to the format's hook; ``asks``: those that ask the kind's
    handler for something besides."""

    @functools.wraps(handler)
    def run(call: Any, **arguments: Any) -> Any:
        include = arguments.get("include")
        if not include or INCLUDE not in include:
            return handler(call, **arguments)
        rest = [item for item in include if item != INCLUDE]
        given = {name: arguments[name] for name in scope if arguments.get(name) is not None}
        rules = rules_of(getattr(call.session, "numbers", None))
        data = report(call, texts_of(call, given), rules, narrower=scope)
        said = summary(data)
        if not rest and not any(arguments.get(name) not in (None, False, [], {})
                                for name in asks):
            return Result(summary=said, data={"numbers": data})
        returned = handler(call, **{**arguments, "include": rest or None})
        if isinstance(returned, Result):
            if isinstance(returned.data, dict):
                returned.data["numbers"] = data
            returned.summary = f"{returned.summary}; {said}" if returned.summary else said
            return returned
        if isinstance(returned, dict):
            returned["numbers"] = data
        return returned

    return run


__all__ = ["DEFAULT_RULES", "Found", "INCLUDE", "Index", "NOTE", "NumberRules", "NumberText",
           "Source", "Value", "find_numbers", "index_of", "report", "rules_of", "shown",
           "summary", "texts_of", "with_numbers"]

