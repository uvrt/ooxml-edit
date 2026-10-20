"""A small Markdown subset, read and written: emphasis, strikethrough, code spans, escapes,
line breaks and lists -- runs of text with their marks, line by line.

What a format writes as formatted runs (a cell's rich text, a run of a paragraph) and reads
back, without a Markdown library: lxml is this package's only dependency.  The subset is
CommonMark's (0.31.2) for what it has, and GFM's strikethrough as markdown-it-py reads it
(the parser the format libraries use for whole documents), so that they agree on it:

* ``**strong**`` (bold), ``*em*`` and ``_em_`` (italic), ``***both***``; CommonMark's
  delimiter-run rules decide -- left- and right-flanking runs, an underscore inside a word
  is no emphasis (``snake_case_name``), the rule of three -- and a delimiter that finds no
  partner is text;
* ``~~strike~~``: tildes in pairs (one tilde is text; an odd run's first tilde is text),
  flanking as an asterisk's;
* ``\\*``: a backslash before ASCII punctuation is that character as text; before anything
  else it is a backslash;
* ``` `code` ```: a code span, its content as text with no emphasis read in it (a run with
  ``code`` set, for a format that has a code font; the backticks are markup), CommonMark's
  rules for the backtick string and the one space stripped at each end.

Everything else is text, as written: links and images (``[a](b)``), raw HTML, autolinks,
entities (``&amp;`` stays five characters), headings, block quotes, thematic breaks, code
blocks, tables, hard breaks.

**Lines** (:func:`parse`) -- deliberately not CommonMark's paragraphs: each line given is
a line of its own (CommonMark joins a paragraph's lines with a space).  Spaces at either
end of a line go; an empty line between lines is kept as one (:attr:`Line.kind`
``"blank"``), however many are written, and empty lines at the start and the end are
dropped.  Emphasis does not cross a line's end.  List items are CommonMark's, for this
subset: a marker -- ``-``, ``*``, ``+``, or a number of up to nine digits and ``.`` or
``)`` -- then a space (or the line's end), up to three spaces in when no list is open; an
item's content starts after the marker and the one to four spaces that follow it (one when
there are more), and a line indented at least that far is inside it: a nested item, or a
line more of it (``"more"``).  A line indented less ends the lists, right after an item's
line too: with no paragraphs to continue, there is no lazy continuation line.
A list is its items of one level, one kind and one marker (``-`` and ``*`` are two
lists, so are ``1.`` and ``1)``); an ordered list is numbered from its first item's number
on, whatever numbers the later items were written with, as CommonMark renders it.  An
ordered item interrupts a line of text only when it is ``1``, and an empty item never
does (otherwise the line is text, as in CommonMark).  A line of three or more ``-``,
``*`` or ``_`` (a thematic break) is text.  Tabs before a line's text count to the next
multiple of four columns.

**Writing** (:func:`to_markdown`, :func:`inline_markdown`, :func:`escape`): lines of runs as
Markdown that :func:`parse` reads back as the same lines -- text escaped
(:data:`ESCAPED`, and a line's start where it would read as a list item), whitespace
moved out of marked text so no delimiter touches it, marks opened together nested by how
long they last.  The result is parsed back; where CommonMark's flanking rules would read
it otherwise (``a**(b)**c``), italic is written with underscores, then strikethrough and
then emphasis are dropped from that line -- the text never changes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Iterable, Sequence

#: The characters :func:`escape` puts a backslash before: what could start markup here or
#: in the format libraries' fuller dialect (the escape they write with).
ESCAPED = frozenset("\\`*_[]<~")

#: ASCII punctuation, which a backslash escapes (CommonMark 2.4).
ASCII_PUNCTUATION = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")

#: Line kinds (:attr:`Line.kind`).
KINDS = ("text", "bullet", "number", "more", "blank")

# markdown-it-py's whitespace (common.utils.isWhiteSpace): Zs, tab, line feed, vertical
# tab, form feed, carriage return.
_WHITESPACE = frozenset("\t\n\x0b\x0c\r \xa0   　") | frozenset(
    chr(code) for code in range(0x2000, 0x200B))
_ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")
_ITEM = re.compile(r"([-+*]|[0-9]{1,9}[.)])(?=[ \t]|$)")
_BREAK = re.compile(r"(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})")
#: What would start a list item at a line's start, or a heading or a block quote in
#: the format libraries' fuller dialect.
_LINE_START = re.compile(r"[+*-](?=[ \t]|$)|>|#{1,6}(?=[ \t]|$)"
                         r"|([0-9]{1,9})([.)])(?=[ \t]|$)")


@dataclass(frozen=True)
class Run:
    """Text with its marks.  ``code``: from a code span (what a format with a code font
    shows in it; a format without one shows the text)."""

    text: str
    bold: bool = False
    italic: bool = False
    strike: bool = False
    code: bool = False

    @property
    def marks(self) -> tuple[bool, bool, bool, bool]:
        return (self.bold, self.italic, self.strike, self.code)

    @property
    def formatted(self) -> bool:
        """Bold, italic or struck through."""
        return self.bold or self.italic or self.strike


@dataclass(frozen=True)
class Line:
    """One line: its runs, and what it is (:data:`KINDS`) -- ``text``, a list item
    (``bullet``, or ``number`` with its :attr:`number`, renumbered, and ``delimiter``,
    ``.`` or ``)``), ``more`` (a further line of the item at :attr:`depth`) or ``blank``.
    ``depth``: an item's nesting, from 0 for a list's own item; ``0`` for text."""

    runs: tuple[Run, ...]
    kind: str = "text"
    depth: int = 0
    number: int | None = None
    delimiter: str | None = None

    @property
    def text(self) -> str:
        return plain(self.runs)


def plain(runs: Iterable[Run]) -> str:
    """The characters of ``runs``, marks dropped."""
    return "".join(run.text for run in runs)


def merge(runs: Iterable[Run]) -> tuple[Run, ...]:
    """``runs`` with neighbours alike in marks joined and empty ones dropped."""
    out: list[Run] = []
    for run in runs:
        if not run.text:
            continue
        if out and out[-1].marks == run.marks:
            out[-1] = replace(out[-1], text=out[-1].text + run.text)
        else:
            out.append(run)
    return tuple(out)


# -- reading: inline ---------------------------------------------------------------------


def _punctuation(char: str) -> bool:
    return char in ASCII_PUNCTUATION or unicodedata.category(char).startswith(("P", "S"))


@dataclass
class _Delimiter:
    char: str
    node: int           # its node's index
    count: int          # characters not yet used
    length: int         # the run's length, for the rule of three (0: none, as for ~~)
    opens: bool
    closes: bool
    run: int = 0        # the delimiter run it is of: a pair of tildes never closes its own


def parse_inline(text: str) -> tuple[Run, ...]:
    """One line's runs (see the module): emphasis, strikethrough, code spans and escapes
    read, everything else text.  A line end inside is text like any other character."""
    nodes: list[list] = []          # [text, code]; a delimiter's node holds its characters
    delimiters: list[_Delimiter] = []
    n = len(text)
    k = 0
    pending: list[str] = []
    #: The pairs of each tilde run of odd length, whose one tilde more is text.
    odd: list[list[int]] = []

    def flush() -> None:
        if pending:
            nodes.append(["".join(pending), False])
            pending.clear()

    while k < n:
        char = text[k]
        if char == "\\" and k + 1 < n and text[k + 1] in ASCII_PUNCTUATION:
            pending.append(text[k + 1])
            k += 2
            continue
        if char == "`":
            end = k
            while end < n and text[end] == "`":
                end += 1
            width = end - k
            close = _closing_backticks(text, end, width)
            if close is None:
                pending.append(text[k:end])
                k = end
                continue
            content = text[end:close].replace("\n", " ")
            if len(content) > 1 and content[0] == " " and content[-1] == " " \
                    and content.strip(" "):
                content = content[1:-1]
            flush()
            nodes.append([content, True])
            k = close + width
            continue
        if char in "*_~":
            end = k
            while end < n and text[end] == char:
                end += 1
            length = end - k
            before = text[k - 1] if k else " "
            after = text[end] if end < n else " "
            left = after not in _WHITESPACE and (
                not _punctuation(after) or before in _WHITESPACE or _punctuation(before))
            right = before not in _WHITESPACE and (
                not _punctuation(before) or after in _WHITESPACE or _punctuation(after))
            if char == "_":
                opens = left and (not right or _punctuation(before))
                closes = right and (not left or _punctuation(after))
            else:
                opens, closes = left, right
            if char == "~":
                if length < 2:
                    pending.append("~")
                    k = end
                    continue
                flush()
                pairs = []
                for _ in range(length // 2):
                    nodes.append(["~~", False])
                    pairs.append(len(nodes) - 1)
                    delimiters.append(_Delimiter("~", len(nodes) - 1, 2, 0, opens, closes, k))
                if length % 2:
                    odd.append(pairs)
            else:
                flush()
                nodes.append([char * length, False])
                delimiters.append(_Delimiter(char, len(nodes) - 1, length, length, opens,
                                             closes))
            k = end
            continue
        pending.append(char)
        k += 1
    flush()

    spans = _match(delimiters, nodes)
    used = {d.node: d.count for d in delimiters}
    for index, count in used.items():
        nodes[index][0] = nodes[index][0][:count]
    # An odd tilde run's one tilde more is text at its start, or after the pairs of it
    # that close a strikethrough (markdown-it's strikethrough.postProcess).
    closers = {last for _, last, _ in spans}
    for pairs in odd:
        p = 0
        while p < len(pairs) and pairs[p] in closers:
            p += 1
        if p:
            nodes[pairs[p - 1]][0] += "~"
        else:
            nodes[pairs[0]][0] = "~" + nodes[pairs[0]][0]
    runs: list[Run] = []
    for index, (content, code) in enumerate(nodes):
        bold = italic = strike = False
        for first, last, kind in spans:
            if first < index < last:
                bold |= kind == "strong"
                italic |= kind == "em"
                strike |= kind == "strike"
        runs.append(Run(content, bold, italic, strike, code))
    return merge(runs)


def _closing_backticks(text: str, start: int, width: int) -> int | None:
    """Where the backtick string of ``width`` that closes a code span opened before
    ``start`` begins, or ``None``."""
    k = start
    n = len(text)
    while k < n:
        if text[k] != "`":
            k += 1
            continue
        end = k
        while end < n and text[end] == "`":
            end += 1
        if end - k == width:
            return k
        k = end
    return None


def _match(delimiters: list[_Delimiter], nodes: list) -> list[tuple[int, int, str]]:
    """CommonMark's "process emphasis" (6.2, the delimiter stack): the pairs matched, as
    ``(opener node, closer node, kind)``; each delimiter's ``count`` left as the characters
    that stay text."""
    spans: list[tuple[int, int, str]] = []
    stack = list(delimiters)
    position = 0
    while position < len(stack):
        closer = stack[position]
        if not closer.closes or closer.count == 0:
            position += 1
            continue
        found = None
        for back in range(position - 1, -1, -1):
            opener = stack[back]
            if opener.char != closer.char or not opener.opens or opener.count == 0 \
                    or (closer.char == "~" and opener.run == closer.run):
                continue
            if closer.char != "~" and (opener.closes or closer.opens) \
                    and (opener.length + closer.length) % 3 == 0 \
                    and not (opener.length % 3 == 0 and closer.length % 3 == 0):
                continue
            found = back
            break
        if found is None:
            if not closer.opens:
                stack.pop(position)
            else:
                position += 1
            continue
        opener = stack[found]
        if closer.char == "~":
            use, kind = 2, "strike"
        else:
            use = 2 if opener.count >= 2 and closer.count >= 2 else 1
            kind = "strong" if use == 2 else "em"
        opener.count -= use
        closer.count -= use
        spans.append((opener.node, closer.node, kind))
        del stack[found + 1:position]       # the delimiters between are text now
        position = found + 1
        if opener.count == 0:
            stack.pop(found)
            position -= 1
        if closer.count == 0:
            stack.pop(position)
    return spans


# -- reading: lines ----------------------------------------------------------------------


@dataclass
class _Open:
    """An open list item: where its content starts, its depth, its list."""

    content: int
    depth: int


def parse(text: str) -> tuple[Line, ...]:
    """``text``'s lines (see the module): each line its own, list items recognised and
    numbered, each line's runs read by :func:`parse_inline`."""
    lines: list[Line] = []
    open_items: list[_Open] = []
    #: Per depth, the list the last item there was in: (kind, marker, next number).
    lists: dict[int, tuple[str, str, int]] = {}
    previous = "blank"       # what the line before was: blank, text or item
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        expanded = _expand(raw)
        stripped = expanded.lstrip(" ")
        if not stripped.strip():
            if lines and lines[-1].kind != "blank":
                lines.append(Line((), "blank"))
            previous = "blank"
            continue
        indent = len(expanded) - len(stripped)
        item = _item(stripped, indent, open_items, previous)
        if item is not None:
            marker, gap = item
            while open_items and open_items[-1].content > indent:
                open_items.pop()
            depth = len(open_items)
            for deeper in [d for d in lists if d > depth]:
                del lists[deeper]
            kind = "number" if marker[-1] in ".)" else "bullet"
            symbol = marker[-1] if kind == "number" else marker
            number = int(marker[:-1]) if kind == "number" else None
            current = lists.get(depth)
            if current is not None and current[:2] == (kind, symbol) and kind == "number":
                number = current[2]
            if kind == "number":
                lists[depth] = (kind, symbol, number + 1)
            else:
                lists[depth] = (kind, symbol, 0)
            content = stripped[len(marker):]
            spaces = len(content) - len(content.lstrip(" "))
            width = 1 if spaces > 4 or not content.strip() else spaces
            open_items.append(_Open(indent + len(marker) + width, depth))
            lines.append(Line(parse_inline(content.strip(" \t")), kind, depth, number,
                              symbol if kind == "number" else None))
            previous = "item"
            continue
        if any(o.content <= indent for o in open_items):
            while open_items[-1].content > indent:
                open_items.pop()
            for deeper in [d for d in lists if d > open_items[-1].depth]:
                del lists[deeper]
            lines.append(Line(parse_inline(stripped.rstrip(" \t")), "more",
                              open_items[-1].depth))
            previous = "item"
        else:
            open_items.clear()
            lists.clear()
            lines.append(Line(parse_inline(stripped.rstrip(" \t"))))
            previous = "text"
    while lines and lines[-1].kind == "blank":
        lines.pop()
    return tuple(lines)


