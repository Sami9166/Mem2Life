from __future__ import annotations

from types import SimpleNamespace

import pytest

from recall.index.embeddings.factory import DEFAULT_PROVIDER
from recall.index.embeddings.gemini import (
    DEFAULT_DIM,
    DEFAULT_MODEL,
    GeminiEmbeddingClient,
)


class _FakeModels:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def embed_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            embeddings=[
                SimpleNamespace(values=[0.6, 0.8, *([0.0] * (DEFAULT_DIM - 2))]) for _ in kwargs["contents"]
            ]
        )


def test_gemini_embedding_2_uses_768_dimensions_and_retrieval_prompts() -> None:
    models = _FakeModels()
    client = GeminiEmbeddingClient(client=SimpleNamespace(models=models))

    assert len(client.embed(["기록 한 건"], task="document")[0]) == DEFAULT_DIM
    assert len(client.embed(["어디에 뒀지?"], task="query")[0]) == DEFAULT_DIM

    document_call, query_call = models.calls
    assert document_call["model"] == DEFAULT_MODEL
    assert document_call["config"].output_dimensionality == 768
    assert document_call["config"].task_type is None
    assert query_call["config"].task_type is None
    assert document_call["contents"][0].parts[0].text == "title: none | text: 기록 한 건"
    assert query_call["contents"][0].parts[0].text == "task: search result | query: 어디에 뒀지?"


def test_gemini_is_default_and_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DEFAULT_PROVIDER == "gemini"
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        GeminiEmbeddingClient()
