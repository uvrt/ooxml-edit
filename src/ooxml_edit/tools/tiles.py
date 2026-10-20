"""Contact images: many rendered views tiled into one image, each labelled.

``render`` returns at most :attr:`~.limits.Limits.max_images_per_call` images; a reviewer
looking over a whole document wants more at once, and at a lower resolution each.  A
contact image is one image: the tiles in order, row by row, each with its label
(``3 · s:258``, ``page 3``) in a strip under it.  This module is the format-neutral half --
the options, the layout and the SVG that places each tile's PNG at its own pixel size, so
nothing is resampled; a format library renders the tiles at :attr:`Tiling.width` (its
render cache serves repeats) and rasterises :func:`tiled_svg`'s document with its own
renderer, once.  The shared ``render`` tool takes the options as one argument; the caller
passes its name (``name``), which the errors use.

The width is each tile's, not the whole image's: what the model chooses is how legible one
view is.  The image's own width is ``columns * width`` and the gaps; with the defaults (4
columns of 360 px) it is 1,480 px, within the standard tier's 1,568 px long edge, so no
provider scales it down.  An image that would be over :attr:`~.limits.Limits.max_image_edge`
on either side is drawn with narrower tiles instead -- the widest that fit, never under
:data:`MIN_WIDTH` -- and :attr:`Tiling.shrunk` says from what width and why
(:func:`tiling_of` for its width, before anything is drawn; :func:`fit` for its height,
from the tiles' shapes, before drawing where a format knows them and again once the tiles
are drawn).  Only tiles that would not fit even at :data:`MIN_WIDTH` are refused, naming
what would.  A call's own ``width`` standing in for the tile's is taken as at most
:data:`MAX_WIDTH`, said the same way.
"""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from xml.sax.saxutils import escape

from .results import ToolError

#: Views one contact image may hold: 8 rows of 4 at most, and 49 views in two calls.
MAX_TILES = 30
#: Columns, and their default.
MIN_COLUMNS, MAX_COLUMNS, DEFAULT_COLUMNS = 1, 8, 4
#: Each tile's width in pixels, and its default: 160 px keeps a label of 20 characters on
#: one line at :data:`LABEL_SIZE`; 1,280 px is a single render's default.
MIN_WIDTH, MAX_WIDTH, DEFAULT_WIDTH = 160, 1280, 360
#: The gap round every tile, the label strip's height and its text's size, in pixels.
GAP, LABEL_HEIGHT, LABEL_SIZE = 8, 20, 13
#: The background, a tile's frame, the label's text: a white view stays a tile.
BACKGROUND, FRAME, INK = "#d4d4d4", "#8c8c8c", "#1a1a1a"


@dataclass(frozen=True)
class Tiling:
    """The options as checked: ``columns`` (no more than the tiles) and each tile's
    ``width`` in pixels; ``asked`` the width asked for when it was narrowed to fit, and
    ``why``."""

    columns: int
    width: int
    asked: int | None = None
    why: str | None = None

    @property
    def total_width(self) -> int:
        return self.columns * self.width + (self.columns + 1) * GAP

    @property
    def shrunk(self) -> dict[str, Any] | None:
        """``{"from": <width asked>, "why": ...}`` when the tiles are narrower than asked,
        for the result to say so; else ``None``."""
        if self.asked is None:
            return None
        return {"from": self.asked, "why": self.why}

    def narrowed(self, width: int, why: str) -> "Tiling":
        """This tiling at ``width``, remembering the width first asked for."""
        if width == self.width:
            return self
        asked = self.asked if self.asked is not None else self.width
        reason = f"{self.why}; then {why}" if self.why else why
        return Tiling(self.columns, width, asked, reason)


