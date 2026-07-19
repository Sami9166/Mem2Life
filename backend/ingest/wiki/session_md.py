"""Obsidian 볼트 `sessions/YYYY-MM-DD_HHMM_제목.md` 생성.

CLAUDE.md / 기술조사_의사결정.md 조사 6의 세션 로그 스키마를 그대로 따른다:

    ---
    date: 2026-07-17
    time: 10:30-11:05
    participants: ["[[화자1]]", "[[화자2]]"]
    video: "/path/to/video.mp4"
    ---
    ## 요약
    ## 주요 순간
    ## 전사록
    ## 장면 캡션

원칙(전사록 전문 보존): `## 전사록` 섹션은 STT 결과의 요약이 아니라 전문을
`[HH:MM:SS] 화자: 발화` 형식으로 모두 담는다. 이번 단계(1단계 프로토타입)는
VLM 캡션·LLM 요약이 없으므로 `## 요약` / `## 주요 순간` / `## 장면 캡션`은
TODO 플레이스홀더로 남긴다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

from ..stt.base import Transcript

_TODO_SUMMARY = "TODO: LLM 요약 — 다음 단계(요약·엔티티 갱신)에서 채워진다."
_TODO_HIGHLIGHTS = "TODO: VLM 캡션 기반 주요 순간 추출 — 다음 단계에서 채워진다."
_TODO_SCENE_CAPTIONS = "TODO: VLM 키프레임 캡션 — 다음 단계에서 채워진다."

_TITLE_SANITIZE_RE = re.compile(r"[\\/:*?\"<>|\s]+")


def _yaml_double_quoted(value: str) -> str:
    """값을 YAML 이중따옴표(double-quoted) 스칼라로 안전하게 escape한다.

    영상 경로나 참석자 이름에 콜론(`:`), 따옴표(`"`), 역슬래시(`\\`) 등
    YAML 특수문자가 섞여 있어도 frontmatter 전체가 깨지지 않도록
    (예: `/Movies/test: session #1.mp4` -> `yaml.safe_load` ScannerError)
    이중따옴표 스칼라 규칙에 따라 escape한다.
    """
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return f'"{escaped}"'


def sanitize_title(title: str) -> str:
    """파일명에 안전하도록 제목의 공백/구분자를 밑줄로 치환한다."""
    cleaned = _TITLE_SANITIZE_RE.sub("_", title.strip())
    cleaned = cleaned.strip("_")
    return cleaned or "세션"


def session_filename(session_start: datetime, title: str) -> str:
    """`YYYY-MM-DD_HHMM_제목.md` 형식의 파일명을 만든다."""
    date_part = session_start.strftime("%Y-%m-%d_%H%M")
    return f"{date_part}_{sanitize_title(title)}.md"


def _format_frontmatter(
    *,
    session_start: datetime,
    session_end: datetime,
    participants: Sequence[str],
    video_path: str | Path,
) -> str:
    date_str = session_start.strftime("%Y-%m-%d")
    time_str = f"{session_start.strftime('%H:%M')}-{session_end.strftime('%H:%M')}"
    participants_str = ", ".join(_yaml_double_quoted(f"[[{name}]]") for name in participants)
    video_str = _yaml_double_quoted(str(video_path))
    lines = [
        "---",
        f"date: {date_str}",
        f"time: {time_str}",
        f"participants: [{participants_str}]",
        f"video: {video_str}",
        "---",
    ]
    return "\n".join(lines)


def _format_transcript_section(transcript: Transcript) -> str:
    if not transcript.segments:
        return "(전사록 없음 — STT 결과가 비어 있습니다)"
    lines = [
        f"{segment.timestamp_label} {segment.speaker}: {segment.text}" for segment in transcript.segments
    ]
    return "\n".join(lines)


def build_session_markdown(
    *,
    session_start: datetime,
    session_end: datetime | None = None,
    participants: Sequence[str],
    video_path: str | Path,
    transcript: Transcript,
) -> str:
    """세션 md 본문 문자열을 만든다 (파일 쓰기는 하지 않음 — 테스트하기 쉽게 분리).

    Args:
        session_start: 세션 시작 시각.
        session_end: 세션 종료 시각. 생략 시 `session_start` + 전사록 길이로 추정한다.
        participants: 참석자 표기 목록 (예: ["화자1", "화자2"] 또는 실제 이름).
            `[[위키링크]]`는 이 함수가 자동으로 감싸주므로 대괄호 없이 넘긴다.
        video_path: 원본 영상 경로 (fallback 재조회용, frontmatter에 그대로 기록).
        transcript: STT 전문 결과 (요약본 아님).
    """
    if session_end is None:
        session_end = session_start + timedelta(seconds=transcript.duration_sec)

    frontmatter = _format_frontmatter(
        session_start=session_start,
        session_end=session_end,
        participants=participants,
        video_path=video_path,
    )
    transcript_section = _format_transcript_section(transcript)

    body = f"""{frontmatter}
## 요약

{_TODO_SUMMARY}

## 주요 순간

{_TODO_HIGHLIGHTS}

## 전사록

{transcript_section}

## 장면 캡션

{_TODO_SCENE_CAPTIONS}
"""
    return body


def write_session_md(
    vault_dir: Path,
    *,
    session_start: datetime,
    title: str,
    participants: Sequence[str],
    video_path: str | Path,
    transcript: Transcript,
    session_end: datetime | None = None,
) -> Path:
    """세션 md를 `vault_dir/sessions/YYYY-MM-DD_HHMM_제목.md`에 생성하고 경로를 반환한다."""
    vault_dir = Path(vault_dir)
    sessions_dir = vault_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    markdown = build_session_markdown(
        session_start=session_start,
        session_end=session_end,
        participants=participants,
        video_path=video_path,
        transcript=transcript,
    )

    out_path = sessions_dir / session_filename(session_start, title)
    out_path.write_text(markdown, encoding="utf-8")
    return out_path
