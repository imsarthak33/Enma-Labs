"""File processor — content-type detection + PDF page handling.

Scope (Phase 4):

  * Detect the file kind from the leading bytes (magic numbers).
  * For PDFs: count pages, split into single-page PDF byte buffers.
  * For images: pass through (the vision LLM does the heavy lifting).

We deliberately do NOT rasterise PDF pages to images here. Modern vision
LLMs accept PDF pages natively (gpt-4o, NVIDIA NIM vision), so an extra
rasterisation step would burn CPU for no benefit. If we ever hit a model
that doesn't, this is the file where pdf2image/Pillow would land.

Phase 7 adds voice-note transcription handling — likely in a separate
``whisper.py`` module since the dependency surface is different.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from pypdf import PdfReader, PdfWriter

from app.logging_setup import get_logger

_log = get_logger(__name__)


class FileKind(StrEnum):
    PDF = "pdf"
    IMAGE_JPEG = "image_jpeg"
    IMAGE_PNG = "image_png"
    IMAGE_WEBP = "image_webp"
    UNKNOWN = "unknown"


# Maximum PDF pages we'll attempt to split in one go. Anything larger gets
# rejected with a clear error — protects the LLM token budget and keeps
# memory bounded.
MAX_PDF_PAGES: Final[int] = 50


# Maximum per-page PDF size after split. Sanity guard against a single
# pathological page consuming gigabytes.
MAX_PAGE_BYTES: Final[int] = 20 * 1024 * 1024  # 20 MB


# Magic numbers — read the first few bytes to identify file type. We never
# trust client-supplied MIME because the gateway passes Telegram metadata
# verbatim and a malicious client could lie. The bytes don't.
_PDF_MAGIC: Final[bytes] = b"%PDF-"
_PNG_MAGIC: Final[bytes] = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC: Final[bytes] = b"\xff\xd8\xff"
_WEBP_MAGIC: Final[bytes] = b"WEBP"  # appears at offset 8 after RIFF header


class FileProcessingError(RuntimeError):
    """Raised when file inspection or page-splitting fails."""


@dataclass(frozen=True)
class PdfMetadata:
    """Structured PDF information, returned from :func:`inspect_pdf`."""

    page_count: int
    is_encrypted: bool


# ---------------------------------------------------------------------------
# Kind detection
# ---------------------------------------------------------------------------


def detect_kind(data: bytes) -> FileKind:
    """Identify file type from its first bytes.

    Returns ``FileKind.UNKNOWN`` when nothing matches — we never guess.
    """
    if not isinstance(data, bytes):  # pragma: no cover — defensive
        raise TypeError(f"expected bytes, got {type(data).__name__}")
    if len(data) < 8:
        return FileKind.UNKNOWN
    if data.startswith(_PDF_MAGIC):
        return FileKind.PDF
    if data.startswith(_PNG_MAGIC):
        return FileKind.IMAGE_PNG
    if data.startswith(_JPEG_MAGIC):
        return FileKind.IMAGE_JPEG
    # WEBP: RIFF....WEBP
    if data[:4] == b"RIFF" and data[8:12] == _WEBP_MAGIC:
        return FileKind.IMAGE_WEBP
    return FileKind.UNKNOWN


def is_image(kind: FileKind) -> bool:
    return kind in (
        FileKind.IMAGE_JPEG,
        FileKind.IMAGE_PNG,
        FileKind.IMAGE_WEBP,
    )


# ---------------------------------------------------------------------------
# PDF page handling
# ---------------------------------------------------------------------------


def _open_reader(data: bytes) -> PdfReader:
    try:
        return PdfReader(io.BytesIO(data), strict=False)
    except Exception as exc:  # pypdf raises a variety of types
        raise FileProcessingError(f"failed to parse PDF: {exc}") from exc


def inspect_pdf(data: bytes) -> PdfMetadata:
    """Return page count + encryption status for ``data``.

    Raises:
        FileProcessingError: when ``data`` is not a parseable PDF.
    """
    reader = _open_reader(data)
    if reader.is_encrypted:
        return PdfMetadata(page_count=0, is_encrypted=True)
    return PdfMetadata(page_count=len(reader.pages), is_encrypted=False)


def split_pdf_pages(data: bytes, *, max_pages: int = MAX_PDF_PAGES) -> list[bytes]:
    """Split a multi-page PDF into one-page PDF byte buffers.

    Each returned ``bytes`` is a self-contained, valid PDF document
    containing exactly one page from the source. Page ordering is preserved.

    Raises:
        FileProcessingError: when the PDF is encrypted, has zero pages,
            exceeds ``max_pages``, or a per-page split exceeds
            :data:`MAX_PAGE_BYTES`.
    """
    meta = inspect_pdf(data)
    if meta.is_encrypted:
        raise FileProcessingError("PDF is encrypted; cannot split")
    if meta.page_count == 0:
        raise FileProcessingError("PDF has zero pages")
    if meta.page_count > max_pages:
        raise FileProcessingError(
            f"PDF has {meta.page_count} pages, exceeding limit of {max_pages}"
        )

    reader = _open_reader(data)
    out: list[bytes] = []
    for idx, page in enumerate(reader.pages):
        writer = PdfWriter()
        writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        page_bytes = buf.getvalue()
        if len(page_bytes) > MAX_PAGE_BYTES:
            raise FileProcessingError(
                f"split page {idx + 1} is {len(page_bytes)} bytes, "
                f"exceeding limit of {MAX_PAGE_BYTES}"
            )
        out.append(page_bytes)
    _log.debug("pdf_split", pages=len(out))
    return out


__all__ = [
    "MAX_PAGE_BYTES",
    "MAX_PDF_PAGES",
    "FileKind",
    "FileProcessingError",
    "PdfMetadata",
    "detect_kind",
    "inspect_pdf",
    "is_image",
    "split_pdf_pages",
]
