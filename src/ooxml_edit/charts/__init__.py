"""Charts, their embedded workbooks, and SmartArt diagrams, edited losslessly.

An optional part of ooxml-edit, imported explicitly (``import ooxml_edit.charts``): the core
never imports it, and importing the core registers none of this vocabulary.

A chart part, the workbook behind it and a diagram's data model are the same DrawingML
whichever Office document embeds them, so the editing lives here once:

* :mod:`.chart` -- :class:`Chart`: series, categories, values, titles and legend, edited in
  the chart's caches and its embedded workbook together, in one undo step;
* :mod:`.workbook` -- the embedded ``.xlsx``, edited cell by cell: shared and inline strings,
  tables, formulas and the calculation chain kept in step;
* :mod:`.diagram` -- :class:`Diagram`: SmartArt node text, and nodes added and removed, with
  the cached drawing kept exactly in step or, by the host's choice, dropped or kept;
* :mod:`.dmltext` -- DrawingML text rewritten in place, keeping mixed formatting;
* :mod:`.model` -- a chart's and a diagram's content as JSON-ready data, validated and applied
  back;
* :mod:`.host` -- :class:`GraphicHost`, all a document format has to say about where a chart
  or a diagram lives;
* :mod:`.namespaces` -- the ``a:``, ``c:``, ``dgm:``, ``dsp:`` and ``x:`` vocabulary and the
  child orders of everything inserted into.

Nothing here knows any one document format: ``tests/test_charts_neutrality.py`` checks it.
"""

from .namespaces import A_NS, C_NS, DGM_NS, DSP_NS, SML_NS
from .host import GraphicHost, chart_part, diagram_parts
from .workbook import Workbook, Worksheet
from .chart import Chart, ChartDataError, ChartDataWarning, Series, chart_model, workbook_part
from .diagram import Diagram, DiagramDrawingError, DiagramNode, diagram_model
from .model import (
    ChartModelError,
    apply_chart_model,
    apply_diagram_model,
    canonical_chart,
    canonical_diagram,
)

__all__ = [
    "A_NS", "C_NS", "DGM_NS", "DSP_NS", "SML_NS",
    "Chart", "ChartDataError", "ChartDataWarning", "ChartModelError", "Diagram",
    "DiagramDrawingError", "DiagramNode", "GraphicHost", "Series", "Workbook", "Worksheet",
    "apply_chart_model", "apply_diagram_model", "canonical_chart", "canonical_diagram",
    "chart_model", "chart_part", "diagram_model", "diagram_parts", "workbook_part",
]
