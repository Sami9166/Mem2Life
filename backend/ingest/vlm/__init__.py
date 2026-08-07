"""VLM 장면 캡션 + LLM 세션 요약 클라이언트 패키지.

기술조사_의사결정.md 조사4(2026-07-26 갱신)에 따라 VLM 캡션(이미지 입력)과
LLM 요약(텍스트 입력)을 모두 Gemini로 통일했다. `stt/` 패키지와 동일한
Protocol + factory 패턴을 따른다:

    base.py          — `VLMCaptioner`/`LLMSummarizer` Protocol 정의
    prompts.py        — 프롬프트 문구(네트워크와 무관, 단독 테스트 가능)
    gemini_client.py  — 실제 Gemini(`google-genai`) 구현체
    stub.py           — GEMINI_API_KEY가 없거나 호출이 실패했을 때 쓰는 플레이스홀더
    factory.py        — provider 선택 (`get_vlm_captioner()`/`get_llm_summarizer()`)

외부에서는 보통 factory 함수만 사용하면 된다:

    from ingest.vlm import get_vlm_captioner, get_llm_summarizer

    captioner = get_vlm_captioner()  # 또는 "gemini" 명시
    captions = captioner.caption_keyframes(keyframes, transcript, media_slug=slug)
"""

from __future__ import annotations

from .base import CaptionItem, LLMSummarizer, VLMCaptioner
from .factory import (
    DEFAULT_CAPTION_PROVIDER,
    DEFAULT_SUMMARY_PROVIDER,
    available_caption_providers,
    available_summary_providers,
    get_llm_summarizer,
    get_vlm_captioner,
)
from .gemini_client import GeminiAPIError, GeminiCredentialError, GeminiLLMSummarizer, GeminiVLMCaptioner
from .stub import PlaceholderLLMSummarizer, PlaceholderVLMCaptioner

__all__ = [
    "CaptionItem",
    "VLMCaptioner",
    "LLMSummarizer",
    "GeminiVLMCaptioner",
    "GeminiLLMSummarizer",
    "GeminiCredentialError",
    "GeminiAPIError",
    "PlaceholderVLMCaptioner",
    "PlaceholderLLMSummarizer",
    "get_vlm_captioner",
    "get_llm_summarizer",
    "available_caption_providers",
    "available_summary_providers",
    "DEFAULT_CAPTION_PROVIDER",
    "DEFAULT_SUMMARY_PROVIDER",
]
