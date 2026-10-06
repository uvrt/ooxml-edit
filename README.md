# ooxml-edit

The format-neutral half of an Office Open XML *editor*: the OPC package read and written
back losslessly, parts added, copied, removed and reaped, schema-ordered insertion, undo and
redo with batches, and durable ids stamped into an extension list. What a `.pptx` editor,
a `.docx` editor and an `.xlsx` editor all need, and nothing any one of them needs alone.

Beside that core, an optional subpackage, [`ooxml_edit.charts`](#ooxml_editcharts-optional),
edits what a deck and a Word document embed alike: charts with their embedded workbooks,
and SmartArt diagrams.

Depends on **lxml**, and only on lxml. Python 3.10+.

## Why it exists

pptx-agent edits PowerPoint decks; docx-agent will edit Word documents. Below the
vocabulary they are the same program: open a ZIP of XML parts, change a few of them, put a
new child where the schema says it goes, remove a part only once nothing refers to it, undo
any of it to the exact bytes that were there, and write everything that was not touched back
as the bytes that were read. pptx-agent had already separated that layer from its
PresentationML, and kept it separate with a test. This package is that layer, so that the
second editor depends on it instead of copying it.

It was extracted from pptx-agent **with its git history**: `git log` on any module shows
how it came to be, from the commit that first separated it.

## How it relates to ooxml-common

[ooxml-common](https://github.com/uvrt/ooxml-common) and ooxml-edit are both format-neutral
OOXML packages, and they do different jobs:

| | ooxml-common | ooxml-edit |
| --- | --- | --- |
| Does | **reads and renders**: the OPC container read, units, fonts and text metrics, DrawingML and charts drawn as SVG | **edits losslessly**: parts and relationships changed, written back byte for byte where untouched, every change undoable |
| Used by | the renderers, [pptx2svg](https://github.com/uvrt/pptx2svg) and [docx2svg](https://github.com/uvrt/docx2svg) | the editors, pptx-agent and docx-agent |
| Runtime dependencies | none: the standard library only | lxml |

Neither depends on the other. An editor that also renders installs both, through its
renderer; a renderer never needs this package.

## Why lxml

ooxml-common keeps to the standard library because a renderer only reads. An editor writes
back what it read, and the standard library's `xml.etree` cannot do that: it renames
namespace prefixes to `ns0`, `ns1`... and drops declarations it believes unused. In OOXML
prefixes are load-bearing -- `mc:Ignorable="a14 p14"` names *prefixes*, not URIs -- so a
part rewritten that way is silently corrupt. lxml writes back what it parsed, prefixes and
declarations included, apart from CRLF line ends becoming LF, which the XML specification
requires. That is the whole reason for the dependency.

## What is in it

| Module | What it is |
| --- | --- |
| `ooxml_edit.opc` | `OpcPackage`: parts as original bytes plus lazily parsed trees; content types, and `content_types_with` for a copy written with a part retyped while the open package stays as it is; relationships, internal and external; adding, replacing, removing and copying parts; `release` and `reap`, which remove a part only once it is proved unreferenced from anywhere in the package, and leave no empty relationships part and no unused `Default` behind, as Word writes none; packages inside the package (`open_embedded`, `replace_embedded`); snapshots for undo; saving with entry order, timestamps and compression kept, optionally with derived parts written in place (`replacements`); `changed_parts` and `opened` |
| `ooxml_edit.xml` | The namespace registry and `qn`; attribute helpers; `register_child_order` and `insert_in_order`, which put a new child where its parent's schema sequence requires, with rank groups for repeating choices, and append a detached child where no sequence is known; `replace_choice`; `remove`, which keeps the whitespace around what it removes |
| `ooxml_edit.history` | `History`: undo, redo and nested batches over anything with `snapshot` and `restore`; a failed batch rolls back |
| `ooxml_edit.stamp` | `ExtensionStamp`: an id frozen into an `extLst`/`ext` extension, which Office keeps when it does not know the URI |

The losslessness rule, which everything else rests on:

> A part that was never parsed is written back as the exact bytes that were read.

Only a part whose tree was changed, and marked so with `mark_dirty`, is serialized again.
Undo snapshots hold only those parts, and parts written as raw bytes, so undoing anything
gives back the original bytes, and redoing it gives back the edited ones.

## Using it

A format layer registers its namespaces and the child sequences of the elements it inserts
into, and usually subclasses `OpcPackage` with its own entry points:

```python
from ooxml_edit.history import History
from ooxml_edit.opc import OpcPackage
from ooxml_edit.xml import make, append_in_order, register_child_order, register_namespaces

register_namespaces({"my": "urn:example:my-format"})
register_child_order({"my:props": ("my:name", ("my:item", "my:note"), "my:extLst")})

package = OpcPackage.open("in.zip")
history = History(package)

part = package.main_document_part()
history.checkpoint()                      # before every mutation
append_in_order(package.tree(part), make("my:item", val="1"))
package.mark_dirty(part)

history.undo()                            # the original bytes again
package.save("out.zip")
```

## What is deliberately not in it

- **Any one format.** No PresentationML, WordprocessingML or SpreadsheetML: no tag, part
  path, content type or relationship type of one format. `tests/test_neutrality.py` checks
  the source for them mechanically, and checks that importing the package registers only the
  packaging namespaces every format shares.
- **DrawingML and charts, in the core.** A deck and a Word document carry the same chart
  parts and the same DrawingML, but editing them is a vocabulary, not a package operation.
  They live in the optional `ooxml_edit.charts` subpackage, which the core never imports.
- **Rendering.** That is the renderers', on ooxml-common.

## `ooxml_edit.charts` (optional)

A chart part, the workbook behind it and a SmartArt diagram's data model are the same
markup wherever they are embedded, so the editing is here once, for every format. It is
optional and imported explicitly -- `import ooxml_edit.charts` -- and importing the core
registers none of its vocabulary. It still needs nothing but lxml.

| Module | What it is |
| --- | --- |
| `charts.host` | `GraphicHost`: where a chart or diagram lives, as the format layer sees it -- the package, the part whose relationships name it, the frame `c:chart` or `dgm:relIds` sits under (at any depth), the format's undo step, an address, and the words for messages (the application whose Edit Data opens the workbook, what the document is called), with an optional `lang` and title template; `chart_part(host)`, `diagram_parts(host)` |
| `charts.chart` | `Chart(resolve)` and `Series`: chart types, categories, series names and values, titles, axis titles and the legend, read and edited; categories and series added and removed (a series added gets a `c16:uniqueId`, as Word and PowerPoint give one on saving, chosen deterministically). `workbook_values()` reads back what Edit Data shows for each series' name and values and for the categories, with their formulas, to check the workbook against the cache. Every edit writes the caches (`ptCount`, `pt idx`) and the workbook cells together, moves the cells and rewrites the formulas when the shape of the data changes, keeps per-point formatting on its point and a table over the data in step -- one undo step each. A chart whose workbook is linked, an OLE object or missing is edited in its cache only, with a `ChartDataWarning`; a workbook laid out so that insertion cannot follow is refused with `ChartDataError` before anything changes |
| `charts.workbook` | The embedded `.xlsx`, edited cell by cell inside the package: numbers, shared or inline strings, cells in order, `dimension` and `spans`, tables that grow, shrink and are named after their headers, a formula a value replaces removed with the calculation chain |
| `charts.diagram` | `Diagram(resolve, on_inexact_drawing="drop", notify=None)` and `DiagramNode`: node text, nodes added and removed. The cached drawing is patched exactly where it can be -- the shape that shows a node is found through its `presOf` connection -- and otherwise dropped (PowerPoint lays the diagram out again from its data, measured), kept stale, or the edit refused, as the host chooses |
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
history.undo()                                # the original bytes, workbook included
```

The registry of child orders is last-wins: the subpackage owns the sequences of the DrawingML
text, chart and diagram elements, so a format layer should not register its own copies of
them.

## Install

Not on PyPI yet. From a checkout:

```sh
pip install -e .            # + lxml
pip install -e '.[dev]'     # + pytest
```

## Tests

```sh
python -m pytest -q
```

The tests build the small packages they need (`tests/synthetic.py`, and
`tests/charts_synthetic.py` for charts of every kind, their workbooks and SmartArt); no
Office document is committed here. Chart edits are checked against the embedded workbook
by an independent reader, `tests/xlsx.py`, rather than by the code that wrote it.

## Licence

MIT; see [LICENSE](LICENSE).
