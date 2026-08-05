"""임베딩 provider 선택 팩토리 (`ingest/stt/factory.py`와 동일한 패턴).

실서비스 기본값은 `"gemini"`이고, `"hash"`는 API를 호출하지 않는 테스트용
provider로 남긴다.
"""

from __future__ import annotations

from .base import EmbeddingClient
from .gemini import GeminiEmbeddingClient
from .hash_stub import HashEmbeddingClient

_PROVIDERS: dict[str, type[EmbeddingClient]] = {
    "gemini": GeminiEmbeddingClient,
    "hash": HashEmbeddingClient,
}

DEFAULT_PROVIDER = "gemini"


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
