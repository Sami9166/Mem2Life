from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
import yaml

import ingest.pipeline as pipeline_module
from ingest.pipeline import IngestResult, run_ingest_pipeline
from ingest.stt.base import Transcript
from ingest.stt.rtzr_client import RTZRAPIError


def _parse_frontmatter(md_path: Path) -> dict:
    content = md_path.read_text(encoding="utf-8")
    _, frontmatter_block, _ = content.split("---", 2)
    return yaml.safe_load(frontmatter_block)


def test_run_ingest_pipeline_end_to_end(dummy_video: Path, tmp_path: Path) -> None:
    """영상 파일 하나 -> 오디오 추출 -> STT 스텁 -> 세션 md 생성이 API 키 없이 끝까지 성공해야 한다."""
    vault_dir = tmp_path / "vault"

    result = run_ingest_pipeline(
        dummy_video,
        vault_dir,
        title="테스트 세션",
        session_start=datetime(2026, 7, 16, 15, 0),
        stt_provider="rtzr",
    )

    assert isinstance(result, IngestResult)
    assert result.audio_path.exists()
    assert result.session_md_path.exists()
    assert result.stt_provider == "rtzr-stub"
    assert result.transcript.segments

    content = result.session_md_path.read_text(encoding="utf-8")
    assert "date: 2026-07-16" in content
    assert "## 전사록" in content
    assert "화자1" in content or "화자2" in content

    assert result.session_md_path.parent.name == "sessions"
    assert result.session_md_path.name == "2026-07-16_1500_테스트_세션.md"


def test_run_ingest_pipeline_clova_provider(dummy_video: Path, tmp_path: Path) -> None:
    result = run_ingest_pipeline(dummy_video, tmp_path / "vault", stt_provider="clova")
    assert result.stt_provider == "clova-stub"


def test_run_ingest_pipeline_no_keep_audio(dummy_video: Path, tmp_path: Path) -> None:
    result = run_ingest_pipeline(
        dummy_video,
        tmp_path / "vault",
        audio_dir=tmp_path / "audio",
        keep_audio=False,
    )

    assert not result.audio_path.exists()
    assert result.session_md_path.exists()


def test_run_ingest_pipeline_requires_no_api_key_env(
    monkeypatch: pytest.MonkeyPatch, dummy_video: Path, tmp_path: Path
) -> None:
    for key in ("RTZR_API_KEY", "CLOVA_SPEECH_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    result = run_ingest_pipeline(dummy_video, tmp_path / "vault")
    assert result.session_md_path.exists()


def test_run_ingest_pipeline_missing_video_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_ingest_pipeline(tmp_path / "no_such.mp4", tmp_path / "vault")


def test_run_ingest_pipeline_falls_back_to_stub_when_rtzr_api_fails(
    monkeypatch: pytest.MonkeyPatch,
    dummy_video: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """RTZR 인증 정보는 있지만 API 호출 자체가 실패(RTZRAPIError)해도, 데모
    도중 전체 실행이 중단돼 세션 md가 아예 생성되지 않는 것보다는 스텁
    품질 전사록으로라도 끝까지 완주해야 한다 (블로커 회귀 테스트)."""

    class _FailingRealClient:
        provider_name = "rtzr"

        def transcribe(self, audio_path: Path) -> Transcript:
            raise RTZRAPIError("RTZR 서버가 계속 5xx를 반환했습니다 (재시도 소진)")

    monkeypatch.setattr(pipeline_module, "get_stt_client", lambda provider: _FailingRealClient())

    result = run_ingest_pipeline(dummy_video, tmp_path / "vault", stt_provider="rtzr")

    assert result.session_md_path.exists()
    assert result.transcript.segments
    assert result.stt_provider == "rtzr-stub"  # 실패 시 대체된 스텁 전사록

    warning = capsys.readouterr().err
    assert "[경고]" in warning
    assert "RTZR API 호출이 실패" in warning


def test_run_ingest_pipeline_does_not_fall_back_on_credential_error(
    monkeypatch: pytest.MonkeyPatch, dummy_video: Path, tmp_path: Path
) -> None:
    """RTZRCredentialError는 이미 `stt.factory`가 생성 시점에 스텁으로 대체하는
    경로다 — 혹시라도 `transcribe()` 호출 중 발생하더라도(예: 토큰 만료로
    인한 뒤늦은 401) pipeline이 조용히 스텁으로 대체하면 안 되고 그대로
    전파해 설정 문제로 인지되게 해야 한다."""
    from ingest.stt.rtzr_client import RTZRCredentialError

    class _CredentialFailingClient:
        provider_name = "rtzr"

        def transcribe(self, audio_path: Path) -> Transcript:
            raise RTZRCredentialError("인증 정보가 유효하지 않습니다")

    monkeypatch.setattr(pipeline_module, "get_stt_client", lambda provider: _CredentialFailingClient())

    with pytest.raises(RTZRCredentialError):
        run_ingest_pipeline(dummy_video, tmp_path / "vault", stt_provider="rtzr")


def test_run_ingest_pipeline_participants_override(dummy_video: Path, tmp_path: Path) -> None:
    result = run_ingest_pipeline(
        dummy_video,
        tmp_path / "vault",
        participants=["민수", "현우"],
    )
    content = result.session_md_path.read_text(encoding="utf-8")
    assert 'participants: ["[[민수]]", "[[현우]]"]' in content


def test_run_ingest_pipeline_writes_absolute_video_path(
    dummy_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """세션 md의 `video:` 필드는 CWD/입력 경로 형태와 무관하게 항상 절대경로여야 한다.

    recall-dev의 fallback(영상 클립 재조회)가 다른 작업 디렉토리에서 이
    경로로 파일을 다시 열어야 하는 wiki-builder<->recall-dev 계약이기 때문.
    """
    # 다른 작업 디렉토리에서 상대경로로 영상을 넘겨도 절대경로로 기록돼야 한다.
    monkeypatch.chdir(dummy_video.parent)
    relative_video = Path(os.path.relpath(dummy_video, dummy_video.parent))
    assert not relative_video.is_absolute()

    result = run_ingest_pipeline(relative_video, tmp_path / "vault")

    assert result.video_path.is_absolute()
    assert result.video_path == dummy_video.resolve()

    frontmatter = _parse_frontmatter(result.session_md_path)
    assert Path(frontmatter["video"]).is_absolute()
    assert Path(frontmatter["video"]) == dummy_video.resolve()
