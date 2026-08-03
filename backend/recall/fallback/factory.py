"""영상 재조회 provider 선택 팩토리 (`ingest/stt/factory.py`와 동일한 패턴).

fallback 영상 재조회는 표준 API로 영상을 통째로 받는 계열이 Gemini뿐이라
Gemini에 고정돼 있다(기술조사_의사결정.md 조사 4). 그래도 인터페이스는
교체 가능하게 두어(`VideoRequeryClient` Protocol) 다른 provider가 생기면
`_PROVIDERS`에 한 줄만 추가하면 되게 한다.

STT의 RTZR 폴백과 동일한 원칙 — API 키 없이도 파이프라인이 끝까지 돌아야
한다 — 을 지키기 위해 provider "gemini"는 생성 시점 폴백을 둔다:

    GEMINI_API_KEY/GOOGLE_API_KEY가 없으면 `GeminiVideoRequeryClient`를 만들지
    않고 곧바로 `StubVideoRequeryClient`(재조회 미수행, 지어내지 않음)를
    반환한다. 이 덕분에 테스트/CI는 키 없이도 결정적으로 스텁을 고른다.

다만 STT와 다른 점: fallback 재조회는 이미 "1차 답변이 불충분하다"는 신호라
스텁으로 대체해도 실제 답을 줄 수 없다. 그래서 스텁은 답을 지어내는 대신
`grounded=False`로 "기록에 없음"류 정직한 실패를 돌려주고, 파이프라인이 그걸
그대로 사용자에게 표시한다(`pipeline.py` 참고).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from .gemini_requery import DEFAULT_MODEL, GeminiCredentialError, GeminiVideoRequeryClient
from .trigger import StubVideoRequeryClient, VideoRequeryClient

_ENV_KEYS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


def _gemini_credentials_present() -> bool:
    return any(os.environ.get(key) for key in _ENV_KEYS)


def _build_gemini_client() -> VideoRequeryClient:
    """Gemini 재조회 클라이언트를 만든다. 인증 정보가 없으면 스텁으로 대체한다.

    `.env` 로드 책임은 이 함수가 아니라 호출부(CLI 진입점)에 있다 —
    테스트에서는 `.env`를 로드하지 않으므로 이 분기가 결정적으로 스텁을
    반환해 네트워크 호출 없이 통과한다.
    """
    if not _gemini_credentials_present():
        print(
            "[안내] GEMINI_API_KEY가 설정돼 있지 않아 영상 재조회 스텁으로 동작합니다 "
            "(fallback 시 실제 재조회 없이 '기록에 없음'으로 표시). 실제 재조회가 "
            "필요하면 backend/.env에 GEMINI_API_KEY를 설정하세요 (.env.example 참고).",
            file=sys.stderr,
        )
        return StubVideoRequeryClient()
    try:
        return GeminiVideoRequeryClient(model=DEFAULT_MODEL)
    except GeminiCredentialError as exc:
        print(f"[안내] {exc} 스텁으로 대체합니다.", file=sys.stderr)
        return StubVideoRequeryClient()


_PROVIDERS: dict[str, Callable[[], VideoRequeryClient]] = {
    "gemini": _build_gemini_client,
    "stub": StubVideoRequeryClient,
}

DEFAULT_PROVIDER = "gemini"  # 기술조사_의사결정.md 조사 4: 영상 입력은 Gemini 고정


def available_providers() -> list[str]:
    """등록된 영상 재조회 provider 이름 목록(정렬됨)."""
    return sorted(_PROVIDERS)


def get_video_requery_client(provider: str = DEFAULT_PROVIDER) -> VideoRequeryClient:
    """provider 이름(대소문자 무관)으로 영상 재조회 클라이언트를 만든다.

    Raises:
        ValueError: 등록되지 않은 provider 이름일 때.
    """
    try:
        build_fn = _PROVIDERS[provider.lower()]
    except KeyError as exc:
        available = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"알 수 없는 영상 재조회 provider: {provider!r} (사용 가능: {available})") from exc
    return build_fn()
