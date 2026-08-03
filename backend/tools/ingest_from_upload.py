"""업로드 세션 → 전사 → 볼트 세션.md 글루.

mock-backend(또는 실 수신 서버)가 저장한 세션 디렉터리 하나를 받아:

  1. audio_16k_mono_s16le.pcm → WAV 래핑
  2. RTZR STT로 전사(.env에 RTZR 키가 없으면 자동 스텁)
  3. chunk_*.mp4 를 이어붙여 fallback 재조회용 원본 영상 1개로 concat
  4. 이어붙인 영상에서 사건 경계 키프레임 추출 → Gemini VLM 장면 캡션
  5. 전사록+캡션으로 Gemini LLM 세션 요약
  6. ingest.wiki.session_md.write_session_md 로 볼트에 세션 .md 생성

즉 앱↔백엔드 계약(분리 전송된 청크+PCM)과 기존 ingest/recall(완성 영상+전사록
기반) 사이의 배선이다.

4~5단계는 `ingest/pipeline.py`의 `resolve_captions`/`resolve_summary`를 그대로
재사용한다 — CLI 진입점(`mem2life-ingest`, 완성 영상 1개 입력)과 이 글루(글래스가
청크로 올린 업로드 세션 입력)가 서로 다른 폴백 규칙을 갖게 되는 것을 막기 위해서다.
따라서 여기서도 GEMINI_API_KEY가 없으면 플레이스홀더로, 호출이 실패하면(429 등)
같은 플레이스홀더로 자동 폴백하고 세션 .md 생성 자체는 항상 끝까지 진행한다.

왜 `run_ingest_pipeline`을 통째로 부르지 않는가: 앱이 올리는 mp4 청크에는 오디오
트랙이 없고(오디오는 계약상 WebSocket PCM으로 따로 온다) `run_ingest_pipeline`은
영상에서 오디오를 추출해 STT를 돌리는 구조라 형태가 맞지 않는다. 그래서 STT
입력만 PCM 경로로 두고, 그 이후 단계는 위처럼 공유한다.

사용:
    uv run python tools/ingest_from_upload.py <업로드_세션_디렉터리> \
        [--vault ../vault] [--title "제목"] [--stt rtzr] [--no-captions]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from ingest.pipeline import resolve_captions, resolve_summary
from ingest.stt.factory import DEFAULT_PROVIDER, get_stt_client
from ingest.visual import VideoOpenError, VisualProcessingResult, process_video
from ingest.wiki.session_md import session_filename, write_session_md

_SAMPLE_RATE_HZ = 16_000
_DEFAULT_VAULT = Path(__file__).resolve().parents[2] / "vault"


def _pcm_to_wav(pcm_path: Path, wav_path: Path) -> None:
    pcm = pcm_path.read_bytes()
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # s16le
        w.setframerate(_SAMPLE_RATE_HZ)
        w.writeframes(pcm)


def _concat_chunks(session_dir: Path, out_path: Path) -> Path | None:
    """chunk_*.mp4 를 하나로 이어붙인다. ffmpeg가 없으면 건너뛰고 청크 디렉터리를 그대로 참조한다."""
    chunks = sorted(session_dir.glob("chunk_*.mp4"))
    if not chunks:
        return None
    listfile = session_dir / "_concat_list.txt"
    listfile.write_text("".join(f"file '{c.resolve()}'\n" for c in chunks), encoding="utf-8")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(out_path)],
            check=True,
            capture_output=True,
        )
        return out_path
    except (FileNotFoundError, subprocess.CalledProcessError):
        # ffmpeg 미설치/실패: 원본 청크 디렉터리를 video_path로 참조(재조회는 청크 단위로 가능).
        return None


def _session_start(session_dir: Path) -> datetime:
    summary = session_dir / "session_summary.json"
    if summary.exists():
        data = json.loads(summary.read_text(encoding="utf-8"))
        started = data.get("started_at")
        if started:
            return datetime.fromisoformat(started)
    return datetime.now(UTC)


def main() -> int:
    parser = argparse.ArgumentParser(description="업로드 세션을 볼트 세션.md로 변환한다.")
    parser.add_argument("session_dir", type=Path, help="mock-backend data/<session_id> 디렉터리")
    parser.add_argument(
        "--vault", type=Path, default=_DEFAULT_VAULT, help=f"볼트 경로 (기본값: {_DEFAULT_VAULT})"
    )
    parser.add_argument("--title", default=None, help="세션 제목 (생략 시 세션ID)")
    parser.add_argument("--stt", default=DEFAULT_PROVIDER, help=f"STT provider (기본값: {DEFAULT_PROVIDER})")
    parser.add_argument(
        "--no-captions",
        action="store_true",
        help="키프레임 추출·VLM 장면 캡션을 건너뛴다 (전사록만 필요할 때 / Gemini 호출 수 절약).",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="LLM 세션 요약을 건너뛴다 (## 요약은 TODO 플레이스홀더로 남는다).",
    )
    parser.add_argument(
        "--media-dir",
        type=Path,
        default=None,
        help="키프레임 이미지 저장 위치 (기본값: <vault>/media).",
    )
    args = parser.parse_args()

    load_dotenv(".env")

    session_dir: Path = args.session_dir
    if not session_dir.is_dir():
        print(f"[실패] 세션 디렉터리를 찾을 수 없습니다: {session_dir}", file=sys.stderr)
        return 1

    pcm = session_dir / "audio_16k_mono_s16le.pcm"
    if not pcm.exists():
        print(f"[실패] 오디오 PCM이 없습니다: {pcm}", file=sys.stderr)
        return 1

    session_id = session_dir.name
    title = args.title or session_id

    # 1. PCM → WAV
    wav = session_dir / "audio.wav"
    _pcm_to_wav(pcm, wav)
    print(f"[1/6] WAV 생성: {wav.name} ({wav.stat().st_size / 1e6:.1f}MB)")

    # 2. STT
    client = get_stt_client(args.stt)
    print(f"[2/6] STT({type(client).__name__}) 전사 중...")
    transcript = client.transcribe(wav)
    print(
        f"      → provider={transcript.provider}, 발화 {len(transcript.segments)}개, "
        f"화자 {transcript.speakers}"
    )

    # 3. 청크 concat (fallback 원본 영상)
    merged = _concat_chunks(session_dir, session_dir / "session_video.mp4")
    video_path = merged if merged is not None else session_dir
    print(f"[3/6] 원본 영상 참조: {video_path}")

    session_start = _session_start(session_dir)
    participants = list(transcript.speakers or ["화자1"])
    # 세션 md 파일명과 같은 이름을 미디어 서브디렉터리로 써서 세션 간 키프레임
    # 파일명이 충돌하지 않게 한다(`run_ingest_pipeline`과 동일한 규칙).
    media_slug = Path(session_filename(session_start, title)).stem
    media_dir = args.media_dir if args.media_dir is not None else args.vault / "media"

    # 4. 키프레임 추출 (concat이 실패해 video_path가 디렉터리면 건너뛴다)
    visual = VisualProcessingResult(session_duration_sec=transcript.duration_sec)
    if args.no_captions:
        print("[4/6] 키프레임 추출 건너뜀 (--no-captions)")
    elif merged is None:
        print("[4/6] 키프레임 추출 건너뜀 (청크 concat 실패 — 이어붙인 영상이 없음)", file=sys.stderr)
    else:
        try:
            visual = process_video(merged, media_dir=media_dir, session_id=media_slug)
            print(f"[4/6] 키프레임 {len(visual.processed_keyframes)}장 추출 → {media_dir}")
        except VideoOpenError as exc:
            # STT 실패와 동일 원칙 — 한 단계가 실패해도 세션 md 생성은 끝까지 간다.
            print(f"[4/6] 키프레임 추출 실패, 장면 캡션 없이 진행합니다: {exc}", file=sys.stderr)

    # 5. VLM 장면 캡션 + LLM 요약 (키/호출 실패 시 자동 플레이스홀더 폴백)
    captions = resolve_captions(
        (),
        visual.processed_keyframes,
        transcript,
        media_slug=media_slug,
    )
    print(f"[5/6] 장면 캡션 {len(captions)}건")
    summary = None if args.no_summary else resolve_summary(None, transcript, captions, participants)
    print(f"      요약: {'생성됨' if summary else '없음(TODO 플레이스홀더)'}")

    # 6. 볼트 세션 .md
    out = write_session_md(
        args.vault,
        session_start=session_start,
        title=title,
        participants=participants,
        video_path=str(video_path),
        transcript=transcript,
        summary=summary,
        captions=[(start_sec, text) for start_sec, _end_sec, text in captions],
    )
    print(f"[6/6] 볼트 세션 생성: {out}")
    print("\n완료. 아래로 질의 가능:")
    print(f'  uv run mem2life-recall ask "<질문>" --vault {args.vault}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
