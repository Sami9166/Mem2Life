"""GeminiSttClient(오디오 전사) 단위 테스트 — 네트워크 없이 결정적.

파싱 로직(위험 지점)은 순수 함수 `parse_transcript_json`으로 직접 검증하고,
`transcribe()`는 File API/모델 호출을 흉내내는 최소 가짜 클라이언트로 검증한다.
factory 폴백은 키 없이 스텁으로 떨어지는지 확인한다.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ingest.stt import RTZRStubClient, get_stt_client
from ingest.stt.base import Transcript
from ingest.stt.gemini_client import (
    GeminiSttClient,
    GeminiSttCredentialError,
    _parse_timestamp,
    parse_transcript_json,
)

_SAMPLE = (
    '{"transcript": ['
    '{"start": "00:00", "speaker": "화자1", "text": "요즘 어떻게 지내?"},'
    '{"start": "00:06", "speaker": "화자2", "text": "프로젝트 준비하느라 바빠."},'
    '{"start": "00:11", "speaker": "화자2", "text": "금요일까지 초안 보내줘."}'
    "]}"
)


# -- 순수 파서 --------------------------------------------------------------


def test_parse_basic_segments_and_speakers() -> None:
    t = parse_transcript_json(_SAMPLE)
    assert isinstance(t, Transcript)
    assert t.provider == "gemini"
    assert [s.speaker for s in t.segments] == ["화자1", "화자2", "화자2"]
    assert t.speakers == ["화자1", "화자2"]
    assert t.segments[0].text == "요즘 어떻게 지내?"


def test_end_sec_is_next_start_and_last_gets_tail() -> None:
    t = parse_transcript_json(_SAMPLE)
    assert t.segments[0].start_sec == 0.0
    assert t.segments[0].end_sec == 6.0  # 다음 발화 시작
    assert t.segments[1].end_sec == 11.0
    # 마지막 발화는 시작 + tail(3s) 근사
    assert t.segments[2].end_sec == pytest.approx(14.0)


def test_segments_sorted_by_start() -> None:
    scrambled = (
        '{"transcript": ['
        '{"start":"00:10","speaker":"화자1","text":"뒤"},'
        '{"start":"00:02","speaker":"화자1","text":"앞"}]}'
    )
    t = parse_transcript_json(scrambled)
    assert [s.text for s in t.segments] == ["앞", "뒤"]


def test_missing_speaker_defaults_and_empty_text_skipped() -> None:
    raw = (
        '{"transcript": ['
        '{"start":"00:01","text":"라벨없음"},'
        '{"start":"00:02","speaker":"화자1","text":"   "}]}'
    )
    t = parse_transcript_json(raw)
    assert len(t.segments) == 1  # 빈 text는 버림
    assert t.segments[0].speaker == "화자1"  # speaker 누락 → 기본값


def test_markdown_fenced_json_is_parsed() -> None:
    fenced = "```json\n" + _SAMPLE + "\n```"
    t = parse_transcript_json(fenced)
    assert len(t.segments) == 3


def test_bare_array_without_wrapper_is_parsed() -> None:
    # flash-lite 등은 {"transcript":[...]} 대신 [...] 배열로만 반환하기도 한다.
    bare = (
        "[\n"
        '  {"start":"00:01","speaker":"화자1","text":"나 보면서 이야기를 해 줄래?"},\n'
        '  {"start":"00:10","speaker":"화자1","text":"잘 지내지. [불분명] 프로젝트."}\n'
        "]"
    )
    t = parse_transcript_json(bare)
    assert len(t.segments) == 2
    assert t.segments[0].text == "나 보면서 이야기를 해 줄래?"
    assert "[불분명]" in t.segments[1].text  # 불확실 표기가 텍스트로 보존됨


def test_no_json_object_raises() -> None:
    with pytest.raises(ValueError):
        parse_transcript_json("전사에 실패했습니다. JSON 없음.")


def test_missing_transcript_array_raises() -> None:
    with pytest.raises(ValueError):
        parse_transcript_json('{"speaker_count": 2}')


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("00:06", 6.0),
        ("01:30", 90.0),
        ("01:02:03", 3723.0),
        (12, 12.0),
        (7.5, 7.5),
        ("", 0.0),
        ("garbage", 0.0),
        (None, 0.0),
    ],
)
def test_parse_timestamp(value: object, expected: float) -> None:
    assert _parse_timestamp(value) == pytest.approx(expected)


# -- transcribe() (가짜 클라이언트 주입) --------------------------------------


class _FakeFiles:
    def upload(self, file: str) -> SimpleNamespace:  # noqa: A002 - SDK 시그니처 모방
        return SimpleNamespace(name="files/abc", state="ACTIVE")

    def get(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, state="ACTIVE")


class _FakeModels:
    def __init__(self, text: str) -> None:
        self._text = text

    def generate_content(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(text=self._text)


class _FakeClient:
    def __init__(self, text: str) -> None:
        self.files = _FakeFiles()
        self.models = _FakeModels(text)


def test_transcribe_with_injected_client(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF....dummy")
    client = GeminiSttClient(client=_FakeClient(_SAMPLE))
    t = client.transcribe(audio)
    assert t.provider == "gemini"
    assert t.speakers == ["화자1", "화자2"]
    assert len(t.segments) == 3


def test_transcribe_missing_file_raises() -> None:
    client = GeminiSttClient(client=_FakeClient(_SAMPLE))
    with pytest.raises(FileNotFoundError):
        client.transcribe(Path("/nonexistent/audio.wav"))


def test_spk_count_hint_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RTZR_SPK_COUNT", "3")
    client = GeminiSttClient(client=_FakeClient(_SAMPLE))
    assert client._spk_count == 3
    # 명시 인자가 env보다 우선
    client2 = GeminiSttClient(client=_FakeClient(_SAMPLE), spk_count=5)
    assert client2._spk_count == 5


# -- factory 폴백 -----------------------------------------------------------


def test_constructor_without_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(GeminiSttCredentialError):
        GeminiSttClient()


def test_factory_gemini_without_key_falls_back_to_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert isinstance(get_stt_client("gemini"), RTZRStubClient)
