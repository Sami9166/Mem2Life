"""영상 파일 하나로 기록 파이프라인 전체(오디오 추출 → STT, 키프레임 추출 →
VLM 캡션 → LLM 요약 → 세션 md 생성)를 처음부터 끝까지 실행하는 오케스트레이션
모듈.

이번 단계 범위: 오디오 추출 → STT(화자분리) → 영상 사건 경계 탐지·대표
키프레임 저장(`ingest/visual.py`) → VLM 캡션(`ingest/vlm/`) → LLM 요약
(`ingest/vlm/`) → Obsidian 세션 md 생성까지. 엔티티(인물/주제) 페이지 갱신은
아직 다음 단계다.

API 키 없이 끝까지 성공하는 것이 이번 단계의 핵심 목표다. STT(RTZR/Clova)와
VLM 캡션·LLM 요약(Gemini, 기술조사_의사결정.md 조사4) 모두 같은 두 단계 폴백
원칙을 따른다:

    1. 생성 시점 폴백(각 `factory.py`의 책임): 인증 정보가 아예 없으면 실제
       클라이언트를 만들지 않고 곧바로 스텁/플레이스홀더를 쓴다.
    2. 실행 시점 폴백(이 모듈의 책임, 아래): 인증 정보는 있어서 실제
       클라이언트가 만들어졌지만 호출 자체가 실패하면, 데모 도중 전체 실행이
       중단돼 세션 md가 아예 생성되지 않는 최악의 상황을 피하기 위해 그
       세션만 스텁/플레이스홀더로 대체해 파이프라인을 끝까지 진행시킨다
       (경고 메시지 출력, 나중에 재처리 권장).

`stt_client.transcribe()`가 `RTZRAPIError`(네트워크 429/5xx 소진, RTZR
서버가 status="failed"를 반환, 폴링 타임아웃 등 인증 이후 단계에서 발생하는
실패)를 던지면 화자1/화자2 더미 스텁 전사록으로 대체한다. `RTZRCredentialError`
(인증 정보 누락/오류)는 여기서 잡지 않는다 — 그 경로는 이미
`stt.factory._build_rtzr_client()`가 생성 시점에 스텁으로 대체하므로 이
지점까지 올라오지 않아야 하고, 만약 올라온다면 설정 문제이지 일시적
장애가 아니므로 그대로 실패시켜 사용자가 인지하게 한다.

VLM 캡션/LLM 요약도 동일하다 — `GeminiAPIError`(네트워크 오류, 429/5xx, 빈
응답 등)는 플레이스홀더로 대체하고, `GeminiCredentialError`는 잡지 않고
그대로 전파한다(설정 문제이지 일시적 장애가 아니므로).

`database_url`을 지정했는데 PostgreSQL 연결 자체가 실패하는 경우
(`psycopg.OperationalError` — 서버 미기동, 네트워크 문제 등)도 같은
원칙을 따른다: 전체 실행을 중단하는 대신 DB 없이 기존 파일 모드로
전환해 세션 md 생성까지는 끝까지 진행한다.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg

from recall.index.embeddings.factory import DEFAULT_PROVIDER as DEFAULT_EMBEDDING_PROVIDER
from wiki_db import MemoryItem, StoredSession, WikiDatabase

from .audio import ExtractedAudio, extract_audio
from .stt.base import Transcript
from .stt.factory import DEFAULT_PROVIDER, get_stt_client
from .stt.rtzr_client import RTZRAPIError
from .stt.rtzr_stub import RTZRStubClient
from .visual import ProcessedKeyframe, VideoOpenError, VisualProcessingResult, process_video
from .vlm.base import CaptionItem
from .vlm.factory import (
    DEFAULT_CAPTION_PROVIDER,
    DEFAULT_SUMMARY_PROVIDER,
    get_llm_summarizer,
    get_vlm_captioner,
)
from .vlm.gemini_client import GeminiAPIError
from .vlm.stub import PlaceholderLLMSummarizer, PlaceholderVLMCaptioner
from .wiki.session_md import session_filename, write_session_md


@dataclass(frozen=True, slots=True)
class IngestResult:
    """파이프라인 실행 결과 요약."""

    video_path: Path
    audio_path: Path
    session_md_path: Path
    stt_provider: str
    transcript: Transcript
    visual: VisualProcessingResult
    session_id: str | None = None
    transcript_path: Path | None = None
    # database_url을 지정했는데 PostgreSQL 연결 실패로 파일 모드로 대체된
    # 경우에만 True. database_url을 처음부터 안 준 경우(정상 파일 모드)와
    # 구분하는 용도 — 둘 다 session_id is None이라 그것만으론 "DB를
    # 시도했다가 실패"인지 구별이 안 된다.
    database_fallback: bool = False


def _write_transcript_json(path: Path, transcript: Transcript) -> None:
    """STT 원본을 재처리 가능한 JSON으로 원자적으로 저장한다."""
    payload = {
        "provider": transcript.provider,
        "segments": [
            {
                "start_sec": segment.start_sec,
                "end_sec": segment.end_sec,
                "speaker": segment.speaker,
                "text": segment.text,
            }
            for segment in transcript.segments
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _memory_items(
    transcript: Transcript,
    summary: str | None,
    highlights: Sequence[tuple[float, float, str]],
    captions: Sequence[tuple[float, float, str]],
) -> tuple[MemoryItem, ...]:
    items = [
        MemoryItem(
            kind="transcript",
            ordinal=index,
            content=segment.text,
            speaker=segment.speaker,
            start_ms=round(segment.start_sec * 1000),
            end_ms=round(segment.end_sec * 1000),
        )
        for index, segment in enumerate(transcript.segments)
    ]
    if summary and summary.strip():
        items.append(
            MemoryItem(
                kind="summary",
                ordinal=0,
                content=summary.strip(),
                start_ms=0,
                end_ms=round(transcript.duration_sec * 1000),
            )
        )
    items.extend(
        MemoryItem(
            kind="highlight",
            ordinal=index,
            content=text,
            start_ms=round(start_sec * 1000),
            end_ms=round(end_sec * 1000),
        )
        for index, (start_sec, end_sec, text) in enumerate(highlights)
    )
    items.extend(
        MemoryItem(
            kind="caption",
            ordinal=index,
            content=text,
            start_ms=round(start_sec * 1000),
            end_ms=round(end_sec * 1000),
        )
        for index, (start_sec, end_sec, text) in enumerate(captions)
    )
    return tuple(items)


def resolve_captions(
    captions: Sequence[CaptionItem],
    keyframes: Sequence[ProcessedKeyframe],
    transcript: Transcript,
    *,
    media_slug: str,
    caption_provider: str = DEFAULT_CAPTION_PROVIDER,
) -> list[CaptionItem]:
    """호출자가 `captions`를 명시적으로 넘기면 그대로 쓰고, 아니면 VLM으로 만든다.

    VLM 캡션 생성 자체가 실패하면(`GeminiAPIError`) RTZR과 동일한 원칙으로
    플레이스홀더로 대체해 세션 md 생성까지는 끝까지 진행한다.

    `run_ingest_pipeline`(완성 영상 1개 입력)과 `tools/ingest_from_upload.py`
    (글래스가 청크로 올린 업로드 세션 입력) 두 진입점이 공유한다 — 실행 시점
    폴백 규칙이 두 경로에서 갈라지지 않도록 여기 한 곳에만 둔다.
    """
    if captions:
        return list(captions)
    if not keyframes:
        return []

    captioner = get_vlm_captioner(caption_provider)
    try:
        return captioner.caption_keyframes(keyframes, transcript, media_slug=media_slug)
    except GeminiAPIError as exc:
        print(
            "[경고] VLM 캡션 생성이 실패해 이번 세션은 실제 장면 캡션 대신 "
            f"키프레임 이미지 플레이스홀더로 대체합니다: {exc}\n"
            "        Gemini API가 정상화되면 이 영상으로 다시 실행해 재처리하는 "
            "것을 권장합니다.",
            file=sys.stderr,
        )
        return PlaceholderVLMCaptioner().caption_keyframes(keyframes, transcript, media_slug=media_slug)


def resolve_summary(
    summary: str | None,
    transcript: Transcript,
    captions: Sequence[CaptionItem],
    participants: Sequence[str],
    *,
    summary_provider: str = DEFAULT_SUMMARY_PROVIDER,
) -> str | None:
    """호출자가 `summary`를 명시적으로 넘기면 그대로 쓰고, 아니면 LLM으로 만든다.

    LLM 요약 생성 자체가 실패하면(`GeminiAPIError`) `None`으로 대체해
    `ingest/wiki/session_md.py`의 기존 TODO 플레이스홀더에 위임한다(세션 md
    생성 자체는 끝까지 진행).

    `resolve_captions`와 마찬가지로 두 진입점(CLI 영상 입력 / 업로드 세션 글루)이
    공유한다.
    """
    if summary and summary.strip():
        return summary

    summarizer = get_llm_summarizer(summary_provider)
    try:
        return summarizer.summarize_session(transcript, captions, participants)
    except GeminiAPIError as exc:
        print(
            "[경고] LLM 요약 생성이 실패해 이번 세션은 요약 없이(TODO 플레이스홀더) "
            f"진행합니다: {exc}\n"
            "        Gemini API가 정상화되면 이 영상으로 다시 실행해 재처리하는 "
            "것을 권장합니다.",
            file=sys.stderr,
        )
        return PlaceholderLLMSummarizer().summarize_session(transcript, captions, participants)


def _transcript_from_session(session: StoredSession) -> Transcript:
    from .stt.base import TranscriptSegment

    segments = [
        TranscriptSegment(
            start_sec=item.start_ms / 1000,
            end_sec=(item.end_ms if item.end_ms is not None else item.start_ms) / 1000,
            speaker=item.speaker or "화자1",
            text=item.content,
        )
        for item in session.items
        if item.kind == "transcript"
    ]
    return Transcript(segments=segments, provider=session.stt_provider)


def _timed_items(session: StoredSession, kind: str) -> list[tuple[float, str]]:
    return [(item.start_ms / 1000, item.content) for item in session.items if item.kind == kind]


def run_ingest_pipeline(
    video_path: Path | str,
    vault_dir: Path | str,
    *,
    title: str = "세션",
    session_start: datetime | None = None,
    participants: Sequence[str] | None = None,
    stt_provider: str = DEFAULT_PROVIDER,
    audio_dir: Path | str | None = None,
    keep_audio: bool = True,
    database_url: str | None = None,
    summary: str | None = None,
    highlights: Sequence[tuple[float, float, str]] = (),
    captions: Sequence[tuple[float, float, str]] = (),
    media_dir: Path | str | None = None,
    extract_keyframes: bool = True,
    caption_provider: str = DEFAULT_CAPTION_PROVIDER,
    summary_provider: str = DEFAULT_SUMMARY_PROVIDER,
    embedding_provider: str = DEFAULT_EMBEDDING_PROVIDER,
) -> IngestResult:
    """영상 파일 → 오디오 추출 → STT 스텁 → 세션 md 생성을 순서대로 실행한다.

    Args:
        video_path: 입력 영상 파일 경로. 이 함수만으로 전체 파이프라인이
            돌아가야 하며, 글래스/컴패니언 앱 연동은 필요 없다.
        vault_dir: Obsidian 볼트 루트 디렉토리 (`sessions/`가 이 아래 생성됨).
        title: 세션 제목 (파일명 `YYYY-MM-DD_HHMM_제목.md`에 사용).
        session_start: 세션 시작 시각. 생략 시 현재 시각(`datetime.now()`).
            리허설에서 "어제" 세션을 재현할 때 전날 날짜를 명시적으로
            넘기는 용도로도 쓸 수 있다 (데모_시나리오.md 리스크 항목 대응).
        participants: 참석자 표기 목록. 생략 시 전사록에서 감지된 화자
            라벨(화자1, 화자2, ...)을 그대로 사용한다 (이름 매핑은 이후 단계).
        stt_provider: "rtzr"(기본, 1순위) 또는 "clova"(2순위) 스텁 선택.
        audio_dir: 추출된 오디오(.wav)를 저장할 디렉토리. 생략 시 영상과
            같은 디렉토리에 저장한다.
        keep_audio: False면 세션 md 생성 후 추출된 오디오 파일을 삭제한다.
        database_url: PostgreSQL DSN. 지정하면 DB에 원본을 저장하고 다시 읽어
            Markdown과 pgvector 검색 색인을 만든다. 생략하면 기존 파일 모드.
        summary: 세션 전체 LLM 요약. 생략하면(또는 빈 문자열이면) 전사록+캡션을
            바탕으로 `summary_provider`(기본 Gemini)가 자동으로 만든다.
            GEMINI_API_KEY가 없거나 호출이 실패하면 `ingest/wiki/session_md.py`의
            기존 TODO 플레이스홀더로 대체된다.
        highlights: ``(시작 초, 종료 초, 설명)`` 주요 순간 목록.
        captions: ``(시작 초, 종료 초, 설명)`` VLM 장면 캡션 목록. 생략하면(빈
            시퀀스) `extract_keyframes`로 뽑은 키프레임마다 `caption_provider`
            (기본 Gemini)가 직전 전사록을 컨텍스트로 넣어 한국어 장면 캡션을
            자동으로 만든다. GEMINI_API_KEY가 없거나 호출이 실패하면 키프레임
            이미지를 참조하는 placeholder 캡션(TODO 문구 포함)으로 대체된다.
        media_dir: 키프레임 이미지를 저장할 디렉토리. 생략 시 `vault_dir/media`.
        extract_keyframes: False면 `ingest/visual.py`의 사건 경계 탐지·키프레임
            저장을 건너뛴다(오디오/STT만 실행 — 이 경우 캡션도 자동으로 비게 된다).
        caption_provider: VLM 캡션 provider 이름(기본 "gemini").
        summary_provider: LLM 요약 provider 이름(기본 "gemini").
        embedding_provider: DB 검색 색인 provider 이름(기본 "gemini").

    Returns:
        IngestResult: 생성된 세션 md 경로 등 실행 결과.
    """
    # video_path는 이 세션 md의 frontmatter `video:` 필드로 그대로 기록되며,
    # recall-dev의 fallback(영상 클립 재조회)이 나중에 다른 작업 디렉토리에서
    # 이 경로로 파일을 다시 열어야 하는 wiki-builder<->recall-dev 계약이다.
    # 상대경로/CWD 의존 경로가 그대로 저장되면 재조회가 깨지므로 절대경로로
    # 고정한다.
    video_path = Path(video_path).resolve()
    vault_dir = Path(vault_dir)
    resolved_start = session_start or datetime.now()

    audio_output_path: Path | None = None
    if audio_dir is not None:
        audio_dir = Path(audio_dir)
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_output_path = audio_dir / f"{video_path.stem}.wav"

    extracted: ExtractedAudio = extract_audio(video_path, audio_output_path)

    stt_client = get_stt_client(stt_provider)
    try:
        transcript = stt_client.transcribe(extracted.path)
    except RTZRAPIError as exc:
        # RTZR 인증 정보는 있지만 API 호출 자체가 실패한 경우(네트워크 문제,
        # 429/5xx 재시도 소진, RTZR 서버가 전사 실패를 반환, 폴링 타임아웃 등).
        # 인증 정보가 아예 없는 경우와 동일하게 "전체 실행 중단·세션 md 미생성"
        # 대신 스텁 품질 전사록으로 대체해 데모가 끝까지 진행되게 한다.
        print(
            "[경고] RTZR API 호출이 실패해 이번 세션은 실제 STT 대신 "
            "화자1/화자2 더미 스텁 전사록으로 대체합니다 (실제 발화 내용이 "
            f"아니므로 신뢰하지 마세요): {exc}\n"
            "        RTZR API가 정상화되면 이 영상으로 다시 실행해 재처리하는 "
            "것을 권장합니다.",
            file=sys.stderr,
        )
        transcript = RTZRStubClient().transcribe(extracted.path)

    resolved_participants = list(participants) if participants else (transcript.speakers or ["화자1"])

    # 세션 md 파일명과 같은 이름을 미디어 서브디렉토리로 써서, 서로 다른 세션의
    # 키프레임이 같은 media_dir 아래에서도 파일명 충돌 없이 저장되게 한다.
    media_slug = Path(session_filename(resolved_start, title)).stem
    resolved_media_dir = Path(media_dir) if media_dir is not None else vault_dir / "media"

    if extract_keyframes:
        try:
            visual_result = process_video(
                video_path,
                media_dir=resolved_media_dir,
                session_id=media_slug,
            )
        except VideoOpenError as exc:
            # STT의 RTZRAPIError 처리와 같은 원칙 — 영상 처리 하나가 실패해도
            # 세션 md 생성 자체는 끝까지 진행시킨다(경고만 출력).
            print(
                f"[경고] 키프레임 추출에 실패해 이번 세션은 장면 캡션 이미지 없이 진행합니다: {exc}",
                file=sys.stderr,
            )
            visual_result = VisualProcessingResult(session_duration_sec=extracted.duration_sec)
    else:
        visual_result = VisualProcessingResult(session_duration_sec=extracted.duration_sec)

    resolved_captions = resolve_captions(
        captions,
        visual_result.processed_keyframes,
        transcript,
        media_slug=media_slug,
        caption_provider=caption_provider,
    )
    resolved_summary = resolve_summary(
        summary,
        transcript,
        resolved_captions,
        resolved_participants,
        summary_provider=summary_provider,
    )

    session_id: str | None = None
    transcript_path: Path | None = None
    database_fallback = False
    if database_url:
        try:
            from recall.index.postgres_store import index_markdown_file

            database = WikiDatabase(database_url)
            database.initialize()
            session_id = str(uuid4())
            transcript_path = (
                vault_dir.resolve().parent / "data" / "sessions" / session_id / "transcript.json"
            )
            _write_transcript_json(transcript_path, transcript)

            stored_session = StoredSession(
                session_id=session_id,
                title=title,
                started_at=resolved_start,
                ended_at=resolved_start + timedelta(seconds=transcript.duration_sec),
                participants=tuple(resolved_participants),
                video_path=str(video_path),
                transcript_path=str(transcript_path),
                markdown_path=None,
                stt_provider=transcript.provider,
                status="processing",
                items=_memory_items(transcript, resolved_summary, highlights, resolved_captions),
            )
            database.save_session(stored_session)

            # DB가 원본이다. 바로 위의 로컬 객체를 재사용하지 않고 DB에서 다시
            # 읽은 값으로 Markdown을 만들어 저장 방향(DB -> md)을 고정한다.
            stored_session = database.load_session(session_id)
            stored_transcript = _transcript_from_session(stored_session)
            db_summary = next(
                (item.content for item in stored_session.items if item.kind == "summary"),
                None,
            )
            session_md_path = write_session_md(
                vault_dir,
                session_start=stored_session.started_at,
                session_end=stored_session.ended_at,
                title=stored_session.title,
                participants=stored_session.participants,
                video_path=stored_session.video_path,
                transcript=stored_transcript,
                session_id=stored_session.session_id,
                transcript_path=stored_session.transcript_path,
                summary=db_summary,
                highlights=_timed_items(stored_session, "highlight"),
                captions=_timed_items(stored_session, "caption"),
            )
            database.set_session_output(session_id, str(session_md_path.resolve()), "processing")
            index_markdown_file(
                database,
                vault_dir,
                session_md_path,
                session_id=session_id,
                embedding_provider=embedding_provider,
            )
            database.set_session_output(session_id, str(session_md_path.resolve()), "ready")
        except psycopg.OperationalError as exc:
            # PostgreSQL 연결 자체가 안 되는 경우(서버 미기동, 네트워크 문제 등).
            # RTZR API 실패와 동일한 원칙 — 데모 도중 전체 실행이 죽는 대신
            # 기존 파일 모드로 계속 진행한다(경고 메시지 출력, 나중에 재처리 권장).
            print(
                "[경고] PostgreSQL 연결에 실패해 이번 세션은 DB 대신 기존 "
                f"파일 모드로 대체합니다: {exc}\n"
                "        DB가 복구되면 이 영상으로 다시 실행해 재처리하는 "
                "것을 권장합니다.",
                file=sys.stderr,
            )
            database_url = None
            session_id = None
            transcript_path = None
            database_fallback = True

    if not database_url:
        session_md_path = write_session_md(
            vault_dir,
            session_start=resolved_start,
            title=title,
            participants=resolved_participants,
            video_path=video_path,
            transcript=transcript,
            summary=resolved_summary,
            highlights=[(start_sec, text) for start_sec, _end_sec, text in highlights],
            captions=[(start_sec, text) for start_sec, _end_sec, text in resolved_captions],
        )

    if not keep_audio:
        extracted.path.unlink(missing_ok=True)

    return IngestResult(
        video_path=video_path,
        audio_path=extracted.path,
        session_md_path=session_md_path,
        stt_provider=transcript.provider,
        transcript=transcript,
        visual=visual_result,
        session_id=session_id,
        transcript_path=transcript_path,
        database_fallback=database_fallback,
    )