def _expand(line: str) -> str:
    """The line with the tabs before its text counted to the next multiple of four."""
    out = []
    column = 0
    for k, char in enumerate(line):
        if char == " ":
            out.append(" ")
            column += 1
        elif char == "\t":
            stop = 4 - column % 4
            out.append(" " * stop)
            column += stop
        else:
            return "".join(out) + line[k:]
    return "".join(out)


def _item(stripped: str, indent: int, open_items: list[_Open],
          previous: str) -> tuple[str, int] | None:
    """The list marker a line starts with, when it starts an item (see the module)."""
    if _BREAK.fullmatch(stripped.rstrip()):
        return None
    match = _ITEM.match(stripped)
    if match is None:
        return None
    marker = match.group(1)
    # Up to three spaces before a marker, from the content column of the item it is in.
    base = max((o.content for o in open_items if o.content <= indent), default=0)
    if indent - base > 3:
        return None
    if previous == "text":
        rest = stripped[len(marker):]
        if not rest.strip():
            return None
        if marker[-1] in ".)" and int(marker[:-1]) != 1:
            return None
    return marker, indent


# -- writing -------------------------------------------------------------------------------


def escape(text: str) -> str:
    """``text`` as Markdown that reads back as itself, inline: a backslash before each of
    :data:`ESCAPED`, and before an ``&`` that starts an entity (which the fuller
    dialect reads)."""
    out = []
    for k, char in enumerate(text):
        if char in ESCAPED:
            out.append("\\" + char)
        elif char == "&" and _ENTITY.match(text, k):
            out.append("\\&")
        else:
            out.append(char)
    return "".join(out)


