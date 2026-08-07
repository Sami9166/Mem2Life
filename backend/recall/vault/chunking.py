"""`VaultDocument` → 검색 인덱스용 `Chunk` 목록 변환.

EgoRAG coarse-to-fine 층위(기술조사_의사결정.md 조사 6)에 맞춰 세션 문서를
쪼갠다:

    daily/*.md      → ChunkLevel.DAILY        (1개, "## 요약" 섹션 전체)
    sessions/*.md   → ChunkLevel.SESSION_SUMMARY (1개, "## 요약")
                    → ChunkLevel.HIGHLIGHT       (줄 단위, "## 주요 순간")
                    → ChunkLevel.TRANSCRIPT      (줄 단위, "## 전사록")
                    → ChunkLevel.SCENE_CAPTION   (줄 단위, "## 장면 캡션")
    people/*.md     → ChunkLevel.ENTITY        (섹션 단위)
    topics/*.md     → ChunkLevel.ENTITY        (섹션 단위)

원칙: 전사록/캡션은 "줄 단위"로 쪼개야 세밀(fine) 검색에서 정확한 타임스탬프를
근거로 돌려줄 수 있다 (CLAUDE.md "모든 답변에 근거 타임스탬프 포함" 원칙).
TODO 플레이스홀더 섹션(`ingest/wiki/session_md.py`가 아직 채우지 못한 절
— 예: "TODO: LLM 요약...")은 실제 근거가 아니므로 인덱싱에서 제외한다.
"""

from __future__ import annotations

import re

from .types import Chunk, ChunkLevel, DocKind, VaultDocument

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
_TIMESTAMP_RE = re.compile(r"\[(\d{2}):(\d{2}):(\d{2})\]")
_TRANSCRIPT_LINE_RE = re.compile(
    r"^\[(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})\]\s*(?P<speaker>[^:]+):\s*(?P<text>.+)$"
)
_TODO_MARKER = "TODO"
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def _strip_wikilinks(text: str) -> str:
    """`[[민수]]`(또는 `[[민수|표시명]]`) → 표시 텍스트만 남긴다.

    청크 본문은 Obsidian 렌더링용이 아니라 검색·TTS·화면 인용문 조립용이므로
    이중 대괄호를 그대로 남기면(예: "[[민수]]가 짧게...") 답변 문장이
    어색해지고 BM25/임베딩에도 불필요한 토큰이 섞인다. 원본 md 파일은
    건드리지 않는다 — 청킹 시점의 인메모리 텍스트만 변환한다.
    """
    return _WIKILINK_RE.sub(r"\1", text)


def _split_sections(body: str) -> list[tuple[str, str]]:
    """`## 섹션명` 헤더 기준으로 (섹션명, 섹션 본문) 목록을 만든다."""
    sections: list[tuple[str, list[str]]] = []
    current_name: str | None = None
    current_lines: list[str] = []
    for line in body.splitlines():
        match = _SECTION_RE.match(line)
        if match:
            if current_name is not None:
                sections.append((current_name, current_lines))
            current_name = match.group(1)
            current_lines = []
        else:
            current_lines.append(line)
    if current_name is not None:
        sections.append((current_name, current_lines))
    return [(name, "\n".join(lines).strip()) for name, lines in sections]


def _hhmmss_to_sec(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s)


def _parse_session_start_sec(time_range: str | None) -> float | None:
    """frontmatter `time: "15:00-15:03"`의 시작 시각을 초로 변환."""
    if not time_range or "-" not in time_range:
        return None
    start_str = time_range.split("-", 1)[0].strip()
    if ":" not in start_str:
        return None
    try:
        hh, mm = start_str.split(":")
        return int(hh) * 3600 + int(mm) * 60
    except ValueError:
        return None


def _to_video_offset(timestamp_sec: float, session_start_sec: float | None) -> float:
    """Markdown 타임스탬프를 영상 시작 기준 초로 통일한다.

    기존 손작성 볼트는 ``[15:00:20]`` 같은 실제 시각을, ingest가 만드는
    Markdown은 ``[00:00:20]`` 같은 영상 기준 시각을 사용한다. 세션 시작보다
    이른 값은 영상 기준으로 보고 그대로 두면 두 형식을 모두 안전하게 읽는다.
    """
    if session_start_sec is not None and timestamp_sec >= session_start_sec:
        return timestamp_sec - session_start_sec
    return timestamp_sec


