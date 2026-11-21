#!/usr/bin/env python3
"""Claude Code PreToolUse adapter for the Forge guard.

Design: docs/intent/guard/guard-design.md (Adapter Contract).
Reads the hook input on stdin, does nothing unless agent_type starts with "forge-",
maps the call onto guard_core.decide, and blocks with exit 2 and a reason on stderr.
guard_core.py is imported from this file's own directory, where the installer puts both.
Everything before the version check runs on any python3.
"""

import json
import sys
from pathlib import Path

TARGET_FIELD = {"Write": "file_path", "Edit": "file_path", "NotebookEdit": "notebook_path"}


# @spec GUARD-SCOPE-001, GUARD-SCOPE-002, GUARD-ADAPT-001, GUARD-ADAPT-002, GUARD-ADAPT-003, GUARD-ADAPT-004
# @spec GUARD-ADAPT-005, GUARD-ADAPT-006
def main():
    try:
        data = json.loads(sys.stdin.read())
    except ValueError:
        return 0  # cannot be attributed to a Forge agent
    if not isinstance(data, dict):
        return 0
    agent = data.get("agent_type")
    if not isinstance(agent, str) or not agent.startswith("forge-"):
        return 0
    if sys.version_info < (3, 11):
        print("Forge guard blocked this call (PYTHON_TOO_OLD): the python3 on PATH is "
              f"{sys.version_info[0]}.{sys.version_info[1]}; Forge needs 3.11 or newer", file=sys.stderr)
        return 2
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import guard_core

        tool = data.get("tool_name")
        tool_input = data.get("tool_input")
        if not isinstance(tool_input, dict):
            raise ValueError("tool_input is not an object")
        call = {key: data.get(key) for key in ("agent_type", "agent_id", "session_id", "cwd", "scratchpad_dir")}
        call.update(tool_name=tool, tool_input=tool_input)
        if tool in TARGET_FIELD:
            call["target"] = tool_input[TARGET_FIELD[tool]]
        elif tool == "Bash":
            call["command"] = tool_input["command"]
        decision = guard_core.decide(call)
    except Exception as e:  # a guard that fails open drops every rule
        print(f"Forge guard error, call blocked: {e!r}", file=sys.stderr)
        return 2
    if decision["allow"]:
        return 0
    print(f"Forge guard blocked this call ({decision['code']}): {decision['reason']}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
