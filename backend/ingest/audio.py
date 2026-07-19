"""영상 파일에서 오디오 트랙을 추출한다 (ffmpeg 래퍼).

STT(리턴제로/Clova) API는 대부분 16kHz 모노 WAV 입력을 기대하므로 기본
추출 포맷을 그에 맞춘다. ffmpeg 바이너리가 PATH에 없으면 명확한 에러를
낸다 — 실기기 없이도 노트북에 ffmpeg만 설치돼 있으면 동작해야 한다.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_CHANNELS = 1


class FFmpegNotFoundError(RuntimeError):
    """ffmpeg(또는 ffprobe) 바이너리를 PATH에서 찾지 못했을 때."""


class AudioExtractionError(RuntimeError):
    """ffmpeg 실행은 됐지만 오디오 추출에 실패했을 때 (ffmpeg stderr 포함)."""


@dataclass(frozen=True, slots=True)
class ExtractedAudio:
    """오디오 추출 결과."""

    path: Path
    sample_rate: int
    channels: int
    duration_sec: float


def _require_binary(name: str) -> str:
    binary = shutil.which(name)
    if binary is None:
        raise FFmpegNotFoundError(
            f"'{name}' 실행 파일을 찾을 수 없습니다. macOS: `brew install ffmpeg`로 설치 후 다시 시도하세요."
        )
    return binary


def probe_duration(media_path: Path) -> float:
    """ffprobe로 영상/오디오 파일의 길이(초)를 조회한다."""
    media_path = Path(media_path)
    if not media_path.exists():
        raise FileNotFoundError(f"입력 파일이 존재하지 않습니다: {media_path}")

    ffprobe = _require_binary("ffprobe")
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AudioExtractionError(f"ffprobe 실행 실패 ({media_path}):\n{result.stderr}")
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise AudioExtractionError(f"ffprobe 출력에서 길이를 파싱할 수 없습니다: {result.stdout!r}") from exc


def _has_audio_stream(video_path: Path, ffprobe: str) -> bool:
    """ffprobe로 입력 파일에 오디오 스트림이 하나라도 있는지 확인한다."""
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(video_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AudioExtractionError(f"ffprobe 스트림 조회 실패 ({video_path}):\n{result.stderr}")
    return bool(result.stdout.strip())


def extract_audio(
    video_path: Path,
    output_path: Path | None = None,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
    overwrite: bool = True,
) -> ExtractedAudio:
    """영상 파일에서 오디오 트랙을 WAV(PCM 16-bit)로 추출한다.

    Args:
        video_path: 입력 영상 파일 경로 (mp4 등, 글래스 스트림/폰 촬영 모두 동일 경로).
        output_path: 추출 결과 WAV 경로. 생략 시 `video_path`와 같은 디렉토리에
            `<stem>.wav`로 저장한다.
        sample_rate: 출력 샘플레이트(Hz). STT 엔진 기본값인 16kHz.
        channels: 출력 채널 수. 화자분리 STT는 보통 모노(1) 입력을 기대한다.
        overwrite: 기존 파일이 있으면 덮어쓸지 여부.

    Returns:
        ExtractedAudio: 추출된 오디오 파일 경로와 메타데이터.

    Raises:
        FileNotFoundError: 입력 영상 파일이 없을 때.
        FFmpegNotFoundError: ffmpeg(또는 ffprobe)가 설치돼 있지 않을 때.
        AudioExtractionError: ffmpeg가 0이 아닌 코드로 종료했을 때, 또는
            입력 영상에 오디오 트랙 자체가 없을 때(명확한 한국어 메시지로
            구분해서 알려준다 — ffmpeg의 원시 stderr 배너를 그대로 노출하지 않음).
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"입력 영상 파일이 존재하지 않습니다: {video_path}")

    ffmpeg = _require_binary("ffmpeg")
    ffprobe = _require_binary("ffprobe")

    if not _has_audio_stream(video_path, ffprobe):
        raise AudioExtractionError(f"이 영상에는 오디오 트랙이 없습니다: {video_path}")

    if output_path is None:
        output_path = video_path.with_suffix(".wav")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg,
        "-y" if overwrite else "-n",
        "-i",
        str(video_path),
        "-vn",  # 비디오 스트림 제거
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise AudioExtractionError(f"오디오 추출 실패 ({video_path} → {output_path}):\n{result.stderr}")
    if not output_path.exists():
        raise AudioExtractionError(
            f"ffmpeg는 성공했다고 보고했지만 출력 파일이 생성되지 않았습니다: {output_path}"
        )

    duration = probe_duration(output_path)
    return ExtractedAudio(
        path=output_path,
        sample_rate=sample_rate,
        channels=channels,
        duration_sec=duration,
    )
