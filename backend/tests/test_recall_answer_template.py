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


def test_factory_default_provider_is_template() -> None:
    gen = get_answer_generator()
    assert isinstance(gen, TemplateAnswerGenerator)
