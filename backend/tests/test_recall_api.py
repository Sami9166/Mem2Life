from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recall.api import create_app
from recall.pipeline import RecallPipeline


@pytest.fixture
def client(mock_vault_dir: Path, tmp_path: Path) -> TestClient:
    pipeline = RecallPipeline(mock_vault_dir, cache_path=tmp_path / "cache.json", embedding_provider="hash")
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
    assert "서랍" in body["answer_text"]
    assert body["citations"]
    assert body["fallback"]["triggered"] is False

    # 음성과 화면은 더 이상 같은 문자열이 아니다 — answer_text에는 인용 표기가
    # 붙지만 TTS로 읽을 문장에는 붙으면 안 된다("괄호 근거 세션 …"으로 읽힌다).
    assert "(근거:" in body["answer_text"]
    assert "근거:" not in body["tts_text"]
    assert body["tts_text"] == body["glass"]["tts_text"]
    assert "서랍" in body["tts_text"]


def test_query_endpoint_returns_glass_payload(client: TestClient) -> None:
    """앱(Blade 2)이 실제로 소비할 필드 — 이것만 보고 화면·음성을 만들 수 있어야 한다."""
    resp = client.post(
        "/recall/query",
        json={"question": "충전기 어디에 넣었지?", "reference_date": "2026-07-18"},
    )
    glass = resp.json()["glass"]

    assert glass["status"] == "answered"
    assert glass["status_label"] == "기록 확인됨"
    assert "근거:" not in glass["display_text"]
    # 480x480 화면이라 근거는 2건까지만, 라벨은 상대 시각으로 짧게.
    assert len(glass["evidence"]) <= 2
    assert glass["evidence"], "근거 타임스탬프 표시는 CLAUDE.md 필수 요구사항"
    # 벽시계 시각이 아니라 영상 오프셋으로 표기한다(볼트마다 timestamp_label
    # 관례가 달라서 — recall/present/glass.py의 `_offset_phrase` 주석 참고).
    assert glass["evidence"][0]["label"].startswith("어제 1분")
    assert "제주도_여행_계획" in glass["evidence"][0]["label"]
    assert glass["evidence"][0]["video_link"]


def test_query_endpoint_not_found_hides_internal_reasoning(client: TestClient) -> None:
    """근거를 못 찾았을 때 내부 판정 사유가 사용자 문구로 새면 안 된다.

    질문은 `test_recall_grounding_safety.py`가 "볼트 어휘와 전혀 겹치지 않는다"고
    이미 고정해둔 것을 그대로 쓴다(바이그램 우연 매칭으로 grounded가 되는 질문을
    쓰면 이 테스트가 의도와 다른 이유로 통과/실패한다).
    """
    resp = client.post(
        "/recall/query",
        json={"question": "오뚜기 진짬뽕 맛있어?", "reference_date": "2026-07-18"},
    )
    glass = resp.json()["glass"]

    assert glass["status"] == "not_found"
    for leaked in ("Gemini", "fallback", "재조회", "불충분"):
        assert leaked not in glass["tts_text"], f"내부 구현 용어 '{leaked}'가 음성으로 나가면 안 된다"
        assert leaked not in glass["display_text"]
    assert glass["evidence"] == []


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
