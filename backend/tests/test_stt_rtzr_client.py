"""RTZR VITO API 실제 클라이언트(`ingest.stt.rtzr_client.RTZRClient`) 테스트.

`httpx.MockTransport`로 HTTP 계층을 완전히 대체하므로, 이 파일의 테스트는
절대 실제 네트워크를 타지 않는다(요청 구성/응답 파싱/에러 처리 로직만 검증).
실제 RTZR API를 상대로 하는 수동 스모크 테스트는 `test_stt_rtzr_live.py`에
`RTZR_LIVE_TEST=1`로 옵트인해야만 실행되도록 분리돼 있다.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from ingest.stt.base import Transcript
from ingest.stt.rtzr_client import (
    AUTH_URL,
    TRANSCRIBE_URL,
    RTZRAPIError,
    RTZRClient,
    RTZRCredentialError,
)


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    **kwargs: object,
) -> RTZRClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    return RTZRClient(
        client_id="dummy-id",
        client_secret="dummy-secret",
        http_client=http_client,
        sleep_fn=lambda _seconds: None,  # 테스트는 실제로 기다리지 않는다
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.fixture()
def wav_file(tmp_path: Path) -> Path:
    path = tmp_path / "audio.wav"
    path.write_bytes(b"RIFF....WAVEfmt ")  # 내용은 검증 대상이 아님(전송만 확인)
    return path


def _auth_response(token: str = "test-access-token") -> httpx.Response:
    return httpx.Response(200, json={"access_token": token, "expire_at": 9999999999})


def test_transcribe_success_parses_utterances_with_speaker_labels(wav_file: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if str(request.url) == AUTH_URL:
            return _auth_response()
        if str(request.url) == TRANSCRIBE_URL:
            return httpx.Response(200, json={"id": "job-123"})
        if str(request.url).startswith("https://openapi.vito.ai/v1/transcribe/job-123"):
            return httpx.Response(
                200,
                json={
                    "id": "job-123",
                    "status": "completed",
                    "results": {
                        "utterances": [
                            {
                                "start_at": 0,
                                "duration": 2500,
                                "msg": "안녕하세요, 오늘 어떠셨나요?",
                                "spk": 0,
                                "lang": "ko",
                            },
                            {
                                "start_at": 2500,
                                "duration": 1800,
                                "msg": "네, 좋았습니다.",
                                "spk": 1,
                                "lang": "ko",
                            },
                        ]
                    },
                },
            )
        raise AssertionError(f"예상치 못한 요청: {request.url}")

    client = _make_client(handler)
    transcript = client.transcribe(wav_file)

    assert isinstance(transcript, Transcript)
    assert transcript.provider == "rtzr"  # 스텁("rtzr-stub")과 구분되는 라벨
    assert [seg.speaker for seg in transcript.segments] == ["화자1", "화자2"]
    assert transcript.segments[0].start_sec == 0.0
    assert transcript.segments[0].end_sec == 2.5
    assert transcript.segments[0].text == "안녕하세요, 오늘 어떠셨나요?"
    assert transcript.segments[1].start_sec == 2.5
    assert transcript.segments[1].end_sec == pytest.approx(4.3)

    # 인증 → 제출 → 폴링 순서로 3번의 요청이 나갔는지 확인
    assert [str(r.url) for r in calls] == [
        AUTH_URL,
        TRANSCRIBE_URL,
        "https://openapi.vito.ai/v1/transcribe/job-123",
    ]
    # 제출 요청의 config JSON이 명세대로 구성됐는지 확인
    submit_request = calls[1]
    body_text = submit_request.content.decode("utf-8", errors="ignore")
    assert '"model_name": "sommers"' in body_text
    assert '"language": "ko"' in body_text
    assert '"use_diarization": true' in body_text
    assert '"spk_count": 0' in body_text
    assert '"domain": "GENERAL"' in body_text
    # 인증 단계에서 받은 토큰이 이후 요청 헤더에 실제로 쓰였는지 확인
    assert submit_request.headers["Authorization"] == "Bearer test-access-token"


def test_polling_retries_until_completed(wav_file: Path) -> None:
    poll_statuses = iter(["transcribing", "transcribing", "completed"])
    poll_call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_call_count
        if str(request.url) == AUTH_URL:
            return _auth_response()
        if str(request.url) == TRANSCRIBE_URL:
            return httpx.Response(200, json={"id": "job-456"})
        poll_call_count += 1
        status = next(poll_statuses)
        payload = {"id": "job-456", "status": status}
        if status == "completed":
            payload["results"] = {"utterances": []}
        return httpx.Response(200, json=payload)

    client = _make_client(handler, poll_interval_sec=0.0)
    transcript = client.transcribe(wav_file)

    assert poll_call_count == 3
    assert transcript.segments == []


def test_missing_credentials_raises_credential_error() -> None:
    with pytest.raises(RTZRCredentialError, match="인증 정보"):
        RTZRClient(env={})


def test_auth_401_raises_credential_error(wav_file: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client = _make_client(handler)
    with pytest.raises(RTZRCredentialError, match="401"):
        client.transcribe(wav_file)


def test_transcribe_job_failed_surfaces_error_code_and_message(wav_file: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == AUTH_URL:
            return _auth_response()
        if str(request.url) == TRANSCRIBE_URL:
            return httpx.Response(200, json={"id": "job-789"})
        return httpx.Response(
            200,
            json={
                "id": "job-789",
                "status": "failed",
                "error": {"code": "invalid_audio", "message": "오디오 형식이 잘못됐습니다"},
            },
        )

    client = _make_client(handler)
    with pytest.raises(RTZRAPIError, match="invalid_audio"):
        client.transcribe(wav_file)


def test_poll_network_error_raises_api_error(wav_file: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == AUTH_URL:
            return _auth_response()
        if str(request.url) == TRANSCRIBE_URL:
            return httpx.Response(200, json={"id": "job-000"})
        raise httpx.ReadTimeout("polling timed out", request=request)

    client = _make_client(handler)
    with pytest.raises(RTZRAPIError, match="네트워크"):
        client.transcribe(wav_file)


def test_poll_timeout_exceeded_raises_api_error(wav_file: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == AUTH_URL:
            return _auth_response()
        if str(request.url) == TRANSCRIBE_URL:
            return httpx.Response(200, json={"id": "job-forever"})
        return httpx.Response(200, json={"id": "job-forever", "status": "transcribing"})

    fake_clock = iter([0.0, 100.0, 200.0])  # 첫 호출이 deadline 계산, 이후 즉시 초과

    def time_fn() -> float:
        return next(fake_clock, 200.0)

    client = _make_client(handler, poll_timeout_sec=1.0, time_fn=time_fn)
    with pytest.raises(RTZRAPIError, match="시간이 초과"):
        client.transcribe(wav_file)


def test_missing_audio_file_raises_before_any_network_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("오디오 파일이 없으면 HTTP 요청 자체가 나가면 안 됨")

    client = _make_client(handler)
    with pytest.raises(FileNotFoundError):
        client.transcribe(Path("/no/such/audio.wav"))


def test_submit_retries_on_429_then_succeeds(wav_file: Path) -> None:
    """제출 요청이 429를 받아도 즉시 실패하지 않고 재시도해 성공해야 한다."""
    submit_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal submit_attempts
        if str(request.url) == AUTH_URL:
            return _auth_response()
        if str(request.url) == TRANSCRIBE_URL:
            submit_attempts += 1
            if submit_attempts == 1:
                return httpx.Response(429, text="rate limited")
            return httpx.Response(200, json={"id": "job-429"})
        return httpx.Response(
            200,
            json={"id": "job-429", "status": "completed", "results": {"utterances": []}},
        )

    client = _make_client(handler)
    transcript = client.transcribe(wav_file)

    assert submit_attempts == 2
    assert transcript.segments == []


def test_poll_retries_on_5xx_then_succeeds(wav_file: Path) -> None:
    """폴링 요청이 5xx를 받아도 즉시 실패하지 않고 재시도해 성공해야 한다."""
    poll_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_attempts
        if str(request.url) == AUTH_URL:
            return _auth_response()
        if str(request.url) == TRANSCRIBE_URL:
            return httpx.Response(200, json={"id": "job-5xx"})
        poll_attempts += 1
        if poll_attempts == 1:
            return httpx.Response(503, text="service unavailable")
        return httpx.Response(
            200,
            json={"id": "job-5xx", "status": "completed", "results": {"utterances": []}},
        )

    client = _make_client(handler, poll_interval_sec=0.0)
    transcript = client.transcribe(wav_file)

    assert poll_attempts == 2
    assert transcript.segments == []


def test_submit_exhausts_retries_then_raises(wav_file: Path) -> None:
    """429가 계속되면 무한 재시도하지 않고 정해진 횟수 뒤 RTZRAPIError를 낸다."""
    submit_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal submit_attempts
        if str(request.url) == AUTH_URL:
            return _auth_response()
        submit_attempts += 1
        return httpx.Response(429, text="rate limited forever")

    client = _make_client(handler)
    with pytest.raises(RTZRAPIError, match="429"):
        client.transcribe(wav_file)

    assert submit_attempts == 3  # 최대 시도 횟수만큼만 재시도하고 포기해야 함


def test_poll_prints_progress_message_while_waiting(
    wav_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    poll_statuses = iter(["transcribing", "completed"])

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == AUTH_URL:
            return _auth_response()
        if str(request.url) == TRANSCRIBE_URL:
            return httpx.Response(200, json={"id": "job-progress"})
        status = next(poll_statuses)
        payload = {"id": "job-progress", "status": status}
        if status == "completed":
            payload["results"] = {"utterances": []}
        return httpx.Response(200, json=payload)

    client = _make_client(handler, poll_interval_sec=0.0)
    client.transcribe(wav_file)

    assert "[진행]" in capsys.readouterr().err


def test_upload_uses_fixed_ascii_filename_for_korean_audio_path(tmp_path: Path) -> None:
    """오디오 파일명이 한글이어도 업로드 시 고정 ASCII 파일명을 써야 한다."""
    korean_named_wav = tmp_path / "회의_녹음_2026-07-19.wav"
    korean_named_wav.write_bytes(b"RIFF....WAVEfmt ")

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == AUTH_URL:
            return _auth_response()
        if str(request.url) == TRANSCRIBE_URL:
            body_text = request.content.decode("utf-8", errors="ignore")
            assert 'filename="audio.wav"' in body_text
            assert "회의_녹음" not in body_text
            return httpx.Response(200, json={"id": "job-korean"})
        return httpx.Response(
            200,
            json={"id": "job-korean", "status": "completed", "results": {"utterances": []}},
        )

    client = _make_client(handler)
    client.transcribe(korean_named_wav)


def test_upload_uses_longer_timeout_than_poll(wav_file: Path) -> None:
    """멀티파트 업로드는 인증/폴링보다 더 긴 전용 타임아웃을 써야 한다."""
    seen_timeouts: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == AUTH_URL:
            return _auth_response()
        if str(request.url) == TRANSCRIBE_URL:
            seen_timeouts["submit"] = request.extensions.get("timeout")
            return httpx.Response(200, json={"id": "job-timeout"})
        seen_timeouts["poll"] = request.extensions.get("timeout")
        return httpx.Response(
            200,
            json={"id": "job-timeout", "status": "completed", "results": {"utterances": []}},
        )

    client = _make_client(handler, upload_timeout_sec=120.0)
    client.transcribe(wav_file)

    submit_timeout = seen_timeouts["submit"]
    poll_timeout = seen_timeouts["poll"]
    assert submit_timeout is not None
    assert submit_timeout["read"] == 120.0
    # 폴링은 클라이언트 기본 타임아웃을 그대로 쓰므로 업로드보다 짧아야 한다
    # (기본 httpx.Client에는 명시적 timeout override가 안 걸리므로 None이거나
    # 업로드보다 작은 값이다).
    if poll_timeout is not None:
        assert poll_timeout["read"] != 120.0


def test_credential_values_never_appear_in_error_messages(wav_file: Path) -> None:
    """에러 메시지에 실제 client_id/secret 문자열이 노출되면 안 된다."""
    secret_marker = "super-secret-value-should-not-leak"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    transport = httpx.MockTransport(handler)
    client = RTZRClient(
        client_id="id-should-not-leak",
        client_secret=secret_marker,
        http_client=httpx.Client(transport=transport),
        sleep_fn=lambda _s: None,
    )
    with pytest.raises(RTZRCredentialError) as exc_info:
        client.transcribe(wav_file)
    assert secret_marker not in str(exc_info.value)


def _completed_handler(request: httpx.Request) -> httpx.Response:
    """인증→제출→완료를 즉시 돌려주는 최소 핸들러(spk_count config 검증용)."""
    if str(request.url) == AUTH_URL:
        return _auth_response()
    if str(request.url) == TRANSCRIBE_URL:
        return httpx.Response(200, json={"id": "job-1"})
    if str(request.url).startswith("https://openapi.vito.ai/v1/transcribe/job-1"):
        return httpx.Response(
            200,
            json={
                "id": "job-1",
                "status": "completed",
                "results": {"utterances": [{"start_at": 0, "duration": 1000, "msg": "네", "spk": 0}]},
            },
        )
    raise AssertionError(f"예상치 못한 요청: {request.url}")


def test_spk_count_param_flows_to_diarization_config(wav_file: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _completed_handler(request)

    _make_client(handler, spk_count=2).transcribe(wav_file)
    body = calls[1].content.decode("utf-8", errors="ignore")
    assert '"spk_count": 2' in body


def test_spk_count_from_env(wav_file: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _completed_handler(request)

    _make_client(handler, env={"RTZR_SPK_COUNT": "3"}).transcribe(wav_file)
    body = calls[1].content.decode("utf-8", errors="ignore")
    assert '"spk_count": 3' in body


def test_spk_count_param_overrides_env(wav_file: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _completed_handler(request)

    _make_client(handler, spk_count=1, env={"RTZR_SPK_COUNT": "9"}).transcribe(wav_file)
    body = calls[1].content.decode("utf-8", errors="ignore")
    assert '"spk_count": 1' in body


def test_spk_count_defaults_to_zero_when_unset(wav_file: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _completed_handler(request)

    _make_client(handler, env={}).transcribe(wav_file)
    body = calls[1].content.decode("utf-8", errors="ignore")
    assert '"spk_count": 0' in body
