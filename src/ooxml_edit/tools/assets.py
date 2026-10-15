"""An application's own asset catalogue -- brand icons, logos, pictures -- that tools search
and place by id.

The catalogue is application configuration, never a tool argument: a format library takes
one in its format's settings (an ``assets`` setting) and only then offers a search
tool and an ``asset`` argument (:attr:`~.session.DocumentFormat.tools`), so a toolbox
without one sends no definition for it.  The model sees ids, names and keywords; the bytes
go from the catalogue straight into the document.

Any object with these two methods is a catalogue (:class:`AssetCatalogue`)::

    class MyIcons:
        def search(self, query: str, limit: int) -> list[Asset]: ...   # best first
        def get(self, asset_id: str) -> tuple[bytes, str]: ...          # (bytes, MIME type)

``search`` returns :class:`Asset` objects (or mappings with ``id``, ``name`` and
``keywords``), best match first, at most ``limit``; ``get`` raises ``KeyError`` for an id it
does not have.  :class:`FolderCatalogue` is a simple one over a folder and an index file.

The index (JSON or CSV, read once when the catalogue is made):

* JSON: a list of entries, or ``{"assets": [...]}``; each entry an object with ``file``
  (the asset's path, relative to the folder; required), and optionally ``id`` (default:
  the file's name without its extension), ``name`` (default: the id) and ``keywords`` (a
  list of strings, or one string of words separated by ``;`` or ``,``)::

      [{"id": "arrow-up", "file": "arrows/arrow-up.svg", "name": "Arrow up",
        "keywords": ["arrow", "up", "increase"]}]

* CSV: a header row naming the same columns (``file`` required; ``id``, ``name`` and
  ``keywords`` optional, in any order), one asset a row, its keywords separated by ``;``::

      id,file,name,keywords
      arrow-up,arrows/arrow-up.svg,Arrow up,arrow;up;increase

Ids are unique.  Every file stays inside the folder (no ``..``, no absolute path, no link
out of it); a file that is missing when an asset is fetched is an error then, not when the
index is read.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .limits import Limits, check_image, sniff
from .results import ToolError

#: The most results one search returns, whatever the caller asks.
MAX_RESULTS = 50
#: Words of a query or of keywords: letters and digits, any script.
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class Asset:
    """One asset as a search returns it: its ``id`` (what a tool takes), a ``name`` and its
    ``keywords``."""

    id: str
    name: str
    keywords: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "keywords": list(self.keywords)}


@runtime_checkable
class AssetCatalogue(Protocol):
    """What a tool needs of an application's catalogue (see the module docstring)."""

    def search(self, query: str, limit: int) -> Sequence[Any]: ...

    def get(self, asset_id: str) -> tuple[bytes, str]: ...


def words(text: str) -> list[str]:
    """``text``'s words, lower case: what queries and keywords are compared as."""
    return [word.casefold() for word in _WORD.findall(text or "")]


class FolderCatalogue:
    """A catalogue over a folder and an index of its files (JSON or CSV; the module
    docstring says how one is written).

    ``index`` is the index file's path, relative to ``folder`` or absolute; ``None`` looks
    for ``index.json``, then ``index.csv``, in the folder.  A ``ValueError`` names what is
    wrong with an index that cannot be read.

    Search: each word of the query is compared with each asset's keywords and the words of
    its name and id; a whole word scores 3, the start of one 2, a part of one 1 (no part
    under three letters).  An asset must match every word of the query; the highest scores
    come first, then the names in order.

    For example::

        icons = FolderCatalogue("brand-icons")                  # brand-icons/index.json
        icons.search("arrow up", 5)    # [Asset(id='arrow-up', name='Arrow up', ...)]
        data, mime = icons.get("arrow-up")                       # (b'<svg ...', 'image/svg+xml')
    """

    def __init__(self, folder: "str | os.PathLike[str]",
                 index: "str | os.PathLike[str] | None" = None) -> None:
        self.folder = Path(folder).resolve()
        if not self.folder.is_dir():
            raise ValueError(f"{folder}: not a folder")
        if index is None:
            found = [self.folder / name for name in ("index.json", "index.csv")
                     if (self.folder / name).is_file()]
            if not found:
                raise ValueError(f"{folder}: no index.json or index.csv; pass index=")
            path = found[0]
        else:
            path = Path(index)
            path = path if path.is_absolute() else self.folder / path
        text = path.read_text(encoding="utf-8-sig")
        rows = _json_rows(text, path) if path.suffix.lower() == ".json" else _csv_rows(text, path)
        self._assets: dict[str, tuple[Asset, str]] = {}
        for number, row in enumerate(rows, 1):
            asset, file = _entry(row, f"{path.name} entry {number}")
            if asset.id in self._assets:
                raise ValueError(f"{path.name} entry {number}: the id {asset.id!r} is used twice")
            self._assets[asset.id] = (asset, file)
        self._words = {asset_id: (set(words(" ".join(asset.keywords))),
                                  set(words(asset.name)) | set(words(asset.id)))
                       for asset_id, (asset, _) in self._assets.items()}

    def __len__(self) -> int:
        return len(self._assets)

    def search(self, query: str, limit: int = 10) -> list[Asset]:
        wanted = words(query)
        if not wanted:
            return []
        scored = []
        for asset_id, (keywords, named) in self._words.items():
            total = 0
            for word in wanted:
                best = max(_score(word, keywords), _score(word, named))
                if best == 0:
                    break
                total += best
            else:
                asset = self._assets[asset_id][0]
                scored.append((-total, asset.name.casefold(), asset.id, asset))
        scored.sort(key=lambda item: item[:3])
        return [item[3] for item in scored[:max(0, int(limit))]]

    def get(self, asset_id: str) -> tuple[bytes, str]:
        if asset_id not in self._assets:
            raise KeyError(asset_id)
        file = self._assets[asset_id][1]
        path = (self.folder / file).resolve()
        if not path.is_relative_to(self.folder):
            raise ValueError(f"asset {asset_id!r}: {file} is outside the catalogue's folder")
        data = path.read_bytes()
        return data, sniff(data, path.name)


