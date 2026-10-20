"""The workbook behind a chart: now :mod:`ooxml_edit.sheets`, re-exported here.

A chart's embedded ``.xlsx`` is edited with the same lossless cell writer as a whole
workbook, so the writer moved to the optional :mod:`ooxml_edit.sheets` subpackage, with its
history.  This module keeps the old import path, and every chart edit, as they were.
"""

from ..sheets.workbook import (  # noqa: F401  (re-exported)
    CT_SHARED_STRINGS,
    MAX_COLUMN,
    MAX_ROW,
    REL_CALC_CHAIN,
    REL_SHARED_STRINGS,
    REL_TABLE,
    REL_WORKSHEET,
    SML_NS,
    Area,
    Cell,
    CellValue,
    SheetRange,
    Table,
    Workbook,
    Worksheet,
    column_index,
    column_letters,
    number_text,
    parse_formula,
)
