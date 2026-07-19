"""BM25 Okapi 키워드 검색 (외부 의존성 없이 순수 파이썬 구현).

`rank_bm25` 같은 패키지를 추가할 수도 있지만, 데모 스케일(문서 수백 개
이하)에서는 직접 구현이 더 단순하고 의존성도 줄어든다. 공식은 표준
BM25 Okapi(k1=1.5, b=0.75 기본값)를 따른다.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass


@dataclass
class BM25Index:
    """토큰화된 문서 코퍼스에 대한 BM25 인덱스.

    `fit()`으로 전체 코퍼스를 다시 계산한다 — 데모 스케일에서는 매번
    새로 계산해도 충분히 빠르므로 "증분 BM25"는 구현하지 않는다(증분
    갱신은 임베딩 재계산을 건너뛰는 쪽에서 비용을 절감한다 — `store.py`
    참고).
    """

    k1: float = 1.5
    b: float = 0.75

    def __post_init__(self) -> None:
        self._doc_term_counts: list[Counter[str]] = []
        self._doc_lengths: list[int] = []
        self._avgdl: float = 0.0
        self._df: Counter[str] = Counter()
        self._n_docs: int = 0

    def fit(self, tokenized_docs: list[list[str]]) -> None:
        self._doc_term_counts = [Counter(doc) for doc in tokenized_docs]
        self._doc_lengths = [len(doc) for doc in tokenized_docs]
        self._n_docs = len(tokenized_docs)
        self._avgdl = (sum(self._doc_lengths) / self._n_docs) if self._n_docs else 0.0

        self._df = Counter()
        for term_counts in self._doc_term_counts:
            for term in term_counts:
                self._df[term] += 1

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        # BM25 표준 idf (음수 방지를 위한 +1 스무딩 변형)
        return math.log(1 + (self._n_docs - df + 0.5) / (df + 0.5))

    def score(self, query_tokens: list[str]) -> list[float]:
        """코퍼스의 각 문서에 대한 BM25 점수 목록 (문서 순서 유지)."""
        if self._n_docs == 0:
            return []
        scores = [0.0] * self._n_docs
        query_term_counts = Counter(query_tokens)
        for term in query_term_counts:
            idf = self._idf(term)
            if idf <= 0:
                continue
            for doc_idx, term_counts in enumerate(self._doc_term_counts):
                freq = term_counts.get(term, 0)
                if freq == 0:
                    continue
                doc_len = self._doc_lengths[doc_idx]
                denom = freq + self.k1 * (1 - self.b + self.b * doc_len / (self._avgdl or 1))
                scores[doc_idx] += idf * (freq * (self.k1 + 1)) / denom
        return scores
