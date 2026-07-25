"""회상(recall) 파이프라인 최상위 오케스트레이션.

`RecallPipeline.answer_question()`이 CLAUDE.md의 질의 흐름을 그대로
구현한다:

    검색(하이브리드, coarse-to-fine) → 텍스트로 답변 시도
    → 질문 분류 + 근거 충분성 자기평가 → 불충분 시 fallback 트리거 판정
    (영상 재조회 자체는 스텁)

API 키 없이 끝까지 동작하는 것이 1단계 목표이므로 기본 provider는 모두
오프라인 스텁(임베딩=해시, 답변생성=템플릿, 질문분류=키워드)이다.

`database_url`을 지정했는데 PostgreSQL 연결 자체가 실패하면
(`psycopg.OperationalError`) `ingest/pipeline.py`와 동일한 원칙으로
기존 파일/캐시 기반 인덱스로 전환한다 — `serve` 서브커맨드가 uvicorn
기동 전에 죽어버리는 것을 막기 위한 것이 특히 중요하다.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date as date_type
from pathlib import Path

import psycopg

from .answer.base import AnswerResult, Citation
from .answer.factory import DEFAULT_PROVIDER as DEFAULT_ANSWER_PROVIDER
from .answer.factory import get_answer_generator
from .classify.factory import DEFAULT_PROVIDER as DEFAULT_CLASSIFIER_PROVIDER
from .classify.factory import get_question_classifier
from .classify.question_type import QuestionType
from .fallback.trigger import (
    FallbackDecision,
    StubVideoRequeryClient,
    VideoRequeryClient,
    decide_fallback,
)
from .index.embeddings.factory import DEFAULT_PROVIDER as DEFAULT_EMBEDDING_PROVIDER
from .index.postgres_store import PostgresIndex, build_postgres_index
from .index.store import RefreshStats, VaultIndex, build_index
from .search.coarse_to_fine import RetrievalResult, coarse_to_fine_search

_FALLBACK_NOTICE_PREFIX = "기록에 없음 — 텍스트 근거가 부족합니다. 영상을 확인하고 있어요."


@dataclass(frozen=True, slots=True)
class RecallAnswer:
    """`answer_question()`의 전체 결과. 화면 표시/TTS 응답 둘 다 이걸로 만든다."""

    question: str
    question_type: QuestionType
    draft_answer: AnswerResult  # fallback 이전, 텍스트 검색만으로 만든 1차 답변
    retrieval: RetrievalResult
    fallback: FallbackDecision
    fallback_stub_result: str | None
    final_text: str  # 사용자에게 실제로 보여줄/읽어줄 최종 텍스트

    @property
    def tts_text(self) -> str:
        """TTS 재생용 텍스트 (현재는 화면 표시 텍스트와 동일)."""
        return self.final_text

    @property
    def citations(self) -> tuple[Citation, ...]:
        return self.draft_answer.citations

    def to_display_dict(self) -> dict:
        """화면 표시용(근거 링크 포함) 응답 구조 — `api.py`가 그대로 JSON화."""
        return {
            "answer_text": self.final_text,
            "question_type": self.question_type.value,
            "grounded": self.draft_answer.grounded,
            "citations": [
                {
                    "label": c.label,
                    "session_title": c.session_title,
                    "date": c.date.isoformat() if c.date else None,
                    "timestamp": c.timestamp_label,
                    "video_link": c.video_link,
                    "excerpt": c.excerpt,
                }
                for c in self.citations
            ],
            "fallback": {
                "triggered": self.fallback.triggered,
                "reason": self.fallback.verdict.reason,
                "note": self.fallback.note,
                "clip_targets": [
                    {
                        "video_path": t.video_path,
                        "start_sec": t.start_sec,
                        "end_sec": t.end_sec,
                        "session_title": t.session_title,
                    }
                    for t in self.fallback.clip_targets
                ],
                "stub_result": self.fallback_stub_result,
            },
        }


class RecallPipeline:
    """볼트 하나에 대해 인덱스를 들고 질의응답을 반복 처리하는 오케스트레이터."""

    def __init__(
        self,
        vault_dir: Path | str,
        *,
        cache_path: Path | str | None = None,
        embedding_provider: str = DEFAULT_EMBEDDING_PROVIDER,
        answer_provider: str = DEFAULT_ANSWER_PROVIDER,
        classifier_provider: str = DEFAULT_CLASSIFIER_PROVIDER,
        video_requery_client: VideoRequeryClient | None = None,
        database_url: str | None = None,
    ) -> None:
        self.index: VaultIndex | PostgresIndex
        if database_url:
            try:
                self.index = build_postgres_index(
                    database_url,
                    vault_dir,
                    embedding_provider=embedding_provider,
                )
            except psycopg.OperationalError as exc:
                # ingest/pipeline.py와 동일한 원칙: PostgreSQL 연결 자체가
                # 실패해도(서버 미기동 등) 서버·CLI가 죽는 대신 기존
                # 파일/캐시 기반 인덱스로 전환해 질의응답을 계속한다.
                print(
                    f"[경고] PostgreSQL 연결에 실패해 기존 파일/캐시 기반 검색 인덱스로 대체합니다: {exc}",
                    file=sys.stderr,
                )
                self.index = build_index(
                    vault_dir, cache_path=cache_path, embedding_provider=embedding_provider
                )
        else:
            self.index = build_index(vault_dir, cache_path=cache_path, embedding_provider=embedding_provider)
        self.classifier = get_question_classifier(classifier_provider)
        self.answer_generator = get_answer_generator(answer_provider)
        self.video_requery_client: VideoRequeryClient = video_requery_client or StubVideoRequeryClient()

    def refresh_index(self) -> RefreshStats:
        """볼트 변경분만 다시 인덱싱한다 (위키 파일 변경 감지 시 호출)."""
        return self.index.refresh()

    def answer_question(self, question: str, *, reference_date: date_type) -> RecallAnswer:
        """질문 하나에 대한 전체 recall 흐름을 실행한다.

        Args:
            question: 사용자 질문(한국어, STT로 이미 텍스트화됐다고 가정).
            reference_date: "오늘"에 해당하는 날짜. 실제 서비스에서는
                `date.today()`를 넘기지만, 회귀 테스트에서는 모의 볼트의
                세션 날짜에 맞춰 고정된 날짜를 넘긴다(재현 가능성 확보).
        """
        classification = self.classifier.classify(question)
        retrieval = coarse_to_fine_search(self.index, question, reference_date=reference_date)
        draft_answer = self.answer_generator.generate(question, retrieval.combined_evidence)
        fallback = decide_fallback(classification.question_type, draft_answer)

        fallback_stub_result: str | None = None
        final_text = draft_answer.text
        if fallback.triggered:
            fallback_stub_result = self.video_requery_client.requery(question, fallback.clip_targets)
            final_text = f"{_FALLBACK_NOTICE_PREFIX} {fallback.note}"

        return RecallAnswer(
            question=question,
            question_type=classification.question_type,
            draft_answer=draft_answer,
            retrieval=retrieval,
            fallback=fallback,
            fallback_stub_result=fallback_stub_result,
            final_text=final_text,
        )
