"""The charts subpackage's exceptions, in a module of their own so every part can raise them."""

from __future__ import annotations


class ChartDataError(ValueError):
    """A chart edit that cannot be made faithfully -- refused before anything changes."""


class ChartDataWarning(UserWarning):
    """A chart edit reached the chart's cache but not its workbook (none to reach)."""


class FormatError(ChartDataError):
    """A formatting request that cannot be written: a bad colour, marker, size or axis."""


__all__ = ["ChartDataError", "ChartDataWarning", "FormatError"]
