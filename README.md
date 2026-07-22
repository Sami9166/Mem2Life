# Mem2Life

Meta Ray-Ban Gen 2 스마트 글래스로 착용자의 1인칭 영상과 대화를 기록하고, LLM이 이를 텍스트 지식(위키)으로 변환해 PostgreSQL에 보관하고 Obsidian Markdown으로 만든 뒤, "어제 누구랑 했던 얘기 중 ~내용 뭐였지?" 같은 질문에 답하는 개인 기억 보조 시스템입니다. 아키텍처는 [EgoLife 논문](https://arxiv.org/abs/2503.03803)(CVPR 2025)의 EgoRAG 구조를 차용했습니다.

이 저장소는 실제 코드가 들어가는 곳입니다. 설계 배경·의사결정 기록 등 문서 자료는 별도 저장소(문서 전용, 이 저장소에는 포함되지 않음)에 있습니다.

## 아키텍처 한눈에 보기

```
글래스 (영상 720p + 음성)
  → Android 컴패니언 앱 (Meta DAT SDK)
  → 30초 영상 청크 업로드 + 오디오 WebSocket 스트림 → 백엔드 서버 (Python FastAPI)
      ① STT + 화자분리 (리턴제로 RTZR API 1순위, Clova Speech 2순위)
      ② 키프레임 → VLM 캡션 (예정)
      ③ LLM 요약 → 세션 요약 + 인물/주제 엔티티 페이지 갱신 (예정)
      ④ PostgreSQL에 원본 저장 → Obsidian md 생성 → pgvector 검색 색인
  원본 영상: 세션별 로컬 저장 (fallback 재조회용)

질의: 폰 앱 푸시투톡(예정) → STT → 하이브리드 검색(BM25+pgvector, daily→session→전사록 coarse-to-fine)
  → 텍스트로 답변 시도 → 근거 불충분 시 fallback: 해당 구간 영상을 VLM으로 재조회 (예정)
  → 답변: 텍스트 + 근거 타임스탬프 (TTS/화면 표시는 예정)
```

## 저장소 구조

```
Mem2Life/
├── backend/
│   ├── ingest/     # 기록 파이프라인: 영상 → 오디오 추출 → STT → Obsidian 세션 md 생성
│   ├── recall/     # 질의 파이프라인: 하이브리드 검색 → 답변 생성 → fallback 판정
│   ├── wiki_db/    # PostgreSQL 원본·Markdown 문서·pgvector 색인 저장
│   └── tests/      # ingest·recall 공통 테스트 스위트
└── android/        # Kotlin 컴패니언 앱 — Mock Device Kit 기반 녹화·업로드 클라이언트
    └── tools/mock-backend/  # 실제 백엔드 수신 서버가 없는 동안 쓰는 로컬 검증용 목업 서버
```

`backend/`와 `android/`는 서로 독립적으로 개발·테스트됩니다. 둘 사이의 계약은 이 문서의 "업로드 API 계약" 절이고, `backend/ingest`와 `backend/recall` 사이의 계약은 PostgreSQL의 `wiki_documents`/`search_items`와 "Obsidian 볼트 스키마" 절입니다. 계약을 변경할 때는 이 README를 먼저 갱신하세요.

## 빠른 시작

### backend (Python 3.11+, [uv](https://docs.astral.sh/uv/) 사용)

```bash
cd backend
uv sync

uv run pytest                              # 전체 테스트
uv run ruff check . && uv run ruff format --check .   # 린트/포맷 확인

uv run mem2life-ingest <영상경로>           # 기록 파이프라인 실행 (API 키 없이도 끝까지 동작)

# --vault 생략 시 기본값은 런타임 생성용 Mem2Life/vault/(git-ignore 대상, 클론 직후엔 비어있음)라서
# 아래처럼 데모 시나리오가 채워진 픽스처를 명시해야 함
uv run mem2life-recall ask "<질문>" --vault testdata/mock_vault
uv run mem2life-recall serve --vault testdata/mock_vault   # FastAPI 서버로 /recall/query 노출
```

PostgreSQL/pgvector를 사용할 때는 빈 DB를 준비하고 `backend/.env`에 DSN을 넣습니다. 테이블과 pgvector 인덱스는 첫 실행 시 자동 생성됩니다.

```dotenv
MEM2LIFE_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/mem2life
```

이 값이 있으면 ingest는 `DB 원본 → Markdown → pgvector` 순서로 저장하고 recall은 PostgreSQL의 벡터를 검색합니다. 값이 없으면 기존 Markdown+JSON 캐시 모드로 동작해 PostgreSQL 없이도 로컬 테스트할 수 있습니다.

`RTZR_CLIENT_ID`/`RTZR_CLIENT_SECRET`을 `backend/.env`에 넣으면(`.env.example` 참고) STT가 실제 RTZR API를 사용합니다. 넣지 않으면 자동으로 화자1/화자2 더미 전사록을 만드는 스텁으로 동작합니다 — 자격증명 없이도 파이프라인 전체가 끝까지 실행되는 것이 이 프로젝트의 핵심 원칙입니다. 실제 API 호출이 중간에 실패해도(네트워크 문제 등) 스텁으로 자동 폴백해 세션 md는 항상 생성됩니다.

### android (Android Studio, JDK, Android SDK 필요)

```bash
cd android
./gradlew testDebugUnitTest
./gradlew assembleDebug
```

DAT SDK가 GitHub Packages로 배포되므로 `read:packages` 스코프의 GitHub 토큰이 필요합니다. 자세한 빌드 준비물·패키지 구조·Mock Device Kit 사용법은 [`android/README.md`](android/README.md) 참고.

## Obsidian 볼트 스키마

DB 원본에서 생성되는 사람이 읽는 Wiki 계약입니다. Markdown은 Obsidian 그래프와 근거 확인에 사용하고, RAG는 Markdown을 섹션·발화·캡션 단위로 나눈 PostgreSQL 색인을 검색합니다.

```
vault/
├── sessions/YYYY-MM-DD_HHMM_제목.md   # 세션 로그: frontmatter(일시·참석자·video경로) + 요약/전사록 전문/장면캡션
├── people/이름.md                      # 인물 페이지 (세션 종료 시 LLM이 자동 갱신 — 예정)
├── topics/주제.md                      # 주제 페이지 (〃)
└── daily/YYYY-MM-DD.md                # 일별 요약
```

원칙: 전사록은 요약이 아니라 전문을 보존하고, 전사록과 캡션에는 영상 기준 타임스탬프를 붙이며, `[[위키링크]]`로 그래프를 형성합니다. `backend/testdata/mock_vault/`는 이 스키마를 손으로 채워 넣은 픽스처로, `ingest`가 아직 만들지 못하는 완성형 볼트(VLM 캡션·LLM 요약 포함)를 흉내내 `recall`을 독립적으로 개발·테스트하기 위한 것입니다.

## LLM Wiki DB 스키마

한 PostgreSQL DB 안에서 역할을 네 테이블로 분리합니다.

```text
sessions        세션 상태 + video/transcript/markdown 경로
memory_items    타임스탬프가 붙은 summary/transcript/caption 원본
wiki_documents DB 원본에서 생성한 Markdown 본문
search_items    Markdown 검색 단위 + 256차원 임베딩(pgvector)
```

관계는 `sessions 1:N memory_items`, `sessions 1:N wiki_documents`, `wiki_documents 1:N search_items`입니다. 현재 세션마다 session Markdown 하나를 만들며, people/topics/daily 문서는 `session_id` 없이 `wiki_documents`에 저장할 수 있습니다.

`sessions`와 `memory_items`가 원본입니다. `wiki_documents`와 `search_items`는 원본으로 다시 만들 수 있으며, Markdown 파일은 DB에서 생성하는 단방향(`DB → md`) 산출물입니다.

### 한 세션의 저장 예시

`sessions`에는 세션 전체 정보와 원본 파일 경로가 한 행으로 들어갑니다.

```text
id              550e8400-e29b-41d4-a716-446655440000
title           제주도 여행 계획
started_at      2026-07-22 14:00:00+09
ended_at        2026-07-22 14:05:00+09
participants    {민수,현우}
video_path      C:/Mem2Life/videos/session.mp4
transcript_path C:/Mem2Life/data/sessions/550e8400/transcript.json
markdown_path   C:/Mem2Life/vault/sessions/2026-07-22_1400_제주도_여행_계획.md
status          ready
```

`memory_items`에는 요약·전사·캡션을 별도 행으로 저장합니다. `start_ms`와 `end_ms`는 영상 시작을 0으로 본 상대 시간입니다.

| kind | ordinal | start_ms | end_ms | speaker | content |
| --- | ---: | ---: | ---: | --- | --- |
| `summary` | 0 | 0 | 300000 | `NULL` | 민수와 현우가 제주도 여행 계획을 논의했다. |
| `transcript` | 0 | 5000 | 9000 | 민수 | 숙소는 서귀포로 알아보자. |
| `transcript` | 1 | 9000 | 13000 | 현우 | 좋아, 가격을 비교해보자. |
| `caption` | 0 | 10000 | 20000 | `NULL` | 휴대폰에 서귀포 호텔 예약 화면이 보인다. |

이 원본을 DB에서 다시 읽어 다음 Markdown을 생성하고, 파일과 동일한 본문을 `wiki_documents.markdown_content`에도 저장합니다.

```markdown
---
session_id: "550e8400-e29b-41d4-a716-446655440000"
date: 2026-07-22
time: 14:00-14:05
participants: ["[[민수]]", "[[현우]]"]
video: "C:/Mem2Life/videos/session.mp4"
transcript: "C:/Mem2Life/data/sessions/550e8400/transcript.json"
---
## 요약

민수와 현우가 제주도 여행 계획을 논의했다.

## 전사록

[00:00:05] 민수: 숙소는 서귀포로 알아보자.
[00:00:09] 현우: 좋아, 가격을 비교해보자.

## 장면 캡션

- [00:00:10] 휴대폰에 서귀포 호텔 예약 화면이 보인다.
```

Markdown은 검색 단위로 나뉘어 `search_items`에 들어갑니다. `document_id`는 위 Markdown의 `wiki_documents.id`이고, 임베딩은 아래처럼 축약하지 않고 실제로 256개 숫자를 저장합니다.

| chunk 종류 | content | metadata 예시 | embedding 예시 |
| --- | --- | --- | --- |
| `session_summary` | 민수와 현우가 제주도 여행 계획을 논의했다. | `{"start_sec": null}` | `[0.12, -0.03, …]` |
| `transcript` | 민수: 숙소는 서귀포로 알아보자. | `{"start_sec": 5, "timestamp_label": "[00:00:05]", "speaker": "민수"}` | `[0.08, 0.21, …]` |
| `scene_caption` | 휴대폰에 서귀포 호텔 예약 화면이 보인다. | `{"start_sec": 10, "timestamp_label": "[00:00:10]"}` | `[-0.04, 0.17, …]` |

RAG는 `session_id`로 문장을 찾는 것이 아니라 `search_items.content`의 BM25 점수와 pgvector 유사도로 관련 chunk를 찾습니다. `session_id`는 검색 결과가 어느 세션과 Markdown에서 나왔는지 역추적하는 연결 키입니다. 현재 기본 임베딩은 256차원 해시 스텁이므로 실제 의미 임베딩 모델을 채택할 때 DB 벡터 차원도 함께 마이그레이션해야 합니다.

## 업로드 API 계약 (android ↔ backend)

Android 컴패니언 앱과 백엔드가 스트리밍 수신을 주고받는 계약입니다. 영상은 청크 업로드, 오디오는 WebSocket 스트림으로 전송 경로가 다릅니다.

```
POST /sessions/start
  body: {"title"?: str, "participants"?: [str]}
  →     {"session_id": str, "started_at": iso8601}

POST /sessions/{session_id}/video-chunks   (30초 단위, multipart/form-data)
  fields: chunk(mp4 파일), seq(0부터 순번, int), start_ts(세션 시작 기준 초), duration_sec

WS   /sessions/{session_id}/audio-stream
  바이너리 프레임: PCM 16-bit, 16kHz, mono
  프레임 순서 = 전송 순서 그대로, 재조립 번호 없음 (재연결 시 유실 구간 허용)

POST /sessions/{session_id}/end
  → 세션 종료, 백엔드가 최종 파이프라인(요약·엔티티 갱신) 비동기 트리거
```

네트워크 끊김 시 영상 청크는 순번대로 재전송(exponential backoff)하지만, 오디오는 재연결만 하고 유실분은 포기합니다(실시간 스트림 특성상 재전송이 의미 없음). **이 엔드포인트들의 실제 수신 서버는 아직 구현되지 않았습니다** — 현재 `backend/ingest`는 완성된 영상 파일 하나를 CLI로 받는 구조이고, android 쪽은 `android/tools/mock-backend/`의 로컬 목업 서버로 업로드 클라이언트를 검증합니다.

## 현재 상태

| 영역 | 상태 |
| --- | --- |
| `backend/ingest` | 영상 → 오디오 추출 → STT → PostgreSQL 원본 저장 → DB에서 세션 md 생성 → pgvector 색인까지 동작. DB URL이 없으면 기존 파일 모드. VLM 캡션·LLM 요약·엔티티 갱신은 TODO |
| `backend/recall` | 하이브리드 검색·coarse-to-fine·답변 생성·fallback 판정까지 동작. DB URL이 있으면 pgvector, 없으면 기존 메모리 벡터/JSON 캐시 사용. 임베딩은 아직 의미 기반이 아닌 해시 스텁 |
| `android` | Mock Device Kit 기반 녹화 → 청크 업로드 → 오디오 스트림 클라이언트 구현. 푸시투톡 질의 UI·TTS 재생은 미착수. 실기기 미보유로 컴파일 검증은 Android Studio 환경에서 별도 필요 |

알려진 트레이드오프와 리스크(RTZR 실사용 미검증, recall 임베딩이 패러프레이즈에 약함, DAT SDK에 오디오 API가 없어 표준 Bluetooth HFP로 우회 등)는 각 모듈 안의 코드 주석과 `android/README.md`의 "알려진 제약" 절에 정리돼 있습니다.

## 코딩 컨벤션

- Python 3.11+, 타입힌트 필수. 패키지 관리는 `uv`, 린트/포맷은 `ruff`
- 외부 API(STT/VLM/LLM/임베딩)는 인터페이스(Protocol)로 추상화해 구현체를 교체 가능하게 유지
- API 키는 `.env`로 관리, 커밋 금지 (`.env.example`에 플레이스홀더만)
- 각 백엔드 모듈은 실기기·글래스 의존 없이 단독 테스트 가능해야 함
- 커밋 메시지·코드 주석은 한국어 허용
