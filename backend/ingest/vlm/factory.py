"""VLM 캡션 / LLM 요약 provider 선택 팩토리.

`VLMCaptioner`/`LLMSummarizer` 인터페이스(`base.py`)를 만족하는 구현체를
provider 이름으로 선택한다. `stt/factory.py`와 동일한 두 단계 폴백 패턴을
따른다(`gemini_client.py` 모듈 docstring 참고):

    1. 생성 시점 폴백 (이 모듈의 책임): `backend/.env`에 GEMINI_API_KEY가
       아예 없으면, 클라이언트를 만들려는 시도조차 하지 않고 곧바로
       플레이스홀더(`stub.py`)를 반환한다.
    2. 실행 시점 폴백 (`ingest/pipeline.py`의 책임, 여기서는 하지 않음):
       인증 정보는 있어서 실제 Gemini 클라이언트가 만들어졌지만 호출 자체가
       실패하면(`GeminiAPIError`), 파이프라인이 그 세션만 같은 플레이스홀더로
       대체해 이어간다.

두 폴백 모두 목적은 같다 — 데모/CI가 Gemini 서비스의 실제 가용성과 무관하게
끝까지 실행되도록 하는 것. 1번 폴백 덕분에 `uv run pytest`/CI는 `.env`나 실제
자격증명 없이도 항상 그대로 통과한다 — 테스트 환경에는 GEMINI_API_KEY가 없기
때문이다.

지금은 "gemini" 하나만 등록돼 있다(기술조사_의사결정.md 조사4 — "인터페이스는
여전히 교체 가능하게 추상화, 지금은 그 자리에 Gemini 구현체 하나만 등록").
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from .base import LLMSummarizer, VLMCaptioner
from .gemini_client import GeminiCredentialError, GeminiLLMSummarizer, GeminiVLMCaptioner
from .stub import PlaceholderLLMSummarizer, PlaceholderVLMCaptioner

_ENV_KEY_API_KEY = "GEMINI_API_KEY"


def _gemini_credentials_present() -> bool:
    return bool(os.environ.get(_ENV_KEY_API_KEY))


def _build_gemini_captioner() -> VLMCaptioner:
    if not _gemini_credentials_present():
        print(
            "[안내] GEMINI_API_KEY가 설정돼 있지 않아 VLM 캡션은 플레이스홀더(키프레임 "
            "이미지 링크만 포함)로 동작합니다. 실제 캡션이 필요하면 backend/.env에 "
            "GEMINI_API_KEY를 설정하세요 (.env.example 참고).",
            file=sys.stderr,
        )
        return PlaceholderVLMCaptioner()
    try:
        return GeminiVLMCaptioner()
    except GeminiCredentialError as exc:
        print(f"[안내] {exc} 플레이스홀더로 대체합니다.", file=sys.stderr)
        return PlaceholderVLMCaptioner()


def _build_gemini_summarizer() -> LLMSummarizer:
    if not _gemini_credentials_present():
        print(
            "[안내] GEMINI_API_KEY가 설정돼 있지 않아 LLM 요약은 TODO 플레이스홀더로 "
            "남습니다. 실제 요약이 필요하면 backend/.env에 GEMINI_API_KEY를 설정하세요 "
            "(.env.example 참고).",
            file=sys.stderr,
        )
        return PlaceholderLLMSummarizer()
    try:
        return GeminiLLMSummarizer()
    except GeminiCredentialError as exc:
        print(f"[안내] {exc} 플레이스홀더로 대체합니다.", file=sys.stderr)
        return PlaceholderLLMSummarizer()


_CAPTION_PROVIDERS: dict[str, Callable[[], VLMCaptioner]] = {"gemini": _build_gemini_captioner}
_SUMMARY_PROVIDERS: dict[str, Callable[[], LLMSummarizer]] = {"gemini": _build_gemini_summarizer}

DEFAULT_CAPTION_PROVIDER = "gemini"
DEFAULT_SUMMARY_PROVIDER = "gemini"


def available_caption_providers() -> list[str]:
    """등록된 VLM 캡션 provider 이름 목록(정렬됨)."""
    return sorted(_CAPTION_PROVIDERS)


def available_summary_providers() -> list[str]:
    """등록된 LLM 요약 provider 이름 목록(정렬됨)."""
    return sorted(_SUMMARY_PROVIDERS)


def get_vlm_captioner(provider: str = DEFAULT_CAPTION_PROVIDER) -> VLMCaptioner:
    """provider 이름(대소문자 무관)으로 VLM 캡션 클라이언트 인스턴스를 만든다.

    Raises:
        ValueError: 등록되지 않은 provider 이름일 때.
    """
    try:
        build_fn = _CAPTION_PROVIDERS[provider.lower()]
    except KeyError as exc:
        available = ", ".join(sorted(_CAPTION_PROVIDERS))
        raise ValueError(f"알 수 없는 VLM 캡션 provider: {provider!r} (사용 가능: {available})") from exc
    return build_fn()


def get_llm_summarizer(provider: str = DEFAULT_SUMMARY_PROVIDER) -> LLMSummarizer:
    """provider 이름(대소문자 무관)으로 LLM 요약 클라이언트 인스턴스를 만든다.

    Raises:
        ValueError: 등록되지 않은 provider 이름일 때.
    """
    try:
        build_fn = _SUMMARY_PROVIDERS[provider.lower()]
    except KeyError as exc:
        available = ", ".join(sorted(_SUMMARY_PROVIDERS))
        raise ValueError(f"알 수 없는 LLM 요약 provider: {provider!r} (사용 가능: {available})") from exc
    return build_fn()
