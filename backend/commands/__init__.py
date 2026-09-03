"""Command handlers mirroring llm_wiki's src-tauri/src/commands/*.rs.

The browser frontend kept the desktop app's `invoke("command", args)`
calls; the web shim routes them to POST /api/v1/tauri/invoke and this
package dispatches by command name.
"""

from backend.commands import agent_commands, fs_commands, misc_commands, project_commands
from backend.commands.misc_commands import command

COMMANDS: dict[str, callable] = {}
COMMANDS.update(fs_commands.COMMANDS)
COMMANDS.update(project_commands.COMMANDS)
COMMANDS.update(misc_commands.COMMANDS)
COMMANDS.update(agent_commands.COMMANDS)
