"""Date patterns: a date written as text, or kept as a number format code.

A caller names how a date is shown with a *pattern* in the date letters of Office's number
formats, which models know: ``d mmmm yyyy`` writes 2 March 2026 as ``2 March 2026``
(``2 maart 2026`` in nl-NL), ``dd/mm/yyyy`` as ``02/03/2026``.  The tokens (:data:`TOKENS`):

* ``d`` the day (``2``), ``dd`` two digits (``02``), ``ddd`` the weekday's abbreviation
  (``Mon``), ``dddd`` its name (``Monday``);
* ``m`` the month (``3``), ``mm`` two digits (``03``), ``mmm`` its abbreviation (``Mar``),
  ``mmmm`` its name (``March``), ``mmmmm`` its first letter (``M``);
* ``yy`` the year's last two digits (``26``), ``yyyy`` all four (``2026``).

Letters are read in either case (``DD/MM/YYYY``).  Every character that is not a letter is
written as given -- spaces, ``/``, ``-``, ``.``, ``,`` -- and text in double quotes too
(``d "de" mmmm "de" yyyy``), or one character after a backslash.  Any other letter
outside quotes, and a run of letters that is no token (``ddddd``, ``yyy``, ``h``), is
refused (:class:`PatternError`), listing the tokens: a pattern is about dates only, and a
letter a number format reads as something else (``h`` an hour, ``s`` a second) is never
written into a kept code by accident.

**Names are measured.**  Month and day names (:data:`NAMES`) are the ones Office shows for
each locale, measured on Office for Mac, 2026-10-16 (the application and the method:
``docs/TOOLS.md``, "Date patterns"), with an explicit locale code in the number format
(``[$-413]mmmm``, one locale at a time; ``mmmmm`` is the full name's first letter there
too).  en-US and en-GB show the same names, and nl-NL and nl-BE.

**A region shows its language's names.**  Measured in Office for Mac, 2026-10-17: en-BE
and en-IE show en-US's names, fr-BE and fr-CH fr-FR's, de-BE and de-CH de-DE's -- Office
names a date in the region's language (and it-CH as it-IT).  So a locale whose tag is not
in :data:`NAMES` takes its language's (:data:`LANGUAGES`: en, nl, de, fr, it), in every
region; its other conventions (separators, day order) stay its own.  Not Spanish:
measured, es-MX and ``es`` abbreviate otherwise than es-ES (``mié.``, ``set``), so they
are listed each, and another Spanish region refuses name tokens.  A locale whose language has no
measured names refuses a name token (``ddd``, ``dddd``, ``mmm``, ``mmmm``, ``mmmmm``)
rather than guessing them; the numeric tokens work in any locale.  The locale is the
session's: anything with a ``language`` (an :class:`ooxml_common.locales.Locale`), or a
language tag.

**Text and kept codes.**  :func:`format_date` writes fixed text: its separators are the
pattern's, exactly.  :func:`format_code` turns a pattern into a number format code, as
Office stores one -- spaces and commas escaped, the locale's id in front:
``[$-413]d\\ mmmm\\ yyyy`` (measured: Office takes ``[$-nl-NL]`` too and stores it as
``[$-413]``; ``/``, ``-`` and ``.`` stay unescaped) -- for a chart to keep with its dates
(:class:`DateCategory`).  The id is the locale's own where Office was seen storing it
(:data:`LCIDS`: ``[$-fr-BE]`` is stored ``[$-80C]``), else its language's in
:data:`LANGUAGES` (en-BE writes ``[$-409]``): the prefix fixes the names only, and those
are the language's either way.  Office shows a kept code differently per viewer (measured on
Office for Mac, 2026-10-16, switching the macOS region): a ``/`` in the code is the
viewer's date separator (``dd/mm/yyyy`` shows ``02-03-2026`` in a Dutch (Netherlands)
region and ``02.03.2026`` in a German one; a ``-`` stays ``-``), and the ``[$-LCID]``
prefix changes the names only, never the separator or the order.  So a kept code fixes
the names and the order of a date, not its separators.

**Reading a kept code back.**  :func:`is_date_code` tells a date code from a number one,
so a chart's categories kept as serials read back as dates (``Chart.category_dates``).
ooxml-common keeps a copy of :data:`NAMES`, :data:`LANGUAGES` and :data:`LCIDS` to draw
such categories (``ooxml_common.dates``, which may not import this package); a test there
checks the two agree.

Standard library only; nothing here reads the operating system's locale.
"""

from __future__ import annotations

