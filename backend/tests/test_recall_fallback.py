from __future__ import annotations

from pathlib import Path

from recall.answer.base import AnswerResult, citation_from_chunk
from recall.classify.question_type import QuestionType
from recall.fallback.self_assessment import assess_sufficiency
from recall.fallback.trigger import StubVideoRequeryClient, decide_fallback
from recall.vault.types import Chunk, ChunkLevel, DocKind, Evidence


def _chunk(
    level: ChunkLevel,
    text: str,
    *,
    start_sec: float | None = None,
    video_path: str | None = "v.mp4",
) -> Chunk:
    return Chunk(
        chunk_id=f"test#{level.value}#{text[:8]}",
        doc_path=Path("sessions/2026-07-17_1500_test.md"),
        doc_kind=DocKind.SESSION,
        level=level,
        text=text,
        date=None,
        session_title="test",
        start_sec=start_sec,
        timestamp_label="[15:01:20]" if start_sec is not None else None,
        video_path=video_path,
    )


def test_no_evidence_is_insufficient() -> None:
    answer = AnswerResult(text="기록에 없음", citations=(), grounded=False, evidence=())
    verdict = assess_sufficiency(QuestionType.CONVERSATIONAL, answer)
    assert not verdict.sufficient


def test_visual_question_without_scene_caption_is_insufficient() -> None:
    chunk = _chunk(ChunkLevel.TRANSCRIPT, "민수: 이 책 진짜 좋았어.")
    ev = Evidence(chunk=chunk, score=0.5)
    answer = AnswerResult(
        text="민수: 이 책 진짜 좋았어. (근거: ...)",
        citations=(citation_from_chunk(chunk),),
        grounded=True,
        evidence=(ev,),
    )
    verdict = assess_sufficiency(QuestionType.VISUAL, answer)
    assert not verdict.sufficient


def test_visual_question_with_explicit_no_info_caption_is_insufficient() -> None:
    chunk = _chunk(
        ChunkLevel.SCENE_CAPTION,
        "표지 문구·제목은 해상도 문제로 캡션에 기록되지 않음(향후 fallback 대상).",
        start_sec=80.0,
    )
    ev = Evidence(chunk=chunk, score=0.6)
    answer = AnswerResult(
        text=chunk.text, citations=(citation_from_chunk(chunk),), grounded=True, evidence=(ev,)
    )
    verdict = assess_sufficiency(QuestionType.VISUAL, answer)
    assert not verdict.sufficient
    assert "기록되지" in verdict.reason


def test_visual_question_with_descriptive_caption_is_sufficient() -> None:
    chunk = _chunk(ChunkLevel.SCENE_CAPTION, "빨간색 표지의 책을 들고 있다.", start_sec=80.0)
    ev = Evidence(chunk=chunk, score=0.6)
    answer = AnswerResult(
        text=chunk.text, citations=(citation_from_chunk(chunk),), grounded=True, evidence=(ev,)
    )
    verdict = assess_sufficiency(QuestionType.VISUAL, answer)
    assert verdict.sufficient


def test_conversational_question_with_low_score_is_insufficient() -> None:
    chunk = _chunk(ChunkLevel.TRANSCRIPT, "그냥 잡담", start_sec=10.0)
    ev = Evidence(chunk=chunk, score=0.001)
    answer = AnswerResult(
        text=chunk.text, citations=(citation_from_chunk(chunk),), grounded=True, evidence=(ev,)
    )
    verdict = assess_sufficiency(QuestionType.CONVERSATIONAL, answer)
    assert not verdict.sufficient


def test_decide_fallback_not_triggered_when_sufficient() -> None:
    chunk = _chunk(ChunkLevel.TRANSCRIPT, "숙소는 하루에 15만원 넘지 않게 잡자.", start_sec=50.0)
    ev = Evidence(chunk=chunk, score=0.5)
    answer = AnswerResult(
        text=chunk.text, citations=(citation_from_chunk(chunk),), grounded=True, evidence=(ev,)
    )
    decision = decide_fallback(QuestionType.CONVERSATIONAL, answer)
    assert not decision.triggered
    assert decision.clip_targets == ()


def test_decide_fallback_triggered_builds_clip_targets_from_evidence() -> None:
    chunk = _chunk(
        ChunkLevel.SCENE_CAPTION,
        "표지 문구·제목은 캡션에 기록되지 않음.",
        start_sec=80.0,
        video_path="testdata/videos/test_session_A_20260717.mp4",
    )
    ev = Evidence(chunk=chunk, score=0.6)
    answer = AnswerResult(
        text=chunk.text, citations=(citation_from_chunk(chunk),), grounded=True, evidence=(ev,)
    )
    decision = decide_fallback(QuestionType.VISUAL, answer)
    assert decision.triggered
    assert len(decision.clip_targets) == 1
    target = decision.clip_targets[0]
    assert target.video_path == "testdata/videos/test_session_A_20260717.mp4"
    assert target.start_sec < 80.0 < target.end_sec


def test_stub_video_requery_client_never_fabricates_an_answer() -> None:
    """스텁은 실제 답을 만들어내면 안 된다 — 무엇이 필요한지만 설명해야 한다."""
    client = StubVideoRequeryClient()
    chunk = _chunk(ChunkLevel.SCENE_CAPTION, "표지 문구는 기록되지 않음.", start_sec=80.0)
    ev = Evidence(chunk=chunk, score=0.6)
    answer = AnswerResult(
        text=chunk.text, citations=(citation_from_chunk(chunk),), grounded=True, evidence=(ev,)
    )
    decision = decide_fallback(QuestionType.VISUAL, answer)
    result = client.requery("책 제목이 뭐야?", decision.clip_targets)
    # 스텁은 절대 근거를 만들어냈다고 주장하지 않는다.
    assert result.grounded is False
    assert "미수행" in result.answer_text
