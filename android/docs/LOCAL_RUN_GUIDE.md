# 로컬 실행 가이드 — 실기기 없이 녹화→업로드 전 구간 검증

에뮬레이터 + 목업 백엔드만으로 Vuzix Blade 2 온글래스 앱의 전체 경로
(세션 시작 → 30초 mp4 청크 업로드 → 오디오 WebSocket 스트리밍 → 세션 종료)를
끝까지 돌려보기 위한 절차서다. 마지막 절에 Blade 2 실기기 설치 절차도 있다.

앱의 구조·설계 배경은 `../README.md`, 업로드 API 계약은 루트 `CLAUDE.md`를 참고한다.
이 문서는 **"어떤 순서로 눌러야 실제로 동작하는가"** 만 다룬다.

> Blade 2는 Android 11(API 30) 표준 기기라서 과거 Meta DAT SDK 시절과 달리
> **별도 목업 SDK 없이 일반 에뮬레이터의 가상 카메라/마이크로 그대로 검증된다.**

---

## 사전 환경 (한 번만)

| 항목 | 요구 | 확인 방법 |
| --- | --- | --- |
| JDK | 17 | `java -version` |
| Android SDK | compileSdk 36 / **minSdk 30** | `$ANDROID_HOME/platforms/` 에 `android-36` |
| Gradle | 8.14.1 | 래퍼 동봉 — 별도 설치 불필요 |
| cmdline-tools | sdkmanager / avdmanager CLI 용 | Android Studio → Settings → Android SDK → **SDK Tools** 탭 → `Android SDK Command-line Tools (latest)` |
| AVD | **API 30 이상**(Blade 2와 같은 API 30 권장), arm64-v8a | Android Studio → Device Manager → Create Virtual Device |
| uv | 목업 백엔드 실행용 | `uv --version` |

사설 SDK 저장소 토큰(GitHub PAT)은 **더 이상 필요 없다** — 모든 의존성이
공개 저장소(google/mavenCentral)에서 내려받아진다.

### 셸 PATH

```bash
export ANDROID_HOME=$HOME/Library/Android/sdk
export PATH=$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH
```

### 환경 확인용 스모크 테스트

```bash
cd Mem2Life/android
./gradlew testDebugUnitTest
```

유닛 테스트가 전부 통과하면 툴체인·SDK가 정상이라는 뜻이다.
(리샘플러 5, 업로드 큐 3, 업로드 큐 레이스 1, 오디오 소켓 1)

---

## 0단계 — 에뮬레이터 카메라 준비 ⚠️

앱은 온보드 카메라를 Camera2로 직접 캡처한다. AVD의 **후면 카메라**가
`VirtualScene`(3D 가상 씬) 또는 `Webcam`으로 설정돼 있어야 한다:

- Android Studio → Device Manager → AVD 편집 → Show Advanced Settings →
  Camera **Back = VirtualScene** (또는 Webcam0)
- `Back = None`이면 카메라가 없어서 녹화 시작이
  `카메라 시작 실패: 사용 가능한 카메라가 없음`으로 떨어진다.

마이크는 에뮬레이터의 호스트 마이크 패스스루(Extended Controls → Microphone →
`Virtual microphone uses host audio input` 켜기)를 쓰거나, 앱의 "목업 오디오
소스 사용" 체크박스로 대체할 수 있다.

## 1단계 — 목업 백엔드 먼저 띄운다 (터미널 A) ⚠️

**반드시 첫 번째다.** `startRecording()`은 가장 먼저 `POST /sessions/start`를 치고,
실패하면 즉시 `RecordingState.Error("세션 시작 실패(백엔드 연결 확인)")`로 떨어져
녹화 자체가 시작되지 않는다.

```bash
cd Mem2Life/android/tools/mock-backend
uv sync    # 최초 1회 (이미 .venv가 있으면 즉시 끝남)
uv run uvicorn mock_backend.main:app --host 0.0.0.0 --port 8000 --reload
```

`--host 0.0.0.0`이 중요하다. `127.0.0.1`에 묶으면 에뮬레이터(`10.0.2.2`)도
Blade 2 실기기(LAN IP)도 접근하지 못한다.

