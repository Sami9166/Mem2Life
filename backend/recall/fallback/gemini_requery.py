"""Gemini 영상 입력 재조회 클라이언트 (fallback 라우팅 ③단계의 실제 호출).

fallback이 트리거되면(1차 텍스트 답변이 불충분) 해당 구간 영상 클립을 잘라
Gemini에 영상 입력으로 넣고 다시 물어본다(재답변). 표준 API로 영상을 통째로
받는 계열이 Gemini뿐이라 이 경로는 Gemini에 고정돼 있다(기술조사_의사결정.md
조사 4).

`VideoRequeryClient` Protocol(`trigger.py`)을 만족하므로
`recall/fallback/factory.py`의 provider 매핑을 통해 `RecallPipeline`에 그대로
주입된다(STT/임베딩과 동일한 교체 패턴).

핵심 원칙(CLAUDE.md):
    - 답을 지어내지 않는다: 영상에서 확인 못 하면 "확인불가"로 정직하게 실패.
      프롬프트가 모델에게 첫 줄 sentinel("[확인됨]"/"[확인불가]")을 강제하고,
      형식이 불명확하면 안전하게 미근거(grounded=False)로 처리한다.
    - 근거 타임스탬프 포함: 답을 찾으면 video@mm:ss 형식으로 어느 구간에서
      확인했는지 밝히도록 프롬프트로 지시한다.
    - 조용히 죽지 않는다: 재조회는 이미 "1차 답변이 불충분하다"는 신호라,
      실패해도 예외를 밖으로 던지지 않고 `VideoRequeryResult(grounded=False)`
      로 정직한 실패 문구를 돌려준다(파이프라인이 "기록에 없음"으로 표시).

실제 google-genai 스키마(2.14.0 기준 확인):
    from google import genai
    client = genai.Client(api_key=...)          # GEMINI_API_KEY/GOOGLE_API_KEY
    from google.genai import types
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[prompt_text, types.Part.from_bytes(data=..., mime_type="video/mp4")],
    )
    resp.text
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from ..answer.base import format_mmss
from .trigger import VideoClipTarget, VideoRequeryResult
from .video_clip import ClipExtractionError, FFmpegNotFoundError, extract_clip

DEFAULT_MODEL = "gemini-2.5-flash"

# 인라인 영상 요청은 총 요청 크기 ~20MB 제한이 있다(그 이상은 File API 업로드
# 필요). 30초 안팎 클립은 보통 이 아래지만, 안전 여유를 두고 이 값을 넘는
# 클립은 재조회에서 제외한다(File API 업로드는 후속 작업).
_MAX_INLINE_BYTES = 18 * 1024 * 1024

# 한 번의 재조회에 넣는 클립 수 상한 — 근거 후보가 많아도 상위 몇 개만.
_MAX_CLIPS = 2

_ENV_KEYS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

_MAX_ERROR_CHARS = 300


class GeminiCredentialError(RuntimeError):
    """GEMINI_API_KEY가 없거나 google-genai 패키지를 못 불러올 때."""


def _summarize_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}".strip()
    if len(text) > _MAX_ERROR_CHARS:
        return text[:_MAX_ERROR_CHARS] + "…"
    return text


def _clip_label(index: int, clip: VideoClipTarget) -> str:
    start = format_mmss(clip.start_sec) or "00:00"
    end = format_mmss(clip.end_sec) or "??:??"
    title = clip.session_title or Path(clip.video_path).stem
    return f"클립 {index}: 세션 '{title}'의 {start}~{end} 구간"


class GeminiVideoRequeryClient:
    """영상 클립을 Gemini에 넣어 재조회하는 실제 클라이언트."""

    provider_name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        client: object | None = None,
        video_root: Path | str | None = None,
        max_clips: int = _MAX_CLIPS,
        max_inline_bytes: int = _MAX_INLINE_BYTES,
        clip_reencode: bool = False,
        env: dict[str, str] | None = None,
    ) -> None:
        """
        Args:
            api_key: 명시하면 환경변수보다 우선. 생략 시 `env`(기본 `os.environ`)의
                GEMINI_API_KEY 또는 GOOGLE_API_KEY를 읽는다.
            model: 사용할 Gemini 모델 이름.
            client: 주입 가능한 genai.Client 유사 객체(`.models.generate_content`
                를 가진 것). 테스트에서 네트워크 없이 응답을 흉내낼 때 쓴다.
                주입하면 api_key 검사를 건너뛴다.
            video_root: `clip.video_path`가 상대경로일 때 이 디렉토리 기준으로
                해석한다(볼트/프로젝트 루트). 절대경로면 무시된다.
            max_clips: 한 번의 재조회에 넣을 최대 클립 수.
            max_inline_bytes: 인라인으로 보낼 클립 1개의 최대 바이트.
            clip_reencode: 클립 추출 시 재인코딩 여부(정확 길이 vs 속도).
            env: 환경변수 딕셔너리(기본 os.environ). 테스트 결정성용 주입 가능.
        """
        self._model = model
        self._video_root = Path(video_root) if video_root is not None else None
        self._max_clips = max(1, max_clips)
        self._max_inline_bytes = max_inline_bytes
        self._clip_reencode = clip_reencode

        if client is not None:
            self._client = client
            return

        source_env = os.environ if env is None else env
        resolved_key = api_key or next(
            (source_env[k] for k in _ENV_KEYS if source_env.get(k)),
            None,
        )
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

    # -- 내부 단계들 ---------------------------------------------------

    def _resolve_video(self, video_path: str) -> Path:
        path = Path(video_path)
        if not path.is_absolute() and self._video_root is not None:
            return self._video_root / path
        return path

    def _prepare_clip_parts(
        self, clips: Sequence[VideoClipTarget], tmpdir: str
    ) -> tuple[list[object], list[VideoClipTarget], list[str]]:
        """각 클립을 잘라 Gemini용 video Part로 만든다.

        Returns:
            (parts, used_clips, prep_errors). 준비에 실패한 클립은 parts/used에서
            빠지고 사유가 prep_errors에 쌓인다. 하나도 준비 못하면 parts가 빈다.
        """
        from google.genai import types

        parts: list[object] = []
        used: list[VideoClipTarget] = []
        prep_errors: list[str] = []
        for i, clip in enumerate(clips):
            source = self._resolve_video(clip.video_path)
            out = Path(tmpdir) / f"clip_{i}.mp4"
            try:
                extract_clip(
                    source,
                    clip.start_sec,
                    clip.end_sec,
                    out,
                    reencode=self._clip_reencode,
                )
            except FileNotFoundError:
                prep_errors.append(f"원본 영상 없음({clip.video_path})")
                continue
            except FFmpegNotFoundError as exc:
                prep_errors.append(str(exc))
                continue
            except (ClipExtractionError, ValueError) as exc:
                prep_errors.append(f"클립 추출 실패({clip.video_path}): {_summarize_error(exc)}")
                continue

            size = out.stat().st_size
            if size > self._max_inline_bytes:
                prep_errors.append(f"클립이 인라인 한계를 초과({clip.video_path}, {size / 1_048_576:.1f}MB)")
                continue
            parts.append(types.Part.from_bytes(data=out.read_bytes(), mime_type="video/mp4"))
            used.append(clip)
        return parts, used, prep_errors

    def _build_prompt(self, question: str, used: Sequence[VideoClipTarget]) -> str:
        clip_lines = "\n".join(_clip_label(i + 1, c) for i, c in enumerate(used))
        return (
            "당신은 착용자의 1인칭 영상 기록을 검토해 질문에 답하는 보조자입니다.\n"
            "아래 영상 클립들은 특정 세션의 특정 구간입니다(입력된 영상 순서와 동일):\n"
            f"{clip_lines}\n\n"
            f"질문: {question}\n\n"
            "규칙:\n"
            "- 영상에서 눈으로 확인할 수 있는 내용만으로 답하세요. 추측하거나 "
            "일반 상식으로 지어내지 마세요.\n"
            "- 답을 찾았으면 첫 줄을 정확히 '[확인됨]'으로 시작하고, 근거가 보인 "
            "시점을 video@mm:ss 형식으로 함께 적으세요(각 클립의 구간 오프셋 기준 절대 시각).\n"
            "- 영상에서 확인할 수 없으면 첫 줄을 정확히 '[확인불가]'로 시작하고 "
            "왜 확인이 어려운지 한 문장으로만 설명하세요.\n"
            "- 한국어로 간결하게 답하세요."
        )

    def _call_gemini(self, prompt: str, parts: Sequence[object]) -> str:
        contents: list[object] = [prompt, *parts]
        response = self._client.models.generate_content(model=self._model, contents=contents)
        text = getattr(response, "text", None)
        return (text or "").strip()

    def _interpret_response(self, text: str, used: Sequence[VideoClipTarget]) -> VideoRequeryResult:
        if not text:
            return VideoRequeryResult(
                answer_text="[영상 재조회 실패] Gemini가 빈 응답을 반환했습니다.",
                grounded=False,
                clips_used=tuple(used),
                error="empty response",
            )
        first_line = text.splitlines()[0]
        # 형식이 불명확하면(둘 다 아님) 지어냄 방지를 위해 안전하게 미근거 처리.
        if "확인불가" in first_line:
            grounded = False
        elif "확인됨" in first_line:
            grounded = True
        else:
            grounded = False
        return VideoRequeryResult(answer_text=text, grounded=grounded, clips_used=tuple(used))

    # -- 공개 인터페이스 -----------------------------------------------

    def requery(self, question: str, clips: Sequence[VideoClipTarget]) -> VideoRequeryResult:
        if not clips:
            return VideoRequeryResult(
                answer_text=(
                    "[영상 재조회 실패] 재조회할 영상 구간을 찾지 못했습니다 — 원본 영상이 "
                    "볼트에 기록돼 있는지 확인이 필요합니다."
                ),
                grounded=False,
            )

        selected = list(clips)[: self._max_clips]
        with tempfile.TemporaryDirectory(prefix="requery_") as tmpdir:
            parts, used, prep_errors = self._prepare_clip_parts(selected, tmpdir)
            if not parts:
                detail = "; ".join(prep_errors) if prep_errors else "알 수 없는 이유"
                return VideoRequeryResult(
                    answer_text=f"[영상 재조회 실패] 영상 클립을 준비하지 못했습니다: {detail}",
                    grounded=False,
                    clips_used=(),
                    error=detail,
                )
            prompt = self._build_prompt(question, used)
            try:
                text = self._call_gemini(prompt, parts)
            except Exception as exc:  # noqa: BLE001 - 어떤 실패든 정직한 미근거로 환원한다
                msg = _summarize_error(exc)
                print(f"[경고] Gemini 영상 재조회 호출 실패: {msg}", file=sys.stderr)
                return VideoRequeryResult(
                    answer_text=f"[영상 재조회 실패] Gemini 영상 재조회 호출이 실패했습니다: {msg}",
                    grounded=False,
                    clips_used=tuple(used),
                    error=msg,
                )
        return self._interpret_response(text, used)
