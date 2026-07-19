"""답변 생성기 provider 선택 팩토리 (`ingest/stt/factory.py`와 동일 패턴).

지금은 `"template"`(오프라인 조립 스텁) 하나만 등록돼 있다. 실제
Claude/GPT 기반 생성기를 추가할 때는 이 파일의 `_PROVIDERS`에 클래스만
추가하고 기본값을 바꾸면 되며, `pipeline.py`/`fallback/` 쪽 코드는
손댈 필요가 없다.
"""

from __future__ import annotations

from .base import AnswerGenerator
from .template_generator import TemplateAnswerGenerator

_PROVIDERS: dict[str, type[AnswerGenerator]] = {
    "template": TemplateAnswerGenerator,
}

DEFAULT_PROVIDER = "template"


def get_answer_generator(provider: str = DEFAULT_PROVIDER) -> AnswerGenerator:
    try:
        client_cls = _PROVIDERS[provider.lower()]
    except KeyError as exc:
        available = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"알 수 없는 답변 생성기 provider: {provider!r} (사용 가능: {available})") from exc
    return client_cls()
