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
provider scales it down.  An image over :attr:`~.limits.Limits.max_image_edge` on either
side is refused, with what would fit -- its width before anything is drawn, its height as
soon as the tiles' heights are known.
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
    ``width`` in pixels."""

    columns: int
    width: int

    @property
    def total_width(self) -> int:
        return self.columns * self.width + (self.columns + 1) * GAP


def tiling_of(options: Mapping[str, Any] | None, count: int, *, name: str,
              width: int | None = None, max_edge: int = 2576, what: str = "views") -> Tiling:
    """The tool's ``name`` argument (``{"columns", "width"}``, either left out for its
    default) checked for ``count`` tiles of ``what`` -- ``pages``, say: the field an error
    names.  ``width`` is the call's own, taken as the tile's when the options give none.
    Raises :class:`ToolError` naming the bound and what to do."""
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
    tile = (width or DEFAULT_WIDTH) if tile is None else tile
    if not isinstance(tile, int) or not MIN_WIDTH <= tile <= MAX_WIDTH:
        raise ToolError("invalid_arguments", f"a {name}'s tile width ({field}) is "
                        f"{MIN_WIDTH}-{MAX_WIDTH} px, not {tile!r}", field=field)
    result = Tiling(max(1, min(columns, count)), tile)
    if result.total_width > max_edge:
        widest = (max_edge - (result.columns + 1) * GAP) // result.columns
        raise ToolError("limit", f"{result.columns} columns of {tile} px make the {name} "
                        f"{result.total_width} px wide, over the {max_edge} px limit: at "
                        f"{result.columns} columns, a width of at most {widest}", field=field)
    return result


def tiled_size(tiling: Tiling, heights: Sequence[int]) -> tuple[int, int]:
    """The image's pixel size for tiles of these heights (each :attr:`Tiling.width` wide):
    a row is as tall as its tallest tile and the label strip."""
    rows = [heights[start:start + tiling.columns]
            for start in range(0, len(heights), tiling.columns)]
    height = sum(max(row) + LABEL_HEIGHT for row in rows) + (len(rows) + 1) * GAP
    return tiling.total_width, height


def check_size(tiling: Tiling, heights: Sequence[int], *, name: str, max_edge: int = 2576,
               what: str = "views") -> tuple[int, int]:
    """:func:`tiled_size`, refused when the image is over ``max_edge`` tall, naming how many
    tiles fit -- a format whose views' heights are known may call this before rendering."""
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


__all__ = ["MAX_TILES", "Tiling", "check_size", "tiled_size", "tiled_svg", "tiling_of"]
