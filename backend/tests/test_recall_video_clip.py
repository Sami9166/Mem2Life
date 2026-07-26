"""fallback 영상 클립 추출(ffmpeg) 단위 테스트.

`conftest.py`의 `dummy_video`(3초 파란 화면 + 440Hz 사인파) fixture로
실제 ffmpeg 클립 추출을 검증한다 — 글래스/실촬영 영상 없이 노트북의
ffmpeg만으로 동작해야 한다는 원칙(CLAUDE.md)에 맞춘다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ingest.audio import probe_duration
from recall.fallback.video_clip import ClipExtractionError, extract_clip


def test_extract_clip_reencode_has_expected_duration(dummy_video: Path, tmp_path: Path) -> None:
    out = tmp_path / "clip.mp4"
    result = extract_clip(dummy_video, 1.0, 2.5, out, reencode=True)
    assert result == out
    assert out.exists() and out.stat().st_size > 0
    # 재인코딩이면 정확히 요청한 길이(1.5초)에 가깝다.
    assert abs(probe_duration(out) - 1.5) < 0.3


def test_extract_clip_copy_mode_produces_nonempty_file(dummy_video: Path, tmp_path: Path) -> None:
    # 기본(-c copy)은 키프레임 정렬로 길이가 약간 길 수 있으므로 길이 대신
    # "유효한 비어있지 않은 클립이 생겼는지"만 검증한다.
    out = extract_clip(dummy_video, 0.5, 2.0, tmp_path / "copy.mp4")
    assert out.exists() and out.stat().st_size > 0
    assert probe_duration(out) > 0


def test_extract_clip_default_output_is_tempfile(dummy_video: Path) -> None:
    out = extract_clip(dummy_video, 0.0, 1.0)
    try:
        assert out.exists() and out.stat().st_size > 0
        assert out.name.startswith("requery_clip_")
    finally:
        out.unlink(missing_ok=True)


def test_extract_clip_clamps_negative_start(dummy_video: Path, tmp_path: Path) -> None:
    out = extract_clip(dummy_video, -5.0, 1.0, tmp_path / "clamped.mp4")
    assert out.exists() and out.stat().st_size > 0


def test_extract_clip_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        extract_clip(tmp_path / "nope.mp4", 0.0, 1.0, tmp_path / "out.mp4")


def test_extract_clip_invalid_range_raises(dummy_video: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        extract_clip(dummy_video, 2.0, 2.0, tmp_path / "out.mp4")
    with pytest.raises(ValueError):
        extract_clip(dummy_video, 3.0, 1.0, tmp_path / "out.mp4")


def test_extract_clip_corrupt_source_raises_clip_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a real video")
    with pytest.raises(ClipExtractionError):
        extract_clip(bad, 0.0, 1.0, tmp_path / "out.mp4")
