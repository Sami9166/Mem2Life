"""임베딩 클라이언트 Protocol.

`ingest/stt/base.py`의 `SpeechToTextClient` 추상화와 같은 이유로 만든다:
실서비스 기본 구현은 Gemini Embedding API이고, 로컬 해시 구현은 테스트와
오프라인 진단을 위한 명시적 provider로만 남긴다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingClient(Protocol):
    """텍스트 목록 → 고정 차원 벡터 목록을 반환하는 클라이언트."""

    dim: int

    def embed(self, texts: Sequence[str], *, task: str = "document") -> list[list[float]]:
        """텍스트 목록을 문서 또는 질의 벡터로 변환한다 (입력 순서 유지)."""
        ...
