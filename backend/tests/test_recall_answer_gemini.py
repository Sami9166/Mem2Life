"""Gemini 답변 생성기 — 지어냄 방지 3중 방어와 폴백 동작 검증.

실제 Gemini 네트워크는 절대 타지 않는다(`test_recall_gemini_requery.py`와 동일
원칙) — `.models.generate_content`를 가진 fake client를 주입해 프롬프트 구성 /
sentinel 해석 / 실패 시 템플릿 폴백만 검증한다.

특히 중요한 것은 `[근거부족]` 경로다: 모델이 "기록에 없다"는 문장을 만들어도
`AnswerResult.grounded`가 True로 남으면 `fallback/self_assessment.py`가 근거가
충분하다고 판정해 영상 재조회가 아예 트리거되지 않는다. 그 배선이 실제로 이어져
있는지를 파이프라인 레벨에서까지 확인한다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from recall.answer.factory import available_providers, get_answer_generator
from recall.answer.gemini_generator import (
    NO_ANSWER_IN_EVIDENCE_TEXT,
    GeminiAnswerGenerator,
    GeminiCredentialError,
    build_prompt,
    split_sentinel,
)
from recall.answer.template_generator import NO_EVIDENCE_TEXT, TemplateAnswerGenerator
from recall.fallback.trigger import StubVideoRequeryClient
from recall.pipeline import RecallPipeline
from recall.vault.types import Chunk, ChunkLevel, DocKind, Evidence


class _FakeModels:
    def __init__(
        self,
        *,
        text: str | None = None,
        raise_exc: Exception | None = None,
        finish_reason: str | None = None,
    ) -> None:
        self.text = text
        self.raise_exc = raise_exc
        self.finish_reason = finish_reason
        self.captured: dict[str, object] = {}
        self.call_count = 0

    def generate_content(self, *, model: str, contents: list[object], config: object = None):  # noqa: ANN201
        self.call_count += 1
        self.captured["model"] = model
        self.captured["contents"] = contents
        self.captured["config"] = config
        if self.raise_exc is not None:
            raise self.raise_exc
        candidates = None
        if self.finish_reason is not None:
            candidates = [SimpleNamespace(finish_reason=SimpleNamespace(name=self.finish_reason))]
        return SimpleNamespace(text=self.text, candidates=candidates)


class _FakeClient:
    def __init__(self, **kwargs: object) -> None:
        self.models = _FakeModels(**kwargs)  # type: ignore[arg-type]


def _evidence(text: str, score: float, *, speaker: str | None = None) -> Evidence:
    chunk = Chunk(
        chunk_id=f"test#{text[:8]}",
        doc_path=Path("sessions/2026-07-18_1430_근황_토크.md"),
        doc_kind=DocKind.SESSION,
        level=ChunkLevel.TRANSCRIPT,
        text=text,
        date=date(2026, 7, 18),
        session_title="근황_토크",
        timestamp_label="[14:32:10]",
        speaker=speaker,
        start_sec=130.0,
        video_path="/videos/session_b.mp4",
    )
    return Evidence(chunk=chunk, score=score)


_EV = _evidence("금요일까지 발표자료 초안 좀 보내줄 수 있어?", 0.8, speaker="민수")


# -- 인증/생성 ---------------------------------------------------------------


def test_missing_api_key_raises_credential_error() -> None:
    with pytest.raises(GeminiCredentialError):
        GeminiAnswerGenerator(env={})


def test_injected_client_skips_key_check() -> None:
    gen = GeminiAnswerGenerator(client=_FakeClient(text="[답변] 응답"), env={})
    assert gen.provider_name == "gemini"


# -- 방어 1: 근거 없으면 모델을 호출조차 하지 않는다 --------------------------


def test_no_evidence_does_not_call_gemini() -> None:
    client = _FakeClient(text="[답변] 지어낸 답")
    gen = GeminiAnswerGenerator(client=client, env={})

    result = gen.generate("아무 질문", [])

    assert result.grounded is False
    assert result.text == NO_EVIDENCE_TEXT
    assert client.models.call_count == 0, "근거가 없으면 Gemini를 호출하면 안 된다(지어냄 방지 + 쿼터 절약)"


def test_zero_score_evidence_does_not_call_gemini() -> None:
    client = _FakeClient(text="[답변] 지어낸 답")
    gen = GeminiAnswerGenerator(client=client, env={})

    result = gen.generate("무관한 질문", [_evidence("전혀 무관한 문장", 0.0)])

    assert result.grounded is False
    assert client.models.call_count == 0


# -- 방어 2: sentinel 해석 ----------------------------------------------------


def test_grounded_sentinel_produces_natural_answer_with_citation() -> None:
    gen = GeminiAnswerGenerator(
        client=_FakeClient(text="[답변]\n민수가 금요일까지 발표자료 초안을 보내달라고 했어요."),
        env={},
    )

    result = gen.generate("민수가 부탁한 게 뭐였지?", [_EV])

    assert result.grounded is True
    assert "발표자료 초안을 보내달라고" in result.text
    # 인용 문구는 모델 출력이 아니라 실제 Chunk에서 만들어진다.
    assert "근황_토크" in result.text
    assert "[14:32:10]" in result.text
    assert len(result.citations) == 1
    assert result.citations[0].session_title == "근황_토크"


def test_ungrounded_sentinel_marks_answer_not_grounded() -> None:
    """근거는 검색됐지만 답을 확정할 수 없을 때 — fallback으로 넘어가야 한다."""
    gen = GeminiAnswerGenerator(
        client=_FakeClient(text="[근거부족] 전사록에 책 제목이 나오지 않습니다."),
        env={},
    )

    result = gen.generate("민수가 보여준 책 제목이 뭐였지?", [_EV])

    assert result.grounded is False, "grounded=False여야 영상 재조회 fallback이 트리거된다"
    assert NO_ANSWER_IN_EVIDENCE_TEXT.split(" —")[0] in result.text
    assert "책 제목이 나오지 않습니다" in result.text


def test_ungrounded_sentinel_without_body_still_marks_not_grounded() -> None:
    """모델이 프롬프트를 어기고 `[근거부족]`만 뱉고 설명 문장을 안 써도(body 빈 문자열)
    여전히 grounded=False여야 한다.

    이 검사가 "형식 불명확(not body)" 검사보다 먼저 오지 않으면, 빈 본문 `[근거부족]`이
    템플릿 답변(grounded=True)으로 떨어져 모델이 "답 못 함"이라 판단한 근거를 짜깁기한
    답이 fallback 없이 나간다 — 안전 의도가 정확히 뒤집힌다."""
    gen = GeminiAnswerGenerator(client=_FakeClient(text="[근거부족]"), env={})

    result = gen.generate("민수가 보여준 책 제목이 뭐였지?", [_EV])

    assert result.grounded is False, "빈 본문이어도 [근거부족]은 grounded=False로 fallback을 트리거해야 한다"
    assert NO_ANSWER_IN_EVIDENCE_TEXT.split(" —")[0] in result.text


def test_sentinel_on_same_line_as_body_is_parsed() -> None:
    sentinel, body = split_sentinel("[답변] 15만원으로 정했어요.")
    assert sentinel == "답변"
    assert body == "15만원으로 정했어요."


def test_sentinel_on_separate_line_is_parsed() -> None:
    sentinel, body = split_sentinel("[근거부족]\n기록에 없습니다.")
    assert sentinel == "근거부족"
    assert body == "기록에 없습니다."


def test_unknown_format_returns_no_sentinel() -> None:
    sentinel, body = split_sentinel("그냥 아무 문장")
    assert sentinel is None
    assert body == "그냥 아무 문장"


def test_malformed_response_falls_back_to_template() -> None:
    """형식을 안 지킨 응답은 신뢰하지 않고 근거 문장을 그대로 인용하는 쪽으로 되돌린다."""
    gen = GeminiAnswerGenerator(
        client=_FakeClient(text="민수가 뭔가 부탁했던 것 같은데 아마 회의록이었을 거예요."),
        env={},
    )

    result = gen.generate("민수가 부탁한 게 뭐였지?", [_EV])

    assert result.grounded is True
    # 템플릿 답변이므로 근거 원문이 그대로 들어가고, 모델이 지어낸 "회의록"은 없다.
    assert "발표자료 초안" in result.text
    assert "회의록" not in result.text


# -- 방어 3 + 실행 시점 폴백 --------------------------------------------------


def test_api_exception_falls_back_to_template() -> None:
    gen = GeminiAnswerGenerator(client=_FakeClient(raise_exc=RuntimeError("429 quota")), env={})

    result = gen.generate("민수가 부탁한 게 뭐였지?", [_EV])

    assert result.grounded is True
    assert "발표자료 초안" in result.text


def test_max_tokens_truncation_falls_back_to_template() -> None:
    """thinking 토큰이 예산을 먼저 먹어 답이 잘린 경우 — 빈 문자열이 아니라 못 잡는 케이스."""
    gen = GeminiAnswerGenerator(
        client=_FakeClient(text="[답변] 민수가 금요일까", finish_reason="MAX_TOKENS"),
        env={},
    )

    result = gen.generate("민수가 부탁한 게 뭐였지?", [_EV])

    assert "발표자료 초안" in result.text, "잘린 답변 대신 템플릿 답변이어야 한다"


def test_empty_response_falls_back_to_template() -> None:
    gen = GeminiAnswerGenerator(client=_FakeClient(text="   "), env={})

    result = gen.generate("민수가 부탁한 게 뭐였지?", [_EV])

    assert "발표자료 초안" in result.text


def test_citations_never_come_from_model_output() -> None:
    """모델이 없는 세션을 지어내도 인용에는 실제 검색된 근거만 남아야 한다."""
    gen = GeminiAnswerGenerator(
        client=_FakeClient(text="[답변] 세션 '가짜_세션' (1999-01-01)에서 확인했어요."),
        env={},
    )

    result = gen.generate("질문", [_EV])

    assert all(c.session_title == "근황_토크" for c in result.citations)
    assert all(c.doc_path.endswith("근황_토크.md") for c in result.citations)


# -- 프롬프트 구성 -----------------------------------------------------------


def test_prompt_includes_evidence_text_and_source_label() -> None:
    prompt = build_prompt("민수가 부탁한 게 뭐였지?", [_EV])

    assert "민수가 부탁한 게 뭐였지?" in prompt
    assert "발표자료 초안" in prompt
    assert "근황_토크" in prompt  # 출처(citation_label)도 모델에게 보여준다
    assert "[답변]" in prompt and "[근거부족]" in prompt


def test_only_positive_evidence_is_sent_to_model() -> None:
    client = _FakeClient(text="[답변] 답")
    gen = GeminiAnswerGenerator(client=client, env={})

    gen.generate("질문", [_EV, _evidence("점수 0인 무관한 문장", 0.0)])

    prompt = client.models.captured["contents"][0]  # type: ignore[index]
    assert "발표자료 초안" in prompt
    assert "무관한 문장" not in prompt


def test_max_evidence_caps_prompt_size() -> None:
    client = _FakeClient(text="[답변] 답")
    gen = GeminiAnswerGenerator(client=client, env={}, max_evidence=2)

    many = [_evidence(f"근거 문장 {i}", 0.9 - i * 0.01) for i in range(6)]
    result = gen.generate("질문", many)

    prompt = client.models.captured["contents"][0]  # type: ignore[index]
    assert "근거 문장 0" in prompt and "근거 문장 1" in prompt
    assert "근거 문장 5" not in prompt
    assert len(result.citations) == 2
    # evidence 자체는 자기평가(self_assessment)가 쓰므로 잘라내지 않고 전부 보존한다.
    assert len(result.evidence) == 6


# -- factory (생성 시점 폴백) -------------------------------------------------


def test_factory_registers_both_providers() -> None:
    assert available_providers() == ["gemini", "template"]


def test_factory_falls_back_to_template_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert isinstance(get_answer_generator(), TemplateAnswerGenerator)


def test_factory_builds_gemini_when_api_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key")
    assert isinstance(get_answer_generator(), GeminiAnswerGenerator)


def test_factory_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="알 수 없는 답변 생성기 provider"):
        get_answer_generator("gpt-nonexistent")


# -- 파이프라인 통합: [근거부족] → 영상 재조회 fallback -----------------------


def test_pipeline_triggers_fallback_when_model_says_not_enough(mock_vault_dir: Path, tmp_path: Path) -> None:
    """모델이 [근거부족]으로 답하면 파이프라인이 영상 재조회로 넘어가야 한다."""
    pipeline = RecallPipeline(
        mock_vault_dir,
        cache_path=tmp_path / "cache.json",
        embedding_provider="hash",
        answer_generator=GeminiAnswerGenerator(
            client=_FakeClient(text="[근거부족] 전사록에 책 제목이 없습니다."),
            env={},
        ),
        video_requery_client=StubVideoRequeryClient(),
    )

    result = pipeline.answer_question(
        "아까 민수가 나한테 부탁한 거 뭐였지?", reference_date=date(2026, 7, 18)
    )

    assert result.draft_answer.grounded is False
    assert result.fallback.triggered is True
    assert "기록에 없음" in result.final_text


def test_pipeline_uses_model_answer_when_grounded(mock_vault_dir: Path, tmp_path: Path) -> None:
    pipeline = RecallPipeline(
        mock_vault_dir,
        cache_path=tmp_path / "cache.json",
        embedding_provider="hash",
        answer_generator=GeminiAnswerGenerator(
            client=_FakeClient(text="[답변] 금요일까지 발표자료 초안을 보내달라고 했어요."),
            env={},
        ),
        video_requery_client=StubVideoRequeryClient(),
    )

    result = pipeline.answer_question(
        "아까 민수가 나한테 부탁한 거 뭐였지?", reference_date=date(2026, 7, 18)
    )

    assert result.fallback.triggered is False
    assert "발표자료 초안을 보내달라고" in result.final_text
    assert result.citations, "자연어 답변에도 근거 인용은 반드시 남아야 한다"
