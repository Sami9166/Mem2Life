"""회상(recall) 파이프라인 최상위 오케스트레이션.

`RecallPipeline.answer_question()`이 CLAUDE.md의 질의 흐름을 그대로
구현한다:

    검색(하이브리드, coarse-to-fine) → 텍스트로 답변 시도
    → 질문 분류 + 근거 충분성 자기평가 → 불충분 시 fallback 트리거 판정
    (영상 재조회 자체는 스텁)

검색 임베딩 기본값은 Gemini Embedding 2이며, 답변 생성과 영상 재조회는
API 키가 없으면 오프라인 구현으로 폴백한다. 완전한 오프라인 검색 검증은
`embedding_provider="hash"`를 명시한다.

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

from .answer.base import AnswerGenerator, AnswerResult, Citation
from .answer.factory import DEFAULT_PROVIDER as DEFAULT_ANSWER_PROVIDER
from .answer.factory import get_answer_generator
from .classify.factory import DEFAULT_PROVIDER as DEFAULT_CLASSIFIER_PROVIDER
from .classify.factory import get_question_classifier
from .classify.question_type import QuestionType
from .fallback.factory import DEFAULT_PROVIDER as DEFAULT_VIDEO_REQUERY_PROVIDER
from .fallback.factory import get_video_requery_client
from .fallback.trigger import (
    FallbackDecision,
    VideoRequeryClient,
    decide_fallback,
)
from .index.embeddings.factory import DEFAULT_PROVIDER as DEFAULT_EMBEDDING_PROVIDER
from .index.postgres_store import PostgresIndex, build_postgres_index
from .index.store import RefreshStats, VaultIndex, build_index
from .present.glass import (
    NOT_FOUND_DISPLAY,
    AnswerStatus,
    GlassAnswer,
    build_glass_answer,
    strip_requery_sentinel,
)
from .search.coarse_to_fine import RetrievalResult, coarse_to_fine_search


@dataclass(frozen=True, slots=True)
class RecallAnswer:
    """`answer_question()`의 전체 결과. 화면 표시/TTS 응답 둘 다 이걸로 만든다."""

    question: str
    question_type: QuestionType
    draft_answer: AnswerResult  # fallback 이전, 텍스트 검색만으로 만든 1차 답변
    retrieval: RetrievalResult
    fallback: FallbackDecision
    fallback_stub_result: str | None
    final_text: str  # 사용자에게 실제로 보여줄 최종 텍스트(인용 표기 포함, CLI/로그용)
    status: AnswerStatus = AnswerStatus.ANSWERED
    # 인용 표기를 뺀 본문 — 음성/글래스 화면은 이걸 쓴다. `final_text`를 그대로
    # 읽으면 "괄호 근거 세션 …" 같은 인용 덩어리가 음성으로 나간다.
    spoken_body: str = ""
    # 상대 시각("어제 15:01") 표기 기준이 되는 날짜.
    reference_date: date_type | None = None

    @property
    def tts_text(self) -> str:
        """TTS 재생용 텍스트 — 인용·링크·대괄호 표기를 걷어낸 문장."""
        return self.glass.tts_text

    @property
    def glass(self) -> GlassAnswer:
        """글래스 출력(음성 + 480x480 화면)용 표현."""
        return build_glass_answer(
            status=self.status,
            body=self.spoken_body or self.final_text,
            citations=self.citations,
            reference_date=self.reference_date or date_type.today(),
        )

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
            "glass": self.glass.to_dict(),
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
        answer_generator: AnswerGenerator | None = None,
        classifier_provider: str = DEFAULT_CLASSIFIER_PROVIDER,
        video_requery_client: VideoRequeryClient | None = None,
        video_requery_provider: str = DEFAULT_VIDEO_REQUERY_PROVIDER,
        database_url: str | None = None,
    ) -> None:
        # 위키 열람 API(wiki_api)가 볼트 파일을 직접 읽을 때 쓴다.
        self.vault_dir = Path(vault_dir)
        self.index: VaultIndex | PostgresIndex
        # database_url을 지정했는데 연결 실패로 파일 모드로 대체된 경우에만
        # True. 서버가 오래 떠 있는 동안(`serve`) 지금 어느 모드로 동작
        # 중인지 재시작·로그 확인 없이도 알 수 있도록 `/health`에서 그대로
        # 노출한다(index_mode 프로퍼티 참고).
        self.database_fallback = False
        self.database_fallback_detail: str | None = None
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
                self.database_fallback = True
                self.database_fallback_detail = str(exc)
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
        # video_requery_client와 같은 이유로 인스턴스 주입을 허용한다 — 기본
        # provider "gemini"는 GEMINI_API_KEY 유무로 실제/템플릿이 갈리므로,
        # 환경변수 오염에 영향받으면 안 되는 회귀 테스트는 여기에
        # TemplateAnswerGenerator를 명시적으로 넣어 고정한다.
        self.answer_generator: AnswerGenerator = answer_generator or get_answer_generator(answer_provider)
        # 명시적으로 주입하면 그걸 쓰고(테스트/커스텀), 아니면 factory가 provider별
        # 클라이언트를 만든다. 기본 provider "gemini"는 GEMINI_API_KEY가 없으면
        # 자동으로 스텁으로 폴백하므로 API 키 없이도 파이프라인이 끝까지 돈다.
        self.video_requery_client: VideoRequeryClient = video_requery_client or get_video_requery_client(
            video_requery_provider
        )

    @property
    def index_mode(self) -> str:
        """현재 검색 인덱스가 어디서 오는지 — `/health`에서 그대로 노출."""
        return "postgres" if isinstance(self.index, PostgresIndex) else "file"

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
        spoken_body = draft_answer.spoken_body
        status = AnswerStatus.ANSWERED
        if fallback.triggered:
            requery = self.video_requery_client.requery(question, fallback.clip_targets)
            # 재조회 원문(성공/실패 불문)은 디버깅용으로 sentinel까지 그대로 보존한다.
            fallback_stub_result = requery.answer_text
            if requery.grounded:
                # 영상에서 실제 근거를 찾았다 → 재답변으로 승격(CLAUDE.md ③단계).
                # 프롬프트가 강제한 "[확인됨]" sentinel은 판정에만 쓰고 사용자에게는
                # 보여주지 않는다(표현 계층에서 제거).
                status = AnswerStatus.ANSWERED_FROM_VIDEO
                final_text = strip_requery_sentinel(requery.answer_text)
                spoken_body = final_text
            else:
                # 텍스트도 영상도 근거가 없다 → 지어내지 않고 정직하게 실패.
                # 이전에는 내부 판정 사유(fallback.note — "Gemini(영상 입력)로
                # 재조회합니다")까지 사용자 문구에 붙었는데, 구현 얘기라 걷어냈다.
                # 판정 사유가 필요하면 `fallback.verdict.reason`으로 따로 볼 수 있다.
                status = AnswerStatus.NOT_FOUND
                final_text = NOT_FOUND_DISPLAY
                spoken_body = NOT_FOUND_DISPLAY

        return RecallAnswer(
            question=question,
            question_type=classification.question_type,
            draft_answer=draft_answer,
            retrieval=retrieval,
            fallback=fallback,
            fallback_stub_result=fallback_stub_result,
            final_text=final_text,
            status=status,
            spoken_body=spoken_body,
            reference_date=reference_date,
        )
