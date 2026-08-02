"""업로드 세션 → 전사 → 볼트 세션.md 글루.

mock-backend(또는 실 수신 서버)가 저장한 세션 디렉터리 하나를 받아:

  1. audio_16k_mono_s16le.pcm → WAV 래핑
  2. RTZR STT로 전사(.env에 RTZR 키가 없으면 자동 스텁)
  3. chunk_*.mp4 를 이어붙여 fallback 재조회용 원본 영상 1개로 concat
  4. ingest.wiki.session_md.write_session_md 로 볼트에 세션 .md 생성

즉 앱↔백엔드 계약(분리 전송된 청크+PCM)과 기존 ingest/recall(완성 영상+전사록
기반) 사이의 배선이다. LLM 요약/VLM 캡션은 이 글루의 책임이 아니다(후속 작업).

사용:
    uv run python tools/ingest_from_upload.py <업로드_세션_디렉터리> \
        [--vault ../vault] [--title "제목"] [--stt rtzr]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from ingest.stt.factory import DEFAULT_PROVIDER, get_stt_client
from ingest.wiki.session_md import write_session_md

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
    return datetime.now(timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(description="업로드 세션을 볼트 세션.md로 변환한다.")
    parser.add_argument("session_dir", type=Path, help="mock-backend data/<session_id> 디렉터리")
    parser.add_argument("--vault", type=Path, default=_DEFAULT_VAULT, help=f"볼트 경로 (기본값: {_DEFAULT_VAULT})")
    parser.add_argument("--title", default=None, help="세션 제목 (생략 시 세션ID)")
    parser.add_argument("--stt", default=DEFAULT_PROVIDER, help=f"STT provider (기본값: {DEFAULT_PROVIDER})")
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
    print(f"[1/4] WAV 생성: {wav.name} ({wav.stat().st_size / 1e6:.1f}MB)")

    # 2. STT
    client = get_stt_client(args.stt)
    print(f"[2/4] STT({type(client).__name__}) 전사 중...")
    transcript = client.transcribe(wav)
    print(f"      → provider={transcript.provider}, 발화 {len(transcript.segments)}개, 화자 {transcript.speakers}")

    # 3. 청크 concat (fallback 원본 영상)
    merged = _concat_chunks(session_dir, session_dir / "session_video.mp4")
    video_path = merged if merged is not None else session_dir
    print(f"[3/4] 원본 영상 참조: {video_path}")

    # 4. 볼트 세션 .md
    out = write_session_md(
        args.vault,
        session_start=_session_start(session_dir),
        title=title,
        participants=transcript.speakers or ["화자1"],
        video_path=str(video_path),
        transcript=transcript,
    )
    print(f"[4/4] 볼트 세션 생성: {out}")
    print("\n완료. 아래로 질의 가능:")
    print(f'  uv run mem2life-recall ask "<질문>" --vault {args.vault}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
