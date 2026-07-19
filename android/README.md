# Mem2Life Android 컴패니언 앱

Meta Ray-Ban Gen 2 글래스에서 720p 영상 스트림을 받아 30초 mp4 청크로 잘라
백엔드에 업로드하고, 오디오를 PCM 16kHz/mono WebSocket 스트림으로 전송하는
컴패니언 앱. 실기기가 없는 1단계 시점에는 Meta DAT SDK의 Mock Device Kit으로
영상 스트림을 시뮬레이션한다. 계약/원칙은 루트 `CLAUDE.md`의 "업로드 API 계약
(android ↔ wiki-builder, v1 초안)" 절 참고.

푸시투톡 질의 UI와 TTS 응답 재생은 이 저장소의 후속 작업 범위다(현재는 녹화 ->
업로드 경로만 구현).

## 패키지 구조

`app/src/main/java/com/mem2life/companion/` 아래 패키지별 책임:

| 패키지 | 책임 |
| --- | --- |
| (root) | `MainActivity`(단일 화면 Compose UI), `Mem2LifeApplication`(DAT SDK 초기화) |
| `wearables/` | DAT SDK 글래스 등록/세션/카메라 스트림 컨트롤러(`WearablesGlassesController`). 실기기든 Mock Device Kit이든 이 계층에서는 동일한 코드로 다뤄진다 |
| `mock/` | Mock Device Kit 디버그 컨트롤러(`MockDeviceKitController`) — 페어링/전원/착용/카메라 피드 시뮬레이션만 담당, 오디오는 시뮬레이션하지 않는다 |
| `capture/` | DAT `VideoFrame`(YUV) -> H.264 -> 30초 mp4 청크 인코딩(`VideoChunkEncoder`, `YuvColorConverter`, `ChunkFile`) |
| `audio/` | 마이크 오디오 입력 추상화(`AudioSource`). 실기기용 `BluetoothScoAudioSource`(HFP 8kHz), 목업용 `MockPcmAudioSource`, 공통 리샘플러 `PcmResampler`(8kHz -> 16kHz) |
| `net/` | 업로드 API 계약 클라이언트 — `SessionApiClient`(HTTP), `AudioStreamSocket`(WebSocket), `VideoChunkUploadQueue`(디스크 큐+재시도), `NetworkModels`(요청/응답 모델) |
| `config/` | 백엔드 host/port 설정 로드/저장(`BackendConfig`, `BackendConfigStore`) — 하드코딩 금지 원칙의 구현 지점 |
| `recording/` | 녹화 세션 전체 오케스트레이션(`RecordingSessionController`), 백그라운드 유지용 포그라운드 서비스(`RecordingForegroundService`), 상태 모델(`RecordingState`) |

의존 방향은 대략 `recording/`이 나머지 패키지를 조합하는 최상위 오케스트레이터이고,
`net/`·`audio/`·`capture/`·`wearables/`·`mock/`·`config/`는 서로 거의 모르는 채
독립적으로 테스트 가능하게 나뉘어 있다.

## 빌드 준비물

- Android Studio (Flamingo 이상), Android SDK (compileSdk 36, minSdk 31)
- GitHub 개인 액세스 토큰(`read:packages` 스코프) — DAT SDK가 GitHub Packages로
  배포되기 때문에 필요하다. `local.properties`에 `github_token=...`으로 넣거나
  `GITHUB_TOKEN` 환경변수로 제공한다 (`local.properties.example` 참고, 실제
  `local.properties`는 커밋하지 않는다).

Android Studio로 이 디렉터리(`Mem2Life/android/`)를 열면 Gradle 동기화 시 위
토큰으로 DAT SDK를 내려받는다.

## 백엔드 연결 설정 (하드코딩 아님)

기본값은 `app/src/main/assets/backend_config.json`에서 읽는다(에뮬레이터
기준 `10.0.2.2:8000`). 앱 안의 "백엔드 설정" 화면에서 host/port를 바꾸면
SharedPreferences에 저장되어 재빌드 없이 다른 서버를 가리킬 수 있다
(`config/BackendConfigStore.kt`).

