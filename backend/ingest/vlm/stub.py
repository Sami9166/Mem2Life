"""GEMINI_API_KEY가 없거나 실제 호출이 실패했을 때 쓰는 플레이스홀더 구현체.

RTZR 스텁(`stt/rtzr_stub.py`)과 같은 원칙 — CLAUDE.md의 "API 키 없이도 전체
파이프라인이 끝까지 실행돼야 한다" 원칙을 지키기 위해 존재한다. 이전에
`ingest/pipeline.py` 안에 있던 `_placeholder_captions_from_keyframes()`/
`_TODO_SUMMARY` 폴백 동작을 `VLMCaptioner`/`LLMSummarizer` Protocol을 만족하는
클래스로 그대로 옮겨왔다 — `pipeline.py`는 이제 실제 Gemini 구현체와 이
플레이스홀더를 구분하지 않고 동일한 인터페이스로 호출한다.

세션 md 자체의 "요약/캡션이 비었을 때 TODO를 채우는" 최종 방어선은 여전히
`ingest/wiki/session_md.py`(`_TODO_SUMMARY`/`_TODO_SCENE_CAPTIONS`)가 담당한다
— 그래서 `PlaceholderLLMSummarizer`는 `None`을 반환해 그 로직에 그대로
위임한다(요약 문구를 두 곳에서 따로 관리하지 않기 위함).
"""

from __future__ import annotations

from collections.abc import Sequence

from ..stt.base import Transcript
from ..visual import ProcessedKeyframe
from .base import CaptionItem


class PlaceholderVLMCaptioner:
    """저장된 키프레임 이미지만이라도 볼트에서 바로 보이게 하는 임시 캡션.

    실제 장면 서술(무엇이 찍혔는지)이 아니라 "이 시점에 대표 이미지가 준비돼
    있다"는 것만 알려주는 placeholder다. 캡션 텍스트에 "TODO"를 포함시켜
    `recall/vault/chunking.py`의 `_is_placeholder()`가 이 섹션 전체를 검색
    인덱싱에서 제외하게 한다(실제 캡션이 아니므로 근거로 쓰이면 안 됨).
    """

    provider_name = "placeholder"

    def caption_keyframes(
        self,
        keyframes: Sequence[ProcessedKeyframe],
        transcript: Transcript,
        *,
        media_slug: str,
    ) -> list[CaptionItem]:
        del transcript  # 플레이스홀더는 전사록 컨텍스트를 쓰지 않는다(Protocol 시그니처 유지용).
        return [
            (
                keyframe.timestamp_sec,
                keyframe.timestamp_sec,
                f"![[media/{media_slug}/{keyframe.image_path.name}]] "
                "— TODO: VLM 캡션 (키프레임 이미지만 준비됨)",
            )
            for keyframe in keyframes
        ]


class PlaceholderLLMSummarizer:
    """LLM 요약이 아직 없을 때 `None`을 반환해 `session_md.py`의 TODO 플레이스홀더에 위임한다."""

    provider_name = "placeholder"

    def summarize_session(
        self,
        transcript: Transcript,
        captions: Sequence[CaptionItem],
        participants: Sequence[str],
    ) -> str | None:
        del transcript, captions, participants  # Protocol 시그니처 유지용, 실제로는 쓰지 않는다.
        return None
