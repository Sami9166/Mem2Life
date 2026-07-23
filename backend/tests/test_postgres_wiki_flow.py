from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

import ingest.pipeline as pipeline_module
from ingest.audio import ExtractedAudio
from ingest.pipeline import run_ingest_pipeline
from ingest.stt.base import Transcript, TranscriptSegment
from recall.index.postgres_store import PostgresIndex, index_markdown_file
from wiki_db import StoredSession
from wiki_db.store import VECTOR_DIM, _vector_literal


class _MemoryWikiDatabase:
    """실제 PostgreSQL 없이 DB → Markdown → 색인 순서만 검증한다."""

    last: _MemoryWikiDatabase | None = None

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.session: StoredSession | None = None
        self.outputs: list[tuple[str | None, str]] = []
        self.document: dict | None = None
        type(self).last = self

    def initialize(self) -> None:
        pass

    def save_session(self, session: StoredSession) -> None:
        self.session = session

    def load_session(self, session_id: str) -> StoredSession:
        assert self.session is not None and self.session.session_id == session_id
        return self.session

    def set_session_output(self, session_id: str, markdown_path: str | None, status: str) -> None:
        assert self.session is not None and self.session.session_id == session_id
        self.outputs.append((markdown_path, status))

    def replace_document(self, **values) -> None:
        self.document = values


def test_ingest_db_flow_reads_db_before_markdown_and_indexes_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = tmp_path / "session.mp4"
    audio = tmp_path / "session.wav"
    video.write_bytes(b"fake video")
    audio.write_bytes(b"fake audio")
    transcript = Transcript(
        segments=[TranscriptSegment(0.0, 5.0, "민수", "숙소는 서귀포로 알아보자.")],
        provider="test-stt",
    )

    monkeypatch.setattr(pipeline_module, "WikiDatabase", _MemoryWikiDatabase)
    monkeypatch.setattr(
        pipeline_module,
        "extract_audio",
        lambda video_path, output_path: ExtractedAudio(audio, 16_000, 1, 5.0),
    )
    monkeypatch.setattr(
        pipeline_module,
        "get_stt_client",
        lambda provider: type("_Client", (), {"transcribe": lambda self, path: transcript})(),
    )

    result = run_ingest_pipeline(
        video,
        tmp_path / "vault",
        title="DB 세션",
        session_start=datetime(2026, 7, 22, 14, 0),
        database_url="postgresql://test",
        summary="민수와 제주도 여행 계획을 논의했다.",
        highlights=[(1.0, 2.0, "제주도 여행 출발일을 확정했다.")],
        captions=[(2.0, 5.0, "제주도 여행 책자가 보인다.")],
    )

    database = _MemoryWikiDatabase.last
    assert database is not None and database.session is not None
    assert result.session_id == database.session.session_id
    assert result.transcript_path is not None and result.transcript_path.exists()
    assert {item.kind for item in database.session.items} == {
        "summary",
        "highlight",
        "transcript",
        "caption",
    }
    assert database.outputs[-1][1] == "ready"
    assert database.document is not None
    assert database.document["session_id"] == result.session_id
    assert database.document["items"]

    markdown = result.session_md_path.read_text(encoding="utf-8")
    assert f'session_id: "{result.session_id}"' in markdown
    assert "transcript:" in markdown
    assert "민수와 제주도 여행 계획을 논의했다." in markdown
    assert "제주도 여행 출발일을 확정했다." in markdown
    assert "제주도 여행 책자가 보인다." in markdown


