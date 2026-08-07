"""Gemini 실제 VLM 캡션/LLM 요약 클라이언트(`ingest.vlm.gemini_client`) 테스트.

`httpx.MockTransport`로 HTTP 계층을 완전히 대체하므로(`google-genai` SDK가
`types.HttpOptions(httpx_client=...)`로 커스텀 httpx 클라이언트 주입을
지원한다 — 실제로 설치된 google-genai==2.14.0에서 직접 확인했다), 이 파일의
테스트는 절대 실제 네트워크를 타지 않는다. `test_stt_rtzr_client.py`와 같은
원칙이다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from google import genai
from google.genai import types

from ingest.stt.base import Transcript, TranscriptSegment
from ingest.visual import ProcessedKeyframe
from ingest.vlm.gemini_client import (
    GeminiAPIError,
    GeminiCredentialError,
    GeminiLLMSummarizer,
    GeminiVLMCaptioner,
)


def _fake_client(handler: Callable[[httpx.Request], httpx.Response], **kwargs: object) -> genai.Client:
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    return genai.Client(
        api_key="fake-key", http_options=types.HttpOptions(httpx_client=http_client), **kwargs
    )  # type: ignore[arg-type]


def _text_response(text: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json={
            "candidates": [{"content": {"parts": [{"text": text}], "role": "model"}, "finishReason": "STOP"}]
        },
    )


@pytest.fixture()
def keyframe_image(tmp_path: Path) -> ProcessedKeyframe:
    image_path = tmp_path / "keyframe_00m05s.jpg"
    image_path.write_bytes(b"\xff\xd8\xff\xe0fakejpegbytes")  # 내용은 검증 대상이 아님(바이트 전송만 확인)
    return ProcessedKeyframe(timestamp_str="00:05", timestamp_sec=5.0, image_path=image_path)


@pytest.fixture()
def sample_transcript() -> Transcript:
    return Transcript(
        segments=[
            TranscriptSegment(0.0, 4.0, "화자1", "이 책 진짜 좋았어."),
            TranscriptSegment(4.0, 8.0, "화자2", "오, 재밌어 보인다."),
        ],
        provider="rtzr-stub",
    )


class TestGeminiVLMCaptioner:
    def test_caption_keyframes_sends_image_and_context_and_parses_response(
        self, keyframe_image: ProcessedKeyframe, sample_transcript: Transcript
    ) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return _text_response("화자1이 책을 들고 화자2에게 보여준다.")

        captioner = GeminiVLMCaptioner(client=_fake_client(handler))
        results = captioner.caption_keyframes(
            [keyframe_image], sample_transcript, media_slug="2026-07-17_1500_test"
        )

        assert results == [(5.0, 5.0, "화자1이 책을 들고 화자2에게 보여준다.")]
        assert len(calls) == 1

        body = json.loads(calls[0].content)
        parts = body["contents"][0]["parts"]
        # 이미지 바이트가 base64로 실려갔는지 확인 (mime_type도 jpg여야 함)
        assert parts[0]["inlineData"]["mimeType"] == "image/jpeg"
        # 직전 전사록 컨텍스트가 함께 들어갔는지 확인 (EgoLife 시각+청각 융합 형식)
        assert "화자1: 이 책 진짜 좋았어." in parts[1]["text"]
        assert "화자2: 오, 재밌어 보인다." in parts[1]["text"]
        # 불확실성 명시 지시(self_assessment 연동 필수 규칙)가 system_instruction에 있는지 확인
        system_text = body["systemInstruction"]["parts"][0]["text"]
        assert "확인되지 않음" in system_text
        assert "기록되지 않음" in system_text

    def test_caption_keyframes_skips_transcript_lines_outside_context_window(
        self, keyframe_image: ProcessedKeyframe
    ) -> None:
        """키프레임보다 훨씬 이전(60초 초과)의 발화는 컨텍스트에서 빠져야 한다."""
        far_past_transcript = Transcript(
            segments=[TranscriptSegment(0.0, 1.0, "화자1", "아주 예전 발화")],
            provider="rtzr-stub",
        )
        far_keyframe = ProcessedKeyframe(
            timestamp_str="10:00", timestamp_sec=600.0, image_path=keyframe_image.image_path
        )
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return _text_response("장면 설명")

        captioner = GeminiVLMCaptioner(client=_fake_client(handler))
        captioner.caption_keyframes([far_keyframe], far_past_transcript, media_slug="slug")

        body = json.loads(calls[0].content)
        prompt_text = body["contents"][0]["parts"][1]["text"]
        assert "아주 예전 발화" not in prompt_text
        assert "직전 전사록 없음" in prompt_text

    def test_no_api_key_raises_credential_error(self) -> None:
        with pytest.raises(GeminiCredentialError, match="GEMINI_API_KEY"):
            GeminiVLMCaptioner(env={})

    def test_server_error_raises_api_error(
        self, keyframe_image: ProcessedKeyframe, sample_transcript: Transcript
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                500, json={"error": {"code": 500, "message": "internal", "status": "INTERNAL"}}
            )

        captioner = GeminiVLMCaptioner(client=_fake_client(handler))
        with pytest.raises(GeminiAPIError, match="500"):
            captioner.caption_keyframes([keyframe_image], sample_transcript, media_slug="slug")

    def test_invalid_key_raises_credential_error_not_api_error(
        self, keyframe_image: ProcessedKeyframe, sample_transcript: Transcript
    ) -> None:
        """401/403 같은 인증 문제는 재시도해도 소용없는 설정 오류이므로
        `GeminiCredentialError`로 승격돼야 한다(RTZRCredentialError와 동일한 구분)."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={"error": {"code": 401, "message": "API key not valid", "status": "UNAUTHENTICATED"}},
            )

        captioner = GeminiVLMCaptioner(client=_fake_client(handler))
        with pytest.raises(GeminiCredentialError, match="401"):
            captioner.caption_keyframes([keyframe_image], sample_transcript, media_slug="slug")

    def test_network_error_raises_api_error(
        self, keyframe_image: ProcessedKeyframe, sample_transcript: Transcript
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        captioner = GeminiVLMCaptioner(client=_fake_client(handler))
        with pytest.raises(GeminiAPIError, match="네트워크"):
            captioner.caption_keyframes([keyframe_image], sample_transcript, media_slug="slug")

    def test_rate_limit_raises_api_error_not_credential_error(
        self, keyframe_image: ProcessedKeyframe, sample_transcript: Transcript
    ) -> None:
        """429(쿼터 초과)는 401/403과 달리 재시도하면 바뀌는 일시적 문제라
        GeminiAPIError로 분류돼야 한다 — pipeline.py가 이걸 잡아 스텁으로
        폴백한다. 실제로 무료 티어 일일 한도(20req/day)에 걸려서 발견된
        회귀 테스트다(잘못 분류돼 있으면 크레덴셜 에러로 승격돼 파이프라인
        전체가 죽는다)."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                json={
                    "error": {
                        "code": 429,
                        "message": "You exceeded your current quota",
                        "status": "RESOURCE_EXHAUSTED",
                    }
                },
            )

        captioner = GeminiVLMCaptioner(client=_fake_client(handler))
        with pytest.raises(GeminiAPIError, match="429"):
            captioner.caption_keyframes([keyframe_image], sample_transcript, media_slug="slug")

    def test_max_tokens_truncation_raises_api_error(
        self, keyframe_image: ProcessedKeyframe, sample_transcript: Transcript
    ) -> None:
        """finishReason=MAX_TOKENS면 응답이 비어있지 않아도(문장 중간에 잘림)
        GeminiAPIError로 잡아 스텁 폴백을 트리거해야 한다. 실키로 검증 중
        발견된 사고를 고정한 회귀 테스트 — 최신 Gemini 모델이 내부 "thinking"
        토큰을 먼저 소비해 max_output_tokens 예산을 다 써버리면 눈에 보이는
        텍스트가 몇 글자만 남고 잘리는데, 빈 텍스트 체크만으로는 못 잡는다."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "이미지는"}], "role": "model"},
                            "finishReason": "MAX_TOKENS",
                        }
                    ]
                },
            )

        captioner = GeminiVLMCaptioner(client=_fake_client(handler))
        with pytest.raises(GeminiAPIError, match="max_output_tokens"):
            captioner.caption_keyframes([keyframe_image], sample_transcript, media_slug="slug")

    def test_empty_response_raises_api_error(
        self, keyframe_image: ProcessedKeyframe, sample_transcript: Transcript
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"candidates": []})

        captioner = GeminiVLMCaptioner(client=_fake_client(handler))
        with pytest.raises(GeminiAPIError, match="비어"):
            captioner.caption_keyframes([keyframe_image], sample_transcript, media_slug="slug")

    def test_multiple_keyframes_in_one_window_are_batched_into_one_call(
        self, keyframe_image: ProcessedKeyframe, sample_transcript: Transcript
    ) -> None:
        # 60초 창 안의 두 프레임 → 한 번의 호출(JSON 배열 응답)로 캡션.
        second_keyframe = ProcessedKeyframe(
            timestamp_str="00:10", timestamp_sec=10.0, image_path=keyframe_image.image_path
        )
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return _text_response('["첫 이미지 캡션.", "둘째 이미지 캡션."]')

        captioner = GeminiVLMCaptioner(client=_fake_client(handler))
        results = captioner.caption_keyframes(
            [keyframe_image, second_keyframe], sample_transcript, media_slug="slug"
        )

        assert call_count == 1, "배치는 한 번의 호출이어야 한다"
        assert [ts_s for ts_s, _e, _t in results] == [5.0, 10.0]
        assert [text for _s, _e, text in results] == ["첫 이미지 캡션.", "둘째 이미지 캡션."]

    def test_batch_format_error_falls_back_to_per_frame(
        self, keyframe_image: ProcessedKeyframe, sample_transcript: Transcript
    ) -> None:
        # 첫 배치 호출이 JSON 배열이 아니면(형식 오류) 그 배치를 프레임별로 재호출.
        second_keyframe = ProcessedKeyframe(
            timestamp_str="00:10", timestamp_sec=10.0, image_path=keyframe_image.image_path
        )
        replies = iter(["JSON 아님 그냥 텍스트", "프레임 캡션 A", "프레임 캡션 B"])

        def handler(request: httpx.Request) -> httpx.Response:
            return _text_response(next(replies))

        captioner = GeminiVLMCaptioner(client=_fake_client(handler))
        results = captioner.caption_keyframes(
            [keyframe_image, second_keyframe], sample_transcript, media_slug="slug"
        )
        assert [text for _s, _e, text in results] == ["프레임 캡션 A", "프레임 캡션 B"]

    def test_frames_more_than_a_window_apart_are_separate_batches(
        self, keyframe_image: ProcessedKeyframe, sample_transcript: Transcript
    ) -> None:
        # 90초 떨어진 두 프레임 → 서로 다른 배치(각 1프레임 → 프레임별 호출) = 2회.
        far = ProcessedKeyframe(
            timestamp_str="01:35", timestamp_sec=95.0, image_path=keyframe_image.image_path
        )
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return _text_response(f"캡션 {call_count}")

        captioner = GeminiVLMCaptioner(client=_fake_client(handler))
        captioner.caption_keyframes([keyframe_image, far], sample_transcript, media_slug="slug")
        assert call_count == 2


