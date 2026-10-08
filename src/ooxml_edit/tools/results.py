"""What a tool call returns: the result envelope, error codes, images, truncation and paging.

A :class:`Result` is rendered to the model as one JSON text block, plus any images; the
adapters in :mod:`.adapters` turn it into each provider's content blocks.  Errors are
results too (``ok: false``), with a code from :data:`ERROR_CODES` and, where there is a
closed set, the ``valid_options`` the model can choose from instead.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Sequence

#: Every error code a tool result can carry, and when.
ERROR_CODES: dict[str, str] = {
    "not_found": "an address, document or blob handle that does not exist",
    "label_not_found": "a label that names nothing",
    "ambiguous": "a target that matches more than once",
    "refused": "an edit the library refuses, or a layout that cannot fit",
    "unit": "a length or value in the wrong unit or out of range",
    "invalid_arguments": "arguments that do not match the tool's schema, or an unknown tool",
    "entangled": "a scoped undo whose change shares parts with later changes outside the scope",
    "timeout": "the deadline passed",
    "limit": "a size or budget limit",
    "internal": "an unexpected failure in the tool layer or a library",
}


class ToolError(Exception):
    """An error a tool reports to the model, with the options it could use instead."""

    def __init__(self, code: str, message: str, *, valid_options: Sequence[Any] = (),
                 field: str | None = None, details: dict[str, Any] | None = None) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code {code!r}; one of {sorted(ERROR_CODES)}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.valid_options = list(valid_options)
        self.field = field
        self.details = dict(details or {})

    def __reduce__(self) -> Any:
        # Pickles whole, so an error raised in a worker process arrives intact.
        return (_rebuild_error, (self.code, self.message, self.valid_options, self.field,
                                 self.details))

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field:
            data["field"] = self.field
        data["valid_options"] = self.valid_options
        if self.details:
            data["details"] = self.details
        return data


def _rebuild_error(code: str, message: str, valid_options: list[Any], field: str | None,
                   details: dict[str, Any]) -> ToolError:
    return ToolError(code, message, valid_options=valid_options, field=field, details=details)


@dataclass
class Image:
    """An image result: PNG (or JPEG) bytes with their pixel size."""

    data: bytes
    width: int
    height: int
    media_type: str = "image/png"
    label: str = ""

    def describe(self) -> dict[str, Any]:
        return {"label": self.label, "width": self.width, "height": self.height,
                "media_type": self.media_type,
                "tokens": {"anthropic": anthropic_image_tokens(self.width, self.height),
                           "openai": openai_image_tokens(self.width, self.height)}}


@dataclass
class Result:
    """The envelope every call returns.  ``to_json`` is what the model reads."""

    ok: bool = True
    doc: str | None = None
    version: int | None = None
    summary: str = ""
    changed: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    renamed: dict[str, str] = field(default_factory=dict)
    #: Refs this call defined: ``{name: address}``; later calls may write ``$name``.
    refs: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    data: Any = None
    next_cursor: str | None = None
    total: int | None = None
    error: ToolError | None = None
    images: list[Image] = field(default_factory=list)
    #: Set by the dispatcher: the tool's name and how long the call took.
    tool: str = ""
    duration: float = 0.0

    @classmethod
    def failure(cls, error: ToolError, **fields: Any) -> "Result":
        return cls(ok=False, error=error, summary=error.message, **fields)

    @property
    def is_error(self) -> bool:
        return not self.ok

    def to_json(self) -> dict[str, Any]:
        if not self.ok:
            data: dict[str, Any] = {"ok": False, "doc": self.doc, "version": self.version,
                                    "error": self.error.to_json() if self.error else None}
            if self.warnings:
                data["warnings"] = self.warnings
            return data
        data = {"ok": True, "doc": self.doc, "version": self.version, "summary": self.summary,
                "changed": self.changed, "created": self.created, "removed": self.removed,
                "renamed": self.renamed, "warnings": self.warnings, "checks": self.checks,
                "data": self.data, "next_cursor": self.next_cursor}
        if self.refs:
            data["refs"] = self.refs
        if self.total is not None:
            data["total"] = self.total
        if self.images:
            data["images"] = [image.describe() for image in self.images]
        return data

    def to_text(self, limit: int | None = None) -> str:
        """The envelope as compact JSON, cut to ``limit`` characters by dropping ``data``."""
        text = json.dumps(self.to_json(), ensure_ascii=False, separators=(",", ":"))
        if limit is None or len(text) <= limit:
            return text
        envelope = self.to_json()
        envelope["data"] = None
        envelope["truncated"] = (f"the result was {len(text)} characters, over the "
                                 f"{limit} limit; ask for less (a range or a page)")
        text = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        if len(text) > limit:
            # Still too long: keep the counts of what else was there, so it stays JSON.
            for key in ("checks", "warnings", "changed", "created", "removed", "renamed", "refs"):
                value = envelope.get(key)
                if value:
                    envelope[key] = {"omitted": len(value)}
            text = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        return text if len(text) <= limit else json.dumps(
            {"ok": self.ok, "doc": self.doc, "version": self.version,
             "truncated": envelope["truncated"]})


# -- images ------------------------------------------------------------------------------------


def anthropic_image_tokens(width: int, height: int, *, max_edge: int = 2576,
                           max_tokens: int = 4784) -> int:
    """Claude's visual tokens: one per 28 px patch, ``ceil(w/28) * ceil(h/28)``.

    The defaults are the high-resolution tier (Claude 4.7 and later: long edge 2576 px,
    4784 tokens); pass ``max_edge=1568, max_tokens=1568`` for the standard tier.  Larger
    images are scaled down by the API first, which this follows.
    """
    width, height = _fit(width, height, max_edge)
    tokens = math.ceil(width / 28) * math.ceil(height / 28)
    while tokens > max_tokens:
        width, height = max(1, int(width * 0.98)), max(1, int(height * 0.98))
        tokens = math.ceil(width / 28) * math.ceil(height / 28)
    return tokens


def openai_image_tokens(width: int, height: int, *, multiplier: float = 1.2) -> int:
    """OpenAI's patch tokens: ``ceil(w/32) * ceil(h/32)`` patches times the model multiplier.

    1.2 is gpt-6-astra's multiplier at ``detail`` ``auto``/``original``, which keep the
    image's size; an image over 30,000 patches is rejected, not resized.
    """
    patches = math.ceil(width / 32) * math.ceil(height / 32)
    return math.ceil(patches * multiplier)


def _fit(width: int, height: int, max_edge: int) -> tuple[int, int]:
    edge = max(width, height)
    if edge <= max_edge:
        return width, height
    scale = max_edge / edge
    return max(1, round(width * scale)), max(1, round(height * scale))


# -- truncation and paging ---------------------------------------------------------------------


def page_text(text: str, *, cursor: str | None = None, limit: int = 24000) -> tuple[str, str | None]:
    """One page of ``text`` and the cursor of the next (``None`` at the end).

    Pages break at a line end where one falls in the second half of the page, so a page
    rarely ends mid-line.  The cursor is opaque to the model: pass it back unchanged.
    """
    start = _offset(cursor)
    if start > len(text):
        raise ToolError("invalid_arguments", f"cursor {cursor!r} is past the end",
                        field="cursor")
    end = start + limit
    if end >= len(text):
        return text[start:], None
    newline = text.rfind("\n", start + limit // 2, end)
    if newline != -1:
        end = newline + 1
    return text[start:end], f"c{end}"


def page_list(items: Sequence[Any], *, cursor: str | None = None,
              limit: int = 50) -> tuple[list[Any], int, str | None]:
    """One page of ``items``, the total, and the next page's cursor (``None`` at the end)."""
    start = _offset(cursor)
    if start > len(items):
        raise ToolError("invalid_arguments", f"cursor {cursor!r} is past the end",
                        field="cursor")
    page = list(items[start:start + limit])
    end = start + len(page)
    return page, len(items), (f"c{end}" if end < len(items) else None)


def truncate(text: str, limit: int, marker: str = "…") -> str:
    """``text`` cut to ``limit`` characters, marked where it was cut."""
    if len(text) <= limit:
        return text
    return text[:max(0, limit - len(marker))] + marker


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    if not (cursor.startswith("c") and cursor[1:].isdigit()):
        raise ToolError("invalid_arguments", f"cursor {cursor!r} is not one this tool gave",
                        field="cursor")
    return int(cursor[1:])