def test_index_markdown_file_creates_all_session_vectors(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    sessions = vault / "sessions"
    sessions.mkdir(parents=True)
    path = sessions / "2026-07-22_1400_여행.md"
    path.write_text(
        """---
date: 2026-07-22
time: 14:00-14:10
participants: ["[[민수]]"]
video: "video.mp4"
---
## 요약

민수와 제주도 여행을 논의했다.

## 주요 순간

- [00:01:02] 제주도 여행 출발일을 확정했다.

## 전사록

[00:01:00] 민수: 숙소는 서귀포로 알아보자.

## 장면 캡션

- [00:01:05] 제주도 여행 책자가 보인다.
""",
        encoding="utf-8",
    )
    database = _MemoryWikiDatabase("postgresql://test")

    count = index_markdown_file(database, vault, path, session_id="session-1")

    assert count == 4
    assert database.document is not None
    items = database.document["items"]
    assert {item.metadata["level"] for item in items} == {
        "session_summary",
        "highlight",
        "transcript",
        "scene_caption",
    }
    assert all(len(item.embedding) == VECTOR_DIM for item in items)


def test_vector_literal_rejects_schema_dimension_mismatch() -> None:
    assert _vector_literal([0.0] * VECTOR_DIM).startswith("[")
    with pytest.raises(ValueError, match="임베딩 차원"):
        _vector_literal([0.0])


@pytest.mark.skipif(
    not os.getenv("MEM2LIFE_TEST_DATABASE_URL"),
    reason="실제 PostgreSQL 통합 테스트 DSN이 없습니다.",
)
def test_real_postgres_ingest_markdown_index_and_search(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """실제 DB에 저장한 원본으로 Markdown을 만들고 pgvector 검색까지 확인한다."""
    import psycopg

    database_url = os.environ["MEM2LIFE_TEST_DATABASE_URL"]
    video = tmp_path / "session.mp4"
    audio = tmp_path / "session.wav"
    vault = tmp_path / "vault"
    video.write_bytes(b"fake video")
    audio.write_bytes(b"fake audio")
    transcript = Transcript(
        segments=[
            TranscriptSegment(
                0.0,
                5.0,
                "Min",
                "Book a Seogwipo lodging near the market.",
            )
        ],
        provider="test-stt",
    )

    monkeypatch.setattr(
        pipeline_module,
        "extract_audio",
        lambda video_path, output_path: ExtractedAudio(audio, 16_000, 1, 5.0),
    )
    monkeypatch.setattr(
        pipeline_module,
        "get_stt_client",
        lambda provider: type("_Client", (), {"transcribe": lambda self, path: transcript})(),
    )

    result = run_ingest_pipeline(
        video,
        vault,
        title="PostgreSQL smoke",
        session_start=datetime(2026, 7, 22, 14, 0),
        database_url=database_url,
        summary="Min plans a Seogwipo lodging trip.",
        highlights=[(1.0, 2.0, "Min confirms the Seogwipo lodging plan.")],
        captions=[(2.0, 5.0, "A Seogwipo travel guide is visible.")],
    )

    assert result.session_id is not None
    try:
        with psycopg.connect(database_url) as connection:
            session = connection.execute(
                "SELECT status, transcript_path, markdown_path FROM sessions WHERE id = %s",
                (result.session_id,),
            ).fetchone()
            kinds = connection.execute(
                "SELECT kind FROM memory_items WHERE session_id = %s ORDER BY kind",
                (result.session_id,),
            ).fetchall()
            document = connection.execute(
                """
                SELECT wd.markdown_content, count(si.id), min(vector_dims(si.embedding))
                FROM wiki_documents AS wd
                JOIN search_items AS si ON si.document_id = wd.id
                WHERE wd.session_id = %s
                GROUP BY wd.id
                """,
                (result.session_id,),
            ).fetchone()

        assert session is not None and session[0] == "ready"
        assert Path(session[1]).exists() and Path(session[2]).exists()
        assert {row[0] for row in kinds} == {
            "summary",
            "highlight",
            "transcript",
            "caption",
        }
        assert document is not None
        assert "Seogwipo lodging" in document[0]
        assert document[1] >= 4
        assert document[2] == VECTOR_DIM

        index = PostgresIndex(database_url, vault)
        index.refresh()
        evidence = index.search("Seogwipo lodging", top_k=3)
        assert evidence
        assert "Seogwipo lodging" in evidence[0].chunk.text
        assert evidence[0].vector_score > 0
    finally:
        with psycopg.connect(database_url) as connection:
            connection.execute("DELETE FROM sessions WHERE id = %s", (result.session_id,))
