"""API 키 없이 동작하는 기본(offline) 답변 생성기.

검색된 근거 청크의 문장을 그대로 이어붙여 답을 "조립"한다 — 실제 LLM
(Claude/GPT)만큼 자연스럽진 않지만, 검색된 근거 밖의 어떤 사실도
덧붙이지 않는다는 점이 중요하다("답을 지어내지 않는다" 원칙을 코드로
강제하는 가장 단순한 방법). 자연스러운 문장이 필요하면 `AnswerGenerator`
Protocol을 만족하는 LLM 기반 구현체로 교체한다(`factory.py`).

근거가 아예 없거나(검색 결과 0건) 최상위 점수가 0 이하(질문과 무관)면
사실을 지어내는 대신 "기록에 없음"을 명시한다 — CLAUDE.md 핵심 원칙.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..vault.types import Evidence
from .base import AnswerResult, citation_from_chunk

NO_EVIDENCE_TEXT = "기록에 없음 — 질문과 관련된 근거를 위키에서 찾지 못했습니다."


@dataclass
class TemplateAnswerGenerator:
    """provider 이름: `"template"` (기본값, API 키 불필요)."""

    max_facts: int = 3

    def generate(self, question: str, evidence: Sequence[Evidence]) -> AnswerResult:
        positive = [e for e in evidence if e.score > 0]
        if not positive:
            return AnswerResult(
                text=NO_EVIDENCE_TEXT,
                citations=(),
                grounded=False,
                evidence=tuple(evidence),
            )

        top_evidence = positive[: self.max_facts]
        citations = tuple(citation_from_chunk(e.chunk) for e in top_evidence)

        # 근거 문장을 순서 유지 + 중복 제거하며 이어붙인다.
        seen: dict[str, None] = {}
        for e in top_evidence:
            seen.setdefault(e.chunk.text.strip(), None)
        body = " ".join(seen.keys())

        citation_labels: dict[str, None] = {}
        for c in citations:
            citation_labels.setdefault(c.label, None)
        citation_note = " / ".join(citation_labels.keys())

        text = f"{body} (근거: {citation_note})"
        return AnswerResult(
            text=text, citations=citations, grounded=True, evidence=tuple(evidence), body=body
        )
