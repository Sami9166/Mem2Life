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

from recall.answer.template_generator import TemplateAnswerGenerator
from recall.classify.question_type import QuestionType
from recall.fallback.trigger import StubVideoRequeryClient
from recall.pipeline import RecallPipeline
from recall.search.coarse_to_fine import coarse_to_fine_search


@pytest.fixture(scope="module")
def pipeline(mock_vault_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> RecallPipeline:
    cache_path = tmp_path_factory.mktemp("recall_regression_cache") / "cache.json"
    # video_requery_client / answer_generator를 오프라인 구현으로 명시 주입한다 —
    # 이 fixture가 module-scope라 함수별 autouse 크레덴셜 클리어(conftest.py)보다
    # 먼저 생성될 수 있고, 그 순간 실제 backend/.env의 GEMINI_API_KEY가 process
    # 환경에 남아있으면(예: 같은 세션에서 앞서 실행된 CLI e2e 테스트가
    # load_dotenv()를 호출한 경우) factory가 실제 Gemini 클라이언트를 골라
    # 이 회귀 테스트가 진짜 네트워크를 타는 사고가 난다 — 실제로 재현됨.
    # 아래 단언들은 템플릿 생성기가 근거 문장을 그대로 인용한다는 전제(예:
    # "발표자료"/"15만원"이 답변 문구에 그대로 등장)에 의존하므로 더더욱 고정이 필요하다.
    return RecallPipeline(
        mock_vault_dir,
        cache_path=cache_path,
        answer_generator=TemplateAnswerGenerator(),
        video_requery_client=StubVideoRequeryClient(),
    )


def test_highlights_do_not_crowd_out_distinct_sessions(pipeline: RecallPipeline) -> None:
    result = coarse_to_fine_search(
        pipeline.index,
        "민수 부탁",
        reference_date=date(2026, 7, 18),
        top_k_session=1,
        max_chosen_sessions=2,
    )

    assert len(result.session_evidence) == 1
    assert len(result.chosen_sessions) == 2


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

    # fallback 대상 영상 구간은 세션 A(어제) 영상이어야 하고, 이 테스트 환경엔
    # GEMINI_API_KEY가 없으므로(conftest가 비움) 재조회가 스텁으로 폴백돼
    # "미수행"이 결과 문구에 드러나야 한다(지어낸 답이 아님).
    assert result.fallback.clip_targets
    assert all("test_session_A" in target.video_path for target in result.fallback.clip_targets)
    assert result.fallback_stub_result is not None
    assert "미수행" in result.fallback_stub_result


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