class TestGeminiLLMSummarizer:
    def test_summarize_session_wraps_participants_as_wikilinks_instruction(
        self, sample_transcript: Transcript
    ) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return _text_response("[[민수]]와 책 이야기를 나눴다.")

        summarizer = GeminiLLMSummarizer(client=_fake_client(handler))
        summary = summarizer.summarize_session(
            sample_transcript, [(5.0, 5.0, "책을 들어 보인다.")], participants=["민수"]
        )

        assert summary == "[[민수]]와 책 이야기를 나눴다."
        body = json.loads(calls[0].content)
        prompt_text = body["contents"][0]["parts"][0]["text"]
        assert "참석자: 민수" in prompt_text
        assert "화자1: 이 책 진짜 좋았어." in prompt_text
        assert "책을 들어 보인다." in prompt_text
        system_text = body["systemInstruction"]["parts"][0]["text"]
        assert "[[이름]]" in system_text

    def test_summarize_session_returns_none_for_empty_transcript(self) -> None:
        empty_transcript = Transcript(segments=[], provider="rtzr-stub")

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("전사록이 비어 있으면 API 호출 자체가 나가면 안 됨")

        summarizer = GeminiLLMSummarizer(client=_fake_client(handler))
        assert summarizer.summarize_session(empty_transcript, [], participants=[]) is None

    def test_no_api_key_raises_credential_error(self) -> None:
        with pytest.raises(GeminiCredentialError, match="GEMINI_API_KEY"):
            GeminiLLMSummarizer(env={})


