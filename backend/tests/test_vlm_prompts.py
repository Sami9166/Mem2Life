"""`ingest.vlm.prompts` 순수 함수 테스트 (네트워크 무관)."""

from __future__ import annotations

from ingest.stt.base import Transcript, TranscriptSegment
from ingest.vlm import prompts
from recall.fallback.self_assessment import _NO_INFO_MARKERS


def test_caption_system_instruction_contains_all_self_assessment_markers() -> None:
    """`recall/fallback/self_assessment.py`가 인식하는 불확실성 문구를
    프롬프트가 하나라도 빠뜨리면 fallback 안전장치가 조용히 깨진다 —
    두 파일이 같은 문구 목록을 유지하도록 하는 회귀 테스트."""
    for marker in _NO_INFO_MARKERS:
        assert marker in prompts.CAPTION_SYSTEM_INSTRUCTION, f"프롬프트에 '{marker}' 문구가 빠져 있습니다"


def test_build_caption_context_includes_only_window_before_timestamp() -> None:
    transcript = Transcript(
        segments=[
            TranscriptSegment(0.0, 4.0, "화자1", "너무 이른 발화"),
            TranscriptSegment(150.0, 154.0, "화자1", "적당히 최근 발화"),
            TranscriptSegment(170.0, 174.0, "화자2", "미래 발화(키프레임 이후)"),
        ],
        provider="rtzr-stub",
    )
    context = prompts.build_caption_context(transcript, keyframe_timestamp_sec=160.0)

    assert "적당히 최근 발화" in context
    assert "너무 이른 발화" not in context  # 60초 창(window, 100~160초) 밖
    assert "미래 발화" not in context  # 키프레임 이후 발화는 컨텍스트가 아님


def test_build_caption_context_empty_transcript_returns_placeholder() -> None:
    empty = Transcript(segments=[], provider="rtzr-stub")
    assert prompts.build_caption_context(empty, keyframe_timestamp_sec=10.0) == "(직전 전사록 없음)"


def test_build_summary_prompt_includes_participants_transcript_and_captions() -> None:
    transcript = Transcript(
        segments=[TranscriptSegment(0.0, 4.0, "화자1", "제주도 여행 가자.")],
        provider="rtzr-stub",
    )
    prompt = prompts.build_summary_prompt(
        transcript, [(5.0, 5.0, "지도를 펼쳐 보인다.")], participants=["민수", "현우"]
    )

    assert "참석자: 민수, 현우" in prompt
    assert "제주도 여행 가자." in prompt
    assert "지도를 펼쳐 보인다." in prompt


def test_build_summary_prompt_handles_no_participants() -> None:
    transcript = Transcript(segments=[], provider="rtzr-stub")
    prompt = prompts.build_summary_prompt(transcript, [], participants=[])

    assert "참석자 정보 없음" in prompt
    assert "(전사록 없음)" in prompt
    assert "(장면 캡션 없음)" in prompt
