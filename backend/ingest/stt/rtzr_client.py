"""리턴제로(RTZR) VITO API 실제 클라이언트 (배치/파일 전사 모드).

STT 1순위(기술조사_의사결정.md 조사 2). ingest 파이프라인은 언제나 완성된
WAV 파일 하나를 전사하므로(스트리밍이 아니라 배치) VITO의 파일 업로드 +
폴링 방식 엔드포인트를 사용한다.

API 개요 (https://developers.rtzr.ai/docs/en/):
    1. 인증: POST /v1/authenticate (client_id/client_secret) → access_token
       (6시간 유효 — ingest 1회 실행 동안은 재발급 로직이 불필요해 매 실행마다
       새로 발급받는다. 세션 간 캐싱은 하지 않는다)
    2. 제출: POST /v1/transcribe (multipart: file + config JSON) → transcribe id
    3. 폴링: GET /v1/transcribe/{id} → status가 "completed"/"failed"가 될 때까지
       약 5초 간격으로 조회 (너무 자주 조회하면 429가 날 수 있다)
    4. 완료 시 results.utterances가 화자분리 포함 발화 목록

인증 정보는 `backend/.env`의 RTZR_CLIENT_ID/RTZR_CLIENT_SECRET에서 읽는다.
`.env`를 실제로 로드(`python-dotenv`)하는 책임은 이 모듈이 아니라 CLI
진입점(`ingest/cli.py`)에 있다 — 이 클라이언트는 `os.environ`만 읽으므로
`monkeypatch.setenv`만으로 테스트 가능하다.

이 클라이언트는 credential이 없거나 잘못됐을 때, 그리고 API 자체가
실패했을 때 명확한 한국어 메시지로 실패한다(raw traceback 노출 금지 —
`cli.py`의 기존 에러 처리 관례를 따름). 절대 credential 값 자체를
에러 메시지/로그에 포함하지 않는다.

제출/폴링 요청이 429(Too Many Requests)나 5xx(서버 오류) 응답을 받으면
`_send_with_retry`가 짧은 backoff로 몇 번 재시도한 뒤에야 포기한다 — 그래도
계속 실패하면 `RTZRAPIError`를 그대로 던지며, 이 실패를 어떻게 다룰지(예:
스텁 전사록으로 대체)는 이 클라이언트가 아니라 호출부(`ingest/pipeline.py`)의
책임이다.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from .base import Transcript, TranscriptSegment

AUTH_URL = "https://openapi.vito.ai/v1/authenticate"
TRANSCRIBE_URL = "https://openapi.vito.ai/v1/transcribe"
TRANSCRIBE_STATUS_URL_TMPL = "https://openapi.vito.ai/v1/transcribe/{transcribe_id}"

DEFAULT_MODEL_NAME = "sommers"
DEFAULT_LANGUAGE = "ko"
DEFAULT_DOMAIN = "GENERAL"

DEFAULT_POLL_INTERVAL_SEC = 5.0
DEFAULT_POLL_TIMEOUT_SEC = 600.0  # 10분: 실제 오디오 길이 대비 충분히 여유있게 잡은 상한
DEFAULT_REQUEST_TIMEOUT_SEC = 30.0  # 인증/폴링처럼 응답 본문이 작은 요청에만 적용
# 멀티파트 업로드(오디오 파일 전송)는 회의실 wifi/핫스팟에서 수 분짜리 WAV를
# 올릴 수도 있으므로 인증/폴링과 분리된 더 긴 타임아웃을 둔다.
DEFAULT_UPLOAD_TIMEOUT_SEC = 120.0

_ENV_KEY_CLIENT_ID = "RTZR_CLIENT_ID"
_ENV_KEY_CLIENT_SECRET = "RTZR_CLIENT_SECRET"

_MAX_ERROR_BODY_CHARS = 300

# 업로드 파일의 실제 이름과 무관하게 멀티파트 `filename=`에는 이 고정 ASCII
# 값을 쓴다. httpx는 non-ASCII(한글) 파일명을 RFC 2231로 인코딩해주지 않고,
# RTZR 실제 멀티파트 파서가 한글 파일명을 어떻게 처리하는지도 검증되지
# 않았다. 파일명 자체는 API 입장에서 의미가 없고(오디오 바이트와
# content-type만 중요) 세션 녹음 파일명은 한글일 가능성이 높으므로 방어적으로
# 고정한다.
_UPLOAD_FILENAME = "audio.wav"

# 429(Too Many Requests)/5xx(서버 오류)에 한해 짧게 재시도한다. 그 외 4xx는
# 요청 자체의 문제(인증 실패 등)이므로 재시도해도 결과가 바뀌지 않는다.
_MAX_ATTEMPTS_PER_CALL = 3
_RETRY_BACKOFF_BASE_SEC = 1.0


class RTZRCredentialError(RuntimeError):
    """RTZR 인증 정보가 없거나(.env 미설정) 잘못됐을 때(401)."""


class RTZRAPIError(RuntimeError):
    """인증 이후 RTZR API 호출(전사 제출/폴링)이 실패했을 때 (네트워크·서버 오류 포함)."""


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) > _MAX_ERROR_BODY_CHARS:
        return text[:_MAX_ERROR_BODY_CHARS] + "…"
    return text


class RTZRClient:
    """RTZR VITO 배치(파일) STT API 클라이언트. 화자분리 포함 전사록을 만든다.

    `SpeechToTextClient` Protocol(`stt/base.py`)을 만족하므로
    `ingest.stt.factory.get_stt_client("rtzr")`가 그대로 주입할 수 있다.
    """

    provider_name = "rtzr"

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        http_client: httpx.Client | None = None,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        poll_timeout_sec: float = DEFAULT_POLL_TIMEOUT_SEC,
        upload_timeout_sec: float = DEFAULT_UPLOAD_TIMEOUT_SEC,
        sleep_fn: Callable[[float], None] = time.sleep,
        time_fn: Callable[[], float] = time.monotonic,
        spk_count: int | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """
        Args:
            client_id / client_secret: 명시적으로 넘기면 환경변수보다 우선한다
                (테스트/수동 스모크 테스트 용도). 생략 시 `env`(기본 `os.environ`)의
                RTZR_CLIENT_ID/RTZR_CLIENT_SECRET을 읽는다.
            http_client: 주입 가능한 httpx.Client (테스트에서 MockTransport로 교체).
                생략 시 내부에서 새로 만들고, 이 인스턴스가 소유권을 가져 `close()`/
                컨텍스트 매니저 종료 시 함께 닫는다.
            poll_interval_sec / poll_timeout_sec: 폴링 간격/전체 대기 상한(초).
            upload_timeout_sec: 오디오 파일 업로드(멀티파트 전송) 전용 타임아웃(초).
                인증/폴링 요청은 본문이 작아 `http_client`의 기본 타임아웃
                (`DEFAULT_REQUEST_TIMEOUT_SEC`)을 그대로 쓰고, 업로드만 더 길게 잡는다.
            sleep_fn / time_fn: 테스트에서 실제로 기다리지 않도록 주입 가능.
            env: 환경변수 딕셔너리(기본 `os.environ`). 테스트 결정성을 위해 주입 가능.
        """
        import os

        source_env = os.environ if env is None else env
        self._client_id = client_id or source_env.get(_ENV_KEY_CLIENT_ID)
        self._client_secret = client_secret or source_env.get(_ENV_KEY_CLIENT_SECRET)
        if not self._client_id or not self._client_secret:
            raise RTZRCredentialError(
                "RTZR API 인증 정보가 없습니다. backend/.env에 "
                f"{_ENV_KEY_CLIENT_ID}와 {_ENV_KEY_CLIENT_SECRET}을(를) 설정했는지 "
                "확인하세요 (.env.example 참고)."
            )

        self._http = http_client or httpx.Client(timeout=DEFAULT_REQUEST_TIMEOUT_SEC)
        self._owns_http = http_client is None
        self._poll_interval_sec = poll_interval_sec
        self._poll_timeout_sec = poll_timeout_sec
        self._upload_timeout_sec = upload_timeout_sec
        self._sleep_fn = sleep_fn
        self._time_fn = time_fn
        # 화자 수. 0 = 자동 추정(불안정, 과분할 경향). 아는 경우 명시하면 안정된다.
        # 명시 인자 > RTZR_SPK_COUNT 환경변수 > 0(자동).
        if spk_count is not None:
            self._spk_count = spk_count
        else:
            try:
                self._spk_count = int(source_env.get("RTZR_SPK_COUNT", "0") or "0")
            except ValueError:
                self._spk_count = 0

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> RTZRClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- 내부 단계들 ---------------------------------------------------

    def _send_with_retry(self, send: Callable[[], httpx.Response]) -> httpx.Response:
        """429/5xx 응답에 한해 최대 `_MAX_ATTEMPTS_PER_CALL`번까지 짧게 재시도한다.

        `send`는 매 시도마다 새로 호출되는 무인자 콜백이다(재시도 시 파일
        포인터를 되감는 등 시도별 준비 작업은 `send` 내부에서 처리).
        네트워크 예외(`httpx.RequestError`)는 여기서 삼키지 않고 그대로
        전파한다 — 재시도 대상은 스펙상 429/5xx 응답으로 한정한다.
        """
        response = send()
        attempt = 1
        while (response.status_code == 429 or response.status_code >= 500) and (
            attempt < _MAX_ATTEMPTS_PER_CALL
        ):
            self._sleep_fn(_RETRY_BACKOFF_BASE_SEC * attempt)
            response = send()
            attempt += 1
        return response

    def _authenticate(self) -> str:
        try:
            response = self._http.post(
                AUTH_URL,
                data={"client_id": self._client_id, "client_secret": self._client_secret},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.RequestError as exc:
            raise RTZRAPIError(f"RTZR 인증 서버에 연결하지 못했습니다 (네트워크 오류): {exc}") from exc

        if response.status_code == 401:
            raise RTZRCredentialError(
                "RTZR API 인증에 실패했습니다 (401 Unauthorized). backend/.env의 "
                f"{_ENV_KEY_CLIENT_ID}/{_ENV_KEY_CLIENT_SECRET} 값이 올바른지 확인하세요."
            )
        if response.status_code != 200:
            raise RTZRAPIError(
                f"RTZR 인증 요청이 실패했습니다 (HTTP {response.status_code}): {_truncate(response.text)}"
            )

        try:
            payload = response.json()
            token = payload["access_token"]
        except (ValueError, KeyError) as exc:
            raise RTZRAPIError(
                f"RTZR 인증 응답 형식을 해석할 수 없습니다: {_truncate(response.text)}"
            ) from exc
        if not isinstance(token, str) or not token:
            raise RTZRAPIError("RTZR 인증 응답에 access_token이 비어 있습니다.")
        return token

    def _submit_transcription(self, token: str, audio_path: Path) -> str:
        config = {
            "model_name": DEFAULT_MODEL_NAME,
            "language": DEFAULT_LANGUAGE,
            "use_diarization": True,
            "diarization": {"spk_count": self._spk_count},  # 0 = 자동 추정, N = N명으로 고정
            "domain": DEFAULT_DOMAIN,
        }
        try:
            with audio_path.open("rb") as audio_file:

                def _send() -> httpx.Response:
                    # 재시도 시 이전 시도에서 끝까지 읽어 소진된 파일 포인터를
                    # 되감아야 두 번째 시도가 빈 본문을 보내지 않는다.
                    audio_file.seek(0)
                    return self._http.post(
                        TRANSCRIBE_URL,
                        headers={"Authorization": f"Bearer {token}"},
                        files={"file": (_UPLOAD_FILENAME, audio_file, "audio/wav")},
                        data={"config": json.dumps(config, ensure_ascii=False)},
                        timeout=self._upload_timeout_sec,
                    )

                response = self._send_with_retry(_send)
        except httpx.RequestError as exc:
            raise RTZRAPIError(f"RTZR 전사 요청 중 네트워크 오류가 발생했습니다: {exc}") from exc

        if response.status_code == 401:
            raise RTZRCredentialError("RTZR API 인증이 만료되었거나 유효하지 않습니다 (401 Unauthorized).")
        if response.status_code >= 400:
            raise RTZRAPIError(
                f"RTZR 전사 요청이 실패했습니다 (HTTP {response.status_code}): {_truncate(response.text)}"
            )

        try:
            transcribe_id = response.json()["id"]
        except (ValueError, KeyError) as exc:
            raise RTZRAPIError(
                f"RTZR 전사 요청 응답 형식을 해석할 수 없습니다: {_truncate(response.text)}"
            ) from exc
        return transcribe_id

    def _poll_until_done(self, token: str, transcribe_id: str) -> dict[str, Any]:
        url = TRANSCRIBE_STATUS_URL_TMPL.format(transcribe_id=transcribe_id)
        deadline = self._time_fn() + self._poll_timeout_sec

        while True:
            try:
                response = self._send_with_retry(
                    lambda: self._http.get(url, headers={"Authorization": f"Bearer {token}"})
                )
            except httpx.RequestError as exc:
                raise RTZRAPIError(f"RTZR 전사 결과 조회 중 네트워크 오류가 발생했습니다: {exc}") from exc

            if response.status_code != 200:
                raise RTZRAPIError(
                    "RTZR 전사 결과 조회가 실패했습니다 "
                    f"(HTTP {response.status_code}): {_truncate(response.text)}"
                )

            try:
                payload: dict[str, Any] = response.json()
                status = payload["status"]
            except (ValueError, KeyError) as exc:
                raise RTZRAPIError(
                    f"RTZR 전사 상태 응답 형식을 해석할 수 없습니다: {_truncate(response.text)}"
                ) from exc

            if status == "completed":
                return payload
            if status == "failed":
                error = payload.get("error") or {}
                code = error.get("code", "알 수 없음")
                message = error.get("message", "메시지 없음")
                raise RTZRAPIError(f"RTZR 전사가 실패했습니다 (code={code}): {message}")

            if self._time_fn() >= deadline:
                raise RTZRAPIError(
                    f"RTZR 전사 결과 대기 시간이 초과됐습니다 ({self._poll_timeout_sec:.0f}초). "
                    "오디오가 너무 길거나 RTZR 서버가 지연되고 있을 수 있습니다."
                )
            # 폴링은 최대 10분까지 걸릴 수 있어(DEFAULT_POLL_TIMEOUT_SEC) 아무
            # 출력도 없으면 데모 중 멈춘 것처럼 보인다 — 매 회 짧게 진행 상황을 알린다.
            print("[진행] RTZR 전사 대기 중...", file=sys.stderr)
            self._sleep_fn(self._poll_interval_sec)

    @staticmethod
    def _parse_utterances(payload: dict[str, Any]) -> list[TranscriptSegment]:
        utterances = payload.get("results", {}).get("utterances", [])
        segments: list[TranscriptSegment] = []
        for utterance in utterances:
            start_at_ms = utterance["start_at"]
            duration_ms = utterance["duration"]
            speaker_idx = utterance.get("spk", 0)
            segments.append(
                TranscriptSegment(
                    start_sec=start_at_ms / 1000,
                    end_sec=(start_at_ms + duration_ms) / 1000,
                    speaker=f"화자{speaker_idx + 1}",
                    text=utterance["msg"],
                )
            )
        return segments

    # -- 공개 인터페이스 -----------------------------------------------

    def transcribe(self, audio_path: Path) -> Transcript:
        """오디오 파일(WAV)을 RTZR VITO API로 전사한다 (화자분리 포함).

        Raises:
            FileNotFoundError: 오디오 파일이 없을 때.
            RTZRCredentialError: 인증 정보 누락/오류.
            RTZRAPIError: 전사 제출/폴링 중 API 또는 네트워크 오류, 혹은
                RTZR이 전사 자체를 실패(status="failed")로 반환했을 때.
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"오디오 파일이 존재하지 않습니다: {audio_path}")

        token = self._authenticate()
        transcribe_id = self._submit_transcription(token, audio_path)
        payload = self._poll_until_done(token, transcribe_id)
        segments = self._parse_utterances(payload)
        return Transcript(segments=segments, provider=self.provider_name)