def tiling_of(options: Mapping[str, Any] | None, count: int, *, name: str,
              width: int | None = None, max_edge: int = 2576, what: str = "views") -> Tiling:
    """The tool's ``name`` argument (``{"columns", "width"}``, either left out for its
    default) checked for ``count`` tiles of ``what`` -- ``pages``, say: the field an error
    names.  ``width`` is the call's own, taken as the tile's when the options give none
    (at most :data:`MAX_WIDTH`).  Tiles too wide for ``max_edge`` side by side are
    narrowed to the widest that fit (:attr:`Tiling.shrunk`).  Raises :class:`ToolError`
    naming the bound and what to do."""
    options = dict(options or {})
    if count > MAX_TILES:
        raise ToolError("limit", f"one {name} holds at most {MAX_TILES} {what}, not {count}: "
                        f"render the first {MAX_TILES}, and the rest in another call",
                        field=what)
    columns = options.get("columns")
    columns = DEFAULT_COLUMNS if columns is None else columns
    if not isinstance(columns, int) or not MIN_COLUMNS <= columns <= MAX_COLUMNS:
        raise ToolError("invalid_arguments", f"{name}.columns is {MIN_COLUMNS}-{MAX_COLUMNS}, "
                        f"not {columns!r}", field=f"{name}.columns")
    tile = options.get("width")
    field = f"{name}.width" if tile is not None or not width else "width"
    asked = why = None
    if tile is None and isinstance(width, int) and not isinstance(width, bool) \
            and width > MAX_WIDTH:
        asked, why = width, f"width {width} is over a tile's {MAX_WIDTH} px"
        tile = MAX_WIDTH
    tile = (width or DEFAULT_WIDTH) if tile is None else tile
    if not isinstance(tile, int) or not MIN_WIDTH <= tile <= MAX_WIDTH:
        raise ToolError("invalid_arguments", f"a {name}'s tile width ({field}) is "
                        f"{MIN_WIDTH}-{MAX_WIDTH} px, not {tile!r}", field=field)
    result = Tiling(max(1, min(columns, count)), tile, asked, why)
    if result.total_width > max_edge:
        widest = (max_edge - (result.columns + 1) * GAP) // result.columns
        if widest < MIN_WIDTH:
            most = max(1, (max_edge - GAP) // (MIN_WIDTH + GAP))
            raise ToolError("limit", f"{result.columns} columns make the {name} over the "
                            f"{max_edge} px limit even at a tile width of {MIN_WIDTH}: at most "
                            f"{most} columns", field=f"{name}.columns")
        result = result.narrowed(widest, f"{result.columns} columns of {result.width} px "
                                 f"make the {name} {result.total_width} px wide, over the "
                                 f"{max_edge} px limit")
    return result


def fit(tiling: Tiling, ratios: Sequence[float], *, name: str, max_edge: int = 2576,
        what: str = "views") -> Tiling:
    """``tiling`` with its tiles narrowed to the widest whose image is at most ``max_edge``
    tall, for tiles of these shapes (each its height over its width; a tile ``w`` px wide
    is ``ceil(w * ratio)`` tall, as the rasteriser sizes it); ``tiling`` itself when it
    fits.  Refused (:class:`ToolError`) when even :data:`MIN_WIDTH` is too tall, naming how
    many fit there."""
    if not ratios:
        return tiling
    heights = lambda width: [math.ceil(width * ratio - 1e-9) for ratio in ratios]  # noqa: E731
    if _tall(tiling.columns, heights(tiling.width)) <= max_edge:
        return tiling
    low, high = MIN_WIDTH, tiling.width - 1         # the widest that fits, by halving
    if _tall(tiling.columns, heights(low)) > max_edge:
        row = max(heights(low)) + LABEL_HEIGHT + GAP
        rows = max(1, (max_edge - GAP) // row)
        raise ToolError("limit", f"{len(ratios)} {what} in {tiling.columns} column(s) make the "
                        f"{name} over the {max_edge} px limit even at a tile width of "
                        f"{MIN_WIDTH}: at most {rows * tiling.columns} {what} at these "
                        f"columns; or more columns", field=what)
    while low < high:
        middle = (low + high + 1) // 2
        if _tall(tiling.columns, heights(middle)) <= max_edge:
            low = middle
        else:
            high = middle - 1
    total = _tall(tiling.columns, heights(tiling.width))
    return tiling.narrowed(low, f"{len(ratios)} {what} in {tiling.columns} column(s) of "
                           f"{tiling.width} px make the {name} {total} px tall, over the "
                           f"{max_edge} px limit")


def _tall(columns: int, heights: Sequence[int]) -> int:
    """The image's height for tiles of these heights in ``columns`` columns."""
    rows = [heights[start:start + columns] for start in range(0, len(heights), columns)]
    return sum(max(row) + LABEL_HEIGHT for row in rows) + (len(rows) + 1) * GAP


def narrowed_note(tiling: Tiling) -> str:
    """What a result's summary adds for tiles narrower than asked: `` (narrowed from 1280
    px: ...)``; empty otherwise."""
    if tiling.asked is None:
        return ""
    return f" (narrowed from {tiling.asked} px: {tiling.why})"


def tiled_size(tiling: Tiling, heights: Sequence[int]) -> tuple[int, int]:
    """The image's pixel size for tiles of these heights (each :attr:`Tiling.width` wide):
    a row is as tall as its tallest tile and the label strip."""
    return tiling.total_width, _tall(tiling.columns, heights)


def check_size(tiling: Tiling, heights: Sequence[int], *, name: str, max_edge: int = 2576,
               what: str = "views") -> tuple[int, int]:
    """:func:`tiled_size`, refused when the image is over ``max_edge`` tall, naming how many
    tiles fit -- the last check, once the tiles are drawn (:func:`fit` narrows them
    first)."""
    width, height = tiled_size(tiling, heights)
    if height > max_edge:
        row = max(heights) + LABEL_HEIGHT + GAP
        rows = max(1, (max_edge - GAP) // row)
        raise ToolError("limit", f"{len(heights)} {what} in {tiling.columns} column(s) make the "
                        f"{name} {width}x{height} px, over the {max_edge} px limit: at most "
                        f"{rows * tiling.columns} {what} at this width and columns; or more "
                        f"columns, or a smaller {name}.width", field=what)
    return width, height


def tiled_svg(tiles: Sequence[tuple[bytes, int, int, str]], tiling: Tiling) -> tuple[str, int, int]:
    """One SVG document of the tiles -- ``(png, width, height, label)`` each, in order -- and
    its pixel size: each PNG at its own size on a whole pixel (drawn, not resampled), framed,
    its label under it, cut to the tile's width."""
    width, height = tiled_size(tiling, [h for _, _, h, _ in tiles])
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}">',
             f'<rect width="{width}" height="{height}" fill="{BACKGROUND}"/>']
    top = GAP
    for start in range(0, len(tiles), tiling.columns):
        row = tiles[start:start + tiling.columns]
        tallest = max(h for _, _, h, _ in row)
        for column, (png, w, h, label) in enumerate(row):
            left = GAP + column * (tiling.width + GAP)
            data = base64.b64encode(png).decode("ascii")
            parts.append(f'<rect x="{left - 0.5}" y="{top - 0.5}" width="{w + 1}" '
                         f'height="{h + 1}" fill="none" stroke="{FRAME}"/>')
            parts.append(f'<image x="{left}" y="{top}" width="{w}" height="{h}" '
                         f'href="data:image/png;base64,{data}"/>')
            parts.append(f'<text x="{left + 2}" y="{top + tallest + LABEL_SIZE + 3}" '
                         f'font-family="sans-serif" font-size="{LABEL_SIZE}" fill="{INK}">'
                         f'{escape(_fit(label, tiling.width))}</text>')
        top += tallest + LABEL_HEIGHT + GAP
    parts.append("</svg>")
    return "".join(parts), width, height


def _fit(label: str, width: int) -> str:
    """``label`` cut to what fits ``width`` px at :data:`LABEL_SIZE` (about 0.55 em a
    character), its end an ellipsis."""
    room = max(4, math.floor((width - 4) / (LABEL_SIZE * 0.55)))
    return label if len(label) <= room else label[:room - 1].rstrip() + "…"


__all__ = ["MAX_TILES", "Tiling", "check_size", "fit", "narrowed_note", "tiled_size", "tiled_svg", "tiling_of"]
