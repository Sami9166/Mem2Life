"""LLM 기반 화자 라벨 병합 후처리 (내용 기반 화자 교정).

RTZR 화자분리는 **음향 기반**이라 한 사람을 여러 화자로 과분할하는 일이 잦다
(실기기 실측: 같은 사람 혼자 발화가 화자1~4로 갈리고, 한 문장이 화자4→화자1로
쪼개짐). 이 모듈은 그 위에 **내용 기반** 층을 얹는다 — LLM이 전사 내용·대화
흐름을 읽고, 실제로 같은 사람이 말한 라벨을 하나로 합친다.

안전 원칙(지어내기/오병합 방지):
- 텍스트는 절대 바꾸지 않는다. 라벨 그룹만 재배치한다.
- 확신이 없으면 원래 분리를 유지한다(서로 다른 사람을 잘못 합치는 게 과분할보다
  나쁠 수 있으므로 보수적으로). 응답 형식이 어긋나거나 호출이 실패하면 원본 전사록을
  그대로 반환한다.
- 화자가 0~1명이면 병합할 게 없으므로 호출조차 하지 않는다.

폴백은 STT/VLM/답변 생성과 동일한 2단계다: GEMINI_API_KEY가 없으면 factory가
NoOp(무변경)을 반환하고, 키는 있는데 호출이 실패하면 여기서 원본을 그대로 돌려준다.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .base import Transcript, TranscriptSegment

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
_ENV_KEYS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
_MAX_OUTPUT_TOKENS = 1024
_TEMPERATURE = 0.0  # 결정적 교정이 목적 — 창의성 불필요.
_MAX_ERROR_CHARS = 300

SYSTEM_INSTRUCTION = (
    "당신은 전사록의 화자 라벨을 교정하는 도구입니다. 음향 화자분리가 한 사람을 "
    "여러 화자로 과분할했을 수 있습니다. 전사 내용과 대화 흐름만 보고, 실제로 같은 "
    "사람이 말한 라벨들을 하나의 그룹으로 묶으세요.\n"
    "규칙:\n"
    "- 한 문장이나 하나의 생각이 여러 라벨로 쪼개졌으면 같은 화자(같은 그룹)입니다.\n"
    "- 질문↔답변, 호칭('~님', '~야'), 상반된 입장 등 명백한 turn-taking이 있으면 "
    "다른 화자(다른 그룹)로 유지하세요.\n"
    "- 확신이 없으면 원래대로 분리를 유지하세요. 서로 다른 사람을 잘못 합치지 마세요.\n"
    "- 전사 텍스트는 절대 바꾸지 마세요. 라벨 그룹만 결정합니다."
)


@runtime_checkable
class SpeakerMerger(Protocol):
    def merge(self, transcript: Transcript) -> Transcript: ...


class NoOpSpeakerMerger:
    """무변경 병합기(기본 폴백) — GEMINI_API_KEY가 없을 때 factory가 반환한다."""

    provider_name = "noop"

    def merge(self, transcript: Transcript) -> Transcript:
        return transcript


class GeminiCredentialError(RuntimeError):
    """GEMINI_API_KEY가 없거나 google-genai 패키지를 못 불러올 때."""


def _summarize_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}".strip()
    return text[:_MAX_ERROR_CHARS] + "…" if len(text) > _MAX_ERROR_CHARS else text


def build_prompt(transcript: Transcript) -> str:
    lines = [
        f"[{i}] {seg.speaker}: {seg.text.strip()}" for i, seg in enumerate(transcript.segments, start=1)
    ]
    speakers = ", ".join(transcript.speakers)
    body = "\n".join(lines)
    return (
        f"원본 화자 라벨: {speakers}\n\n"
        f"전사록:\n{body}\n\n"
        "각 원본 화자 라벨을 그룹 번호(1부터 정수)에 매핑한 JSON만 출력하세요. "
        "같은 그룹 번호 = 같은 사람. 다른 설명·마크다운 없이 JSON 객체만.\n"
        '예: {"화자1": 1, "화자2": 1, "화자3": 2}'
    )


def _extract_json_object(text: str) -> dict[str, int]:
    """모델 출력에서 첫 JSON 객체를 뽑아 {라벨: 그룹번호}로 파싱한다."""
    fenced = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", fenced, flags=re.DOTALL)
    if not match:
        raise ValueError("응답에서 JSON 객체를 찾지 못했습니다.")
    raw = json.loads(match.group(0))
    return {str(k): int(v) for k, v in raw.items()}


def apply_mapping(transcript: Transcript, group_of: dict[str, int]) -> Transcript:
    """{원본라벨: 그룹번호}를 적용해 라벨을 합치고, 첫 등장 순서로 화자N을 재부여한다."""
    # 그룹 번호 → 새 라벨(첫 등장 순서). 매핑에 없는 라벨은 자기 자신을 그룹으로.
    new_label_of_group: dict[int, str] = {}
    label_cache: dict[str, str] = {}
    next_index = 1

    def resolve(original: str) -> str:
        if original in label_cache:
            return label_cache[original]
        nonlocal next_index
        group = group_of.get(original)
        key = group if group is not None else f"__self_{original}"
        if key not in new_label_of_group:
            new_label_of_group[key] = f"화자{next_index}"
            next_index += 1
        label_cache[original] = new_label_of_group[key]
        return label_cache[original]

    merged = [
        TranscriptSegment(
            start_sec=seg.start_sec,
            end_sec=seg.end_sec,
            speaker=resolve(seg.speaker),
            text=seg.text,
        )
        for seg in transcript.segments
    ]
    return Transcript(segments=merged, provider=transcript.provider)


class GeminiSpeakerMerger:
    """`SpeakerMerger`를 만족하는 Gemini 구현체."""

    provider_name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        client: object | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._model = model
        if client is not None:
            self._client = client
            return
        source_env = os.environ if env is None else env
        resolved = api_key or next((source_env[k] for k in _ENV_KEYS if source_env.get(k)), None)
        if not resolved:
            raise GeminiCredentialError(
                "Gemini API 인증 정보가 없습니다. backend/.env에 GEMINI_API_KEY를 설정하세요."
            )
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover
            raise GeminiCredentialError("google-genai 패키지를 불러올 수 없습니다(uv sync 확인).") from exc
        self._client = genai.Client(api_key=resolved)

    def _call_gemini(self, prompt: str) -> str:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self._model,
            contents=[prompt],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=_TEMPERATURE,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
            ),
        )
        text = getattr(response, "text", None)
        if not text or not text.strip():
            raise RuntimeError("응답 텍스트가 비어 있습니다.")
        return text.strip()

    def merge(self, transcript: Transcript) -> Transcript:
        # 화자가 0~1명이면 병합할 게 없다 — 호출조차 하지 않는다.
        if len(transcript.speakers) <= 1:
            return transcript
        try:
            raw = self._call_gemini(build_prompt(transcript))
            group_of = _extract_json_object(raw)
        except Exception as exc:  # noqa: BLE001 - 실패 시 원본 유지가 안전
            print(
                f"[경고] 화자 병합 호출이 실패해 원본 화자분리를 유지합니다: {_summarize_error(exc)}",
                file=sys.stderr,
            )
            return transcript
        # 원본 라벨을 하나라도 누락하면 신뢰하지 않고 원본 유지(보수적).
        if not set(transcript.speakers).issubset(group_of.keys()):
            print("[경고] 화자 병합 응답이 일부 라벨을 누락해 원본을 유지합니다.", file=sys.stderr)
            return transcript
        return apply_mapping(transcript, group_of)


def _build_gemini_merger() -> SpeakerMerger:
    if not any(os.environ.get(k) for k in _ENV_KEYS):
        return NoOpSpeakerMerger()
    try:
        return GeminiSpeakerMerger()
    except GeminiCredentialError as exc:
        print(f"[안내] {exc} 화자 병합 없이 진행합니다.", file=sys.stderr)
        return NoOpSpeakerMerger()


_PROVIDERS = {
    "gemini": _build_gemini_merger,
    "noop": NoOpSpeakerMerger,
}

DEFAULT_PROVIDER = "gemini"


def get_speaker_merger(provider: str = DEFAULT_PROVIDER) -> SpeakerMerger:
    try:
        return _PROVIDERS[provider.lower()]()
    except KeyError as exc:
        available = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"알 수 없는 화자 병합 provider: {provider!r} (사용 가능: {available})") from exc


def available_providers() -> Sequence[str]:
    return sorted(_PROVIDERS)
