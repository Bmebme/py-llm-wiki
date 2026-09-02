"""SSE helpers — frame format port of api_server.rs sse_frame (1963).

Wire format: `event: <name>\\ndata: <json>\\n\\n`, heartbeat comment
`: keepalive` every 10 seconds.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from backend import config


def frame(event: str, data: dict | str) -> str:
    if isinstance(data, (dict, list)):
        data = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


def keepalive() -> str:
    return ": keepalive\n\n"


async def heartbeat(events: AsyncIterator[str], interval: float | None = None) -> AsyncIterator[str]:
    """Yield events, injecting a heartbeat comment every interval seconds."""
    interval = interval if interval is not None else config.SSE_HEARTBEAT_INTERVAL
    done = asyncio.Event()

    async def timer() -> None:
        while not done.is_set():
            await asyncio.sleep(interval)
            if not done.is_set():
                queue.put_nowait(keepalive())

    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)

    async def producer() -> None:
        async for item in events:
            await queue.put(item)
        done.set()
        await queue.put(None)  # sentinel

    timer_task = asyncio.create_task(timer())
    producer_task = asyncio.create_task(producer())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
    finally:
        done.set()
        timer_task.cancel()
        producer_task.cancel()
