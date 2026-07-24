"""테스트 공용 픽스처.

실제 촬영 영상은 아직 없으므로(사용자가 이후 `testdata/`에 추가 예정),
ffmpeg의 lavfi 소스로 몇 초짜리 더미 영상(색상 패턴 + 사인파 오디오)을
생성해 오디오 추출~md 생성까지 전체 파이프라인을 검증한다.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

DUMMY_VIDEO_DURATION_SEC = 3

# recall 회귀 테스트용 모의 볼트 — 데모_시나리오.md 세션 A("어제")/B("오늘")를
# 그대로 재현한 손으로 작성한 Obsidian 볼트 (recall-dev 담당, wiki-builder의
# 실제 산출물이 아직 요약/캡션을 채우지 못하므로 recall을 독립적으로
# 검증하기 위한 fixture). 절대 이 디렉토리에 쓰기 작업을 하지 않는다
# (recall은 위키 파일에 대해 읽기 전용).
MOCK_VAULT_DIR = Path(__file__).resolve().parent.parent / "testdata" / "mock_vault"

# 모의 볼트 세션 A/B 날짜에 맞춘 고정 "오늘" — 회귀 테스트가 실행 시점의
# 실제 오늘 날짜와 무관하게 결정적으로 동작하도록 명시적으로 고정한다.
MOCK_VAULT_TODAY = date(2026, 7, 18)


@pytest.fixture(scope="session")
def mock_vault_dir() -> Path:
    assert MOCK_VAULT_DIR.is_dir(), f"모의 볼트를 찾을 수 없습니다: {MOCK_VAULT_DIR}"
    return MOCK_VAULT_DIR


@pytest.fixture
def mock_vault_today() -> date:
    return MOCK_VAULT_TODAY


@pytest.fixture(autouse=True)
def _no_ambient_rtzr_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """모든 테스트에서 RTZR 인증 관련 환경변수를 확실히 비운다.

    `ingest/cli.py`의 `load_dotenv()`가 실제 `backend/.env`(RTZR 실 크레덴셜
    포함)를 로드할 수 있으므로, 이 autouse 픽스처 없이는 test_cli.py의 e2e
    테스트가 먼저 실행된 뒤 같은 프로세스 안의 다른 테스트(예:
    test_stt_stubs.py::test_get_stt_client_factory)가 실제 RTZR 클라이언트를
    골라 네트워크를 태울 위험이 있다. 이 스위트는 어떤 테스트도 실제
    네트워크를 타면 안 되므로 매 테스트 시작 시 무조건 비운다 — 실제 RTZR
    API 응답을 검증하는 테스트는 httpx.MockTransport로 네트워크를 완전히
    대체하거나(`test_stt_rtzr_client.py`), RTZR_LIVE_TEST=1로 명시적으로
    옵트인하는 별도 수동 테스트여야 한다.
    """
    monkeypatch.delenv("RTZR_CLIENT_ID", raising=False)
    monkeypatch.delenv("RTZR_CLIENT_SECRET", raising=False)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture(scope="session")
def dummy_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """짧은(3초) 무음 패턴 더미 mp4 (색상 배경 + 440Hz 사인파 오디오)."""
    if not _ffmpeg_available():
        pytest.skip("ffmpeg/ffprobe가 설치돼 있지 않습니다 (brew install ffmpeg)")

    out_dir = tmp_path_factory.mktemp("dummy_video")
    video_path = out_dir / "dummy_session.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=blue:s=320x240:d={DUMMY_VIDEO_DURATION_SEC}:r=10",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={DUMMY_VIDEO_DURATION_SEC}",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(f"더미 영상 생성 실패:\n{result.stderr}")
    return video_path


@pytest.fixture(scope="session")
def dummy_video_with_scene_change(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """앞 1.5초는 파란 화면, 뒤 1.5초는 빨간 화면인 더미 mp4(10fps, 무음).

    `ingest/visual.py`의 사건 경계 탐지를 검증하려고 t=1.5s 부근에 뚜렷한
    장면 전환을 하나 심어둔 결정적(deterministic) 픽스처다.
    """
    if not _ffmpeg_available():
        pytest.skip("ffmpeg/ffprobe가 설치돼 있지 않습니다 (brew install ffmpeg)")

    out_dir = tmp_path_factory.mktemp("dummy_video_scene_change")
    video_path = out_dir / "dummy_session_scene_change.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=320x240:d=1.5:r=10",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=320x240:d=1.5:r=10",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(f"장면 전환 더미 영상 생성 실패:\n{result.stderr}")
    return video_path


@pytest.fixture(scope="session")
def dummy_video_no_audio(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """오디오 트랙이 아예 없는 더미 mp4 (색상 패턴만, 오디오 입력 없음)."""
    if not _ffmpeg_available():
        pytest.skip("ffmpeg/ffprobe가 설치돼 있지 않습니다 (brew install ffmpeg)")

    out_dir = tmp_path_factory.mktemp("dummy_video_no_audio")
    video_path = out_dir / "dummy_session_no_audio.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=red:s=320x240:d={DUMMY_VIDEO_DURATION_SEC}:r=10",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",  # 오디오 스트림 없음
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(f"오디오 없는 더미 영상 생성 실패:\n{result.stderr}")
    return video_path
