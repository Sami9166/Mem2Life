from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recall.api import create_app
from recall.pipeline import RecallPipeline


@pytest.fixture
def client(mock_vault_dir: Path, tmp_path: Path) -> TestClient:
    pipeline = RecallPipeline(mock_vault_dir, cache_path=tmp_path / "cache.json")
    app = create_app(pipeline)
    return TestClient(app)


def test_query_endpoint_returns_tts_and_display_fields(client: TestClient) -> None:
    resp = client.post(
        "/recall/query",
        json={
            "question": "충전기 어디에 넣었지?",
            "reference_date": "2026-07-18",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "tts_text" in body
    assert "answer_text" in body
    assert body["tts_text"] == body["answer_text"]  # fallback 미발동 시 동일
    assert "서랍" in body["answer_text"]
    assert body["citations"]
    assert body["fallback"]["triggered"] is False


def test_query_endpoint_visual_question_triggers_fallback(client: TestClient) -> None:
    resp = client.post(
        "/recall/query",
        json={
            "question": "어제 민수가 보여준 책 제목이 뭐였지?",
            "reference_date": "2026-07-18",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["question_type"] == "visual"
    assert body["fallback"]["triggered"] is True
    assert "기록에 없음" in body["answer_text"]


def test_query_endpoint_missing_question_is_422(client: TestClient) -> None:
    resp = client.post("/recall/query", json={})
    assert resp.status_code == 422


def test_health_endpoint_reports_file_mode_when_no_database_configured(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["index_mode"] == "file"
    assert body["database_fallback"] is False
