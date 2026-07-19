from __future__ import annotations

from pathlib import Path

import pytest

from ingest.audio import extract_audio
from ingest.stt import ClovaStubClient, RTZRStubClient, Transcript, get_stt_client
from ingest.stt.base import format_timestamp
from ingest.stt.rtzr_client import RTZRCredentialError


@pytest.fixture()
def dummy_audio(dummy_video: Path, tmp_path: Path) -> Path:
    result = extract_audio(dummy_video, tmp_path / "audio.wav")
    return result.path


def test_format_timestamp() -> None:
    assert format_timestamp(0) == "00:00:00"
    assert format_timestamp(65) == "00:01:05"
    assert format_timestamp(3725) == "01:02:05"
    with pytest.raises(ValueError):
        format_timestamp(-1)


@pytest.mark.parametrize("client_cls", [RTZRStubClient, ClovaStubClient])
def test_stub_transcribe_returns_speaker_labeled_transcript(
    client_cls: type[RTZRStubClient] | type[ClovaStubClient], dummy_audio: Path
) -> None:
    client = client_cls()
    transcript = client.transcribe(dummy_audio)

    assert isinstance(transcript, Transcript)
    assert transcript.segments, "전사록이 비어 있으면 안 됨"
    assert set(transcript.speakers) <= {"화자1", "화자2"}

    starts = [seg.start_sec for seg in transcript.segments]
    assert starts == sorted(starts), "타임스탬프는 시간순으로 증가해야 함"
    for seg in transcript.segments:
        assert seg.end_sec > seg.start_sec
        assert seg.text
        assert seg.timestamp_label.startswith("[")


def test_stub_transcribe_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        RTZRStubClient().transcribe(Path("/no/such/audio.wav"))


def test_get_stt_client_factory() -> None:
    assert isinstance(get_stt_client("rtzr"), RTZRStubClient)
    assert isinstance(get_stt_client("clova"), ClovaStubClient)
    assert isinstance(get_stt_client("RTZR"), RTZRStubClient)  # 대소문자 무관

    with pytest.raises(ValueError):
        get_stt_client("unknown-provider")


def test_stub_clients_require_no_api_key_env(monkeypatch: pytest.MonkeyPatch, dummy_audio: Path) -> None:
    # 관련 환경변수가 전혀 없어도 스텁은 정상 동작해야 한다 (네트워크 호출 없음).
    monkeypatch.delenv("RTZR_API_KEY", raising=False)
    monkeypatch.delenv("CLOVA_SPEECH_API_KEY", raising=False)
    transcript = RTZRStubClient().transcribe(dummy_audio)
    assert transcript.segments


def test_get_stt_client_falls_back_to_stub_without_rtzr_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # conftest.py의 autouse 픽스처가 이미 지워두지만, 이 테스트의 의도를
    # 명시적으로 드러내기 위해 다시 한 번 확실히 지운다.
    monkeypatch.delenv("RTZR_CLIENT_ID", raising=False)
    monkeypatch.delenv("RTZR_CLIENT_SECRET", raising=False)

    client = get_stt_client("rtzr")

    assert isinstance(client, RTZRStubClient)


def test_get_stt_client_selects_real_rtzr_client_when_credentials_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """인증 정보가 있으면 팩토리가 스텁이 아닌 실제 RTZRClient 생성을 시도해야
    한다. 실제 네트워크를 타지 않도록 `RTZRClient` 자체를 가짜로 교체해
    "어떤 분기를 탔는지"만 검증한다 (실제 요청/응답 로직은
    test_stt_rtzr_client.py에서 별도로 검증)."""
    import ingest.stt.factory as factory_module

    class _FakeRealClient:
        provider_name = "rtzr"

        def transcribe(self, audio_path: Path) -> Transcript:  # pragma: no cover
            raise AssertionError("이 테스트에서는 transcribe가 호출되지 않아야 함")

    monkeypatch.setenv("RTZR_CLIENT_ID", "fake-id")
    monkeypatch.setenv("RTZR_CLIENT_SECRET", "fake-secret")
    monkeypatch.setattr(factory_module, "RTZRClient", _FakeRealClient)

    client = get_stt_client("rtzr")

    assert isinstance(client, _FakeRealClient)


def test_get_stt_client_falls_back_to_stub_if_real_client_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """인증 정보 환경변수는 있지만 실제 클라이언트 생성이 실패하는 방어적
    엣지 케이스에서도 파이프라인이 죽지 않고 스텁으로 안전하게 대체돼야 한다."""
    import ingest.stt.factory as factory_module

    def _raise_credential_error() -> None:
        raise RTZRCredentialError("가짜 인증 오류: 자격 증명이 유효하지 않습니다")

    class _FailingClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            _raise_credential_error()

    monkeypatch.setenv("RTZR_CLIENT_ID", "fake-id")
    monkeypatch.setenv("RTZR_CLIENT_SECRET", "fake-secret")
    monkeypatch.setattr(factory_module, "RTZRClient", _FailingClient)

    client = get_stt_client("rtzr")

    assert isinstance(client, RTZRStubClient)
    assert "스텁으로 대체" in capsys.readouterr().err
