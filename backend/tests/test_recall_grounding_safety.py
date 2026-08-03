"""무관/난센스 질문에 대해 "근거 없음" 안전장치가 실제로 동작하는지 검증한다.

배경(회귀 방지 목적): `index/store.py`의 코사인→[0,1] 변환이 예전에는
`(score + 1) / 2` 선형 이동을 썼는데, 이러면 완전히 무관한 질의(코사인 ≈ 0)도
0.5 근방으로 매핑돼 하이브리드 합산 점수가 인위적인 "바닥값"을 갖게 됐다.
그 결과 BM25 겹침이 전혀 없는(원점수 0) 질문조차 벡터 항만으로
`self_assessment.MIN_CONFIDENT_SCORE`를 넘겨 "충분한 근거가 있다"고
오판하고, 모의 볼트의 무관한 세션 내용을 근거인 것처럼 답해버리는
치명적인 버그가 있었다(CLAUDE.md "답을 지어내지 않는다" 원칙 위반).

이 파일은 그 버그의 재현 사례(코드 리뷰에서 지적된 4개 질문)를 영구
회귀 테스트로 고정한다.
"""

from __future__ import annotations

from datetime import date

import pytest

from recall.answer.template_generator import TemplateAnswerGenerator
from recall.fallback.trigger import StubVideoRequeryClient
from recall.pipeline import RecallPipeline

_TODAY = date(2026, 7, 18)


@pytest.fixture(scope="module")
def pipeline(mock_vault_dir, tmp_path_factory: pytest.TempPathFactory) -> RecallPipeline:
    cache_path = tmp_path_factory.mktemp("recall_grounding_safety_cache") / "cache.json"
    # video_requery_client / answer_generator 오프라인 구현 명시 주입 이유는
    # test_recall_regression_demo.py의 동일 패턴 주석 참고 — module-scope
    # fixture라 GEMINI_API_KEY 환경 오염에 취약하다.
    return RecallPipeline(
        mock_vault_dir,
        cache_path=cache_path,
        answer_generator=TemplateAnswerGenerator(),
        video_requery_client=StubVideoRequeryClient(),
    )


# ---------------------------------------------------------------------------
# 완전히 무관한 질문 — BM25 원점수가 모든 청크에 대해 0이어야 하고(볼트
# 어휘와 전혀 겹치지 않으므로), 벡터 항만으로는 근거로 인정되면 안 된다.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "question",
    [
        "zzz qqq xyz",
        "오뚜기 진짬뽕 맛있어?",
        "호랑이 발톱 크기가 궁금해",
    ],
)
def test_off_topic_question_is_not_grounded(pipeline: RecallPipeline, question: str) -> None:
    result = pipeline.answer_question(question, reference_date=_TODAY)

    assert result.draft_answer.grounded is False, (
        f"무관한 질문 {question!r}이 grounded=True로 잘못 판정됐다 "
        f"(top score={max((e.score for e in result.draft_answer.evidence), default=0.0):.3f})"
    )
    assert result.fallback.triggered is True
    assert "기록에 없음" in result.final_text


def test_off_topic_question_has_zero_raw_bm25_overlap(pipeline: RecallPipeline) -> None:
    """전제 확인용 — 위 케이스들이 정말로 "키워드 겹침이 전혀 없는" 질문인지
    직접 검증한다(그렇지 않다면 아래의 "BM25 원점수 0 => 벡터 단독 근거 불인정"
    규칙과는 다른 이유로 통과하는 것이므로 회귀 테스트로서 의미가 옅어진다)."""
    result = pipeline.answer_question("오뚜기 진짬뽕 맛있어?", reference_date=_TODAY)
    assert all(ev.bm25_score <= 0.0 for ev in result.draft_answer.evidence)


# ---------------------------------------------------------------------------
# 알려진 한계 — 한글 바이그램 토크나이저의 부작용
# ---------------------------------------------------------------------------
# "그래서 이제 뭐 하지" 같은 대화 이어가기용 질문은 실제로는 볼트의 어떤
# 내용과도 무관하지만, 짧은 추임새 전사록("민수: 어, 그래.")과 바이그램이
# 우연히 겹쳐(index/tokenize.py의 한글 2-gram 토큰화 때문에) BM25 원점수가
# 0보다 커진다 — 그래서 "BM25 원점수 0 => 근거 불인정" 규칙이 적용되지
# 않고 여전히 grounded=True로 판정된다.
#
# 이건 형태소 분석 없는 가벼운 토크나이저(tokenize.py 참고)의 알려진 한계다.
# 정식 수정(kiwipiepy 등 순수 파이썬 형태소 분석기 도입)은 1단계 목표
# ("API 키 없이 노트북에서 바로 동작") 범위를 벗어나므로 지금은 고치지
# 않고, 아래 xfail로 한계를 명시적으로 추적만 한다 — 언젠가 토크나이저가
# 개선돼 이 테스트가 예상외로 통과(xpass)하면 바로 알아챌 수 있다.
@pytest.mark.xfail(
    reason=(
        "알려진 한계: '그래서 이제 뭐 하지'가 짧은 추임새 전사록과 바이그램이 "
        "겹쳐 BM25 원점수가 0보다 커지고, 그래서 grounded=True로 잘못 판정된다 "
        "(tokenize.py의 한글 바이그램 토큰화 부작용, 정식 수정은 형태소 분석기 "
        "도입이 필요해 범위 밖)."
    ),
    strict=True,
)
def test_filler_phrase_bigram_false_match_known_limitation(pipeline: RecallPipeline) -> None:
    result = pipeline.answer_question("그래서 이제 뭐 하지", reference_date=_TODAY)
    assert result.draft_answer.grounded is False
