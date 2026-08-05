"""Gemini Embedding 2 기반 768차원 텍스트 임베딩."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types as genai_types

DEFAULT_MODEL = "gemini-embedding-2"
DEFAULT_DIM = 768
_TASK_PREFIX = {
    "document": "title: none | text: ",
    "query": "task: search result | query: ",
}


@dataclass
class GeminiEmbeddingClient:
    """`gemini-embedding-2`를 비대칭 RAG 검색 형식으로 호출한다."""

    api_key: str | None = None
    model: str = DEFAULT_MODEL
    dim: int = DEFAULT_DIM
    client: Any | None = None

    def __post_init__(self) -> None:
        if self.client is not None:
            return
        api_key = self.api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Gemini 임베딩 API 키가 없습니다. backend/.env에 GEMINI_API_KEY를 설정하세요."
            )
        self.client = genai.Client(api_key=api_key)

    def embed(self, texts: Sequence[str], *, task: str = "document") -> list[list[float]]:
        if not texts:
            return []
        try:
            task_prefix = _TASK_PREFIX[task]
        except KeyError as exc:
            raise ValueError(f"알 수 없는 임베딩 task: {task!r}") from exc

        response = self.client.models.embed_content(
            model=self.model,
            contents=[
                genai_types.Content(parts=[genai_types.Part.from_text(text=f"{task_prefix}{text}")])
                for text in texts
            ],
            config=genai_types.EmbedContentConfig(output_dimensionality=self.dim),
        )

        embeddings = response.embeddings or []
        if len(embeddings) != len(texts):
            raise RuntimeError(
                f"Gemini 임베딩 응답 개수가 다릅니다: 요청 {len(texts)}, 응답 {len(embeddings)}"
            )
        vectors = [list(embedding.values or []) for embedding in embeddings]
        if any(len(vector) != self.dim for vector in vectors):
            dimensions = sorted({len(vector) for vector in vectors})
            raise RuntimeError(f"Gemini 임베딩 차원이 {self.dim}이 아닙니다: {dimensions}")
        return vectors
