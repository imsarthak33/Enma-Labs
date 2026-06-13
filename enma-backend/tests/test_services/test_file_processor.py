"""Tests for the file processor.

We build small synthetic PDFs using pypdf itself so the tests don't
depend on bundled binary fixtures.
"""

from __future__ import annotations

import io

import pytest
from app.services.file_processor import (
    MAX_RASTER_BYTES,
    FileKind,
    FileProcessingError,
    detect_kind,
    inspect_pdf,
    is_image,
    rasterize_pdf_page,
    split_pdf_pages,
)
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject


def _make_pdf(pages: int = 1) -> bytes:
    """Build a minimal in-memory PDF with ``pages`` blank pages."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Kind detection
# ---------------------------------------------------------------------------


class TestDetectKind:
    def test_pdf_magic(self) -> None:
        assert detect_kind(_make_pdf()) == FileKind.PDF

    def test_png_magic(self) -> None:
        assert detect_kind(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16) == FileKind.IMAGE_PNG

    def test_jpeg_magic(self) -> None:
        assert detect_kind(b"\xff\xd8\xff" + b"\x00" * 16) == FileKind.IMAGE_JPEG

    def test_webp_magic(self) -> None:
        data = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 16
        assert detect_kind(data) == FileKind.IMAGE_WEBP

    def test_unknown_for_random_bytes(self) -> None:
        assert detect_kind(b"\x00" * 32) == FileKind.UNKNOWN

    def test_unknown_for_too_short(self) -> None:
        assert detect_kind(b"\x89PNG") == FileKind.UNKNOWN

    def test_is_image_helper(self) -> None:
        assert is_image(FileKind.IMAGE_PNG) is True
        assert is_image(FileKind.IMAGE_JPEG) is True
        assert is_image(FileKind.IMAGE_WEBP) is True
        assert is_image(FileKind.PDF) is False
        assert is_image(FileKind.UNKNOWN) is False


# ---------------------------------------------------------------------------
# Inspect PDF
# ---------------------------------------------------------------------------


class TestInspectPdf:
    def test_single_page(self) -> None:
        meta = inspect_pdf(_make_pdf(1))
        assert meta.page_count == 1
        assert meta.is_encrypted is False

    def test_multi_page(self) -> None:
        meta = inspect_pdf(_make_pdf(5))
        assert meta.page_count == 5

    def test_invalid_bytes_raises(self) -> None:
        with pytest.raises(FileProcessingError, match="parse PDF"):
            inspect_pdf(b"not a pdf at all")


# ---------------------------------------------------------------------------
# Split PDF pages
# ---------------------------------------------------------------------------


class TestSplitPdfPages:
    def test_single_page_returns_one_buffer(self) -> None:
        out = split_pdf_pages(_make_pdf(1))
        assert len(out) == 1
        # Round-trip: each buffer must itself be a 1-page PDF.
        assert PdfReader(io.BytesIO(out[0])).pages.__len__() == 1

    def test_multi_page_returns_correct_count(self) -> None:
        out = split_pdf_pages(_make_pdf(4))
        assert len(out) == 4
        for buf in out:
            reader = PdfReader(io.BytesIO(buf))
            assert len(reader.pages) == 1

    def test_page_order_preserved(self) -> None:
        # Build a PDF where each page has a distinguishable rectangle size.
        writer = PdfWriter()
        for i in range(3):
            page = writer.add_blank_page(width=100 + i, height=200)
            # We rely on RectangleObject identity to differentiate.
            assert page.mediabox == RectangleObject((0, 0, 100 + i, 200))
        buf = io.BytesIO()
        writer.write(buf)
        out = split_pdf_pages(buf.getvalue())
        widths = []
        for b in out:
            page = PdfReader(io.BytesIO(b)).pages[0]
            widths.append(float(page.mediabox.width))
        assert widths == [100.0, 101.0, 102.0]

    def test_max_pages_limit(self) -> None:
        with pytest.raises(FileProcessingError, match="exceeding"):
            split_pdf_pages(_make_pdf(3), max_pages=2)

    def test_zero_pages_raises(self) -> None:
        # pypdf doesn't easily make a zero-page PDF; we monkeypatch.
        from unittest.mock import patch

        from app.services import file_processor as fp

        class _FakeReader:
            is_encrypted = False
            pages: list = []  # noqa: RUF012 — test stub

        with (
            patch.object(fp, "PdfReader", lambda *_a, **_kw: _FakeReader()),
            pytest.raises(FileProcessingError, match="zero pages"),
        ):
            split_pdf_pages(b"%PDF-fake")

    def test_encrypted_pdf_raises(self) -> None:
        from unittest.mock import patch

        from app.services import file_processor as fp

        class _FakeReader:
            is_encrypted = True
            pages: list = []  # noqa: RUF012

        with (
            patch.object(fp, "PdfReader", lambda *_a, **_kw: _FakeReader()),
            pytest.raises(FileProcessingError, match="encrypted"),
        ):
            split_pdf_pages(b"%PDF-fake")


# ---------------------------------------------------------------------------
# rasterize_pdf_page
# ---------------------------------------------------------------------------


class TestRasterizePdfPage:
    """Refactor I: PDF → PNG via PyMuPDF.

    NVIDIA NIM's chat-completions schema rejects the OpenAI ``file`` content
    part with HTTP 400 ``"data did not match any variant of untagged enum
    ChatCompletionRequestUserMessageContent"``. Rasterising the page to
    PNG locally lets us send it through the standard ``image_url`` path,
    which works across every vision LLM we use.
    """

    def test_renders_first_page_as_png(self) -> None:
        png = rasterize_pdf_page(_make_pdf(pages=2))
        # PNG magic — 8-byte header.
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        # Non-trivial size for a 72x72 blank page at 200 DPI.
        assert len(png) > 100

    def test_page_index_out_of_range_raises(self) -> None:
        with pytest.raises(FileProcessingError, match="out of range"):
            rasterize_pdf_page(_make_pdf(pages=1), page_index=5)

    def test_bad_pdf_raises(self) -> None:
        with pytest.raises(FileProcessingError, match="failed to open PDF"):
            rasterize_pdf_page(b"not-a-pdf-at-all")

    def test_oversized_png_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Cap raster bytes to a value below what 200 DPI on a blank page
        # produces, to exercise the size guard.
        from app.services import file_processor as fp

        monkeypatch.setattr(fp, "MAX_RASTER_BYTES", 1)
        with pytest.raises(FileProcessingError, match="exceeding limit"):
            rasterize_pdf_page(_make_pdf())

    def test_max_raster_bytes_is_a_constant(self) -> None:
        # Cheap sanity check — make sure the public constant is exported.
        assert MAX_RASTER_BYTES > 0
