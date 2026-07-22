from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

from ingest.stt.base import Transcript, TranscriptSegment
from ingest.wiki.session_md import (
    build_session_markdown,
    sanitize_title,
    session_filename,
    write_session_md,
)


def _parse_frontmatter(md: str) -> dict:
    """md 본문에서 `---`로 감싸인 frontmatter 블록을 뽑아 YAML로 파싱한다."""
    assert md.startswith("---\n")
    _, frontmatter_block, _ = md.split("---", 2)
    return yaml.safe_load(frontmatter_block)


def _sample_transcript() -> Transcript:
    return Transcript(
        segments=[
            TranscriptSegment(0.0, 4.0, "화자1", "민수야, 여행 계획 좀 정하자"),
            # 종료 시각(마지막 end_sec)을 넉넉히 잡아 자동 종료시각 추정(분 단위 롤오버)도 검증한다.
            TranscriptSegment(4.0, 630.0, "화자2", "좋아, 날짜부터 정하자"),
        ],
        provider="rtzr-stub",
    )


def test_sanitize_title_replaces_unsafe_chars() -> None:
    assert sanitize_title("제주도 여행") == "제주도_여행"
    assert sanitize_title("a/b:c") == "a_b_c"
    assert sanitize_title("   ") == "세션"


def test_session_filename_format() -> None:
    dt = datetime(2026, 7, 17, 10, 30)
    assert session_filename(dt, "팀 회의") == "2026-07-17_1030_팀_회의.md"


def test_build_session_markdown_contains_required_sections() -> None:
    dt = datetime(2026, 7, 17, 10, 30)
    md = build_session_markdown(
        session_start=dt,
        participants=["화자1", "화자2"],
        video_path="testdata/sample.mp4",
        transcript=_sample_transcript(),
    )

    assert "date: 2026-07-17" in md
    assert "time: 10:30-10:40" in md  # 마지막 발화 종료(630초=10분30초) -> 종료시각 자동 추정
    assert 'participants: ["[[화자1]]", "[[화자2]]"]' in md
    assert 'video: "testdata/sample.mp4"' in md
    assert "## 요약" in md
    assert "## 전사록" in md
    assert "## 장면 캡션" in md
    assert "[00:00:00] 화자1: 민수야, 여행 계획 좀 정하자" in md
    assert "[00:00:04] 화자2: 좋아, 날짜부터 정하자" in md
    assert "TODO" in md  # 요약/장면캡션은 플레이스홀더


def test_build_session_markdown_explicit_end_time() -> None:
    dt = datetime(2026, 7, 17, 10, 30)
    end = datetime(2026, 7, 17, 11, 5)
    md = build_session_markdown(
        session_start=dt,
        session_end=end,
        participants=["화자1"],
        video_path="x.mp4",
        transcript=_sample_transcript(),
    )
    assert "time: 10:30-11:05" in md


def test_build_session_markdown_renders_db_content_and_paths() -> None:
    dt = datetime(2026, 7, 17, 10, 30)
    md = build_session_markdown(
        session_start=dt,
        participants=["민수"],
        video_path="videos/session.mp4",
        transcript=_sample_transcript(),
        session_id="550e8400-e29b-41d4-a716-446655440000",
        transcript_path="data/sessions/550e8400/transcript.json",
        summary="[[민수]]와 제주도 여행 계획을 논의했다.",
        captions=[(25.0, "테이블 위에 제주도 여행 책자가 놓여 있다.")],
    )

    assert 'session_id: "550e8400-e29b-41d4-a716-446655440000"' in md
    assert 'transcript: "data/sessions/550e8400/transcript.json"' in md
    assert "[[민수]]와 제주도 여행 계획을 논의했다." in md
    assert "- [00:00:25] 테이블 위에 제주도 여행 책자가 놓여 있다." in md


def test_write_session_md_creates_expected_path(tmp_path: Path) -> None:
    dt = datetime(2026, 7, 16, 15, 0)
    path = write_session_md(
        tmp_path,
        session_start=dt,
        title="제주도 여행 상담",
        participants=["화자1", "화자2"],
        video_path="testdata/test_session_A.mp4",
        transcript=_sample_transcript(),
    )

    assert path == tmp_path / "sessions" / "2026-07-16_1500_제주도_여행_상담.md"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert content.startswith("---\ndate: 2026-07-16")


def test_frontmatter_escapes_colon_in_video_path() -> None:
    """콜론이 포함된 영상 경로도 frontmatter를 깨뜨리지 않고 안전하게 파싱돼야 한다.

    (code-reviewer 블로커 1 회귀 테스트: `/Movies/test: session #1 (final).mp4`
    같은 경로가 raw f-string 삽입 시 yaml.safe_load에서 ScannerError를 냈다.)
    """
    dt = datetime(2026, 7, 17, 10, 30)
    tricky_path = "/Movies/test: session #1 (final).mp4"
    md = build_session_markdown(
        session_start=dt,
        participants=["화자1"],
        video_path=tricky_path,
        transcript=_sample_transcript(),
    )

    frontmatter = _parse_frontmatter(md)
    assert frontmatter["video"] == tricky_path
    # YAML은 `date: 2026-07-17` 같은 unquoted 스칼라를 datetime.date로 자동
    # 파싱한다 — frontmatter가 정상 YAML로 파싱됐는지가 핵심이므로 문자열로
    # 변환해 비교한다.
    assert str(frontmatter["date"]) == "2026-07-17"
    assert frontmatter["participants"] == ["[[화자1]]"]


def test_frontmatter_escapes_quote_in_participant_name() -> None:
    """참석자 이름에 이중따옴표가 섞여도 frontmatter가 깨지지 않아야 한다."""
    dt = datetime(2026, 7, 17, 10, 30)
    tricky_name = '민수 "코드네임" 김'
    md = build_session_markdown(
        session_start=dt,
        participants=[tricky_name, "화자2"],
        video_path="x.mp4",
        transcript=_sample_transcript(),
    )

    frontmatter = _parse_frontmatter(md)
    assert frontmatter["participants"] == [f"[[{tricky_name}]]", "[[화자2]]"]


def test_frontmatter_escapes_backslash_in_video_path() -> None:
    """역슬래시(윈도 경로 등)가 섞여도 이중이스케이프 없이 정확히 왕복돼야 한다."""
    dt = datetime(2026, 7, 17, 10, 30)
    tricky_path = r"C:\Users\jade\videos\세션.mp4"
    md = build_session_markdown(
        session_start=dt,
        participants=["화자1"],
        video_path=tricky_path,
        transcript=_sample_transcript(),
    )

    frontmatter = _parse_frontmatter(md)
    assert frontmatter["video"] == tricky_path


def test_transcript_section_empty_placeholder(tmp_path: Path) -> None:
    """전사록이 비어 있으면(발화 0건) '(전사록 없음...)' 플레이스홀더가 렌더링돼야 한다."""
    dt = datetime(2026, 7, 17, 10, 30)
    empty_transcript = Transcript(segments=[], provider="rtzr-stub")

    md = build_session_markdown(
        session_start=dt,
        session_end=datetime(2026, 7, 17, 10, 35),
        participants=["화자1"],
        video_path="x.mp4",
        transcript=empty_transcript,
    )

    assert "(전사록 없음" in md
