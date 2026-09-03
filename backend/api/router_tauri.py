"""POST /api/v1/tauri/invoke — dispatcher for the frontend's invoke() shim.

The browser frontend keeps the desktop app's `invoke("command", args)`
call sites; the shim relays them here and backend/commands/ dispatches
by command name. Response shape mirrors Tauri IPC: the handler's return
value becomes {ok: true, value: <result>} unless it already contains
"ok".
"""

from __future__ import annotations

import inspect

from fastapi import APIRouter, Request

from backend import commands, config
from backend.api.router_v1 import err, ok

router = APIRouter(prefix=config.API_PREFIX)


@router.post("/tauri/invoke")
async def tauri_invoke(request: Request) -> dict:
    body = await request.json()
    command_name = body.get("command")
    if not isinstance(command_name, str):
        raise err(400, "Missing command")
    handler = commands.COMMANDS.get(command_name)
    if handler is None:
        raise err(400, f"Unknown command: {command_name}")
    args = body.get("args")
    args = args if isinstance(args, dict) else {}
    try:
        result = handler(**args)
        if inspect.isawaitable(result):
            result = await result
    except NotImplementedError as exc:
        raise err(501, str(exc) or "Not implemented") from exc
    except TypeError as exc:
        raise err(400, f"Invalid arguments for {command_name}: {exc}") from exc
    except Exception as exc:
        from backend.core.file_service import FsError

        if isinstance(exc, FsError):
            raise err(400, str(exc)) from exc
        raise
    return ok({"value": result})
