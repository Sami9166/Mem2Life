"""Gemini 오디오 전사 클라이언트 (`SpeechToTextClient` 구현체).

RTZR(음향 기반)의 대안. 오디오 트랙을 Gemini에 보내 전사 + 화자 라벨 +
타임스탬프를 한 번에 받는다. 같은 세션 실측 비교 결과(5b04dea):

  - RTZR       : 실제 대화를 잡지만 잡음 많고 턴 배분이 일부 어긋남.
  - Gemini(오디오): RTZR보다 깔끔하고 화자분리(2명)·턴 배분 정확.
  - Gemini(영상) : 화면에 맞춘 가짜 대사를 지어냄(할루시네이션) → 전사에 부적합.

그래서 이 클라이언트는 **오디오만** 보낸다(영상 Part 금지). 전사는 사실이어야
하므로 시각 정보로 오염시키지 않는다.

폴백은 다른 provider와 동일한 2단계다: GEMINI_API_KEY가 없으면 factory가
스텁(더미 전사록)을 반환하고, 키는 있는데 호출/파싱이 실패하면 예외를 던진다
(호출부가 세션별로 스텁 대체 가능).

thinking 모델(gemini-flash-latest)은 내부 사고에 출력 토큰을 먼저 소진하므로
출력 상한을 넉넉히(8192) 둔다 — 상한이 낮으면 JSON을 뱉기 전에 잘린다(실측).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .base import Transcript, TranscriptSegment

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
_ENV_KEYS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
_MAX_OUTPUT_TOKENS = 8192  # thinking 토큰이 출력을 잠식해도 JSON이 들어가도록 넉넉히.
_TEMPERATURE = 0.0  # 충실한 전사가 목적 — 창의성 불필요.
_FILE_ACTIVE_TIMEOUT_SEC = 60.0
_FILE_POLL_INTERVAL_SEC = 1.0
_LAST_SEGMENT_TAIL_SEC = 3.0  # 마지막 발화 종료 시각 근사(다음 발화가 없을 때).

SYSTEM_INSTRUCTION = (
    "당신은 한국어 음성을 축자(verbatim) 전사하는 받아쓰기 도구입니다. "
    "생성 모델이 아니라 '들린 소리를 그대로 옮기는 기계'처럼 동작하세요.\n"
    "필수 규칙:\n"
    "- 오디오에서 **실제로 들리는 소리 그대로** 적으세요. 의미가 안 통하거나 "
    "문법이 어색해도 **들린 대로** 두세요.\n"
    "- **문맥·상식·개연성으로 단어를 추측하거나 자연스럽게 고치지 마세요.** "
    "더 그럴듯한 말로 바꾸는 것은 오류입니다(예: 애매하게 들린 소리를 흔한 "
    "단어로 '보정'하지 말 것).\n"
    "- 더듬음, 말끝 흐림, 필러(어, 음, 그, 저)를 **그대로 포함**하세요.\n"
    "- 소리가 불분명해 확신할 수 없으면 그 부분을 **[불분명]**으로 표기하고 "
    "**절대 지어내지 마세요**. 없는 말을 채우는 것보다 [불분명]이 낫습니다.\n"
    "- 화자가 바뀌면 화자1, 화자2 같은 익명 라벨로 구분하되, 한 사람이 계속 "
    "말하면 같은 라벨을 유지하세요(과분할 금지)."
)


class GeminiSttCredentialError(RuntimeError):
    """GEMINI_API_KEY가 없거나 google-genai 패키지를 못 불러올 때."""


def _build_prompt(spk_count: int) -> str:
    hint = (
        f"화자는 정확히 {spk_count}명입니다. 그보다 많거나 적게 나누지 마세요.\n"
        if spk_count and spk_count > 0
        else ""
    )
    return (
        "이 오디오를 들린 그대로(축자) 전사하세요. 아래 JSON만 출력하고 "
        "마크다운·설명은 넣지 마세요.\n"
        f"{hint}"
        '{"transcript": [{"start": "MM:SS", "speaker": "화자1", "text": "..."}]}\n'
        "start는 발화 시작 시각(MM:SS 또는 HH:MM:SS). "
        "text는 들린 소리 그대로 — 문맥으로 추측·보정하지 말고, 불분명한 부분은 "
        "[불분명]으로 표기하세요."
    )


def _parse_timestamp(value: object) -> float:
    """"MM:SS"/"HH:MM:SS"/숫자(초)를 초 단위 float로 변환한다. 파싱 실패 시 0.0."""
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    if not isinstance(value, str):
        return 0.0
    text = value.strip()
    if not text:
        return 0.0
    if ":" in text:
        parts = text.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return 0.0
        seconds = 0.0
        for n in nums:  # 앞에서부터 시:분:초 누적 (2개면 분:초, 3개면 시:분:초)
            seconds = seconds * 60 + n
        return max(0.0, seconds)
    try:
        return max(0.0, float(text))
    except ValueError:
        return 0.0


def _strip_fences(text: str) -> str:
    return re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()


def _loads_lenient(text: str) -> object:
    """모델 JSON 출력을 관대하게 로드한다.

    깔끔한 JSON이면 그대로, 앞뒤 잡텍스트가 섞였으면 최상위 배열/객체를 추출한다.
    (flash-lite 등은 스키마를 무시하고 `[{...}]` 배열로만 주기도 한다.)
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for pattern in (r"\[.*\]", r"\{.*\}"):  # 배열 우선(바로 [..]로 주는 모델 대응)
        match = re.search(pattern, text, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
    raise ValueError("Gemini 전사 응답에서 JSON을 파싱하지 못했습니다.")


def parse_transcript_json(raw: str, *, provider: str = "gemini") -> Transcript:
    """모델 JSON 출력을 `Transcript`로 파싱한다(순수 함수 — 네트워크 없음).

    두 형식을 모두 받는다: `{"transcript": [...]}`(객체) 또는 `[...]`(바로 배열).
    끝 시각(end_sec)은 다음 발화 시작으로, 마지막 발화는 시작+여유(tail)로 근사한다.
    """
    data = _loads_lenient(_strip_fences(raw))
    if isinstance(data, list):
        items: object = data
    elif isinstance(data, dict):
        items = data.get("transcript")
    else:
        items = None
    if not isinstance(items, list):
        raise ValueError("Gemini 전사 응답에 발화 배열이 없습니다.")

    raw_segments: list[tuple[float, str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        start = _parse_timestamp(item.get("start"))
        speaker = str(item.get("speaker") or "화자1").strip() or "화자1"
        raw_segments.append((start, speaker, text))

    raw_segments.sort(key=lambda s: s[0])
    segments: list[TranscriptSegment] = []
    for i, (start, speaker, text) in enumerate(raw_segments):
        if i + 1 < len(raw_segments):
            end = max(start, raw_segments[i + 1][0])
        else:
            end = start + _LAST_SEGMENT_TAIL_SEC
        segments.append(
            TranscriptSegment(start_sec=start, end_sec=end, speaker=speaker, text=text)
        )
    return Transcript(segments=segments, provider=provider)


class GeminiSttClient:
    """`SpeechToTextClient`를 만족하는 Gemini 오디오 전사 구현체."""

    provider_name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        client: object | None = None,
        spk_count: int | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        source_env = os.environ if env is None else env
        # 모델은 런타임에 해석한다(명시 인자 > env의 GEMINI_MODEL > 기본). import 시점
        # 모듈 상수(DEFAULT_MODEL)로 고정하면 CLI가 .env를 load_dotenv 하기 전에 값이
        # 굳어져 .env의 GEMINI_MODEL이 무시된다(STT_PROVIDER와 동일한 순서 함정).
        self._model = model or source_env.get("GEMINI_MODEL") or DEFAULT_MODEL
        # 화자 수 힌트. 명시 인자 > STT_SPK_COUNT > RTZR_SPK_COUNT(기존 설정 재사용) > 0.
        if spk_count is not None:
            self._spk_count = spk_count
        else:
            raw = source_env.get("STT_SPK_COUNT") or source_env.get("RTZR_SPK_COUNT") or "0"
            try:
                self._spk_count = int(raw)
            except ValueError:
                self._spk_count = 0
        if client is not None:  # 테스트에서 가짜 클라이언트 주입.
            self._client = client
            return
        resolved = api_key or next((source_env[k] for k in _ENV_KEYS if source_env.get(k)), None)
        if not resolved:
            raise GeminiSttCredentialError(
                "Gemini API 인증 정보가 없습니다. backend/.env에 GEMINI_API_KEY를 설정하세요."
            )
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover
            raise GeminiSttCredentialError(
                "google-genai 패키지를 불러올 수 없습니다(uv sync 확인)."
            ) from exc
        self._client = genai.Client(api_key=resolved)

    def _upload_active(self, audio_path: Path) -> object:
        """오디오를 File API로 올리고 ACTIVE 상태가 될 때까지 대기한다."""
        file = self._client.files.upload(file=str(audio_path))
        deadline = time.monotonic() + _FILE_ACTIVE_TIMEOUT_SEC
        while "ACTIVE" not in str(getattr(file, "state", "")):
            if time.monotonic() > deadline:
                raise RuntimeError(f"업로드 파일이 ACTIVE 되지 않았습니다: state={getattr(file, 'state', '?')}")
            time.sleep(_FILE_POLL_INTERVAL_SEC)
            file = self._client.files.get(name=file.name)
        return file

    def _generate(self, file: object) -> str:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self._model,
            contents=[file, _build_prompt(self._spk_count)],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=_TEMPERATURE,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
                response_mime_type="application/json",
            ),
        )
        text = getattr(response, "text", None)
        if not text or not text.strip():
            raise RuntimeError("Gemini 전사 응답 텍스트가 비어 있습니다.")
        return text.strip()

    def transcribe(self, audio_path: Path) -> Transcript:
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"오디오 파일이 존재하지 않습니다: {audio_path}")
        file = self._upload_active(audio_path)
        raw = self._generate(file)
        return parse_transcript_json(raw, provider=self.provider_name)
