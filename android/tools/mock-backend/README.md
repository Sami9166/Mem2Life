# Mem2Life Mock Backend (android-dev 검증용)

`Mem2Life/android/` 컴패니언 앱의 업로드 클라이언트(`SessionApiClient`,
`VideoChunkUploadQueue`, `AudioStreamSocket`)를 실제 백엔드 없이 끝까지
검증하기 위한 1회성 개발 도구다. 루트 `CLAUDE.md`의 "업로드 API 계약 (android
↔ wiki-builder, v1 초안)"에 정의된 4개 엔드포인트만 그대로 구현한다.

**이것은 실제 수신 서버가 아니다.** STT/VLM/LLM/Obsidian 기록 로직은 전혀 없고,
받은 mp4 청크와 오디오 PCM 프레임을 디스크에 그대로 저장할 뿐이다. 실제 서버는
wiki-builder가 `Mem2Life/backend/`에 별도로 구현한다.

## 실행

```bash
cd Mem2Life/android/tools/mock-backend
uv sync
uv run uvicorn mock_backend.main:app --host 0.0.0.0 --port 8000 --reload
```

에뮬레이터에서 폰 앱을 실행한다면 앱의 백엔드 설정 화면에서 host를 `10.0.2.2`
(에뮬레이터가 보는 호스트 PC의 localhost)로 두면 된다. 실기기로 테스트한다면
호스트 PC의 LAN IP를 쓴다.

## 계약 검증 테스트

```bash
uv run pytest -q
```

`tests/test_contract.py`는 FastAPI `TestClient`로 세션 시작 -> 영상 청크 업로드
(seq 순서) -> 오디오 WebSocket 스트리밍(연결 끊김 후 재연결까지 시뮬레이션) ->
세션 종료의 전체 흐름을 검증한다.

## 확인된 이슈 (이 목업을 만들며 발견)

`seq`/`start_ts`/`duration_sec`를 FastAPI 핸들러 시그니처에 평범한 `int`/`float`
파라미터로 선언하면, `UploadFile`과 같은 요청에 있어도 FastAPI는 이를 **쿼리
파라미터**로 취급하고 멀티파트 폼 필드로 파싱하지 않는다(422 오류). Android
클라이언트(OkHttp `MultipartBody`)는 이 세 필드를 모두 멀티파트 폼 필드로
보낸다 — 그래서 서버 쪽에서는 반드시 `Form(...)`으로 선언해야 한다
(`mock_backend/main.py`의 `upload_video_chunk` 참고). **wiki-builder가 실제
서버를 구현할 때 이 부분을 놓치지 않도록 특히 주의할 것.**

## 디렉터리

```
mock_backend/main.py   # FastAPI 앱 (계약의 4개 엔드포인트 + 디버그용 GET)
tests/test_contract.py # 계약 검증 테스트
data/                  # 실행 중 받은 청크/오디오/세션 요약 저장 위치 (git 무시)
```
