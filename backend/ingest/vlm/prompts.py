"""VLM 캡션 / LLM 요약 프롬프트 문구 구성.

네트워크 호출(`gemini_client.py`)과 분리해 프롬프트 텍스트 자체를 순수 함수로
만들어 독립적으로 테스트할 수 있게 한다.

## 캡션 프롬프트 설계 원칙

1. 기술조사_의사결정.md 조사4 "EgoLife visual-audio caption 형식" — 행동/
   상호작용 객체/장면 설명을 한국어로, 직전 전사록을 컨텍스트로 함께 입력한다
   (시각+청각 융합이 성능 핵심이라는 EgoLife 어블레이션 결과 반영).
2. **불확실성 명시 지시(필수)**: `recall/fallback/self_assessment.py`의
   `_NO_INFO_MARKERS`가 캡션 텍스트에 아래 문구 중 하나가 있어야 "시각 정보가
   기록되지 않았다"고 판단해 fallback(영상 재조회)을 트리거한다:

       기록되지 않 / 확인되지 않 / 미확인 / 알 수 없 / 포착되지 않 / 식별되지 않

   VLM이 작은 글자·흐릿한 문구처럼 확실히 읽을 수 없는 세부사항을 그럴듯하게
   지어내면(예: 불확실성 표시 없이 "책을 들고 있다"라고만 쓰면) 이 안전장치가
   조용히 깨진다 — 반드시 위 문구 중 하나를 그대로 포함해 명시적으로 "모른다"고
   쓰게 강제한다. 이 목록이 바뀌면(`self_assessment.py` 수정) 이 프롬프트도
   함께 갱신해야 한다.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..stt.base import Transcript, format_timestamp

# 키프레임 시각 기준으로 이만큼 이전까지의 전사록만 컨텍스트로 넣는다. 세션
# 전체 전사록을 매 키프레임마다 통째로 넣으면 키프레임이 많을 때(시간당 약
# 360장, 기술조사_의사결정.md 조사4) 토큰 비용이 선형으로 불어나고, 정작 그
# 장면과 무관한 먼 과거 발화가 캡션에 섞여드는 부작용도 있다.
CAPTION_CONTEXT_WINDOW_SEC = 60.0

_CAPTION_RULES = (
    "1. 인물의 행동, 상호작용하는 물체, 장면/배경을 1~2문장으로 서술하세요"
    '("~한다.", "~보인다." 같은 서술형 어미).',
    '2. 함께 주어지는 "직전 전사록"은 장면을 이해하는 참고 맥락일 뿐입니다 — 전사록 내용을'
    " 그대로 반복하지 말고, 이미지에서 실제로 보이는 시각 정보를 우선해서 쓰세요.",
    '3. 등장인물은 "화자1", "화자2"처럼 익명 라벨로만 지칭하세요. 실명을 추측하지 마세요.',
    "4. 아주 중요: 작은 글자, 흐릿한 문구, 책 표지·포장·화면의 텍스트처럼 이미지만으로는"
    " 확실하게 읽어낼 수 없는 세부사항은 절대로 추측하거나 지어내지 마세요. 그런 경우 반드시"
    " 다음 표현 중 하나를 그대로 포함해 명시적으로 미확인 상태임을 표시하세요:"
    ' "기록되지 않음", "확인되지 않음", "미확인", "알 수 없음", "포착되지 않음", "식별되지 않음".'
    ' 예: "책 표지의 제목 문구는 해상도 문제로 확인되지 않음."',
    "5. 확실히 보이는 내용에는 불확실성 문구를 쓰지 마세요 — 정말 읽을 수 없을 때만 사용하세요.",
    "6. 캡션 텍스트만 출력하세요(따옴표, 번호, 접두사 없이).",
)

CAPTION_SYSTEM_INSTRUCTION = (
    "당신은 1인칭 웨어러블 카메라로 촬영된 대화 장면의 키프레임 이미지 한 장을 보고, "
    "한국어로 간결한 장면 캡션을 작성하는 어시스턴트입니다.\n\n규칙:\n" + "\n".join(_CAPTION_RULES)
)


def build_caption_context(transcript: Transcript, keyframe_timestamp_sec: float) -> str:
    """키프레임 직전 `CAPTION_CONTEXT_WINDOW_SEC`초 이내의 전사록 줄을 모은다."""
    window_start = keyframe_timestamp_sec - CAPTION_CONTEXT_WINDOW_SEC
    lines = [
        f"{segment.timestamp_label} {segment.speaker}: {segment.text}"
        for segment in transcript.segments
        if window_start <= segment.start_sec <= keyframe_timestamp_sec
    ]
    if not lines:
        return "(직전 전사록 없음)"
    return "\n".join(lines)


def build_caption_prompt(context: str) -> str:
    return (
        "다음은 이 키프레임 직전의 대화 전사록입니다(참고용 — 그대로 반복하지 마세요):\n"
        f"{context}\n\n"
        "위 규칙에 따라 이 이미지의 장면 캡션을 한국어 1~2문장으로 작성하세요."
    )


def build_batch_caption_context(
    transcript: Transcript, start_sec: float, end_sec: float
) -> str:
    """배치 구간 `[start-window, end]`에 걸친 전사록 줄을 모은다(여러 프레임 공통 컨텍스트)."""
    window_start = start_sec - CAPTION_CONTEXT_WINDOW_SEC
    lines = [
        f"{segment.timestamp_label} {segment.speaker}: {segment.text}"
        for segment in transcript.segments
        if window_start <= segment.start_sec <= end_sec
    ]
    if not lines:
        return "(직전 전사록 없음)"
    return "\n".join(lines)


def build_batch_caption_prompt(context: str, timestamps: Sequence[float]) -> str:
    """여러 키프레임을 한 번에 캡션하는 프롬프트 — 이미지 순서대로 JSON 배열을 요청한다."""
    listing = "\n".join(
        f"- 이미지 {i}: 세션 {format_timestamp(ts)} 지점" for i, ts in enumerate(timestamps, start=1)
    )
    n = len(timestamps)
    return (
        "다음은 이 구간의 대화 전사록입니다(참고용 — 그대로 반복하지 마세요):\n"
        f"{context}\n\n"
        f"아래에 이 구간의 키프레임 이미지 {n}장이 시간 순서로 주어집니다:\n"
        f"{listing}\n\n"
        f"각 이미지에 대해 위 규칙에 따라 한국어 1~2문장 장면 캡션을 작성하세요.\n"
        f"출력은 반드시 캡션 문자열 {n}개를 이미지 순서대로 담은 JSON 배열 하나여야 합니다. "
        "다른 설명·마크다운·키 없이 JSON 배열만 출력하세요.\n"
        '예: ["첫 번째 이미지 캡션.", "두 번째 이미지 캡션."]'
    )


_SUMMARY_RULES = (
    "1. 전사록 전문과 장면 캡션을 참고해 세션에서 실제로 있었던 일을 3~6문장으로 요약하세요.",
    "2. 전사록·캡션에 없는 내용을 지어내지 마세요(사실 기반 — 추측이나 일반화는 피하세요).",
    '3. 요약문에서 사용자가 알려준 "참석자" 목록에 있는 이름을 언급할 때는 반드시 그 이름을'
    " `[[이름]]` 형식(위키링크, 대괄호 두 개)으로 감싸세요. 참석자 목록에 없는 화자"
    "(예: 카메라 착용자 본인)는 감싸지 않습니다.",
    "4. 세션의 핵심 화제(예: 여행지 이름, 프로젝트명)가 있으면 자연스러운 곳에서 `[[화제명]]`"
    " 형식으로 한 번 감싸 언급하세요 — 억지로 만들지 말고, 실제로 대화의 중심 주제일 때만"
    " 그렇게 하세요.",
    '5. "화자1"/"화자2" 같은 익명 라벨 대신, 주어진 참석자 이름을 자연스럽게 사용하세요.',
    "6. 타임스탬프는 쓰지 마세요(타임스탬프가 있는 상세 내용은 `## 전사록`/`## 주요 순간`이"
    " 이미 담당합니다). 요약은 흐름을 파악하기 위한 문단입니다.",
    "7. 요약 문단만 출력하세요(제목, 글머리 기호 없이).",
)

SUMMARY_SYSTEM_INSTRUCTION = (
    "당신은 1인칭 웨어러블 카메라로 기록된 대화 세션의 전사록과 장면 캡션을 보고, "
    "Obsidian 볼트 세션 문서의 `## 요약`에 들어갈 문단을 한국어로 작성하는 어시스턴트입니다.\n\n"
    "규칙:\n" + "\n".join(_SUMMARY_RULES)
)


def build_summary_prompt(
    transcript: Transcript,
    captions: Sequence[tuple[float, float, str]],
    participants: Sequence[str],
) -> str:
    transcript_lines = (
        "\n".join(
            f"{segment.timestamp_label} {segment.speaker}: {segment.text}" for segment in transcript.segments
        )
        or "(전사록 없음)"
    )
    caption_lines = (
        "\n".join(f"[{format_timestamp(start_sec)}] {text}" for start_sec, _end_sec, text in captions)
        or "(장면 캡션 없음)"
    )
    participants_str = (
        ", ".join(participants) if participants else "(참석자 정보 없음 — 위키링크로 감쌀 이름 없음)"
    )

    return (
        f"참석자: {participants_str}\n\n"
        f"## 전사록\n{transcript_lines}\n\n"
        f"## 장면 캡션\n{caption_lines}\n\n"
        "위 내용을 바탕으로 규칙에 따라 세션 요약 문단을 작성하세요."
    )
