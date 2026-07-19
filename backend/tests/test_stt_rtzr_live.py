"""RTZR 실제 API를 상대로 하는 수동/선택적 스모크 테스트.

기본 `uv run pytest`에서는 절대 실행되지 않는다 — `RTZR_LIVE_TEST=1` 환경변수로
명시적으로 옵트인해야만 동작한다(과금 발생 + 실제 네트워크 호출).

    RTZR_LIVE_TEST=1 uv run pytest tests/test_stt_rtzr_live.py -v

실행 전 `backend/.env`에 RTZR_CLIENT_ID/RTZR_CLIENT_SECRET이 설정돼 있어야
한다(이 테스트가 직접 로드한다).

알려진 한계: `testdata/`에는 아직 실제 사람 음성이 담긴 오디오/영상 fixture가
없다(2인 대화 샘플 미확보 — 기술조사_의사결정.md "남은 결정·검증 항목" 참고).
그래서 이 테스트는 conftest.py의 `dummy_video`(무음 배경 + 440Hz 사인파 톤)를
사용한다 — 이는 인증→제출→폴링→완료 응답 파싱까지 API 연동 자체가 정상
동작하는지만 확인해줄 뿐, 실제 화자분리/전사 품질은 검증하지 못한다.
실제 한국어 2인 대화 샘플이 `testdata/`에 추가되면 그 파일로 교체할 것.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ingest.audio import extract_audio
from ingest.stt.rtzr_client import RTZRClient

_LIVE_TEST_ENV_VAR = "RTZR_LIVE_TEST"
_BACKEND_DIR = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    os.environ.get(_LIVE_TEST_ENV_VAR) != "1",
    reason=(
        f"{_LIVE_TEST_ENV_VAR}=1 일 때만 실행되는 수동 스모크 테스트입니다 "
        "(실제 RTZR API 호출 + 과금 발생). 기본 테스트 실행에서는 항상 스킵됩니다."
    ),
)


def test_rtzr_live_authenticate_and_transcribe(dummy_video: Path, tmp_path: Path) -> None:
    # 이 테스트에서만 명시적으로 .env를 로드한다 (다른 테스트에 영향 없음 —
    # 이 파일 전체가 옵트인 상태에서만 수집·실행되기 때문).
    from dotenv import load_dotenv

    load_dotenv(_BACKEND_DIR / ".env")

    if not os.environ.get("RTZR_CLIENT_ID") or not os.environ.get("RTZR_CLIENT_SECRET"):
        pytest.fail(
            "RTZR_LIVE_TEST=1이지만 backend/.env에 RTZR_CLIENT_ID/RTZR_CLIENT_SECRET이 설정돼 있지 않습니다."
        )

    extracted = extract_audio(dummy_video, tmp_path / "live_smoke.wav")

    client = RTZRClient()
    try:
        transcript = client.transcribe(extracted.path)
    finally:
        client.close()

    assert transcript.provider == "rtzr"
    # 무음/사인파 톤 입력이라 발화가 0개일 수도 있다 — 이 테스트의 목적은
    # "API 연동 자체가 에러 없이 끝까지 도는지" 확인이지 전사 품질 검증이 아니다.
