from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from ingest.cli import main


def test_cli_main_end_to_end(dummy_video: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault_dir = tmp_path / "vault"
    exit_code = main(
        [
            str(dummy_video),
            "--vault",
            str(vault_dir),
            "--title",
            "CLI 테스트",
            "--datetime",
            "2026-07-16T15:00",
            "--participant",
            "민수",
            "--participant",
            "현우",
        ]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "완료" in out

    expected_md = vault_dir / "sessions" / "2026-07-16_1500_CLI_테스트.md"
    assert expected_md.exists()
    content = expected_md.read_text(encoding="utf-8")
    assert 'participants: ["[[민수]]", "[[현우]]"]' in content


def test_cli_missing_video_returns_nonzero(tmp_path: Path) -> None:
    exit_code = main([str(tmp_path / "no_such.mp4")])
    assert exit_code == 1


def test_cli_invalid_datetime_raises_usage_error(dummy_video: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main([str(dummy_video), "--datetime", "not-a-date"])


def test_cli_permission_error_prints_clean_failure_message(
    dummy_video: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """볼트 디렉토리가 쓰기 불가(PermissionError)면 raw traceback이 아니라
    `[실패] ...` 메시지 + exit code 1로 처리돼야 한다 (블로커 2 회귀 테스트).

    FileNotFoundError는 OSError의 서브클래스이지만 PermissionError는
    형제 클래스라, 이전에는 `(FileNotFoundError, ValueError, RuntimeError)`
    로만 잡아서 이 경로가 raw traceback으로 새고 있었다.
    """
    vault_dir = tmp_path / "readonly_vault"
    vault_dir.mkdir()
    read_only_mode = stat.S_IRUSR | stat.S_IXUSR  # 쓰기 권한 제거

    original_mode = vault_dir.stat().st_mode
    os.chmod(vault_dir, read_only_mode)
    try:
        exit_code = main([str(dummy_video), "--vault", str(vault_dir)])
    finally:
        os.chmod(vault_dir, original_mode)  # tmp_path 정리가 실패하지 않도록 복구

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "[실패]" in captured.err
    assert "Traceback" not in captured.err
