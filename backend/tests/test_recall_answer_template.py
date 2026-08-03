from __future__ import annotations

from pathlib import Path

from recall.answer.factory import get_answer_generator
from recall.answer.template_generator import NO_EVIDENCE_TEXT, TemplateAnswerGenerator
from recall.vault.types import Chunk, ChunkLevel, DocKind, Evidence


def _chunk(text: str, score: float) -> Evidence:
    chunk = Chunk(
        chunk_id=f"test#{text[:8]}",
        doc_path=Path("sessions/x.md"),
        doc_kind=DocKind.SESSION,
        level=ChunkLevel.HIGHLIGHT,
        text=text,
        date=None,
        session_title="test",
        timestamp_label="[15:00:50]",
    )
    return Evidence(chunk=chunk, score=score)


def test_no_evidence_never_fabricates() -> None:
    gen = TemplateAnswerGenerator()
    result = gen.generate("아무 질문", [])
    assert result.grounded is False
    assert result.text == NO_EVIDENCE_TEXT
    assert result.citations == ()


def test_zero_or_negative_score_treated_as_no_evidence() -> None:
    gen = TemplateAnswerGenerator()
    result = gen.generate("아무 질문", [_chunk("무관한 문장", 0.0)])
    assert result.grounded is False


def test_grounded_answer_includes_evidence_text_and_citation() -> None:
    gen = TemplateAnswerGenerator()
    ev = _chunk("숙소 예산 1박 15만원 이하로 합의", 0.8)
    result = gen.generate("숙소 예산 얼마?", [ev])
    assert result.grounded is True
    assert "15만원" in result.text
    assert len(result.citations) == 1
    assert result.citations[0].timestamp_label == "[15:00:50]"


def test_factory_default_falls_back_to_template_without_api_key() -> None:
    """기본 provider는 "gemini"지만, GEMINI_API_KEY가 없으면 생성 시점에 템플릿으로
    폴백한다(conftest.py의 autouse fixture가 키를 비워 이 조건을 보장한다) —
    "API 키 없이도 끝까지 동작한다"는 1단계 목표를 이 테스트가 지킨다."""
    gen = get_answer_generator()
    assert isinstance(gen, TemplateAnswerGenerator)


def test_factory_template_provider_can_be_requested_explicitly() -> None:
    assert isinstance(get_answer_generator("template"), TemplateAnswerGenerator)
