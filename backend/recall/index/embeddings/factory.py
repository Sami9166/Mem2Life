"""임베딩 provider 선택 팩토리 (`ingest/stt/factory.py`와 동일한 패턴).

지금은 `"hash"`(로컬 스텁) 하나만 등록돼 있다. 실제 서비스용 임베딩
API(OpenAI `text-embedding-3-*`, Gemini `text-embedding-004` 등)를
도입할 때는 이 파일의 `_PROVIDERS`에 클래스만 추가하면 되고, 호출부
(`index/store.py`)는 손댈 필요가 없다.
"""

from __future__ import annotations

from .base import EmbeddingClient
from .hash_stub import HashEmbeddingClient

_PROVIDERS: dict[str, type[EmbeddingClient]] = {
    "hash": HashEmbeddingClient,
}

DEFAULT_PROVIDER = "hash"  # API 키 없이 1단계 프로토타입을 끝까지 돌리기 위한 기본값


def get_embedding_client(provider: str = DEFAULT_PROVIDER) -> EmbeddingClient:
    """provider 이름(대소문자 무관)으로 임베딩 클라이언트 인스턴스를 만든다.

    Raises:
        ValueError: 등록되지 않은 provider 이름일 때.
    """
    try:
        client_cls = _PROVIDERS[provider.lower()]
    except KeyError as exc:
        available = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"알 수 없는 임베딩 provider: {provider!r} (사용 가능: {available})") from exc
    return client_cls()
