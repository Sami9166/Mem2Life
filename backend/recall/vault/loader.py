"""Obsidian 볼트를 읽기 전용으로 스캔해 `VaultDocument` 목록을 만든다.

중요: 이 모듈은 절대 vault 파일에 쓰기 작업을 하지 않는다 (recall-dev
담당 범위 원칙 — wiki-builder와의 계약은 읽기 전용). 파일 변경 감지는
`index/store.py`가 mtime/해시로 담당하고, 이 로더는 "현재 시점 볼트
스냅샷"을 만드는 순수 함수 역할만 한다.
"""

from __future__ import annotations

import re
from datetime import date as date_type
from pathlib import Path

from .frontmatter import split_frontmatter
from .types import DocKind, VaultDocument

_SESSION_FILENAME_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{4})_(?P<title>.+)$")

_DIR_TO_KIND = {
    "sessions": DocKind.SESSION,
    "people": DocKind.PEOPLE,
    "topics": DocKind.TOPIC,
    "daily": DocKind.DAILY,
}


def iter_vault_md_files(vault_dir: Path) -> list[Path]:
    """볼트 하위 4개 디렉토리(sessions/people/topics/daily)의 .md 파일 경로 목록.

    존재하지 않는 디렉토리는 조용히 건너뛴다 (아직 아무 세션도 없는 초기
    상태의 볼트도 에러 없이 다뤄야 한다).
    """
    paths: list[Path] = []
    for dirname in _DIR_TO_KIND:
        subdir = vault_dir / dirname
        if not subdir.is_dir():
            continue
        paths.extend(sorted(subdir.glob("*.md")))
    return paths


def _doc_kind_for(path: Path, vault_dir: Path) -> DocKind:
    rel_parts = path.relative_to(vault_dir).parts
    if not rel_parts:
        raise ValueError(f"볼트 바깥 경로입니다: {path}")
    kind = _DIR_TO_KIND.get(rel_parts[0])
    if kind is None:
        raise ValueError(f"알 수 없는 볼트 하위 디렉토리입니다: {rel_parts[0]}")
    return kind


def _parse_date(value: object) -> date_type | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date_type.fromisoformat(value)
    except ValueError:
        return None


def _derive_title(path: Path, kind: DocKind, frontmatter: dict[str, object]) -> str:
    stem = path.stem
    if kind is DocKind.SESSION:
        match = _SESSION_FILENAME_RE.match(stem)
        if match:
            return match.group("title")
        return stem
    if kind is DocKind.DAILY:
        return str(frontmatter.get("date", stem))
    return stem  # people/topics: 파일명이 곧 이름


def parse_document(path: Path, vault_dir: Path, raw_text: str) -> VaultDocument:
    """파일 또는 DB에서 읽은 Markdown을 `VaultDocument`로 변환한다."""
    kind = _doc_kind_for(path, vault_dir)
    frontmatter, _ = split_frontmatter(raw_text)

    doc_date: date_type | None
    if kind in (DocKind.SESSION, DocKind.DAILY):
        doc_date = _parse_date(frontmatter.get("date"))
        if doc_date is None and kind is DocKind.SESSION:
            # frontmatter가 없거나 날짜 파싱 실패 시 파일명에서 폴백 추출
            match = _SESSION_FILENAME_RE.match(path.stem)
            if match:
                doc_date = _parse_date(match.group("date"))
        if doc_date is None and kind is DocKind.DAILY:
            doc_date = _parse_date(path.stem)
    else:
        doc_date = None

    title = _derive_title(path, kind, frontmatter)
    rel_path = path.relative_to(vault_dir)

    return VaultDocument(
        path=rel_path,
        kind=kind,
        frontmatter=frontmatter,
        raw_text=raw_text,
        date=doc_date,
        title=title,
    )


def load_document(path: Path, vault_dir: Path) -> VaultDocument:
    """md 파일 하나를 읽어 `VaultDocument`로 변환한다."""
    return parse_document(path, vault_dir, path.read_text(encoding="utf-8"))


def load_vault_documents(vault_dir: Path) -> list[VaultDocument]:
    """볼트 전체를 스캔해 `VaultDocument` 목록을 반환한다 (정렬: 경로순)."""
    vault_dir = Path(vault_dir)
    if not vault_dir.is_dir():
        raise FileNotFoundError(f"볼트 디렉토리를 찾을 수 없습니다: {vault_dir}")
    return [load_document(path, vault_dir) for path in iter_vault_md_files(vault_dir)]
