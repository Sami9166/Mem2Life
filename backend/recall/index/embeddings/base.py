"""임베딩 클라이언트 Protocol.

`ingest/stt/base.py`의 `SpeechToTextClient` 추상화와 같은 이유로 만든다:
실제 서비스에서는 OpenAI/Gemini 임베딩 API로 교체하겠지만, API 키 없이도
1단계 프로토타입이 끝까지 동작해야 하므로(코딩 컨벤션) 기본 provider는
로컬 해시 기반 스텁(`hash_stub.py`)이다. `factory.py`의 provider 매핑
한 줄만 바꾸면 실제 API 클라이언트로 교체할 수 있다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingClient(Protocol):
    """텍스트 목록 → 고정 차원 벡터 목록을 반환하는 클라이언트."""

    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """텍스트 목록을 임베딩 벡터 목록으로 변환한다 (입력 순서 유지)."""
        ...
