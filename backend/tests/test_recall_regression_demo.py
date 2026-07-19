"""데모_시나리오.md 2막(Q1·Q2·Q3·예비)을 그대로 회귀 테스트로 고정한다.

이 파일이 통과한다는 것은 곧:
  - 하이브리드(BM25+벡터) 인덱싱이 모의 볼트 전체에 대해 동작하고
  - coarse-to-fine 검색(daily→session→transcript/caption)이 올바른
    세션/타임스탬프를 근거로 뽑아내며
  - 질문 분류 + 자기평가 + fallback 트리거 판정이 설계대로 동작하고
  - Q3에서는 "책 제목을 지어내지 않고" fallback으로 넘어간다
는 것을 보장한다는 뜻이다. 데모 리허설 전 회귀 검증용으로 유지한다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from recall.classify.question_type import QuestionType
from recall.pipeline import RecallPipeline


@pytest.fixture(scope="module")
def pipeline(mock_vault_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> RecallPipeline:
    cache_path = tmp_path_factory.mktemp("recall_regression_cache") / "cache.json"
    return RecallPipeline(mock_vault_dir, cache_path=cache_path)


# ---------------------------------------------------------------------------
# Q1 — "아까 민수가 나한테 부탁한 거 뭐였지?" (방금 기록 즉답, 세션 B)
# ---------------------------------------------------------------------------
def test_q1_recent_request_from_session_b(pipeline: RecallPipeline) -> None:
    result = pipeline.answer_question(
        "아까 민수가 나한테 부탁한 거 뭐였지?", reference_date=date(2026, 7, 18)
    )

    assert result.question_type is QuestionType.CONVERSATIONAL
    assert result.fallback.triggered is False
    assert "발표자료" in result.final_text
    assert "금요일" in result.final_text

    # 세션 B(2026-07-18, 근황_토크)를 근거로 인용해야 하고, 타임스탬프가 있어야 한다.
    assert result.citations, "근거 인용이 비어 있으면 안 된다"
    assert any(c.date == date(2026, 7, 18) for c in result.citations)
    assert any(c.timestamp_label is not None for c in result.citations)
    assert any(c.session_title == "근황_토크" for c in result.citations)


# ---------------------------------------------------------------------------
# Q2 — "어제 민수랑 제주도 여행 얘기했을 때 숙소 예산 얼마로 정했지?" (장기 기억)
# ---------------------------------------------------------------------------
def test_q2_budget_from_session_a_yesterday(pipeline: RecallPipeline) -> None:
    result = pipeline.answer_question(
        "어제 민수랑 제주도 여행 얘기했을 때 숙소 예산 얼마로 정했지?",
        reference_date=date(2026, 7, 18),
    )

    assert result.question_type is QuestionType.CONVERSATIONAL
    assert result.fallback.triggered is False
    assert "15만원" in result.final_text
    assert "9월 12일" in result.final_text  # 요약 품질(출발일도 자연스럽게 포함)

    assert result.retrieval.resolved_date == date(2026, 7, 17)
    assert any(c.date == date(2026, 7, 17) for c in result.citations)
    assert any(c.session_title == "제주도_여행_계획" for c in result.citations)
    assert any(c.timestamp_label is not None for c in result.citations)


# ---------------------------------------------------------------------------
# Q3 — "어제 민수가 보여준 책 제목이 뭐였지?" (fallback — 시각 정보)
# ---------------------------------------------------------------------------
def test_q3_book_title_triggers_fallback_without_fabricating(pipeline: RecallPipeline) -> None:
    result = pipeline.answer_question(
        "어제 민수가 보여준 책 제목이 뭐였지?", reference_date=date(2026, 7, 18)
    )

    assert result.question_type is QuestionType.VISUAL
    assert result.fallback.triggered is True
    assert "기록에 없음" in result.final_text

    # 지어낸 책 제목이 없어야 한다 — 모의 볼트 어디에도 실제 제목 문자열이
    # 없으므로, 답변이 그럴듯한 책 제목처럼 보이는 『』 인용부호를 포함하면
    # 안 된다(있다면 지어낸 것이다).
    assert "『" not in result.final_text and "」" not in result.final_text

    # fallback 대상 영상 구간은 세션 A(어제) 영상이어야 하고, 재조회는
    # 아직 스텁이라는 점이 stub 결과 문구에 드러나야 한다.
    assert result.fallback.clip_targets
    assert all("test_session_A" in target.video_path for target in result.fallback.clip_targets)
    assert result.fallback_stub_result is not None
    assert "구현되지 않았습니다" in result.fallback_stub_result


def test_q3_evidence_includes_scene_caption_about_the_book(pipeline: RecallPipeline) -> None:
    result = pipeline.answer_question(
        "어제 민수가 보여준 책 제목이 뭐였지?", reference_date=date(2026, 7, 18)
    )
    caption_texts = [
        ev.chunk.text for ev in result.draft_answer.evidence if ev.chunk.level.value == "scene_caption"
    ]
    assert any("책" in t for t in caption_texts)
    assert any("기록되지 않" in t for t in caption_texts)


# ---------------------------------------------------------------------------
# 예비 질문 1 — "충전기 어디에 넣었지?"
# ---------------------------------------------------------------------------
def test_backup_q_charger_location(pipeline: RecallPipeline) -> None:
    result = pipeline.answer_question("충전기 어디에 넣었지?", reference_date=date(2026, 7, 18))

    assert result.fallback.triggered is False
    assert "서랍" in result.final_text
    assert any(c.session_title == "제주도_여행_계획" for c in result.citations)
    assert any(c.timestamp_label is not None for c in result.citations)


# ---------------------------------------------------------------------------
# 예비 질문 2 — "민수한테 빌려주기로 한 거 뭐지?"
# ---------------------------------------------------------------------------
def test_backup_q_borrow_promise_is_battery_not_slides(pipeline: RecallPipeline) -> None:
    result = pipeline.answer_question("민수한테 빌려주기로 한 거 뭐지?", reference_date=date(2026, 7, 18))

    assert result.fallback.triggered is False
    assert "보조배터리" in result.final_text
    # 세션 B의 "발표자료 초안"(빌리는 게 아니라 보내는 부탁)과 헷갈리면 안 된다.
    assert "발표자료" not in result.final_text
    assert any(c.timestamp_label is not None for c in result.citations)
