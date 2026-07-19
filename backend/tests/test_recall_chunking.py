"""모의 볼트(testdata/mock_vault)를 실제로 로딩·청킹해 스키마 계약을 검증한다."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from recall.vault.chunking import chunk_documents
from recall.vault.loader import load_vault_documents
from recall.vault.types import ChunkLevel, DocKind


def test_load_vault_documents_finds_all_expected_docs(mock_vault_dir: Path) -> None:
    docs = load_vault_documents(mock_vault_dir)
    kinds = {d.kind for d in docs}
    assert kinds == {DocKind.SESSION, DocKind.PEOPLE, DocKind.TOPIC, DocKind.DAILY}

    session_docs = [d for d in docs if d.kind is DocKind.SESSION]
    assert {d.date for d in session_docs} == {date(2026, 7, 17), date(2026, 7, 18)}


def test_session_frontmatter_parses_quoted_video_and_participants(mock_vault_dir: Path) -> None:
    docs = load_vault_documents(mock_vault_dir)
    session_a = next(d for d in docs if d.kind is DocKind.SESSION and d.date == date(2026, 7, 17))
    assert session_a.frontmatter["video"] == "testdata/videos/test_session_A_20260717.mp4"
    assert session_a.frontmatter["participants"] == ["[[민수]]"]
    assert session_a.title == "제주도_여행_계획"


def test_chunk_session_document_produces_all_levels(mock_vault_dir: Path) -> None:
    docs = load_vault_documents(mock_vault_dir)
    session_a = next(d for d in docs if d.kind is DocKind.SESSION and d.date == date(2026, 7, 17))
    chunks = chunk_documents([session_a])
    levels = {c.level for c in chunks}
    assert levels == {
        ChunkLevel.SESSION_SUMMARY,
        ChunkLevel.HIGHLIGHT,
        ChunkLevel.TRANSCRIPT,
        ChunkLevel.SCENE_CAPTION,
    }


def test_transcript_chunks_have_absolute_and_relative_timestamps(mock_vault_dir: Path) -> None:
    docs = load_vault_documents(mock_vault_dir)
    session_a = next(d for d in docs if d.kind is DocKind.SESSION and d.date == date(2026, 7, 17))
    chunks = chunk_documents([session_a])
    budget_line = next(c for c in chunks if c.level is ChunkLevel.TRANSCRIPT and "15만원" in c.text)
    assert budget_line.timestamp_label == "[15:00:50]"
    # 세션 시작(15:00:00) 기준 상대 초 — fallback 영상 클립 offset 계산용
    assert budget_line.start_sec == 50.0
    assert budget_line.speaker == "민수"


def test_book_title_never_appears_in_any_indexable_text(mock_vault_dir: Path) -> None:
    """Q3(fallback) 회귀 테스트가 성립하려면 책 제목이 텍스트 근거 어디에도
    없어야 한다 — 있다면 fallback이 트리거되지 않고 테스트 취지가 깨진다.

    모의 볼트를 저작할 때 애초에 책 제목을 어디에도 적지 않았으므로(전사록에
    말하지 않았고, 장면 캡션도 "기록되지 않음"이라고만 적음), 여기서는 그
    "제목 미확인" 사실이 실제로 텍스트에 남아 있는지만 확인한다.
    """
    docs = load_vault_documents(mock_vault_dir)
    chunks = chunk_documents(docs)
    combined_text = "\n".join(c.text for c in chunks)
    assert "제목은 언급하지 않" in combined_text or "제목은 해상도 문제로" in combined_text
    # "책"이 언급된 모든 청크는 "제목을 모른다"는 취지의 문구를 동반해야
    # 한다 — 그렇지 않다면 어딘가에 실제 제목이 새어 들어간 것일 수 있다.
    book_mention_chunks = [c for c in chunks if "책" in c.text]
    assert book_mention_chunks
    no_title_markers = ("언급하지 않", "기록되지 않", "미확인", "말하지")
    for chunk in book_mention_chunks:
        if "표지" in chunk.text or "권" in chunk.text:
            assert any(marker in chunk.text for marker in no_title_markers), chunk.text


def test_scene_caption_explicitly_marks_title_as_not_captured(mock_vault_dir: Path) -> None:
    docs = load_vault_documents(mock_vault_dir)
    session_a = next(d for d in docs if d.kind is DocKind.SESSION and d.date == date(2026, 7, 17))
    chunks = chunk_documents([session_a])
    caption_chunks = [c for c in chunks if c.level is ChunkLevel.SCENE_CAPTION]
    assert any("기록되지 않" in c.text for c in caption_chunks)


def test_entity_and_daily_chunks_have_wikilinks_stripped(mock_vault_dir: Path) -> None:
    docs = load_vault_documents(mock_vault_dir)
    chunks = chunk_documents(docs)
    for chunk in chunks:
        assert "[[" not in chunk.text and "]]" not in chunk.text
