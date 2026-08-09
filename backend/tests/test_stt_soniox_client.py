"""SonioxSttClient 단위 테스트 — 네트워크 없이 결정적.

토큰→발화 병합(위험 지점)은 순수 함수 `parse_tokens_to_transcript`로 직접
검증하고, `transcribe()`는 4개 REST 단계를 흉내내는 httpx.MockTransport로
검증한다. factory 폴백은 키 없이 스텁으로 떨어지는지 확인한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from ingest.stt import RTZRStubClient, get_stt_client
from ingest.stt.base import Transcript
from ingest.stt.soniox_client import (
    SonioxCredentialError,
    SonioxSttClient,
    parse_tokens_to_transcript,
)

# 단어 단위 토큰: 화자1 두 단어 → 화자2 한 단어 → 화자1 한 단어
_TOKENS = [
    {"text": "안녕", "start_ms": 0, "end_ms": 400, "speaker": 1},
    {"text": "하세요", "start_ms": 400, "end_ms": 900, "speaker": 1},
    {"text": "네 반갑습니다", "start_ms": 1000, "end_ms": 1800, "speaker": 2},
    {"text": "그럼 시작할게요", "start_ms": 2000, "end_ms": 2900, "speaker": 1},
]


# -- 순수 파서 --------------------------------------------------------------


def test_groups_consecutive_tokens_by_speaker() -> None:
    t = parse_tokens_to_transcript(_TOKENS)
    assert isinstance(t, Transcript)
    assert t.provider == "soniox"
    assert [s.speaker for s in t.segments] == ["화자1", "화자2", "화자1"]
    assert t.segments[0].text == "안녕하세요"  # 같은 화자 토큰이 한 발화로 병합
    assert t.speakers == ["화자1", "화자2"]


def test_segment_timestamps_ms_to_sec() -> None:
    t = parse_tokens_to_transcript(_TOKENS)
    assert t.segments[0].start_sec == pytest.approx(0.0)
    assert t.segments[0].end_sec == pytest.approx(0.9)  # 마지막 토큰 end_ms
    assert t.segments[2].start_sec == pytest.approx(2.0)


def test_empty_and_missing_speaker_handled() -> None:
    tokens = [
        {"text": "혼자말", "start_ms": 0, "end_ms": 500},  # speaker 없음 → 화자1
        {"text": "", "start_ms": 500, "end_ms": 600, "speaker": 1},  # 빈 text 스킵
    ]
    t = parse_tokens_to_transcript(tokens)
    assert len(t.segments) == 1
    assert t.segments[0].speaker == "화자1"
    assert t.segments[0].text == "혼자말"


def test_empty_tokens_gives_empty_transcript() -> None:
    t = parse_tokens_to_transcript([])
    assert t.segments == []
    assert t.speakers == []


# -- transcribe() (httpx.MockTransport로 4단계 흉내) --------------------------


def _mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert request.headers.get("Authorization") == "Bearer test-key"
        if request.method == "POST" and url.endswith("/v1/files"):
            return httpx.Response(200, json={"id": "file-123"})
        if request.method == "POST" and url.endswith("/v1/transcriptions"):
            body = json.loads(request.content)
            assert body["file_id"] == "file-123"
            assert body["model"] == "stt-async-v5"
            assert body["enable_speaker_diarization"] is True
            return httpx.Response(200, json={"id": "tr-456"})
        if request.method == "GET" and url.endswith("/v1/transcriptions/tr-456"):
            return httpx.Response(200, json={"status": "completed"})
        if request.method == "GET" and url.endswith("/v1/transcriptions/tr-456/transcript"):
            return httpx.Response(200, json={"text": "안녕하세요", "tokens": _TOKENS})
        return httpx.Response(404, json={"error": f"unexpected {request.method} {url}"})

    return httpx.MockTransport(handler)


def test_transcribe_end_to_end_with_mock(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF....dummy")
    client = SonioxSttClient(
        api_key="test-key",
        http_client=httpx.Client(transport=_mock_transport()),
        sleep_fn=lambda _s: None,
    )
    t = client.transcribe(audio)
    assert t.provider == "soniox"
    assert [s.speaker for s in t.segments] == ["화자1", "화자2", "화자1"]
    assert t.segments[0].text == "안녕하세요"


def test_transcribe_missing_file_raises() -> None:
    client = SonioxSttClient(api_key="test-key", http_client=httpx.Client(transport=_mock_transport()))
    with pytest.raises(FileNotFoundError):
        client.transcribe(Path("/nonexistent/audio.wav"))


def test_error_status_raises(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/v1/files"):
            return httpx.Response(200, json={"id": "f"})
        if url.endswith("/v1/transcriptions"):
            return httpx.Response(200, json={"id": "t"})
        if url.endswith("/v1/transcriptions/t"):
            return httpx.Response(200, json={"status": "error", "error_message": "boom"})
        return httpx.Response(404)

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    client = SonioxSttClient(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep_fn=lambda _s: None,
    )
    from ingest.stt.soniox_client import SonioxAPIError

    with pytest.raises(SonioxAPIError):
        client.transcribe(audio)


def test_model_and_language_hints_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SONIOX_API_KEY", "k")
    monkeypatch.setenv("SONIOX_MODEL", "stt-async-vX")
    monkeypatch.setenv("SONIOX_LANGUAGE_HINTS", "ko, ja")
    client = SonioxSttClient(http_client=httpx.Client(transport=_mock_transport()))
    assert client._model == "stt-async-vX"
    assert client._language_hints == ["ko", "ja"]


# -- factory / credential --------------------------------------------------


def test_constructor_without_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SONIOX_API_KEY", raising=False)
    with pytest.raises(SonioxCredentialError):
        SonioxSttClient()


def test_factory_soniox_without_key_falls_back_to_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SONIOX_API_KEY", raising=False)
    assert isinstance(get_stt_client("soniox"), RTZRStubClient)
