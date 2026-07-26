"""Gemini 영상 재조회 클라이언트 + factory + 파이프라인 승격 테스트.

실제 Gemini 네트워크는 절대 타지 않는다 — `.models.generate_content`를 가진
fake client를 주입해 요청 구성/응답 해석/에러 처리 로직만 검증한다. 실제
API 연동은 `test_recall_gemini_requery_live.py`(GEMINI_LIVE_TEST=1)에서 별도로.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from recall.answer.base import format_mmss
from recall.fallback.factory import (
    available_providers,
    get_video_requery_client,
)
from recall.fallback.gemini_requery import GeminiCredentialError, GeminiVideoRequeryClient
from recall.fallback.trigger import (
    StubVideoRequeryClient,
    VideoClipTarget,
    VideoRequeryResult,
)
from recall.pipeline import RecallPipeline


class _FakeModels:
    def __init__(self, *, text: str | None = None, raise_exc: Exception | None = None) -> None:
        self.text = text
        self.raise_exc = raise_exc
        self.captured: dict[str, object] = {}

    def generate_content(self, *, model: str, contents: list[object]):  # noqa: ANN201
        self.captured["model"] = model
        self.captured["contents"] = contents
        if self.raise_exc is not None:
            raise self.raise_exc
        return SimpleNamespace(text=self.text)


class _FakeClient:
    def __init__(self, *, text: str | None = None, raise_exc: Exception | None = None) -> None:
        self.models = _FakeModels(text=text, raise_exc=raise_exc)


def _clip(video: Path, start: float = 0.5, end: float = 2.0) -> VideoClipTarget:
    return VideoClipTarget(
        video_path=str(video), start_sec=start, end_sec=end, session_title="제주도_여행_계획"
    )


# -- 인증/생성 ---------------------------------------------------------------


def test_missing_api_key_raises_credential_error() -> None:
    with pytest.raises(GeminiCredentialError):
        GeminiVideoRequeryClient(env={})


def test_api_key_from_env_is_accepted() -> None:
    # 실제 genai.Client는 dummy 키로도 생성 자체는 성공한다(호출 시에만 검증).
    client = GeminiVideoRequeryClient(env={"GEMINI_API_KEY": "dummy-key"})
    assert client.provider_name == "gemini"


def test_injected_client_skips_key_check() -> None:
    client = GeminiVideoRequeryClient(client=_FakeClient(text="[확인불가] 안 보임"), env={})
    assert client.provider_name == "gemini"


# -- 응답 해석 ---------------------------------------------------------------


def test_grounded_response_is_promoted(dummy_video: Path) -> None:
    fake = _FakeClient(text="[확인됨] 빨간 표지의 책입니다. video@01:22")
    client = GeminiVideoRequeryClient(client=fake)
    result = client.requery("민수가 든 책 표지 색은?", [_clip(dummy_video)])
    assert isinstance(result, VideoRequeryResult)
    assert result.grounded is True
    assert "빨간 표지" in result.answer_text
    assert "video@" in result.answer_text
    assert result.clips_used


def test_not_found_response_is_honest(dummy_video: Path) -> None:
    fake = _FakeClient(text="[확인불가] 표지 글자가 흐릿해 제목을 읽을 수 없습니다.")
    client = GeminiVideoRequeryClient(client=fake)
    result = client.requery("책 제목이 뭐야?", [_clip(dummy_video)])
    assert result.grounded is False
    assert "확인불가" in result.answer_text


def test_unstructured_response_defaults_to_not_grounded(dummy_video: Path) -> None:
    # sentinel 없이 그럴듯한 문장만 오면, 지어냄 방지를 위해 미근거로 처리한다.
    fake = _FakeClient(text="그 책은 아마 소설책 같아요.")
    client = GeminiVideoRequeryClient(client=fake)
    result = client.requery("책 제목?", [_clip(dummy_video)])
    assert result.grounded is False


def test_empty_response_is_failure(dummy_video: Path) -> None:
    client = GeminiVideoRequeryClient(client=_FakeClient(text=""))
    result = client.requery("책 제목?", [_clip(dummy_video)])
    assert result.grounded is False
    assert "실패" in result.answer_text


# -- 요청 구성 (프롬프트/영상 Part) ------------------------------------------


def test_request_includes_video_parts_and_timestamped_prompt(dummy_video: Path) -> None:
    fake = _FakeClient(text="[확인불가] x")
    client = GeminiVideoRequeryClient(client=fake)
    client.requery("질문", [_clip(dummy_video, 60.0, 90.0)])

    contents = fake.models.captured["contents"]
    assert isinstance(contents, list)
    prompt = contents[0]
    assert isinstance(prompt, str)
    # 근거 타임스탬프 원칙: 프롬프트가 video@mm:ss 인용을 지시하고 클립 구간을
    # mm:ss로 라벨링해야 한다.
    assert "video@mm:ss" in prompt
    assert format_mmss(60.0) in prompt  # "01:00"
    # 최소 한 개의 영상 Part(비-문자열)가 포함돼야 한다.
    non_text = [p for p in contents if not isinstance(p, str)]
    assert len(non_text) >= 1


def test_max_clips_limits_number_of_videos(dummy_video: Path) -> None:
    fake = _FakeClient(text="[확인불가] x")
    client = GeminiVideoRequeryClient(client=fake, max_clips=1)
    clips = [_clip(dummy_video, 0.0, 1.0), _clip(dummy_video, 1.0, 2.0), _clip(dummy_video, 2.0, 3.0)]
    result = client.requery("질문", clips)
    non_text = [p for p in fake.models.captured["contents"] if not isinstance(p, str)]
    assert len(non_text) == 1
    assert len(result.clips_used) == 1


# -- 실패 처리 ---------------------------------------------------------------


def test_no_clips_returns_honest_failure() -> None:
    client = GeminiVideoRequeryClient(client=_FakeClient(text="[확인됨] ..."))
    result = client.requery("질문", [])
    assert result.grounded is False
    assert "찾지 못했습니다" in result.answer_text


def test_missing_source_video_is_reported_not_fabricated(tmp_path: Path) -> None:
    fake = _FakeClient(text="[확인됨] 지어낸 답")  # 호출되면 안 됨
    client = GeminiVideoRequeryClient(client=fake)
    missing = VideoClipTarget(
        video_path=str(tmp_path / "does_not_exist.mp4"),
        start_sec=0.0,
        end_sec=5.0,
        session_title="X",
    )
    result = client.requery("질문", [missing])
    assert result.grounded is False
    assert "준비하지 못했습니다" in result.answer_text
    assert result.error is not None
    # 원본이 없으면 Gemini를 아예 호출하지 않는다(지어낸 답 승격 방지).
    assert "contents" not in fake.models.captured


def test_api_error_returns_honest_failure(dummy_video: Path) -> None:
    fake = _FakeClient(raise_exc=RuntimeError("503 backend unavailable"))
    client = GeminiVideoRequeryClient(client=fake)
    result = client.requery("질문", [_clip(dummy_video)])
    assert result.grounded is False
    assert "실패" in result.answer_text
    assert result.error is not None and "503" in result.error


def test_oversize_clip_is_excluded(dummy_video: Path) -> None:
    fake = _FakeClient(text="[확인됨] ...")
    client = GeminiVideoRequeryClient(client=fake, max_inline_bytes=1)  # 1바이트 한계
    result = client.requery("질문", [_clip(dummy_video)])
    assert result.grounded is False
    assert "준비하지 못했습니다" in result.answer_text


def test_relative_video_path_resolved_against_video_root(dummy_video: Path) -> None:
    fake = _FakeClient(text="[확인됨] video@00:01")
    client = GeminiVideoRequeryClient(client=fake, video_root=dummy_video.parent)
    rel = VideoClipTarget(video_path=dummy_video.name, start_sec=0.0, end_sec=1.0, session_title="X")
    result = client.requery("질문", [rel])
    assert result.grounded is True


# -- factory -----------------------------------------------------------------


def test_factory_falls_back_to_stub_without_key() -> None:
    # conftest autouse가 GEMINI_API_KEY를 비우므로 스텁이 나와야 한다.
    client = get_video_requery_client("gemini")
    assert isinstance(client, StubVideoRequeryClient)


def test_factory_stub_provider() -> None:
    assert isinstance(get_video_requery_client("stub"), StubVideoRequeryClient)


def test_factory_unknown_provider_raises() -> None:
    with pytest.raises(ValueError):
        get_video_requery_client("nope")


def test_available_providers_lists_both() -> None:
    assert available_providers() == ["gemini", "stub"]


# -- 파이프라인 승격 통합 -----------------------------------------------------


class _GroundedFake:
    """영상에서 근거를 찾은 것으로 응답하는 재조회 클라이언트(주입용)."""

    def requery(self, question, clips):  # noqa: ANN001, ANN201
        return VideoRequeryResult(
            answer_text="영상에서 확인: 빨간 표지의 책. video@01:22",
            grounded=True,
            clips_used=tuple(clips),
        )


def test_pipeline_promotes_grounded_requery_to_final_text(mock_vault_dir: Path) -> None:
    pipeline = RecallPipeline(mock_vault_dir, video_requery_client=_GroundedFake())
    result = pipeline.answer_question(
        "어제 민수가 보여준 책 제목이 뭐였지?", reference_date=date(2026, 7, 18)
    )
    assert result.fallback.triggered is True
    # 영상 재조회가 근거를 찾았으니 최종 답변이 재답변으로 승격돼야 한다.
    assert "빨간 표지" in result.final_text
    assert "video@" in result.final_text
    assert result.fallback_stub_result is not None


def test_pipeline_uses_honest_failure_when_requery_not_grounded(mock_vault_dir: Path) -> None:
    # 기본(주입 없음) → factory가 키 없어 스텁을 고르고, 스텁은 미근거이므로
    # 최종 답변은 "기록에 없음"류가 되어야 한다(지어내지 않음).
    pipeline = RecallPipeline(mock_vault_dir)
    result = pipeline.answer_question(
        "어제 민수가 보여준 책 제목이 뭐였지?", reference_date=date(2026, 7, 18)
    )
    assert result.fallback.triggered is True
    assert "기록에 없음" in result.final_text
