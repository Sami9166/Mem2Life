"""볼트 문서/청크 데이터 모델.

CLAUDE.md의 Obsidian 볼트 스키마:

    vault/
    ├── sessions/YYYY-MM-DD_HHMM_제목.md   # frontmatter + 요약/주요순간/전사록/장면캡션
    ├── people/이름.md
    ├── topics/주제.md
    └── daily/YYYY-MM-DD.md

이 모듈은 그 md 파일들을 어떻게 "문서(VaultDocument)"와 "검색 단위 청크
(Chunk)"로 표현할지 정의한다. 실제 파싱은 `loader.py`/`chunking.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date as date_type
from enum import StrEnum
from pathlib import Path


class DocKind(StrEnum):
    """볼트 문서 종류 (디렉토리 기준)."""

    SESSION = "session"
    PEOPLE = "people"
    TOPIC = "topic"
    DAILY = "daily"


class ChunkLevel(StrEnum):
    """coarse-to-fine 검색에서 이 청크가 속하는 층위.

    EgoRAG 방식(기술조사_의사결정.md 조사 6): daily 요약(coarse) → 세션 요약
    (middle) → 전사록/캡션 세부 발화(fine) 순으로 검색 창을 좁힌다.
    people/topics 페이지는 엔티티 질문(예: EntityLog 유형)에 필요해
    별도의 ENTITY 층위로 분류하고, 날짜 기반 좁히기 없이 항상 후보에
    포함한다.
    """

    DAILY = "daily"
    SESSION_SUMMARY = "session_summary"
    TRANSCRIPT = "transcript"
    SCENE_CAPTION = "scene_caption"
    ENTITY = "entity"


@dataclass(frozen=True, slots=True)
class VaultDocument:
    """md 파일 하나를 읽은 결과."""

    path: Path  # 볼트 루트 기준 상대 경로
    kind: DocKind
    frontmatter: dict[str, object]
    raw_text: str
    date: date_type | None  # sessions/daily는 frontmatter의 date, people/topics는 None
    title: str  # 사람이 읽을 제목 (파일명에서 유도)


@dataclass(frozen=True, slots=True)
class Chunk:
    """검색 인덱스의 최소 단위. 하나의 근거(evidence) 후보와 1:1 대응한다.

    `citation_label`/`timestamp_label`은 답변 생성 시 그대로 인용 문구로
    쓰인다 — "답을 지어내지 않는다" 원칙을 지키려면 모든 답변이 실제
    Chunk에서 나온 근거를 가리켜야 하므로, 이 dataclass가 recall 전체의
    "진실의 원천"이다.
    """

    chunk_id: str
    doc_path: Path
    doc_kind: DocKind
    level: ChunkLevel
    text: str  # 인덱싱/검색 대상 본문
    date: date_type | None  # 소속 세션/daily 날짜 (people/topics는 None일 수 있음)
    session_title: str | None = None  # 세션 파일명에서 유도한 제목 (예: "제주도_여행_계획")
    session_time_range: str | None = None  # frontmatter의 "15:00-15:03"
    start_sec: float | None = None  # 세션(영상) 시작 기준 상대 초 — fallback 클립 offset용
    timestamp_label: str | None = None  # "[15:01:20]" 절대 시각 표기 (있으면, 인용 문구용)
    speaker: str | None = None
    video_path: str | None = None  # frontmatter의 video 경로 (fallback 재조회용)

    @property
    def citation_label(self) -> str:
        """답변에 붙일 근거 인용 문구. 항상 세션/날짜 + 시각을 포함한다."""
        parts: list[str] = []
        if self.doc_kind is DocKind.SESSION and self.session_title:
            parts.append(f"세션 '{self.session_title}'")
        elif self.doc_kind is DocKind.DAILY and self.date:
            parts.append(f"{self.date.isoformat()} 일별 요약")
        elif self.doc_kind is DocKind.PEOPLE:
            parts.append(f"인물 노트({self.doc_path.stem})")
        elif self.doc_kind is DocKind.TOPIC:
            parts.append(f"주제 노트({self.doc_path.stem})")

        if self.date and self.doc_kind is DocKind.SESSION:
            parts.append(f"({self.date.isoformat()})")
        if self.timestamp_label:
            parts.append(self.timestamp_label)
        elif self.session_time_range:
            parts.append(f"({self.session_time_range})")
        return " ".join(parts) if parts else str(self.doc_path)


@dataclass(frozen=True, slots=True)
class Evidence:
    """검색 결과 1건 — 점수 + 원본 청크."""

    chunk: Chunk
    score: float
    bm25_score: float = 0.0
    vector_score: float = 0.0


@dataclass(frozen=True, slots=True)
class VaultCorpus:
    """로딩·청킹이 끝난 볼트 전체."""

    documents: Sequence[VaultDocument]
    chunks: Sequence[Chunk] = field(default_factory=tuple)