import datetime as _dt
import re
from types import MappingProxyType
from typing import Any, Mapping, NamedTuple

__all__ = ["LANGUAGES", "LCIDS", "NAMES", "NAME_TOKENS", "TOKENS", "TOKENS_TEXT", "DateCategory",
           "DateNames", "PatternError", "date_of", "format_code", "format_date", "from_serial",
           "is_date_code", "lcid_for", "names_for", "names_tag", "parse", "serial", "uses_names"]

#: The tokens a pattern takes, as an error lists them.
TOKENS = ("d", "dd", "ddd", "dddd", "m", "mm", "mmm", "mmmm", "mmmmm", "yy", "yyyy")
#: The tokens that write a name, which need the locale's measured :data:`NAMES`.
NAME_TOKENS = frozenset({"ddd", "dddd", "mmm", "mmmm", "mmmmm"})


class DateNames(NamedTuple):
    """A locale's month names and abbreviations (January first) and day names and
    abbreviations (Monday first, as Python's ``date.weekday()`` counts)."""

    months: tuple[str, ...]
    short_months: tuple[str, ...]
    days: tuple[str, ...]
    short_days: tuple[str, ...]


def _names(months: str, short_months: str, days: str, short_days: str) -> DateNames:
    return DateNames(tuple(months.split()), tuple(short_months.split()), tuple(days.split()),
                     tuple(short_days.split()))


# Measured on Office for Mac, 2026-10-16: each name as Office showed it under [$-LCID]mmmm,
# mmm, dddd and ddd (round 6, R9; docs/TOOLS.md says which application).  Not taken from
# any other source.
_ENGLISH = _names(
    "January February March April May June July August September October November December",
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec",
    "Monday Tuesday Wednesday Thursday Friday Saturday Sunday",
    "Mon Tue Wed Thu Fri Sat Sun")
_DUTCH = _names(
    "januari februari maart april mei juni juli augustus september oktober november december",
    "jan feb mrt apr mei jun jul aug sep okt nov dec",
    "maandag dinsdag woensdag donderdag vrijdag zaterdag zondag",
    "ma di wo do vr za zo")
_GERMAN = _names(
    "Januar Februar März April Mai Juni Juli August September Oktober November Dezember",
    "Jan Feb Mär Apr Mai Jun Jul Aug Sep Okt Nov Dez",
    "Montag Dienstag Mittwoch Donnerstag Freitag Samstag Sonntag",
    "Mo Di Mi Do Fr Sa So")
_FRENCH = _names(
    "janvier février mars avril mai juin juillet août septembre octobre novembre décembre",
    "janv. févr. mars avr. mai juin juil. août sept. oct. nov. déc.",
    "lundi mardi mercredi jeudi vendredi samedi dimanche",
    "lun. mar. mer. jeu. ven. sam. dim.")
_SPANISH = _names(
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre",
    "ene. feb. mar. abr. may. jun. jul. ago. sep. oct. nov. dic.",
    "lunes martes miércoles jueves viernes sábado domingo",
    "lu. ma. mi. ju. vi. sá. do.")
# Measured on Office for Mac's charts, 2026-10-17, under [$-80A] and [$-A] (what Office
# stores for [$-es-MX] and [$-es]): the months and days of es-ES, other abbreviations.
_SPANISH_MEXICO = _SPANISH._replace(
    short_days=tuple("lun. mar. mié. jue. vie. sáb. dom.".split()))
_SPANISH_ANY = _SPANISH._replace(
    short_months=tuple("ene feb mar abr may jun jul ago set oct nov dic".split()),
    short_days=tuple("lun mar mié jue vie sáb dom".split()))
_ITALIAN = _names(
    "gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre "
    "dicembre",
    "gen feb mar apr mag giu lug ago set ott nov dic",
    "lunedì martedì mercoledì giovedì venerdì sabato domenica",
    "lun mar mer gio ven sab dom")

#: Month and day names per language tag, measured on Office for Mac, 2026-10-16.
NAMES: Mapping[str, DateNames] = MappingProxyType({
    "en-US": _ENGLISH, "en-GB": _ENGLISH, "nl-NL": _DUTCH, "nl-BE": _DUTCH,
    "de-DE": _GERMAN, "fr-FR": _FRENCH, "es-ES": _SPANISH, "it-IT": _ITALIAN,
    "es-MX": _SPANISH_MEXICO, "es": _SPANISH_ANY,
})

