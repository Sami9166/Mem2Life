"""`uv run mem2life-recall` 로 실행하는 회상(recall) 파이프라인 CLI.

`ask` 서브커맨드는 질문 하나를 텍스트로 받아 검색→답변→fallback 판정까지
전체 흐름을 실행하고 결과를 사람이 읽기 좋은 형태로 출력한다(개발 중
수동 검증용, ingest의 `mem2life-ingest`와 짝을 이룬다).
`serve` 서브커맨드는 `api.py`의 FastAPI 앱을 uvicorn으로 띄운다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .index.embeddings.factory import DEFAULT_PROVIDER as DEFAULT_EMBEDDING_PROVIDER
from .pipeline import RecallPipeline

# backend/recall/cli.py -> parents[0]=recall, [1]=backend, [2]=Mem2Life
_DEFAULT_VAULT = Path(__file__).resolve().parents[2] / "vault"
_DEFAULT_CACHE = Path(__file__).resolve().parents[1] / ".recall_index_cache.json"


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"날짜 형식이 올바르지 않습니다 (예: 2026-07-18): {value!r}"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mem2life-recall",
        description="Obsidian 볼트 하이브리드 검색 + 답변 생성 + fallback 판정 CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask = subparsers.add_parser("ask", help="질문 하나를 실행하고 결과를 출력한다")
    ask.add_argument("question", help="질문 문구 (예: '어제 민수랑 숙소 예산 얼마로 정했지?')")
    ask.add_argument(
        "--vault", type=Path, default=_DEFAULT_VAULT, help=f"볼트 경로 (기본값: {_DEFAULT_VAULT})"
    )
    ask.add_argument("--cache", type=Path, default=_DEFAULT_CACHE, help="인덱스 캐시 파일 경로")
    ask.add_argument(
        "--reference-date",
        type=_parse_date,
        default=None,
        help="'오늘'로 취급할 날짜(ISO, 예: 2026-07-18). 생략 시 실제 오늘 날짜.",
    )
    ask.add_argument(
        "--embedding",
        dest="embedding_provider",
        default=DEFAULT_EMBEDDING_PROVIDER,
        help=f"임베딩 provider (기본값: {DEFAULT_EMBEDDING_PROVIDER})",
    )

    serve = subparsers.add_parser("serve", help="FastAPI 서버를 uvicorn으로 실행한다")
    serve.add_argument("--vault", type=Path, default=_DEFAULT_VAULT)
    serve.add_argument("--cache", type=Path, default=_DEFAULT_CACHE)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8100)

    return parser


def _run_ask(args: argparse.Namespace) -> int:
    if not args.vault.is_dir():
        print(f"[실패] 볼트 디렉토리를 찾을 수 없습니다: {args.vault}", file=sys.stderr)
        return 1

    pipeline = RecallPipeline(args.vault, cache_path=args.cache, embedding_provider=args.embedding_provider)
    reference_date = args.reference_date or date.today()
    result = pipeline.answer_question(args.question, reference_date=reference_date)

    print(f"[질문] {args.question}")
    print(f"[분류] {result.question_type.value}")
    print(f"[답변] {result.final_text}")
    print(f"[TTS ] {result.tts_text}")
    if result.citations:
        print("[근거]")
        for c in result.citations:
            video = f" ({c.video_link})" if c.video_link else ""
            print(f"  - {c.label}{video}: {c.excerpt}")
    print(f"[fallback] triggered={result.fallback.triggered} — {result.fallback.verdict.reason}")
    if result.fallback.triggered:
        print(f"[fallback stub] {result.fallback_stub_result}")
    return 0


def _run_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("[실패] uvicorn이 설치돼 있지 않습니다 (uv sync 확인)", file=sys.stderr)
        return 1

    from .api import create_app

    if not args.vault.is_dir():
        print(f"[실패] 볼트 디렉토리를 찾을 수 없습니다: {args.vault}", file=sys.stderr)
        return 1

    pipeline = RecallPipeline(args.vault, cache_path=args.cache)
    app = create_app(pipeline)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "ask":
        return _run_ask(args)
    if args.command == "serve":
        return _run_serve(args)
    parser.error(f"알 수 없는 서브커맨드: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
