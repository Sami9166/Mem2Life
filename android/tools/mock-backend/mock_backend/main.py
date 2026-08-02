"""
android 컴패니언 앱의 업로드 클라이언트를 실제 서버 없이 검증하기 위한 목업 백엔드.

루트 CLAUDE.md "업로드 API 계약 (android ↔ wiki-builder, v1 초안)"과 정확히 같은
엔드포인트 4개만 구현한다. 실제 STT/VLM/LLM 파이프라인은 전혀 없다 — 받은 파일과
오디오 프레임을 디스크에 그대로 저장해서 눈으로 확인할 수 있게 하는 것이 전부다.

주의: 이것은 wiki-builder가 Mem2Life/backend/에 구현할 실제 수신 서버가 아니다.
android-dev가 자기 클라이언트 코드를 끝까지(세션 시작 -> 청크 업로드 -> 오디오
스트리밍 -> 세션 종료) 검증하기 위한 1회성 개발 도구다.

실행:
    cd Mem2Life/android/tools/mock-backend
    uv sync
    uv run uvicorn mock_backend.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

logger = logging.getLogger("mock_backend")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Mem2Life Mock Backend (android-dev 검증용)")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 세션 종료 시 backend의 ingest 글루(전사→볼트)를 자동 트리거할지 여부.
# 계약상 /end는 "최종 파이프라인을 비동기 트리거"하므로 기본 on. 앱 업로드 경로만
# 검증할 때는(불필요한 RTZR 호출/비용 방지) MOCK_BACKEND_AUTO_INGEST=0 으로 끈다.
# mock-backend(android/tools/mock-backend/mock_backend/main.py)에서 backend까지: parents[4].
_BACKEND_DIR = Path(__file__).resolve().parents[4] / "backend"
_GLUE_SCRIPT = _BACKEND_DIR / "tools" / "ingest_from_upload.py"
_AUTO_INGEST = os.environ.get("MOCK_BACKEND_AUTO_INGEST", "1") not in ("0", "false", "False", "")


async def _trigger_pipeline(session_id: str) -> None:
    """세션 종료 후 backend 글루(PCM→RTZR 전사→볼트 세션.md)를 backend venv에서 실행한다.

    mock-backend와 backend는 별개 프로젝트/venv라 import가 아닌 subprocess로 호출한다
    (mock-backend에 RTZR/ingest 의존성을 섞지 않기 위함). 실패해도 /end 응답과 무관하게
    로그만 남긴다 — 목업 도구이므로 파이프라인 실패가 업로드 검증을 막지 않아야 한다.
    """
    if not _AUTO_INGEST:
        return
    if not _GLUE_SCRIPT.exists():
        logger.warning("자동 ingest 스킵: 글루 스크립트 없음 (%s)", _GLUE_SCRIPT)
        return
    session_dir = _session_dir(session_id)
    cmd = ["uv", "run", "python", "tools/ingest_from_upload.py", str(session_dir), "--title", session_id]
    logger.info("자동 파이프라인 트리거: session=%s → %s", session_id, " ".join(cmd))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(_BACKEND_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        out = (stdout or b"").decode("utf-8", errors="replace").strip()
        if proc.returncode == 0:
            logger.info("자동 파이프라인 완료: session=%s\n%s", session_id, out)
        else:
            logger.error("자동 파이프라인 실패(code=%s): session=%s\n%s", proc.returncode, session_id, out)
    except Exception:
        logger.exception("자동 파이프라인 실행 중 예외: session=%s", session_id)

# 메모리에만 유지 — 재시작하면 사라진다(목업이므로 충분).
_sessions: dict[str, dict] = {}


def _session_dir(session_id: str) -> Path:
    return DATA_DIR / session_id


@app.post("/sessions/start")
async def start_session(request: Request) -> JSONResponse:
    body: dict = {}
    if request.headers.get("content-length") not in (None, "0"):
        try:
            body = await request.json()
        except Exception:
            body = {}

    session_id = uuid.uuid4().hex[:12]
    started_at = datetime.now(timezone.utc).isoformat()
    _sessions[session_id] = {
        "title": body.get("title"),
        "participants": body.get("participants"),
        "started_at": started_at,
        "video_chunks": [],
        "audio_frame_count": 0,
        "audio_byte_count": 0,
        "ended": False,
    }
    session_dir = _session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    logger.info("세션 시작: %s (title=%s)", session_id, body.get("title"))

    return JSONResponse({"session_id": session_id, "started_at": started_at})


@app.post("/sessions/{session_id}/video-chunks")
async def upload_video_chunk(
    session_id: str,
    chunk: UploadFile,
    seq: int = Form(...),
    start_ts: float = Form(...),
    duration_sec: float = Form(...),
) -> JSONResponse:
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"알 수 없는 session_id: {session_id}")

    session_dir = _session_dir(session_id)
    chunk_path = session_dir / f"chunk_{seq:06d}.mp4"
    contents = await chunk.read()
    chunk_path.write_bytes(contents)

    session["video_chunks"].append(
        {
            "seq": seq,
            "start_ts": start_ts,
            "duration_sec": duration_sec,
            "bytes": len(contents),
        }
    )
    logger.info(
        "청크 수신: session=%s seq=%s start_ts=%.1fs duration=%.1fs bytes=%d",
        session_id,
        seq,
        start_ts,
        duration_sec,
        len(contents),
    )
    return JSONResponse({"ok": True, "seq": seq})


@app.websocket("/sessions/{session_id}/audio-stream")
async def audio_stream(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    session = _sessions.get(session_id)
    if session is None:
        await websocket.close(code=4404, reason=f"알 수 없는 session_id: {session_id}")
        return

    session_dir = _session_dir(session_id)
    audio_path = session_dir / "audio_16k_mono_s16le.pcm"
    logger.info("오디오 WebSocket 연결됨: session=%s", session_id)
    try:
        with audio_path.open("ab") as f:
            while True:
                frame = await websocket.receive_bytes()
                f.write(frame)
                session["audio_frame_count"] += 1
                session["audio_byte_count"] += len(frame)
    except WebSocketDisconnect:
        logger.info(
            "오디오 WebSocket 연결 끊김: session=%s (누적 프레임=%d, 바이트=%d) — "
            "재연결 시 유실 구간은 채워지지 않는다(계약대로).",
            session_id,
            session["audio_frame_count"],
            session["audio_byte_count"],
        )


@app.post("/sessions/{session_id}/end")
async def end_session(session_id: str) -> JSONResponse:
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"알 수 없는 session_id: {session_id}")

    session["ended"] = True
    summary_path = _session_dir(session_id) / "session_summary.json"
    summary_path.write_text(json.dumps(session, ensure_ascii=False, indent=2))
    logger.info(
        "세션 종료: session=%s (청크 %d개, 오디오 프레임 %d개)",
        session_id,
        len(session["video_chunks"]),
        session["audio_frame_count"],
    )
    # 계약: /end는 최종 파이프라인을 "비동기 트리거"한다. 응답을 막지 않도록
    # 백그라운드 태스크로 전사→볼트 글루를 돌린다(결과는 서버 로그로 확인).
    asyncio.create_task(_trigger_pipeline(session_id))
    return JSONResponse({"ok": True})


@app.get("/sessions/{session_id}")
async def debug_get_session(session_id: str) -> JSONResponse:
    """계약에는 없는 디버그 전용 엔드포인트 — 검증 스크립트에서 결과 확인용."""
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"알 수 없는 session_id: {session_id}")
    return JSONResponse(session)
