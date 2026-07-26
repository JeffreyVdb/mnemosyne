"""UserPromptSubmit Hook: inject recalled memory without blocking the Host."""

from __future__ import annotations

import json
import sys
from typing import Any

from .client import SidecarClient
from .identity import session_id
from .transport import HOOK_TIMEOUT_SECONDS, MAX_INJECTION_CHARS


_EVENT_NAME = "UserPromptSubmit"
_RECALLED_MEMORY_LABEL = "# Recalled memory\n\n"
_FAILURE_LINE = "Mnemosyne recalled memory unavailable."


def _prompt(event: dict[str, Any], host: str) -> str:
    fields = (
        ("user_prompt", "prompt")
        if host == "claude-code"
        else ("prompt", "user_prompt")
    )
    for field in fields:
        value = event.get(field)
        if isinstance(value, str) and value:
            return value
    return ""


def _run() -> None:
    try:
        host_index = sys.argv.index("--host")
        host = sys.argv[host_index + 1]
    except (ValueError, IndexError):
        raise ValueError("missing Host name") from None
    if host not in {"claude-code", "codex"}:
        raise ValueError("invalid Host name")
    event = json.load(sys.stdin)
    if not isinstance(event, dict):
        raise ValueError("Hook event must be a JSON object")
    prompt = _prompt(event, host)
    if not prompt:
        return
    result = SidecarClient(timeout=HOOK_TIMEOUT_SECONDS).prefetch(
        prompt,
        session_id(event, host),
    )
    if not result.ok or result.data is None:
        print(_FAILURE_LINE, file=sys.stderr)
        return
    context = result.data["context"]
    if not context:
        return
    additional_context = _RECALLED_MEMORY_LABEL + context
    if len(additional_context) > MAX_INJECTION_CHARS:
        print(_FAILURE_LINE, file=sys.stderr)
        return
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": _EVENT_NAME,
                "additionalContext": additional_context,
            }
        },
        sys.stdout,
        separators=(",", ":"),
    )
    sys.stdout.write("\n")


def main() -> None:
    """Run the Hook and unconditionally return success to the Host."""

    try:
        _run()
    except BaseException:
        try:
            print(_FAILURE_LINE, file=sys.stderr)
        except BaseException:
            pass


if __name__ == "__main__":
    main()
