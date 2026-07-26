"""Durable Hook-side pairing state for one completed Host turn."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from integrations.agent_hooks.transport import DATA_DIR_ENV, DEFAULT_DATA_DIR_NAME
elif __package__:
    from .transport import DATA_DIR_ENV, DEFAULT_DATA_DIR_NAME
else:
    import sys

    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    from transport import DATA_DIR_ENV, DEFAULT_DATA_DIR_NAME


CAPTURE_SUPPRESS_ENV = "MNEMOSYNE_CAPTURE_SUPPRESS"
CAPTURE_SUPPRESS_DIRS_ENV = "MNEMOSYNE_CAPTURE_SUPPRESS_DIRS"


def _data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    return Path(override) if override else Path.home() / DEFAULT_DATA_DIR_NAME


def _state_path(session_id: str) -> Path:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return _data_dir() / "pending" / f"{digest}.json"


def capture_suppressed(event: dict[str, Any]) -> bool:
    """Return whether Capture is suppressed for this Host process or directory."""

    value = os.environ.get(CAPTURE_SUPPRESS_ENV, "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    directory = event.get("cwd")
    if not isinstance(directory, str) or not directory:
        return False
    current = Path(directory).expanduser().resolve(strict=False)
    configured = os.environ.get(CAPTURE_SUPPRESS_DIRS_ENV, "")
    for raw_path in configured.split(os.pathsep):
        if not raw_path:
            continue
        suppressed = Path(raw_path).expanduser().resolve(strict=False)
        if current == suppressed or suppressed in current.parents:
            return True
    return False


def save_prompt(session_id: str, prompt: str) -> bool:
    """Atomically persist an already-hygienic prompt with owner-only modes."""

    path = _state_path(session_id)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"prompt": prompt}, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return True
    except (OSError, TypeError, ValueError):
        return False


def load_prompt(session_id: str) -> str:
    """Return the pending prompt without consuming it."""

    try:
        payload = json.loads(_state_path(session_id).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return ""
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    return prompt if isinstance(prompt, str) else ""


def prompt_state(session_id: str) -> tuple[bool, str]:
    """Return whether submit-time state exists and its hygienic prompt."""

    path = _state_path(session_id)
    if not path.is_file():
        return False, ""
    return True, load_prompt(session_id)


def clear_prompt(session_id: str) -> None:
    """Remove paired state after acknowledgement or explicit suppression."""

    try:
        _state_path(session_id).unlink(missing_ok=True)
    except OSError:
        pass
