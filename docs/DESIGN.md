# Design

Why ooxml-edit exists, where it sits beside ooxml-common, and what it leaves out.

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
| Runtime dependencies | none: the standard library only | lxml, ooxml-common |

**ooxml-common never imports ooxml-edit; ooxml-edit may import ooxml-common.** A renderer
never needs this package (ooxml-common's `tests/test_independence.py` holds that), and an
editor installs both. What a reader and an editor must answer the same way is written once,
in ooxml-common, and read from there: whether a number format code shows a date
(`ooxml_common.numfmt`, measured in Excel), the month and day names measured per locale
(`ooxml_common.dates`), the locale itself (`ooxml_common.locales.Locale`; a session given
none has `EN_US`), the package's part-path rule and relationship types
(`ooxml_common.opc`), units. ooxml-edit re-exports those under the names it always had
(`ooxml_edit.dates.NAMES`, `is_date_code`, `tools.DEFAULT_LOCALE`), so nothing that imports
them changes. Until 0.64.0 neither depended on the other, and ooxml-edit kept copies, held
to ooxml-common's by tests there; the copies had drifted (21 number format codes read
otherwise than Excel), which is why there is now one.

ooxml-common being standard library only, the dependency costs an install nothing more.

## Why lxml

ooxml-common keeps to the standard library because a renderer only reads. An editor writes
back what it read, and the standard library's `xml.etree` cannot do that: it renames
namespace prefixes to `ns0`, `ns1`... and drops declarations it believes unused. In OOXML
prefixes are load-bearing -- `mc:Ignorable="a14 p14"` names *prefixes*, not URIs -- so a
part rewritten that way is silently corrupt. lxml writes back what it parsed, prefixes and
declarations included, apart from CRLF line ends becoming LF, which the XML specification
requires. That is the whole reason for lxml.

## What is deliberately not in it

- **Any one format.** No PresentationML, WordprocessingML or SpreadsheetML: no tag, part
  path, content type or relationship type of one format. `tests/test_neutrality.py` checks
  the source for them mechanically, and checks that importing the package registers only the
  packaging namespaces every format shares.
- **DrawingML and charts, in the core.** A deck and a Word document carry the same chart
  parts and the same DrawingML, but editing them is a vocabulary, not a package operation.
  They live in the optional `ooxml_edit.charts` subpackage, which the core never imports.
- **Rendering.** That is the renderers', on ooxml-common.
- **Any one format's tools.** `ooxml_edit.tools` is plumbing only; pptx-agent and
  docx-agent define their tools on it, in their own packages.
