"""Round 47 (lane B): "changed while this call read it" only when the change touched what the
read covered.

A read that lets the document's lock go (:meth:`Call.unlocked`) and says what it covered
(:attr:`Call.read_scope`) is warned that its document moved on only when a change made
meanwhile touched that -- as its format's ``BlockEdits.touches`` tells; a read of the whole
document (no scope), a format that cannot tell, and changes no longer kept still warn on
any change."""

from __future__ import annotations

import dataclasses
import threading
import time

from ooxml_edit.tools import Result, Toolbox, shared, string, tool
from ooxml_edit.tools import session as session_module

import synthetic
import tools_toys

STALL = 1.0


@tool("toy_scoped_read", "Test only: a slow read of pages, the lock let go.",
      {"doc": string("Document id."), "pages": string("Pages read, comma separated, or all.")},
      kind="pptx")
def toy_scoped_read(call, doc, pages):
    if pages != "all":
        call.read_scope = frozenset(call.document.page(int(n)) for n in pages.split(","))
    with call.unlocked():
        time.sleep(STALL)
    return Result(summary="read")


class PagesTouched:
    """The toy deck's ``BlockEdits`` for reads alone: a change touched a read of some pages
    when it changed one of their parts."""

    def admits(self, tool, arguments):
        return False

    def touches(self, change, scope):
        return bool(change.parts & scope)


def _box(hook=True):
    fmt = dataclasses.replace(tools_toys.DECK, blocks=PagesTouched()) if hook else tools_toys.DECK
    return Toolbox(tools_toys.TOOLS + [toy_scoped_read, *shared.SESSION_TOOLS],
                   formats=[fmt, tools_toys.TEXT], groups=tools_toys.GROUPS, workers=0)


def _read_while_editing(box, pages, edited=2, edits=1):
    session = box.session()
    doc = session.open(synthetic.outer_package(), "deck.pptx")
    out = {}
    reader = threading.Thread(target=lambda: out.setdefault("read", box.dispatch(
        session, "toy_scoped_read", {"doc": doc, "pages": pages})))
    reader.start()
    time.sleep(0.3)
    for k in range(edits):
        assert box.dispatch(session, "toy_ppt_set_title", {"doc": doc, "page": edited, "text": f"New {k}"}).ok
    reader.join()
    return out["read"]


def _warned(result):
    return any("changed while this call read it" in w for w in result.warnings)


def test_a_change_elsewhere_does_not_warn_a_read_of_some_pages():
    with _box() as box:
        result = _read_while_editing(box, "1")
        assert result.ok and not _warned(result)


def test_a_change_to_what_was_read_warns():
    with _box() as box:
        assert _warned(_read_while_editing(box, "1,2"))


def test_a_read_of_the_whole_document_warns_on_any_change():
    with _box() as box:
        assert _warned(_read_while_editing(box, "all"))


def test_a_format_that_cannot_tell_warns_on_any_change():
    with _box(hook=False) as box:
        assert _warned(_read_while_editing(box, "1"))


def test_changes_no_longer_kept_warn(monkeypatch):
    monkeypatch.setattr(session_module, "CHANGES_KEPT", 2)
    with _box() as box:
        assert _warned(_read_while_editing(box, "1", edits=4))


def test_the_changes_between_two_versions_are_the_way_there():
    entry = session_module.DocumentEntry(doc_id="d1", kind="pptx", name="x", source="app", document=None)
    for before, after in [(0, 1), (1, 2), (2, 1), (1, 3)]:      # an undo back to 1, then a change
        entry.changes_kept.append((before, after, f"{before}>{after}"))
    assert entry.changes_between(1, 3) == ["1>3"]         # from the latest time at 1
    assert entry.changes_between(0, 3) == ["0>1", "1>2", "2>1", "1>3"]
    assert entry.changes_between(3, 3) == []
    assert entry.changes_between(5, 3) is None
