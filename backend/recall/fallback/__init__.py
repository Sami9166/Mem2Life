"""근거 충분성 자기평가 + fallback 트리거 판정.

CLAUDE.md fallback 라우팅: ① 질문 분류(대화형 vs 시각형) ② 텍스트 답변 후
근거 충분성 자기평가 ③ 불충분 시 해당 구간 영상 클립을 Gemini(영상 입력)로
재조회 후 재답변.

구성:
    - `self_assessment.py` / `trigger.py`: ①②와 "③으로 넘어갈지 판정".
    - `gemini_requery.py`: ③의 실제 Gemini 영상 입력 재조회(+`video_clip.py`
      ffmpeg 클립 추출).
    - `factory.py`: provider 선택(기본 "gemini", 키 없으면 스텁 폴백).
    - `trigger.py`의 `StubVideoRequeryClient`: 오프라인/무키 폴백.
"""

from __future__ import annotations
