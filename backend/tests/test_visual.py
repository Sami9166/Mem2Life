from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from ingest.visual import (
    PixelDiffBoundaryDetector,
    ProcessedKeyframe,
    VideoOpenError,
    VisualProcessingResult,
    _EventCandidate,
    _save_keyframes,
    process_video,
)


def test_process_video_static_clip_falls_back_to_one_keyframe(dummy_video: Path, tmp_path: Path) -> None:
    """정적인(색상 배경 고정) 영상은 사건 경계가 안 잡혀도 키프레임 1장은 보장돼야 한다."""
    result = process_video(dummy_video, media_dir=tmp_path / "media", session_id="s1")

    assert isinstance(result, VisualProcessingResult)
    assert result.session_duration_sec == pytest.approx(3.0, abs=0.5)
    assert len(result.processed_keyframes) == 1

    keyframe = result.processed_keyframes[0]
    assert isinstance(keyframe, ProcessedKeyframe)
    assert keyframe.image_path.exists()
    assert keyframe.image_path.stat().st_size > 0
    assert keyframe.event_type == "scene_change"


def test_process_video_detects_scene_change(dummy_video_with_scene_change: Path, tmp_path: Path) -> None:
    """파랑->빨강 전환이 있는 영상은 최소 2개의 서로 다른 키프레임을 만들어야 한다."""
    result = process_video(dummy_video_with_scene_change, media_dir=tmp_path / "media", session_id="s2")

    assert len(result.processed_keyframes) >= 2
    timestamps = [kf.timestamp_sec for kf in result.processed_keyframes]
    assert timestamps == sorted(timestamps)
    # 첫 키프레임은 파란 구간(전환 이전), 마지막은 빨간 구간(전환 이후)에서 나와야 한다.
    assert timestamps[0] < 1.5 < timestamps[-1]


def test_process_video_saves_keyframes_under_session_subdir(dummy_video: Path, tmp_path: Path) -> None:
    """서로 다른 세션의 키프레임은 같은 media_dir 아래에서도 파일명이 충돌하지 않아야 한다."""
    media_dir = tmp_path / "media"
    result_a = process_video(dummy_video, media_dir=media_dir, session_id="session-a")
    result_b = process_video(dummy_video, media_dir=media_dir, session_id="session-b")

    path_a = result_a.processed_keyframes[0].image_path
    path_b = result_b.processed_keyframes[0].image_path

    assert path_a != path_b
    assert path_a.parent == media_dir / "session-a"
    assert path_b.parent == media_dir / "session-b"
    assert path_a.exists()
    assert path_b.exists()


def test_process_video_timestamp_format_matches_video_link_convention(
    dummy_video: Path, tmp_path: Path
) -> None:
    keyframe = process_video(dummy_video, media_dir=tmp_path / "media", session_id="s1").processed_keyframes[
        0
    ]
    assert keyframe.timestamp_str.count(":") == 1
    minutes, seconds = keyframe.timestamp_str.split(":")
    assert len(minutes) == 2 and minutes.isdigit()
    assert len(seconds) == 2 and seconds.isdigit()
    assert keyframe.image_path.name == f"keyframe_{minutes}m{seconds}s.jpg"


def test_process_video_start_offset_shifts_output_timestamps_only(dummy_video: Path, tmp_path: Path) -> None:
    """start_offset_sec은 반환 타임스탬프만 밀어야 하고, seek 자체는 영상 로컬 시간을 써야 한다."""
    baseline = process_video(dummy_video, media_dir=tmp_path / "media_a", session_id="s1")
    offset_result = process_video(
        dummy_video, media_dir=tmp_path / "media_b", session_id="s1", start_offset_sec=100.0
    )

    assert offset_result.processed_keyframes[0].timestamp_sec == pytest.approx(
        baseline.processed_keyframes[0].timestamp_sec + 100.0
    )


def test_process_video_missing_input_raises() -> None:
    with pytest.raises(FileNotFoundError):
        process_video(Path("/no/such/video.mp4"), media_dir=Path("/tmp/whatever"), session_id="s1")


def test_process_video_unreadable_file_raises_video_open_error(tmp_path: Path) -> None:
    bogus = tmp_path / "not_a_video.mp4"
    bogus.write_bytes(b"this is not a real video file")

    with pytest.raises((VideoOpenError, RuntimeError)):
        process_video(bogus, media_dir=tmp_path / "media", session_id="s1")


def test_process_video_explicit_thresholds_are_respected(
    dummy_video_with_scene_change: Path, tmp_path: Path
) -> None:
    """boundary_threshold를 비현실적으로 높게 주면 경계가 하나도 안 잡혀 키프레임 1장으로 폴백해야 한다."""
    result = process_video(
        dummy_video_with_scene_change,
        media_dir=tmp_path / "media",
        session_id="s1",
        boundary_threshold=1_000_000.0,
    )
    assert len(result.processed_keyframes) == 1


def test_save_keyframes_disambiguates_filename_collision_within_session(tmp_path: Path) -> None:
    """서로 다른 사건의 대표 프레임이 반올림 때문에 같은 mm:ss로 겹치면 덮어쓰지 않고 구분해야 한다.

    (실제 샘플 영상(video/20260723_172001.mp4)에서 두 사건의 대표 프레임이
    둘 다 00:24로 반올림돼, 고정 전이면 뒤 프레임이 앞 프레임을 조용히
    덮어쓰면서도 반환 목록에는 서로 다른 항목 2개가 남는 불일치가 있었다.)
    """
    video_path = tmp_path / "src.mp4"
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (16, 16))
    for shade in range(20):
        writer.write(np.full((16, 16, 3), shade * 10, dtype=np.uint8))
    writer.release()

    media_dir = tmp_path / "media"
    media_dir.mkdir()  # process_video()가 평소 이 자리에서 만들어주는 디렉토리 — 직접 호출이라 수동 생성

    # 23.6초와 23.7초 — 반올림하면 둘 다 "00:24"로 같은 파일명을 만든다.
    candidates = [
        _EventCandidate(timestamp_sec=0.1, sharpness=1.0),
        _EventCandidate(timestamp_sec=0.2, sharpness=1.0),
    ]
    keyframes = _save_keyframes(video_path, candidates, media_dir, start_offset_sec=23.5)

    assert len(keyframes) == 2
    paths = {kf.image_path for kf in keyframes}
    assert len(paths) == 2, "파일명이 겹쳐 하나가 다른 하나를 덮어썼습니다"
    for kf in keyframes:
        assert kf.image_path.exists()


def test_pixel_diff_boundary_detector_threshold() -> None:
    detector = PixelDiffBoundaryDetector(threshold=10.0)
    same = np.zeros((10, 10), dtype=np.uint8)
    different = np.full((10, 10), 255, dtype=np.uint8)

    assert detector.is_boundary(same, same) is False
    assert detector.is_boundary(same, different) is True
