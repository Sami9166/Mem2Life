"""질문을 "대화형"(전사록으로 답 가능) vs "시각형"(영상/이미지 정보가
필요) 으로 분류한다 — fallback 라우팅(CLAUDE.md) 1단계.

지금은 키워드 휴리스틱만 구현한다. `QuestionClassifier` Protocol 뒤에
숨겨 두었으므로, 정확도가 부족하면 LLM 분류기로 교체할 수 있다
(STT/임베딩과 동일한 교체 패턴 — `factory.py`의 provider 매핑 한 줄만
추가).

시각형 키워드는 데모 시나리오(Q3: "민수가 보여준 책 제목이 뭐였지?")를
기준으로 삼았다 — 색깔/모양/글자/표지처럼 "말하지 않고 보여준" 정보를
묻는 질문을 잡아낸다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

_VISUAL_KEYWORDS: tuple[str, ...] = (
    "제목",
    "표지",
    "색깔",
    "색상",
    "생김새",
    "생겼",
    "보여준",
    "보여줬",
    "보여 준",
    "보여 줬",
    "적혀",
    "쓰여",
    "글자",
    "그림",
    "사진",
    "입고",
    "들고 있",
    "몇 개",
    "어떻게 생",
    "브랜드",
    "로고",
    "디자인",
)


class QuestionType(StrEnum):
    # (ruff UP042 검토 결과: str(x)의 "QuestionType.CONVERSATIONAL" 형태
    # 표현에 의존하는 코드가 없고 — 직렬화는 전부 `.value`를 명시적으로
    # 쓴다(api.py/cli.py/store.py) — StrEnum으로 바꿔도 동작 차이가 없어
    # 안전하게 적용했다. DocKind/ChunkLevel(vault/types.py)도 동일 이유로 함께 바꿈.)
    CONVERSATIONAL = "conversational"  # 전사록(발화 내용)으로 답할 수 있는 질문
    VISUAL = "visual"  # 화면에 보였지만 말하지 않은 정보를 묻는 질문


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    question_type: QuestionType
    matched_keywords: tuple[str, ...]


@runtime_checkable
class QuestionClassifier(Protocol):
    def classify(self, question: str) -> ClassificationResult: ...


@dataclass
class KeywordQuestionClassifier:
    """기본(offline) 분류기 — 시각형 키워드 사전 매칭. API 키 불필요."""

    visual_keywords: tuple[str, ...] = _VISUAL_KEYWORDS

    def classify(self, question: str) -> ClassificationResult:
        matched = tuple(kw for kw in self.visual_keywords if kw in question)
        question_type = QuestionType.VISUAL if matched else QuestionType.CONVERSATIONAL
        return ClassificationResult(question_type=question_type, matched_keywords=matched)
