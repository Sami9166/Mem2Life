"""
업로드 API 계약(v1 초안)을 목업 백엔드가 정확히 구현하는지, 그리고 android
클라이언트가 이 계약을 상대로 어떻게 동작할지 FastAPI TestClient로 시뮬레이션한다.

실기기/에뮬레이터에서 Kotlin OkHttp 클라이언트를 직접 실행해 검증하는 것을
대체하지는 못한다(이 프로젝트 개발 환경에는 Android SDK/에뮬레이터가 없음) —
대신 여기서는 (1) 목업 서버가 계약대로 동작하는지, (2) Kotlin 클라이언트 코드가
가정하는 필드명/JSON 스키마/멀티파트 구조가 서버와 정확히 맞는지를 검증한다.
"""

from __future__ import annotations

import io
import json

from fastapi.testclient import TestClient

from mock_backend.main import app


def _fake_mp4_bytes(seq: int) -> bytes:
    # 실제 mp4 콘텐츠일 필요는 없다 — 서버는 바이트를 그대로 저장할 뿐이다.
    return f"FAKE_MP4_CHUNK_{seq}".encode()


def test_full_session_lifecycle() -> None:
    client = TestClient(app)

    # 1) POST /sessions/start
    start_resp = client.post(
        "/sessions/start", json={"title": "테스트 세션", "participants": ["현우", "친구"]}
    )
    assert start_resp.status_code == 200
    body = start_resp.json()
    assert "session_id" in body and "started_at" in body
    session_id = body["session_id"]

    # 2) POST /sessions/{id}/video-chunks — 30초 청크 3개를 seq 순서대로 업로드.
    #    필드명은 계약과 정확히 같아야 한다: chunk(mp4), seq, start_ts, duration_sec.
    for seq in range(3):
        chunk_resp = client.post(
            f"/sessions/{session_id}/video-chunks",
            data={
                "seq": str(seq),
                "start_ts": str(seq * 30.0),
                "duration_sec": "30.0",
            },
            files={"chunk": (f"chunk_{seq:06d}.mp4", io.BytesIO(_fake_mp4_bytes(seq)), "video/mp4")},
        )
        assert chunk_resp.status_code == 200, chunk_resp.text
        assert chunk_resp.json()["seq"] == seq

    # 3) WS /sessions/{id}/audio-stream — 첫 연결에서 프레임 몇 개를 보내고 끊은 뒤,
    #    재연결해서 이어 보낸다(그 사이 유실 구간은 계약대로 재전송하지 않는다).
    frame = bytes(640)  # 20ms @ 16kHz/16-bit/mono
    with client.websocket_connect(f"/sessions/{session_id}/audio-stream") as ws:
        for _ in range(5):
            ws.send_bytes(frame)
    # 재연결 시뮬레이션 — 새 WebSocket 연결.
    with client.websocket_connect(f"/sessions/{session_id}/audio-stream") as ws:
        for _ in range(5):
            ws.send_bytes(frame)

    # 4) POST /sessions/{id}/end
    end_resp = client.post(f"/sessions/{session_id}/end")
    assert end_resp.status_code == 200

    # 검증: 서버가 실제로 청크 3개 + 오디오 프레임 10개를 순서/개수 그대로 받았는지.
    debug_resp = client.get(f"/sessions/{session_id}")
    session_state = debug_resp.json()
    assert session_state["ended"] is True
    assert [c["seq"] for c in session_state["video_chunks"]] == [0, 1, 2]
    assert session_state["audio_frame_count"] == 10
    assert session_state["audio_byte_count"] == 10 * len(frame)


def test_video_chunk_unknown_session_returns_404() -> None:
    client = TestClient(app)
    resp = client.post(
        "/sessions/does-not-exist/video-chunks",
        data={"seq": "0", "start_ts": "0", "duration_sec": "30.0"},
        files={"chunk": ("chunk_000000.mp4", io.BytesIO(b"x"), "video/mp4")},
    )
    assert resp.status_code == 404
