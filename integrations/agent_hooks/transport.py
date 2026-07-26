"""Shared Unix-socket transport configuration."""

from __future__ import annotations

import os
from pathlib import Path

SOCKET_ENV = "MNEMOSYNE_HOOKS_SOCKET"
DEFAULT_SOCKET_NAME = ".mnemosyne-hooks.sock"
DATA_DIR_ENV = "MNEMOSYNE_HOOKS_DATA_DIR"
DEFAULT_DATA_DIR_NAME = ".mnemosyne-hooks"
HOOK_TIMEOUT_SECONDS = 0.75
MAX_INJECTION_CHARS = 12_000


def socket_path() -> Path:
    """Return the configured Sidecar socket path."""

    override = os.environ.get(SOCKET_ENV)
    return Path(override) if override else Path.home() / DEFAULT_SOCKET_NAME
