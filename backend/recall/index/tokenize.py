"""한국어 대화체 텍스트를 위한 가벼운 토큰화.

형태소 분석기(mecab/kiwi 등)를 붙이면 정확도가 오르겠지만 시스템 바이너리
의존성이 생겨 "노트북 로컬에서 API 키 없이 바로 동작"이라는 1단계 목표에
어긋난다. 대신 다음 두 가지로 대부분의 한국어 조사(조사가 붙어도 매칭되게)
문제를 완화한다:

1. 공백 기준 어절 분리 + 구두점 제거
2. 한글 어절은 문자 2-gram(바이그램)도 함께 토큰으로 추가
   ("민수가" → "민수가", "민수", "수가" — "민수는", "민수랑"과도 "민수" 바이그램이
   겹쳐 BM25가 매칭시킬 수 있다)

TODO: 데모 이후 정확도가 부족하면 kiwipiepy 등 순수 파이썬 형태소 분석기
도입 검토 (기술조사_의사결정.md에는 아직 명시되지 않은 항목).
"""

from __future__ import annotations

import re

_WORD_SPLIT_RE = re.compile(r"[^\w가-힣]+", re.UNICODE)


def _is_hangul(ch: str) -> bool:
    return "가" <= ch <= "힣"


def _char_ngrams(word: str, n: int = 2) -> list[str]:
    if len(word) <= n:
        return []
    return [word[i : i + n] for i in range(len(word) - n + 1)]


def tokenize(text: str) -> list[str]:
    """검색/인덱싱용 토큰 목록을 만든다 (소문자화 + 어절 + 한글 바이그램)."""
    if not text:
        return []
    words = [w for w in _WORD_SPLIT_RE.split(text.lower()) if w]
    tokens: list[str] = []
    for word in words:
        tokens.append(word)
        if any(_is_hangul(ch) for ch in word):
            tokens.extend(_char_ngrams(word, 2))
    return tokens
