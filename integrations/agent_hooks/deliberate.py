"""CLI used by plugin skills for deliberate memory operations."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from integrations.agent_hooks.client import ClientResult, SidecarClient
    from integrations.agent_hooks.identity import session_id
elif __package__:
    from .client import ClientResult, SidecarClient
    from .identity import session_id
else:
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    from client import ClientResult, SidecarClient
    from identity import session_id


def _run(operation: str, value: str, host: str) -> ClientResult:
    client = SidecarClient(timeout=3.0)
    current_session_id = session_id({}, host)
    return getattr(client, operation)(value, current_session_id)


def main() -> int:
    """Run one deliberate operation and make failures visible to the developer."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", choices=("claude-code", "codex"), required=True)
    parser.add_argument("operation", choices=("remember", "recall", "forget"))
    parser.add_argument("value")
    arguments = parser.parse_args()

    result = _run(arguments.operation, arguments.value, arguments.host)
    if result.timed_out:
        print(
            f"Mnemosyne {arguments.operation} outcome unknown: request timed out; "
            "check with recall before retrying",
            file=sys.stderr,
        )
        return 1
    if not result.ok or result.data is None:
        print(
            f"Mnemosyne {arguments.operation} failed: "
            f"{result.error or 'invalid response'}",
            file=sys.stderr,
        )
        return 1
    if "error" in result.data:
        print(
            f"Mnemosyne {arguments.operation} failed: {result.data['error']}",
            file=sys.stderr,
        )
        return 1
    json.dump(result.data, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
