"""UserPromptSubmit Hook: perform Injection without blocking the Host."""

from __future__ import annotations

import json
import os
import signal
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from integrations.agent_hooks.client import SidecarClient
    from integrations.agent_hooks.identity import session_id
    from integrations.agent_hooks.transport import (
        HOOK_TIMEOUT_SECONDS,
        MAX_INJECTION_CHARS,
    )
elif __package__:
    from .client import SidecarClient
    from .identity import session_id
    from .transport import HOOK_TIMEOUT_SECONDS, MAX_INJECTION_CHARS
else:
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    from client import SidecarClient
    from identity import session_id
    from transport import HOOK_TIMEOUT_SECONDS, MAX_INJECTION_CHARS


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


def _deadline_exceeded(_signum: int, _frame: object) -> None:
    raise TimeoutError("Hook wall-clock deadline exceeded")


def _run_with_deadline() -> None:
    previous_handler = signal.signal(signal.SIGALRM, _deadline_exceeded)
    armed = False
    try:
        signal.setitimer(signal.ITIMER_REAL, HOOK_TIMEOUT_SECONDS)
        armed = True
        _run()
    finally:
        if armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def main() -> None:
    """Run the Hook and unconditionally return success to the Host."""

    try:
        _run_with_deadline()
    except BaseException:
        try:
            print(_FAILURE_LINE, file=sys.stderr)
        except BaseException:
            pass


if __name__ == "__main__":
    main()
