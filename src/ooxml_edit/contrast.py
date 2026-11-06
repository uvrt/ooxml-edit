"""WCAG 2's contrast ratio of two colours, and the text colour that reads on a fill.

One reading for every format: a library measures text against what is drawn behind it with
it (``check``'s ``contrast``), and a scale's steps (:mod:`ooxml_edit.tools.scales`) choose
white or black text for each step's fill with it.

* :func:`luminance` -- WCAG 2's relative luminance of an sRGB colour (each channel
  linearised: ``c / 12.92`` up to 0.04045, else ``((c + 0.055) / 1.055) ** 2.4``; then
  ``0.2126 R + 0.7152 G + 0.0722 B``);
* :func:`ratio` -- ``(L1 + 0.05) / (L2 + 0.05)``, the lighter over the darker: 1 to 21;
* :func:`readable_text` -- white or black, whichever has the higher ratio with the fill; a
  tie goes to black.

Colours are ``RRGGBB`` or ``#RRGGBB``, either case::

    from ooxml_edit.contrast import ratio, readable_text

    ratio("FFFFFF", "000000")          # 21.0
    readable_text("2E7D32")            # ('FFFFFF', 5.13)
"""

from __future__ import annotations

import re

#: White and black, as a step's ``text`` names them.
WHITE, BLACK = "FFFFFF", "000000"

#: WCAG 2's minimum contrast for text (AA); 3:1 for large text.
TEXT_MINIMUM = 4.5

_HEX = re.compile(r"#?([0-9A-Fa-f]{6})")


def hex_colour(value: str) -> str | None:
    """``value`` as ``RRGGBB`` in capitals, ``None`` when it is not six hex digits (a
    leading ``#`` allowed)."""
    match = _HEX.fullmatch(value.strip()) if isinstance(value, str) else None
    return match.group(1).upper() if match else None


def luminance(colour: str) -> float:
    """WCAG 2's relative luminance of ``RRGGBB`` (or ``#RRGGBB``), 0 to 1."""
    value = colour.lstrip("#")
    channels = []
    for index in (0, 2, 4):
        c = int(value[index:index + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def ratio(first: str, second: str) -> float:
    """WCAG 2's contrast ratio of two colours, 1 to 21."""
    a, b = luminance(first), luminance(second)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def readable_text(fill: str) -> tuple[str, float]:
    """The text colour for ``fill``: white or black, whichever has the higher WCAG 2 ratio
    with it (a tie goes to black), and that ratio rounded to two places."""
    white, black = ratio(WHITE, fill), ratio(BLACK, fill)
    return (WHITE, round(white, 2)) if white > black else (BLACK, round(black, 2))


__all__ = ["BLACK", "TEXT_MINIMUM", "WHITE", "hex_colour", "luminance", "ratio", "readable_text"]
