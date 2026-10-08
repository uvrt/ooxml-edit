# `ooxml_edit.charts`

A chart part, the workbook behind it and a SmartArt diagram's data model are the same
markup wherever they are embedded, so the editing is here once, for every format. It is
optional and imported explicitly -- `import ooxml_edit.charts` -- and importing the core
registers none of its vocabulary. It still needs nothing but lxml.

| Module | What it is |
| --- | --- |
| `charts.host` | `GraphicHost`: where a chart or diagram lives, as the format layer sees it -- the package, the part whose relationships name it, the frame `c:chart` or `dgm:relIds` sits under (at any depth), the format's undo step, an address, and the words for messages (the application whose Edit Data opens the workbook, what the document is called), with an optional `lang`, title template and `look` (the application's new-chart sizes, for new titles and data labels); `chart_part(host)`, `diagram_parts(host)` |
| `charts.create` | `add_chart(package, part, kind, categories, series, ...)`: a new chart from data -- clustered or stacked column and bar, line, pie, scatter, radar, with a title, axis titles, the legend position (by default Office's: the bottom, a radar's top) and a number format -- written with its embedded workbook so the caches and Edit Data agree, and related to `part`; the format adds only its frame around `NewChart.graphic()`. It looks as PowerPoint's or Word's new chart of that type looks in the document's theme (`POWERPOINT_LOOK`, `WORD_LOOK`, measured on Office for Mac 16); Office's chart-style parts are not written |
| `charts.chart` | `Chart(resolve)` and `Series`: chart types, categories, series names and values, titles, axis titles and the legend, read and edited; data labels with a number format and the gap width of bars (`set_data_labels`, `set_gap_width`); categories and series added and removed (a series added gets a `c16:uniqueId`, as Word and PowerPoint give one on saving, chosen deterministically). `workbook_values()` reads back what Edit Data shows for each series' name and values and for the categories, with their formulas, to check the workbook against the cache. Every edit writes the caches (`ptCount`, `pt idx`) and the workbook cells together, moves the cells and rewrites the formulas when the shape of the data changes, keeps per-point formatting on its point and a table over the data in step -- one undo step each. A chart whose workbook is linked, an OLE object or missing is edited in its cache only, with a `ChartDataWarning`; a workbook laid out so that insertion cannot follow is refused with `ChartDataError` before anything changes |
| `charts.workbook` | The embedded `.xlsx`, edited cell by cell inside the package: numbers, shared or inline strings, cells in order, `dimension` and `spans`, tables that grow, shrink and are named after their headers, a formula a value replaces removed with the calculation chain |
| `charts.diagram` | `Diagram(resolve, on_inexact_drawing="drop", notify=None)` and `DiagramNode`: node text, nodes added and removed. A node's new points and connection get ids made from the edit (`uuid5`), so the same additions give the same bytes. The cached drawing is patched exactly where it can be -- the shape that shows a node is found through its `presOf` connection -- and otherwise dropped (PowerPoint lays the diagram out again from its data, measured), kept stale, or the edit refused, as the host chooses |
| `charts.dmltext` | DrawingML text rewritten in place, keeping mixed formatting character by character -- what a chart title, a diagram node and a text box share |
| `charts.model` | A chart's and a diagram's content as JSON-ready data: `chart_model`, `diagram_model`, `canonical_chart` and `canonical_diagram` to validate untrusted input, and `apply_chart_model` and `apply_diagram_model` to bring the document there through the edits; refusals are `ChartModelError` |
| `charts.namespaces` | The `a:`, `c:`, `dgm:`, `dsp:` and `x:` namespaces and the child orders of every element the edits insert into, DrawingML runs and paragraphs included |

A format layer builds a `GraphicHost` each time a chart is resolved, so a `Chart` survives
undo:

```python
from ooxml_edit.charts import Chart, GraphicHost
from ooxml_edit.history import History
from ooxml_edit.opc import OpcPackage

package = OpcPackage.open("in.zip")
history = History(package)

def host() -> GraphicHost:
    frame = find_the_frame(package.tree("doc/main.xml"))   # the format's own lookup
    return GraphicHost(package=package, part="doc/main.xml", frame=frame,
                       edit=history.batch, address="chart 1", application="the editor")

chart = Chart(host)
chart.series[0].set_value(2, 4285)            # cache and workbook cell
chart.add_category("Q4", [4400, 530])         # cells, formulas and table follow
chart.set_title("Revenue")
chart.set_data_labels(True, number_format='"€"#,##0.0"m"')
chart.set_gap_width(60)                       # wider columns, room for the labels
history.undo()                                # the original bytes, workbook included
```

The registry of child orders is last-wins: the subpackage owns the sequences of the DrawingML
text, chart and diagram elements, so a format layer should not register its own copies of
them.
