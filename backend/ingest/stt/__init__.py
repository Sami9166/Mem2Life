"""STT(음성인식) + 화자분리 추상 인터페이스와 스텁 구현체.

외부에서는 보통 `get_stt_client()`만 사용하면 된다:

    from ingest.stt import get_stt_client

    client = get_stt_client("rtzr")  # 또는 "clova"
    transcript = client.transcribe(audio_path)
"""

from __future__ import annotations

from .base import SpeechToTextClient, Transcript, TranscriptSegment, format_timestamp
from .clova_stub import ClovaStubClient
from .factory import DEFAULT_PROVIDER, get_stt_client
from .rtzr_client import RTZRAPIError, RTZRClient, RTZRCredentialError
from .rtzr_stub import RTZRStubClient

__all__ = [
    "SpeechToTextClient",
    "Transcript",
    "TranscriptSegment",
    "format_timestamp",
    "ClovaStubClient",
    "RTZRStubClient",
    "RTZRClient",
    "RTZRCredentialError",
    "RTZRAPIError",
    "get_stt_client",
    "DEFAULT_PROVIDER",
]
