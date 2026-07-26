"""Stop Hook: acknowledge Capture without waiting on the Bank write."""

from __future__ import annotations

import json
import os
import signal
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from integrations.agent_hooks.client import SidecarClient
    from integrations.agent_hooks.hygiene import (
        extract_assistant,
        redact_credentials,
    )
    from integrations.agent_hooks.identity import session_id
    from integrations.agent_hooks.transport import HOOK_TIMEOUT_SECONDS
    from integrations.agent_hooks.turn_state import (
        capture_suppressed,
        clear_prompt,
        prompt_state,
    )
elif __package__:
    from .client import SidecarClient
    from .hygiene import extract_assistant, redact_credentials
    from .identity import session_id
    from .transport import HOOK_TIMEOUT_SECONDS
    from .turn_state import capture_suppressed, clear_prompt, prompt_state
else:
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    from client import SidecarClient
    from hygiene import extract_assistant, redact_credentials
    from identity import session_id
    from transport import HOOK_TIMEOUT_SECONDS
    from turn_state import capture_suppressed, clear_prompt, prompt_state


_FAILURE_LINE = "Mnemosyne capture unavailable."


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
    current_session_id = session_id(event, host)
    if capture_suppressed(event):
        clear_prompt(current_session_id)
        return
    prompt_was_submitted, user_content = prompt_state(current_session_id)
    if prompt_was_submitted and not user_content:
        clear_prompt(current_session_id)
        return
    assistant_content = redact_credentials(extract_assistant(event))
    if not user_content and not assistant_content:
        return
    result = SidecarClient(timeout=HOOK_TIMEOUT_SECONDS).capture(
        user_content,
        assistant_content,
        current_session_id,
    )
    if not result.ok:
        print(_FAILURE_LINE, file=sys.stderr)
        return
    clear_prompt(current_session_id)


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
