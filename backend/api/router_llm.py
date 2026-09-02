"""POST /api/v1/llm/proxy — transparent HTTP relay for the frontend.

The desktop app routed browser LLM calls through Tauri's Rust HTTP
plugin to dodge provider CORS headers. The web port keeps that pattern:
the frontend's fetch shim posts the request here and the backend relays
it server-side, streaming the raw SSE body back.

Security: strips the browser Origin header (meaningless to providers),
caps body at 10MB, applies a 30-minute timeout.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from backend import config

router = APIRouter(prefix=config.API_PREFIX)

MAX_PROXY_BODY = 10 * 1024 * 1024
PROXY_TIMEOUT = httpx.Timeout(connect=10.0, read=1800.0, write=30.0, pool=10.0)
STRIPPED_REQUEST_HEADERS = {"origin", "host", "content-length", "connection", "accept-encoding"}


@router.post("/llm/proxy")
async def llm_proxy(request: Request):
    body = await request.json()
    url = body.get("url")
    if not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://")):
        return Response(
            status_code=400,
            content=b'{"ok":false,"error":"Invalid url"}',
            media_type="application/json",
        )
    method = (body.get("method") or "POST").upper()
    if method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        method = "POST"

    headers = body.get("headers")
    headers = {k: v for k, v in headers.items()} if isinstance(headers, dict) else {}
    for key in STRIPPED_REQUEST_HEADERS:
        headers.pop(key, None)

    payload = body.get("body")
    if isinstance(payload, (dict, list)):
        import json

        payload = json.dumps(payload, ensure_ascii=False)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if isinstance(payload, bytes) and len(payload) > MAX_PROXY_BODY:
        return Response(
            status_code=413,
            content=b'{"ok":false,"error":"Request body is too large"}',
            media_type="application/json",
        )

    client = httpx.AsyncClient(timeout=PROXY_TIMEOUT)
    upstream = await client.send(
        httpx.Request(method=method, url=url, headers=headers, content=payload),
        stream=True,
    )
    upstream_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() in ("content-type",)
    }

    async def body_stream():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    # The frontend shim needs the upstream status; surface it in a header.
    upstream_headers["x-proxy-status"] = str(upstream.status_code)
    return StreamingResponse(body_stream(), headers=upstream_headers, status_code=200)
