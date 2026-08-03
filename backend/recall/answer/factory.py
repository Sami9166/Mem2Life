"""답변 생성기 provider 선택 팩토리 (`ingest/vlm/factory.py`와 동일 패턴).

등록된 provider는 두 개다:

- ``"gemini"`` (기본): 검색된 근거만으로 자연스러운 한국어 답변을 생성한다.
- ``"template"``: API 키 없이 근거 문장을 그대로 이어붙이는 오프라인 생성기.

`stt/factory.py`·`ingest/vlm/factory.py`와 같은 두 단계 폴백을 따른다:

    1. 생성 시점 폴백(이 모듈의 책임): `backend/.env`에 GEMINI_API_KEY가 아예
       없으면 Gemini 클라이언트를 만들려는 시도조차 하지 않고 곧바로
       `TemplateAnswerGenerator`를 반환한다. 덕분에 `uv run pytest`/CI는 실제
       자격증명 없이도 항상 그대로 통과하고, 데모도 키 없이 끝까지 돈다.
    2. 실행 시점 폴백(`gemini_generator.py`의 책임): 키는 있지만 호출이
       실패하면(429/5xx/네트워크/형식 위반) 그 질문만 템플릿 답변으로 대체한다.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from .base import AnswerGenerator
from .gemini_generator import GeminiAnswerGenerator, GeminiCredentialError
from .template_generator import TemplateAnswerGenerator

_ENV_KEY_API_KEY = "GEMINI_API_KEY"


def _gemini_credentials_present() -> bool:
    return bool(os.environ.get(_ENV_KEY_API_KEY))


def _build_gemini_generator() -> AnswerGenerator:
    if not _gemini_credentials_present():
        print(
            "[안내] GEMINI_API_KEY가 설정돼 있지 않아 답변은 근거 문장을 그대로 이어붙이는 "
            "템플릿 생성기로 동작합니다(내용은 정확하지만 문장이 자연스럽지 않습니다). "
            "자연어 답변이 필요하면 backend/.env에 GEMINI_API_KEY를 설정하세요 "
            "(.env.example 참고).",
            file=sys.stderr,
        )
        return TemplateAnswerGenerator()
    try:
        return GeminiAnswerGenerator()
    except GeminiCredentialError as exc:
        print(f"[안내] {exc} 템플릿 생성기로 대체합니다.", file=sys.stderr)
        return TemplateAnswerGenerator()


_PROVIDERS: dict[str, Callable[[], AnswerGenerator]] = {
    "gemini": _build_gemini_generator,
    "template": TemplateAnswerGenerator,
}

DEFAULT_PROVIDER = "gemini"


def available_providers() -> list[str]:
    """등록된 답변 생성기 provider 이름 목록(정렬됨)."""
    return sorted(_PROVIDERS)


def get_answer_generator(provider: str = DEFAULT_PROVIDER) -> AnswerGenerator:
    """provider 이름(대소문자 무관)으로 답변 생성기 인스턴스를 만든다.

    Raises:
        ValueError: 등록되지 않은 provider 이름일 때.
    """
    try:
        build_fn = _PROVIDERS[provider.lower()]
    except KeyError as exc:
        available = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"알 수 없는 답변 생성기 provider: {provider!r} (사용 가능: {available})") from exc
    return build_fn()
