"""The format-neutral core: what a ``.pptx`` editor and a ``.docx`` editor have in common.

* :mod:`.opc` -- the OPC package: parts, relationships, content types, lossless save.
* :mod:`.xml` -- lxml helpers, a namespace registry and schema-ordered insertion.
* :mod:`.history` -- undo/redo/batch over package snapshots.
* :mod:`.stamp` -- durable ids written into an ``extLst`` extension.

Nothing in this package may know about any one document format -- no presentation or
word-processing vocabulary, no format-specific tag, part path or relationship type.  Format
layers register their namespaces and child-order tables with :mod:`.xml` and subclass the
package.  ``tests/test_core_neutrality.py`` enforces the rule, because this package is the
planned seed of a shared lxml-based library once docx editing starts (see ROADMAP.md).
"""