이 터미널은 **띄워둔 채로 유지**한다. 청크가 올라올 때마다 로그가 실시간으로 찍힌다.

## 2단계 — APK 빌드 (터미널 B)

```bash
cd Mem2Life/android
./gradlew assembleDebug
# 산출물: app/build/outputs/apk/debug/app-debug.apk
```

## 3단계 — 에뮬레이터 부팅

```bash
emulator -avd Pixel_7 &
adb wait-for-device
adb shell getprop sys.boot_completed   # 1 이 나올 때까지 대기
```

## 4단계 — 설치 & 실행

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n com.mem2life.companion/.MainActivity
```

로그는 터미널 C에 따로 띄워두면 디버깅이 훨씬 쉽다. 앱 로그 태그에 콜론이
들어있어(`Mem2Life:BladeCamera` 등) `logcat -s` 필터 문법과 충돌하므로 grep을 쓴다:

```bash
adb logcat | grep --line-buffered "Mem2Life:"
# 특정 컴포넌트만: adb logcat | grep --line-buffered -E "Mem2Life:(BladeCamera|DeviceMic|RecordingCtrl|UploadQueue|VideoEncoder)"
```

앱 실행 직후 권한 팝업(카메라 / 마이크 / 알림)이 뜬다 — **전부 허용**한다.
알림 권한을 거부하면 포그라운드 서비스가 붙지 않아 앱을 백그라운드로 보냈을 때
녹화가 끊긴다.

---

## 5단계 — 앱 조작

화면은 위에서부터 `녹화` / `백엔드 설정` / `디버그` 순으로 배치돼 있다.

### 5-1. 백엔드 설정 확인

`app/src/main/assets/backend_config.json` 기본값이 이미 `10.0.2.2:8000`이라
**에뮬레이터 + 로컬 목업 백엔드 조합에서는 손대지 않아도 된다.**
Blade 2 실기기로 붙일 때만 백엔드 PC의 LAN IP로 바꾸고 "저장"을 누른다
(SharedPreferences에 저장되어 재빌드 불필요).

### 5-2. "목업 오디오 소스 사용" 체크박스 (기본 꺼짐)

기본값 꺼짐 = 온보드/에뮬레이터 마이크를 `DeviceMicAudioSource`로 직접 캡처.
호스트 마이크 패스스루가 안 되는 환경에서만 켠다(켜면 `MockPcmAudioSource`가
에셋 PCM 또는 440Hz 톤을 실시간 페이스로 흘려보낸다).

> 이 체크박스는 녹화 중 잠긴다. 녹화 중 변경하면 `remember(useMockAudio)`가 새
> 컨트롤러를 만들면서 기존 세션(살아있는 코루틴·업로드 큐·오디오 소켓)이 UI에서
> 끊긴 채 백그라운드에 남고 정지시킬 방법이 없어지기 때문에 의도적으로 막아둔 것이다.

### 5-3. "녹화 시작"

내부 실행 순서:

```
POST /sessions/start
  → 업로드 큐 시작 (디스크 큐)
  → 인코더 준비 (1280x720 @ 24fps)
  → 오디오 소스 시작 (온보드 마이크 16kHz, 실패해도 영상만으로 계속)
  → 온보드 카메라 Camera2 캡처 시작
```

---

## 6단계 — 성공 판단 기준

### 앱 화면

- `상태: Recording`
- `영상 청크 — 대기 중: 0, 업로드됨: 1`
  → **30초가 지나야 첫 청크가 잡힌다.** 그전까지 0인 것은 정상이다.
- `오디오 WebSocket: Connected (재연결로 인한 유실 구간 0회)`

### 터미널 A (목업 백엔드 로그)

```
세션 시작: a1b2c3d4e5f6 (title=None)
오디오 WebSocket 연결됨: session=a1b2c3d4e5f6
청크 수신: session=a1b2c3d4e5f6 seq=0 start_ts=0.0s duration=30.0s bytes=1234567
```

---

## 7단계 — "녹화 종료" 후 디스크 검증

종료 시 업로드 큐를 최대 10초간 드레인한 뒤(`QUEUE_DRAIN_GRACE_MS`)
`POST /sessions/{id}/end`를 보내고 요약 파일을 쓴다.

```bash
cd Mem2Life/android/tools/mock-backend
ls -la data/*/
cat data/*/session_summary.json

