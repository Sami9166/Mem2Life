"""순수 파이썬 코사인 유사도 벡터 스토어.

데모 스케일(문서 수백 개)에서는 numpy/faiss 없이 O(N·dim) 선형 스캔으로
충분히 빠르다. 벡터가 이미 L2 정규화돼 있다는 전제(코사인 유사도 = 내적)
하에 동작한다 — Gemini Embedding 2와 해시 스텁 모두 정규화된
벡터를 반환한다.
"""

from __future__ import annotations

from dataclasses import dataclass


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@dataclass
class VectorStore:
    vectors: list[list[float]]

    def score(self, query_vector: list[float]) -> list[float]:
        """코퍼스의 각 벡터에 대한 코사인 유사도 점수 목록 (문서 순서 유지)."""
        return [_dot(query_vector, vec) for vec in self.vectors]
