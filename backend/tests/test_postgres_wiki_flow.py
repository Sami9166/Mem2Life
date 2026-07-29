from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

import ingest.pipeline as pipeline_module
from ingest.audio import ExtractedAudio
from ingest.pipeline import run_ingest_pipeline
from ingest.stt.base import Transcript, TranscriptSegment
from ingest.visual import VisualProcessingResult
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
    # 이 테스트는 DB -> Markdown -> 색인 순서만 검증하므로, 실제로 디코딩할
    # 수 없는 가짜 video.mp4(b"fake video")를 열어야 하는 실제 키프레임 추출은
    # extract_audio/get_stt_client와 같은 원칙으로 우회한다.
    monkeypatch.setattr(
        pipeline_module,
        "process_video",
        lambda video_path, *, media_dir, session_id: VisualProcessingResult(session_duration_sec=5.0),
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


# 실제로 연결이 안 되는(닫힌) 포트 — mock이 아니라 psycopg가 진짜
# OperationalError를 던지게 해서 폴백 경로를 검증한다. 실 PostgreSQL 설치는
# 필요 없다(TCP 연결 거부는 즉시 발생하며 네트워크 접근도 없음).
_UNREACHABLE_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:1/mem2life"


def test_run_ingest_pipeline_falls_back_to_file_mode_when_postgres_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PostgreSQL 연결 자체가 실패해도(서버 미기동 등) RTZR API 실패와 동일한
    원칙으로 세션 md 생성까지는 끝까지 진행해야 한다 (블로커 회귀 테스트)."""
    video = tmp_path / "session.mp4"
    audio = tmp_path / "session.wav"
    video.write_bytes(b"fake video")
    audio.write_bytes(b"fake audio")
    transcript = Transcript(
        segments=[TranscriptSegment(0.0, 5.0, "민수", "숙소는 서귀포로 알아보자.")],
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
        tmp_path / "vault",
        title="DB 장애",
        session_start=datetime(2026, 7, 22, 14, 0),
        database_url=_UNREACHABLE_DATABASE_URL,
        summary="민수와 제주도 여행 계획을 논의했다.",
        # 이 테스트는 DB 폴백만 검증한다. video.mp4가 실제 디코딩 가능한
        # 파일이 아니라서(가짜 바이트) 키프레임 추출(ffprobe 실호출)까지
        # 켜두면 무관한 이유로 실패한다.
        extract_keyframes=False,
    )

    assert result.session_md_path.exists()
    assert result.session_id is None  # DB 실패했으니 파일 모드로 대체됨
    assert result.database_fallback is True  # database_url을 아예 안 준 것과 구분되는 신호
    assert "민수와 제주도 여행 계획을 논의했다." in result.session_md_path.read_text(encoding="utf-8")

    warning = capsys.readouterr().err
    assert "[경고]" in warning
    assert "PostgreSQL 연결에 실패" in warning


def test_run_ingest_pipeline_database_fallback_is_false_in_normal_file_mode(
    monkeypatch: pytest.MonkeyPatch,
    dummy_video: Path,
    tmp_path: Path,
) -> None:
    """database_url을 아예 안 준 정상 파일 모드는 database_fallback=False여야
    한다 — True가 "DB를 시도했다가 실패"만을 의미하도록 구분을 지킨다."""
    result = run_ingest_pipeline(dummy_video, tmp_path / "vault")
    assert result.database_fallback is False


def test_recall_pipeline_falls_back_to_file_index_when_postgres_unreachable(
    mock_vault_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`mem2life-recall serve`가 uvicorn 기동 전에(=`RecallPipeline` 생성
    시점에) DB 장애로 죽어버리는 것을 막는다 (블로커 회귀 테스트)."""
    from recall.index.store import VaultIndex
    from recall.pipeline import RecallPipeline

    pipeline = RecallPipeline(
        mock_vault_dir,
        cache_path=tmp_path / "cache.json",
        database_url=_UNREACHABLE_DATABASE_URL,
    )

    assert isinstance(pipeline.index, VaultIndex)
    assert pipeline.index_mode == "file"
    assert pipeline.database_fallback is True
    assert pipeline.database_fallback_detail is not None
    warning = capsys.readouterr().err
    assert "[경고]" in warning
    assert "PostgreSQL 연결에 실패" in warning


def test_recall_pipeline_health_endpoint_reports_fallback_when_postgres_unreachable(
    mock_vault_dir: Path, tmp_path: Path
) -> None:
    """`/health`로 재시작·로그 확인 없이 지금 파일 모드로 대체됐는지 바로
    확인할 수 있어야 한다 (관측 가능성 보완, 블로커 회귀 테스트 연장)."""
    from fastapi.testclient import TestClient

    from recall.api import create_app
    from recall.pipeline import RecallPipeline

    pipeline = RecallPipeline(
        mock_vault_dir,
        cache_path=tmp_path / "cache.json",
        database_url=_UNREACHABLE_DATABASE_URL,
    )
    client = TestClient(create_app(pipeline))

    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["index_mode"] == "file"
    assert body["database_fallback"] is True
    assert body["database_fallback_detail"]


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
    monkeypatch.setattr(
        pipeline_module,
        "process_video",
        lambda video_path, *, media_dir, session_id: VisualProcessingResult(session_duration_sec=5.0),
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
