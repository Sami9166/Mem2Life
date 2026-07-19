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


class RecallQueryResponse(BaseModel):
    """TTS 재생용(`tts_text`)과 화면 표시용(나머지 필드) 응답을 함께 담는다."""

    tts_text: str
    answer_text: str
    question_type: str
    grounded: bool
    citations: list[CitationOut]
    fallback: FallbackOut


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
        return RecallQueryResponse(
            tts_text=result.tts_text,
            answer_text=display["answer_text"],
            question_type=display["question_type"],
            grounded=display["grounded"],
            citations=[CitationOut(**c) for c in display["citations"]],
            fallback=FallbackOut(**display["fallback"]),
        )

    return router


def create_app(pipeline: RecallPipeline) -> FastAPI:
    """단독 실행용 FastAPI 앱 (`cli.py serve`에서 사용)."""
    app = FastAPI(title="Mem2Life Recall API", version="0.1.0")
    app.include_router(create_recall_router(pipeline))
    return app
