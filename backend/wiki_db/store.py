"""PostgreSQL에 세션 원본과 Markdown 검색 색인을 저장한다.

저장 경계는 단순하다.

* ``sessions`` / ``memory_items``: STT·VLM·LLM이 만든 구조화된 원본
* ``wiki_documents``: 원본에서 렌더링한 Obsidian Markdown
* ``search_items``: Markdown을 검색 단위로 나눈 pgvector 색인

Markdown과 검색 색인은 모두 원본에서 다시 만들 수 있다. 이 모듈은 ORM이나
마이그레이션 프레임워크를 두지 않고 PostgreSQL/pgvector가 제공하는 기능을
그대로 사용한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

VECTOR_DIM = 768  # Gemini Embedding 2의 명시적 output_dimensionality와 일치한다.

SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY,
    title TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    participants TEXT[] NOT NULL DEFAULT '{{}}',
    video_path TEXT NOT NULL,
    transcript_path TEXT NOT NULL,
    markdown_path TEXT,
    stt_provider TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'processing'
        CHECK (status IN ('processing', 'ready', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memory_items (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('summary', 'highlight', 'transcript', 'caption')),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    content TEXT NOT NULL CHECK (content <> ''),
    speaker TEXT,
    start_ms BIGINT NOT NULL CHECK (start_ms >= 0),
    end_ms BIGINT CHECK (end_ms IS NULL OR end_ms >= start_ms),
    UNIQUE (session_id, kind, ordinal)
);

CREATE TABLE IF NOT EXISTS wiki_documents (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    vault_path TEXT NOT NULL,
    file_path TEXT NOT NULL,
    kind TEXT NOT NULL,
    markdown_content TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (vault_path, file_path)
);

CREATE TABLE IF NOT EXISTS search_items (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES wiki_documents(id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB NOT NULL,
    embedding vector({VECTOR_DIM}) NOT NULL,
    embedding_model TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_id)
);

DO $$
DECLARE
    current_dim INTEGER;
BEGIN
    SELECT atttypmod INTO current_dim
    FROM pg_attribute
    WHERE attrelid = 'search_items'::regclass
      AND attname = 'embedding'
      AND NOT attisdropped;

    IF current_dim IS NOT NULL AND current_dim <> {VECTOR_DIM} THEN
        -- search_items는 Markdown에서 다시 만드는 파생 색인이므로 구형 벡터만 제거한다.
        DELETE FROM search_items;
        ALTER TABLE search_items
            ALTER COLUMN embedding TYPE vector({VECTOR_DIM})
            USING embedding::vector({VECTOR_DIM});
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'memory_items'::regclass
          AND conname = 'memory_items_kind_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%highlight%'
    ) THEN
        ALTER TABLE memory_items DROP CONSTRAINT memory_items_kind_check;
        ALTER TABLE memory_items ADD CONSTRAINT memory_items_kind_check
            CHECK (kind IN ('summary', 'highlight', 'transcript', 'caption'));
    END IF;
END $$;
"""

_MEMORY_KINDS = {"summary", "highlight", "transcript", "caption"}


@dataclass(frozen=True, slots=True)
class MemoryItem:
    """세션에 속한 요약·발화·캡션 한 건과 영상 기준 시간 범위."""

    kind: str
    ordinal: int
    content: str
    start_ms: int
    end_ms: int | None = None
    speaker: str | None = None


@dataclass(frozen=True, slots=True)
class StoredSession:
    """DB에서 Markdown 생성에 필요한 세션 원본을 모두 읽은 결과."""

    session_id: str
    title: str
    started_at: datetime
    ended_at: datetime | None
    participants: tuple[str, ...]
    video_path: str
    transcript_path: str
    markdown_path: str | None
    stt_provider: str
    status: str
    items: tuple[MemoryItem, ...]


@dataclass(frozen=True, slots=True)
class SearchItem:
    """Markdown에서 파생돼 pgvector에 저장되는 검색 근거 한 건."""

    chunk_id: str
    content: str
    metadata: dict[str, object]
    embedding: Sequence[float]
    embedding_model: str
    content_hash: str


