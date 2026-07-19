"""STT 스텁 구현체가 공유하는 더미 전사록 생성 로직.

실제 STT API 키/네트워크 호출 없이도 파이프라인 전체를 검증할 수 있도록,
오디오 길이에 맞춰 화자1/화자2가 번갈아 발화하는 더미 전사록을 만든다.
실제 RTZR/Clova 클라이언트로 교체되면 이 모듈은 더 이상 쓰이지 않는다.
"""

from __future__ import annotations

from .base import Transcript, TranscriptSegment

# provider마다 살짝 다른 더미 문장을 써서, 어느 스텁이 호출됐는지
# 테스트/로그에서 구분할 수 있게 한다.
RTZR_DUMMY_LINES: tuple[str, ...] = (
    "민수야, 지난번에 말한 여행 계획 좀 정하자.",
    "좋아, 날짜부터 맞춰볼까?",
    "숙소 예산은 넉넉하게 잡아두자.",
    "이 책 진짜 좋았어, 한번 볼래?",
    "충전기는 여기 넣어둘게.",
    "다음에 만날 때 그거 좀 빌려줘.",
)

CLOVA_DUMMY_LINES: tuple[str, ...] = (
    "안녕하세요, 오늘 이야기 나눠볼까요?",
    "네, 좋습니다. 지난번 이어서 진행하죠.",
    "일정은 이렇게 정리하면 될 것 같아요.",
    "예산 부분도 같이 확인해봐요.",
    "알겠습니다, 그럼 그렇게 진행할게요.",
    "혹시 더 확인할 사항이 있을까요?",
)

_SPEAKERS: tuple[str, str] = ("화자1", "화자2")
_MIN_LAST_SEGMENT_SEC = 0.5


def build_dummy_transcript(
    duration_sec: float,
    *,
    provider: str,
    lines: tuple[str, ...],
    segment_len_sec: float = 4.0,
) -> Transcript:
    """오디오 길이에 맞춰 화자1/화자2가 번갈아 말하는 더미 전사록을 만든다.

    Args:
        duration_sec: 오디오 길이(초). 0 이하이면 `lines` 전체 길이만큼 생성한다.
        provider: 이 전사록을 생성한 스텁의 식별자 (예: "rtzr-stub").
        lines: 순환 사용할 더미 발화 문장 목록.
        segment_len_sec: 발화 하나의 기본 길이(초).
    """
    if duration_sec <= 0:
        duration_sec = len(lines) * segment_len_sec

    segments: list[TranscriptSegment] = []
    cursor_sec = 0.0
    segment_index = 0
    while cursor_sec < duration_sec:
        end = min(cursor_sec + segment_len_sec, duration_sec)
        if end - cursor_sec < _MIN_LAST_SEGMENT_SEC and segments:
            # 너무 짧은 마지막 조각은 만들지 않고 종료
            break
        segments.append(
            TranscriptSegment(
                start_sec=cursor_sec,
                end_sec=end,
                speaker=_SPEAKERS[segment_index % len(_SPEAKERS)],
                text=lines[segment_index % len(lines)],
            )
        )
        cursor_sec = end
        segment_index += 1

    return Transcript(segments=segments, provider=provider)
