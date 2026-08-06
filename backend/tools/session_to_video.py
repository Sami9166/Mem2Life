"""수집 서버가 받아둔 세션 디렉토리를 ingest CLI가 먹을 수 있는 mp4 하나로 합친다.

Blade 2 앱은 영상과 오디오를 **서로 다른 경로로** 보낸다:

  - 영상: 30초마다 회전하는 `chunk_%06d.mp4` (H.264, **오디오 트랙 없음**)
  - 오디오: WebSocket으로 흘려보낸 헤더 없는 raw PCM
    (`audio_16k_mono_s16le.pcm`, 16kHz mono s16le)

반면 `mem2life-ingest`는 "오디오가 들어있는 영상 파일 하나"를 입력으로 받는다.
이 스크립트가 그 간극을 메운다 — 청크를 seq 순서로 이어붙이고 PCM을 입혀
단일 mp4를 만든다.

영상은 재인코딩하지 않는다(`-c:v copy`). 같은 인코더가 같은 파라미터로 뽑은
청크들이라 그대로 이어붙일 수 있고, 첫 청크의 회전 힌트(Blade 2는 180도)도
보존된다. 재인코딩하면 시간도 오래 걸리고 화질만 손해다.

사용 예:

    uv run python tools/session_to_video.py \\
        ../android/tools/mock-backend/data/<sessionId> -o session.mp4
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

AUDIO_FILENAME = "audio_16k_mono_s16le.pcm"
SUMMARY_FILENAME = "session_summary.json"
CHUNK_GLOB = "chunk_*.mp4"

# 앱의 AudioCaptureConfig와 맞물린 값. 여기서 어긋나면 오디오가 느리거나
# 빨라진 채로 전사되므로, 앱 설정을 바꾸면 이 값도 같이 바꿔야 한다.
SAMPLE_RATE = 16000
CHANNELS = 1
BYTES_PER_SAMPLE = 2


class SessionAssemblyError(RuntimeError):
    """세션 디렉토리가 합치기에 부적합할 때."""


@dataclass
class SessionInputs:
    chunks: list[Path]
    audio_pcm: Path | None
    summary: dict | None

    @property
    def audio_duration_sec(self) -> float | None:
        if self.audio_pcm is None:
            return None
        size = self.audio_pcm.stat().st_size
        return size / (SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE)


def _require_binary(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise SessionAssemblyError(
            f"'{name}' 실행 파일을 찾을 수 없습니다. macOS: `brew install ffmpeg`로 설치하세요."
        )
    return path


def _chunk_seq(path: Path) -> int:
    """`chunk_000012.mp4` -> 12. 파일명 규칙이 깨지면 정렬이 조용히 틀어지므로 엄격히 판단한다."""
    stem = path.stem
    _, _, digits = stem.partition("_")
    if not digits.isdigit():
        raise SessionAssemblyError(f"청크 파일명에서 seq를 읽을 수 없습니다: {path.name}")
    return int(digits)


def collect_inputs(session_dir: Path) -> SessionInputs:
    if not session_dir.is_dir():
        raise SessionAssemblyError(f"세션 디렉토리가 아닙니다: {session_dir}")

    chunks = sorted(session_dir.glob(CHUNK_GLOB), key=_chunk_seq)
    if not chunks:
        raise SessionAssemblyError(f"{CHUNK_GLOB} 파일이 하나도 없습니다: {session_dir}")

    empty = [c.name for c in chunks if c.stat().st_size == 0]
    if empty:
        raise SessionAssemblyError(
            f"0바이트 청크가 있어 합칠 수 없습니다: {', '.join(empty)} — "
            "업로드가 중간에 끊겼거나 인코더가 청크를 마감하지 못한 세션입니다."
        )

    # seq에 구멍이 있으면 영상이 조용히 짧아진다. 막지는 않되 반드시 알린다.
    seqs = [_chunk_seq(c) for c in chunks]
    expected = list(range(seqs[0], seqs[-1] + 1))
    if seqs != expected:
        missing = sorted(set(expected) - set(seqs))
        print(
            f"[경고] 청크 seq에 구멍이 있습니다(누락: {missing}). "
            "그 구간만큼 영상이 짧아지고 이후 오디오와 어긋납니다.",
            file=sys.stderr,
        )

    audio_pcm = session_dir / AUDIO_FILENAME
    if not audio_pcm.exists() or audio_pcm.stat().st_size == 0:
        print(
            f"[경고] {AUDIO_FILENAME}이 없거나 비어 있습니다 — 무음 영상으로 합칩니다. "
            "STT 단계에서 전사록이 비게 됩니다.",
            file=sys.stderr,
        )
        audio_pcm = None

    summary_path = session_dir / SUMMARY_FILENAME
    summary = None
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
        if not summary.get("ended"):
            print(
                "[경고] session_summary.json의 ended가 false입니다 — "
                "녹화가 정상 종료되지 않은 세션일 수 있습니다.",
                file=sys.stderr,
            )

    return SessionInputs(chunks=chunks, audio_pcm=audio_pcm, summary=summary)


def assemble(inputs: SessionInputs, output: Path) -> None:
    ffmpeg = _require_binary("ffmpeg")

    # concat 데머서는 파일 목록을 받는다. 경로에 작은따옴표가 들어가면 목록
    # 문법이 깨지므로 ffmpeg 규칙대로 이스케이프한다.
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        for chunk in inputs.chunks:
            escaped = str(chunk.resolve()).replace("'", r"'\''")
            fh.write(f"file '{escaped}'\n")
        list_path = Path(fh.name)

    try:
        cmd = [ffmpeg, "-y", "-loglevel", "error"]
        cmd += ["-f", "concat", "-safe", "0", "-i", str(list_path)]
        if inputs.audio_pcm is not None:
            cmd += [
                "-f", "s16le",
                "-ar", str(SAMPLE_RATE),
                "-ac", str(CHANNELS),
                "-i", str(inputs.audio_pcm),
            ]
        # 영상은 그대로 복사(회전 힌트 보존), 오디오만 aac로 인코딩한다.
        cmd += ["-c:v", "copy"]
        if inputs.audio_pcm is not None:
            cmd += ["-c:a", "aac", "-b:a", "96k"]
        cmd += [str(output)]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise SessionAssemblyError(f"ffmpeg 합치기 실패:\n{result.stderr}")
    finally:
        list_path.unlink(missing_ok=True)


def _report(inputs: SessionInputs, output: Path) -> None:
    print(f"[완료] 청크 {len(inputs.chunks)}개 → {output}")

    video_dur = None
    if inputs.summary:
        video_chunks = inputs.summary.get("video_chunks") or []
        if video_chunks:
            last = video_chunks[-1]
            video_dur = last.get("start_ts", 0.0) + last.get("duration_sec", 0.0)
            print(f"[정보] 영상 길이(요약 기준): {video_dur:.2f}초")

    audio_dur = inputs.audio_duration_sec
    if audio_dur is not None:
        print(f"[정보] 오디오 길이(PCM 크기 기준): {audio_dur:.2f}초")

    # 영상과 오디오는 서로 다른 경로로 와서 시작·종료 시점이 정확히 같지
    # 않다. 작은 차이는 정상이지만 크게 벌어지면 전사 타임스탬프가 영상과
    # 어긋나므로 눈에 띄게 알린다.
    if video_dur is not None and audio_dur is not None:
        skew = audio_dur - video_dur
        marker = "  ← 확인 필요" if abs(skew) > 2.0 else ""
        print(f"[정보] 오디오-영상 차이: {skew:+.2f}초{marker}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="session_to_video",
        description=(
            "수집 서버 세션 디렉토리(청크 mp4 + raw PCM)를 ingest CLI용 mp4 하나로 합친다. "
            "영상은 재인코딩하지 않는다."
        ),
    )
    parser.add_argument("session_dir", type=Path, help="세션 디렉토리 (chunk_*.mp4 와 PCM이 있는 곳)")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="출력 mp4 경로 (기본값: <세션디렉토리>/session_merged.mp4)",
    )
    args = parser.parse_args(argv)

    output = args.output or (args.session_dir / "session_merged.mp4")

    try:
        inputs = collect_inputs(args.session_dir)
        output.parent.mkdir(parents=True, exist_ok=True)
        assemble(inputs, output)
    except SessionAssemblyError as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 1

    _report(inputs, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
