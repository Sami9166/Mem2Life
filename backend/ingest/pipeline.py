"""영상 파일 하나로 기록 파이프라인 전체(오디오 추출 → STT → 세션 md 생성)를
처음부터 끝까지 실행하는 오케스트레이션 모듈.

이번 1단계 프로토타입 범위: 오디오 추출 → STT(화자분리, 스텁) → Obsidian
세션 md 생성까지. VLM 캡션·LLM 요약·엔티티 페이지 갱신은 다음 단계.

API 키 없이 끝까지 성공하는 것이 이번 단계의 핵심 목표이므로, STT는 항상
스텁(RTZR/Clova 중 선택)을 사용한다.

실제 RTZR API 연동 이후에도 이 원칙은 유지된다 — `stt_client.transcribe()`가
`RTZRAPIError`(네트워크 429/5xx 소진, RTZR 서버가 status="failed"를 반환,
폴링 타임아웃 등 인증 이후 단계에서 발생하는 실패)를 던지면, 데모 도중
전체 실행이 중단돼 세션 md가 아예 생성되지 않는 최악의 상황을 피하기 위해
이 함수가 화자1/화자2 더미 스텁 전사록으로 대체해 파이프라인을 끝까지
진행시킨다(경고 메시지 출력, 나중에 재처리 권장). `RTZRCredentialError`
(인증 정보 누락/오류)는 여기서 잡지 않는다 — 그 경로는 이미
`stt.factory._build_rtzr_client()`가 생성 시점에 스텁으로 대체하므로 이
지점까지 올라오지 않아야 하고, 만약 올라온다면 설정 문제이지 일시적
장애가 아니므로 그대로 실패시켜 사용자가 인지하게 한다.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .audio import ExtractedAudio, extract_audio
from .stt.base import Transcript
from .stt.factory import DEFAULT_PROVIDER, get_stt_client
from .stt.rtzr_client import RTZRAPIError
from .stt.rtzr_stub import RTZRStubClient
from .wiki.session_md import write_session_md


@dataclass(frozen=True, slots=True)
class IngestResult:
    """파이프라인 실행 결과 요약."""

    video_path: Path
    audio_path: Path
    session_md_path: Path
    stt_provider: str
    transcript: Transcript


def run_ingest_pipeline(
    video_path: Path | str,
    vault_dir: Path | str,
    *,
    title: str = "세션",
    session_start: datetime | None = None,
    participants: Sequence[str] | None = None,
    stt_provider: str = DEFAULT_PROVIDER,
    audio_dir: Path | str | None = None,
    keep_audio: bool = True,
) -> IngestResult:
    """영상 파일 → 오디오 추출 → STT 스텁 → 세션 md 생성을 순서대로 실행한다.

    Args:
        video_path: 입력 영상 파일 경로. 이 함수만으로 전체 파이프라인이
            돌아가야 하며, 글래스/컴패니언 앱 연동은 필요 없다.
        vault_dir: Obsidian 볼트 루트 디렉토리 (`sessions/`가 이 아래 생성됨).
        title: 세션 제목 (파일명 `YYYY-MM-DD_HHMM_제목.md`에 사용).
        session_start: 세션 시작 시각. 생략 시 현재 시각(`datetime.now()`).
            리허설에서 "어제" 세션을 재현할 때 전날 날짜를 명시적으로
            넘기는 용도로도 쓸 수 있다 (데모_시나리오.md 리스크 항목 대응).
        participants: 참석자 표기 목록. 생략 시 전사록에서 감지된 화자
            라벨(화자1, 화자2, ...)을 그대로 사용한다 (이름 매핑은 이후 단계).
        stt_provider: "rtzr"(기본, 1순위) 또는 "clova"(2순위) 스텁 선택.
        audio_dir: 추출된 오디오(.wav)를 저장할 디렉토리. 생략 시 영상과
            같은 디렉토리에 저장한다.
        keep_audio: False면 세션 md 생성 후 추출된 오디오 파일을 삭제한다.

    Returns:
        IngestResult: 생성된 세션 md 경로 등 실행 결과.
    """
    # video_path는 이 세션 md의 frontmatter `video:` 필드로 그대로 기록되며,
    # recall-dev의 fallback(영상 클립 재조회)이 나중에 다른 작업 디렉토리에서
    # 이 경로로 파일을 다시 열어야 하는 wiki-builder<->recall-dev 계약이다.
    # 상대경로/CWD 의존 경로가 그대로 저장되면 재조회가 깨지므로 절대경로로
    # 고정한다.
    video_path = Path(video_path).resolve()
    vault_dir = Path(vault_dir)
    resolved_start = session_start or datetime.now()

    audio_output_path: Path | None = None
    if audio_dir is not None:
        audio_dir = Path(audio_dir)
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_output_path = audio_dir / f"{video_path.stem}.wav"

    extracted: ExtractedAudio = extract_audio(video_path, audio_output_path)

    stt_client = get_stt_client(stt_provider)
    try:
        transcript = stt_client.transcribe(extracted.path)
    except RTZRAPIError as exc:
        # RTZR 인증 정보는 있지만 API 호출 자체가 실패한 경우(네트워크 문제,
        # 429/5xx 재시도 소진, RTZR 서버가 전사 실패를 반환, 폴링 타임아웃 등).
        # 인증 정보가 아예 없는 경우와 동일하게 "전체 실행 중단·세션 md 미생성"
        # 대신 스텁 품질 전사록으로 대체해 데모가 끝까지 진행되게 한다.
        print(
            "[경고] RTZR API 호출이 실패해 이번 세션은 실제 STT 대신 "
            "화자1/화자2 더미 스텁 전사록으로 대체합니다 (실제 발화 내용이 "
            f"아니므로 신뢰하지 마세요): {exc}\n"
            "        RTZR API가 정상화되면 이 영상으로 다시 실행해 재처리하는 "
            "것을 권장합니다.",
            file=sys.stderr,
        )
        transcript = RTZRStubClient().transcribe(extracted.path)

    resolved_participants = list(participants) if participants else (transcript.speakers or ["화자1"])

    session_md_path = write_session_md(
        vault_dir,
        session_start=resolved_start,
        title=title,
        participants=resolved_participants,
        video_path=video_path,
        transcript=transcript,
    )

    if not keep_audio:
        extracted.path.unlink(missing_ok=True)

    return IngestResult(
        video_path=video_path,
        audio_path=extracted.path,
        session_md_path=session_md_path,
        stt_provider=transcript.provider,
        transcript=transcript,
    )
