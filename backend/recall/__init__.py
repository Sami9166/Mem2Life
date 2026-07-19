"""Mem2Life 회상(recall) 파이프라인.

Obsidian 볼트(위키)를 읽기 전용으로 인덱싱하고, 사용자 질문에 근거
타임스탬프를 포함한 한국어 답변을 생성한다. 위키 파일은 wiki-builder가
생성하는 계약(CLAUDE.md의 Obsidian 볼트 스키마)을 그대로 신뢰하며, 이
패키지 어디에서도 vault 파일을 수정하지 않는다.

하위 패키지:
    vault      — md 파싱 + 청크 분할 (읽기 전용 로더)
    index      — BM25 + 벡터 임베딩 하이브리드 인덱스 (증분 갱신)
    search     — coarse-to-fine 검색 (daily → session → transcript)
    classify   — 질문 분류 (대화형 vs 시각형)
    answer     — 근거 기반 답변 생성 (LLM 인터페이스 추상화)
    fallback   — 근거 충분성 자기평가 + fallback 트리거 판정(영상 재조회는 스텁)

최상위 오케스트레이션은 `recall.pipeline.RecallPipeline`을 참고.
"""

from __future__ import annotations
