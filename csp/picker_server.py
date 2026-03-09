import asyncio
import colorsys
import json
import os
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from csp.config import config
from csp.csp_client import CSPClient
from csp.qr_extractor import get_csp_url_from_screen

csp_client: CSPClient | None = None
client_thread = None
broadcast_thread = None

# ── Config ────────────────────────────────────────────────────────────────────
PORT = config.port
HOST = config.host
BASE_DIR = config.web_base_dir

app = FastAPI(title="CSP ColorPicker server")
_clients: set[WebSocket] = set()
_loop: asyncio.AbstractEventLoop | None = None


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/")
async def serve_index():
    return FileResponse(Path(BASE_DIR) / "index.html")


# ── WebSocket endpoint ────────────────────────────────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)
    print(f"[ws] client connected  ({len(_clients)} total)")

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[ws] invalid JSON: {raw!r}")
                continue
            handle_message(msg)
    except WebSocketDisconnect:
        _clients.discard(ws)
        print(f"[ws] client disconnected  ({len(_clients)} remaining)")


# ── Message handler  ──────────────────────────────────────────────────────────
def do_csp_client_sync():  # Called in thread to sync csp color => client
    while True:
        if csp_client is not None:
            selected_rgb = csp_client.cached_current_color
            selected_rgb = colorsys.hsv_to_rgb(selected_rgb[0] / 255, selected_rgb[1] / 255, selected_rgb[2] / 255)
            selected_rgb = [round(x * 255) for x in selected_rgb]

            broadcast_sync(
                {
                    "type": "sync",
                    "selected_rgb": selected_rgb,
                    "opacity": csp_client.brush_opacity,
                    "is_main": csp_client.color_is_main,
                    "hsv_main": csp_client.hsv_main,
                    "hsv_sub": csp_client.hsv_sub,
                    "is_connected": csp_client.connected,
                }
            )
        time.sleep(config.client_refresh_rate_s) # Going faster pointless since CSPClient only updates color at this rate


def handle_message(msg: dict) -> None:
    global csp_client, client_thread, broadcast_thread  # noqa: PLW0603
    msg_type = msg.get("type", "unknown")

    # ── Color change from the picker ──────────────────────────────
    if msg_type == "c": # colorChange
        r = msg.get("r", 0) or 0 # In case NaN
        g = msg.get("g", 0) or 0
        b = msg.get("b", 0) or 0
        alpha = msg.get("a", 1.0)
        if not alpha: # In case NaN -> None
            alpha = 1

        if csp_client is not None:
            r, g, b = [round(x) for x in (r, g, b)]
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            csp_client.setcolor(round(h * 255), round(s * 255), round(v * 255))
            if abs(alpha - csp_client.cached_current_color[3]) > 1e-4:
                csp_client.set_brush_opacity(alpha)

    # ── Terminate the color picker ──────────────────────────────
    elif msg_type == "close":
        print("[close] Close requested")
        os._exit(0)

    # ── QR locate button ──────────────────────────────────────────
    elif msg_type == "qr": # qrLocate
        print("[qr]  locate requested")

        TOTAL_ATTEMPTS = 20
        for attempt in range(TOTAL_ATTEMPTS):
            url = get_csp_url_from_screen()
            if url is not None:
                # Cleanup old client instance
                if csp_client is not None:
                    csp_client.client_socket.close()

                csp_client = CSPClient(url, sync_rate=config.client_refresh_rate_s)
                client_thread = threading.Thread(target=csp_client.connect, daemon=True)
                client_thread.start()

                if broadcast_thread is None:
                    broadcast_thread = threading.Thread(target=do_csp_client_sync, daemon=True)
                    broadcast_thread.start()
                break

            print(f"[qr]  Failed to find url, please click File > Connect smartphone, attempt {attempt + 1} / {TOTAL_ATTEMPTS}")
            time.sleep(1)
    # ── Catch-all ─────────────────────────────────────────────────
    else:
        print(f"[ws]  unhandled message  type={msg_type!r}  data={msg}")


# ── Broadcast helper ─────────────────────────────────────────────────────────
async def broadcast(data: dict) -> int:
    """
    Send a JSON message to all connected clients.
    Returns the number of clients that received it.

    Usage from async context:
        await server.broadcast({"type": "ping"})

    Usage from sync/thread context:
        asyncio.run_coroutine_threadsafe(
            server.broadcast({"type": "ping"}), server._loop
        )
    """
    if not _clients:
        return 0

    payload = json.dumps(data)
    dead: set[WebSocket] = set()
    count = 0

    for ws in list(_clients):
        try:
            await ws.send_text(payload)
            count += 1
        except Exception:  # noqa: PERF203
            dead.add(ws)

    _clients.difference_update(dead)
    return count


def broadcast_sync(data: dict) -> None:
    """
    Thread-safe broadcast — call this from non-async code.
    Requires that the server is running (start() has been called).
    """
    if _loop and _loop.is_running():
        asyncio.run_coroutine_threadsafe(broadcast(data), _loop)


# ── Startup hook to capture event loop ───────────────────────────────────────
@app.on_event("startup")
async def _capture_loop():
    global _loop  # noqa: PLW0603
    _loop = asyncio.get_running_loop()


# ── Entry point ───────────────────────────────────────────────────────────────
def start(port: int = PORT, host: str = HOST) -> None:
    """Block and run the server. Call from a daemon thread in main.py."""
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    print(f"[server] listening on http://{HOST}:{PORT}")
    start()
