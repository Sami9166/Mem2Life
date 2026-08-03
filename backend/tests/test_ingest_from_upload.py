"""업로드 세션 글루(`tools/ingest_from_upload.py`) 검증.

실기기 경로(글래스 녹화 → 30초 mp4 청크 + PCM 스트림 → /end)에서 실제로
돌아가는 진입점이다. CLI 진입점(`mem2life-ingest`, 완성 영상 1개 입력)과 달리
테스트가 없었는데, VLM 캡션·LLM 요약을 여기에 배선하면서 "CLI에는 캡션이
붙는데 실기기 경로에는 안 붙는" 상태로 갈라지지 않도록 회귀 테스트로 고정한다.

네트워크는 타지 않는다 — conftest의 autouse fixture가 GEMINI_API_KEY를 비우므로
캡션/요약 모두 생성 시점 폴백(플레이스홀더)으로 동작한다. 다만 이 글루는
스크립트라 자체적으로 `load_dotenv(".env")`를 호출하므로, 실제 `backend/.env`가
그 폴백을 되돌리지 않도록 아래에서 명시적으로 무력화한다.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

_GLUE_PATH = Path(__file__).resolve().parent.parent / "tools" / "ingest_from_upload.py"


def _load_glue() -> ModuleType:
    """`tools/`는 패키지가 아니라 스크립트 디렉터리라 경로로 직접 로드한다."""
    spec = importlib.util.spec_from_file_location("ingest_from_upload", _GLUE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def glue(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _load_glue()
    # 이 스크립트는 실행 시 backend/.env를 직접 읽는다 — 실제 크레덴셜이 테스트
    # 프로세스에 들어와 네트워크를 타는 것을 막는다(conftest의 환경 비우기가
    # load_dotenv에 덮어써지지 않도록).
    monkeypatch.setattr(module, "load_dotenv", lambda *args, **kwargs: False)
    return module


@pytest.fixture
def upload_session(tmp_path: Path, dummy_video_with_scene_change: Path) -> Path:
    """mock-backend가 저장하는 것과 같은 모양의 업로드 세션 디렉터리.

    청크 mp4에 오디오 트랙이 없는 것이 실제와 동일하다(오디오는 계약상
    WebSocket PCM으로 따로 온다 — `android/.../VideoChunkEncoder.kt`).
    """
    session_dir = tmp_path / "session-abc123"
    session_dir.mkdir()
    shutil.copy(dummy_video_with_scene_change, session_dir / "chunk_000.mp4")
    # 16kHz mono s16le 1초분 무음 — STT는 스텁이라 내용은 무관하다.
    (session_dir / "audio_16k_mono_s16le.pcm").write_bytes(b"\x00\x00" * 16_000)
    (session_dir / "session_summary.json").write_text(
        json.dumps({"started_at": datetime(2026, 8, 1, 14, 30, tzinfo=UTC).isoformat()}),
        encoding="utf-8",
    )
    return session_dir


def _run(glue: ModuleType, monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["ingest_from_upload.py", *args])
    return glue.main()


def _session_md(vault: Path) -> str:
    files = list((vault / "sessions").glob("*.md"))
    assert len(files) == 1, f"세션 md가 정확히 1개여야 합니다: {files}"
    return files[0].read_text(encoding="utf-8")


def test_glue_generates_session_md_with_captions(
    glue: ModuleType, upload_session: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """핵심 회귀: 실기기 경로에서도 `## 장면 캡션`이 TODO 플레이스홀더로 남지 않는다."""
    vault = tmp_path / "vault"

    assert _run(glue, monkeypatch, str(upload_session), "--vault", str(vault)) == 0

    text = _session_md(vault)
    assert "## 전사록" in text
    assert "## 장면 캡션" in text
    # 키프레임이 실제로 추출돼 캡션 줄이 만들어졌는지 (GEMINI_API_KEY가 없으므로
    # 플레이스홀더 캡션이지만, 배선 자체가 살아 있다는 것이 이 테스트의 목적).
    assert "![[media/" in text, "키프레임 이미지 참조가 세션 md에 들어가야 한다"
    assert "TODO: VLM 키프레임 캡션" not in text, "캡션 배선이 끊기면 이 TODO 문구가 남는다"

    keyframes = list((vault / "media").rglob("*.jpg"))
    assert keyframes, "키프레임 이미지가 볼트 media/ 아래 저장돼야 한다"


def test_glue_concats_chunks_into_single_video_for_fallback(
    glue: ModuleType, upload_session: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fallback 영상 재조회가 열 수 있도록 frontmatter video 경로가 실제 파일이어야 한다."""
    vault = tmp_path / "vault"

    assert _run(glue, monkeypatch, str(upload_session), "--vault", str(vault)) == 0

    merged = upload_session / "session_video.mp4"
    assert merged.is_file()
    assert str(merged) in _session_md(vault)


def test_no_captions_flag_skips_keyframe_extraction(
    glue: ModuleType, upload_session: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gemini 호출 수를 아껴야 할 때(무료 티어) 캡션만 끌 수 있어야 한다."""
    vault = tmp_path / "vault"

    assert _run(glue, monkeypatch, str(upload_session), "--vault", str(vault), "--no-captions") == 0

    assert not list(vault.rglob("*.jpg")), "--no-captions면 키프레임을 추출하지 않아야 한다"
    assert "TODO: VLM 키프레임 캡션" in _session_md(vault)


def test_missing_pcm_fails_cleanly(glue: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty-session"
    empty.mkdir()

    assert _run(glue, monkeypatch, str(empty), "--vault", str(tmp_path / "vault")) == 1


def test_missing_session_dir_fails_cleanly(
    glue: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run(glue, monkeypatch, str(tmp_path / "nope"), "--vault", str(tmp_path / "vault")) == 1
