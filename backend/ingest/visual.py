"""영상에서 사건 경계(Event Boundary)를 찾아 대표 키프레임을 추출한다.

VLM 캡셔닝 이전 단계로, "어떤 프레임이 캡션을 붙일 가치가 있는가"만 정제한다.
실제 캡션 생성(무엇이 찍혔는지 서술)과 "주요 순간" 판단(무엇이 중요한지)은
다음 단계(VLM/LLM)의 몫이며, 이 모듈은 그 후보 프레임과 타임스탬프만 만든다
(`## 장면 캡션`과 `## 주요 순간`은 서로 다른 섹션이라는 점에 주의 — 여기서
찾은 "장면이 바뀐 지점"이 곧바로 "중요한 순간"인 것은 아니다).

파이프라인 (4단계):
    1단계 Temporal Frame Skip  — 원본에서 `sample_fps`만큼 서브샘플링한 뒤,
                                 직전에 유지한 프레임과 픽셀 차이가 작으면 버림
    2단계 Event Boundary 탐지  — 픽셀 차이가 크게 튀는 지점을 사건 경계로 판정
                                 (`BoundaryDetector` Protocol — 기본 구현은 픽셀
                                 차이 기반이고, 추후 CLIP/SigLIP 임베딩 코사인
                                 유사도 기반 구현체로 스캔 루프 변경 없이 교체
                                 가능하도록 분리해뒀다)
    3단계 대표 프레임 선정      — 사건 구간 내에서 가장 선명한
                                 (Laplacian 분산 최대) 프레임 1장
    4단계 이미지 저장 + 메타데이터 반환

임계값은 영상마다 다른 움직임 양(예: 삼각대 고정 촬영 vs 손에 든 카메라의
지속적인 흔들림)에 맞춰 자동으로 정해진다 — 이 영상 자체의 프레임 간 차이값
분포에서 백분위수를 뽑아 씀(`frame_diff_threshold`/`boundary_threshold`를
명시하면 그 값으로 고정). 실제 스마트폰 촬영본(`video/*.mp4`)으로 확인해보면
손 떨림만으로도 최소 차이값이 고정 임계값 하나로는 정적/동적 구간을 잘
못 가르는 경우가 많아, 절대값 상수 대신 이 방식을 택했다.

메모리 절약을 위해 스캔 과정은 축소·그레이스케일 프레임만 들고 있고, 실제
Vault에 저장하는 원본 화질 프레임은 사건 구간이 끝난 뒤 그 타임스탬프로
다시 seek해서 한 장만 디코딩한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

import cv2
import numpy as np

from .audio import probe_duration

DEFAULT_SAMPLE_FPS = 4.0
DEFAULT_SKIP_PERCENTILE = 30.0
DEFAULT_BOUNDARY_PERCENTILE = 90.0
DEFAULT_MIN_EVENT_GAP_SEC = 2.0

# 정적인 세션(대화만 하고 카메라가 거의 안 움직이는 경우)에서 센서 노이즈만으로
# 백분위수 임계값이 지나치게 낮아져 노이즈를 사건으로 오판하지 않도록 하는 하한.
_MIN_SKIP_THRESHOLD = 0.5
_MIN_BOUNDARY_THRESHOLD = 5.0

_SCAN_WIDTH = 320  # 스캔(차이 계산·선명도 판정)용 축소 프레임 폭 — 연산량 절감

EventType = Literal["scene_change"]


class VideoOpenError(RuntimeError):
    """OpenCV가 영상 파일을 열지 못했을 때(코덱 미지원 등)."""


class BoundaryDetector(Protocol):
    """두 스캔 프레임(축소·그레이스케일) 사이가 '사건 경계'인지 판정하는 인터페이스.

    기본 구현은 픽셀 차이 기반(`PixelDiffBoundaryDetector`)이다. CLIP/SigLIP
    임베딩 코사인 유사도 기반 구현체로 교체하려면 이 Protocol만 만족하는
    클래스를 만들어 `process_video(boundary_detector=...)`로 넘기면 되고,
    스캔 루프(`process_video`)는 손댈 필요가 없다.
    """

    def is_boundary(self, prev_frame: np.ndarray, curr_frame: np.ndarray) -> bool: ...


@dataclass(frozen=True, slots=True)
class PixelDiffBoundaryDetector:
    """인접 프레임 간 평균 절대 픽셀 차이가 임계값을 넘으면 경계로 판정하는 기본 구현체."""

    threshold: float

    def is_boundary(self, prev_frame: np.ndarray, curr_frame: np.ndarray) -> bool:
        return bool(cv2.absdiff(prev_frame, curr_frame).mean() > self.threshold)


@dataclass(frozen=True, slots=True)
class ProcessedKeyframe:
    """세션 md `## 장면 캡션`/`video@mm:ss` 링크용으로 저장된 대표 프레임 1개."""

    timestamp_str: str
    timestamp_sec: float
    image_path: Path
    event_type: EventType = "scene_change"


@dataclass(frozen=True, slots=True)
class VisualProcessingResult:
    """`process_video()` 실행 결과."""

    session_duration_sec: float
    processed_keyframes: list[ProcessedKeyframe] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _SampledFrame:
    """1단계 서브샘플링 직후의 프레임 — 영상 파일 기준 로컬 타임스탬프(0초부터)."""

    timestamp_sec: float
    gray: np.ndarray


@dataclass(slots=True)
class _EventCandidate:
    """사건 구간 하나의 대표 프레임 후보 — 선명도가 가장 높은 프레임을 계속 갱신한다."""

    timestamp_sec: float = 0.0
    sharpness: float = -1.0

    def consider(self, timestamp_sec: float, sharpness: float) -> None:
        if sharpness > self.sharpness:
            self.timestamp_sec = timestamp_sec
            self.sharpness = sharpness


def _require_video_capture(video_path: Path) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise VideoOpenError(f"OpenCV가 영상 파일을 열지 못했습니다: {video_path}")
    return cap


def _format_timestamp(seconds: float) -> str:
    """`video@mm:ss` 링크 규격에 맞춘 `mm:ss` 포맷(코드베이스 전체에서 쓰는 형식)."""
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


def _keyframe_filename(timestamp_str: str) -> str:
    minutes, seconds = timestamp_str.split(":")
    return f"keyframe_{minutes}m{seconds}s.jpg"


def _scan_frame(frame: np.ndarray) -> np.ndarray:
    """스캔(차이 계산·선명도 판정)용으로 축소한 그레이스케일 프레임."""
    height, width = frame.shape[:2]
    scale = _SCAN_WIDTH / width
    resized = cv2.resize(frame, (_SCAN_WIDTH, max(1, round(height * scale))))
    return cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)


def _sharpness(gray_frame: np.ndarray) -> float:
    """Laplacian 분산 — 값이 클수록 포커스가 잘 맞고 흔들림이 적은 프레임."""
    return float(cv2.Laplacian(gray_frame, cv2.CV_64F).var())


def _sample_frames(video_path: Path, sample_fps: float) -> list[_SampledFrame]:
    """원본 영상에서 `sample_fps`만큼 서브샘플링해 축소·그레이스케일 프레임을 모은다."""
    cap = _require_video_capture(video_path)
    try:
        native_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        stride = max(1, round(native_fps / sample_fps))
        frames: list[_SampledFrame] = []
        frame_idx = 0
        while True:
            ok = cap.grab()
            if not ok:
                break
            if frame_idx % stride == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                frames.append(_SampledFrame(timestamp_sec=frame_idx / native_fps, gray=_scan_frame(frame)))
            frame_idx += 1
        return frames
    finally:
        cap.release()


def _detect_events(
    frames: list[_SampledFrame],
    *,
    skip_threshold: float,
    min_event_gap_sec: float,
    detector: BoundaryDetector,
) -> list[_EventCandidate]:
    """1단계(정적 프레임 스킵) + 2단계(사건 경계 탐지) + 3단계(구간별 대표 프레임 선정)."""
    segments = [_EventCandidate()]
    last_kept: _SampledFrame | None = None
    last_boundary_sec = -min_event_gap_sec

    for sample in frames:
        if last_kept is not None:
            diff = float(cv2.absdiff(last_kept.gray, sample.gray).mean())
            if diff < skip_threshold:
                continue  # 1단계: 직전 유지 프레임과 거의 같음 — 정적 구간으로 보고 버림

            if sample.timestamp_sec - last_boundary_sec >= min_event_gap_sec and detector.is_boundary(
                last_kept.gray, sample.gray
            ):
                segments.append(_EventCandidate())
                last_boundary_sec = sample.timestamp_sec

        segments[-1].consider(sample.timestamp_sec, _sharpness(sample.gray))
        last_kept = sample

    return [candidate for candidate in segments if candidate.sharpness >= 0]


def _save_keyframes(
    video_path: Path,
    candidates: list[_EventCandidate],
    session_media_dir: Path,
    start_offset_sec: float,
) -> list[ProcessedKeyframe]:
    """선정된 각 (영상 로컬) 타임스탬프로 다시 seek해 원본 화질 프레임 1장씩 jpg로 저장한다.

    타임스탬프는 `mm:ss`(초 단위)로 반올림하므로, 서로 다른 사건 구간의 대표
    프레임이 우연히 같은 초에 찍힐 수 있다(예: 한 구간의 마지막 프레임과 다음
    구간의 첫 프레임이 경계 바로 앞뒤라서 둘 다 23~24초 근처). 파일명이
    겹치면 뒤 프레임이 앞 프레임을 덮어써 결과 목록과 실제 저장된 이미지가
    어긋나므로, 같은 세션 안에서 파일명이 겹치면 `_2`, `_3`, ... 를 붙여 구분한다.
    """
    cap = _require_video_capture(video_path)
    try:
        results: list[ProcessedKeyframe] = []
        seen_filenames: dict[str, int] = {}
        for candidate in candidates:
            cap.set(cv2.CAP_PROP_POS_MSEC, candidate.timestamp_sec * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            session_timestamp_sec = start_offset_sec + candidate.timestamp_sec
            timestamp_str = _format_timestamp(session_timestamp_sec)
            filename = _keyframe_filename(timestamp_str)
            occurrence = seen_filenames.get(filename, 0) + 1
            seen_filenames[filename] = occurrence
            if occurrence > 1:
                stem, suffix = filename.rsplit(".", 1)
                filename = f"{stem}_{occurrence}.{suffix}"
            image_path = session_media_dir / filename
            if not cv2.imwrite(str(image_path), frame):
                continue  # 디스크 풀 등으로 저장 실패 — 존재하지 않는 파일을 가리키는 항목을 반환하지 않는다
            results.append(
                ProcessedKeyframe(
                    timestamp_str=timestamp_str,
                    timestamp_sec=session_timestamp_sec,
                    image_path=image_path,
                )
            )
        return results
    finally:
        cap.release()


def process_video(
    video_path: Path | str,
    *,
    media_dir: Path | str,
    session_id: str,
    start_offset_sec: float = 0.0,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
    frame_diff_threshold: float | None = None,
    boundary_threshold: float | None = None,
    min_event_gap_sec: float = DEFAULT_MIN_EVENT_GAP_SEC,
    boundary_detector: BoundaryDetector | None = None,
) -> VisualProcessingResult:
    """영상에서 정적 구간을 버리고, 사건 경계마다 대표 키프레임 1장을 저장한다.

    Args:
        video_path: 입력 영상 경로 (30초 청크 또는 세션 전체 영상 — 둘 다
            "프레임이 있는 mp4 파일"이라는 점에서 이 함수 입장에서는 동일하다).
        media_dir: 키프레임 이미지를 저장할 Vault 미디어 루트(`vault/media/`).
            세션별 파일명 충돌을 피하려고 이 함수가 그 아래 `session_id`
            서브디렉토리를 만든다.
        session_id: 세션을 식별하는 문자열(세션 md 파일명과 동일하게 맞추는
            것을 권장). 청크 단위로 이 함수를 여러 번 호출해도 같은
            `session_id`를 넘기면 같은 미디어 폴더에 쌓인다.
        start_offset_sec: 이 영상이 세션 전체 타임라인에서 시작하는 상대 시간
            (초). 30초 청크를 세션 전체 영상 대신 넘길 때, 청크 자체는
            0초부터 시작하지만 세션 기준 타임스탬프는 이만큼 밀려야 한다.
            세션 전체 영상을 통째로 넘기는 현재 CLI 흐름에서는 0(기본값).
        sample_fps: 원본 프레임에서 1초당 몇 프레임을 샘플링할지(1단계 서브샘플링).
        frame_diff_threshold: 1단계 "정적 프레임" 판정 임계값. 생략하면 이
            영상 자체의 프레임 간 차이값 분포에서 하위 `DEFAULT_SKIP_PERCENTILE`
            백분위수로 자동 계산한다.
        boundary_threshold: 2단계 "사건 경계" 판정 임계값. 생략하면 이 영상
            자체의 차이값 분포에서 상위 `DEFAULT_BOUNDARY_PERCENTILE`
            백분위수로 자동 계산한다. `boundary_detector`를 직접 넘기면 무시된다.
        min_event_gap_sec: 경계 판정 후 이 시간 안에 또 경계가 잡히면 무시한다
            (노이즈로 인한 과분할 방지 디바운스).
        boundary_detector: 2단계 구현체를 완전히 교체하고 싶을 때
            (예: CLIP 임베딩 기반). 생략하면 `PixelDiffBoundaryDetector`를
            자동 계산된(또는 명시된) `boundary_threshold`로 사용한다.

    Returns:
        VisualProcessingResult: 세션 길이와 저장된 키프레임 목록. 영상 전체에서
        사건 경계가 하나도 안 잡혀도(예: 정적인 짧은 세션) 빈 리스트를 반환하지
        않고 영상 중간 지점 프레임 1장을 대표 키프레임으로 저장한다 — 세션 md의
        `## 장면 캡션`이 완전히 비는 상황을 피하기 위한 최소 보장이다(오디오 STT
        실패 시 스텁으로라도 전사록을 채우는 `pipeline.py`의 원칙과 동일).

    Raises:
        FileNotFoundError: 입력 영상 파일이 없을 때.
        VideoOpenError: OpenCV가 영상을 열지 못했을 때(코덱 미지원 등).
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"입력 영상 파일이 존재하지 않습니다: {video_path}")

    duration_sec = probe_duration(video_path)
    session_media_dir = Path(media_dir) / session_id
    session_media_dir.mkdir(parents=True, exist_ok=True)

    frames = _sample_frames(video_path, sample_fps)

    if len(frames) < 2:
        candidates = [_EventCandidate(timestamp_sec=duration_sec / 2, sharpness=0.0)]
    else:
        diffs = [float(cv2.absdiff(frames[i - 1].gray, frames[i].gray).mean()) for i in range(1, len(frames))]
        skip_threshold = (
            frame_diff_threshold
            if frame_diff_threshold is not None
            else max(_MIN_SKIP_THRESHOLD, float(np.percentile(diffs, DEFAULT_SKIP_PERCENTILE)))
        )
        detector = boundary_detector or PixelDiffBoundaryDetector(
            threshold=(
                boundary_threshold
                if boundary_threshold is not None
                else max(_MIN_BOUNDARY_THRESHOLD, float(np.percentile(diffs, DEFAULT_BOUNDARY_PERCENTILE)))
            )
        )
        candidates = _detect_events(
            frames,
            skip_threshold=skip_threshold,
            min_event_gap_sec=min_event_gap_sec,
            detector=detector,
        )
        if not candidates:
            candidates = [_EventCandidate(timestamp_sec=duration_sec / 2, sharpness=0.0)]

    processed = _save_keyframes(video_path, candidates, session_media_dir, start_offset_sec)

    return VisualProcessingResult(session_duration_sec=duration_sec, processed_keyframes=processed)