def test_batch_by_window_caps_at_max_frames() -> None:
    from ingest.vlm.gemini_client import _CAPTION_BATCH_MAX_FRAMES, _batch_by_window

    # 같은 창(0~9초) 안에 10프레임 → max_frames(8)에서 나뉘어야 한다.
    kfs = [
        ProcessedKeyframe(timestamp_str=f"00:{i:02d}", timestamp_sec=float(i), image_path=Path("x.jpg"))
        for i in range(10)
    ]
    batches = _batch_by_window(kfs, 60.0, _CAPTION_BATCH_MAX_FRAMES)
    assert [len(b) for b in batches] == [8, 2]


def test_batch_by_window_splits_on_time_gap() -> None:
    from ingest.vlm.gemini_client import _batch_by_window

    kfs = [
        ProcessedKeyframe(timestamp_str="00:00", timestamp_sec=0.0, image_path=Path("a.jpg")),
        ProcessedKeyframe(timestamp_str="00:30", timestamp_sec=30.0, image_path=Path("b.jpg")),
        ProcessedKeyframe(timestamp_str="01:40", timestamp_sec=100.0, image_path=Path("c.jpg")),
    ]
    batches = _batch_by_window(kfs, 60.0, 8)
    # 0·30초는 한 창, 100초는 60초 넘게 떨어져 새 배치.
    assert [[k.timestamp_sec for k in b] for b in batches] == [[0.0, 30.0], [100.0]]


def test_parse_caption_array_rejects_count_mismatch() -> None:
    from ingest.vlm.gemini_client import _BatchCaptionFormatError, _parse_caption_array

    with pytest.raises(_BatchCaptionFormatError):
        _parse_caption_array('["하나만"]', expected=2)


def test_parse_caption_array_strips_markdown_fence() -> None:
    from ingest.vlm.gemini_client import _parse_caption_array

    out = _parse_caption_array('```json\n["가", "나"]\n```', expected=2)
    assert out == ["가", "나"]