def _escape_line_start(text: str) -> str:
    """A line's Markdown with a marker at its start escaped, so that it is text."""
    match = _LINE_START.match(text)
    if match is None:
        return text
    if match.group(1) is None:
        return "\\" + text
    return match.group(1) + "\\" + match.group(2) + text[match.end():]


def inline_markdown(runs: Sequence[Run], *, line_start: bool = True) -> str:
    """``runs`` as one line of Markdown that :func:`parse_inline` reads back as ``runs``
    (merged, a space at a mark's end unmarked: see the module), marks dropped where the
    dialect cannot place them;
    ``line_start``: escape a list marker at its start.  A line end in the text is written
    as it is."""
    runs = list(merge(runs))
    attempts = ((), ("underscore",), ("strike",), ("strike", "bold", "italic"))
    text = ""
    for dropped in attempts:
        shown = runs
        if "strike" in dropped:
            shown = [replace(r, strike=False) for r in shown]
        if "bold" in dropped:
            shown = [replace(r, bold=False, italic=False) for r in shown]
        text = _write(shown, underscore="underscore" in dropped)
        if line_start:
            text = _escape_line_start(text)
        if not any(r.formatted for r in shown) or parse_inline(text) == merge(_spaced(shown)):
            return text
    return text


_DELIMITERS = {"bold": "**", "italic": "*", "strike": "~~"}
_MARKS = ("strike", "bold", "italic")


