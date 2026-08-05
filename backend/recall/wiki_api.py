"""위키 열람 API — 볼트(옵시디언 세션/인물/주제 md)를 목록·페이지로 서빙한다.

질의응답(`/recall/query`)과 별개로, 스마트 글래스 앱이 위키 자체를 브라우징할 수
있게 하는 읽기 전용 라우터다. 글래스는 PC의 볼트 파일을 직접 읽을 수 없으므로(HTTP만
가능) 로컬 recall 서버가 이 경로로 내용을 돌려준다. **배포와 무관하며**(로컬 서버에
붙는 라우트일 뿐) 볼트를 파일 그대로 읽는다 — 데모의 파일 모드에 맞춘 것이다.

라우트:
    GET /wiki/pages           → 볼트 문서 목록(경로·종류·제목·날짜)
    GET /wiki/page?path=...    → 한 페이지의 제목/종류/본문(프론트매터 제거)
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .vault.frontmatter import split_frontmatter
from .vault.loader import load_document, load_vault_documents


class WikiPageSummary(BaseModel):
    path: str  # 볼트 루트 기준 상대 경로(POSIX) — /wiki/page의 path 인자로 그대로 쓴다
    kind: str  # sessions | people | topics | daily
    title: str
    date: str | None


class WikiPagesResponse(BaseModel):
    pages: list[WikiPageSummary]


class WikiPageResponse(BaseModel):
    path: str
    kind: str
    title: str
    date: str | None
    body: str  # 프론트매터를 뺀 본문(마크다운). 글래스 앱은 이걸 큰 글씨로 렌더한다.


def _resolve_within_vault(vault_dir: Path, rel_path: str) -> Path:
    """`rel_path`가 볼트 밖(경로 traversal)을 가리키지 않도록 검증해 절대 경로를 만든다."""
    vault_root = vault_dir.resolve()
    candidate = (vault_root / rel_path).resolve()
    if vault_root != candidate and vault_root not in candidate.parents:
        raise HTTPException(status_code=400, detail="볼트 밖 경로는 허용되지 않습니다.")
    if not candidate.is_file() or candidate.suffix != ".md":
        raise HTTPException(status_code=404, detail=f"위키 페이지를 찾을 수 없습니다: {rel_path}")
    return candidate


def create_wiki_router(vault_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/wiki", tags=["wiki"])
    # 절대 경로로 고정한다 — load_document가 문서 경로를 vault_dir 기준 상대경로로
    # 계산할 때 상대/절대가 섞이면 relative_to가 실패하기 때문(상대 vault_dir로
    # serve하면 500). 여기서 한 번 resolve해 두면 /pages·/page 모두 일관된다.
    vault_dir = Path(vault_dir).resolve()

    @router.get("/pages", response_model=WikiPagesResponse)
    def pages() -> WikiPagesResponse:
        docs = load_vault_documents(vault_dir)
        return WikiPagesResponse(
            pages=[
                WikiPageSummary(
                    path=doc.path.as_posix(),
                    kind=str(doc.kind),
                    title=doc.title,
                    date=doc.date.isoformat() if doc.date else None,
                )
                for doc in docs
            ]
        )

    @router.get("/page", response_model=WikiPageResponse)
    def page(path: str = Query(..., description="볼트 루트 기준 상대 경로 (예: sessions/....md)")) -> WikiPageResponse:
        abs_path = _resolve_within_vault(vault_dir, path)
        doc = load_document(abs_path, vault_dir)
        _, body = split_frontmatter(doc.raw_text)
        return WikiPageResponse(
            path=doc.path.as_posix(),
            kind=str(doc.kind),
            title=doc.title,
            date=doc.date.isoformat() if doc.date else None,
            body=body.strip(),
        )

    return router
