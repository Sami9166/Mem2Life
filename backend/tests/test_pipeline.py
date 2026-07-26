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
from ingest.vlm.base import CaptionItem
from ingest.vlm.gemini_client import GeminiAPIError


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

    # captions/summary를 명시적으로 안 넘겼고 GEMINI_API_KEY도 없으므로
    # 플레이스홀더(키프레임 이미지 링크 + TODO)로 채워져야 한다 — VLM/LLM 없이도
    # 세션 md가 끝까지 만들어진다는 CLAUDE.md 원칙의 회귀 테스트.
    content = result.session_md_path.read_text(encoding="utf-8")
    assert "TODO: LLM 요약" in content
    assert "TODO: VLM 캡션" in content


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


class _FailingCaptioner:
    provider_name = "gemini"

    def caption_keyframes(self, keyframes, transcript, *, media_slug: str) -> list[CaptionItem]:
        raise GeminiAPIError("Gemini API 호출이 실패했습니다 (HTTP 503): 서버 과부하 (재시도 소진)")


class _FailingSummarizer:
    provider_name = "gemini"

    def summarize_session(self, transcript, captions, participants) -> str | None:
        raise GeminiAPIError("Gemini API 호출이 실패했습니다 (HTTP 503): 서버 과부하 (재시도 소진)")


def test_run_ingest_pipeline_falls_back_to_placeholder_when_vlm_caption_api_fails(
    monkeypatch: pytest.MonkeyPatch,
    dummy_video: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """GEMINI_API_KEY는 있지만(실제 클라이언트가 만들어졌지만) VLM 캡션 호출
    자체가 실패(GeminiAPIError)해도, RTZR API 실패와 동일한 원칙으로 세션 md
    생성까지는 끝까지 진행해야 한다 (회귀 테스트)."""
    monkeypatch.setattr(pipeline_module, "get_vlm_captioner", lambda provider: _FailingCaptioner())

    result = run_ingest_pipeline(dummy_video, tmp_path / "vault")

    assert result.session_md_path.exists()
    content = result.session_md_path.read_text(encoding="utf-8")
    assert "TODO: VLM 캡션" in content  # 실패 시 대체된 플레이스홀더 캡션

    warning = capsys.readouterr().err
    assert "[경고]" in warning
    assert "VLM 캡션 생성이 실패" in warning


def test_run_ingest_pipeline_falls_back_to_placeholder_when_llm_summary_api_fails(
    monkeypatch: pytest.MonkeyPatch,
    dummy_video: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """LLM 요약 호출 자체가 실패(GeminiAPIError)해도 세션 md는 TODO 요약으로
    끝까지 생성돼야 한다 (회귀 테스트)."""
    monkeypatch.setattr(pipeline_module, "get_llm_summarizer", lambda provider: _FailingSummarizer())

    result = run_ingest_pipeline(dummy_video, tmp_path / "vault")

    assert result.session_md_path.exists()
    content = result.session_md_path.read_text(encoding="utf-8")
    assert "TODO: LLM 요약" in content

    warning = capsys.readouterr().err
    assert "[경고]" in warning
    assert "LLM 요약 생성이 실패" in warning


def test_run_ingest_pipeline_does_not_fall_back_on_gemini_credential_error(
    monkeypatch: pytest.MonkeyPatch, dummy_video: Path, tmp_path: Path
) -> None:
    """`GeminiCredentialError`는 RTZRCredentialError와 동일한 원칙으로 설정
    문제이지 일시적 장애가 아니므로 조용히 플레이스홀더로 대체하지 않고 그대로
    전파해야 한다."""
    from ingest.vlm.gemini_client import GeminiCredentialError

    class _CredentialFailingCaptioner:
        provider_name = "gemini"

        def caption_keyframes(self, keyframes, transcript, *, media_slug: str) -> list[CaptionItem]:
            raise GeminiCredentialError("Gemini API 인증/요청 오류입니다 (HTTP 401): API key not valid")

    monkeypatch.setattr(pipeline_module, "get_vlm_captioner", lambda provider: _CredentialFailingCaptioner())

    with pytest.raises(GeminiCredentialError):
        run_ingest_pipeline(dummy_video, tmp_path / "vault")


def test_run_ingest_pipeline_explicit_captions_and_summary_skip_vlm_llm_calls(
    monkeypatch: pytest.MonkeyPatch, dummy_video: Path, tmp_path: Path
) -> None:
    """호출자가 captions/summary를 명시적으로 넘기면 그대로 써야 하고, VLM/LLM
    provider를 아예 호출하면 안 된다 (기존 인터페이스 하위호환 요구사항)."""

    def _fail_if_called(provider: str) -> None:  # pragma: no cover
        raise AssertionError(f"명시적으로 넘긴 값이 있으면 {provider} provider를 호출하면 안 됨")

    monkeypatch.setattr(pipeline_module, "get_vlm_captioner", _fail_if_called)
    monkeypatch.setattr(pipeline_module, "get_llm_summarizer", _fail_if_called)

    result = run_ingest_pipeline(
        dummy_video,
        tmp_path / "vault",
        summary="명시적으로 넘긴 요약문.",
        captions=[(1.0, 2.0, "명시적으로 넘긴 캡션.")],
    )

    content = result.session_md_path.read_text(encoding="utf-8")
    assert "명시적으로 넘긴 요약문." in content
    assert "명시적으로 넘긴 캡션." in content


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
