"""Chat agent 与外部 CLI 代理命令。

agent_start_turn 是前端 chat-panel 的非流式聊天入口（浏览器版用
web-agent.ts 替换了流式变体）。claude_cli / codex_cli 系列在浏览器
移植版不受支持，保留为明确的 NotImplemented 以便 UI 给出清晰提示。
"""
from __future__ import annotations

from backend.commands.misc_commands import command

COMMANDS: dict[str, callable] = {}


@command("agent_start_turn")
async def agent_start_turn(
    projectId: str = "current", request: dict | None = None
) -> dict:
    """运行一轮非流式 agent 对话, 返回聚合 done 帧
    (sessionId/mode/message/references/toolEvents/usage)。"""
    from backend.chat.agent import AgentRequest, ChatAgent
    from backend.core import project_registry

    project = project_registry.resolve_project(projectId)
    agent_request = AgentRequest.from_body(request or {})
    agent = ChatAgent(project)
    return await agent.run(agent_request)


@command("agent_start_turn_stream")
def agent_start_turn_stream(**kwargs) -> None:
    raise NotImplementedError(
        "Streaming agent turns use the SSE /chat endpoint in the browser port"
    )


@command("claude_cli_spawn")
def claude_cli_spawn(**kwargs) -> None:
    raise NotImplementedError(
        "Claude CLI agent mode is not supported in the browser port"
    )


@command("claude_cli_kill")
def claude_cli_kill(**kwargs) -> None:
    raise NotImplementedError(
        "Claude CLI agent mode is not supported in the browser port"
    )


@command("codex_cli_spawn")
def codex_cli_spawn(**kwargs) -> None:
    raise NotImplementedError(
        "Codex CLI agent mode is not supported in the browser port"
    )


@command("codex_cli_kill")
def codex_cli_kill(**kwargs) -> None:
    raise NotImplementedError(
        "Codex CLI agent mode is not supported in the browser port"
    )
