"""글래스로 내보낼 답변 표현 — 음성(TTS)과 화면(웨이브가이드)을 분리한다.

CLAUDE.md: "답변: TTS(글래스 스피커) + 글래스 디스플레이 표시(웨이브가이드
480x480, 근거 타임스탬프 링크 포함)". 그런데 지금까지 `tts_text`는 화면 표시
텍스트와 글자 그대로 같았고, 그 텍스트에는 인용 표기가 통째로 박혀 있었다.
실제로 읽히면 이렇게 나간다:

    "…침묵의 봄입니다. 괄호 근거 세션 책 대여 약속 이천이십육 년 팔 월 이 일
     영영 영영 영칠 슬래시 세션 책 대여 약속…"

음성과 화면은 요구사항이 다르다:

- **음성**: 문장만. 인용·타임스탬프·위키링크 대괄호는 읽으면 소음이다. 대신
  "어제", "3일 전" 같은 상대 시각은 말로 들었을 때 오히려 유용하다.
- **화면**: 480x480 웨이브가이드에 20° FOV. 문장 한두 줄 + 근거 1~2건이
  한계다. 근거는 `[15:01:20]` 같은 원본 라벨 대신 "어제 15:01 · 세션명"처럼
  줄여야 들어간다.

그래서 이 모듈은 같은 답변을 두 형태로 각각 만든다. 줄바꿈(워드랩)은 하지
않는다 — 폰트 메트릭은 앱만 알기 때문에, 여기서는 "짧은 문자열"까지만
책임지고 실제 줄나눔은 앱에 맡긴다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as date_type
from enum import StrEnum

from ..answer.base import Citation

# 좁은 화면에 올릴 근거 개수 상한. 3건 이상은 480x480에서 잘린다.
MAX_GLASS_EVIDENCE = 2

# 영상 재조회 응답의 첫 줄 sentinel(`fallback/gemini_requery.py`가 프롬프트로
# 강제하는 것) + 재조회 실패 머리말. 판정에는 필요하지만 사용자에게 보여줄
# 문구는 아니므로 표현 계층에서 걷어낸다.
_SENTINEL_RE = re.compile(r"^\s*\[(확인됨|확인불가|영상 재조회 [^\]]*)\]\s*")

# `[[위키링크]]` / `![[임베드]]` → 안쪽 텍스트만 남긴다(말로 읽을 때 "대괄호
# 대괄호"가 되지 않도록). 별칭 표기 `[[대상|표시]]`는 표시 쪽을 쓴다.
_WIKILINK_RE = re.compile(r"!?\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")

# `[15:01:20]` / `[00:00:07]` 같은 절대·상대 시각 표기 — 화면엔 유용하지만
# 음성에서는 숫자 나열로 들려서 걷어낸다.
_BRACKET_TIME_RE = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]")

# `/path/to/video.mp4@01:23` 또는 `video@01:23` → "1분 23초 지점".
# (그대로 읽으면 "슬래시 패스 투 비디오 점 엠피포 골뱅이 영일 콜론 이삼")
# 맨 앞 `\S*`가 경로까지 통째로 먹는다. 반대로 맨몸 `01:23`은 건드리지 않는다 —
# 시각 표기("15:01에 만나기로")를 "15분 1초 지점"으로 바꿔버리면 오히려 틀린다.
_VIDEO_LINK_RE = re.compile(r"\S*@(\d{1,2}):(\d{2})")

_MARKDOWN_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_WHITESPACE_RE = re.compile(r"\s+")

# 근거를 못 찾았을 때 사용자에게 그대로 들려주는 문구. 이전에는 내부 판정
# 사유("텍스트 근거가 불충분합니다 — 해당 구간 영상을 Gemini(영상 입력)로
# 재조회합니다")가 그대로 붙어 나갔는데, 사용자에게 Gemini나 재조회 같은
# 내부 구현을 알릴 이유가 없고 중복 표현이라 한 문장으로 정리했다.
NOT_FOUND_SPOKEN = "그건 기록에 없어요. 대화에서도 화면에서도 확인되지 않았습니다."
NOT_FOUND_DISPLAY = "기록에 없음 — 대화·화면 어디에서도 확인되지 않았습니다."


class AnswerStatus(StrEnum):
    """답변이 어디까지 가서 나온 결과인지 — 앱이 아이콘/색을 고를 때 쓴다."""

    ANSWERED = "answered"  # 텍스트 기록만으로 답함
    ANSWERED_FROM_VIDEO = "answered_from_video"  # 텍스트론 부족해 영상 재조회로 답함
    NOT_FOUND = "not_found"  # 지어내지 않고 정직하게 실패


_STATUS_LABELS: dict[AnswerStatus, str] = {
    AnswerStatus.ANSWERED: "기록 확인됨",
    AnswerStatus.ANSWERED_FROM_VIDEO: "영상에서 확인",
    AnswerStatus.NOT_FOUND: "기록에 없음",
}


@dataclass(frozen=True, slots=True)
class GlassEvidence:
    """화면에 한 줄로 올릴 근거 1건."""

    label: str  # "어제 15:01 · 제주도_여행_계획"
    video_link: str | None  # "/path/video.mp4@15:01" (탭하면 그 구간 재생)

    def to_dict(self) -> dict:
        return {"label": self.label, "video_link": self.video_link}


@dataclass(frozen=True, slots=True)
class GlassAnswer:
    """글래스 한 화면 + 한 번의 발화에 대응하는 답변."""

    status: AnswerStatus
    tts_text: str  # 스피커로 읽을 문장 (인용·링크·대괄호 없음)
    display_text: str  # 화면 본문 (역시 인용 표기 없음 — 근거는 아래 필드로 분리)
    evidence: tuple[GlassEvidence, ...]

    @property
    def status_label(self) -> str:
        return _STATUS_LABELS[self.status]

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "status_label": self.status_label,
            "tts_text": self.tts_text,
            "display_text": self.display_text,
            "evidence": [e.to_dict() for e in self.evidence],
        }


def strip_requery_sentinel(text: str) -> str:
    """영상 재조회 응답 앞머리의 `[확인됨]`/`[확인불가]` 등을 제거한다."""
    return _SENTINEL_RE.sub("", text).strip()


def _spoken_time(minutes: str, seconds: str) -> str:
    m, s = int(minutes), int(seconds)
    if m and s:
        return f"{m}분 {s}초 지점"
    if m:
        return f"{m}분 지점"
    return f"{s}초 지점"


def speakable(text: str) -> str:
    """TTS로 읽어도 자연스럽도록 표기 문자를 걷어낸다.

    화면용 텍스트를 그대로 읽으면 대괄호·슬래시·경로가 소음이 되므로,
    실제 음성으로 나갈 문자열은 반드시 이 함수를 거친다.
    """
    cleaned = strip_requery_sentinel(text)
    cleaned = _WIKILINK_RE.sub(lambda m: m.group(2) or m.group(1), cleaned)
    cleaned = _MARKDOWN_BULLET_RE.sub("", cleaned)
    cleaned = _VIDEO_LINK_RE.sub(lambda m: _spoken_time(m.group(1), m.group(2)), cleaned)
    cleaned = _BRACKET_TIME_RE.sub("", cleaned)
    cleaned = cleaned.replace("**", "").replace("#", "")
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


def relative_day_label(day: date_type | None, reference_date: date_type) -> str | None:
    """날짜를 사람이 말하듯 표현한다 — 기억 보조라 절대 날짜보다 이쪽이 자연스럽다."""
    if day is None:
        return None
    delta = (reference_date - day).days
    if delta == 0:
        return "오늘"
    if delta == 1:
        return "어제"
    if delta == 2:
        return "그저께"
    if 3 <= delta <= 6:
        return f"{delta}일 전"
    return f"{day.month}월 {day.day}일"


def _offset_phrase(video_offset_label: str | None) -> str | None:
    """`"01:20"`(영상 오프셋 mm:ss) → `"1분 20초"`.

    왜 `Citation.timestamp_label`을 안 쓰는가: 그 필드는 볼트마다 관례가 다르다.
    모의 볼트(`testdata/mock_vault/`)는 `[15:00:00]`처럼 벽시계 시각을 쓰는데,
    실제 ingest 산출물(`ingest/wiki/session_md.py`가 `format_timestamp(start_sec)`
    으로 씀)은 `[00:00:07]`처럼 세션 시작 기준 경과 시간을 쓴다. 이걸 시각으로
    포맷하면 "오늘 00:00"(자정)처럼 사용자에게 거짓말이 된다.

    반면 `video_offset_label`은 `Chunk.start_sec`("세션 시작 기준 상대 초")에서
    나오고, `chunking._to_video_offset()`이 두 관례를 모두 정규화해주므로 어느
    볼트에서도 의미가 같다. 영상 딥링크가 가리키는 지점과도 정확히 일치한다.
    """
    if not video_offset_label:
        return None
    match = re.fullmatch(r"(\d+):(\d{2})", video_offset_label)
    if not match:
        return None
    minutes, seconds = int(match.group(1)), int(match.group(2))
    if minutes and seconds:
        return f"{minutes}분 {seconds}초"
    if minutes:
        return f"{minutes}분"
    return f"{seconds}초"


def compact_evidence_label(citation: Citation, reference_date: date_type) -> str:
    """ "어제 1분 20초 · 제주도_여행_계획" 형태의 짧은 근거 라벨."""
    parts: list[str] = []
    day = relative_day_label(citation.date, reference_date)
    offset = _offset_phrase(citation.video_offset_label)
    when = " ".join(p for p in (day, offset) if p)
    if when:
        parts.append(when)
    if citation.session_title:
        parts.append(citation.session_title)
    return " · ".join(parts) if parts else citation.label


def build_glass_answer(
    *,
    status: AnswerStatus,
    body: str,
    citations: Sequence[Citation],
    reference_date: date_type,
    max_evidence: int = MAX_GLASS_EVIDENCE,
) -> GlassAnswer:
    """답변 본문 + 근거를 글래스용 음성/화면 두 형태로 만든다.

    Args:
        status: 어느 경로로 나온 답인지(`AnswerStatus`).
        body: 인용 표기가 붙지 않은 답변 본문. 근거를 못 찾은 경우엔 무시되고
            사용자용 "기록에 없음" 문구로 대체된다.
        citations: 실제 검색된 근거. 화면에는 앞에서 `max_evidence`건만 올린다.
        reference_date: "오늘"로 취급할 날짜(상대 시각 표기 기준).
    """
    if status is AnswerStatus.NOT_FOUND:
        # 근거가 없을 때는 모델/판정 문구를 그대로 내보내지 않는다 — 사용자에게
        # 필요한 정보는 "없다"는 사실 하나뿐이다.
        return GlassAnswer(
            status=status,
            tts_text=NOT_FOUND_SPOKEN,
            display_text=NOT_FOUND_DISPLAY,
            evidence=(),
        )

    display_text = strip_requery_sentinel(body).strip()
    evidence = tuple(
        GlassEvidence(
            label=compact_evidence_label(c, reference_date),
            video_link=c.video_link,
        )
        for c in list(citations)[:max_evidence]
    )
    return GlassAnswer(
        status=status,
        tts_text=speakable(body),
        display_text=display_text,
        evidence=evidence,
    )