def _is_placeholder(text: str) -> bool:
    return _TODO_MARKER in text


_VIDEO_LINK_SUFFIX_RE = re.compile(r"[—\-]\s*video@[\d:]+\s*$")


def _clean_bullet_text(line: str) -> str:
    """`[HH:MM:SS] 설명 — video@mm:ss` 줄에서 타임스탬프/영상 링크 접미사를
    제거해 검색·답변 생성에 쓸 순수 설명문만 남긴다. 타임스탬프는 별도
    필드(`timestamp_label`/`start_sec`)로 이미 구조화돼 있으므로 본문에서는
    빼는 편이 BM25/임베딩 노이즈도 줄고 답변 문장 조립도 자연스럽다."""
    text = _TIMESTAMP_RE.sub("", line, count=1).strip()
    text = _VIDEO_LINK_SUFFIX_RE.sub("", text).strip()
    return _strip_wikilinks(text)


def _bullet_lines(section_body: str) -> list[str]:
    lines = []
    for raw_line in section_body.splitlines():
        line = raw_line.strip()
        if line.startswith("- "):
            lines.append(line[2:].strip())
        elif line:
            # 불릿 없이 이어지는 줄바꿈은 이전 항목에 붙임(긴 캡션 등 대비)
            if lines:
                lines[-1] = f"{lines[-1]} {line}"
    return lines


def _make_chunk_id(doc_path, *parts: str) -> str:
    suffix = "#".join(parts)
    return f"{doc_path.as_posix()}#{suffix}" if suffix else doc_path.as_posix()


def chunk_session_document(doc: VaultDocument) -> list[Chunk]:
    _, body = _split_body(doc)
    sections = dict(_split_sections(body))

    session_title = doc.title
    time_range = doc.frontmatter.get("time") if isinstance(doc.frontmatter.get("time"), str) else None
    video_path = doc.frontmatter.get("video") if isinstance(doc.frontmatter.get("video"), str) else None
    session_start_sec = _parse_session_start_sec(time_range)

    chunks: list[Chunk] = []

    summary = sections.get("요약", "")
    if summary and not _is_placeholder(summary):
        chunks.append(
            Chunk(
                chunk_id=_make_chunk_id(doc.path, "요약"),
                doc_path=doc.path,
                doc_kind=DocKind.SESSION,
                level=ChunkLevel.SESSION_SUMMARY,
                text=_strip_wikilinks(summary),
                date=doc.date,
                session_title=session_title,
                session_time_range=time_range,
                video_path=video_path,
            )
        )

    highlights = sections.get("주요 순간", "")
    if highlights and not _is_placeholder(highlights):
        for idx, line in enumerate(_bullet_lines(highlights)):
            ts_match = _TIMESTAMP_RE.search(line)
            start_sec = None
            if ts_match:
                timestamp_sec = _hhmmss_to_sec(*ts_match.groups())
                start_sec = _to_video_offset(timestamp_sec, session_start_sec)
            chunks.append(
                Chunk(
                    chunk_id=_make_chunk_id(doc.path, "주요순간", str(idx)),
                    doc_path=doc.path,
                    doc_kind=DocKind.SESSION,
                    level=ChunkLevel.HIGHLIGHT,
                    text=_clean_bullet_text(line),
                    date=doc.date,
                    session_title=session_title,
                    session_time_range=time_range,
                    start_sec=start_sec,
                    timestamp_label=ts_match.group(0) if ts_match else None,
                    video_path=video_path,
                )
            )

    transcript = sections.get("전사록", "")
    if transcript and not _is_placeholder(transcript):
        for idx, raw_line in enumerate(transcript.splitlines()):
            line = raw_line.strip()
            if not line:
                continue
            match = _TRANSCRIPT_LINE_RE.match(line)
            if not match:
                continue
            abs_sec = _hhmmss_to_sec(match.group("h"), match.group("m"), match.group("s"))
            start_sec = _to_video_offset(abs_sec, session_start_sec)
            chunks.append(
                Chunk(
                    chunk_id=_make_chunk_id(doc.path, "전사록", str(idx)),
                    doc_path=doc.path,
                    doc_kind=DocKind.SESSION,
                    level=ChunkLevel.TRANSCRIPT,
                    text=f"{match.group('speaker').strip()}: {match.group('text').strip()}",
                    date=doc.date,
                    session_title=session_title,
                    session_time_range=time_range,
                    start_sec=start_sec,
                    timestamp_label=f"[{match.group('h')}:{match.group('m')}:{match.group('s')}]",
                    speaker=match.group("speaker").strip(),
                    video_path=video_path,
                )
            )

    scene_captions = sections.get("장면 캡션", "")
    if scene_captions and not _is_placeholder(scene_captions):
        for idx, line in enumerate(_bullet_lines(scene_captions)):
            ts_match = _TIMESTAMP_RE.search(line)
            start_sec = None
            if ts_match:
                timestamp_sec = _hhmmss_to_sec(*ts_match.groups())
                start_sec = _to_video_offset(timestamp_sec, session_start_sec)
            chunks.append(
                Chunk(
                    chunk_id=_make_chunk_id(doc.path, "장면캡션", str(idx)),
                    doc_path=doc.path,
                    doc_kind=DocKind.SESSION,
                    level=ChunkLevel.SCENE_CAPTION,
                    text=_clean_bullet_text(line),
                    date=doc.date,
                    session_title=session_title,
                    session_time_range=time_range,
                    start_sec=start_sec,
                    timestamp_label=ts_match.group(0) if ts_match else None,
                    video_path=video_path,
                )
            )

    return chunks