#: The tag in :data:`NAMES` whose names every region of a language shows, by language
#: subtag.  Measured in Office for Mac, 2026-10-17: en-BE and en-IE named a date as en-US,
#: fr-BE and fr-CH as fr-FR, de-BE and de-CH as de-DE (and nl-BE as nl-NL, 2026-10-16);
#: it-CH ([$-810]) and it ([$-10]) as it-IT, 2026-10-17.  Not Spanish: es-MX and es
#: abbreviate otherwise than es-ES (:data:`NAMES` has each), so another Spanish region is
#: not guessed.
LANGUAGES: Mapping[str, str] = MappingProxyType({
    "en": "en-US", "nl": "nl-NL", "de": "de-DE", "fr": "fr-FR", "it": "it-IT",
})

#: The locale id Office stores in a code's ``[$-LCID]`` prefix, per language tag: each
#: accepted and honoured (names in that language) on Office for Mac, 2026-10-16; the
#: regional and language-only ones are what Office for Mac stored for a ``[$-fr-BE]``,
#: ``[$-en]`` ... prefix, 2026-10-17, each showing its language's names.
LCIDS: Mapping[str, int] = MappingProxyType({
    "en-US": 0x409, "en-GB": 0x809, "nl-NL": 0x413, "nl-BE": 0x813,
    "de-DE": 0x407, "fr-FR": 0x40C, "es-ES": 0xC0A, "it-IT": 0x410,
    "en-IE": 0x1809, "fr-BE": 0x80C, "fr-CH": 0x100C,
    "en": 0x9, "nl": 0x13, "de": 0x7, "fr": 0xC,
    # What Office stored for [$-es-MX], [$-it-CH] on re-saving, 2026-10-17; [$-A] and
    # [$-10] honoured (es's and it-IT's names).
    "es-MX": 0x80A, "it-CH": 0x810, "es": 0xA, "it": 0x10,
})


class PatternError(ValueError):
    """A pattern that is not one, or a name token in a locale without measured names: the
    message says what is wrong and what to write."""


#: What a pattern's tokens are, in words, for an error.
TOKENS_TEXT = ("d dd (day), ddd dddd (weekday name), m mm (month), mmm mmmm (month name), "
               "mmmmm (its first letter), yy yyyy (year)")


def _language(locale: Any) -> str:
    """The language tag of ``locale``: its ``language``, or the tag itself; en-US for
    ``None``.  ``_`` reads as ``-`` and case is ignored (``nl_be`` is ``nl-BE``)."""
    tag = locale if isinstance(locale, str) else getattr(locale, "language", None) or "en-US"
    parts = tag.replace("_", "-").split("-")
    return "-".join([parts[0].lower()] + [part.upper() if len(part) == 2 else part
                                          for part in parts[1:]])


def names_tag(locale: Any) -> str | None:
    """The tag in :data:`NAMES` whose names ``locale`` shows: its own tag when listed, else
    its language's (:data:`LANGUAGES`: fr-BE shows fr-FR's); ``None`` for a language with
    no measured names."""
    tag = _language(locale)
    return tag if tag in NAMES else LANGUAGES.get(tag.split("-", 1)[0])


def names_for(locale: Any) -> DateNames | None:
    """The measured names ``locale`` shows (:func:`names_tag`); ``None`` without."""
    tag = names_tag(locale)
    return None if tag is None else NAMES[tag]


def lcid_for(locale: Any) -> int | None:
    """The ``[$-LCID]`` id of ``locale``: its tag's (:data:`LCIDS`), else that of the tag
    whose names it shows (:func:`names_tag`: en-BE takes en-US's ``409``); ``None`` for a
    language with no measured names."""
    tag = _language(locale)
    if tag in LCIDS:
        return LCIDS[tag]
    shown = names_tag(tag)
    return None if shown is None else LCIDS[shown]


