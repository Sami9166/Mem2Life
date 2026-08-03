"""답변을 사용자에게 내보내는 표현 계층 (음성 / 글래스 화면).

`recall/answer/`가 "무엇을 답할지"를 정한다면, 이 패키지는 "그걸 어떻게
들려주고 보여줄지"만 담당한다 — 검색·판정 로직과 섞이지 않도록 분리했다.
"""

from .glass import (
    AnswerStatus,
    GlassAnswer,
    GlassEvidence,
    build_glass_answer,
    relative_day_label,
    speakable,
    strip_requery_sentinel,
)

__all__ = [
    "AnswerStatus",
    "GlassAnswer",
    "GlassEvidence",
    "build_glass_answer",
    "relative_day_label",
    "speakable",
    "strip_requery_sentinel",
]
