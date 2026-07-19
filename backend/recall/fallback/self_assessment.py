"""텍스트 근거 충분성 자기평가 (fallback 라우팅 ②단계).

휴리스틱 규칙:

- 검색된 근거가 아예 없으면(=`AnswerResult.grounded is False`) 무조건 불충분.
- **시각형** 질문(`QuestionType.VISUAL`)은 `## 장면 캡션` 청크가 근거에
  포함돼 있어야 하고, 그 캡션 본문에 "기록되지 않음"류 명시적 미확인
  문구가 없어야 충분하다고 본다. 데모 모의 볼트의 책 제목 캡션은 일부러
  "표지 문구·제목은 캡션에 기록되지 않음"이라고 적어뒀으므로(전사록에도
  제목이 없음), 이 규칙이 Q3(책 제목)에서 정확히 fallback을 트리거해야
  한다.
- **대화형** 질문은 최상위 근거 점수가 최소 신뢰도 이상이어야 충분하다고
  본다 — 점수가 너무 낮으면(질문과 사실상 무관한 매칭) 답변을 이미
  했더라도 자기평가 단계에서 다시 불충분 판정할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..answer.base import AnswerResult
from ..classify.question_type import QuestionType
from ..vault.types import ChunkLevel

MIN_CONFIDENT_SCORE = 0.05

_NO_INFO_MARKERS: tuple[str, ...] = (
    "기록되지 않",
    "확인되지 않",
    "미확인",
    "알 수 없",
    "포착되지 않",
    "식별되지 않",
)


@dataclass(frozen=True, slots=True)
class SufficiencyVerdict:
    sufficient: bool
    reason: str


def assess_sufficiency(question_type: QuestionType, answer: AnswerResult) -> SufficiencyVerdict:
    """`answer`(텍스트 검색 기반 1차 답변)가 fallback 없이 충분한지 판정한다."""
    if not answer.grounded:
        return SufficiencyVerdict(sufficient=False, reason="검색된 근거가 없어 답변을 지어낼 수 없습니다.")

    if question_type is QuestionType.VISUAL:
        caption_chunks = [ev.chunk for ev in answer.evidence if ev.chunk.level is ChunkLevel.SCENE_CAPTION]
        if not caption_chunks:
            return SufficiencyVerdict(
                sufficient=False,
                reason="시각 정보를 묻는 질문인데 장면 캡션 근거가 검색되지 않았습니다.",
            )
        if any(marker in chunk.text for chunk in caption_chunks for marker in _NO_INFO_MARKERS):
            return SufficiencyVerdict(
                sufficient=False,
                reason="장면 캡션에 해당 시각 정보가 명시적으로 기록되지 않았습니다.",
            )
        return SufficiencyVerdict(sufficient=True, reason="장면 캡션 근거로 답변 가능합니다.")

    top_score = max((ev.score for ev in answer.evidence), default=0.0)
    if top_score < MIN_CONFIDENT_SCORE:
        return SufficiencyVerdict(
            sufficient=False,
            reason=f"근거 신뢰도가 낮습니다(최고 점수 {top_score:.3f} < {MIN_CONFIDENT_SCORE}).",
        )
    return SufficiencyVerdict(sufficient=True, reason="전사록/요약 근거로 충분히 답변 가능합니다.")
