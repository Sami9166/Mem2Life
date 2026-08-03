"""fallback 영상 재조회용 클립 추출 (ffmpeg 래퍼).

fallback이 트리거되면 원본 세션 영상 전체가 아니라 관련 구간(수십 초)만
Gemini에 넣는다 — 인라인 업로드 용량 한계를 지키고 응답 지연/비용을 줄이기
위해서다. 이 모듈은 `VideoClipTarget`(video_path + start/end 초)을 받아 그
구간만 잘라낸 임시 mp4 파일을 만든다.

`ingest/audio.py`와 같은 패턴(ffmpeg 서브프로세스 + 명확한 한국어 에러)을
따르되, recall은 ingest에 의존하지 않으므로(위키 쓰기 경로와 분리) 최소한의
ffmpeg 헬퍼를 여기에 따로 둔다.

seek 전략: `-ss`를 입력 옵션(-i 앞)으로 두어 빠른 키프레임 seek을 하고,
길이는 출력 옵션 `-t`(초 단위 지속시간)로 지정한다 — `-to`를 입력 옵션으로
쓸 때 버전에 따라 절대/상대 기준이 갈리는 모호함을 피하기 위함이다.
기본은 `-c copy`(무재인코딩, 빠름)라 컷이 키프레임 경계로 정렬되어 실제
길이가 요청보다 약간 길 수 있다 — 재조회 이해에는 문제없다. 정확한 길이가
필요하면 `reencode=True`로 재인코딩한다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class FFmpegNotFoundError(RuntimeError):
    """ffmpeg 바이너리를 PATH에서 찾지 못했을 때."""


class ClipExtractionError(RuntimeError):
    """ffmpeg 실행은 됐지만 클립 추출에 실패했을 때 (ffmpeg stderr 포함)."""


def _require_binary(name: str) -> str:
    binary = shutil.which(name)
    if binary is None:
        raise FFmpegNotFoundError(
            f"'{name}' 실행 파일을 찾을 수 없습니다. macOS: `brew install ffmpeg`로 설치 후 다시 시도하세요."
        )
    return binary


def extract_clip(
    video_path: Path | str,
    start_sec: float,
    end_sec: float,
    output_path: Path | str | None = None,
    *,
    reencode: bool = False,
) -> Path:
    """원본 영상에서 [start_sec, end_sec) 구간만 잘라 mp4로 저장한다.

    Args:
        video_path: 원본 세션 영상 경로.
        start_sec: 세션(영상) 시작 기준 클립 시작 초. 음수면 0으로 보정한다.
        end_sec: 클립 종료 초. `start_sec`보다 커야 한다.
        output_path: 저장 경로. 생략 시 임시 파일(`tempfile`)을 만들어 반환한다 —
            호출부가 사용 후 삭제할 책임을 진다(`GeminiVideoRequeryClient`는
            `TemporaryDirectory`로 감싸 자동 정리한다).
        reencode: True면 정확한 길이로 재인코딩(느림), False면 `-c copy`(빠름,
            키프레임 경계 정렬로 길이가 약간 길 수 있음).

    Returns:
        추출된 클립 파일 경로.

    Raises:
        FileNotFoundError: 원본 영상 파일이 없을 때.
        ValueError: 구간이 잘못됐을 때(end <= start).
        FFmpegNotFoundError: ffmpeg가 설치돼 있지 않을 때.
        ClipExtractionError: ffmpeg가 실패했거나 출력이 비어 있을 때.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"원본 영상 파일이 존재하지 않습니다: {video_path}")

    start_sec = max(0.0, start_sec)
    if end_sec <= start_sec:
        raise ValueError(f"클립 구간이 올바르지 않습니다 (end<=start): start={start_sec}, end={end_sec}")

    ffmpeg = _require_binary("ffmpeg")
    duration = end_sec - start_sec

    if output_path is None:
        fd, tmp_name = tempfile.mkstemp(prefix="requery_clip_", suffix=".mp4")
        # mkstemp가 연 파일 디스크립터는 바로 닫는다 — ffmpeg가 경로로 새로 연다.
        os.close(fd)
        output_path = Path(tmp_name)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [ffmpeg, "-y", "-ss", f"{start_sec:.3f}", "-i", str(video_path), "-t", f"{duration:.3f}"]
    if reencode:
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"]
    else:
        cmd += ["-c", "copy"]
    # 오디오 스트림이 없는 영상도 있으므로 -map 강제 없이 자동 매핑에 맡긴다.
    cmd += [str(output_path)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ClipExtractionError(
            f"영상 클립 추출 실패 ({video_path} [{start_sec:.1f}s~{end_sec:.1f}s]):\n{result.stderr}"
        )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise ClipExtractionError(f"ffmpeg는 성공했다고 보고했지만 클립 파일이 비어 있습니다: {output_path}")
    return output_path