def _flags(run: Run) -> frozenset[str]:
    return frozenset(mark for mark in _MARKS if getattr(run, mark))


def _write(runs: list[Run], *, underscore: bool) -> str:
    delimiters = dict(_DELIMITERS, italic="_" if underscore else "*")
    items = _spaced(runs)
    out: list[str] = []
    opened: list[str] = []
    for k, run in enumerate(items):
        marks = _flags(run)
        while opened and not set(opened) <= marks:
            out.append(delimiters[opened.pop()])
        for mark in sorted(marks - set(opened), key=_lasting(items, k)):
            out.append(delimiters[mark])
            opened.append(mark)
        out.append(_code(run.text) if run.code else escape(run.text))
    while opened:
        out.append(delimiters[opened.pop()])
    return "".join(out)


def _lasting(items: list[Run], k: int):
    """Marks opened together nest by how long they last: the longest outermost."""
    def key(mark: str) -> tuple:
        n = k
        while n < len(items) and getattr(items[n], mark):
            n += 1
        return (-n, _MARKS.index(mark))
    return key


def _spaced(runs: list[Run]) -> list[Run]:
    """Whitespace moved out of marked text, so that no delimiter touches it."""
    out: list[Run] = []
    for run in runs:
        if not run.formatted:
            out.append(run)
            continue
        core = run.text.strip(" ")
        if not core:
            out.append(Run(run.text, code=run.code))
            continue
        lead = run.text[:len(run.text) - len(run.text.lstrip(" "))]
        trail = run.text[len(run.text.rstrip(" ")):]
        if lead:
            out.append(Run(lead, code=run.code))
        out.append(replace(run, text=core))
        if trail:
            out.append(Run(trail, code=run.code))
    return out


