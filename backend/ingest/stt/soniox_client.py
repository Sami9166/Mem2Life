"""Soniox 비동기(파일) 전사 클라이언트 (`SpeechToTextClient` 구현체).

RTZR/Gemini의 대안. 전용 STT 서비스라 "들린 그대로"에 충실하고 단어 단위
타임스탬프 + 화자분리를 함께 준다. ingest 파이프라인은 완성된 WAV 하나를
전사하므로(스트리밍 아님) 비동기 파일 API를 쓴다.

API 흐름 (https://soniox.com/docs/stt/async/async-transcription):
    1. 업로드: POST /v1/files (multipart: file) → { id }
    2. 제출:   POST /v1/transcriptions (JSON: model, file_id, language_hints,
               enable_speaker_diarization) → { id }
    3. 폴링:   GET /v1/transcriptions/{id} → status가 "completed"/"error"까지
    4. 결과:   GET /v1/transcriptions/{id}/transcript → { text, tokens[] }
               tokens[i] = { text, start_ms, end_ms, confidence, speaker, language }

토큰은 단어/서브워드 단위라, 연속된 같은 speaker 토큰을 하나의 발화
세그먼트로 묶는다(과분할 방지). speaker는 정수(1,2,...) → "화자{n}"으로 매핑.

인증: `Authorization: Bearer $SONIOX_API_KEY`. `.env` 로드 책임은 이 모듈이
아니라 CLI 진입점(글루/`cli.py`)에 있다 — 여기서는 `os.environ`만 읽는다.
API 키 값 자체는 절대 에러/로그에 넣지 않는다.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from .base import Transcript, TranscriptSegment

BASE_URL = "https://api.soniox.com"
FILES_URL = f"{BASE_URL}/v1/files"
TRANSCRIPTIONS_URL = f"{BASE_URL}/v1/transcriptions"

DEFAULT_MODEL = "stt-async-v5"
DEFAULT_LANGUAGE_HINTS = ("ko", "en")
_ENV_KEY = "SONIOX_API_KEY"

DEFAULT_POLL_INTERVAL_SEC = 2.0
DEFAULT_POLL_TIMEOUT_SEC = 600.0  # 10분 상한
DEFAULT_REQUEST_TIMEOUT_SEC = 30.0
DEFAULT_UPLOAD_TIMEOUT_SEC = 120.0

# 멀티파트 filename에는 고정 ASCII 값을 쓴다(세션 파일명이 한글일 수 있고, 파일명은
# API 입장에서 의미가 없다 — 오디오 바이트/content-type만 중요).
_UPLOAD_FILENAME = "audio.wav"
_MAX_ERROR_BODY_CHARS = 300


class SonioxCredentialError(RuntimeError):
    """SONIOX_API_KEY가 없을 때."""


class SonioxAPIError(RuntimeError):
    """Soniox API 호출이 실패했을 때(HTTP 오류, 전사 status=error, 폴링 타임아웃)."""


def _speaker_label(raw: object) -> str:
    """Soniox speaker(정수/문자열/None)를 '화자N' 라벨로 정규화한다."""
    if raw is None or raw == "":
        return "화자1"
    return f"화자{raw}"


def parse_tokens_to_transcript(
    tokens: Sequence[dict[str, Any]], *, provider: str = "soniox"
) -> Transcript:
    """토큰 배열을 화자별로 묶어 `Transcript`로 변환한다(순수 함수 — 네트워크 없음).

    연속된 같은 speaker 토큰을 하나의 발화로 병합한다. 화자가 바뀌면 새 발화를
    시작한다. 타임스탬프는 ms → 초로 변환하고, 발화 text는 토큰 text를 이어붙인다.
    """
    segments: list[TranscriptSegment] = []
    cur_speaker: str | None = None
    cur_text: list[str] = []
    cur_start = 0.0
    cur_end = 0.0

    def flush() -> None:
        if cur_speaker is None:
            return
        text = "".join(cur_text).strip()
        if text:
            segments.append(
                TranscriptSegment(
                    start_sec=cur_start,
                    end_sec=max(cur_end, cur_start),
                    speaker=cur_speaker,
                    text=text,
                )
            )

    for tok in tokens:
        text = str(tok.get("text", ""))
        if text == "":
            continue
        speaker = _speaker_label(tok.get("speaker"))
        start = float(tok.get("start_ms", 0) or 0) / 1000.0
        end = float(tok.get("end_ms", tok.get("start_ms", 0)) or 0) / 1000.0
        if speaker != cur_speaker:
            flush()
            cur_speaker = speaker
            cur_text = [text]
            cur_start = start
            cur_end = end
        else:
            cur_text.append(text)
            cur_end = max(cur_end, end)
    flush()
    return Transcript(segments=segments, provider=provider)


class SonioxSttClient:
    """`SpeechToTextClient`를 만족하는 Soniox 비동기 전사 구현체."""

    provider_name = "soniox"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        language_hints: Sequence[str] | None = None,
        enable_speaker_diarization: bool = True,
        http_client: httpx.Client | None = None,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        poll_timeout_sec: float = DEFAULT_POLL_TIMEOUT_SEC,
        sleep_fn: Any = time.sleep,
        time_fn: Any = time.monotonic,
        env: dict[str, str] | None = None,
    ) -> None:
        source_env = os.environ if env is None else env
        self._api_key = api_key or source_env.get(_ENV_KEY)
        if not self._api_key:
            raise SonioxCredentialError(
                "Soniox API 인증 정보가 없습니다. backend/.env에 SONIOX_API_KEY를 설정하세요."
            )
        # 모델/언어는 런타임에 해석(명시 인자 > env > 기본). import 시점 상수로 굳히면
        # CLI가 .env를 load_dotenv 하기 전 값이 박힌다(다른 provider와 동일한 순서 함정).
        self._model = model or source_env.get("SONIOX_MODEL") or DEFAULT_MODEL
        if language_hints is not None:
            self._language_hints = list(language_hints)
        else:
            raw = source_env.get("SONIOX_LANGUAGE_HINTS")
            self._language_hints = (
                [s.strip() for s in raw.split(",") if s.strip()] if raw else list(DEFAULT_LANGUAGE_HINTS)
            )
        self._diarization = enable_speaker_diarization
        self._http = http_client or httpx.Client(timeout=DEFAULT_REQUEST_TIMEOUT_SEC)
        self._owns_http = http_client is None
        self._poll_interval_sec = poll_interval_sec
        self._poll_timeout_sec = poll_timeout_sec
        self._sleep_fn = sleep_fn
        self._time_fn = time_fn

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> SonioxSttClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _raise_for_status(self, resp: httpx.Response, what: str) -> None:
        if resp.is_success:
            return
        body = resp.text[:_MAX_ERROR_BODY_CHARS]
        raise SonioxAPIError(f"Soniox {what} 실패: HTTP {resp.status_code} {body}")

    def _upload(self, audio_path: Path) -> str:
        with audio_path.open("rb") as fh:
            resp = self._http.post(
                FILES_URL,
                headers=self._auth_headers,
                files={"file": (_UPLOAD_FILENAME, fh, "audio/wav")},
                timeout=DEFAULT_UPLOAD_TIMEOUT_SEC,
            )
        self._raise_for_status(resp, "파일 업로드")
        file_id = resp.json().get("id")
        if not file_id:
            raise SonioxAPIError("Soniox 파일 업로드 응답에 id가 없습니다.")
        return str(file_id)

    def _create_transcription(self, file_id: str) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "file_id": file_id,
            "language_hints": self._language_hints,
            "enable_speaker_diarization": self._diarization,
        }
        resp = self._http.post(TRANSCRIPTIONS_URL, headers=self._auth_headers, json=payload)
        self._raise_for_status(resp, "전사 생성")
        transcription_id = resp.json().get("id")
        if not transcription_id:
            raise SonioxAPIError("Soniox 전사 생성 응답에 id가 없습니다.")
        return str(transcription_id)

    def _poll_until_done(self, transcription_id: str) -> None:
        status_url = f"{TRANSCRIPTIONS_URL}/{transcription_id}"
        deadline = self._time_fn() + self._poll_timeout_sec
        while True:
            resp = self._http.get(status_url, headers=self._auth_headers)
            self._raise_for_status(resp, "전사 상태 조회")
            data = resp.json()
            status = data.get("status")
            if status == "completed":
                return
            if status == "error":
                reason = data.get("error_message", "(사유 미제공)")
                raise SonioxAPIError(f"Soniox 전사 실패: {reason}")
            if self._time_fn() > deadline:
                raise SonioxAPIError(
                    f"Soniox 전사 폴링 타임아웃({self._poll_timeout_sec:.0f}s 초과, status={status})."
                )
            self._sleep_fn(self._poll_interval_sec)

    def _fetch_transcript(self, transcription_id: str) -> list[dict[str, Any]]:
        url = f"{TRANSCRIPTIONS_URL}/{transcription_id}/transcript"
        resp = self._http.get(url, headers=self._auth_headers)
        self._raise_for_status(resp, "전사 결과 조회")
        tokens = resp.json().get("tokens")
        if not isinstance(tokens, list):
            raise SonioxAPIError("Soniox 전사 결과에 tokens 배열이 없습니다.")
        return tokens

    def transcribe(self, audio_path: Path) -> Transcript:
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"오디오 파일이 존재하지 않습니다: {audio_path}")
        file_id = self._upload(audio_path)
        transcription_id = self._create_transcription(file_id)
        self._poll_until_done(transcription_id)
        tokens = self._fetch_transcript(transcription_id)
        return parse_tokens_to_transcript(tokens, provider=self.provider_name)
