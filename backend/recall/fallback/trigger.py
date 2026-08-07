"""fallback 트리거 판정 + 영상 재조회 클라이언트 계약.

`decide_fallback()`이 이 모듈의 핵심이다: 질문 분류 + 자기평가 결과를 받아
"영상 재조회가 필요한가"를 결정하고, 필요하다면 어느 영상의 어느 구간
(`VideoClipTarget`)을 넘겨야 하는지까지 계산한다.

트리거 이후의 실제 영상 재조회는 `VideoRequeryClient` Protocol 뒤에 숨긴다.
실제 구현은 `gemini_requery.GeminiVideoRequeryClient`(Gemini 영상 입력)이고,
`StubVideoRequeryClient`는 GEMINI_API_KEY가 없거나 오프라인일 때의 폴백이다
(STT/임베딩과 동일한 provider 교체 패턴 — `fallback/factory.py` 참고).

재조회 결과는 단순 문자열이 아니라 `VideoRequeryResult`로 돌려준다: 영상에서
실제로 답을 찾았는지(`grounded`)를 파이프라인이 알아야 "재답변"으로 승격할지,
아니면 "기록에 없음"류 정직한 실패로 남길지 결정할 수 있기 때문이다
(CLAUDE.md: 답을 지어내지 않는다).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..answer.base import AnswerResult
from ..classify.question_type import QuestionType
from .self_assessment import SufficiencyVerdict, assess_sufficiency

_CLIP_PADDING_BEFORE_SEC = 5.0
_CLIP_PADDING_AFTER_SEC = 10.0
_DEFAULT_CLIP_DURATION_SEC = 30.0  # start_sec을 모를 때(세션 시작 구간 추정)


@dataclass(frozen=True, slots=True)
class VideoClipTarget:
    """fallback 시 재조회해야 할 영상 구간."""

    video_path: str
    start_sec: float
    end_sec: float
    session_title: str | None


@dataclass(frozen=True, slots=True)
class FallbackDecision:
    triggered: bool
    question_type: QuestionType
    verdict: SufficiencyVerdict
    clip_targets: tuple[VideoClipTarget, ...]
    note: str


def _build_clip_targets(answer: AnswerResult) -> tuple[VideoClipTarget, ...]:
    targets: list[VideoClipTarget] = []
    seen: set[tuple[str, float, float]] = set()
    for ev in answer.evidence:
        chunk = ev.chunk
        if not chunk.video_path:
            continue
        if chunk.start_sec is not None:
            start = max(0.0, chunk.start_sec - _CLIP_PADDING_BEFORE_SEC)
            end = chunk.start_sec + _CLIP_PADDING_AFTER_SEC
        else:
            start, end = 0.0, _DEFAULT_CLIP_DURATION_SEC
        key = (chunk.video_path, start, end)
        if key in seen:
            continue
        seen.add(key)
        targets.append(
            VideoClipTarget(
                video_path=chunk.video_path,
                start_sec=start,
                end_sec=end,
                session_title=chunk.session_title,
            )
        )
    return tuple(targets)


def decide_fallback(question_type: QuestionType, answer: AnswerResult) -> FallbackDecision:
    """1차 텍스트 답변을 보고 fallback(영상 재조회) 여부를 판정한다."""
    verdict = assess_sufficiency(question_type, answer)

    if verdict.sufficient:
        return FallbackDecision(
            triggered=False,
            question_type=question_type,
            verdict=verdict,
            clip_targets=(),
            note="텍스트 근거로 충분합니다 — fallback이 필요하지 않습니다.",
        )

    return FallbackDecision(
        triggered=True,
        question_type=question_type,
        verdict=verdict,
        clip_targets=_build_clip_targets(answer),
        note="텍스트 근거가 불충분합니다 — 해당 구간 영상을 Gemini(영상 입력)로 재조회합니다.",
    )


@dataclass(frozen=True, slots=True)
class VideoRequeryResult:
    """영상 재조회 1회의 결과.

    `grounded=True`면 영상에서 실제로 답의 근거를 찾았다는 뜻이고, 그때만
    `answer_text`가 사용자에게 보여줄 "재답변"으로 승격된다. `grounded=False`면
    (근거를 못 찾음 / API 키 없음 / 재조회 실패) 답을 지어내지 않고 정직한 실패
    문구를 담는다 — `error`에 원인을 남긴다(있으면).
    """

    answer_text: str
    grounded: bool
    clips_used: tuple[VideoClipTarget, ...] = ()
    error: str | None = None


@runtime_checkable
class VideoRequeryClient(Protocol):
    """영상 클립을 Gemini(영상 입력)에 넣어 재조회하는 클라이언트.

    실제 구현은 `gemini_requery.GeminiVideoRequeryClient`이고, 이 Protocol만
    만족시키면 `pipeline.py` 호출부는 그대로 둘 수 있다(STT/임베딩과 동일한
    교체 패턴). 재조회가 실패해도 예외를 밖으로 던지지 않고
    `VideoRequeryResult(grounded=False, ...)`로 정직하게 돌려주는 것이 계약이다
    — fallback은 이미 "1차 답변이 불충분하다"는 신호라, 여기서 죽으면 사용자에게
    아무 답도 못 준다.
    """

    def requery(self, question: str, clips: Sequence[VideoClipTarget]) -> VideoRequeryResult: ...


@dataclass
class StubVideoRequeryClient:
    """오프라인/무키(無key) 폴백. 실제 영상 재조회 대신 "무엇이 필요한지"만
    설명하는 결과를 돌려준다 — 데모에서 fallback 경로가 끊기지 않고 끝까지
    흐르도록 하되, 있지도 않은 사실(예: 책 제목)을 절대 지어내지 않는다
    (`grounded=False` 고정). GEMINI_API_KEY가 없을 때 `fallback/factory.py`가
    `GeminiVideoRequeryClient` 대신 이걸 반환한다.
    """

    def requery(self, question: str, clips: Sequence[VideoClipTarget]) -> VideoRequeryResult:
        if not clips:
            return VideoRequeryResult(
                answer_text=(
                    "[영상 재조회 미수행] 재조회할 영상 구간을 찾지 못했습니다 — 원본 영상이 "
                    "볼트에 기록돼 있는지 확인이 필요합니다."
                ),
                grounded=False,
                clips_used=(),
            )
        clip_desc = "; ".join(f"{c.video_path}@{c.start_sec:.0f}-{c.end_sec:.0f}s" for c in clips)
        return VideoRequeryResult(
            answer_text=(
                "[영상 재조회 미수행] GEMINI_API_KEY가 설정돼 있지 않아 실제 Gemini 영상 "
                "재조회를 수행하지 못했습니다. 다음 구간을 Gemini에 영상 입력으로 넣어 "
                f"재조회해야 합니다: {clip_desc}"
            ),
            grounded=False,
            clips_used=tuple(clips),
        )
