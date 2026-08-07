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

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .vault.frontmatter import split_frontmatter
from .vault.loader import load_document, load_vault_documents

# `[[대상]]` 또는 `[[대상|표시]]`에서 대상(링크 타깃)만 뽑는다(별칭은 그래프 노드가 아님).
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def _extract_links(text: str) -> list[str]:
    """중복 제거된 위키링크 타깃 목록(등장 순서 유지)."""
    seen: dict[str, None] = {}
    for match in _WIKILINK_RE.finditer(text):
        seen.setdefault(match.group(1).strip(), None)
    return list(seen)


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
    links: list[str]  # 이 페이지가 언급한 위키링크 대상(인물·주제) — 그래프 이웃


class GraphNode(BaseModel):
    id: str  # 세션은 상대경로, 엔티티(인물·주제)는 링크 텍스트
    label: str
    kind: str  # session | daily | entity
    has_file: bool  # 실제 md 파일이 있는 노드인지(엔티티는 대개 False = 가상 집계)


class GraphEdge(BaseModel):
    source: str  # 문서 노드 id(상대경로)
    target: str  # 링크 대상 노드 id


class WikiGraphResponse(BaseModel):
    """볼트 전체 위키링크 그래프 — 노드(문서+엔티티) + 엣지(문서→링크대상)."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]


class EntityMention(BaseModel):
    path: str
    title: str
    date: str | None
    excerpts: list[str]  # 그 엔티티를 언급한 줄(링크 포함 라인)


class WikiEntityResponse(BaseModel):
    """엔티티(인물·주제) 가상 페이지 — 파일이 없어도 백링크로 집계해 보여준다."""

    name: str
    mentioned_in: list[EntityMention]
    related: list[str]  # 같은 문서에서 함께 등장한 다른 엔티티(그래프 이웃)


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
            links=_extract_links(doc.raw_text),
        )

    @router.get("/graph", response_model=WikiGraphResponse)
    def graph() -> WikiGraphResponse:
        """볼트 전체를 훑어 위키링크 그래프를 만든다.

        노드 = 문서(세션 등) + 링크로 언급된 엔티티(인물·주제). 엣지 = 문서→링크대상.
        엔티티 페이지 파일이 아직 없어도(생성 미구현) 링크만으로 그래프가 자란다.
        """
        docs = load_vault_documents(vault_dir)
        file_ids = {doc.path.as_posix() for doc in docs}
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []

        for doc in docs:
            doc_id = doc.path.as_posix()
            nodes[doc_id] = GraphNode(id=doc_id, label=doc.title, kind=str(doc.kind), has_file=True)
            for target in _extract_links(doc.raw_text):
                if target not in nodes:
                    nodes[target] = GraphNode(
                        id=target, label=target, kind="entity", has_file=target in file_ids
                    )
                edges.append(GraphEdge(source=doc_id, target=target))

        return WikiGraphResponse(nodes=list(nodes.values()), edges=edges)

    @router.get("/entity", response_model=WikiEntityResponse)
    def entity(name: str = Query(..., min_length=1, description="인물/주제 이름(위키링크 대상)")) -> WikiEntityResponse:
        """엔티티 가상 페이지 — 이 엔티티를 링크한 문서들과 함께 등장한 다른 엔티티를 집계한다."""
        docs = load_vault_documents(vault_dir)
        mentions: list[EntityMention] = []
        related: dict[str, None] = {}

        for doc in docs:
            links = _extract_links(doc.raw_text)
            if name not in links:
                continue
            for other in links:
                if other != name:
                    related.setdefault(other, None)
            # 이름(또는 [[이름]])이 들어간 줄을 근거 발췌로 모은다.
            excerpts = [
                line.strip()
                for line in doc.raw_text.splitlines()
                if f"[[{name}]]" in line or f"[[{name}|" in line
            ]
            mentions.append(
                EntityMention(
                    path=doc.path.as_posix(),
                    title=doc.title,
                    date=doc.date.isoformat() if doc.date else None,
                    excerpts=excerpts[:3],
                )
            )

        if not mentions:
            raise HTTPException(status_code=404, detail=f"엔티티를 언급한 문서가 없습니다: {name}")
        return WikiEntityResponse(name=name, mentioned_in=mentions, related=list(related))

    return router
