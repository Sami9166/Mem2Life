"""위키 열람 API(`/wiki/pages`, `/wiki/page`) 테스트.

글래스 앱이 볼트를 브라우징하는 읽기 전용 라우터다. 목록·본문 서빙과
경로 traversal 방어를 검증한다.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from recall.wiki_api import create_wiki_router

_SESSION_MD = """---
date: 2026-07-31
time: 13:15-13:16
participants: ["[[화자1]]"]
video: "/tmp/x.mp4"
---
## 요약

TODO

## 전사록

[00:00:01] 화자1: 안녕하세요.
"""


_SESSION_MD_2 = """---
date: 2026-08-01
participants: ["[[화자1]]"]
video: "/tmp/y.mp4"
---
## 요약

[[화자1]]이 다시 등장했다.
"""


def _make_vault(tmp_path: Path) -> Path:
    sessions = tmp_path / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "2026-07-31_1315_투자_증시_대화.md").write_text(_SESSION_MD, encoding="utf-8")
    return tmp_path


def _make_two_session_vault(tmp_path: Path) -> Path:
    """[[화자1]]이 두 세션 모두에 등장 → 그래프에서 두 세션을 잇는 공유 노드."""
    vault = _make_vault(tmp_path)
    (vault / "sessions" / "2026-08-01_0900_다른_대화.md").write_text(_SESSION_MD_2, encoding="utf-8")
    return vault


def _client(vault_dir: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_wiki_router(vault_dir))
    return TestClient(app)


def test_pages_lists_vault_documents(tmp_path: Path) -> None:
    client = _client(_make_vault(tmp_path))
    resp = client.get("/wiki/pages")
    assert resp.status_code == 200
    pages = resp.json()["pages"]
    assert len(pages) == 1
    page = pages[0]
    assert page["kind"] == "session"
    assert page["title"] == "투자_증시_대화"
    assert page["date"] == "2026-07-31"
    assert page["path"] == "sessions/2026-07-31_1315_투자_증시_대화.md"


def test_page_returns_body_without_frontmatter(tmp_path: Path) -> None:
    client = _client(_make_vault(tmp_path))
    resp = client.get("/wiki/page", params={"path": "sessions/2026-07-31_1315_투자_증시_대화.md"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "투자_증시_대화"
    assert "## 전사록" in data["body"]
    assert "안녕하세요" in data["body"]
    # 프론트매터(date/participants 등)는 본문에서 제거돼야 한다.
    assert "participants:" not in data["body"]


def test_relative_vault_dir_does_not_500(tmp_path: Path, monkeypatch) -> None:
    """상대 경로로 라우터를 만들어도 load_document의 relative_to가 깨지지 않아야 한다
    (vault_dir을 내부에서 resolve하는지 회귀 검증)."""
    _make_vault(tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    client = _client(Path(tmp_path.name))  # 상대 경로
    resp = client.get("/wiki/page", params={"path": "sessions/2026-07-31_1315_투자_증시_대화.md"})
    assert resp.status_code == 200


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    client = _client(_make_vault(tmp_path))
    resp = client.get("/wiki/page", params={"path": "../../../../etc/passwd"})
    assert resp.status_code == 400


def test_missing_page_returns_404(tmp_path: Path) -> None:
    client = _client(_make_vault(tmp_path))
    resp = client.get("/wiki/page", params={"path": "sessions/does_not_exist.md"})
    assert resp.status_code == 404


def test_page_includes_wikilink_targets(tmp_path: Path) -> None:
    client = _client(_make_vault(tmp_path))
    resp = client.get("/wiki/page", params={"path": "sessions/2026-07-31_1315_투자_증시_대화.md"})
    assert "화자1" in resp.json()["links"]


def test_graph_nodes_and_edges(tmp_path: Path) -> None:
    client = _client(_make_two_session_vault(tmp_path))
    graph = client.get("/wiki/graph").json()
    labels = {n["label"]: n for n in graph["nodes"]}
    # 세션 2개 + 엔티티 화자1 노드가 있어야 한다.
    assert labels["투자_증시_대화"]["kind"] == "session"
    assert labels["다른_대화"]["kind"] == "session"
    assert labels["화자1"]["kind"] == "entity"
    assert labels["화자1"]["has_file"] is False
    # 두 세션 모두 화자1로 향하는 엣지가 있어야 한다(공유 노드).
    sources_to_hwaja1 = {e["source"] for e in graph["edges"] if e["target"] == "화자1"}
    assert len(sources_to_hwaja1) == 2


def test_entity_aggregates_backlinks_across_sessions(tmp_path: Path) -> None:
    client = _client(_make_two_session_vault(tmp_path))
    resp = client.get("/wiki/entity", params={"name": "화자1"})
    assert resp.status_code == 200
    data = resp.json()
    titles = {m["title"] for m in data["mentioned_in"]}
    # 화자1은 두 세션 모두에서 언급된다 → 백링크 집계에 둘 다 나온다.
    assert titles == {"투자_증시_대화", "다른_대화"}


def test_entity_without_mentions_returns_404(tmp_path: Path) -> None:
    client = _client(_make_vault(tmp_path))
    resp = client.get("/wiki/entity", params={"name": "존재하지않는주제"})
    assert resp.status_code == 404
