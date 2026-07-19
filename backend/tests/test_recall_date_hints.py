from __future__ import annotations

from datetime import date

from recall.search.date_hints import resolve_query_date

_TODAY = date(2026, 7, 18)


def test_resolve_today_keyword() -> None:
    assert resolve_query_date("오늘 뭐 했지?", _TODAY) == _TODAY


def test_resolve_yesterday_keyword() -> None:
    assert resolve_query_date("어제 민수랑 무슨 얘기했지?", _TODAY) == date(2026, 7, 17)


def test_resolve_informal_recency_keyword_아까() -> None:
    """Q1 문구('아까 민수가 나한테 부탁한 거 뭐였지?')를 위한 구어체 표현."""
    assert resolve_query_date("아까 민수가 나한테 부탁한 거 뭐였지?", _TODAY) == _TODAY


def test_resolve_no_hint_returns_none() -> None:
    assert resolve_query_date("충전기 어디에 넣었지?", _TODAY) is None


def test_resolve_absolute_month_day() -> None:
    assert resolve_query_date("7월 17일에 뭐 했지?", _TODAY) == date(2026, 7, 17)


def test_resolve_absolute_iso_date() -> None:
    assert resolve_query_date("2026-07-17에 무슨 일 있었지?", _TODAY) == date(2026, 7, 17)
