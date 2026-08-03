"""VLM 캡션 + LLM 요약 클라이언트의 추상 인터페이스.

`stt/base.py`의 `SpeechToTextClient` Protocol과 같은 이유로 존재한다 — 지금은
Gemini 구현체 하나만 등록돼 있지만(기술조사_의사결정.md 조사4), 다른 모델로
교체될 가능성을 감안해 실제 구현체를 Protocol 뒤에 숨기고
`factory.get_vlm_captioner()`/`factory.get_llm_summarizer()`로 provider 이름만
바꿔 주입할 수 있게 한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ..stt.base import Transcript
from ..visual import ProcessedKeyframe

# (시작 초, 종료 초, 캡션/설명 텍스트) — `ingest/pipeline.py`가 세션 md에 쓰는
# captions/highlights 튜플과 동일한 모양이다.
CaptionItem = tuple[float, float, str]


@runtime_checkable
class VLMCaptioner(Protocol):
    """키프레임 이미지 + 직전 전사록을 넣어 한국어 장면 캡션을 만드는 인터페이스."""

    provider_name: str

    def caption_keyframes(
        self,
        keyframes: Sequence[ProcessedKeyframe],
        transcript: Transcript,
        *,
        media_slug: str,
    ) -> list[CaptionItem]:
        """세션의 키프레임마다 (시작 초, 종료 초, 한국어 캡션) 튜플을 만든다.

        Args:
            keyframes: `ingest/visual.py`가 뽑은 대표 키프레임 목록(세션 기준
                절대 타임스탬프, `image_path`가 실제 jpg 파일을 가리킴).
            transcript: 세션 전체 전사록. 각 키프레임 직전 구간을 컨텍스트로
                함께 넣는 데 쓰인다(EgoLife 시각+청각 융합 캡션 형식).
            media_slug: `vault/media/{media_slug}/` 서브디렉토리 이름. 실제
                VLM 구현체는 쓰지 않아도 되지만(캡션 텍스트 자체에는 이미지
                경로를 넣지 않음 — Obsidian 볼트 스키마의 순수 텍스트 캡션
                관례를 따름), 플레이스홀더 구현체는 이미지 임베드 링크를
                만드는 데 쓴다.
        """
        ...


@runtime_checkable
class LLMSummarizer(Protocol):
    """세션 전체 전사록(+캡션)을 보고 `## 요약`에 들어갈 한국어 문단을 만드는 인터페이스."""

    provider_name: str

    def summarize_session(
        self,
        transcript: Transcript,
        captions: Sequence[CaptionItem],
        participants: Sequence[str],
    ) -> str | None:
        """세션 요약 문단을 만든다. 요약할 내용이 없으면(빈 전사록 등) `None`을 반환한다.

        `None`을 반환하면 `ingest/wiki/session_md.py`가 기존과 동일하게
        TODO 플레이스홀더로 채운다 — 플레이스홀더 구현체(`stub.py`)가 정확히
        이 경로로 동작한다.
        """
        ...
