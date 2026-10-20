"""SpreadsheetML worksheets, edited cell by cell and losslessly.

An optional part of ooxml-edit, imported explicitly (``import ooxml_edit.sheets``): the core
never imports it, it imports nothing but the core and lxml, and importing it registers
nothing.  :mod:`ooxml_edit.charts` writes a chart's embedded workbook through it (and keeps
``charts.workbook`` as the old name); a workbook edited for its own sake uses it directly.

* :mod:`.workbook` -- :class:`Workbook` and :class:`Worksheet`: cells written by position
  (:meth:`Worksheet.write`: text, numbers, booleans, dates, :class:`Formula`, blanks),
  shared strings indexed once, rows with or without ``r``, tables, the calculation chain
  and ``calcPr``; shared formula groups (:meth:`Worksheet.shared_group`) and a master taken
  out of its group with its members' formulas kept (:meth:`Worksheet.release_shared`); references (:class:`Cell`, :class:`Area`, :func:`parse_formula`);
* :mod:`.styles` -- :class:`Styles`: a cell format's number format, whether it shows a
  date, and the format a value needs, reused or appended;
* :mod:`.footprint` -- :func:`outside_footprint`: what an edit may change, written down and
  checked, for tests and for a caller that verifies a saved package.
"""

from .workbook import (CT_SHARED_STRINGS, MAX_COLUMN, MAX_ROW, MAX_TEXT, REL_CALC_CHAIN,
                       REL_SHARED_STRINGS, REL_STYLES, REL_TABLE, REL_WORKSHEET, SML_NS, Area,
                       Cell, Content, Formula, SharedFormulaError, SharedGroup, SheetRange,
                       Table, Workbook, Worksheet, column_index, column_letters, date_serial,
                       number_text, parse_formula)
from .styles import BUILTIN_DATE_IDS, LANGUAGE_IDS, NumberFormat, Styles
from .footprint import outside_footprint

__all__ = [
    "CT_SHARED_STRINGS", "MAX_COLUMN", "MAX_ROW", "MAX_TEXT", "REL_CALC_CHAIN",
    "REL_SHARED_STRINGS", "REL_STYLES", "REL_TABLE", "REL_WORKSHEET", "SML_NS", "Area", "Cell",
    "Content", "Formula", "SharedFormulaError", "SharedGroup", "SheetRange", "Table",
    "Workbook", "Worksheet",
    "column_index", "column_letters", "date_serial", "number_text", "parse_formula",
    "BUILTIN_DATE_IDS", "LANGUAGE_IDS", "NumberFormat", "Styles", "outside_footprint",
]
