"""EgoRAG 방식 coarse-to-fine 검색 오케스트레이션.

기술조사_의사결정.md 조사 6: "일별 요약 → 세션 요약 → 전사록/캡션 순의
coarse-to-fine 검색". 3단계로 구현한다:

1. **daily(coarse)**: 질문의 날짜 힌트(`date_hints.py`)로 후보를 좁히거나,
   힌트가 없으면 daily 요약 전체를 하이브리드 검색해 관련 날짜를 추론한다.
2. **session(middle)**: 좁혀진 날짜(있다면) 안에서 세션 요약/주요 순간과
   people/topics 엔티티 페이지를 하이브리드 검색해 관련 세션을 고른다.
3. **transcript/caption(fine)**: 고른 세션의 전사록/장면 캡션 줄 단위
   청크를 하이브리드 검색해 타임스탬프가 붙은 세부 근거를 뽑는다.

각 단계 결과와 최종적으로 답변 생성에 넘길 통합 근거 목록
(`combined_evidence`)을 `RetrievalResult`에 담아 반환한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type
from pathlib import Path

from ..index.store import VaultIndex
from ..vault.types import ChunkLevel, DocKind, Evidence
from .date_hints import resolve_query_date

_MIN_DAILY_MATCH_SCORE = 1e-6  # 이 이상이어야 daily 검색 결과로 날짜를 좁힌다


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    query: str
    resolved_date: date_type | None  # 질문 문구에서 직접 해석된 날짜(있으면)
    narrowed_dates: frozenset[date_type]  # 실제로 좁혀진 날짜 집합(daily 검색 결과 기반)
    daily_evidence: list[Evidence]
    session_evidence: list[Evidence]  # 세션 요약/주요 순간 (타임스탬프 있음)
    entity_evidence: list[Evidence]  # people/topics 페이지 (라우팅 보조 신호)
    fine_evidence: list[Evidence]
    chosen_sessions: tuple[Path, ...]
    combined_evidence: list[Evidence] = field(default_factory=list)

    @property
    def has_any_evidence(self) -> bool:
        return bool(self.combined_evidence)


def _dedup_by_chunk_id(evidences: list[Evidence]) -> list[Evidence]:
    best: dict[str, Evidence] = {}
    for ev in evidences:
        existing = best.get(ev.chunk.chunk_id)
        if existing is None or ev.score > existing.score:
            best[ev.chunk.chunk_id] = ev
    return sorted(best.values(), key=lambda e: e.score, reverse=True)


def coarse_to_fine_search(
    index: VaultIndex,
    question: str,
    *,
    reference_date: date_type,
    top_k_daily: int = 2,
    top_k_session: int = 3,
    top_k_fine: int = 5,
    max_chosen_sessions: int = 2,
    combined_top_k: int = 6,
) -> RetrievalResult:
    """세 단계 coarse-to-fine 검색을 수행하고 통합 근거 목록을 만든다."""
    resolved_date = resolve_query_date(question, reference_date)

    # ------------------------------------------------------------------
    # 1단계: daily(coarse) — 날짜 창 좁히기
    # ------------------------------------------------------------------
    if resolved_date is not None:
        daily_candidates = index.indices_for(levels={ChunkLevel.DAILY}, dates={resolved_date})
    else:
        daily_candidates = index.indices_for(levels={ChunkLevel.DAILY})

    daily_evidence = index.search(question, indices=daily_candidates, top_k=top_k_daily)

    if resolved_date is not None:
        narrowed_dates = frozenset({resolved_date})
    else:
        narrowed_dates = frozenset(
            ev.chunk.date
            for ev in daily_evidence
            if ev.chunk.date is not None and ev.score > _MIN_DAILY_MATCH_SCORE
        )

    # ------------------------------------------------------------------
    # 2단계: session(middle) — 세션 요약/주요 순간 + 엔티티 페이지
    # ------------------------------------------------------------------
    # 두 풀을 따로 검색한다(합쳐서 한 번에 top_k를 자르지 않는다): people/topics
    # 페이지는 세션 로그를 산문으로 재서술한 것이라 세션 요약/주요 순간과 별개로
    # 라우팅 신호를 제공한다. 두 종류를 독립적으로 top_k개씩 확보한다.
    date_filter = narrowed_dates if narrowed_dates else None
    session_level_idx = index.indices_for(
        levels={ChunkLevel.SESSION_SUMMARY, ChunkLevel.HIGHLIGHT}, dates=date_filter
    )
    entity_idx = index.indices_for(levels={ChunkLevel.ENTITY})

    session_candidates = index.search(
        question, indices=session_level_idx, top_k=len(session_level_idx)
    )
    session_evidence = session_candidates[:top_k_session]
    entity_evidence = index.search(question, indices=entity_idx, top_k=top_k_session)

    chosen_sessions: list[Path] = []
    for ev in session_candidates:
        if ev.chunk.doc_kind is DocKind.SESSION and ev.chunk.doc_path not in chosen_sessions:
            chosen_sessions.append(ev.chunk.doc_path)
        if len(chosen_sessions) >= max_chosen_sessions:
            break

    if not chosen_sessions:
        # 세션 레벨에서 아무것도 못 골랐으면(예: 엔티티 청크만 히트) 좁혀진 날짜
        # 범위 내 모든 세션으로 fine 검색 범위를 넓힌다 — 데모 스케일에서는
        # 세션 수가 적어 비용이 크지 않다.
        fallback_idx = index.indices_for(
            levels={ChunkLevel.SESSION_SUMMARY, ChunkLevel.HIGHLIGHT}, dates=date_filter
        )
        chosen_sessions = sorted(
            {index.chunks[i].doc_path for i in fallback_idx}, key=lambda p: p.as_posix()
        )[:max_chosen_sessions]

    # ------------------------------------------------------------------
    # 3단계: transcript/caption(fine) — 세부 근거
    # ------------------------------------------------------------------
    fine_candidates = index.indices_for(
        levels={ChunkLevel.TRANSCRIPT, ChunkLevel.SCENE_CAPTION},
        doc_paths=set(chosen_sessions) if chosen_sessions else None,
    )
    fine_evidence = index.search(question, indices=fine_candidates, top_k=top_k_fine)

    # 최종 답변·인용 근거는 타임스탬프가 붙는 daily/세션/전사록/캡션을 항상
    # 먼저 채우고, people/topics(엔티티) 근거는 남는 자리만 보조로 채운다.
    # 점수만으로 한 번에 정렬해 자르면 엔티티 산문(시각 정보 없음)이 종종
    # 세션·전사록·캡션 근거를 밀어낼 수 있으므로 엔티티는 보조 역할로 둔다.
    timestamped_pool = _dedup_by_chunk_id(daily_evidence + session_evidence + fine_evidence)
    combined = list(timestamped_pool[:combined_top_k])
    if len(combined) < combined_top_k:
        existing_ids = {e.chunk.chunk_id for e in combined}
        for ev in _dedup_by_chunk_id(entity_evidence):
            if ev.chunk.chunk_id in existing_ids:
                continue
            combined.append(ev)
            if len(combined) >= combined_top_k:
                break

    return RetrievalResult(
        query=question,
        resolved_date=resolved_date,
        narrowed_dates=narrowed_dates,
        daily_evidence=daily_evidence,
        session_evidence=session_evidence,
        entity_evidence=entity_evidence,
        fine_evidence=fine_evidence,
        chosen_sessions=tuple(chosen_sessions),
        combined_evidence=combined,
    )
