"""Lossless, undoable editing of OOXML packages over lxml.

What every Office Open XML editor needs, whatever the document inside the package:

* :mod:`.opc` -- the OPC package: parts, relationships, content types, lossless save,
  packages inside packages, and adding, copying and reaping parts.
* :mod:`.xml` -- lxml helpers, a namespace registry and schema-ordered insertion.
* :mod:`.history` -- undo/redo/batch over package snapshots.
* :mod:`.stamp` -- durable ids written into an ``extLst`` extension.

And two optional subpackages, imported explicitly and never by the core:

* :mod:`.charts` -- charts with the data behind them, and SmartArt diagrams, edited for any
  document that embeds them; a format layer tells it where they live with a ``GraphicHost``.
* :mod:`.tools` -- the plumbing of an agent tool layer: tool definitions for Claude and the
  OpenAI APIs from one schema, a validating dispatcher, in-memory sessions, and a worker
  pool with deadlines.  The format libraries build their tools on it.

Nothing in the core may know about any one document format -- no vocabulary of any one
markup language, no format-specific tag, part path or relationship type.  Format layers
register their namespaces and child-order tables with :mod:`.xml` and subclass the package.
``tests/test_neutrality.py`` enforces the rule, for the core and for :mod:`.tools`, and
``tests/test_charts_neutrality.py`` keeps the charts subpackage free of any one document
format.
"""

__version__ = "0.4.0"
