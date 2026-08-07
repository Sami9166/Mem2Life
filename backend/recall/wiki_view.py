"""PostgreSQL 또는 로컬 Obsidian 볼트를 읽기 전용 웹 Wiki로 노출한다."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel

from wiki_db import WikiDatabase

from .vault.frontmatter import split_frontmatter
from .vault.loader import load_vault_documents, parse_document

_UI_DIR = Path(__file__).with_name("wiki_ui")
_WIKI_LINK_RE = re.compile(r"\[\[([^|\]#]+)(?:#[^|\]]*)?(?:\|[^\]]*)?\]\]")


class WikiDocumentOut(BaseModel):
    path: str
    title: str
    kind: str
    date: str | None
    body: str
    links: list[str]


def wiki_ui_dir() -> Path:
    return _UI_DIR


def _links(body: str) -> list[str]:
    return list(dict.fromkeys(match.strip() for match in _WIKI_LINK_RE.findall(body)))


def create_wiki_router(vault_dir: Path | str, database: WikiDatabase | None = None) -> APIRouter:
    vault_dir = Path(vault_dir).resolve()
    router = APIRouter(tags=["wiki"])

    @router.get("/", include_in_schema=False)
    def wiki_home() -> FileResponse:
        return FileResponse(_UI_DIR / "index.html")

    @router.get("/api/documents", response_model=list[WikiDocumentOut])
    def wiki_documents() -> list[WikiDocumentOut]:
        # ponytail: 개인 Wiki 전체를 한 번에 읽는다. 수천 문서를 넘으면 페이지네이션을 추가한다.
        source = (
            [
                parse_document(vault_dir / path, vault_dir, markdown)
                for path, markdown in database.load_wiki_documents(str(vault_dir))
            ]
            if database
            else load_vault_documents(vault_dir)
        )
        documents = []
        for document in source:
            _, body = split_frontmatter(document.raw_text)
            documents.append(
                WikiDocumentOut(
                    path=document.path.as_posix(),
                    title=document.title,
                    kind=document.kind.value,
                    date=document.date.isoformat() if document.date else None,
                    body=body,
                    links=_links(body),
                )
            )
        return documents

    return router
