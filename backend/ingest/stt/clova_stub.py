"""Clova Speech API 스텁 클라이언트.

STT 2순위 후보(기술조사_의사결정.md 조사 2). 실제 API 키/네트워크 호출 없이
`SpeechToTextClient` 인터페이스를 만족하는 더미 전사록을 반환한다.

실제 Clova 연동 시 할 일 (이번 작업 범위 아님):
    1. `CLOVA_SPEECH_API_KEY`를 `.env`에서 읽는 실제 클라이언트 클래스를 이 파일 옆에 작성
    2. `ingest.stt.factory._PROVIDERS["clova"]`가 그 클래스를 가리키도록 한 줄 교체
   → 파이프라인/CLI 코드는 변경 불필요.
"""

from __future__ import annotations

from pathlib import Path

from ..audio import probe_duration
from ._dummy import CLOVA_DUMMY_LINES, build_dummy_transcript
from .base import Transcript


class ClovaStubClient:
    """Clova Speech API 없이 동작하는 스텁. 화자1/화자2 더미 전사록을 반환한다."""

    provider_name = "clova-stub"

    def transcribe(self, audio_path: Path) -> Transcript:
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"오디오 파일이 존재하지 않습니다: {audio_path}")

        duration = probe_duration(audio_path)
        return build_dummy_transcript(
            duration,
            provider=self.provider_name,
            lines=CLOVA_DUMMY_LINES,
        )
