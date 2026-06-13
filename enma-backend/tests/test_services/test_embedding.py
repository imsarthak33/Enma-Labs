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

    async def embed(self, text: str, *, input_type: str = "query") -> list[float]:
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
        embedder = HttpEmbedder(
            endpoint="https://example/v1/embeddings",
            model_name="text-embedding-3-large",
            dimensions=1024,
        )
        assert embedder._dim == 1024
        assert embedder._model == "text-embedding-3-large"
        assert embedder._send_dimensions is True

    def test_nvidia_e5_v5_omits_dimensions(self) -> None:
        embedder = HttpEmbedder(
            endpoint="https://integrate.api.nvidia.com/v1/embeddings",
            model_name="nvidia/nv-embedqa-e5-v5",
            dimensions=1024,
        )
        assert embedder._send_dimensions is False

    def test_unknown_nvidia_model_defaults_off(self) -> None:
        # Bug 2 regression: any NIM embedding model the allow-list does not
        # explicitly recognise must default to omitting ``dimensions``, so
        # rolling out a new NIM model can't silence RAG in production.
        embedder = HttpEmbedder(
            endpoint="https://integrate.api.nvidia.com/v1/embeddings",
            model_name="nvidia/llama-3.2-nv-embedqa-1b-v2",
            dimensions=1024,
        )
        assert embedder._send_dimensions is False

    def test_generic_model_includes_dimensions(self) -> None:
        embedder = HttpEmbedder(
            endpoint="https://api.openai.com/v1/embeddings",
            model_name="text-embedding-3-large",
            dimensions=1024,
        )
        assert embedder._send_dimensions is True

    def test_send_dimensions_override_true(self) -> None:
        embedder = HttpEmbedder(
            endpoint="https://example/v1/embeddings",
            model_name="some/exotic-matryoshka",
            dimensions=1024,
            send_dimensions=True,
        )
        assert embedder._send_dimensions is True

    def test_send_dimensions_override_false(self) -> None:
        embedder = HttpEmbedder(
            endpoint="https://api.openai.com/v1/embeddings",
            model_name="text-embedding-3-large",
            dimensions=1024,
            send_dimensions=False,
        )
        assert embedder._send_dimensions is False


class TestHttpEmbedderRuntimeFallback:
    async def test_retries_without_dimensions_on_400(self) -> None:
        # Simulate a model that mistakenly matches the heuristic but actually
        # rejects ``dimensions`` — the embedder must retry once without it,
        # cache the discovery, and succeed.
        import httpx

        captured_bodies: list[dict[str, object]] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            import json

            body = json.loads(request.content)
            captured_bodies.append(body)
            if "dimensions" in body:
                return httpx.Response(
                    400,
                    json={
                        "error": (
                            "This model does not support 'dimensions', "
                            "but a value of '1024' was provided."
                        )
                    },
                )
            return httpx.Response(200, json={"data": [{"embedding": [0.0] * 1024}]})

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)
        embedder = HttpEmbedder(
            endpoint="https://example/v1/embeddings",
            model_name="text-embedding-3-large",  # heuristic says "send"
            dimensions=1024,
            client=client,
        )
        vec = await embedder.embed("hello")
        assert len(vec) == 1024
        # First attempt had dimensions, second did not.
        assert "dimensions" in captured_bodies[0]
        assert "dimensions" not in captured_bodies[1]
        # Self-corrected: future calls must skip dimensions entirely.
        assert embedder._send_dimensions is False
        await embedder.embed("again")
        assert "dimensions" not in captured_bodies[2]
        await client.aclose()