def _load_psycopg() -> tuple[Any, Any]:
    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError as exc:  # pragma: no cover - 설치 오류 안내 경계
        raise RuntimeError("PostgreSQL 사용에는 `uv sync`로 psycopg를 설치해야 합니다.") from exc
    return psycopg, Jsonb


def _vector_literal(values: Sequence[float]) -> str:
    if len(values) != VECTOR_DIM:
        raise ValueError(f"임베딩 차원은 {VECTOR_DIM}이어야 합니다: {len(values)}")
    return "[" + ",".join(format(float(value), ".9g") for value in values) + "]"


def _validate_memory_items(items: Sequence[MemoryItem]) -> None:
    for item in items:
        if item.kind not in _MEMORY_KINDS:
            raise ValueError(f"지원하지 않는 memory item 종류입니다: {item.kind}")
        if item.ordinal < 0 or item.start_ms < 0:
            raise ValueError("ordinal과 start_ms는 음수일 수 없습니다.")
        if item.end_ms is not None and item.end_ms < item.start_ms:
            raise ValueError("end_ms는 start_ms보다 빠를 수 없습니다.")
        if not item.content.strip():
            raise ValueError("memory item 내용은 비어 있을 수 없습니다.")


class WikiDatabase:
    """짧은 트랜잭션마다 연결하는 PostgreSQL 저장소.

    ponytail: 현재는 세션 종료·질의 시점의 낮은 빈도만 다룬다. 연결 비용이
    측정될 만큼 커지면 그때 connection pool을 추가한다.
    """

    def __init__(self, database_url: str) -> None:
        if not database_url.strip():
            raise ValueError("database_url은 비어 있을 수 없습니다.")
        self.database_url = database_url

    def _connect(self):
        psycopg, _ = _load_psycopg()
        return psycopg.connect(self.database_url)

    def initialize(self) -> None:
        """필요한 테이블과 pgvector 인덱스를 없을 때만 생성한다."""
        with self._connect() as connection:
            connection.execute(SCHEMA_SQL)

    def save_session(self, session: StoredSession) -> None:
        """세션 메타데이터와 구조화된 기억 항목을 한 트랜잭션으로 저장한다."""
        _validate_memory_items(session.items)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    id, title, started_at, ended_at, participants, video_path,
                    transcript_path, markdown_path, stt_provider, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    started_at = EXCLUDED.started_at,
                    ended_at = EXCLUDED.ended_at,
                    participants = EXCLUDED.participants,
                    video_path = EXCLUDED.video_path,
                    transcript_path = EXCLUDED.transcript_path,
                    markdown_path = EXCLUDED.markdown_path,
                    stt_provider = EXCLUDED.stt_provider,
                    status = EXCLUDED.status,
                    updated_at = now()
                """,
                (
                    session.session_id,
                    session.title,
                    session.started_at,
                    session.ended_at,
                    list(session.participants),
                    session.video_path,
                    session.transcript_path,
                    session.markdown_path,
                    session.stt_provider,
                    session.status,
                ),
            )
            connection.execute("DELETE FROM memory_items WHERE session_id = %s", (session.session_id,))
            if session.items:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO memory_items (
                            session_id, kind, ordinal, content, speaker, start_ms, end_ms
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        [
                            (
                                session.session_id,
                                item.kind,
                                item.ordinal,
                                item.content.strip(),
                                item.speaker,
                                item.start_ms,
                                item.end_ms,
                            )
                            for item in session.items
                        ],
                    )

    def load_session(self, session_id: str) -> StoredSession:
        """DB 원본을 읽어 Markdown 렌더링 입력으로 반환한다."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, title, started_at, ended_at, participants, video_path,
                       transcript_path, markdown_path, stt_provider, status
                FROM sessions
                WHERE id = %s
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"세션을 찾을 수 없습니다: {session_id}")
            item_rows = connection.execute(
                """
                SELECT kind, ordinal, content, start_ms, end_ms, speaker
                FROM memory_items
                WHERE session_id = %s
                ORDER BY kind, ordinal
                """,
                (session_id,),
            ).fetchall()

        items = tuple(
            MemoryItem(
                kind=item[0],
                ordinal=item[1],
                content=item[2],
                start_ms=item[3],
                end_ms=item[4],
                speaker=item[5],
            )
            for item in item_rows
        )
        return StoredSession(
            session_id=str(row[0]),
            title=row[1],
            started_at=row[2],
            ended_at=row[3],
            participants=tuple(row[4]),
            video_path=row[5],
            transcript_path=row[6],
            markdown_path=row[7],
            stt_provider=row[8],
            status=row[9],
            items=items,
        )

    def set_session_output(self, session_id: str, markdown_path: str | None, status: str) -> None:
        if status not in {"processing", "ready", "failed"}:
            raise ValueError(f"지원하지 않는 세션 상태입니다: {status}")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET markdown_path = COALESCE(%s, markdown_path), status = %s, updated_at = now()
                WHERE id = %s
                """,
                (markdown_path, status, session_id),
            )

    def replace_document(
        self,
        *,
        session_id: str | None,
        vault_path: str,
        file_path: str,
        kind: str,
        markdown_content: str,
        content_hash: str,
        items: Sequence[SearchItem],
    ) -> None:
        """Markdown 본문과 그 검색 색인을 한 트랜잭션으로 교체한다."""
        _, Jsonb = _load_psycopg()
        with self._connect() as connection:
            document_id = connection.execute(
                """
                INSERT INTO wiki_documents (
                    session_id, vault_path, file_path, kind, markdown_content, content_hash
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (vault_path, file_path) DO UPDATE SET
                    session_id = EXCLUDED.session_id,
                    kind = EXCLUDED.kind,
                    markdown_content = EXCLUDED.markdown_content,
                    content_hash = EXCLUDED.content_hash,
                    updated_at = now()
                RETURNING id
                """,
                (session_id, vault_path, file_path, kind, markdown_content, content_hash),
            ).fetchone()[0]
            connection.execute("DELETE FROM search_items WHERE document_id = %s", (document_id,))
            if items:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO search_items (
                            document_id, chunk_id, content, metadata, embedding,
                            embedding_model, content_hash
                        ) VALUES (%s, %s, %s, %s, %s::vector, %s, %s)
                        """,
                        [
                            (
                                document_id,
                                item.chunk_id,
                                item.content,
                                Jsonb(item.metadata),
                                _vector_literal(item.embedding),
                                item.embedding_model,
                                item.content_hash,
                            )
                            for item in items
                        ],
                    )

    def load_search_items(
        self, vault_path: str, embedding_model: str
    ) -> list[tuple[int, str, dict[str, object]]]:
        """볼트 하나의 검색 항목을 DB 행 순서대로 읽는다."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT si.id, si.content, si.metadata
                FROM search_items AS si
                JOIN wiki_documents AS wd ON wd.id = si.document_id
                WHERE wd.vault_path = %s AND si.embedding_model = %s
                ORDER BY si.id
                """,
                (vault_path, embedding_model),
            ).fetchall()
        return [(row[0], row[1], row[2]) for row in rows]

    def load_wiki_documents(self, vault_path: str) -> list[tuple[str, str]]:
        """볼트 하나의 Markdown 문서를 경로순으로 읽는다."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT file_path, markdown_content
                FROM wiki_documents
                WHERE vault_path = %s
                ORDER BY file_path
                """,
                (vault_path,),
            ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def vector_scores(
        self, vault_path: str, item_ids: Sequence[int], query_vector: Sequence[float]
    ) -> dict[int, float]:
        """pgvector cosine similarity를 검색 항목 ID별로 반환한다."""
        if not item_ids:
            return {}
        # ponytail: 데모 규모에서는 전체 후보 exact scan이 가장 단순하고 정확하다.
        # 검색 지연이 측정되면 ORDER BY <=> LIMIT 기반 HNSW 후보 검색으로 바꾼다.
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT si.id, 1 - (si.embedding <=> %s::vector) AS score
                FROM search_items AS si
                JOIN wiki_documents AS wd ON wd.id = si.document_id
                WHERE wd.vault_path = %s AND si.id = ANY(%s)
                """,
                (_vector_literal(query_vector), vault_path, list(item_ids)),
            ).fetchall()
        return {row[0]: float(row[1]) for row in rows}
