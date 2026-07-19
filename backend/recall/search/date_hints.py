"""질문 속 상대적/절대적 날짜 표현을 해석해 daily 검색 창을 좁힌다.

EgoRAG coarse-to-fine 검색의 1단계("daily 요약으로 시간 창 좁히기")를
지원한다. "어제", "오늘"처럼 발화 시점(`reference_date`) 기준 상대
표현과, "7월 17일"/"2026-07-17" 같은 절대 표현을 함께 처리한다.

`reference_date`를 항상 명시적으로 주입받는다(기본값으로 `date.today()`를
쓰지 않음) — 회귀 테스트가 실제 실행 날짜와 무관하게 결정적으로 동작해야
하기 때문이다(모의 볼트의 세션 날짜가 고정돼 있으므로, "오늘"의 의미도
테스트 안에서 고정해야 한다).
"""

from __future__ import annotations

import re
from datetime import date, timedelta

_RELATIVE_DAY_OFFSETS: dict[str, int] = {
    "오늘": 0,
    "아까": 0,  # "아까 민수가 부탁한 거" — 방금 전(=오늘) 대화를 가리키는 구어체 표현
    "방금": 0,
    "조금 전": 0,
    "어제": -1,
    "그제": -2,
    "그저께": -2,
    "내일": 1,
    "모레": 2,
}

_ABS_MONTH_DAY_RE = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_ABS_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def resolve_query_date(question: str, reference_date: date) -> date | None:
    """질문에서 "이 대화가 언제 있었는지"에 대한 날짜 힌트를 뽑는다.

    상대 표현("어제"/"오늘"/"그제"/"내일"/"모레")을 절대 표현보다 우선한다.
    아무 힌트도 없으면 None (coarse 단계에서 날짜로 좁히지 않고 전체
    daily/세션을 대상으로 검색한다).

    주의: "9월 12일 출발" 같은 본문 속 사실(fact) 날짜는 여기서 다루지
    않는다 — 이건 "세션이 언제 있었는지"가 아니라 "세션에서 언급된 날짜"
    이므로 검색 창 좁히기와는 다른 문제다. 이 함수는 오직 발화(세션) 자체의
    시점을 가리키는 표현만 해석한다.
    """
    for keyword, offset in _RELATIVE_DAY_OFFSETS.items():
        if keyword in question:
            return reference_date + timedelta(days=offset)

    iso_match = _ABS_ISO_RE.search(question)
    if iso_match:
        try:
            return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            pass

    md_match = _ABS_MONTH_DAY_RE.search(question)
    if md_match:
        month, day = int(md_match.group(1)), int(md_match.group(2))
        try:
            return date(reference_date.year, month, day)
        except ValueError:
            pass

    return None
