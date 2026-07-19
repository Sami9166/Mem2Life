from __future__ import annotations

from pathlib import Path

import pytest

from ingest.audio import (
    AudioExtractionError,
    ExtractedAudio,
    FFmpegNotFoundError,
    extract_audio,
    probe_duration,
)


def test_extract_audio_produces_wav(dummy_video: Path, tmp_path: Path) -> None:
    output = tmp_path / "out.wav"
    result = extract_audio(dummy_video, output)

    assert isinstance(result, ExtractedAudio)
    assert result.path == output
    assert output.exists()
    assert output.stat().st_size > 0
    assert result.sample_rate == 16_000
    assert result.channels == 1
    assert result.duration_sec == pytest.approx(3.0, abs=0.5)


def test_extract_audio_default_output_path(dummy_video: Path) -> None:
    result = extract_audio(dummy_video)
    try:
        assert result.path == dummy_video.with_suffix(".wav")
        assert result.path.exists()
    finally:
        result.path.unlink(missing_ok=True)


def test_extract_audio_missing_input_raises() -> None:
    with pytest.raises(FileNotFoundError):
        extract_audio(Path("/no/such/video.mp4"))


def test_probe_duration(dummy_video: Path) -> None:
    duration = probe_duration(dummy_video)
    assert duration == pytest.approx(3.0, abs=0.5)


def test_probe_duration_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        probe_duration(Path("/no/such/file.wav"))


def test_extract_audio_no_audio_track_raises_targeted_error(
    dummy_video_no_audio: Path, tmp_path: Path
) -> None:
    """오디오 트랙이 없는 영상은 ffmpeg의 원시 stderr 배너가 아니라 명확한
    한국어 메시지("이 영상에는 오디오 트랙이 없습니다.")로 실패해야 한다."""
    with pytest.raises(AudioExtractionError, match="오디오 트랙이 없습니다"):
        extract_audio(dummy_video_no_audio, tmp_path / "out.wav")


def test_extract_audio_missing_ffmpeg_raises(
    dummy_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ffmpeg(또는 ffprobe)가 PATH에 없으면 FFmpegNotFoundError로 명확히 실패해야 한다."""
    monkeypatch.setattr("ingest.audio.shutil.which", lambda _name: None)

    with pytest.raises(FFmpegNotFoundError):
        extract_audio(dummy_video, tmp_path / "out.wav")
