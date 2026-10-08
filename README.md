# ooxml-edit

[![CI](https://github.com/uvrt/ooxml-edit/actions/workflows/ci.yml/badge.svg)](https://github.com/uvrt/ooxml-edit/actions/workflows/ci.yml)

The format-neutral half of an Office Open XML *editor*: the OPC package read and written
back losslessly, parts added, copied, removed and reaped, schema-ordered insertion, undo and
redo with batches, and durable ids stamped into an extension list. What a `.pptx`, a
`.docx` and an `.xlsx` editor all need, and nothing any one of them needs alone.

Depends on **lxml**, and only on lxml. Python 3.10+.

> A part that was never parsed is written back as the exact bytes that were read.

## Install

Not on PyPI yet. From GitHub or a checkout:

```sh
pip install "ooxml-edit @ git+https://github.com/uvrt/ooxml-edit@main"
pip install -e '.[dev]'     # from a checkout, with pytest
```

## Example

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

## What it covers

- **Core** (`ooxml_edit.opc`, `.xml`, `.history`, `.stamp`): lossless OPC packages,
  parts and relationships, safe removal (`release`/`reap`), embedded packages,
  schema-ordered insertion, undo/redo with batches and versions, durable ids.
  See [docs/CORE.md](docs/CORE.md).
- **`ooxml_edit.charts`** (optional): charts with their embedded workbooks, new charts from
  data, and SmartArt diagrams, edited the same way in a deck or a Word document.
  See [docs/CHARTS.md](docs/CHARTS.md).
- **`ooxml_edit.tools`** (optional): the plumbing of an agent tool layer -- tool
  definitions for the Anthropic and OpenAI APIs, sessions, refs and batches.
  See [docs/TOOLS.md](docs/TOOLS.md) and [docs/TOOLS-ROADMAP.md](docs/TOOLS-ROADMAP.md).

Not in it: any one format's vocabulary (no PresentationML, WordprocessingML or
SpreadsheetML -- `tests/test_neutrality.py` checks this), rendering (that is
[ooxml-common](https://github.com/uvrt/ooxml-common)'s), and any one format's tools.
Why, and how it relates to ooxml-common: [docs/DESIGN.md](docs/DESIGN.md).

## Status

Version 0.11.0, used by pptx-agent and docx-agent. Not on PyPI. Changes:
[CHANGELOG.md](CHANGELOG.md).

## Tests

```sh
python -m pytest -q
python -m pytest -m provider     # online checks; need ANTHROPIC_API_KEY (and
                                 # ANTHROPIC_WORKSPACE_ID for a key without a workspace)
```

The tests build the small packages they need (`tests/synthetic.py`, and
`tests/charts_synthetic.py` for charts of every kind, their workbooks and SmartArt); no
Office document is committed here. Chart edits are checked against the embedded workbook
by an independent reader, `tests/xlsx.py`. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Family

- [pptx2svg](https://github.com/uvrt/pptx2svg) -- renders PowerPoint (`.pptx`) slides to SVG and PNG.
- [docx2svg](https://github.com/uvrt/docx2svg) -- renders Word (`.docx`) documents to SVG, page by page.
- [ooxml-common](https://github.com/uvrt/ooxml-common) -- the format-neutral reading, DrawingML, fonts and text metrics both renderers share.
- [ooxml-edit](https://github.com/uvrt/ooxml-edit) (this repo) -- lossless, undoable editing of OOXML packages, shared by both agent layers.
- [pptx-agent](https://github.com/uvrt/pptx-agent) -- an AI-editable PowerPoint layer: inspect, edit, re-render.
- [docx-agent](https://github.com/uvrt/docx-agent) -- an AI-editable Word layer: inspect, edit (optionally as tracked changes), re-render.

## Licence

MIT; see [LICENSE](LICENSE).
