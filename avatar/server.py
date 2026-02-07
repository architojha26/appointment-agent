"""
Avatar Server
==============
Serves the avatar HTML page and broadcasts speaking events via WebSocket.

Runs as an async task inside the main process event loop.
Reads from avatar_queue (mp.Queue) and broadcasts to all connected browsers.

Events from speaker process:
  {"type": "speaking_start", "text": "..."}
  {"type": "audio_energy", "energy": 0.0-1.0}
  {"type": "speaking_end"}
  {"type": "user_speaking", "text": "..."}
  {"type": "listening"}
  {"type": "shutdown"}
"""

import os
import json
import asyncio
import multiprocessing as mp
from pathlib import Path
from utils.logger import get_custom_logger

logger = get_custom_logger("avatar_server")

# We use the lightweight `websockets` approach with http fallback
# But since aiohttp is already a dependency (via cartesia), we use it

try:
    from aiohttp import web
except ImportError:
    web = None

AVATAR_PORT = int(os.getenv("AVATAR_PORT", "8765"))
HTML_PATH = Path(__file__).parent / "index.html"


class AvatarServer:
    def __init__(self, avatar_queue: mp.Queue, port: int = AVATAR_PORT, start_event: mp.Event = None):
        self.avatar_queue = avatar_queue
        self.port = port
        self.start_event = start_event
        self.clients: set[web.WebSocketResponse] = set()
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None

    async def start(self):
        """Start the HTTP + WebSocket server."""
        if web is None:
            logger.error("aiohttp not installed, avatar server disabled")
            return

        self._app = web.Application()
        self._app.router.add_get("/", self._serve_html)
        self._app.router.add_get("/ws", self._ws_handler)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()

        print(f"[avatar] 🎭 Avatar server running at http://localhost:{self.port}")
        print(f"[avatar] 🌐 Open in browser to see the avatar")

        # Start the queue reader task
        asyncio.create_task(self._read_queue())

    async def stop(self):
        """Shutdown the server."""
        # Close all websocket connections
        for ws in list(self.clients):
            try:
                await ws.close()
            except Exception:
                pass
        self.clients.clear()

        if self._runner:
            await self._runner.cleanup()
        print("[avatar] 🎭 Avatar server stopped")

    # ── HTTP handler ─────────────────────────────────────────────────

    async def _serve_html(self, request: web.Request) -> web.Response:
        if HTML_PATH.exists():
            return web.Response(
                text=HTML_PATH.read_text(),
                content_type="text/html",
            )
        return web.Response(text="Avatar HTML not found", status=404)

    # ── WebSocket handler ────────────────────────────────────────────

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.clients.add(ws)
        logger.info("Avatar client connected (%d total)", len(self.clients))

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        if data.get("action") == "start" and self.start_event:
                            if not self.start_event.is_set():
                                self.start_event.set()
                                logger.info("▶️ Start conversation triggered from browser")
                                # Broadcast start ack to all clients
                                await self._broadcast({"type": "conversation_started"})
                    except Exception:
                        pass
        finally:
            self.clients.discard(ws)
            logger.info("Avatar client disconnected (%d remaining)", len(self.clients))

        return ws

    # ── Queue reader → broadcast ─────────────────────────────────────

    async def _read_queue(self):
        """Read events from avatar_queue and broadcast to all WebSocket clients."""
        loop = asyncio.get_event_loop()

        while True:
            try:
                # Non-blocking read from mp.Queue using executor
                event = await loop.run_in_executor(
                    None, self._get_from_queue
                )
                if event is None:
                    await asyncio.sleep(0.02)
                    continue

                if event.get("type") == "shutdown":
                    break

                await self._broadcast(event)

            except Exception as e:
                logger.error("Queue reader error: %s", e)
                await asyncio.sleep(0.1)

    def _get_from_queue(self):
        """Blocking get with short timeout (runs in thread via executor)."""
        try:
            return self.avatar_queue.get(timeout=0.05)
        except Exception:
            return None

    async def _broadcast(self, event: dict):
        """Send event to all connected WebSocket clients."""
        if not self.clients:
            return

        data = json.dumps(event)
        dead = set()
        for ws in self.clients:
            try:
                await ws.send_str(data)
            except Exception:
                dead.add(ws)

        self.clients -= dead