def parse(pattern: str) -> list[tuple[str, str]]:
    """``pattern`` as ``(kind, text)`` parts: ``("token", "mmmm")`` (lower case) or
    ``("literal", " ")``.  Raises :class:`PatternError` for an empty pattern, one without a
    token, an unclosed quote, a letter that is no token, or a run that is none
    (``ddddd``, ``yyy``)."""
    if not isinstance(pattern, str) or not pattern.strip():
        raise PatternError(f"a date pattern is text of the tokens {TOKENS_TEXT}, e.g. "
                           "d mmmm yyyy")
    parts: list[tuple[str, str]] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == '"':
            close = pattern.find('"', index + 1)
            if close < 0:
                raise PatternError(f"{pattern!r}: a quote opened at {index + 1} is not "
                                   'closed; quote text as "de"')
            parts.append(("literal", pattern[index + 1:close]))
            index = close + 1
        elif char == "\\":
            parts.append(("literal", pattern[index + 1:index + 2]))
            index += 2
        elif char.lower() in "dmy":
            end = index
            while end < length and pattern[end].lower() == char.lower():
                end += 1
            run = pattern[index:end].lower()
            if run not in TOKENS:
                raise PatternError(f"{pattern!r}: {pattern[index:end]!r} is no date token; "
                                   f"the tokens are {TOKENS_TEXT}")
            parts.append(("token", run))
            index = end
        elif char.isalpha():
            raise PatternError(f"{pattern!r}: {char!r} is no date token; the tokens are "
                               f"{TOKENS_TEXT}, and other text goes in double quotes "
                               '("week")')
        else:
            parts.append(("literal", char))
            index += 1
    if not any(kind == "token" for kind, _ in parts):
        raise PatternError(f"{pattern!r} has no date token; the tokens are {TOKENS_TEXT}, "
                           "e.g. d mmmm yyyy")
    return parts


def uses_names(pattern: str) -> bool:
    """Whether ``pattern`` writes a month or day name (a token of :data:`NAME_TOKENS`)."""
    return any(kind == "token" and text in NAME_TOKENS for kind, text in parse(pattern))


def _check_names(pattern: str, parts: list[tuple[str, str]], locale: Any) -> DateNames | None:
    names = names_for(locale)
    if names is None:
        used = [text for kind, text in parts if kind == "token" and text in NAME_TOKENS]
        if used:
            known = list(LANGUAGES)
            others = [tag for tag in NAMES if tag.split("-", 1)[0] not in LANGUAGES]
            raise PatternError(
                f"{pattern!r}: {used[0]} writes a name, and no month or day names are "
                f"measured for {_language(locale)} (they are for the languages "
                f"{', '.join(known[:-1])} and {known[-1]}, in any region, and for "
                f"{', '.join(others[:-1])} and {others[-1]}); write the date with numbers "
                "(dd/mm/yyyy, d-m-yyyy, yyyy-mm-dd)")
    return names


def format_date(value: "_dt.date", pattern: str, locale: Any = None) -> str:
    """``value`` (a ``date``; a ``datetime``'s date) as text in ``pattern``, with
    ``locale``'s measured names.  Raises :class:`PatternError` for a pattern that is none,
    or a name token in a locale without measured names (see the module's notes)."""
    parts = parse(pattern)
    names = _check_names(pattern, parts, locale)
    out: list[str] = []
    for kind, text in parts:
        if kind == "literal":
            out.append(text)
        elif text == "d":
            out.append(str(value.day))
        elif text == "dd":
            out.append(f"{value.day:02d}")
        elif text == "ddd":
            out.append(names.short_days[value.weekday()])
        elif text == "dddd":
            out.append(names.days[value.weekday()])
        elif text == "m":
            out.append(str(value.month))
        elif text == "mm":
            out.append(f"{value.month:02d}")
        elif text == "mmm":
            out.append(names.short_months[value.month - 1])
        elif text == "mmmm":
            out.append(names.months[value.month - 1])
        elif text == "mmmmm":
            out.append(names.months[value.month - 1][0])
        elif text == "yy":
            out.append(f"{value.year % 100:02d}")
        else:
            out.append(f"{value.year:04d}")
    return "".join(out)


#: Literal characters a kept code has as they are (measured: Office stores ``/``, ``-`` and
#: ``.`` unescaped); a space and a comma are escaped with a backslash, as Office stores
#: them, and any other character too.
_PLAIN = frozenset("/-.")


def format_code(pattern: str, locale: Any = None) -> str:
    """``pattern`` as a number format code, as Office stores one: the locale's
    ``[$-LCID]`` first when it has one (:func:`lcid_for`), the tokens as they are, ``/``,
    ``-`` and ``.`` unescaped, every other literal character after a backslash
    (``[$-413]d\\ mmmm\\ yyyy``), quoted text in quotes.  Raises :class:`PatternError` as
    :func:`format_date` does."""
    parts = parse(pattern)
    _check_names(pattern, parts, locale)
    lcid = lcid_for(locale)
    out = [f"[$-{lcid:X}]"] if lcid is not None else []
    for kind, text in parts:
        if kind == "token":
            out.append(text)
        elif not text:
            continue
        elif len(text) > 1 or text.isalpha():
            out.append('"' + text.replace('"', "") + '"' if '"' not in text
                       else "".join("\\" + char for char in text))
        elif text in _PLAIN:
            out.append(text)
        else:
            out.append("\\" + text)
    return "".join(out)


