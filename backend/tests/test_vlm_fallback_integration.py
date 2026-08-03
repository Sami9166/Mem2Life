"""VLM 캡션(`ingest/vlm/`) → 세션 md → recall 청킹 → self_assessment/fallback
트리거까지 이어지는 종단 통합 테스트.

이 테스트가 지키는 계약: `ingest/vlm/prompts.py`의 캡션 프롬프트가 "불확실한
세부사항은 명시적으로 미확인 문구로 쓰라"고 지시하고, 실제로 그런 캡션이
생성되면 `recall/fallback/self_assessment.py`가 이를 인식해 fallback(영상
재조회)을 트리거해야 한다 — 반대로 확신을 가지고 서술한 캡션은 fallback을
트리거하면 안 된다. 이 안전장치가 조용히 깨지지 않는지 실제 파이프라인
경로(Gemini 클라이언트 → 세션 md 작성 → recall 청킹)로 확인한다.

`recall/`은 읽기 전용으로만 쓴다(직접 수정하지 않음) — wiki-builder<->recall-dev
계약(Obsidian md 스키마)이 실제로 맞물려 동작하는지 검증하는 목적.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import httpx
from google import genai
from google.genai import types

from ingest.stt.base import Transcript, TranscriptSegment
from ingest.visual import ProcessedKeyframe
from ingest.vlm.gemini_client import GeminiVLMCaptioner
from ingest.wiki.session_md import write_session_md
from recall.answer.base import AnswerResult, citation_from_chunk
from recall.classify.question_type import QuestionType
from recall.fallback.self_assessment import assess_sufficiency
from recall.fallback.trigger import decide_fallback
from recall.vault.chunking import chunk_session_document
from recall.vault.loader import load_document
from recall.vault.types import ChunkLevel, Evidence


def _gemini_client_returning(text: str) -> genai.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": text}], "role": "model"}, "finishReason": "STOP"}
                ]
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    return genai.Client(api_key="fake-key", http_options=types.HttpOptions(httpx_client=http_client))


def _write_session_with_caption(vault_dir: Path, caption_text: str) -> Path:
    """캡션 텍스트 하나를 담은 최소 세션 md를 실제 `write_session_md()`로 만든다."""
    transcript = Transcript(
        segments=[TranscriptSegment(0.0, 4.0, "화자1", "이 책 진짜 좋았어, 한번 볼래?")],
        provider="rtzr-stub",
    )
    return write_session_md(
        vault_dir,
        session_start=datetime(2026, 7, 17, 15, 0),
        title="VLM 통합 테스트",
        participants=["화자1", "화자2"],
        video_path="testdata/videos/test_session_A_20260717.mp4",
        transcript=transcript,
        captions=[(80.0, caption_text)],
    )


def _scene_caption_chunk_from_md(md_path: Path, vault_dir: Path):
    doc = load_document(md_path, vault_dir)
    chunks = chunk_session_document(doc)
    scene_caption_chunks = [c for c in chunks if c.level is ChunkLevel.SCENE_CAPTION]
    assert len(scene_caption_chunks) == 1, "이 테스트는 캡션 1건짜리 최소 세션만 다룬다"
    return scene_caption_chunks[0]


def test_uncertain_caption_from_real_captioner_triggers_visual_fallback(tmp_path: Path) -> None:
    """이미지에서 확실히 읽을 수 없는 세부사항(예: 책 표지 문구)을 캡션 프롬프트
    지시대로 명시적 미확인 문구로 서술하면, 그 캡션이 세션 md에 저장되고
    recall이 인덱싱한 뒤 self_assessment가 fallback을 트리거해야 한다."""
    keyframe = ProcessedKeyframe(
        timestamp_str="01:20",
        timestamp_sec=80.0,
        image_path=(lambda p: (p.write_bytes(b"\xff\xd8\xff\xe0fakejpeg"), p)[1])(
            tmp_path / "keyframe_01m20s.jpg"
        ),
    )
    transcript = Transcript(
        segments=[TranscriptSegment(0.0, 4.0, "화자1", "이 책 진짜 좋았어, 한번 볼래?")],
        provider="rtzr-stub",
    )

    # 실제 GeminiVLMCaptioner가 캡션 프롬프트 지시를 따라 만들어냈을 법한
    # 응답을 흉내낸다(네트워크는 MockTransport로 완전히 대체).
    captioner = GeminiVLMCaptioner(
        client=_gemini_client_returning(
            "화자2가 표지에 삽화가 있는 책 한 권을 카메라 쪽으로 들어 보인다. "
            "표지 문구·제목은 해상도 문제로 확인되지 않음."
        )
    )
    results = captioner.caption_keyframes([keyframe], transcript, media_slug="slug")
    _start_sec, _end_sec, caption_text = results[0]

    vault_dir = tmp_path / "vault"
    md_path = _write_session_with_caption(vault_dir, caption_text)
    chunk = _scene_caption_chunk_from_md(md_path, vault_dir)

    evidence = (Evidence(chunk=chunk, score=0.6),)
    answer = AnswerResult(
        text=chunk.text, citations=(citation_from_chunk(chunk),), grounded=True, evidence=evidence
    )

    verdict = assess_sufficiency(QuestionType.VISUAL, answer)
    assert not verdict.sufficient
    assert "기록되" in verdict.reason or "확인되" in verdict.reason

    decision = decide_fallback(QuestionType.VISUAL, answer)
    assert decision.triggered
    assert len(decision.clip_targets) == 1


def test_confident_caption_from_real_captioner_does_not_trigger_fallback(tmp_path: Path) -> None:
    """이미지에서 확실히 보이는 내용을 서술한 캡션(불확실성 문구 없음)은
    fallback을 트리거하면 안 된다 — 안전장치가 과민반응하지 않는지 확인."""
    keyframe = ProcessedKeyframe(
        timestamp_str="01:48",
        timestamp_sec=108.0,
        image_path=(lambda p: (p.write_bytes(b"\xff\xd8\xff\xe0fakejpeg"), p)[1])(
            tmp_path / "keyframe_01m48s.jpg"
        ),
    )
    transcript = Transcript(
        segments=[TranscriptSegment(0.0, 4.0, "화자1", "충전기 여기 넣어둔다?")],
        provider="rtzr-stub",
    )

    captioner = GeminiVLMCaptioner(
        client=_gemini_client_returning("화자1이 검은색 충전기를 책상 서랍 안에 넣는다.")
    )
    results = captioner.caption_keyframes([keyframe], transcript, media_slug="slug")
    _start_sec, _end_sec, caption_text = results[0]

    vault_dir = tmp_path / "vault"
    md_path = _write_session_with_caption(vault_dir, caption_text)
    chunk = _scene_caption_chunk_from_md(md_path, vault_dir)

    evidence = (Evidence(chunk=chunk, score=0.6),)
    answer = AnswerResult(
        text=chunk.text, citations=(citation_from_chunk(chunk),), grounded=True, evidence=evidence
    )

    verdict = assess_sufficiency(QuestionType.VISUAL, answer)
    assert verdict.sufficient

    decision = decide_fallback(QuestionType.VISUAL, answer)
    assert not decision.triggered
