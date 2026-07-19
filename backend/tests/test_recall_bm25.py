from __future__ import annotations

from recall.index.bm25 import BM25Index
from recall.index.tokenize import tokenize


def test_bm25_ranks_exact_keyword_match_higher() -> None:
    docs = [
        "제주도 여행 숙소 예산은 15만원",
        "충전기는 책상 서랍에 넣었다",
        "발표자료 초안을 금요일까지 보낸다",
    ]
    index = BM25Index()
    index.fit([tokenize(d) for d in docs])
    scores = index.score(tokenize("숙소 예산 얼마"))
    assert scores[0] > scores[1]
    assert scores[0] > scores[2]


def test_bm25_handles_korean_particle_variation_via_bigrams() -> None:
    """조사가 붙어도(예: '민수가' vs '민수는') 바이그램 덕분에 어느 정도 매칭돼야 한다."""
    docs = ["민수가 보조배터리를 빌려달라고 했다", "오늘 날씨가 맑다"]
    index = BM25Index()
    index.fit([tokenize(d) for d in docs])
    scores = index.score(tokenize("민수는 무엇을 빌려달라고 했나"))
    assert scores[0] > scores[1]


def test_bm25_empty_corpus_returns_empty_scores() -> None:
    index = BM25Index()
    index.fit([])
    assert index.score(tokenize("아무 질문")) == []
