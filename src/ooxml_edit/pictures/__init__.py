"""Pictures as DrawingML stores them, the same in every format: an SVG picture and its
recolouring (:mod:`.svg`).  Optional, as :mod:`ooxml_edit.charts` is: the core never
imports it."""

from __future__ import annotations

from .svg import (A_NS, ASVG_NS, R_NS, SVG_BLIP_EXT_URI, SVG_CONTENT_TYPE, SVG_LIMIT, SVG_NS,
                  THEME_SLOTS, SvgError, attach, drop, looks_like_svg, native_size, parse,
                  recolor, stroke_units, svg_blip, theme_class)

__all__ = ["ASVG_NS", "A_NS", "R_NS", "SVG_BLIP_EXT_URI", "SVG_CONTENT_TYPE", "SVG_LIMIT", "SVG_NS",
           "SvgError", "THEME_SLOTS", "attach", "drop", "looks_like_svg", "native_size", "parse",
           "recolor", "stroke_units", "svg_blip", "theme_class"]
