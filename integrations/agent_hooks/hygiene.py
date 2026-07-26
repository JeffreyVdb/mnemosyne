"""Pure turn-hygiene functions shared by Agent Hooks and cleanup tooling."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_PSEUDO_PROMPT_TAGS = (
    "task-notification",
    "system-reminder",
    "local-command-caveat",
    "local-command-stdout",
    "local-command-stderr",
    "command-name",
    "command-message",
    "command-args",
)
_PSEUDO_PROMPT = re.compile(
    r"<\s*(?:" + "|".join(_PSEUDO_PROMPT_TAGS) + r")(?:\s|>)",
    re.IGNORECASE,
)

_CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "PRIVATE_KEY",
        re.compile(
            r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"
            r".*?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    (
        "AWS_ACCESS_KEY",
        re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"),
    ),
    (
        "GITHUB_TOKEN",
        re.compile(
            r"(?<![A-Za-z0-9_])"
            r"(?:gh[pousr]_[A-Za-z0-9]{20,255}|github_pat_[A-Za-z0-9_]{20,255})"
        ),
    ),
    (
        "ANTHROPIC_API_KEY",
        re.compile(r"(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_-]{20,}"),
    ),
    (
        "OPENAI_API_KEY",
        re.compile(r"(?<![A-Za-z0-9_-])sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    ),
    (
        "SLACK_TOKEN",
        re.compile(r"(?<![A-Za-z0-9-])xox[baprs]-[A-Za-z0-9-]{10,}"),
    ),
    (
        "JWT",
        re.compile(
            r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}"
            r"\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
        ),
    ),
    (
        "BEARER_TOKEN",
        re.compile(r"(?i)(?<=\bBearer )[A-Za-z0-9._~+/=-]{20,}"),
    ),
    (
        "ASSIGNED_SECRET",
        re.compile(
            r"""(?ix)
            (?P<prefix>
                \b(?:[A-Za-z][A-Za-z0-9_-]{0,64}[_-])?
                (?:api[_-]?key|access[_-]?key|secret[_-]?access[_-]?key|
                access[_-]?token|auth[_-]?token|client[_-]?secret|
                password|passwd|secret|token)
                \s*(?:=|:)\s*
                ["']?
            )
            (?P<secret>[A-Za-z0-9._~+/=-]{12,})
            """
        ),
    ),
)


def extract_prompt(event: Mapping[str, Any], host: str) -> str:
    """Return the Host's prompt field, accepting the documented fallback."""

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


def extract_assistant(event: Mapping[str, Any]) -> str:
    """Return assistant content from the shared Stop Hook event field."""

    value = event.get("last_assistant_message")
    return value if isinstance(value, str) else ""


def is_pseudo_prompt(content: str) -> bool:
    """Return whether content contains a Host-generated prompt wrapper."""

    return bool(_PSEUDO_PROMPT.search(content))


def redact_credentials(content: str) -> str:
    """Replace recognized credentials while preserving surrounding content."""

    redacted = content
    for label, pattern in _CREDENTIAL_PATTERNS:
        if label == "ASSIGNED_SECRET":
            redacted = pattern.sub(
                lambda match: (
                    f"{match.group('prefix')}[REDACTED:{label}]"
                ),
                redacted,
            )
        else:
            redacted = pattern.sub(f"[REDACTED:{label}]", redacted)
    return redacted


def hygienic_prompt(content: str) -> str:
    """Reject pseudo-prompts, then redact credentials in accepted speech."""

    if is_pseudo_prompt(content):
        return ""
    return redact_credentials(content)
