"""답변 생성 인터페이스 + 근거 인용(Citation) 데이터 모델.

CLAUDE.md 원칙: "모든 답변에 근거 타임스탬프(세션·시각) 포함". `Citation`이
그 근거 표시 단위다 — 화면 표시(근거 링크)와 TTS 답변 문구 모두 이
데이터로부터 만들어진다.

`AnswerGenerator`는 STT(`ingest/stt/base.py`)와 같은 이유로 Protocol
뒤에 숨긴다: 기본 구현(`template_generator.py`)은 API 키 없이 검색된
청크를 그대로 인용해 문장을 조립하는 결정적 스텁이고, 실제 서비스에서는
Claude/GPT 계열 LLM으로 자연스러운 문장을 생성하도록 교체한다
(기술조사_의사결정.md 조사 4: "요약·엔티티 갱신·질의응답 LLM: Claude/GPT
계열 중 선택... 구현 시 교체 가능하게 추상화").
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as date_type
from typing import Protocol, runtime_checkable

from ..vault.types import Chunk, Evidence


def format_mmss(seconds: float | None) -> str | None:
    """초 → `mm:ss` 문자열 (영상 오프셋 표기, `video@mm:ss` 링크용)."""
    if seconds is None or seconds < 0:
        return None
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


@dataclass(frozen=True, slots=True)
class Citation:
    """답변 근거 1건 — 세션/날짜/타임스탬프 + (있다면) 영상 딥링크."""

    label: str  # 사람이 읽는 인용 문구 (Chunk.citation_label 재사용)
    doc_path: str
    date: date_type | None
    session_title: str | None
    timestamp_label: str | None
    video_path: str | None
    video_offset_label: str | None  # "mm:ss" — video@mm:ss 링크에 사용
    excerpt: str  # 근거 원문 발췌

    @property
    def video_link(self) -> str | None:
        if not self.video_path or not self.video_offset_label:
            return None
        return f"{self.video_path}@{self.video_offset_label}"


def citation_from_chunk(chunk: Chunk) -> Citation:
    return Citation(
        label=chunk.citation_label,
        doc_path=chunk.doc_path.as_posix(),
        date=chunk.date,
        session_title=chunk.session_title,
        timestamp_label=chunk.timestamp_label,
        video_path=chunk.video_path,
        video_offset_label=format_mmss(chunk.start_sec),
        excerpt=chunk.text,
    )


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """답변 생성 결과. `grounded=False`면 근거가 부족해 답을 지어내지 않고
    "기록에 없음"류 문구를 반환했다는 뜻이다(자기평가/fallback 판단은 별도
    모듈이 하지만, 답변 생성 단계에서도 근거가 아예 없으면 여기서 막는다)."""

    text: str
    citations: tuple[Citation, ...]
    grounded: bool
    evidence: tuple[Evidence, ...]


@runtime_checkable
class AnswerGenerator(Protocol):
    def generate(self, question: str, evidence: Sequence[Evidence]) -> AnswerResult: ...
