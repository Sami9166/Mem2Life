"""PostgreSQL/pgvector를 사용하는 Markdown 검색 인덱스.

Markdown의 섹션 분리와 BM25는 기존 구현을 그대로 재사용한다. 달라지는 것은
벡터와 검색 항목의 보관 장소뿐이다. Markdown 한 파일을 통째로 임베딩하지
않고 기존 ``chunk_document()``가 만든 요약·발화·캡션 단위로 저장한다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from wiki_db import SearchItem, WikiDatabase

from ..vault.chunking import chunk_document
from ..vault.loader import load_document
from .bm25 import BM25Index
from .embeddings.base import EmbeddingClient
from .embeddings.factory import DEFAULT_PROVIDER, get_embedding_client
from .store import RefreshStats, VaultIndex, _chunk_from_dict, _chunk_to_dict
from .tokenize import tokenize
from .vector_store import VectorStore


def index_markdown_file(
    database: WikiDatabase,
    vault_dir: Path | str,
    markdown_path: Path | str,
    *,
    session_id: str | None = None,
    embedding_provider: str = DEFAULT_PROVIDER,
    embedding_client: EmbeddingClient | None = None,
) -> int:
    """Markdown 파일 하나를 파싱해 DB 문서와 pgvector 색인을 교체한다."""
    vault_dir = Path(vault_dir).resolve()
    markdown_path = Path(markdown_path).resolve()
    document = load_document(markdown_path, vault_dir)
    chunks = chunk_document(document)
    client = embedding_client or get_embedding_client(embedding_provider)
    embeddings = client.embed([chunk.text for chunk in chunks], task="document") if chunks else []

    items: list[SearchItem] = []
    for chunk, embedding in zip(chunks, embeddings, strict=True):
        metadata = _chunk_to_dict(chunk, embedding=[])
        metadata.pop("embedding")
        items.append(
            SearchItem(
                chunk_id=chunk.chunk_id,
                content=chunk.text,
                metadata=metadata,
                embedding=embedding,
                embedding_model=getattr(client, "model", embedding_provider),
                content_hash=hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
            )
        )

    markdown = markdown_path.read_text(encoding="utf-8")
    database.replace_document(
        session_id=session_id,
        vault_path=str(vault_dir),
        file_path=markdown_path.relative_to(vault_dir).as_posix(),
        kind=document.kind.value,
        markdown_content=markdown,
        content_hash=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        items=items,
    )
    return len(items)


class PostgresIndex(VaultIndex):
    """기존 coarse-to-fine 계약을 유지하면서 벡터 점수만 pgvector에서 읽는다."""

    def __init__(
        self,
        database_url: str,
        vault_dir: Path | str,
        *,
        embedding_provider: str = DEFAULT_PROVIDER,
        embedding_client: EmbeddingClient | None = None,
    ) -> None:
        super().__init__(
            vault_dir,
            embedding_provider=embedding_provider,
            embedding_client=embedding_client,
        )
        self.vault_dir = self.vault_dir.resolve()
        self.database = WikiDatabase(database_url)
        self.database.initialize()
        self._item_ids: list[int] = []

    def refresh(self) -> RefreshStats:
        """DB에 이미 색인된 Markdown 항목을 BM25 메모리 인덱스와 맞춘다."""
        rows = self.database.load_search_items(str(self.vault_dir), self.embedding_model)
        chunks = []
        item_ids = []
        for item_id, _content, metadata in rows:
            serialized = dict(metadata)
            serialized["embedding"] = []
            chunk, _ = _chunk_from_dict(serialized)
            chunks.append(chunk)
            item_ids.append(item_id)

        self.chunks = chunks
        self._item_ids = item_ids
        self._bm25 = BM25Index()
        self._bm25.fit([tokenize(chunk.text) for chunk in chunks])
        # 부모 클래스의 빌드 여부 확인에만 쓰며 실제 벡터 점수는 DB에서 조회한다.
        self._vector_store = VectorStore(vectors=[])
        return RefreshStats(
            total_chunks=len(chunks),
            changed_files=[],
            removed_files=[],
            unchanged_files=0,
            reembedded_chunks=0,
            reused_chunks=len(chunks),
            skipped_files=[],
        )

    def vector_scores(self, query: str) -> list[float]:
        self._ensure_built()
        if not self._item_ids:
            return []
        query_vector = self.embedding_client.embed([query], task="query")[0]
        scores = self.database.vector_scores(str(self.vault_dir), self._item_ids, query_vector)
        return [scores.get(item_id, 0.0) for item_id in self._item_ids]


def build_postgres_index(
    database_url: str,
    vault_dir: Path | str,
    *,
    embedding_provider: str = DEFAULT_PROVIDER,
) -> PostgresIndex:
    """PostgreSQL 검색 인덱스를 생성하고 현재 DB 내용을 한 번 읽는다."""
    index = PostgresIndex(database_url, vault_dir, embedding_provider=embedding_provider)
    index.refresh()
    return index
