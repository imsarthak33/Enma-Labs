"""Vector-embedding service — single source of 1024-dim text vectors.

Backed by an OpenAI-compatible ``/v1/embeddings`` endpoint. The
configured model is expected to support **Matryoshka dimension
truncation** (``text-embedding-3-large`` does; specify
``dimensions=1024`` in the request to match the ``VECTOR(1024)`` column).

Production-mode invariants
--------------------------
* Outside ``ENV=test`` and ``ENV=development``, the process refuses to
  boot if ``embedding_endpoint`` is unset (validated in
  :mod:`app.config`). There is no silent "skip embedding" degraded
  mode — semantically wrong RAG results are worse than a loud failure.
* ``embedding_dimensions`` (config) must equal the database column's
  declared dimension. The column-driven dimension is the single source
  of truth; the embedder asks for that many dimensions at request time.

Test mode
---------
Tests inject a deterministic fake via :func:`set_embedder` so the suite
is offline and reproducible. The default real implementation is the
HTTP-backed :class:`HttpEmbedder`.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import Any, Final

import httpx
import sentry_sdk

from app.config import settings
from app.logging_setup import get_logger

_log = get_logger(__name__)

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "Embedder",
    "EmbeddingError",
    "HttpEmbedder",
    "embed_text",
    "get_embedder",
    "reset_embedder",
    "set_embedder",
]


# The pgvector column is declared VECTOR(1024). This is the single
# source of truth; the embedder asks for exactly this many dims.
EMBEDDING_DIMENSIONS: Final[int] = 1024


class EmbeddingError(RuntimeError):
    """Raised when an embedding call fails."""


# ---------------------------------------------------------------------------
# Abstract embedder
# ---------------------------------------------------------------------------


class Embedder(ABC):
    """Single-method interface — returns a 1024-dim list of floats."""

    @abstractmethod
    async def embed(self, text: str) -> list[float]: ...


# ---------------------------------------------------------------------------
# HTTP-backed embedder
# ---------------------------------------------------------------------------


class HttpEmbedder(Embedder):
    """OpenAI-compatible ``/v1/embeddings`` client.

    Sends ``{model, input, dimensions}`` to the configured endpoint.
    Treats anything other than HTTP 200 with a well-formed
    ``data[0].embedding`` array of the expected dimension as an error.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        model_name: str,
        dimensions: int = EMBEDDING_DIMENSIONS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._model = model_name
        self._dim = dimensions
        self._client = client

    async def _http(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        # Reuse the LLM module's process-wide client — same auth, same
        # timeout policy. We import lazily to keep the no-LLM CI gate
        # happy for tax/* modules (this file is in services/, not tax/).
        from app.services.llm import get_client

        return get_client()

    async def embed(self, text: str) -> list[float]:
        body: dict[str, Any] = {
            "model": self._model,
            "input": text,
            "dimensions": self._dim,
        }
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if settings.llm_api_key is not None:
            headers["Authorization"] = f"Bearer {settings.llm_api_key.get_secret_value()}"

        http = await self._http()
        try:
            resp = await http.post(self._endpoint, json=body, headers=headers)
        except httpx.HTTPError as exc:
            _log.error("embedding_request_failed", error=str(exc))
            sentry_sdk.capture_exception(exc)
            raise EmbeddingError(f"embedding request failed: {exc}") from exc

        if resp.status_code != httpx.codes.OK:
            raise EmbeddingError(
                f"embedding endpoint returned HTTP {resp.status_code}: " f"{resp.text[:200]}"
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise EmbeddingError(f"embedding response not JSON: {exc}") from exc

        return _extract_vector(payload, expected_dim=self._dim)


def _extract_vector(payload: object, *, expected_dim: int) -> list[float]:
    if not isinstance(payload, dict):
        raise EmbeddingError("embedding response is not an object")
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise EmbeddingError("embedding response missing 'data' array")
    first = data[0]
    if not isinstance(first, dict):
        raise EmbeddingError("embedding response 'data[0]' is not an object")
    vector = first.get("embedding")
    if not isinstance(vector, list):
        raise EmbeddingError("embedding response missing 'embedding' array")
    if len(vector) != expected_dim:
        raise EmbeddingError(f"embedding dim mismatch: expected {expected_dim}, got {len(vector)}")
    coerced: list[float] = []
    for v in vector:
        if not isinstance(v, int | float):
            raise EmbeddingError("embedding vector contains non-numeric values")
        coerced.append(float(v))
    return coerced


# ---------------------------------------------------------------------------
# Deterministic test embedder
# ---------------------------------------------------------------------------


class DeterministicHashEmbedder(Embedder):
    """A SHA-256-seeded pseudo-embedding for offline tests.

    NOT a real semantic embedder — it produces stable, reproducible
    vectors whose cosine distances are meaningless. The intent is to let
    tests assert "rule with text X always lands in the same hybrid-
    search neighbourhood" without standing up a real model.
    """

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self._dim = dimensions

    async def embed(self, text: str) -> list[float]:
        # Cycle SHA-256 digests until we have enough bytes for ``_dim``
        # floats. Each byte maps to ``(b - 128) / 128.0`` ∈ [-1, 1).
        out: list[float] = []
        seed = text.encode("utf-8")
        round_idx = 0
        while len(out) < self._dim:
            h = hashlib.sha256(seed + round_idx.to_bytes(4, "big")).digest()
            for b in h:
                if len(out) >= self._dim:
                    break
                out.append((b - 128) / 128.0)
            round_idx += 1
        return out


# ---------------------------------------------------------------------------
# Process-wide embedder singleton + overrides
# ---------------------------------------------------------------------------


_embedder: Embedder | None = None


def _build_default_embedder() -> Embedder:
    """Construct the embedder per current settings.

    Boot-fails in non-test envs when the endpoint isn't configured. In
    test mode it falls back to the deterministic embedder so the suite
    runs offline without a manual ``set_embedder`` call.
    """
    if settings.embedding_endpoint:
        model = (
            settings.embedding_model_name
            if settings.embedding_model_name
            else "text-embedding-3-large"
        )
        return HttpEmbedder(
            endpoint=settings.embedding_endpoint,
            model_name=model,
            dimensions=settings.embedding_dimensions,
        )
    if settings.env.value in {"test", "development"}:
        return DeterministicHashEmbedder()
    raise EmbeddingError("embedding_endpoint is required outside development/test envs")


def get_embedder() -> Embedder:
    """Return the active embedder, instantiating on first call."""
    global _embedder  # noqa: PLW0603 — intentional process singleton
    if _embedder is None:
        _embedder = _build_default_embedder()
    return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    """Override the process-wide embedder. Pass ``None`` to clear."""
    global _embedder  # noqa: PLW0603
    _embedder = embedder


def reset_embedder() -> None:
    """Force the next :func:`get_embedder` call to rebuild from settings."""
    global _embedder  # noqa: PLW0603
    _embedder = None


async def embed_text(text: str) -> list[float]:
    """Embed ``text`` with the active embedder."""
    return await get_embedder().embed(text)
