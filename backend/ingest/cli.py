"""`uv run mem2life-ingest <영상경로>` 로 실행하는 기록 파이프라인 CLI.

영상 파일 하나만 넘기면 오디오 추출 → STT(RTZR, 인증 정보 없으면 자동 스텁
폴백) → Obsidian 세션 md 생성까지 API 키 없이도 끝까지 실행된다. 인증 정보가
있어도 API 호출 자체가 실패하면(네트워크 문제, RTZR 서버 오류 등)
`ingest/pipeline.py`가 같은 스텁으로 대체해 세션 md 생성까지는 항상
완료되도록 한다(경고 메시지 출력).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .pipeline import run_ingest_pipeline
from .stt.factory import DEFAULT_PROVIDER, available_providers

# backend/ingest/cli.py -> parents[0]=ingest, [1]=backend, [2]=Mem2Life
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_VAULT = _BACKEND_DIR.parent / "vault"
_ENV_FILE = _BACKEND_DIR / ".env"


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"날짜 형식이 올바르지 않습니다 (ISO 형식, 예: 2026-07-17T15:00): {value!r}"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mem2life-ingest",
        description=(
            "영상 파일 하나로 기록 파이프라인(오디오 추출 → STT 스텁 → "
            "Obsidian 세션 md 생성)을 처음부터 끝까지 실행한다. "
            "API 키가 필요 없다 (STT는 스텁으로 동작)."
        ),
    )
    parser.add_argument("video", type=Path, help="입력 영상 파일 경로")
    parser.add_argument(
        "--vault",
        type=Path,
        default=_DEFAULT_VAULT,
        help=f"Obsidian 볼트 디렉토리 (기본값: {_DEFAULT_VAULT})",
    )
    parser.add_argument("--title", default="세션", help="세션 제목 (파일명에 사용, 기본값: 세션)")
    parser.add_argument(
        "--datetime",
        dest="session_start",
        type=_parse_datetime,
        default=None,
        help=(
            "세션 시작 시각 (ISO 형식, 예: 2026-07-17T15:00). 생략 시 현재 시각. "
            "리허설에서 '어제' 세션을 재현할 때 전날 날짜를 넣는 개발용 옵션으로도 쓴다."
        ),
    )
    parser.add_argument(
        "--stt",
        dest="stt_provider",
        default=DEFAULT_PROVIDER,
        choices=available_providers(),
        help=f"STT provider 선택 (기본값: {DEFAULT_PROVIDER}, 스텁으로 동작)",
    )
    parser.add_argument(
        "--participant",
        dest="participants",
        action="append",
        default=None,
        metavar="이름",
        help="참석자 표기 (여러 번 지정 가능). 생략 시 화자1/화자2로 표기.",
    )
    parser.add_argument(
        "--no-keep-audio",
        dest="delete_audio",
        action="store_true",
        help="파이프라인 종료 후 추출된 .wav 오디오 파일을 삭제한다 (기본: 보존).",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("MEM2LIFE_DATABASE_URL"),
        help="PostgreSQL DSN. 생략하면 기존 파일 모드로 실행한다.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    # STT 클라이언트(rtzr_client.py 등)는 `os.environ`만 읽고 값이 어떻게
    # 채워졌는지는 모른다 — `.env` 실제 로드는 CLI 진입점인 여기서만 담당한다.
    # backend/.env가 없어도 조용히 넘어간다(그 경우 factory가 스텁으로 폴백).
    # 기존에 이미 설정된 환경변수(예: 셸 export, 테스트의 monkeypatch)는
    # override하지 않는다.
    load_dotenv(_ENV_FILE, override=False)

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        result = run_ingest_pipeline(
            args.video,
            args.vault,
            title=args.title,
            session_start=args.session_start,
            participants=args.participants,
            stt_provider=args.stt_provider,
            keep_audio=not args.delete_audio,
            database_url=args.database_url,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        # OSError는 FileNotFoundError(입력 영상 없음)뿐 아니라 PermissionError,
        # 디스크 풀 등 형제 예외까지 모두 포괄한다(FileNotFoundError는 OSError의
        # 서브클래스라 별도로 나열할 필요가 없다). RuntimeError는 audio.py의
        # FFmpegNotFoundError/AudioExtractionError(ffmpeg 미설치, 오디오 트랙
        # 없음 등)가 OSError가 아닌 RuntimeError 계열이라 계속 필요하다 —
        # 여기서 빠지면 그 경로들이 다시 원시 traceback으로 노출된다.
        print(f"[실패] {exc}", file=sys.stderr)
        return 1

    print(f"[완료] STT provider   : {result.stt_provider}")
    print(f"[완료] 오디오 파일     : {result.audio_path}")
    print(f"[완료] 전사록 발화 수  : {len(result.transcript.segments)}")
    print(f"[완료] 감지된 화자     : {', '.join(result.transcript.speakers)}")
    print(f"[완료] 세션 md 생성    : {result.session_md_path}")
    if result.session_id:
        print(f"[완료] DB 세션 ID      : {result.session_id}")
        print(f"[완료] 전사록 원본     : {result.transcript_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
