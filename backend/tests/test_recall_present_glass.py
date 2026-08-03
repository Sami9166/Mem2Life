"""글래스 출력 표현(`recall/present/glass.py`) 검증.

여기서 잡으려는 건 "기술적으로는 맞는데 사람에게 내보내면 이상한" 출력이다.
데모에서 스피커로 나가는 문장이라 회귀가 생기면 바로 티가 난다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from recall.answer.base import Citation, citation_from_chunk
from recall.present.glass import (
    NOT_FOUND_DISPLAY,
    NOT_FOUND_SPOKEN,
    AnswerStatus,
    build_glass_answer,
    compact_evidence_label,
    relative_day_label,
    speakable,
    strip_requery_sentinel,
)
from recall.vault.types import Chunk, ChunkLevel, DocKind

_TODAY = date(2026, 7, 18)


def _citation(
    *,
    day: date | None = date(2026, 7, 17),
    timestamp: str | None = "[15:01:20]",
    title: str | None = "제주도_여행_계획",
    video: str | None = "/videos/a.mp4",
    start_sec: float | None = 80.0,
) -> Citation:
    chunk = Chunk(
        chunk_id="c1",
        doc_path=Path("sessions/2026-07-17_1500_제주도_여행_계획.md"),
        doc_kind=DocKind.SESSION,
        level=ChunkLevel.TRANSCRIPT,
        text="숙소는 1박 15만원 이하로 하자",
        date=day,
        session_title=title,
        timestamp_label=timestamp,
        video_path=video,
        start_sec=start_sec,
    )
    return citation_from_chunk(chunk)


# -- sentinel 제거 ------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "[확인됨] 책 제목은 침묵의 봄입니다.",
        "[확인불가] 책 제목은 침묵의 봄입니다.",
        "[영상 재조회 실패] 책 제목은 침묵의 봄입니다.",
        "  [확인됨]  책 제목은 침묵의 봄입니다.",
    ],
)
def test_requery_sentinel_never_reaches_the_user(raw: str) -> None:
    """sentinel은 grounded 판정용 내부 표식이지 사용자에게 보여줄 문구가 아니다."""
    assert strip_requery_sentinel(raw) == "책 제목은 침묵의 봄입니다."


def test_sentinel_stripping_leaves_normal_text_alone() -> None:
    assert strip_requery_sentinel("그냥 답변입니다.") == "그냥 답변입니다."


# -- TTS 다듬기 ---------------------------------------------------------------


def test_speakable_removes_wikilink_brackets() -> None:
    assert speakable("[[민수]]와 [[제주도 여행]] 얘기를 했어요.") == "민수와 제주도 여행 얘기를 했어요."


def test_speakable_uses_alias_side_of_wikilink() -> None:
    assert speakable("[[topics/제주도|제주도]] 얘기") == "제주도 얘기"


def test_speakable_removes_image_embed_marker() -> None:
    assert speakable("![[media/x/keyframe_00m00s.jpg]] 화면이 보입니다") == (
        "media/x/keyframe_00m00s.jpg 화면이 보입니다"
    )


def test_speakable_converts_video_link_to_spoken_time() -> None:
    """`/videos/a.mp4@01:23`을 그대로 읽으면 경로가 통째로 음성으로 나간다."""
    assert speakable("/videos/a.mp4@01:23 에서 확인했어요") == "1분 23초 지점 에서 확인했어요"


def test_speakable_drops_bracketed_timestamps() -> None:
    assert speakable("[15:01:20] 숙소 예산 얘기") == "숙소 예산 얘기"


def test_speakable_keeps_clock_time_in_sentence() -> None:
    """`15:01`이 문장 안의 시각일 수도 있으므로 맨몸 시각은 건드리지 않는다."""
    assert "15:01" in speakable("15:01에 만나기로 했어요")


def test_speakable_collapses_newlines_and_bullets() -> None:
    assert speakable("- 첫째 줄\n- 둘째 줄") == "첫째 줄 둘째 줄"


# -- 상대 날짜 ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 7, 18), "오늘"),
        (date(2026, 7, 17), "어제"),
        (date(2026, 7, 16), "그저께"),
        (date(2026, 7, 15), "3일 전"),
        (date(2026, 7, 12), "6일 전"),
        (date(2026, 7, 11), "7월 11일"),  # 일주일 넘어가면 절대 날짜가 낫다
        (date(2026, 7, 20), "7월 20일"),  # 미래(있으면 안 되지만) 방어
    ],
)
def test_relative_day_label(day: date, expected: str) -> None:
    assert relative_day_label(day, _TODAY) == expected


def test_relative_day_label_handles_missing_date() -> None:
    assert relative_day_label(None, _TODAY) is None


# -- 근거 라벨 ----------------------------------------------------------------


def test_compact_evidence_label_is_short_and_relative() -> None:
    label = compact_evidence_label(_citation(), _TODAY)
    assert label == "어제 1분 20초 · 제주도_여행_계획"
    # 원본 라벨(세션 '제주도_여행_계획' (2026-07-17) [15:01:20])보다 짧아야 한다.
    assert len(label) < len(_citation().label)


def test_compact_evidence_label_uses_offset_not_ambiguous_clock_label() -> None:
    """볼트마다 `timestamp_label` 관례가 달라(벽시계 vs 경과시간) 그걸 시각으로
    표시하면 거짓말이 된다 — 실제 ingest 산출물의 `[00:00:07]`을 "00:07"로 띄우면
    자정으로 읽힌다. 항상 정규화되는 `start_sec` 기반 오프셋을 써야 한다."""
    ingest_style = _citation(timestamp="[00:00:07]", start_sec=7.0)
    label = compact_evidence_label(ingest_style, _TODAY)
    assert label == "어제 7초 · 제주도_여행_계획"
    assert "00:0" not in label


def test_compact_evidence_label_falls_back_when_no_date_or_title() -> None:
    citation = _citation(day=None, timestamp=None, title=None, start_sec=None)
    assert compact_evidence_label(citation, _TODAY) == citation.label


# -- 조립 ---------------------------------------------------------------------


def test_answered_splits_speech_from_display() -> None:
    glass = build_glass_answer(
        status=AnswerStatus.ANSWERED,
        body="숙소는 1박에 15만원 이하로 정하셨어요.",
        citations=[_citation()],
        reference_date=_TODAY,
    )

    assert glass.tts_text == "숙소는 1박에 15만원 이하로 정하셨어요."
    assert glass.display_text == "숙소는 1박에 15만원 이하로 정하셨어요."
    assert glass.status_label == "기록 확인됨"
    assert glass.evidence[0].label == "어제 1분 20초 · 제주도_여행_계획"
    assert glass.evidence[0].video_link == "/videos/a.mp4@01:20"


def test_evidence_is_capped_for_small_screen() -> None:
    glass = build_glass_answer(
        status=AnswerStatus.ANSWERED,
        body="답변",
        citations=[_citation(), _citation(), _citation(), _citation()],
        reference_date=_TODAY,
    )
    assert len(glass.evidence) == 2, "480x480 화면에 3건 이상은 안 들어간다"


def test_video_answer_strips_sentinel_and_marks_status() -> None:
    glass = build_glass_answer(
        status=AnswerStatus.ANSWERED_FROM_VIDEO,
        body="[확인됨] 표지에 '침묵의 봄'이라고 적혀 있어요. /videos/a.mp4@00:05",
        citations=[_citation()],
        reference_date=_TODAY,
    )

    assert not glass.display_text.startswith("[확인됨]")
    assert not glass.tts_text.startswith("[확인됨]")
    assert "침묵의 봄" in glass.tts_text
    assert "5초 지점" in glass.tts_text
    assert glass.status_label == "영상에서 확인"


def test_not_found_uses_user_facing_wording_only() -> None:
    """근거 판정 사유(내부 구현 용어)가 사용자 문구로 새지 않아야 한다."""
    glass = build_glass_answer(
        status=AnswerStatus.NOT_FOUND,
        body="텍스트 근거가 불충분합니다 — 해당 구간 영상을 Gemini(영상 입력)로 재조회합니다.",
        citations=[_citation()],
        reference_date=_TODAY,
    )

    assert glass.tts_text == NOT_FOUND_SPOKEN
    assert glass.display_text == NOT_FOUND_DISPLAY
    for leaked in ("Gemini", "재조회", "불충분", "fallback"):
        assert leaked not in glass.tts_text
        assert leaked not in glass.display_text
    assert glass.evidence == (), "근거를 못 찾았다면서 근거를 띄우면 모순이다"


def test_to_dict_shape_is_what_the_app_consumes() -> None:
    payload = build_glass_answer(
        status=AnswerStatus.ANSWERED,
        body="답변입니다.",
        citations=[_citation()],
        reference_date=_TODAY,
    ).to_dict()

    assert set(payload) == {"status", "status_label", "tts_text", "display_text", "evidence"}
    assert set(payload["evidence"][0]) == {"label", "video_link"}
