"""Lossless, undoable editing of OOXML packages over lxml.

What every Office Open XML editor needs, whatever the document inside the package:

* :mod:`.opc` -- the OPC package: parts, relationships, content types, lossless save,
  packages inside packages, and adding, copying and reaping parts.
* :mod:`.xml` -- lxml helpers, a namespace registry and schema-ordered insertion.
* :mod:`.history` -- undo/redo/batch over package snapshots.
* :mod:`.stamp` -- durable ids written into an ``extLst`` extension.

Nothing in this package may know about any one document format -- no vocabulary of any one
markup language, no format-specific tag, part path or relationship type.  Format layers
register their namespaces and child-order tables with :mod:`.xml` and subclass the package.
``tests/test_neutrality.py`` enforces the rule.
"""

__version__ = "0.1.0"
