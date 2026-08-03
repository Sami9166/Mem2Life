"""Gemini 기반 자연어 답변 생성기 (질의 경로의 LLM 연동).

`TemplateAnswerGenerator`(근거 문장을 그대로 이어붙이는 오프라인 스텁)를
대체해, 검색된 근거만으로 자연스러운 한국어 한두 문장을 만든다. 답변은
글래스 스피커로 TTS 재생되므로 "읽어서 자연스러운 문장"이 목적이다.

## 지어내지 않기 위한 3중 방어

LLM에게 문장 생성을 맡기는 순간 "답을 지어내지 않는다"(CLAUDE.md 핵심 원칙)가
프롬프트 준수에만 의존하게 된다. 그래서 프롬프트 밖에 코드로 세 겹을 둔다:

1. **호출 전 게이트**: 점수가 양수인 근거가 하나도 없으면 Gemini를 호출조차
   하지 않고 곧바로 "기록에 없음"을 반환한다(`TemplateAnswerGenerator`와
   동일한 판정). 무관한 질문이 모델에게 도달하지 않으므로 지어낼 기회 자체가
   없고, 무료 티어 호출 수도 아낀다.
2. **sentinel 강제**: 주어진 근거만으로 답할 수 없으면 첫 줄을 `[근거부족]`으로
   시작하라고 지시하고, 그 경우 `grounded=False`로 만들어 fallback(영상 재조회)
   경로로 넘긴다. 이게 없으면 모델이 "기록에 없다"는 문장을 만들어도
   `assess_sufficiency()`는 `grounded=True`만 보고 충분하다고 판정해
   영상 재조회가 트리거되지 않는다(`fallback/self_assessment.py` 참고).
   형식이 불명확하면 안전한 쪽(템플릿 답변)으로 되돌린다.
3. **인용은 코드가 붙인다**: `(근거: ...)` 문구는 모델 출력에서 뽑지 않고
   실제 `Chunk`에서 만든다(`citation_from_chunk`). 모델이 출처를 지어낼 수 있는
   경로 자체를 없앤다.

## 두 단계 폴백 (STT/VLM과 동일 — `ingest/vlm/factory.py` 참고)

    1. 생성 시점: GEMINI_API_KEY가 없으면 `answer/factory.py`가 아예
       `TemplateAnswerGenerator`를 반환한다(테스트/CI가 키 없이 그대로 통과).
    2. 실행 시점: 키는 있지만 호출이 실패하면(429/5xx/네트워크/빈 응답) 여기서
       템플릿 답변으로 대체한다 — 질의응답은 데모의 마지막 단계라 여기서 예외를
       던지면 사용자에게 아무 답도 못 준다.

`ingest/vlm/gemini_client.py`의 헬퍼를 재사용하지 않고 이 모듈이 자체적으로
google-genai를 호출하는 이유는 `recall/fallback/gemini_requery.py`와 같다:
`ingest/pipeline.py`가 `recall.index.postgres_store`를 임포트하므로
recall → ingest 방향 임포트는 순환이 된다.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from ..vault.types import Evidence
from .base import AnswerResult, Citation, citation_from_chunk
from .template_generator import TemplateAnswerGenerator

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

_ENV_KEYS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

# 프롬프트에 넣을 근거 최대 개수. 너무 많으면 모델이 질문과 무관한 근거까지
# 끌어와 답을 흐리고, 너무 적으면 정답 근거가 잘린다.
_MAX_EVIDENCE = 6

# `ingest/vlm/gemini_client.py`와 같은 이유로 넉넉하게 잡는다 — gemini-flash-latest는
# 내부 thinking 토큰을 먼저 소비해서 상한이 빠듯하면 답이 문장 중간에서 잘린다.
_MAX_OUTPUT_TOKENS = 2048

# 사실 전달이 목적이라 창작적 다양성보다 일관성을 우선한다.
_TEMPERATURE = 0.2

_MAX_ERROR_CHARS = 300

_GROUNDED_SENTINEL = "답변"
_UNGROUNDED_SENTINEL = "근거부족"

# 근거는 있지만(점수 양수) 그 근거로는 질문에 답할 수 없다고 모델이 판단한 경우.
# `_NO_INFO_MARKERS`(fallback/self_assessment.py)와 어휘를 맞춰, 이 문구가 나중에
# 다른 층위에서 다시 평가되더라도 "미확인"으로 일관되게 읽히도록 한다.
NO_ANSWER_IN_EVIDENCE_TEXT = "기록에 없음 — 검색된 기록만으로는 확인되지 않습니다."

SYSTEM_INSTRUCTION = (
    "당신은 스마트 글래스 착용자의 개인 기억 보조 assistant입니다. "
    "착용자가 직접 겪은 일의 기록(전사록·장면 캡션·요약)에서 검색된 근거만 보고 "
    "질문에 답합니다.\n"
    "절대 규칙: 주어진 근거에 없는 사실은 어떤 경우에도 덧붙이지 마세요. "
    "일반 상식으로 빈칸을 메우거나, 그럴듯한 이름·숫자·날짜를 추측하지 마세요. "
    "기억나지 않는 일을 확인해주는 것이 목적이므로, 틀린 답은 답이 없는 것보다 나쁩니다."
)


class GeminiCredentialError(RuntimeError):
    """GEMINI_API_KEY가 없거나 google-genai 패키지를 못 불러올 때."""


def _summarize_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}".strip()
    if len(text) > _MAX_ERROR_CHARS:
        return text[:_MAX_ERROR_CHARS] + "…"
    return text


def _citation_note(citations: Sequence[Citation]) -> str:
    """중복 제거한 인용 문구 — 템플릿 생성기와 같은 형식을 유지한다."""
    labels: dict[str, None] = {}
    for c in citations:
        labels.setdefault(c.label, None)
    return " / ".join(labels)


def build_evidence_block(evidence: Sequence[Evidence]) -> str:
    """근거를 모델이 읽을 번호 매긴 블록으로 만든다.

    각 근거에 출처(세션·날짜·시각)를 함께 넣어, 모델이 "언제 있었던 일인지"를
    답변 문장에 자연스럽게 녹일 수 있게 한다.
    """
    lines: list[str] = []
    for index, ev in enumerate(evidence, start=1):
        chunk = ev.chunk
        speaker = f"{chunk.speaker}: " if chunk.speaker else ""
        lines.append(f"[근거 {index}] {chunk.citation_label}\n{speaker}{chunk.text.strip()}")
    return "\n\n".join(lines)


def build_prompt(question: str, evidence: Sequence[Evidence]) -> str:
    return (
        f"질문: {question}\n\n"
        "아래는 착용자의 기록에서 이 질문으로 검색된 근거입니다.\n\n"
        f"{build_evidence_block(evidence)}\n\n"
        "규칙:\n"
        f"- 근거만으로 답할 수 있으면 첫 줄을 정확히 '[{_GROUNDED_SENTINEL}]'로 시작하고, "
        "그 다음 줄부터 한국어 1~2문장으로 답하세요.\n"
        f"- 근거가 질문과 관련은 있지만 답을 확정할 수 없으면 첫 줄을 정확히 "
        f"'[{_UNGROUNDED_SENTINEL}]'로 시작하고, 무엇이 기록에 없는지 한 문장으로만 쓰세요. "
        "이 경우 추측한 답을 덧붙이지 마세요.\n"
        "- 답변은 음성으로 읽어주므로 자연스러운 구어체로 쓰고, 목록·마크다운·"
        "이모지를 쓰지 마세요.\n"
        "- 출처 표기(근거 번호, 세션명, 타임스탬프)는 시스템이 따로 붙이므로 "
        "답변 문장에는 넣지 마세요."
    )


def split_sentinel(text: str) -> tuple[str | None, str]:
    """모델 출력에서 첫 줄 sentinel과 본문을 분리한다.

    Returns:
        (sentinel, body). sentinel은 `"답변"`/`"근거부족"`/`None`(형식 불명확).
    """
    stripped = text.strip()
    if not stripped:
        return None, ""
    first_line, _, rest = stripped.partition("\n")
    for sentinel in (_GROUNDED_SENTINEL, _UNGROUNDED_SENTINEL):
        if f"[{sentinel}]" in first_line:
            # sentinel만 있는 줄이면 나머지가 본문, 같은 줄에 이어 썼으면 그 뒤가 본문.
            inline = first_line.split(f"[{sentinel}]", 1)[1].strip()
            body = f"{inline}\n{rest}".strip() if inline else rest.strip()
            return sentinel, body
    return None, stripped


class GeminiAnswerGenerator:
    """`AnswerGenerator` Protocol(`base.py`)을 만족하는 실제 Gemini 구현체."""

    provider_name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        client: object | None = None,
        max_evidence: int = _MAX_EVIDENCE,
        env: dict[str, str] | None = None,
        fallback_generator: TemplateAnswerGenerator | None = None,
    ) -> None:
        """
        Args:
            api_key: 명시하면 환경변수보다 우선. 생략 시 `env`(기본 `os.environ`)의
                GEMINI_API_KEY 또는 GOOGLE_API_KEY를 읽는다.
            model: 사용할 Gemini 모델 이름.
            client: 주입 가능한 genai.Client 유사 객체(`.models.generate_content`를
                가진 것). 테스트에서 네트워크 없이 응답을 흉내낼 때 쓴다. 주입하면
                api_key 검사를 건너뛴다.
            max_evidence: 프롬프트에 넣을 근거 최대 개수.
            env: 환경변수 딕셔너리(기본 `os.environ`). 테스트 결정성용 주입 가능.
            fallback_generator: 호출 실패 시 대체할 생성기(기본 템플릿 생성기).
        """
        self._model = model
        self._max_evidence = max(1, max_evidence)
        self._fallback = fallback_generator or TemplateAnswerGenerator()

        if client is not None:
            self._client = client
            return

        source_env = os.environ if env is None else env
        resolved_key = api_key or next((source_env[k] for k in _ENV_KEYS if source_env.get(k)), None)
        if not resolved_key:
            raise GeminiCredentialError(
                "Gemini API 인증 정보가 없습니다. backend/.env에 GEMINI_API_KEY를 "
                "설정하세요 (Google AI Studio에서 발급, .env.example 참고)."
            )
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - 의존성 설치 시 발생 안 함
            raise GeminiCredentialError(
                "google-genai 패키지를 불러올 수 없습니다. `uv sync`로 설치했는지 확인하세요."
            ) from exc
        self._client = genai.Client(api_key=resolved_key)

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
        candidates = getattr(response, "candidates", None) or []
        finish_reason = getattr(candidates[0], "finish_reason", None) if candidates else None
        if finish_reason is not None and getattr(finish_reason, "name", "") == "MAX_TOKENS":
            # thinking 토큰이 예산을 먼저 먹으면 응답이 비진 않고 문장 중간에서
            # 잘린다 — 빈 문자열 검사로는 못 잡으므로 별도로 걸러 템플릿으로 되돌린다
            # (`ingest/vlm/gemini_client.py`에서 실키로 확인된 것과 같은 현상).
            raise RuntimeError("응답이 max_output_tokens 한도에서 잘렸습니다(thinking 토큰 소비 포함).")
        text = getattr(response, "text", None)
        if not text or not text.strip():
            raise RuntimeError("응답 텍스트가 비어 있습니다(빈 후보 또는 안전 필터 차단).")
        return text.strip()

    def generate(self, question: str, evidence: Sequence[Evidence]) -> AnswerResult:
        all_evidence = tuple(evidence)
        positive = [e for e in all_evidence if e.score > 0]
        if not positive:
            # 방어 1 — 무관한 질문은 모델에 도달시키지 않는다. 판정/문구 모두
            # 템플릿 생성기에 위임해 두 provider의 "기록에 없음" 동작을 일치시킨다.
            return self._fallback.generate(question, all_evidence)

        selected = positive[: self._max_evidence]
        citations = tuple(citation_from_chunk(e.chunk) for e in selected)

        try:
            raw = self._call_gemini(build_prompt(question, selected))
        except Exception as exc:  # noqa: BLE001 - 어떤 실패든 답을 못 주는 것보단 템플릿이 낫다
            print(
                f"[경고] Gemini 답변 생성 호출이 실패해 템플릿 답변으로 대체합니다: {_summarize_error(exc)}",
                file=sys.stderr,
            )
            return self._fallback.generate(question, all_evidence)

        sentinel, body = split_sentinel(raw)

        if sentinel == _UNGROUNDED_SENTINEL:
            # 근거는 검색됐지만 답을 확정할 수 없다 → 지어내지 말고 fallback
            # (영상 재조회)으로 넘긴다. grounded=False가 그 스위치다.
            #
            # 이 분기는 "형식 불명확(sentinel is None or not body)" 검사보다
            # **먼저** 와야 한다: 모델이 프롬프트를 어기고 `[근거부족]`만 뱉고
            # 설명 문장을 안 쓰면(body가 빈 문자열) 그건 여전히 "확정 불가" 신호다.
            # 뒤에 두면 `not body`가 먼저 걸려 템플릿(grounded=True)으로 떨어지고,
            # 모델이 "답 못 함"이라 판단한 근거를 짜깁기한 답이 fallback 없이 나가
            # "틀린 답은 답 없는 것보다 나쁘다" 원칙이 뒤집힌다. body가 비어도
            # NO_ANSWER_IN_EVIDENCE_TEXT가 있어 사용자 문구는 비지 않는다.
            return AnswerResult(
                text=f"{NO_ANSWER_IN_EVIDENCE_TEXT} {body}".strip(),
                citations=citations,
                grounded=False,
                evidence=all_evidence,
                body=body,
            )

        if sentinel is None or not body:
            # 방어 2 — 형식이 불명확하면 모델 문장을 신뢰하지 않고 안전한 템플릿
            # 답변으로 되돌린다(근거 문장을 그대로 인용하므로 지어낼 수 없다).
            print(
                "[경고] Gemini 답변이 지정한 형식([답변]/[근거부족])을 따르지 않아 "
                "템플릿 답변으로 대체합니다.",
                file=sys.stderr,
            )
            return self._fallback.generate(question, all_evidence)

        # 방어 3 — 인용 문구는 모델 출력이 아니라 실제 Chunk에서 만든다.
        text = f"{body} (근거: {_citation_note(citations)})"
        return AnswerResult(text=text, citations=citations, grounded=True, evidence=all_evidence, body=body)
