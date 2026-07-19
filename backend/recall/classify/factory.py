"""질문 분류기 provider 선택 팩토리 (`ingest/stt/factory.py`와 동일 패턴).

지금은 `"keyword"`(오프라인 휴리스틱) 하나만 등록돼 있다. 데모 이후
정확도가 부족하면 LLM 기반 분류기를 추가하고 기본값만 바꾸면 된다.
"""

from __future__ import annotations

from .question_type import KeywordQuestionClassifier, QuestionClassifier

_PROVIDERS: dict[str, type[QuestionClassifier]] = {
    "keyword": KeywordQuestionClassifier,
}

DEFAULT_PROVIDER = "keyword"


def get_question_classifier(provider: str = DEFAULT_PROVIDER) -> QuestionClassifier:
    try:
        client_cls = _PROVIDERS[provider.lower()]
    except KeyError as exc:
        available = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"알 수 없는 질문 분류기 provider: {provider!r} (사용 가능: {available})") from exc
    return client_cls()
