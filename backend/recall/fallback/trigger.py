"""fallback 트리거 판정 (fallback 라우팅 ③단계의 "판정" 부분만).

`decide_fallback()`이 이 모듈의 핵심이다: 질문 분류 + 자기평가 결과를 받아
"영상 재조회가 필요한가"를 결정하고, 필요하다면 어느 영상의 어느 구간
(`VideoClipTarget`)을 넘겨야 하는지까지 계산한다.

**범위 제한 (작업 지시대로)**: 실제 Gemini 영상 입력 재조회 호출은 여기
구현하지 않는다. `VideoRequeryClient` Protocol과 스텁 구현
(`StubVideoRequeryClient`)만 두어, 트리거 판정 이후의 흐름(영상 클립을
어디로 보내야 하는지)까지는 배선해 두고 실제 API 연동은 다음 단계로
남긴다.
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
        note=(
            "텍스트 근거가 불충분합니다 — 해당 구간 영상을 Gemini(영상 입력)로 "
            "재조회해야 합니다. 이번 단계는 트리거 판정까지만 구현하며, 실제 "
            "재조회는 `VideoRequeryClient`의 스텁 구현으로 남겨둔다."
        ),
    )


@runtime_checkable
class VideoRequeryClient(Protocol):
    """영상 클립을 Gemini(영상 입력)에 넣어 재조회하는 클라이언트.

    실제 구현은 다음 단계 작업이다 — 지금은 `StubVideoRequeryClient`만
    등록돼 있고, 이후 실제 Gemini 클라이언트를 추가할 때도 이 Protocol만
    만족시키면 `pipeline.py` 호출부는 그대로 둘 수 있다(STT/임베딩과 동일한
    교체 패턴).
    """

    def requery(self, question: str, clips: Sequence[VideoClipTarget]) -> str: ...


@dataclass
class StubVideoRequeryClient:
    """미구현 스텁. 실제 영상 재조회 대신 "무엇이 필요한지" 설명하는
    플레이스홀더 문자열을 반환한다 — 데모에서 fallback 경로가 끊기지 않고
    끝까지 흐르도록 하되, 있지도 않은 사실(예: 책 제목)을 지어내지 않는다.
    """

    def requery(self, question: str, clips: Sequence[VideoClipTarget]) -> str:
        if not clips:
            return (
                "[STUB] 재조회할 영상 구간을 찾지 못했습니다 — 원본 영상이 "
                "볼트에 기록돼 있는지 확인이 필요합니다."
            )
        clip_desc = "; ".join(f"{c.video_path}@{c.start_sec:.0f}-{c.end_sec:.0f}s" for c in clips)
        return (
            "[STUB] 실제 Gemini 영상 재조회는 아직 구현되지 않았습니다 "
            "(recall-dev 다음 단계 작업). 다음 구간을 Gemini에 영상 입력으로 "
            f"넣어 재조회해야 합니다: {clip_desc}"
        )
