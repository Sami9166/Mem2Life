from __future__ import annotations

from recall.vault.frontmatter import split_frontmatter


def test_split_frontmatter_basic() -> None:
    raw = (
        "---\n"
        "date: 2026-07-17\n"
        "time: 15:00-15:03\n"
        'participants: ["[[민수]]"]\n'
        'video: "testdata/videos/test_session_A_20260717.mp4"\n'
        "---\n"
        "## 요약\n\n본문\n"
    )
    fm, body = split_frontmatter(raw)
    assert fm["date"] == "2026-07-17"
    assert fm["time"] == "15:00-15:03"
    assert fm["participants"] == ["[[민수]]"]
    assert fm["video"] == "testdata/videos/test_session_A_20260717.mp4"
    assert body.startswith("## 요약")


def test_split_frontmatter_no_frontmatter_returns_original_body() -> None:
    raw = "## 요약\n본문만 있음\n"
    fm, body = split_frontmatter(raw)
    assert fm == {}
    assert body == raw


def test_split_frontmatter_time_value_keeps_embedded_colon() -> None:
    """`time: 15:00-15:03`처럼 값 안에도 콜론이 있는 경우 첫 콜론만 구분자로 써야 한다."""
    raw = "---\ntime: 15:00-15:03\n---\n본문\n"
    fm, _ = split_frontmatter(raw)
    assert fm["time"] == "15:00-15:03"
