"""`VaultIndex`의 증분 갱신(파일 변경 감지 → 변경분만 재계산)을 검증한다.

데모 규모 볼트를 대상으로도 동작하지만, 여기서는 recall-dev 원칙("각 모듈은
단독 테스트 가능")에 맞춰 ingest 없이 손으로 만든 아주 작은 합성 볼트로
증분 갱신 로직 자체만 격리해서 검증한다.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from recall.index.store import VaultIndex, _cosine_to_unit, build_index

_SESSION_A = """---
date: 2026-07-17
time: 15:00-15:03
participants: ["[[민수]]"]
video: "test_a.mp4"
---
## 요약

민수와 여행 이야기를 했다.

## 주요 순간

- [15:00:20] 출발일 확정 — video@00:20

## 전사록

[15:00:00] 민수: 여행 계획 좀 정하자.
[15:00:20] 현우: 9월 12일로 하자.

## 장면 캡션

- [15:00:00] 두 사람이 마주 앉아 있다.
"""

_DAILY_A = """---
date: 2026-07-17
---
## 요약

민수와 여행 이야기를 했다.
"""


def _write_vault(root: Path) -> None:
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    (root / "daily").mkdir(parents=True, exist_ok=True)
    (root / "sessions" / "2026-07-17_1500_여행.md").write_text(_SESSION_A, encoding="utf-8")
    (root / "daily" / "2026-07-17.md").write_text(_DAILY_A, encoding="utf-8")


def test_initial_refresh_reembeds_all_new_files(tmp_path: Path) -> None:
    _write_vault(tmp_path)
    index = VaultIndex(tmp_path, embedding_provider="hash")
    stats = index.refresh()
    assert set(stats.changed_files) == {
        "sessions/2026-07-17_1500_여행.md",
        "daily/2026-07-17.md",
    }
    assert stats.unchanged_files == 0
    assert stats.reembedded_chunks == stats.total_chunks
    assert stats.total_chunks > 0


def test_second_refresh_without_changes_reuses_everything(tmp_path: Path) -> None:
    _write_vault(tmp_path)
    index = VaultIndex(tmp_path, embedding_provider="hash")
    index.refresh()

    stats = index.refresh()
    assert stats.changed_files == []
    assert stats.removed_files == []
    assert stats.unchanged_files == 2
    assert stats.reembedded_chunks == 0
    assert stats.reused_chunks == stats.total_chunks


def test_modifying_one_file_only_reembeds_that_file(tmp_path: Path) -> None:
    _write_vault(tmp_path)
    index = VaultIndex(tmp_path, embedding_provider="hash")
    index.refresh()

    # mtime 해상도(파일시스템에 따라 1초 단위일 수 있음) 문제를 피하기 위해
    # 살짝 기다렸다가 수정한다.
    time.sleep(1.1)
    session_path = tmp_path / "sessions" / "2026-07-17_1500_여행.md"
    session_path.write_text(_SESSION_A.replace("9월 12일", "9월 20일"), encoding="utf-8")

    stats = index.refresh()
    assert stats.changed_files == ["sessions/2026-07-17_1500_여행.md"]
    assert stats.unchanged_files == 1
    assert stats.reembedded_chunks > 0

    transcript_texts = [c.text for c in index.chunks if "9월" in c.text]
    assert any("9월 20일" in t for t in transcript_texts)
    assert not any("9월 12일" in t for t in transcript_texts)


def test_removed_file_drops_its_chunks(tmp_path: Path) -> None:
    _write_vault(tmp_path)
    index = VaultIndex(tmp_path, embedding_provider="hash")
    index.refresh()
    before = len(index.chunks)

    (tmp_path / "daily" / "2026-07-17.md").unlink()
    stats = index.refresh()

    assert stats.removed_files == ["daily/2026-07-17.md"]
    assert len(index.chunks) < before
    assert all(c.doc_path.as_posix() != "daily/2026-07-17.md" for c in index.chunks)


def test_cache_persists_across_process_restarts(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    _write_vault(tmp_path)

    first = build_index(tmp_path, cache_path=cache_path, embedding_provider="hash")
    total_chunks = len(first.chunks)
    assert cache_path.exists()

    # 새 VaultIndex 인스턴스(=프로세스 재시작 시뮬레이션)가 캐시를 읽어
    # 아무것도 바뀌지 않았다면 임베딩을 다시 계산하지 않아야 한다.
    second = VaultIndex(tmp_path, cache_path=cache_path, embedding_provider="hash")
    stats = second.refresh()
    assert stats.reembedded_chunks == 0
    assert stats.total_chunks == total_chunks


def test_search_indices_filter_restricts_candidates(tmp_path: Path) -> None:
    _write_vault(tmp_path)
    index = build_index(tmp_path, embedding_provider="hash")
    from recall.vault.types import ChunkLevel

    transcript_idx = index.indices_for(levels={ChunkLevel.TRANSCRIPT})
    results = index.search("여행 계획", indices=transcript_idx, top_k=5)
    assert results
    assert all(ev.chunk.level is ChunkLevel.TRANSCRIPT for ev in results)


def test_refresh_on_empty_vault_dir_does_not_raise(tmp_path: Path) -> None:
    (tmp_path / "sessions").mkdir()
    index = build_index(tmp_path, embedding_provider="hash")
    assert index.chunks == []
    assert index.search("아무 질문") == []


# ---------------------------------------------------------------------------
# 코사인 -> [0,1] 변환 — 무관한 질의(코사인 ≈ 0)가 인위적인 "바닥 점수"를
# 갖지 않아야 한다 (회귀: 예전에는 (score+1)/2 선형 이동이라 0.5 근방으로
# 뜨는 바닥값이 있었다).
# ---------------------------------------------------------------------------
def test_cosine_to_unit_maps_zero_similarity_to_zero_not_half() -> None:
    assert _cosine_to_unit(0.0) == 0.0


def test_cosine_to_unit_clamps_negative_similarity_to_zero() -> None:
    assert _cosine_to_unit(-0.8) == 0.0


def test_cosine_to_unit_preserves_positive_similarity_unscaled() -> None:
    assert _cosine_to_unit(0.6) == pytest.approx(0.6)
    assert _cosine_to_unit(1.0) == 1.0


# ---------------------------------------------------------------------------
# per-file 격리 — 볼트 안의 파일 하나가 깨져 있어도(인코딩 오류 등) 나머지
# 파일은 계속 인덱싱돼야 하고, 예외가 상위로 전파되면 안 된다(Blocker 2).
# ---------------------------------------------------------------------------
def test_refresh_skips_bad_encoding_file_and_indexes_the_rest(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_vault(tmp_path)
    # UTF-8로 디코딩되지 않는 바이트를 가진 세션 파일을 함께 심는다
    # (재현: `load_document`의 `path.read_text(encoding="utf-8")`가 여기서
    # `UnicodeDecodeError`를 던진다).
    bad_path = tmp_path / "sessions" / "2026-07-18_0900_깨진파일.md"
    bad_path.write_bytes(b"---\ndate: 2026-07-18\n---\n\xff\xfe\x00\x01broken bytes")

    with caplog.at_level(logging.WARNING):
        index = build_index(tmp_path, embedding_provider="hash")

    assert index.chunks, "정상 파일들은 계속 인덱싱돼야 한다"
    assert not any(c.doc_path.as_posix() == "sessions/2026-07-18_0900_깨진파일.md" for c in index.chunks)
    results = index.search("여행 계획")
    assert results, "깨진 파일이 있어도 정상 파일은 여전히 검색 가능해야 한다"

    assert any("건너뜁니다" in record.message for record in caplog.records)
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_refresh_reports_bad_file_in_skipped_files_stat(tmp_path: Path) -> None:
    _write_vault(tmp_path)
    bad_path = tmp_path / "sessions" / "2026-07-18_0900_깨진파일.md"
    bad_path.write_bytes(b"\xff\xfe\x00\x01broken bytes")

    index = VaultIndex(tmp_path, embedding_provider="hash")
    stats = index.refresh()

    assert "sessions/2026-07-18_0900_깨진파일.md" in stats.skipped_files
    assert stats.total_chunks > 0


def test_refresh_does_not_raise_and_server_can_still_start(tmp_path: Path) -> None:
    """`cli.py`의 `_run_serve`/`RecallPipeline` 생성 경로가 볼트 안의 깨진
    파일 하나 때문에 실패하지 않아야 한다(재현: 예전에는 `UnicodeDecodeError`가
    `RecallPipeline.__init__` -> `build_index` -> `refresh()`를 타고 그대로
    올라와 서버가 아예 뜨지 못했다)."""
    from recall.pipeline import RecallPipeline

    _write_vault(tmp_path)
    (tmp_path / "sessions" / "2026-07-18_0900_깨진파일.md").write_bytes(b"\xff\xfe\x00\x01broken bytes")

    pipeline = RecallPipeline(tmp_path, embedding_provider="hash")  # 예외 없이 생성돼야 한다
    stats = pipeline.refresh_index()  # 쿼리마다 다시 호출돼도 계속 예외 없이 동작해야 한다
    assert stats.total_chunks > 0
