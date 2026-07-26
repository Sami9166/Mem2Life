"""Gemini 실제 영상 재조회를 상대로 하는 수동/선택적 스모크 테스트.

기본 `uv run pytest`에서는 절대 실행되지 않는다 — `GEMINI_LIVE_TEST=1`
환경변수로 명시적으로 옵트인해야만 동작한다(과금 발생 + 실제 네트워크 호출).

    GEMINI_LIVE_TEST=1 uv run pytest tests/test_recall_gemini_requery_live.py -v -s

실행 전 `backend/.env`에 GEMINI_API_KEY가 설정돼 있어야 한다(이 테스트가 직접
로드한다 — conftest의 autouse 픽스처가 환경변수를 비우므로 여기서 다시 로드).

알려진 한계: `conftest.py`의 `dummy_video`는 무의미한 파란 화면 + 사인파라
Gemini가 "확인불가"로 답하는 게 정상이다. 이 테스트의 목적은 "클립 추출 →
인라인 업로드 → generate_content 응답 파싱까지 API 연동 자체가 에러 없이
끝까지 도는지"이지 재조회 답변 품질 검증이 아니다. 실제 촬영 영상이
`testdata/`에 추가되면 그 파일로 교체할 것.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from recall.fallback.gemini_requery import GeminiVideoRequeryClient
from recall.fallback.trigger import VideoClipTarget, VideoRequeryResult

_LIVE_TEST_ENV_VAR = "GEMINI_LIVE_TEST"
_BACKEND_DIR = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    os.environ.get(_LIVE_TEST_ENV_VAR) != "1",
    reason=(
        f"{_LIVE_TEST_ENV_VAR}=1 일 때만 실행되는 수동 스모크 테스트입니다 "
        "(실제 Gemini API 호출 + 과금 발생). 기본 테스트 실행에서는 항상 스킵됩니다."
    ),
)


def test_gemini_live_requery_end_to_end(dummy_video: Path) -> None:
    from dotenv import load_dotenv

    load_dotenv(_BACKEND_DIR / ".env")

    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        pytest.fail("GEMINI_LIVE_TEST=1이지만 backend/.env에 GEMINI_API_KEY가 설정돼 있지 않습니다.")

    client = GeminiVideoRequeryClient(clip_reencode=True)
    clip = VideoClipTarget(
        video_path=str(dummy_video), start_sec=0.0, end_sec=3.0, session_title="스모크테스트"
    )
    result = client.requery("이 영상에서 어떤 물체가 보이나요?", [clip])

    # 응답 파싱까지 에러 없이 끝났으면 성공 — grounded 여부는 영상 내용에 달렸다.
    assert isinstance(result, VideoRequeryResult)
    assert result.answer_text
    print(f"\n[GEMINI LIVE] grounded={result.grounded}\n{result.answer_text}\n")
