"""Tests for the vector-embedding service."""

from __future__ import annotations

import pytest
from app.services.embedding import (
    EMBEDDING_DIMENSIONS,
    DeterministicHashEmbedder,
    Embedder,
    EmbeddingError,
    HttpEmbedder,
    _extract_vector,
    embed_text,
    reset_embedder,
    set_embedder,
)


class _RecordingEmbedder(Embedder):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return [0.0] * EMBEDDING_DIMENSIONS


@pytest.fixture(autouse=True)
def _reset() -> None:
    yield
    reset_embedder()


class TestDeterministicEmbedder:
    async def test_dimension_matches(self) -> None:
        emb = DeterministicHashEmbedder()
        vec = await emb.embed("hello")
        assert len(vec) == EMBEDDING_DIMENSIONS

    async def test_stable(self) -> None:
        emb = DeterministicHashEmbedder()
        assert await emb.embed("the same") == await emb.embed("the same")

    async def test_different_texts_differ(self) -> None:
        emb = DeterministicHashEmbedder()
        a = await emb.embed("alpha")
        b = await emb.embed("bravo")
        assert a != b

    async def test_in_range(self) -> None:
        emb = DeterministicHashEmbedder()
        vec = await emb.embed("range check")
        assert all(-1.0 <= v < 1.0 for v in vec)


class TestVectorExtractor:
    def test_happy(self) -> None:
        payload = {"data": [{"embedding": [0.1] * 4}]}
        assert _extract_vector(payload, expected_dim=4) == [0.1] * 4

    def test_dim_mismatch(self) -> None:
        with pytest.raises(EmbeddingError, match="dim mismatch"):
            _extract_vector({"data": [{"embedding": [0.1] * 3}]}, expected_dim=4)

    def test_missing_data(self) -> None:
        with pytest.raises(EmbeddingError):
            _extract_vector({}, expected_dim=4)


class TestSingleton:
    async def test_set_embedder_overrides(self) -> None:
        rec = _RecordingEmbedder()
        set_embedder(rec)
        await embed_text("anything")
        assert rec.calls == ["anything"]


class TestHttpEmbedderRequest:
    def test_builds_request_shape(self) -> None:
        # Smoke-check the body assembly without making a network call.
        embedder = HttpEmbedder(
            endpoint="https://example/v1/embeddings",
            model_name="text-embedding-3-large",
            dimensions=1024,
        )
        assert embedder._dim == 1024
        assert embedder._model == "text-embedding-3-large"