def _score(word: str, candidates: set[str]) -> int:
    if word in candidates:
        return 3
    if any(candidate.startswith(word) for candidate in candidates):
        return 2
    if len(word) >= 3 and any(word in candidate for candidate in candidates):
        return 1
    return 0


def _json_rows(text: str, path: Path) -> list[Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"{path.name}: not JSON ({error})") from None
    if isinstance(data, Mapping):
        data = data.get("assets")
    if not isinstance(data, list):
        raise ValueError(f"{path.name}: a list of entries, or {{\"assets\": [...]}}, expected")
    return data


def _csv_rows(text: str, path: Path) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "file" not in [name.strip() for name in reader.fieldnames]:
        raise ValueError(f"{path.name}: the header row names no 'file' column")
    return [{(key or "").strip(): (value or "").strip() for key, value in row.items()}
            for row in reader]


def _entry(row: Any, where: str) -> tuple[Asset, str]:
    if not isinstance(row, Mapping):
        raise ValueError(f"{where}: an object with 'file' expected")
    file = row.get("file")
    if not isinstance(file, str) or not file.strip():
        raise ValueError(f"{where}: no 'file'")
    file = file.strip().replace("\\", "/")
    if file.startswith("/") or re.match(r"^[A-Za-z]:", file) or ".." in file.split("/"):
        raise ValueError(f"{where}: {file!r} must be a path inside the folder")
    asset_id = row.get("id") or Path(file).stem
    name = row.get("name") or asset_id
    keywords = row.get("keywords") or ()
    if isinstance(keywords, str):
        keywords = [part for part in re.split(r"[;,]", keywords)]
    if not isinstance(asset_id, str) or not isinstance(name, str) or not all(
            isinstance(keyword, str) for keyword in keywords):
        raise ValueError(f"{where}: id, name and keywords are text")
    keywords = tuple(keyword.strip() for keyword in keywords if keyword.strip())
    return Asset(asset_id.strip(), name.strip(), keywords), file


# -- what tools use ------------------------------------------------------------------------------


def find(catalogue: AssetCatalogue, query: str, limit: int) -> list[dict[str, Any]]:
    """A search tool's answer: up to ``limit`` (at most :data:`MAX_RESULTS`) assets as
    ``{"id", "name", "keywords"}``, best first.  A catalogue that fails answers
    ``refused`` with what it said."""
    limit = max(1, min(int(limit), MAX_RESULTS))
    try:
        found = list(catalogue.search(query, limit))[:limit]
    except Exception as error:                # the application's code: say what it said
        raise ToolError("refused", f"the asset catalogue failed: {error}",
                        field="query") from None
    answer = []
    for item in found:
        if isinstance(item, Asset):
            answer.append(item.to_json())
        elif isinstance(item, Mapping) and isinstance(item.get("id"), str):
            answer.append({"id": item["id"], "name": str(item.get("name") or item["id"]),
                           "keywords": [str(k) for k in item.get("keywords") or ()]})
        else:
            raise ToolError("refused", "the asset catalogue returned something other than "
                            "assets", field="query")
    return answer


def fetch(catalogue: AssetCatalogue, asset_id: str, limits: Limits | None = None, *,
          field: str = "asset", search_tool: str | None = None) -> tuple[bytes, str]:
    """An asset's ``(bytes, MIME type)`` for a tool that places it: ``not_found`` for an id
    the catalogue does not have (naming ``search_tool`` to find ids with), ``invalid_arguments``
    for one that is not an image, ``limit`` for one over the session's image limits."""
    try:
        data, mime = catalogue.get(asset_id)
    except KeyError:
        hint = f"; find ids with {search_tool}" if search_tool else ""
        raise ToolError("not_found", f"no asset {asset_id!r} in the catalogue{hint}",
                        field=field) from None
    except Exception as error:
        raise ToolError("refused", f"the asset catalogue failed on {asset_id!r}: {error}",
                        field=field) from None
    if not isinstance(data, (bytes, bytearray)) or not isinstance(mime, str):
        raise ToolError("refused", f"the asset catalogue returned no bytes for {asset_id!r}",
                        field=field)
    data = bytes(data)
    if not mime.startswith("image/"):
        raise ToolError("invalid_arguments", f"asset {asset_id!r} is {mime}, not an image",
                        field=field)
    limits = limits or Limits()
    if len(data) > limits.max_blob_bytes:
        raise ToolError("limit", f"asset {asset_id!r} is {len(data)} bytes; at most "
                        f"{limits.max_blob_bytes}", field=field)
    if mime != "image/svg+xml":
        from .limits import LimitError

        try:
            check_image(data, limits, f"asset {asset_id!r}")
        except LimitError as error:
            raise ToolError("limit", str(error), field=field) from None
    return data, mime


__all__ = ["MAX_RESULTS", "Asset", "AssetCatalogue", "FolderCatalogue", "fetch", "find",
           "words"]
