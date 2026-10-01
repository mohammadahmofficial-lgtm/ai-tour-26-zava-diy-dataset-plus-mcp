#!/usr/bin/env python3
"""
Zava Root-Cause Agent — Web Chat Interface

A FastAPI web app that provides a chat window for interacting with the
Root-Cause Agent (src/python/root_cause_agent/root_cause_agent.py).

Run:
    python root_cause_agent_web.py
    → http://127.0.0.1:8007
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import AsyncGenerator, Dict, List

# Add the root_cause_agent folder to path so we can import the agent
AGENT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(AGENT_DIR))

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from root_cause_agent import (
    MAX_ITERATIONS,
    run_investigation,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("root-cause-web")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Zava Root-Cause Agent Chat")

# Static files (shared UI)
STATIC_DIR = Path(__file__).resolve().parents[2] / "shared" / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Dedicated chat page for the Root-Cause Agent
CHAT_PAGE = Path(__file__).resolve().parent / "static" / "index.html"


class ChatApp:
    """Handles the chat web interface for the Root-Cause Agent."""

    def __init__(self, fastapi_app: FastAPI) -> None:
        self.app = fastapi_app
        self.sessions: Dict[str, List[Dict]] = {}
        self._setup_routes()

    def _setup_routes(self) -> None:
        self.app.get("/", response_class=HTMLResponse)(self.get_chat_page)
        self.app.get("/favicon.ico", response_class=FileResponse)(self.get_favicon)
        self.app.get("/chat/stream")(self.stream_chat)
        self.app.get("/health")(self.health_check)

    async def get_chat_page(self) -> HTMLResponse:
        with CHAT_PAGE.open("r") as f:
            return HTMLResponse(content=f.read())

    async def get_favicon(self) -> FileResponse:
        favicon = STATIC_DIR / "favicon.ico"
        return FileResponse(favicon, media_type="image/x-icon")

    async def health_check(self) -> Dict:
        return {"status": "ok"}

    async def stream_chat(self, message: str = "") -> StreamingResponse:
        """Stream the Root-Cause Agent's investigation as SSE."""
        if not message.strip():
            return StreamingResponse(
                iter([f"data: {json.dumps({'error': 'Empty message'})}\n\n"]),
                media_type="text/event-stream",
            )

        return StreamingResponse(
            self._generate_stream(message),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "Access-Control-Allow-Origin": "*",
                "Content-Encoding": "identity",
            },
        )

    async def _generate_stream(self, message: str) -> AsyncGenerator[str, None]:
        """Run the agent and stream progress + final answer."""
        try:
            # Send a "thinking" event so the UI shows activity
            yield f"data: {json.dumps({'content': '🔍 Investigating...'})}\n\n"

            # Run the async agent directly (we're already in an async context)
            answer = await run_investigation(message)

            # Stream the final answer
            yield f"data: {json.dumps({'content': answer})}\n\n"
            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.exception("Agent error")
            yield f"data: {json.dumps({'error': f'Agent error: {e!s}'})}\n\n"
            yield "data: [DONE]\n\n"


# Instantiate the chat app
chat_app = ChatApp(app)


if __name__ == "__main__":
    import uvicorn

    print("🚀 Zava Root-Cause Agent Chat: http://127.0.0.1:8007")
    uvicorn.run(app, host="127.0.0.1", port=8007)