# 받은 청크가 실제 재생 가능한 mp4인지
ffprobe data/*/chunk_000000.mp4

# 오디오 PCM (16kHz mono s16le) 재생
ffplay -f s16le -ar 16000 -ac 1 data/*/audio_16k_mono_s16le.pcm
```

`session_summary.json`의 `video_chunks` 배열에서 `seq`가 **0, 1, 2 … 빠짐없이
순서대로** 들어있으면 업로드 큐가 계약대로 동작한 것이다.

---

## Blade 2 실기기 설치

1. 글래스에서 개발자 모드/USB 디버깅을 켠다 (Settings → About → Build number
   7회 탭 → Developer options → USB debugging).
2. USB-C로 연결 후:

```bash
adb devices                    # Blade 2가 보이는지 확인
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n com.mem2life.companion/.MainActivity
```

3. 글래스와 백엔드 PC를 **같은 Wi-Fi**에 물리고, 앱 "백엔드 설정"에서 host를
   PC의 LAN IP(예: `192.168.0.10`)로 바꾼 뒤 저장한다.
   (텍스트 입력이 번거로우면 `backend_config.json` 기본값을 데모 네트워크
   기준으로 바꿔 빌드해 두는 것이 편하다.)
4. 조작은 관자놀이 터치패드로 한다 — 앞/뒤 스와이프 = 포커스 이동,
   탭 = 클릭, 두 손가락 탭 = 뒤로가기.

---

## 트러블슈팅

| 증상 | 원인 / 조치 |
| --- | --- |
| `세션 시작 실패(백엔드 연결 확인)` | 목업 백엔드 미기동, 또는 `--host 127.0.0.1`로 띄움. 1단계 재확인 |
| `카메라 시작 실패: 사용 가능한 카메라가 없음` | AVD 카메라가 `None`. 0단계 재확인 |
| `카메라가 1280x720 YUV 출력을 지원하지 않음` | AVD/기기 카메라가 720p 미지원. AVD를 VirtualScene 카메라로 재생성 |
| 청크가 영원히 0개 | 30초가 아직 안 지났거나 카메라 프레임이 안 들어옴. logcat `Mem2Life:BladeCamera` 확인 |
| 오디오 WebSocket은 붙는데 소리가 무음 | 에뮬레이터 호스트 마이크 패스스루 꺼짐. Extended Controls에서 켜거나 목업 오디오 체크 |
| `adb root` 거부 | `google_apis_playstore` 이미지의 정상 동작. 앱 내부 파일은 `adb shell run-as com.mem2life.companion`으로 접근 |
| Blade 2에서 화면이 안 보임/어두움 | 웨이브가이드에서 검정=투명이 정상. 밝은 텍스트/버튼만 공중에 떠 보인다 |

---

## 이 경로로 검증되지 않는 것

- **Blade 2 실기기 카메라/마이크 특성.** 에뮬레이터 가상 카메라는 YUV 스트라이드,
  색감, 저조도 특성이 실기기와 다르다. 온보드 마이크의 16kHz 직접 캡처 지원 여부와
  노이즈캔슬링 특성도 실기기에서만 확인된다.
- **배터리/발열.** 카메라 상시 캡처 + 인코딩 + Wi-Fi 업로드를 글래스 단독으로
  수행할 때의 연속 동작 시간은 실기기 리허설로 실측해야 한다.
- **실제 백엔드 파이프라인.** 목업 백엔드는 STT/VLM/LLM/Obsidian 기록 로직이 전혀
  없고, 받은 파일을 디스크에 그대로 저장할 뿐이다. 실제 수신 서버는
  `Mem2Life/backend/`에 별도로 구현된다.
- **푸시투톡 질의 UI / TTS 응답 재생.** 현재 앱 범위 밖(후속 작업 — Vuzix Speech
  SDK 검토 예정).
