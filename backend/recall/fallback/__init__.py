"""근거 충분성 자기평가 + fallback 트리거 판정.

CLAUDE.md fallback 라우팅: ① 질문 분류(대화형 vs 시각형) ② 텍스트 답변 후
근거 충분성 자기평가 ③ 불충분 시 해당 구간 영상 클립을 Gemini(영상 입력)로
재조회 후 재답변.

이 패키지는 ①②③ 중 ①②와 "③으로 넘어갈지 말지의 판정"까지만 구현한다.
실제 Gemini 영상 재조회 호출은 스텁(`trigger.py`의 `VideoRequeryClient`)
으로 남겨둔다 — 향후 구현 대상.
"""

from __future__ import annotations
