"""Gemini 실제 VLM 캡션 + LLM 요약 클라이언트.

기술조사_의사결정.md 조사4(2026-07-26 갱신) — "VLM 캡션·LLM 요약도 Gemini로
통일 확정". fallback 영상 재조회(recall-dev 담당, `recall/fallback/trigger.py`)는
건드리지 않는다.

## 실제 API 스키마 확인 결과 (작업 지시에 있던 스펙과의 차이)

작업 지시에는 `client.interactions.create(model=..., input=[...])` /
`interaction.output_text` 형태의 스펙이 주어졌지만, 실제로
`pip install google-genai`(2.14.0, 이 프로젝트에 설치된 버전)를 설치해
타입힌트/소스를 직접 열어 확인한 결과 그 스펙은 존재하지 않았다
(`google.genai.Client`에 `interactions` 애트리뷰트 자체는 있지만 내부적으로
`GeminiNextGenInteractions`라는 별도 실험적 API로, 문서화된 표준 경로가
아니다). 실제로 쓰이는, 안정적이고 문서화된 경로는 다음과 같다:

    from google import genai
    from google.genai import types

    client = genai.Client()  # GEMINI_API_KEY(또는 GOOGLE_API_KEY) 환경변수를 자동으로 읽음
    response = client.models.generate_content(
        model="gemini-flash-latest",
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            "이 장면을 설명해줘",
        ],
        config=types.GenerateContentConfig(
            system_instruction="...",
            temperature=0.3,
            max_output_tokens=300,
        ),
    )
    response.text  # 결과 텍스트 (Optional[str] — 후보가 비면 None)

`genai.Client()`를 인자 없이 만들면 `GEMINI_API_KEY`를 읽는다는 부분은 작업
지시대로 맞았다(다만 `GOOGLE_API_KEY`가 함께 설정돼 있으면 그쪽이 우선한다 —
`google.genai._api_client.get_env_api_key` 참고. 이 프로젝트는 `GEMINI_API_KEY`
하나만 쓰므로 무관).

에러 계층(`google.genai.errors`): 4xx 응답은 `ClientError`, 5xx 응답은
`ServerError`, 둘 다 `APIError`의 서브클래스이며 `.code`/`.message` 속성을
갖는다. 네트워크 자체가 끊긴 경우(연결 실패 등)는 SDK가 감싸지 않고
`httpx.RequestError`(RTZR 클라이언트가 처리하는 것과 같은 계열)가 그대로
올라온다 — 실제로 `httpx.MockTransport`로 두 경우 모두 재현해 확인했다.

## RTZR과 동일한 두 단계 폴백 원칙 (`stt/factory.py`/`stt/rtzr_client.py` 참고)

    1. 생성 시점 폴백(`factory.py`의 책임): GEMINI_API_KEY가 아예 없으면 실제
       클라이언트를 만들지 않고 곧바로 플레이스홀더(`stub.py`)로 대체한다.
    2. 실행 시점 폴백(`ingest/pipeline.py`의 책임, 여기서는 하지 않음): 인증
       정보는 있어서 실제 클라이언트가 만들어졌지만 호출 자체가 실패하면
       (`GeminiAPIError` — 네트워크 오류, 429/5xx, 응답이 비어 있음 등) 그
       세션만 플레이스홀더로 대체해 이어간다.

`GeminiCredentialError`(API 키가 없거나 명백히 잘못된 인증 문제 — 401/403,
혹은 SDK가 값 자체를 거부하는 400)는 설정 문제이므로 실행 시점에는 절대
삼키지 않고 그대로 전파한다(RTZRCredentialError와 동일한 원칙).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from ..stt.base import Transcript
from ..visual import ProcessedKeyframe
from . import prompts
from .base import CaptionItem

_ENV_KEY_API_KEY = "GEMINI_API_KEY"

# 기술조사_의사결정.md 조사4가 인용하는 모델("Gemini 2.5 Flash($0.30/M 입력)")을
# [2026-07-26] 실제 API 키로 검증: "gemini-2.5-flash"는 client.models.list()에는
# 나오지만 신규 사용자에게는 404("no longer available to new users")를 반환한다.
# "gemini-flash-latest"(항상 현재 권장 flash 모델을 가리키는 별칭)로 실제
# generate_content 호출까지 확인됨 — 모델 세대가 바뀌어도 이 상수를 계속
# 갱신할 필요가 없어 이걸 기본값으로 쓴다. `GEMINI_MODEL` 환경변수로 특정
# 버전에 고정하고 싶으면 언제든 덮어쓸 수 있다.
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

_TEMPERATURE = 0.3  # 사실 기반 서술이 목적이라 창작적 다양성보다 일관성을 우선한다.
_MAX_OUTPUT_TOKENS_CAPTION = 300
_MAX_OUTPUT_TOKENS_SUMMARY = 800


class GeminiCredentialError(RuntimeError):
    """GEMINI_API_KEY가 없거나(.env 미설정) 명백히 잘못됐을 때(400/401/403)."""


class GeminiAPIError(RuntimeError):
    """인증 이후 Gemini API 호출(캡션/요약 생성)이 실패했을 때 (네트워크·서버 오류 포함)."""


def _resolve_api_key(api_key: str | None, env: dict[str, str] | None) -> str:
    source_env = os.environ if env is None else env
    resolved = api_key or source_env.get(_ENV_KEY_API_KEY)
    if not resolved:
        raise GeminiCredentialError(
            "Gemini API 인증 정보가 없습니다. backend/.env에 "
            f"{_ENV_KEY_API_KEY}를 설정했는지 확인하세요 (.env.example 참고)."
        )
    return resolved


def _build_client(*, api_key: str | None, env: dict[str, str] | None) -> genai.Client:
    resolved_key = _resolve_api_key(api_key, env)
    try:
        return genai.Client(api_key=resolved_key)
    except ValueError as exc:
        # genai.Client()가 방어적으로 같은 실패를 낼 수 있는 경우(예: 공백 문자열).
        raise GeminiCredentialError(f"Gemini API 클라이언트 생성에 실패했습니다: {exc}") from exc


def _generate_text(
    client: genai.Client,
    *,
    model: str,
    contents: list[Any],
    system_instruction: str,
    max_output_tokens: int,
) -> str:
    """공통 생성 호출 + 에러 번역(캡션/요약 둘 다 이 헬퍼를 거친다)."""
    try:
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=genai_types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=_TEMPERATURE,
                max_output_tokens=max_output_tokens,
            ),
        )
    except genai_errors.ClientError as exc:
        # 4xx: 잘못되거나 만료된 API 키(401/403), 잘못된 요청(400) 등 — 재시도해도
        # 결과가 바뀌지 않는 설정 문제이므로 credential 에러로 승격한다.
        raise GeminiCredentialError(
            f"Gemini API 인증/요청 오류입니다 (HTTP {exc.code}): {exc.message}"
        ) from exc
    except genai_errors.APIError as exc:
        # 5xx 등 서버측 오류 — 일시적 장애로 보고 호출부가 스텁으로 대체할 수 있게 한다.
        raise GeminiAPIError(f"Gemini API 호출이 실패했습니다 (HTTP {exc.code}): {exc.message}") from exc
    except httpx.RequestError as exc:
        raise GeminiAPIError(f"Gemini API 호출 중 네트워크 오류가 발생했습니다: {exc}") from exc

    text = response.text
    if not text or not text.strip():
        raise GeminiAPIError("Gemini API 응답에 텍스트가 비어 있습니다(빈 후보 또는 안전 필터 차단).")
    return text.strip()


class GeminiVLMCaptioner:
    """`VLMCaptioner` Protocol(`base.py`)을 만족하는 실제 Gemini 구현체."""

    provider_name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        client: genai.Client | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """
        Args:
            api_key: 명시적으로 넘기면 환경변수보다 우선한다(수동 스모크 테스트 용도).
            model: 사용할 Gemini 모델 이름.
            client: 주입 가능한 `genai.Client` (테스트에서
                `types.HttpOptions(httpx_client=...)`로 구성한 가짜 클라이언트를
                넣어 네트워크 없이 검증). 생략 시 `api_key`/`env`로 새로 만든다.
            env: 환경변수 딕셔너리(기본 `os.environ`). 테스트 결정성을 위해 주입 가능.
        """
        self._client = client if client is not None else _build_client(api_key=api_key, env=env)
        self._model = model

    def caption_keyframes(
        self,
        keyframes: Sequence[ProcessedKeyframe],
        transcript: Transcript,
        *,
        media_slug: str,
    ) -> list[CaptionItem]:
        del media_slug  # Protocol 시그니처 유지용 — 실제 캡션 텍스트에는 이미지 경로를 넣지 않는다.
        results: list[CaptionItem] = []
        for keyframe in keyframes:
            context = prompts.build_caption_context(transcript, keyframe.timestamp_sec)
            image_bytes = Path(keyframe.image_path).read_bytes()
            contents: list[Any] = [
                genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                prompts.build_caption_prompt(context),
            ]
            caption_text = _generate_text(
                self._client,
                model=self._model,
                contents=contents,
                system_instruction=prompts.CAPTION_SYSTEM_INSTRUCTION,
                max_output_tokens=_MAX_OUTPUT_TOKENS_CAPTION,
            )
            results.append((keyframe.timestamp_sec, keyframe.timestamp_sec, caption_text))
        return results


class GeminiLLMSummarizer:
    """`LLMSummarizer` Protocol(`base.py`)을 만족하는 실제 Gemini 구현체."""

    provider_name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        client: genai.Client | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._client = client if client is not None else _build_client(api_key=api_key, env=env)
        self._model = model

    def summarize_session(
        self,
        transcript: Transcript,
        captions: Sequence[CaptionItem],
        participants: Sequence[str],
    ) -> str | None:
        if not transcript.segments:
            return None  # 전사록이 비어 있으면 요약할 내용이 없다 — TODO 플레이스홀더에 위임.

        prompt = prompts.build_summary_prompt(transcript, captions, participants)
        return _generate_text(
            self._client,
            model=self._model,
            contents=[prompt],
            system_instruction=prompts.SUMMARY_SYSTEM_INSTRUCTION,
            max_output_tokens=_MAX_OUTPUT_TOKENS_SUMMARY,
        )
