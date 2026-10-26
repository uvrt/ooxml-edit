"""Rendered images kept by what they are drawn from, in memory, sized in bytes.

A format library keys each image by its content -- a digest of the SVG it rasterised and
of everything else the pixels depend on (the width, the raster options, the font files the
rasteriser may load, the renderers' versions; :func:`content_key`) -- so an image is found
again whichever document, version, session or view asks for it: a review after a fix
round draws only the views whose SVG changed.  The key is the library's to compute, in the
worker that drew the SVG; a :class:`ContentStore` keeps what is under it.

A toolbox keeps two (:attr:`~.dispatch.Toolbox.images`,
:attr:`~.dispatch.Toolbox.decoded_fonts`): the images, up to
:attr:`~.limits.Limits.image_cache_bytes`, and the fonts a worker decoded from a
document's embedded ones, up to :attr:`~.limits.Limits.font_cache_bytes`, which the
toolbox hands to the next worker with its task so that no worker decodes them again.
Both are memory only, part of the toolbox: nothing is written to disk.  Separate from the
per-document caches of SVG reads and contact images (``render_cache``), so neither pushes
the other out.
"""

from __future__ import annotations

import hashlib
import json
import struct
import threading
from collections import OrderedDict
from typing import Any

#: What an entry costs beyond its bytes, counted against the budget.
ENTRY_OVERHEAD = 256


def content_key(*parts: Any) -> str:
    """A hex SHA-256 of ``parts``: bytes as they are, text as UTF-8, anything else as its
    canonical JSON -- the key an image is stored under.  Each part is length-prefixed, so
    no two different sequences of parts make the same input."""
    digest = hashlib.sha256()
    for part in parts:
        if isinstance(part, (bytes, bytearray, memoryview)):
            data = bytes(part)
        elif isinstance(part, str):
            data = part.encode("utf-8")
        else:
            data = json.dumps(part, sort_keys=True, separators=(",", ":"), default=str
                              ).encode("utf-8")
        digest.update(struct.pack(">Q", len(data)))
        digest.update(data)
    return digest.hexdigest()


_STAMPS: dict[tuple[str, ...], list] = {}
_STAMPS_LOCK = threading.Lock()


def code_stamp(*modules: str) -> list:
    """What identifies the code of ``modules`` (importable names: ``"resvg_py"``) in this
    process -- each one's version and a digest of its files' paths, sizes and times, with
    the platform -- for a content key, so an image an older renderer drew is never
    served, also where a version number does not change (an editable install).  Read
    once per process.  A module that does not import is named with ``None``."""
    with _STAMPS_LOCK:
        found = _STAMPS.get(modules)
    if found is not None:
        return found
    import importlib
    import os
    import platform

    stamp: list = [platform.system(), platform.machine(), platform.python_version()]
    for name in modules:
        try:
            module = importlib.import_module(name)
        except ImportError:
            stamp.append([name, None])
            continue
        roots = list(getattr(module, "__path__", None) or [])
        file = getattr(module, "__file__", None)
        if not roots and file:
            roots = [os.path.dirname(file)]
        entries = []
        for root in roots:
            for folder, _, names in os.walk(root):
                for file in names:
                    if not file.endswith((".py", ".so", ".pyd", ".dylib", ".json")):
                        continue
                    path = os.path.join(folder, file)
                    try:
                        info = os.stat(path)
                    except OSError:
                        continue
                    entries.append([os.path.relpath(path, root), info.st_size,
                                    info.st_mtime_ns])
        stamp.append([name, getattr(module, "__version__", None),
                      content_key(sorted(entries))])
    with _STAMPS_LOCK:
        _STAMPS[modules] = stamp
    return stamp


class ContentStore:
    """Bytes under content keys, each with a small ``meta`` (what an image leaves out, say),
    the least recently used dropped first once they pass ``max_bytes`` (counting
    :data:`ENTRY_OVERHEAD` an entry); ``max_bytes=0`` keeps nothing.  Thread-safe: every
    session and view of a toolbox shares one."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max(0, int(max_bytes))
        self._items: OrderedDict[str, tuple[bytes, Any]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        #: Lookups answered, and not.
        self.hits = self.misses = 0

    @property
    def size(self) -> int:
        """The bytes held (with :data:`ENTRY_OVERHEAD` per entry)."""
        return self._bytes

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._items

    def peek(self, key: str) -> tuple[bytes, Any] | None:
        """The entry, without counting a lookup or making it recently used."""
        with self._lock:
            return self._items.get(key)

    def entries(self) -> dict[str, tuple[bytes, Any]]:
        """Every entry, as a new dict: what a caller holds while workers say which keys
        they found (entries dropped meanwhile stay valid in it)."""
        with self._lock:
            return dict(self._items)

    def get(self, key: str) -> tuple[bytes, Any] | None:
        """``(data, meta)`` under ``key``, or ``None``."""
        with self._lock:
            found = self._items.get(key)
            if found is None:
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return found

    def put(self, key: str, data: bytes, meta: Any = None) -> None:
        """Keep ``data`` and ``meta`` under ``key`` (an entry larger than the whole budget
        is not kept)."""
        entry = (bytes(data), meta)
        cost = len(entry[0]) + ENTRY_OVERHEAD
        if cost > self.max_bytes:
            return
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self._bytes -= len(old[0]) + ENTRY_OVERHEAD
            self._items[key] = entry
            self._bytes += cost
            while self._bytes > self.max_bytes and self._items:
                _, dropped = self._items.popitem(last=False)
                self._bytes -= len(dropped[0]) + ENTRY_OVERHEAD

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def describe(self) -> dict[str, Any]:
        with self._lock:
            return {"entries": len(self._items), "bytes": self._bytes,
                    "max_bytes": self.max_bytes, "hits": self.hits, "misses": self.misses}


__all__ = ["ContentStore", "ENTRY_OVERHEAD", "code_stamp", "content_key"]
