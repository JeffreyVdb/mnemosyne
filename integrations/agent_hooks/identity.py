"""Session identity derivation for Host Hook events."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from integrations.agent_hooks.transport import DATA_DIR_ENV, DEFAULT_DATA_DIR_NAME
elif __package__:
    from .transport import DATA_DIR_ENV, DEFAULT_DATA_DIR_NAME
else:
    from transport import DATA_DIR_ENV, DEFAULT_DATA_DIR_NAME


_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


def _component(value: str, fallback: str) -> str:
    cleaned = _SAFE_COMPONENT.sub("-", value.strip()).strip("-")
    return cleaned or fallback


def repository_name(directory: str) -> str:
    """Resolve the parent repository name, collapsing linked worktrees."""

    path = Path(directory).expanduser()
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=0.25,
        )
        common_dir = Path(result.stdout.strip())
        repository = common_dir.parent if common_dir.name == ".git" else common_dir
        name = repository.name.removesuffix(".git")
    except (OSError, subprocess.SubprocessError):
        name = path.resolve(strict=False).name
    return _component(name, "unknown")


def _data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    return Path(override) if override else Path.home() / DEFAULT_DATA_DIR_NAME


def _cached_suffix(host: str, host_session_id: str) -> str:
    cache_dir = _data_dir() / "sessions"
    cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{host}\0{host_session_id}".encode()).hexdigest()
    cache_path = cache_dir / digest
    try:
        cached = cache_path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        suffix = secrets.token_hex(3)
        temporary = cache_dir / f".{digest}.{suffix}.tmp"
        temporary.write_text(suffix, encoding="ascii")
        temporary.chmod(0o600)
        try:
            os.link(temporary, cache_path)
        except FileExistsError:
            pass
        finally:
            temporary.unlink(missing_ok=True)
        cached = cache_path.read_text(encoding="ascii").strip()
    if re.fullmatch(r"[0-9a-f]{6}", cached):
        return cached
    raise ValueError("invalid cached Session id suffix")


def session_id(event: dict[str, Any], host: str) -> str:
    """Return ``host:repository:suffix``, stable for one Host session."""

    host_name = _component(host, "unknown")
    host_session_id = str(
        event.get("session_id")
        or event.get("conversation_id")
        or event.get("transcript_path")
        or "unknown"
    )
    directory = str(
        event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    )
    try:
        suffix = _cached_suffix(host_name, host_session_id)
    except (OSError, ValueError):
        suffix = hashlib.sha256(f"{host_name}\0{host_session_id}".encode()).hexdigest()[
            :6
        ]
    return f"{host_name}:{repository_name(directory)}:{suffix}"
