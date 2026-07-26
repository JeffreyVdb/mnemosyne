"""UserPromptSubmit Hook: perform Injection without blocking the Host."""

from __future__ import annotations

import json
import os
import signal
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from integrations.agent_hooks.client import SidecarClient
    from integrations.agent_hooks.hygiene import (
        extract_prompt,
        hygienic_prompt,
        redact_credentials,
    )
    from integrations.agent_hooks.identity import session_id
    from integrations.agent_hooks.transport import (
        HOOK_TIMEOUT_SECONDS,
        MAX_INJECTION_CHARS,
    )
    from integrations.agent_hooks.turn_state import (
        capture_suppressed,
        clear_prompt,
        save_prompt,
    )
elif __package__:
    from .client import SidecarClient
    from .hygiene import extract_prompt, hygienic_prompt, redact_credentials
    from .identity import session_id
    from .transport import HOOK_TIMEOUT_SECONDS, MAX_INJECTION_CHARS
    from .turn_state import capture_suppressed, clear_prompt, save_prompt
else:
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    from client import SidecarClient
    from hygiene import extract_prompt, hygienic_prompt, redact_credentials
    from identity import session_id
    from transport import HOOK_TIMEOUT_SECONDS, MAX_INJECTION_CHARS
    from turn_state import capture_suppressed, clear_prompt, save_prompt


_EVENT_NAME = "UserPromptSubmit"
_RECALLED_MEMORY_LABEL = "# Recalled memory\n\n"
_FAILURE_LINE = "Mnemosyne recalled memory unavailable."


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
    prompt = extract_prompt(event, host)
    if not prompt:
        return
    current_session_id = session_id(event, host)
    capture_prompt = hygienic_prompt(prompt)
    if capture_suppressed(event):
        clear_prompt(current_session_id)
    else:
        if capture_prompt:
            save_prompt(current_session_id, capture_prompt)
        else:
            save_prompt(current_session_id, "")
    result = SidecarClient(timeout=HOOK_TIMEOUT_SECONDS).prefetch(
        redact_credentials(prompt),
        current_session_id,
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
