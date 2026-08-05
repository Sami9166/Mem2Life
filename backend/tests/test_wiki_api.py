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


def _make_vault(tmp_path: Path) -> Path:
    sessions = tmp_path / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "2026-07-31_1315_투자_증시_대화.md").write_text(_SESSION_MD, encoding="utf-8")
    return tmp_path


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