def _code(text: str) -> str:
    """A code span holding ``text``: a backtick string longer than any in it."""
    longest = max((len(m) for m in re.findall("`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = text.startswith("`") or text.endswith("`") or (
        text.startswith(" ") and text.endswith(" ") and text.strip(" ") != "")
    return f"{fence} {text} {fence}" if pad else f"{fence}{text}{fence}"


def to_markdown(lines: Sequence[Line], *, fallback: "Sequence[Line] | None" = None) -> str:
    """``lines`` as Markdown that :func:`parse` reads back as them (see the module), as
    far as Markdown holds them: an item's own number and delimiter are written (``-`` for
    every bullet), nested under its parent's content.  When that would not read back so --
    a list numbered other than one after another, a level skipped, an item that cannot
    interrupt the text before it -- the lines are written as text instead: ``fallback``
    (the same characters as text lines, for a caller that shows markers its own way), or
    each item's marker written before it.  Spaces at a line's ends and a second empty
    line in a row have no Markdown, and are not kept."""
    written = _structured(lines)
    if parse(written) == _normalised(lines):
        return written
    if fallback is None:
        fallback = [Line((Run(_marker(line) + " "),) + line.runs)
                    if line.kind in ("bullet", "number") else line for line in lines]
    return "\n".join("" if line.kind == "blank" else inline_markdown(line.runs)
                     for line in _normalised([Line(l.runs, "blank" if l.kind == "blank"
                                                   else "text") for l in fallback]))


def _marker(line: Line) -> str:
    return "-" if line.kind == "bullet" else f"{line.number}{line.delimiter or '.'}"


def _structured(lines: Sequence[Line]) -> str:
    out: list[str] = []
    columns: list[int] = []         # per depth, where the open item's content starts
    for line in lines:
        if line.kind == "blank":
            out.append("")
            continue
        if line.kind in ("bullet", "number"):
            depth = min(line.depth, len(columns))
            del columns[depth:]
            indent = columns[-1] if columns else 0
            marker = _marker(line)
            columns.append(indent + len(marker) + 1)
            body = inline_markdown(line.runs, line_start=False)
            out.append(" " * indent + marker + (" " + body if body else ""))
        elif line.kind == "more" and columns:
            depth = min(line.depth, len(columns) - 1)
            del columns[depth + 1:]
            out.append(" " * columns[depth] + inline_markdown(line.runs))
        else:
            columns.clear()
            out.append(inline_markdown(line.runs))
    return "\n".join(out)


def _normalised(lines: Sequence[Line]) -> tuple[Line, ...]:
    """``lines`` as :func:`parse` would give them back: runs merged, spaces at a line's
    ends dropped, a text line of nothing an empty one, empty lines in a row one and none
    at either end."""
    out: list[Line] = []
    for line in lines:
        runs = list(merge(_spaced(list(merge(line.runs)))))
        if runs:
            runs[0] = replace(runs[0], text=runs[0].text.lstrip(" \t"))
            runs[-1] = replace(runs[-1], text=runs[-1].text.rstrip(" \t"))
        runs = merge(runs)
        if line.kind == "blank" or (not runs and line.kind in ("text", "more")):
            if out and out[-1].kind != "blank":
                out.append(Line((), "blank"))
            continue
        out.append(replace(line, runs=runs))
    while out and out[-1].kind == "blank":
        out.pop()
    return tuple(out)


__all__ = ["ASCII_PUNCTUATION", "ESCAPED", "KINDS", "Line", "Run", "escape",
           "inline_markdown", "merge", "parse", "parse_inline", "plain", "to_markdown"]