## 로컬 검증용 목업 백엔드

실제 수신 서버(wiki-builder 담당, `Mem2Life/backend/`)는 아직 없다. 업로드
클라이언트를 끝까지 검증하려면 `tools/mock-backend/`의 FastAPI 목업 서버를
띄운다:

```bash
cd Mem2Life/android/tools/mock-backend
uv sync
uv run uvicorn mock_backend.main:app --host 0.0.0.0 --port 8000 --reload
```

자세한 내용은 `tools/mock-backend/README.md` 참고.

## 개발 명령어

```bash
cd Mem2Life/android
./gradlew testDebugUnitTest   # 리샘플러, 업로드 큐 백오프/파일명 규약, 업로드 큐 동시성(레이스) 테스트
./gradlew assembleDebug       # 디버그 APK 빌드
```

## Mock Device Kit으로 영상 스트림 시뮬레이션하기

1. 앱 실행 후 "Mock Device Kit (디버그)" 패널에서 **Enable** -> **Pair RayBan
   Meta** -> **Power On** -> **Unfold** -> **Don** 순서로 누른다.
2. "목업 카메라 피드 영상 선택" 버튼으로 h.264/h.265 영상 파일을 고른다
   (Mock Device Kit은 Android에서 자동 트랜스코딩을 하지 않는다 — 필요하면
   `ffmpeg -c:v hevc_videotoolbox ...`로 변환).
3. "녹화 시작"을 누르면 목업 카메라 피드가 30초 mp4 청크로 인코딩되어 목업
   백엔드로 업로드된다.

## 네트워크 복원력 (데모 안정성 원칙의 구현 지점)

루트 `CLAUDE.md`의 "네트워크 끊김·스트림 중단 시 로컬 버퍼링 후 재전송" 원칙은
`net/` 패키지 두 곳에서 서로 다르게 구현된다 — 업로드 API 계약이 영상과 오디오의
재전송 정책을 다르게 정의하기 때문이다(영상은 유실 불허, 오디오는 재연결만 하고
유실 허용):

- `VideoChunkUploadQueue` — 청크를 디스크에 먼저 쓰고 seq 순서대로 exponential
  backoff 재시도. 워커 코루틴은 세션 동안 절대 스스로 종료하지 않는 상시 루프로
  설계돼 있다(왜 그렇게 설계했는지는 클래스 상단 KDoc 참고 — 과거에 워커가
  재기동을 판단하다 생기던 lost-wakeup 레이스를 구조적으로 없앤 결과다).
  `VideoChunkUploadQueueWorkerRaceTest`가 이 설계를 스트레스 테스트로 검증한다.
- `AudioStreamSocket` — 끊기면 재연결만 하고 그 사이 유실된 프레임은 포기한다
  (실시간 스트림 특성상 재전송이 의미 없다는 계약에 따른 의도된 동작).

## 알려진 제약

- **DAT SDK는 오디오 캡처 API를 제공하지 않는다.** 마이크 입력은 SDK 밖에서
  표준 Android Bluetooth HFP 경로(`audio/BluetoothScoAudioSource.kt`)로
  접근한다. Mock Device Kit도 마이크를 시뮬레이션하지 않으므로, 실기기 없이
  오디오 경로를 검증하려면 `audio/MockPcmAudioSource.kt`(목업 톤/에셋 PCM
  파일 반복 재생)를 쓴다. 자세한 배경은 루트 `CLAUDE.md`의 "알려진 리스크 /
  검증 대기" 절 참고.
- HFP는 8kHz mono로 고정된다(위와 같은 CLAUDE.md 절 참고). 업로드 계약이
  요구하는 16kHz로 `audio/PcmResampler.kt`가 업샘플링한다.
- 이 개발 환경에는 Android SDK/에뮬레이터/실기기가 없어 Kotlin 코드를 직접
  컴파일·실행해 검증하지 못했다 — Android Studio에서 Gradle 동기화와
  `./gradlew testDebugUnitTest` / `assembleDebug`로 재확인이 필요하다.
