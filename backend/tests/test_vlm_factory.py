"""`ingest.vlm.factory` provider 선택 테스트 (`test_stt_stubs.py`와 동일한 패턴)."""

from __future__ import annotations

import pytest

from ingest.vlm.factory import (
    available_caption_providers,
    available_summary_providers,
    get_llm_summarizer,
    get_vlm_captioner,
)
from ingest.vlm.gemini_client import GeminiLLMSummarizer, GeminiVLMCaptioner
from ingest.vlm.stub import PlaceholderLLMSummarizer, PlaceholderVLMCaptioner


def test_available_providers_list_gemini() -> None:
    assert available_caption_providers() == ["gemini"]
    assert available_summary_providers() == ["gemini"]


def test_get_vlm_captioner_falls_back_to_placeholder_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # conftest.py의 autouse 픽스처가 이미 지워두지만, 이 테스트의 의도를
    # 명시적으로 드러내기 위해 다시 한 번 확실히 지운다.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    captioner = get_vlm_captioner("gemini")

    assert isinstance(captioner, PlaceholderVLMCaptioner)


def test_get_llm_summarizer_falls_back_to_placeholder_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    summarizer = get_llm_summarizer("gemini")

    assert isinstance(summarizer, PlaceholderLLMSummarizer)


def test_get_vlm_captioner_selects_real_gemini_client_when_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """인증 정보가 있으면 팩토리가 플레이스홀더가 아닌 실제 GeminiVLMCaptioner
    생성을 시도해야 한다. `genai.Client()` 생성 자체는 네트워크 호출이 없으므로
    (실제 요청은 `.caption_keyframes()` 호출 시점에만 나간다) 이 테스트는 별도
    모킹 없이도 네트워크를 타지 않는다(요청/응답 로직은
    test_vlm_gemini_client.py에서 검증)."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-construction-only")

    captioner = get_vlm_captioner("gemini")

    assert isinstance(captioner, GeminiVLMCaptioner)


def test_get_llm_summarizer_selects_real_gemini_client_when_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-construction-only")

    summarizer = get_llm_summarizer("gemini")

    assert isinstance(summarizer, GeminiLLMSummarizer)


def test_get_vlm_captioner_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="알 수 없는"):
        get_vlm_captioner("unknown-provider")


def test_get_llm_summarizer_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="알 수 없는"):
        get_llm_summarizer("unknown-provider")


def test_get_vlm_captioner_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert isinstance(get_vlm_captioner("GEMINI"), PlaceholderVLMCaptioner)
