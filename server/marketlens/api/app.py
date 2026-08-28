"""FastAPI 앱.

키가 없는 프로바이더가 있어도 앱은 정상적으로 뜬다 — 그 프로바이더만 '비활성'으로
목록에 남는다. 하나 빠졌다고 전체가 안 뜨면 처음 여는 사람이 아무것도 볼 수 없다.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()  # 라우터가 환경변수를 읽기 전에 .env 를 올린다

from ..core.series import IndicatorRequest, candles_payload, compute_requests  # noqa: E402
from ..indicators import catalog  # noqa: E402
from ..signals.engine import evaluate  # noqa: E402
from .routes import load_candles, router  # noqa: E402
from .ws import hub  # noqa: E402

log = logging.getLogger("marketlens")
WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"

# 미확정 봉이 바뀔 때마다 지표를 다시 굴리면 1분봉에서 초당 수십 번이 된다.
# 봉이 닫히는 순간에는 무조건 계산하고, 그 사이에는 이 간격으로만.
LIVE_RECOMPUTE_SECONDS = 1.5

app = FastAPI(title="market-lens", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "indicators": len(catalog.catalog())}


@app.websocket("/ws")
async def live(socket: WebSocket) -> None:
    """구독 하나당 소켓 하나.

    클라이언트가 보내는 것: {action:"subscribe", provider, symbol, timeframe, indicators:[...]}
    서버가 보내는 것: snapshot(전체) → candle(갱신) → analysis(지표·시그널 재계산)
    """
    await socket.accept()
    room = queue = None
    reader: asyncio.Task | None = None
    # 보내는 쪽이 둘(실시간 루프 · 클라이언트 요청 응답)이라 잠근다.
    # 두 코루틴이 같은 소켓에 동시에 쓰면 프레임이 섞인다.
    send_lock = asyncio.Lock()

    async def send(payload: dict) -> None:
        async with send_lock:
            await socket.send_json(payload)

    try:
        request = await socket.receive_json()
        if request.get("action") != "subscribe":
            await send({"type": "error", "reason": "첫 메시지는 subscribe 여야 한다"})
            return

        provider = request["provider"]
        symbol = request["symbol"]
        timeframe = request.get("timeframe", "1h")
        limit = int(request.get("limit", 500))
        # 지표 선택은 도중에 바뀐다. 리스트를 통째로 갈아끼울 수 있게 한 겹 감싼다.
        selection = [_parse_requests(request.get("indicators"))]

        history = await load_candles(provider, symbol, timeframe, limit)
        room, queue = await hub.join(provider, symbol, timeframe, history)
        await send({
            "type": "snapshot",
            "provider": provider, "symbol": symbol, "timeframe": timeframe,
            "candles": candles_payload(room.df),
            **await _analysis(room.df, selection[0], timeframe),
        })

        async def read_client() -> None:
            """구독 중에도 클라이언트가 지표를 껐다 켠다. 그때 소켓을 다시 열지는 않는다."""
            while True:
                message = await socket.receive_json()
                if message.get("action") != "indicators":
                    continue
                selection[0] = _parse_requests(message.get("indicators"))
                await send({"type": "analysis",
                            **await _analysis(room.df, selection[0], timeframe)})

        reader = asyncio.create_task(read_client())

        last_sent = 0.0
        while True:
            if reader.done():
                await reader          # 읽기 쪽이 죽었으면 그 예외를 그대로 올린다
            message = await queue.get()
            await send(message)
            if message.get("type") != "candle":
                continue
            closed_bar = message["candle"].get("closed")
            now = time.monotonic()
            if closed_bar or now - last_sent >= LIVE_RECOMPUTE_SECONDS:
                last_sent = now
                await send({"type": "analysis",
                            **await _analysis(room.df, selection[0], timeframe)})

    except WebSocketDisconnect:
        pass
    except KeyError as exc:
        await _safe_send(socket, {"type": "error", "reason": f"빠진 항목: {exc}"})
    except Exception as exc:  # noqa: BLE001
        log.exception("웹소켓 처리 실패")
        await _safe_send(socket, {"type": "error", "reason": str(exc)})
    finally:
        if reader is not None:
            reader.cancel()
        if room is not None and queue is not None:
            await hub.leave(room, queue)


def _parse_requests(raw: list | None) -> list[IndicatorRequest]:
    items = raw if raw else [dict(d) for d in catalog.DEFAULT_SET]
    return [IndicatorRequest.parse(item, i) for i, item in enumerate(items)]


async def _analysis(df, requests, timeframe) -> dict:
    indicators, signal = await asyncio.gather(
        asyncio.to_thread(compute_requests, df, requests, timeframe),
        asyncio.to_thread(evaluate, df),
    )
    return {"indicators": indicators, "signal": signal.to_dict()}


async def _safe_send(socket: WebSocket, payload: dict) -> None:
    try:
        await socket.send_json(payload)
    except Exception:  # noqa: BLE001 - 이미 끊긴 소켓
        pass


# 빌드된 프론트가 있으면 같은 포트에서 서빙한다. 없으면 API 만 뜬다(dev 서버가 따로 돈다).
if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str) -> FileResponse:
        return FileResponse(WEB_DIST / "index.html")
