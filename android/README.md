# Mem2Life Vuzix Blade 2 온글래스 앱

**Vuzix Blade 2** 스마트글래스(Android 11 / API 30 독립 실행형 기기) 위에서 직접
실행되며, 온보드 카메라 영상을 30초 mp4 청크로 잘라 백엔드에 업로드하고, 온보드
마이크 오디오를 PCM 16kHz/mono WebSocket 스트림으로 전송하는 앱. 계약/원칙은
루트 `CLAUDE.md`의 "업로드 API 계약(android ↔ wiki-builder, v1 초안)" 절 참고.

> **설계 이력**: 초기 설계는 Meta Ray-Ban Gen 2 + 폰 컴패니언 앱(Meta DAT SDK로
> 원격 스트림 수신) 구조였으나, 디바이스가 Vuzix Blade 2로 최종 확정되면서
> **앱이 글래스 위에서 단독 실행되는 구조로 전환**됐다. Blade 2는 Android 11이
> 탑재된 독립 기기라 카메라/마이크를 표준 Android API(Camera2, AudioRecord)로
> 직접 캡처한다 — 벤더 SDK, 사설 저장소 토큰, Bluetooth HFP 경로가 모두 필요
> 없어졌다.

푸시투톡 질의 UI와 TTS 응답 재생은 이 저장소의 후속 작업 범위다(현재는 녹화 ->
업로드 경로만 구현). 후속 작업 시 Vuzix Speech SDK(음성 명령)와
`com.vuzix:hud-actionmenu`(터치패드 메뉴 UI) 도입을 검토한다.

## 대상 하드웨어 — Vuzix Blade 2

| 항목 | 사양 | 이 앱에서의 의미 |
| --- | --- | --- |
| OS | Android 11 (API 30) | `minSdk = 30`, API 31+ 기능은 버전 가드 필요 |
| 카메라 | 8MP AF, 영상 최대 1080p | Camera2로 **1280x720 YUV** 캡처(계약 720p) |
| 마이크 | 노이즈캔슬링 온보드 마이크 | AudioRecord로 **16kHz 직접 캡처**(리샘플링 불필요) |
| 디스플레이 | 480x480, 웨이브가이드(검정=투명) | 순수 검정 배경 다크 테마 필수 |
| 입력 | 관자놀이 터치패드(트랙볼/D-pad 이벤트), 논터치 | UI는 포커스 이동+클릭으로 조작 가능해야 함 |
| 연결 | Wi-Fi 802.11ac, BT 5.0 | 업로드는 글래스 Wi-Fi로 백엔드에 직접 전송 |

## 패키지 구조

`app/src/main/java/com/mem2life/companion/` 아래 패키지별 책임:

| 패키지 | 책임 |
| --- | --- |
| (root) | `MainActivity`(단일 화면 Compose UI, Blade 2 다크 테마), `Mem2LifeApplication` |
| `camera/` | 온보드 카메라 Camera2 캡처 컨트롤러(`BladeCameraController`) — YUV_420_888 → tightly-packed I420 변환 포함 |
| `capture/` | I420 프레임 -> H.264 -> 30초 mp4 청크 인코딩(`VideoChunkEncoder`, `YuvColorConverter`, `ChunkFile`) |
| `audio/` | 마이크 오디오 입력 추상화(`AudioSource`). 실기기용 `DeviceMicAudioSource`(16kHz 직접 캡처, 48k/44.1k 폴백), 개발용 `MockPcmAudioSource`, 폴백 리샘플러 `PcmResampler` |
| `net/` | 업로드 API 계약 클라이언트 — `SessionApiClient`(HTTP), `AudioStreamSocket`(WebSocket), `VideoChunkUploadQueue`(디스크 큐+재시도), `NetworkModels`(요청/응답 모델) |
| `config/` | 백엔드 host/port 설정 로드/저장(`BackendConfig`, `BackendConfigStore`) — 하드코딩 금지 원칙의 구현 지점 |
| `recording/` | 녹화 세션 전체 오케스트레이션(`RecordingSessionController`), 백그라운드 유지용 포그라운드 서비스(`RecordingForegroundService`), 상태 모델(`RecordingState`) |

