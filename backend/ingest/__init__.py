"""Mem2Life 기록(ingest) 파이프라인.

영상 파일 → 오디오 추출 → STT(화자분리) → Obsidian 세션 md 생성까지의
1단계(백엔드 파이프라인 프로토타입) 구현을 담는다.

이 패키지의 어떤 모듈도 글래스/컴패니언 앱에 의존하지 않는다 — 영상 파일
경로 하나만으로 전체 파이프라인이 단독 실행 가능해야 한다.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