def is_date_code(code: Any) -> bool:
    """Whether a number format code shows a number as a date or a time: a ``d``, ``m``,
    ``y``, ``h`` or ``s`` outside quotes, brackets and escapes, in any section
    (``[$-413]d\\ mmmm\\ yyyy``, ``m/d/yyyy``, ``h:mm``).  ``General``, ``@``, a number
    code (``#,##0.0``, ``"days" 0``) and anything not text are not."""
    if not isinstance(code, str) or code in ("", "General", "@"):
        return False
    index = 0
    while index < len(code):
        char = code[index]
        if char == '"':
            close = code.find('"', index + 1)
            index = len(code) if close < 0 else close + 1
            continue
        if char == "[":
            close = code.find("]", index)
            inner = code[index + 1:close if close >= 0 else len(code)].lower()
            if inner and inner[0] in "hms" and inner == inner[0] * len(inner):
                return True   # elapsed time: [h], [mm], [ss]
            index = len(code) if close < 0 else close + 1
            continue
        if char in "\\_*":
            index += 2
            continue
        if char.lower() in "dmyhs":
            return True
        index += 1
    return False


# -- serial numbers ------------------------------------------------------------------------------

#: Day 0 of the 1900 date system for every date from 1 March 1900 (ECMA-376 Part 1,
#: 18.17.4, the 1900 date base: serial 61 is 1900-03-01, as the system counts a 29 February
#: 1900 as day 60).
_EPOCH = _dt.datetime(1899, 12, 30)
_FIRST = _dt.date(1900, 3, 1)


def serial(value: "_dt.date") -> "int | float":
    """``value``'s serial number in the 1900 date system: whole days for a ``date``,
    the time of day as the fraction for a ``datetime``.  Raises :class:`ValueError` before
    1 March 1900, where that system counts a day that did not exist."""
    day = value.date() if isinstance(value, _dt.datetime) else value
    if day < _FIRST:
        raise ValueError(f"{day.isoformat()} is before 1 March 1900, which a chart's dates "
                         "do not reach")
    if isinstance(value, _dt.datetime):
        delta = value.replace(tzinfo=None) - _EPOCH
        whole = delta.days + delta.seconds / 86400 + delta.microseconds / 86400e6
        return int(whole) if whole == int(whole) else whole
    return (value - _FIRST).days + 61


def from_serial(number: "int | float") -> "_dt.date | None":
    """The date (a ``datetime`` when it has a time of day) a 1900-system serial names;
    ``None`` before 1 March 1900 (61) or past year 9999."""
    if isinstance(number, bool) or not isinstance(number, (int, float)) or number < 61:
        return None
    try:
        moment = _EPOCH + _dt.timedelta(days=float(number))
    except OverflowError:
        return None
    if moment.year > 9999:
        return None
    return moment.date() if moment.time() == _dt.time() else moment


_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$")


def date_of(value: Any) -> "_dt.date | None":
    """The date ``value`` is or names: a ``date`` or ``datetime`` as it is, ISO 8601 text
    (``2026-03-02``, ``2026-03-02T14:05``); ``None`` for anything else -- a time of day
    alone, a number, a day that does not exist."""
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        match = _ISO_DATE.match(value.strip())
        if match:
            try:
                day = _dt.date(*(int(part) for part in match.groups()[:3]))
                if match.group(4) is None:
                    return day
                return _dt.datetime.combine(day, _dt.time(int(match.group(4)),
                                                          int(match.group(5)),
                                                          int(match.group(6) or 0)))
            except ValueError:
                return None
    return None


class DateCategory(_dt.date):
    """A chart category that is a date, shown in ``format_code`` -- a number format code
    (:func:`format_code`).  A ``date`` in every other way: equal to the plain date,
    picklable.  ``ooxml_edit.charts``' ``add_chart`` keeps such categories as serial
    numbers (:func:`serial`) in that code."""

    format_code: str

    def __new__(cls, value: _dt.date, format_code: str) -> "DateCategory":
        if not isinstance(value, _dt.date):
            raise TypeError(f"a DateCategory is a date, not {type(value).__name__}")
        if not isinstance(format_code, str) or not format_code.strip():
            raise ValueError("a DateCategory's format_code is a number format code")
        made = super().__new__(cls, value.year, value.month, value.day)
        made.format_code = format_code
        return made

    def __reduce__(self) -> Any:
        return (DateCategory, (_dt.date(self.year, self.month, self.day), self.format_code))

    def __repr__(self) -> str:
        return f"DateCategory({self.isoformat()!r}, {self.format_code!r})"
