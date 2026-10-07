"""Size limits, timeouts and budgets, and the checks run when bytes come in.

Everything the application registers -- a document to open, an image, a template, a
Markdown file -- is checked here before any library sees it: its size, what its first bytes
say it is, an image's decoded size read from its header, and a ZIP's central directory for
the signs of a zip bomb.  Nothing is decompressed to check it.
"""

from __future__ import annotations

import io
import struct
import zipfile
from dataclasses import dataclass

MB = 1024 * 1024


@dataclass(frozen=True)
class Limits:
    """Every limit of a session; all configurable by the application."""

    # documents and inputs
    max_documents: int = 4
    max_package_bytes: int = 50 * MB
    max_total_document_bytes: int = 200 * MB
    max_parts: int = 2000
    max_uncompressed_bytes: int = 500 * MB
    #: A part may expand at most this many times (checked on parts over 1 MB uncompressed).
    max_compression_ratio: int = 200
    max_blob_bytes: int = 50 * MB
    max_total_blob_bytes: int = 100 * MB
    max_image_bytes: int = 20 * MB
    max_image_pixels: int = 40_000_000
    max_markdown_chars: int = 200_000
    # results
    max_result_chars: int = 24_000
    max_list_items: int = 50
    max_images_per_call: int = 4
    max_image_edge: int = 2576
    image_budget: int = 40
    # batches
    max_batch_ops: int = 200
    # deadlines, in seconds
    render_timeout: float = 20.0
    layout_timeout: float = 30.0
    soft_deadline: float = 10.0
    #: The whole of one ``batch`` call: its ops and the checks after them.
    batch_timeout: float = 120.0
    # caches, in entries per document
    render_cache_size: int = 16
    check_cache_size: int = 16


class LimitError(ValueError):
    """Bytes the application registered that break a limit or are not what they claim.

    Raised to the application, which registered them; a tool that meets one reports the
    ``limit`` error code instead.
    """


#: First bytes and the type they announce.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"PK\x03\x04", "application/zip"),
    (b"PK\x05\x06", "application/zip"),
    (b"%PDF-", "application/pdf"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/x-ole-storage"),
)

#: Types whose content is text, recognised by extension and checked to decode as UTF-8.
TEXT_TYPES = {".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain",
              ".csv": "text/csv", ".json": "application/json", ".svg": "image/svg+xml"}


def sniff(data: bytes, name: str = "") -> str:
    """The type ``data`` is, from its first bytes; text types from the name's extension."""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] in (b"\x01\x00\x00\x00", b"\xd7\xcd\xc6\x9a"):
        return "image/x-emf" if data[40:44] == b" EMF" else "image/x-wmf"
    extension = "." + name.rpartition(".")[2].lower() if "." in name else ""
    if extension in TEXT_TYPES:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return "application/octet-stream"
        return TEXT_TYPES[extension]
    return "application/octet-stream"


def check_declared_type(data: bytes, name: str, declared: str | None) -> str:
    """The blob's type: what its bytes say, which must agree with what the caller declared.

    A declared ZIP-based type (an Office package) is accepted for ZIP bytes; a declared
    type the bytes cannot confirm is refused, so a renamed executable is not an image.
    """
    found = sniff(data, name)
    if declared is None:
        return found
    if declared == found:
        return declared
    if found == "application/zip" and (declared.endswith("+zip") or "officedocument" in declared
                                       or declared.startswith("application/vnd.")):
        return declared
    if found == "image/x-emf" and declared in ("image/emf", "image/x-emf"):
        return declared
    if found == "image/x-wmf" and declared in ("image/wmf", "image/x-wmf"):
        return declared
    raise LimitError(f"{name or 'the blob'} is declared {declared} but its bytes are {found}")


def image_size(data: bytes) -> tuple[int, int] | None:
    """An image's pixel size from its header (PNG, JPEG, GIF, BMP, WebP), or ``None``."""
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", data[6:10])
        if data.startswith(b"BM"):
            width, height = struct.unpack("<ii", data[18:26])
            return abs(width), abs(height)
        if data.startswith(b"\xff\xd8"):
            return _jpeg_size(data)
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return _webp_size(data)
    except struct.error:
        return None
    return None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            index += 1 if marker == 0xFF else 2
            continue
        length = struct.unpack(">H", data[index + 2:index + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[index + 5:index + 9])
            return width, height
        index += 2 + length
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    if chunk == b"VP8X":
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    if chunk == b"VP8 ":
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def check_image(data: bytes, limits: Limits, name: str = "") -> tuple[int, int] | None:
    """Refuse an image over the byte or decoded-pixel limit; return its size if known."""
    label = name or "the image"
    if len(data) > limits.max_image_bytes:
        raise LimitError(f"{label} is {len(data)} bytes; at most {limits.max_image_bytes}")
    size = image_size(data)
    if size and size[0] * size[1] > limits.max_image_pixels:
        raise LimitError(f"{label} is {size[0]}x{size[1]} pixels; at most "
                         f"{limits.max_image_pixels} decoded")
    return size


def check_package(data: bytes, limits: Limits, name: str = "") -> None:
    """Refuse a ZIP package that is too large, has too many parts, or looks like a bomb.

    Reads only the central directory: the declared sizes are what the archive would expand
    to, and :mod:`zipfile` refuses to read past a declared size, so they bound the real one.
    """
    label = name or "the package"
    if len(data) > limits.max_package_bytes:
        raise LimitError(f"{label} is {len(data)} bytes; at most {limits.max_package_bytes}")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, ValueError, EOFError) as exc:
        raise LimitError(f"{label} is not a readable ZIP package: {exc}") from None
    if len(infos) > limits.max_parts:
        raise LimitError(f"{label} has {len(infos)} parts; at most {limits.max_parts}")
    total = sum(info.file_size for info in infos)
    if total > limits.max_uncompressed_bytes:
        raise LimitError(f"{label} expands to {total} bytes; at most "
                         f"{limits.max_uncompressed_bytes}")
    for info in infos:
        if info.file_size > MB and info.file_size > limits.max_compression_ratio * max(
                info.compress_size, 1):
            raise LimitError(f"{label}: {info.filename} expands {info.file_size} bytes from "
                             f"{info.compress_size}; over the ratio limit")