def chunk_daily_document(doc: VaultDocument) -> list[Chunk]:
    _, body = _split_body(doc)
    sections = dict(_split_sections(body))
    summary = sections.get("요약", "")
    if not summary or _is_placeholder(summary):
        return []
    return [
        Chunk(
            chunk_id=_make_chunk_id(doc.path, "요약"),
            doc_path=doc.path,
            doc_kind=DocKind.DAILY,
            level=ChunkLevel.DAILY,
            text=_strip_wikilinks(summary),
            date=doc.date,
        )
    ]


def chunk_entity_document(doc: VaultDocument) -> list[Chunk]:
    """people/topics 문서 — 섹션 단위로 ENTITY 청크를 만든다."""
    _, body = _split_body(doc)
    sections = _split_sections(body)
    chunks: list[Chunk] = []
    if not sections:
        # 섹션 헤더가 없는 문서는 본문 전체를 하나의 청크로
        if body.strip():
            chunks.append(
                Chunk(
                    chunk_id=_make_chunk_id(doc.path),
                    doc_path=doc.path,
                    doc_kind=doc.kind,
                    level=ChunkLevel.ENTITY,
                    text=_strip_wikilinks(body.strip()),
                    date=None,
                )
            )
        return chunks

    for name, section_body in sections:
        if not section_body or _is_placeholder(section_body):
            continue
        chunks.append(
            Chunk(
                chunk_id=_make_chunk_id(doc.path, name),
                doc_path=doc.path,
                doc_kind=doc.kind,
                level=ChunkLevel.ENTITY,
                text=_strip_wikilinks(f"[{doc.title} — {name}]\n{section_body}"),
                date=None,
            )
        )
    return chunks


def _split_body(doc: VaultDocument) -> tuple[dict[str, object], str]:
    from .frontmatter import split_frontmatter

    return split_frontmatter(doc.raw_text)


def chunk_document(doc: VaultDocument) -> list[Chunk]:
    """문서 종류에 따라 알맞은 청킹 함수로 위임한다."""
    if doc.kind is DocKind.SESSION:
        return chunk_session_document(doc)
    if doc.kind is DocKind.DAILY:
        return chunk_daily_document(doc)
    if doc.kind in (DocKind.PEOPLE, DocKind.TOPIC):
        return chunk_entity_document(doc)
    raise ValueError(f"알 수 없는 문서 종류: {doc.kind}")


def chunk_documents(docs: list[VaultDocument]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for doc in docs:
        chunks.extend(chunk_document(doc))
    return chunks
