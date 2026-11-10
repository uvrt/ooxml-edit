"""The application's own edits under the document's lock (round 48).

An application that edits a document through the library holds ``Session.lock`` while it
does; letting the lock go counts the change (every part may have changed) and commits the
version it left, as a changing call does, and ``Session.commit`` does the same without
letting go.  The next edit on a draft drafts on that version at once; reads see it.  A lock
held with nothing changed keeps the version.  A change made without the lock is not
supported; it is counted at the next lock let go, commit, or read that commits.
"""

from __future__ import annotations

import time

import pytest

from test_tools_block_edits import STALL, _box, _open, _shape, _thread


def _host_insert(session, doc, *texts, batch=False):
    with session.lock([doc]) as [entry]:
        if batch:
            with entry.document.batch():
                for text in texts:
                    entry.document.insert(text, "end")
        else:
            for text in texts:
                entry.document.insert(text, "end")
        return entry


def test_an_edit_after_the_applications_own_drafts_at_once():
    box, _hook = _box("always")
    with box:
        session, doc = _open(box)
        assert box.dispatch(session, "toy_block_add", {"doc": doc, "text": "first"}).ok
        entry = _host_insert(session, doc, "host one", "host two")
        assert entry.committed.version == entry.version          # committed as the lock went
        result = box.dispatch(session, "toy_block_add", {"doc": doc, "text": "after"})
        assert result.ok and _shape(result.block) == {"tries": 1, "conflicts": []}
        assert entry.document.items() == ["first", "host one", "host two", "after"]


def test_the_change_shows_as_the_historys_own_versions():
    box, _hook = _box("always")
    with box:
        session, doc = _open(box)
        entry = session.entry(doc)
        start = entry.version
        _host_insert(session, doc, "a", "b")                     # two library edits: two steps
        assert entry.version == start + 2
        _host_insert(session, doc, "c", "d", batch=True)         # one batch: one step
        assert entry.version == start + 3
        assert session.undo(doc) == 1 and entry.document.items() == ["a", "b"]


def test_a_lock_held_with_nothing_changed_keeps_the_version_and_the_committed_one():
    box, _hook = _box("always")
    with box:
        session, doc = _open(box)
        assert box.dispatch(session, "toy_block_add", {"doc": doc, "text": "first"}).ok
        entry = session.entry(doc)
        version, committed, seq = entry.version, entry.committed, entry.change_seq
        with session.lock([doc]):
            entry.document.items()                               # reads only
        assert (entry.version, entry.committed, entry.change_seq) == (version, committed, seq)
        assert session.commit(doc) == version and entry.committed is committed


def test_reads_after_the_lock_went_see_the_change_without_taking_the_lock():
    box, _hook = _box("always")
    with box:
        session, doc = _open(box)
        entry = _host_insert(session, doc, "host")
        reader = entry.reader()
        assert reader is not None and reader.document.items() == ["host"]
        read = box.dispatch(session, "toy_word_read", {"doc": doc})
        assert read.ok and "host" in read.data and read.version == entry.version
        with session.read_lock([doc]) as [seen]:
            assert seen is reader


def test_commit_while_holding_the_lock_lets_reads_and_drafts_go_on():
    box, _hook = _box("always")
    with box:
        session, doc = _open(box)
        entry = session.entry(doc)
        with session.lock([doc]):
            entry.document.insert("host", "end")
            version = session.commit(doc)
            assert version == entry.version == entry.committed.version
            started = time.perf_counter()
            thread, read = _thread(lambda: box.dispatch(session, "toy_word_read", {"doc": doc}))
            thread.join(timeout=STALL)
            took = time.perf_counter() - started
            assert not thread.is_alive() and took < STALL - 0.5    # the read did not wait
            assert "host" in read["result"].data
        result = box.dispatch(session, "toy_block_add", {"doc": doc, "text": "after"})
        assert result.ok and result.block["tries"] == 1 and result.block["conflicts"] == []


def test_a_change_is_counted_even_when_the_block_raised():
    box, _hook = _box("always")
    with box:
        session, doc = _open(box)
        entry = session.entry(doc)
        with pytest.raises(RuntimeError):
            with session.lock([doc]):
                entry.document.insert("half", "end")
                raise RuntimeError("the application failed")
        assert entry.committed.version == entry.version == entry.change_version
        result = box.dispatch(session, "toy_block_add", {"doc": doc, "text": "after"})
        assert result.ok and result.block["tries"] == 1


def test_a_draft_in_flight_when_the_application_edits_is_prepared_again():
    box, _hook = _box("always")
    with box:
        session, doc = _open(box)
        thread, slow = _thread(lambda: box.dispatch(
            session, "toy_block_add", {"doc": doc, "text": "slow", "stall": STALL}))
        time.sleep(0.3)
        _host_insert(session, doc, "host")
        thread.join()
        result = slow["result"]
        assert result.ok and result.block["tries"] == 2
        assert result.block["conflicts"] == ["parts changed"]     # every part may have changed
        assert session.entry(doc).document.items() == ["host", "slow"]


def test_refs_are_kept_as_the_applications_versions_own():
    box, _hook = _box("always")
    with box:
        session, doc = _open(box)
        entry = session.entry(doc)
        with entry.lock:
            entry.keep_refs({"top": "item:1"})
        _host_insert(session, doc, "a")
        assert entry.ref_states[entry.version] == {"top": "item:1"}
        assert box.dispatch(session, "toy_block_add", {"doc": doc, "text": "b"}).ok
        assert box.dispatch(session, "undo", {"doc": doc}).ok     # back to the application's version
        assert session.refs(doc) == {"top": "item:1"}


def test_a_change_without_the_lock_is_counted_at_the_next_commit():
    box, _hook = _box("always")
    with box:
        session, doc = _open(box)
        entry = session.entry(doc)
        entry.document.insert("unlocked", "end")                  # not supported: no lock held
        assert entry.change_version != entry.version
        assert session.commit(doc) == entry.version == entry.committed.version
        result = box.dispatch(session, "toy_block_add", {"doc": doc, "text": "after"})
        assert result.ok and result.block["tries"] == 1
