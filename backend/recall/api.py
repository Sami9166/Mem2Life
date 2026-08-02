"""질의응답 출력 API — TTS 재생용 텍스트 + 화면 표시용(근거 링크 포함) 응답.

CLAUDE.md: "답변: TTS(글래스 스피커) + 화면 표시(근거 타임스탬프 링크 포함)".
이 라우터는 `RecallPipeline.answer_question()` 결과를 그 두 형태로 그대로
직렬화한다. 실제 서버 실행(uvicorn)은 `cli.py`의 `serve` 서브커맨드를
참고 — 1단계 시점에는 노트북 로컬 실행이 목표라 인증/CORS 등은 아직
다루지 않는다.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .pipeline import RecallPipeline


class RecallQueryRequest(BaseModel):
    question: str = Field(..., min_length=1, description="폰 앱 STT로 텍스트화된 질문")
    reference_date: date | None = Field(
        default=None,
        description="'오늘'로 취급할 날짜. 생략 시 서버의 실제 오늘 날짜를 사용한다.",
    )


class CitationOut(BaseModel):
    label: str
    session_title: str | None
    date: str | None
    timestamp: str | None
    video_link: str | None
    excerpt: str


class FallbackOut(BaseModel):
    triggered: bool
    reason: str
    note: str
    clip_targets: list[dict]
    stub_result: str | None


class GlassEvidenceOut(BaseModel):
    label: str = Field(..., description='"어제 15:01 · 제주도_여행_계획" 형태의 짧은 근거 라벨')
    video_link: str | None = Field(None, description="탭하면 그 구간을 재생할 영상 딥링크")


class GlassOut(BaseModel):
    """글래스(Blade 2) 출력 전용 표현 — 앱은 이 필드만 보면 된다.

    `tts_text`는 스피커로 그대로 읽으면 되고, `display_text` + `evidence`는
    480x480 웨이브가이드에 올리면 되도록 이미 짧게 다듬어져 있다. 줄바꿈은
    폰트 메트릭을 아는 앱이 하도록 남겨둔다.
    """

    status: str = Field(..., description="answered | answered_from_video | not_found")
    status_label: str = Field(..., description='화면 상단 상태 문구("기록 확인됨" 등)')
    tts_text: str
    display_text: str
    evidence: list[GlassEvidenceOut]


class RecallQueryResponse(BaseModel):
    """글래스 출력용(`glass`)과 디버깅/데스크톱용(나머지 필드)을 함께 담는다.

    앱은 `glass`만 쓰면 되고, 나머지는 CLI 출력·회귀 테스트·문제 추적용이다.
    최상위 `tts_text`는 `glass.tts_text`와 같은 값이다(기존 호출부 호환).
    """

    tts_text: str
    answer_text: str
    question_type: str
    grounded: bool
    citations: list[CitationOut]
    fallback: FallbackOut
    glass: GlassOut


class HealthResponse(BaseModel):
    """서버가 현재 pgvector로 동작 중인지, DB 장애로 파일 모드로 내려가
    있는지 재시작·로그 확인 없이 바로 확인하기 위한 상태 엔드포인트."""

    status: str = "ok"
    index_mode: str
    database_fallback: bool
    database_fallback_detail: str | None = None


def create_recall_router(pipeline: RecallPipeline) -> APIRouter:
    """주어진 `pipeline`에 바인딩된 `/recall/query` 라우터를 만든다.

    호출부(앱 조립 코드)가 어떤 볼트/provider 조합의 `RecallPipeline`을
    넘기든 그대로 재사용할 수 있도록 라우터를 팩토리 함수로 만든다
    (테스트에서 모의 볼트를 가리키는 파이프라인을 주입하기 쉽게 하기 위함).
    """
    router = APIRouter(prefix="/recall", tags=["recall"])

    @router.post("/query", response_model=RecallQueryResponse)
    def query(request: RecallQueryRequest) -> RecallQueryResponse:
        try:
            pipeline.refresh_index()  # 위키 파일 변경 감지 시 증분 갱신
            result = pipeline.answer_question(
                request.question,
                reference_date=request.reference_date or date.today(),
            )
        except Exception as exc:  # noqa: BLE001 - API 경계에서는 502로 통일
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        display = result.to_display_dict()
        glass = display["glass"]
        return RecallQueryResponse(
            tts_text=glass["tts_text"],
            answer_text=display["answer_text"],
            question_type=display["question_type"],
            grounded=display["grounded"],
            citations=[CitationOut(**c) for c in display["citations"]],
            fallback=FallbackOut(**display["fallback"]),
            glass=GlassOut(
                status=glass["status"],
                status_label=glass["status_label"],
                tts_text=glass["tts_text"],
                display_text=glass["display_text"],
                evidence=[GlassEvidenceOut(**e) for e in glass["evidence"]],
            ),
        )

    return router


def create_app(pipeline: RecallPipeline) -> FastAPI:
    """단독 실행용 FastAPI 앱 (`cli.py serve`에서 사용)."""
    app = FastAPI(title="Mem2Life Recall API", version="0.1.0")
    app.include_router(create_recall_router(pipeline))

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        # PostgreSQL 연결 실패는 서버 기동 시(RecallPipeline 생성 시점)
        # 한 번만 조용히 파일 모드로 대체되므로, 재시작하거나 그때의 stderr
        # 로그를 다시 찾아보지 않는 한 지금 서버가 어느 모드로 떠 있는지
        # 알 방법이 없었다 — 이 엔드포인트가 그 상태를 언제든 확인시켜준다.
        return HealthResponse(
            index_mode=pipeline.index_mode,
            database_fallback=pipeline.database_fallback,
            database_fallback_detail=pipeline.database_fallback_detail,
        )

    return app
