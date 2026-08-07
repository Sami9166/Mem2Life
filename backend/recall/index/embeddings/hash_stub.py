"""API 키 없이 동작하는 결정적(deterministic) 해시 기반 임베딩 스텁.

진짜 의미(semantic) 임베딩은 아니다 — 토큰을 고정 차원 공간에 특징
해싱(feature hashing)한 뒤 L2 정규화한 어휘(lexical) 벡터다. 그래도
`tokenize.py`의 한글 바이그램 덕분에 조사가 달라도 어느 정도 유사도가
잡히고, 코사인 유사도로 순위를 매길 수 있어 "벡터 검색" 아키텍처를
그대로 검증할 수 있다.

Gemini API를 쓰지 않는 테스트·오프라인 진단에서만 `--embedding hash`로
명시해 사용한다. `dim`이 다른 provider로 바꾸면 `index/store.py`가 기존
캐시를 무효화하고 자동으로 재계산한다.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..tokenize import tokenize

DEFAULT_DIM = 768


def _feature_hash(token: str, dim: int) -> tuple[int, int]:
    digest = hashlib.sha1(token.encode("utf-8")).digest()
    idx = int.from_bytes(digest[:4], "big") % dim
    sign = 1 if digest[4] % 2 == 0 else -1
    return idx, sign


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector
    return [v / norm for v in vector]


@dataclass
class HashEmbeddingClient:
    """provider 이름: `"hash"` (명시적 오프라인용, API 키 불필요)."""

    dim: int = field(default=DEFAULT_DIM)

    def embed(self, texts: Sequence[str], *, task: str = "document") -> list[list[float]]:
        results: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dim
            for token in tokenize(text):
                idx, sign = _feature_hash(token, self.dim)
                vector[idx] += sign
            results.append(_normalize(vector))
        return results
