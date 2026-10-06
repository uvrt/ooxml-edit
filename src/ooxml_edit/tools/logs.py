"""Call logging: what was called, on what, how it went -- never the document's content.

Every call is recorded as a :class:`CallRecord` on the session (and on each document it
touched) and logged to the ``ooxml_edit.tools`` logger at INFO.  Arguments appear as a
digest -- a hash of their canonical JSON, and their shape (names, types and lengths) -- so
a transcript can tell two calls apart and find the same call again without holding any text
from the document or the conversation.  An application that wants full arguments in its
logs passes ``log_arguments=True`` to the toolbox.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ooxml_edit.tools")


@dataclass
class CallRecord:
    """One call, as a transcript keeps it."""

    tool: str
    docs: list[str]
    digest: str
    shape: dict[str, Any]
    ok: bool
    code: str | None
    summary: str
    duration: float
    version: int | None = None
    arguments: dict[str, Any] | None = field(default=None, repr=False)

    def to_json(self) -> dict[str, Any]:
        data = {"tool": self.tool, "docs": self.docs, "digest": self.digest,
                "shape": self.shape, "ok": self.ok, "code": self.code,
                "summary": self.summary, "duration": round(self.duration, 4),
                "version": self.version}
        if self.arguments is not None:
            data["arguments"] = self.arguments
        return data


def digest(arguments: Any) -> str:
    """A stable 16-hex-digit hash of the arguments' canonical JSON."""
    text = json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=repr)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def shape(value: Any, depth: int = 0) -> Any:
    """The arguments' shape with the content left out: ``{"text": "str[42]", "n": "int"}``."""
    if isinstance(value, dict):
        if depth >= 3:
            return f"object[{len(value)}]"
        return {str(key): shape(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        if depth >= 3 or not value:
            return f"array[{len(value)}]"
        return [shape(value[0], depth + 1), f"x{len(value)}"]
    if isinstance(value, str):
        return f"str[{len(value)}]"
    if isinstance(value, bool):
        return "bool"
    if value is None:
        return "null"
    return type(value).__name__


def log_call(record: CallRecord) -> None:
    logger.info("tool=%s docs=%s ok=%s code=%s version=%s digest=%s duration=%.3fs",
                record.tool, ",".join(record.docs) or "-", record.ok, record.code or "-",
                record.version, record.digest, record.duration)
