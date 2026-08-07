"""LLM 화자 병합 후처리 테스트.

Gemini 응답을 가짜 클라이언트로 주입해 네트워크 없이 병합 로직을 결정적으로
검증한다(quota와 무관). 핵심: 과분할 병합, 진짜 대화는 유지, 실패 시 원본 보존.
"""

from __future__ import annotations

from ingest.stt.base import Transcript, TranscriptSegment
from ingest.stt.speaker_merge import (
    GeminiSpeakerMerger,
    NoOpSpeakerMerger,
    apply_mapping,
    get_speaker_merger,
)


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, text: str) -> None:
        self._text = text

    def generate_content(self, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse(self._text)


class _FakeClient:
    """`.models.generate_content(...).text`만 흉내내는 최소 가짜 클라이언트."""

    def __init__(self, text: str) -> None:
        self.models = _FakeModels(text)


class _RaisingModels:
    def generate_content(self, **_kwargs: object) -> _FakeResponse:
        raise RuntimeError("boom")


class _RaisingClient:
    def __init__(self) -> None:
        self.models = _RaisingModels()


def _oversplit() -> Transcript:
    """한 사람이 화자1~4로 과분할 — 한 문장이 화자4→화자1로 갈림."""
    return Transcript(
        segments=[
            TranscriptSegment(3, 5, "화자1", "안녕하세요, 발표를 맡은 사람입니다."),
            TranscriptSegment(5, 11, "화자2", "오늘 아침에 가장 먼저 하신 일이 무엇인가요?"),
            TranscriptSegment(11, 18, "화자1", "대부분 스마트폰을 확인하셨을 겁니다."),
            TranscriptSegment(18, 28, "화자3", "과도한 사용은 거북목 증후군을 유발합니다."),
            TranscriptSegment(28, 30, "화자4", "오늘 저는 일상에서"),
            TranscriptSegment(30, 36, "화자1", "쉽게 실천할 방법을 소개합니다."),
        ],
        provider="rtzr",
    )


def test_merges_oversplit_into_single_speaker() -> None:
    # 모델이 네 라벨을 모두 같은 그룹(1)으로 판정.
    client = _FakeClient('{"화자1": 1, "화자2": 1, "화자3": 1, "화자4": 1}')
    merged = GeminiSpeakerMerger(client=client).merge(_oversplit())
    assert merged.speakers == ["화자1"]
    # 텍스트는 그대로여야 한다.
    assert merged.segments[0].text == "안녕하세요, 발표를 맡은 사람입니다."
    assert merged.segments[4].text == "오늘 저는 일상에서"


def test_keeps_genuine_two_speakers_and_renumbers_by_first_appearance() -> None:
    # 화자1,3 = 그룹1 / 화자2,4 = 그룹2. 첫 등장 순서로 재부여되어야 한다.
    client = _FakeClient('{"화자1": 1, "화자2": 2, "화자3": 1, "화자4": 2}')
    merged = GeminiSpeakerMerger(client=client).merge(_oversplit())
    assert merged.speakers == ["화자1", "화자2"]
    # 세그먼트 원본: 화자1,화자2,화자1,화자3,화자4,화자1
    # 매핑: 화자1·화자3→그룹1(=화자1), 화자2·화자4→그룹2(=화자2)
    assert [s.speaker for s in merged.segments] == ["화자1", "화자2", "화자1", "화자1", "화자2", "화자1"]


def test_single_speaker_is_not_sent_to_model() -> None:
    # 화자 1명이면 호출조차 하지 않는다 — 예외를 던지는 클라이언트로도 안전.
    one = Transcript(segments=[TranscriptSegment(0, 1, "화자1", "혼잣말.")], provider="rtzr")
    merged = GeminiSpeakerMerger(client=_RaisingClient()).merge(one)
    assert merged.speakers == ["화자1"]


def test_call_failure_preserves_original() -> None:
    merged = GeminiSpeakerMerger(client=_RaisingClient()).merge(_oversplit())
    assert merged.speakers == ["화자1", "화자2", "화자3", "화자4"]


def test_malformed_response_preserves_original() -> None:
    client = _FakeClient("그냥 아무 말이나 하는 응답")
    merged = GeminiSpeakerMerger(client=client).merge(_oversplit())
    assert merged.speakers == ["화자1", "화자2", "화자3", "화자4"]


def test_missing_labels_preserves_original() -> None:
    # 응답이 원본 라벨 일부(화자4)를 누락 → 신뢰하지 않고 원본 유지.
    client = _FakeClient('{"화자1": 1, "화자2": 1, "화자3": 1}')
    merged = GeminiSpeakerMerger(client=client).merge(_oversplit())
    assert merged.speakers == ["화자1", "화자2", "화자3", "화자4"]


def test_json_in_markdown_fence_is_parsed() -> None:
    client = _FakeClient('```json\n{"화자1": 1, "화자2": 1, "화자3": 1, "화자4": 1}\n```')
    merged = GeminiSpeakerMerger(client=client).merge(_oversplit())
    assert merged.speakers == ["화자1"]


def test_apply_mapping_renumbers_deterministically() -> None:
    merged = apply_mapping(_oversplit(), {"화자1": 5, "화자2": 5, "화자3": 9, "화자4": 5})
    # 그룹5(첫 등장) → 화자1, 그룹9 → 화자2
    assert merged.speakers == ["화자1", "화자2"]


def test_factory_without_key_returns_noop(monkeypatch) -> None:
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert isinstance(get_speaker_merger("gemini"), NoOpSpeakerMerger)


def test_noop_is_passthrough() -> None:
    t = _oversplit()
    assert NoOpSpeakerMerger().merge(t) is t
