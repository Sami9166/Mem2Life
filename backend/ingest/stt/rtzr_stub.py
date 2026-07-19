"""리턴제로(RTZR) VITO API 스텁 클라이언트.

STT 1순위(기술조사_의사결정.md 조사 2)의 실제 클라이언트는 `rtzr_client.py`에
구현돼 있다. 이 스텁은 여전히 두 가지 용도로 남아 있다:

    1. `backend/.env`에 RTZR_CLIENT_ID/RTZR_CLIENT_SECRET이 없을 때
       `factory.get_stt_client("rtzr")`의 자동 폴백 (API 키 없이도 전체
       파이프라인이 끝까지 실행돼야 한다는 CLAUDE.md 원칙)
    2. 네트워크 호출 없는 결정적 단위 테스트

`SpeechToTextClient` 인터페이스만 만족하면 되므로 더미 전사록을 반환한다.
"""

from __future__ import annotations

from pathlib import Path

from ..audio import probe_duration
from ._dummy import RTZR_DUMMY_LINES, build_dummy_transcript
from .base import Transcript


class RTZRStubClient:
    """RTZR API 없이 동작하는 스텁. 화자1/화자2 더미 전사록을 반환한다."""

    provider_name = "rtzr-stub"

    def transcribe(self, audio_path: Path) -> Transcript:
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"오디오 파일이 존재하지 않습니다: {audio_path}")

        duration = probe_duration(audio_path)
        return build_dummy_transcript(
            duration,
            provider=self.provider_name,
            lines=RTZR_DUMMY_LINES,
        )
