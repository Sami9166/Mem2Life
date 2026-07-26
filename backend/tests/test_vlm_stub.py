"""`ingest.vlm.stub`의 플레이스홀더 구현체 테스트."""

from __future__ import annotations

from pathlib import Path

from ingest.stt.base import Transcript
from ingest.visual import ProcessedKeyframe
from ingest.vlm.stub import PlaceholderLLMSummarizer, PlaceholderVLMCaptioner


def test_placeholder_captioner_embeds_keyframe_image_with_todo_marker(tmp_path: Path) -> None:
    keyframe = ProcessedKeyframe(
        timestamp_str="00:05", timestamp_sec=5.0, image_path=tmp_path / "keyframe_00m05s.jpg"
    )
    captioner = PlaceholderVLMCaptioner()

    results = captioner.caption_keyframes(
        [keyframe], Transcript(segments=[], provider="rtzr-stub"), media_slug="2026-07-17_1500_test"
    )

    assert len(results) == 1
    start_sec, end_sec, text = results[0]
    assert start_sec == end_sec == 5.0
    assert "![[media/2026-07-17_1500_test/keyframe_00m05s.jpg]]" in text
    assert "TODO" in text  # recall/vault/chunking.py의 _is_placeholder()가 인덱싱에서 제외하는 표식


def test_placeholder_captioner_empty_keyframes_returns_empty_list() -> None:
    captioner = PlaceholderVLMCaptioner()
    assert (
        captioner.caption_keyframes([], Transcript(segments=[], provider="rtzr-stub"), media_slug="x") == []
    )


def test_placeholder_summarizer_returns_none() -> None:
    """`None`을 반환해 `ingest/wiki/session_md.py`의 기존 TODO 플레이스홀더에 위임해야 한다."""
    summarizer = PlaceholderLLMSummarizer()
    result = summarizer.summarize_session(
        Transcript(segments=[], provider="rtzr-stub"), captions=[], participants=["민수"]
    )
    assert result is None
