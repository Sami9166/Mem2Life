"""STT provider 선택 팩토리.

`SpeechToTextClient` 인터페이스를 만족하는 구현체를 provider 이름으로
선택한다. RTZR을 1순위, Clova를 2순위로 채택했다(기술조사_의사결정.md).

RTZR은 실제 API 클라이언트(`rtzr_client.RTZRClient`)로 연동됐다. 다만
CLAUDE.md의 핵심 원칙 — "영상 파일 하나만으로 API 키 없이 전체 파이프라인이
끝까지 실행돼야 한다" — 를 지키기 위해 RTZR 폴백을 두 단계로 나눠뒀다:

    1. 생성 시점 폴백 (이 모듈의 책임): `backend/.env`에 RTZR 인증 정보
       (RTZR_CLIENT_ID/RTZR_CLIENT_SECRET)가 아예 없으면, 클라이언트를
       만들려는 시도조차 하지 않고 곧바로 스텁(`rtzr_stub.RTZRStubClient`,
       화자1/화자2 더미 전사록)을 반환한다.
    2. 실행 시점 폴백 (`ingest/pipeline.py`의 책임, 여기서는 하지 않음):
       인증 정보는 있어서 실제 `RTZRClient`가 만들어졌지만 `transcribe()`
       호출 자체가 실패하면(`RTZRAPIError` — 네트워크 오류, 429/5xx 재시도
       소진, 폴링 타임아웃 등), 파이프라인이 그 세션만 같은 스텁으로 대체해
       이어간다.

두 폴백 모두 목적은 같다 — 데모/CI가 RTZR 서비스의 실제 가용성과 무관하게
끝까지 실행되도록 하는 것. 1번 폴백 덕분에 `uv run pytest`/CI는 `.env`나
실제 크레덴셜 없이도 항상 그대로 통과한다 — 테스트 환경에는 이 두
환경변수가 없기 때문이다.

Clova는 아직 실제 클라이언트가 없어 스텁만 등록돼 있다(향후 작업).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from .base import SpeechToTextClient
from .clova_stub import ClovaStubClient
from .rtzr_client import RTZRClient, RTZRCredentialError
from .rtzr_stub import RTZRStubClient

_ENV_KEY_CLIENT_ID = "RTZR_CLIENT_ID"
_ENV_KEY_CLIENT_SECRET = "RTZR_CLIENT_SECRET"


def _rtzr_credentials_present() -> bool:
    return bool(os.environ.get(_ENV_KEY_CLIENT_ID)) and bool(os.environ.get(_ENV_KEY_CLIENT_SECRET))


def _build_rtzr_client() -> SpeechToTextClient:
    """RTZR 실제 클라이언트를 만든다. 인증 정보가 없으면 스텁으로 대체한다.

    `.env` 로드는 이 함수의 책임이 아니다(호출부인 CLI 진입점에서 이미
    `python-dotenv`로 로드된 상태여야 `os.environ`에 값이 들어있다) —
    테스트에서는 `.env`를 로드하지 않으므로 이 분기가 결정적으로 스텁을
    반환해 네트워크 호출 없이 통과한다.
    """
    if not _rtzr_credentials_present():
        print(
            "[안내] RTZR_CLIENT_ID/RTZR_CLIENT_SECRET이 설정돼 있지 않아 "
            "RTZR 스텁(화자1/화자2 더미 전사록)으로 동작합니다. 실제 STT가 "
            "필요하면 backend/.env에 인증 정보를 설정하세요 (.env.example 참고).",
            file=sys.stderr,
        )
        return RTZRStubClient()
    try:
        return RTZRClient()
    except RTZRCredentialError as exc:
        # 환경변수가 비어있지 않은 문자열로 존재하긴 하지만(예: 공백) 그 외
        # 이유로 RTZRClient 생성자가 거부하는 극히 드문 경우의 방어적 폴백.
        print(f"[안내] {exc} 스텁으로 대체합니다.", file=sys.stderr)
        return RTZRStubClient()


_PROVIDERS: dict[str, Callable[[], SpeechToTextClient]] = {
    "rtzr": _build_rtzr_client,
    "clova": ClovaStubClient,
}

DEFAULT_PROVIDER = "rtzr"  # 기술조사_의사결정.md 기준 1순위


def available_providers() -> list[str]:
    """등록된 STT provider 이름 목록(정렬됨).

    CLI의 `--stt` 선택지가 여기서 파생돼야 provider가 추가/개명될 때
    `_PROVIDERS`와 CLI의 `choices`가 서로 어긋나지 않는다.
    """
    return sorted(_PROVIDERS)


def get_stt_client(provider: str = DEFAULT_PROVIDER) -> SpeechToTextClient:
    """provider 이름(대소문자 무관)으로 STT 클라이언트 인스턴스를 만든다.

    Raises:
        ValueError: 등록되지 않은 provider 이름일 때.
    """
    try:
        build_fn = _PROVIDERS[provider.lower()]
    except KeyError as exc:
        available = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"알 수 없는 STT provider: {provider!r} (사용 가능: {available})") from exc
    return build_fn()
