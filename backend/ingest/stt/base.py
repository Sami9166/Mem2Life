"""STT(음성인식) + 화자분리 클라이언트의 추상 인터페이스.

리턴제로(RTZR)를 1순위, Clova Speech를 2순위로 검토 중이며(기술조사_의사결정.md
조사 2), 두 후보 중 무엇이 최종 채택되든 파이프라인/CLI 코드는 한 줄 설정
교체만으로 전환 가능해야 한다. 이를 위해 `SpeechToTextClient` Protocol 뒤에
실제 구현체를 숨기고, `factory.get_stt_client()`로 provider 이름만 바꿔
주입한다.

화자분리는 이름이 아닌 "화자1", "화자2", ... 익명 라벨까지만 담당한다.
실제 이름 매핑(예: "화자1" → "민수")은 LLM 문맥 추론(호칭 기반) 몫이며
이번 ingest 파이프라인의 책임이 아니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


def format_timestamp(seconds: float) -> str:
    """초 단위 시각을 `HH:MM:SS` 문자열로 변환한다 (전사록 표기 형식)."""
    if seconds < 0:
        raise ValueError(f"음수 타임스탬프는 허용되지 않습니다: {seconds}")
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """전사록 한 발화 단위 (화자분리 결과 포함)."""

    start_sec: float
    end_sec: float
    speaker: str  # "화자1", "화자2", ... 익명 라벨
    text: str

    @property
    def timestamp_label(self) -> str:
        """`[HH:MM:SS]` 형태의 전사록 표기용 시작 타임스탬프."""
        return f"[{format_timestamp(self.start_sec)}]"


@dataclass(frozen=True, slots=True)
class Transcript:
    """세션 전체 전사록. 요약본이 아닌 전문(全文)을 담는다."""

    segments: Sequence[TranscriptSegment]
    provider: str  # 어떤 STT 구현체가 생성했는지 (예: "rtzr-stub", "clova-stub")

    @property
    def speakers(self) -> list[str]:
        """등장한 화자 라벨 목록 (등장 순서 유지, 중복 제거)."""
        seen: list[str] = []
        for segment in self.segments:
            if segment.speaker not in seen:
                seen.append(segment.speaker)
        return seen

    @property
    def duration_sec(self) -> float:
        """마지막 발화의 종료 시각 (전사록 기준 세션 길이 근사값)."""
        if not self.segments:
            return 0.0
        return max(segment.end_sec for segment in self.segments)


@runtime_checkable
class SpeechToTextClient(Protocol):
    """STT + 화자분리 클라이언트가 만족해야 하는 인터페이스.

    실제 RTZR/Clova 클라이언트를 구현할 때도 이 Protocol만 만족시키면
    `ingest.stt.factory.get_stt_client()`의 provider 매핑에 한 줄만
    추가해서 교체할 수 있다.
    """

    def transcribe(self, audio_path: Path) -> Transcript:
        """오디오 파일(WAV)을 받아 화자분리가 포함된 전사록을 반환한다."""
        ...
