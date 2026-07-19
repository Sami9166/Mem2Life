"""세션/daily md의 YAML-유사 frontmatter를 파싱한다.

wiki-builder 쪽 스키마(`ingest/wiki/session_md.py`)가 만드는 frontmatter는
아래처럼 아주 제한된 형태만 쓴다:

    ---
    date: 2026-07-17
    time: 15:00-15:03
    participants: ["[[민수]]"]
    video: testdata/videos/test_session_A_20260717.mp4
    ---

이 제한된 형태만 지원하면 되므로 PyYAML 의존성을 추가하는 대신, "key: value"
줄 파서 + 리스트 값(`[...]`)은 JSON으로 파싱하는 가벼운 전용 파서를 둔다
(참고: `["[[민수]]"]`는 문법상 유효한 JSON이기도 하다). 정식 YAML 문법
전체를 지원하지 않는다 — 볼트 스키마가 바뀌면 이 파서도 같이 갱신해야 한다.
"""

from __future__ import annotations

import json

_DELIM = "---"


def split_frontmatter(raw_text: str) -> tuple[dict[str, object], str]:
    """`raw_text`를 (frontmatter dict, frontmatter 이후 본문)으로 나눈다.

    frontmatter가 없는 파일(예: 향후 추가될 문서 종류)이면 빈 dict와 원문
    전체를 그대로 반환한다.
    """
    lines = raw_text.splitlines()
    if not lines or lines[0].strip() != _DELIM:
        return {}, raw_text

    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == _DELIM:
            end_idx = idx
            break
    if end_idx is None:
        return {}, raw_text

    fm_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1 :]).lstrip("\n")
    return _parse_lines(fm_lines), body


def _parse_lines(lines: list[str]) -> dict[str, object]:
    result: dict[str, object] = {}
    for line in lines:
        if not line.strip() or ":" not in line:
            continue
        key, _, raw_value = line.partition(":")
        key = key.strip()
        value = raw_value.strip()
        result[key] = _parse_value(value)
    return result


def _parse_value(value: str) -> object:
    if value.startswith("[") and value.endswith("]"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            # 콤마 구분 단순 목록으로 폴백 (따옴표 없는 경우 대비)
            inner = value[1:-1].strip()
            if not inner:
                return []
            return [item.strip().strip('"').strip("'") for item in inner.split(",")]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