의존 방향은 대략 `recording/`이 나머지 패키지를 조합하는 최상위 오케스트레이터이고,
`net/`·`audio/`·`capture/`·`camera/`·`config/`는 서로 거의 모르는 채
독립적으로 테스트 가능하게 나뉘어 있다.

## 빌드 준비물

- Android Studio (Flamingo 이상), Android SDK (compileSdk 36, **minSdk 30** —
  Blade 2가 API 30이므로 낮춘 것)
- 사설 저장소 토큰 **불필요** — 모든 의존성은 google()/mavenCentral()에서
  내려받는다.

Android Studio로 이 디렉터리(`Mem2Life/android/`)를 열면 바로 Gradle 동기화된다.

## 백엔드 연결 설정 (하드코딩 아님)

기본값은 `app/src/main/assets/backend_config.json`에서 읽는다(에뮬레이터
기준 `10.0.2.2:8000`). 앱 안의 "백엔드 설정" 화면에서 host/port를 바꾸면
SharedPreferences에 저장되어 재빌드 없이 다른 서버를 가리킬 수 있다
(`config/BackendConfigStore.kt`). **Blade 2 실기기에서는 글래스와 같은 Wi-Fi에
물린 백엔드 PC의 LAN IP를 입력**한다.

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

## 실기기 없이 검증하기 (에뮬레이터)

Blade 2는 표준 Android 기기이므로 별도의 목업 SDK 없이 **일반 Android
에뮬레이터**로 전체 경로가 검증된다:

- **카메라**: 에뮬레이터의 가상 카메라(VirtualScene/webcam)가 Camera2로 그대로
  잡힌다 — `BladeCameraController`가 실기기와 동일한 코드로 동작한다.
- **마이크**: 에뮬레이터의 호스트 마이크 패스스루를 쓰거나, 마이크가 불안정하면
  앱의 "목업 오디오 소스 사용" 체크박스(디버그 패널)로 `MockPcmAudioSource`를 쓴다.

절차는 `docs/LOCAL_RUN_GUIDE.md` 참고.

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

글래스 단독 Wi-Fi 업로드 구조라 이동 중 AP 전환/음영 지역에서 끊김이 폰 테더링
시절보다 잦을 수 있다 — 위 큐/재연결 설계가 그 리스크의 완충 장치다.

## 알려진 제약

- **Blade 2 배터리/발열.** 카메라 상시 캡처 + H.264 인코딩 + Wi-Fi 업로드를
  글래스 단독으로 수행하므로 장시간 세션에서 배터리 소모와 발열이 데모 시간을
  제한할 수 있다(공식 스펙상 일반 사용 약 2시간). 데모 리허설에서 실측할 것.
- **마이크 16kHz 직접 캡처를 전제**로 하되, 실패 시 `DeviceMicAudioSource`가
  48kHz/44.1kHz 캡처 후 `PcmResampler`로 다운샘플링하는 폴백을 갖는다(선형
  보간이라 안티에일리어싱은 없음 — 폴백 경로 한정 허용).
- **UI 텍스트 입력.** Blade 2는 논터치라 host/port 입력이 번거롭다.
  `backend_config.json` 기본값을 데모 네트워크에 맞춰 빌드해 두는 것을 권장한다.
- 이 개발 환경에는 Android SDK/에뮬레이터/실기기가 없어 Kotlin 코드를 직접
  컴파일·실행해 검증하지 못했다 — Android Studio에서 Gradle 동기화와
  `./gradlew testDebugUnitTest` / `assembleDebug`로 재확인이 필요하다.
  특히 Blade 2 실기기에서 Camera2 YUV 스트라이드/색상, MediaCodec 인코더 컬러
  포맷 조합은 실기기 통합 단계에서 재검증할 것.
