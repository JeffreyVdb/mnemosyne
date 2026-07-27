"""Install, verify, and uninstall the Claude Code Agent Hooks integration."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PLUGIN_ID = "mnemosyne@mnemosyne"
ROOT = Path(__file__).resolve().parents[2]
PYTHON_TOKEN = "@MNEMOSYNE_PYTHON@"
LAUNCHER_TOKEN = "@MNEMOSYNE_SIDECAR_LAUNCHER@"
COMMAND_FILES = (
    Path("hooks/hooks.json"),
    Path("skills/remember/SKILL.md"),
    Path("skills/recall/SKILL.md"),
    Path("skills/forget/SKILL.md"),
)
_COMMAND_INTERPRETER = re.compile(
    r'(MNEMOSYNE_HOOKS_DATA_DIR=(?:"[^"]*"|\'[^\']*\'|\S+)\s+)(\S+)'
)
LEGACY_HOOK_COMMANDS = {
    "~/.claude/hooks/mnemosyne-stop",
    "~/.claude/hooks/mnemosyne-user-prompt",
}


@dataclass(frozen=True)
class JsonChange:
    """One planned JSON config-file replacement."""

    path: Path
    before: bytes
    after: bytes


def _load_json(path: Path) -> tuple[dict[str, Any], bytes]:
    if not path.exists():
        return {}, b""
    before = path.read_bytes()
    try:
        value = json.loads(before)
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value, before


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _without_legacy_hooks(settings: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(settings))
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        return result
    for event in ("Stop", "UserPromptSubmit"):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept = []
        for group in groups:
            if not isinstance(group, dict):
                kept.append(group)
                continue
            commands = {
                hook.get("command")
                for hook in group.get("hooks", [])
                if isinstance(hook, dict)
            }
            if commands.isdisjoint(LEGACY_HOOK_COMMANDS):
                kept.append(group)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    return result


def _without_mnemosyne_mcp(config: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(config))
    locations: list[dict[str, Any]] = [result]
    projects = result.get("projects")
    if isinstance(projects, dict):
        locations.extend(
            project
            for project in projects.values()
            if isinstance(project, dict)
        )
    for location in locations:
        servers = location.get("mcpServers")
        if isinstance(servers, dict):
            servers.pop("mnemosyne", None)
    return result


def append_hook_groups(
    config: dict[str, Any],
    additions: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Return Host Hook config with every new group appended by event."""

    result = json.loads(json.dumps(config))
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("Host Hook config .hooks must be an object")
    for event, groups in additions.items():
        existing = hooks.setdefault(event, [])
        if not isinstance(existing, list):
            raise ValueError(f"Host Hook config .hooks.{event} must be an array")
        existing.extend(json.loads(json.dumps(groups)))
    return result


def _json_change(path: Path, transform: Any) -> JsonChange | None:
    value, before = _load_json(path)
    after = _json_bytes(transform(value))
    comparable_before = _json_bytes(value)
    if after == comparable_before:
        return None
    return JsonChange(path, before, after)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def _backup(path: Path, timestamp: str) -> Path:
    suffix = f".bak.{timestamp}"
    if path.exists():
        backup = path.with_name(path.name + suffix)
        shutil.copy2(path, backup)
    else:
        backup = path.with_name(path.name + suffix + ".absent")
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.touch(mode=0o600)
    return backup


def _atomic_write(path: Path, content: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is None:
            mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _run_checked(argv: list[str], *, action: str) -> None:
    process = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        message = process.stderr.strip() or process.stdout.strip()
        raise RuntimeError(f"{action} failed ({process.returncode}): {message}")


def _command_succeeds(argv: list[str]) -> bool:
    return subprocess.run(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def _active_plugin_path(claude_dir: Path) -> Path | None:
    registry_path = claude_dir / "plugins" / "installed_plugins.json"
    if not registry_path.is_file():
        return None
    registry, _before = _load_json(registry_path)
    plugins = registry.get("plugins")
    entries = plugins.get(PLUGIN_ID) if isinstance(plugins, dict) else None
    if not isinstance(entries, list):
        return None
    user_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("scope") == "user"
    ]
    if not user_entries:
        return None
    install_path = user_entries[-1].get("installPath")
    if not isinstance(install_path, str):
        raise ValueError(f"{registry_path} has no valid installPath for {PLUGIN_ID}")
    path = Path(install_path)
    if not path.is_absolute():
        raise ValueError(f"installed plugin path must be absolute: {path}")
    return path


def _prepare_managed_marketplace(
    claude_dir: Path,
    timestamp: str,
) -> tuple[Path, Path]:
    destination = claude_dir / "mnemosyne-marketplace-source"
    if destination.exists():
        backup = destination.with_name(destination.name + f".bak.{timestamp}")
        os.replace(destination, backup)
    else:
        backup = destination.with_name(
            destination.name + f".bak.{timestamp}.absent"
        )
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.touch(mode=0o600)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=claude_dir)
    )
    try:
        shutil.copytree(
            ROOT / ".claude-plugin",
            staging / ".claude-plugin",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        shutil.copytree(
            ROOT / "integrations" / "agent_hooks",
            staging / "integrations" / "agent_hooks",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        os.replace(staging, destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return destination, backup


def _substitute_plugin_root(
    plugin_root: Path,
    timestamp: str,
    interpreter: Path,
) -> list[Path]:
    backups = []
    for relative in COMMAND_FILES:
        path = plugin_root / relative
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"installed plugin command file missing: {path}") from exc
        matches = _COMMAND_INTERPRETER.findall(source)
        if not matches:
            raise RuntimeError(f"installed plugin command is malformed: {path}")
        current_values = {match[1] for match in matches}
        if PYTHON_TOKEN not in current_values:
            if any(not Path(value).is_absolute() for value in current_values):
                raise RuntimeError(
                    f"installed plugin has a non-absolute interpreter: {path}"
                )
            if current_values == {str(interpreter)}:
                continue
        backup = _backup(path, timestamp)
        replaced = _COMMAND_INTERPRETER.sub(
            lambda match: match.group(1) + str(interpreter),
            source,
        )
        _atomic_write(
            path,
            replaced.encode("utf-8"),
            mode=path.stat().st_mode & 0o777,
        )
        backups.append(backup)
        if str(interpreter) not in replaced:
            raise RuntimeError(f"interpreter substitution failed: {path}")
    return backups


def _install_plugin(
    claude_bin: str,
    claude_dir: Path,
    marketplace_root: Path,
    timestamp: str,
    interpreter: Path,
) -> tuple[Path, list[Path], bool]:
    before_path = _active_plugin_path(claude_dir)
    if before_path is None:
        _run_checked(
            [
                claude_bin,
                "plugin",
                "marketplace",
                "add",
                str(marketplace_root),
            ],
            action="adding the Mnemosyne marketplace",
        )
        command = "install"
    else:
        command = "update"
    _run_checked(
        [
            claude_bin,
            "plugin",
            command,
            PLUGIN_ID,
            "--scope",
            "user",
        ],
        action=f"Claude plugin {command}",
    )
    plugin_root = _active_plugin_path(claude_dir)
    if plugin_root is None:
        raise RuntimeError("Claude installed no active user-scoped Mnemosyne plugin")

    backups = _substitute_plugin_root(plugin_root, timestamp, interpreter)

    remaining = [
        str(plugin_root / relative)
        for relative in COMMAND_FILES
        if PYTHON_TOKEN
        in (plugin_root / relative).read_text(encoding="utf-8")
    ]
    if remaining:
        raise RuntimeError(
            "installed plugin retains @MNEMOSYNE_PYTHON@ in: "
            + ", ".join(remaining)
        )
    return plugin_root, backups, before_path is not None


def _render_service(
    home: Path,
    plugin_root: Path,
    interpreter: Path,
    timestamp: str,
) -> tuple[Path, Path]:
    destination, rendered = _service_definition(home, plugin_root, interpreter)
    backup = _backup(destination, timestamp)
    _atomic_write(destination, rendered, mode=0o644)
    return destination, backup


def _service_definition(
    home: Path,
    plugin_root: Path,
    interpreter: Path,
) -> tuple[Path, bytes]:
    template = (
        ROOT
        / "integrations"
        / "agent_hooks"
        / "services"
        / "mnemosyne-agent-hooks-sidecar.service.in"
    )
    launcher = plugin_root / "run_sidecar.py"
    if not launcher.is_absolute():
        raise ValueError(f"Sidecar launcher must be absolute: {launcher}")
    rendered = (
        template.read_text(encoding="utf-8")
        .replace(PYTHON_TOKEN, str(interpreter))
        .replace(LAUNCHER_TOKEN, str(launcher))
    )
    if PYTHON_TOKEN in rendered or LAUNCHER_TOKEN in rendered:
        raise RuntimeError("rendered Sidecar service retains a substitution token")
    destination = (
        home
        / ".config"
        / "systemd"
        / "user"
        / "mnemosyne-agent-hooks-sidecar.service"
    )
    return destination, rendered.encode("utf-8")


def _install_is_current(
    *,
    state_path: Path,
    claude_dir: Path,
    home: Path,
    interpreter: Path,
    changes: list[JsonChange],
    mode_changes: list[tuple[Path, int, int]],
) -> bool:
    if not state_path.is_file() or changes or mode_changes:
        return False
    plugin_root = _active_plugin_path(claude_dir)
    if plugin_root is None:
        return False
    for relative in COMMAND_FILES:
        path = plugin_root / relative
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return False
        if PYTHON_TOKEN in content or str(interpreter) not in content:
            return False
    runtime_plugin_root = (
        claude_dir
        / "mnemosyne-marketplace-source"
        / "integrations"
        / "agent_hooks"
    )
    for relative in COMMAND_FILES:
        try:
            content = (runtime_plugin_root / relative).read_text(encoding="utf-8")
        except OSError:
            return False
        if PYTHON_TOKEN in content or str(interpreter) not in content:
            return False
    destination, rendered = _service_definition(home, plugin_root, interpreter)
    try:
        return destination.read_bytes() == rendered
    except OSError:
        return False


def _display_json_change(change: JsonChange) -> None:
    before = (
        _json_bytes(json.loads(change.before)).decode("utf-8").splitlines(True)
        if change.before
        else []
    )
    after = change.after.decode("utf-8").splitlines(True)
    sys.stdout.writelines(
        difflib.unified_diff(
            before,
            after,
            fromfile=str(change.path),
            tofile=f"{change.path} (planned)",
        )
    )


def _bank_mode_changes(bank_dir: Path) -> list[tuple[Path, int, int]]:
    if not bank_dir.exists():
        return []
    paths = [bank_dir, *sorted(bank_dir.rglob("*"))]
    changes = []
    for path in paths:
        before = path.stat().st_mode & 0o777
        after = 0o700 if path.is_dir() else 0o600
        if before != after:
            changes.append((path, before, after))
    return changes


def _absolute_interpreter(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"Mnemosyne interpreter must be absolute: {value}")
    if not path.is_file():
        raise ValueError(f"Mnemosyne interpreter does not exist: {value}")
    return path


def _install(args: argparse.Namespace) -> int:
    home = Path.home()
    claude_dir = Path(
        os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude")
    )
    settings_path = claude_dir / "settings.json"
    default_claude_dir = home / ".claude"
    config_path = (
        claude_dir / ".claude.json"
        if claude_dir.resolve(strict=False)
        != default_claude_dir.resolve(strict=False)
        else home / ".claude.json"
    )
    bank_dir = home / ".hermes" / "mnemosyne" / "data"
    interpreter = _absolute_interpreter(args.python)

    changes: list[JsonChange] = [
        change
        for change in (
            _json_change(settings_path, _without_legacy_hooks),
            _json_change(config_path, _without_mnemosyne_mcp),
        )
        if change is not None
    ]
    mode_changes = _bank_mode_changes(bank_dir)
    state_dir = home / ".mnemosyne-hooks"
    state_path = state_dir / "install-state.json"
    if not args.dry_run and _install_is_current(
        state_path=state_path,
        claude_dir=claude_dir,
        home=home,
        interpreter=interpreter,
        changes=changes,
        mode_changes=mode_changes,
    ):
        print("already installed: no changes required")
        return 0

    for change in changes:
        _display_json_change(change)
    for path, before, after in mode_changes:
        print(f"mode {path}: {before:04o} -> {after:04o}")
    print(f"plugin install/update: {PLUGIN_ID}")
    print("install Sidecar service")
    print("disable MCP service: mnemosyne.service")
    if args.dry_run:
        print("dry-run: no changes applied")
        return 0
    if not args.yes and not sys.stdin.isatty():
        raise ValueError("non-interactive install requires --yes or --dry-run")
    if not args.yes:
        answer = input("Apply these changes? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("install aborted")
            return 0

    timestamp = _timestamp()
    mcp_was_enabled = _command_succeeds(
        [
            args.systemctl_bin,
            "--user",
            "is-enabled",
            "mnemosyne.service",
        ]
    )
    mcp_was_active = _command_succeeds(
        [
            args.systemctl_bin,
            "--user",
            "is-active",
            "mnemosyne.service",
        ]
    )
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_dir.chmod(0o700)
    repairing = state_path.exists()

    marketplace_root, marketplace_backup = _prepare_managed_marketplace(
        claude_dir,
        timestamp,
    )
    plugin_root, plugin_backups, plugin_was_installed = _install_plugin(
        args.claude_bin,
        claude_dir,
        marketplace_root,
        timestamp,
        interpreter,
    )
    runtime_plugin_root = marketplace_root / "integrations" / "agent_hooks"
    runtime_backups = _substitute_plugin_root(
        runtime_plugin_root,
        timestamp,
        interpreter,
    )
    unit_path, unit_backup = _render_service(
        home,
        plugin_root,
        interpreter,
        timestamp,
    )

    config_backups: dict[str, str] = {}
    for change in changes:
        backup = _backup(change.path, timestamp)
        config_backups[str(change.path)] = str(backup)
        mode = change.path.stat().st_mode & 0o777 if change.path.exists() else 0o600
        _atomic_write(change.path, change.after, mode=mode)

    for path, _before, after in mode_changes:
        path.chmod(after)

    _run_checked(
        [args.systemctl_bin, "--user", "daemon-reload"],
        action="systemd user daemon reload",
    )
    _run_checked(
        [
            args.systemctl_bin,
            "--user",
            "enable",
            "--now",
            unit_path.name,
        ],
        action="enabling the Sidecar",
    )
    _run_checked(
        [
            args.systemctl_bin,
            "--user",
            "disable",
            "--now",
            "mnemosyne.service",
        ],
        action="disabling the MCP service",
    )

    if not repairing:
        state_backup = _backup(state_path, timestamp)
        state = {
            "version": 1,
            "timestamp": timestamp,
            "config_backups": config_backups,
            "mode_changes": [
                {"path": str(path), "before": before, "after": after}
                for path, before, after in mode_changes
            ],
            "plugin_was_installed": plugin_was_installed,
            "plugin_root": str(plugin_root),
            "plugin_backups": [str(path) for path in plugin_backups],
            "runtime_plugin_backups": [str(path) for path in runtime_backups],
            "marketplace_root": str(marketplace_root),
            "marketplace_backup": str(marketplace_backup),
            "unit_path": str(unit_path),
            "unit_backup": str(unit_backup),
            "state_backup": str(state_backup),
            "mcp_was_enabled": mcp_was_enabled,
            "mcp_was_active": mcp_was_active,
        }
        _atomic_write(state_path, _json_bytes(state), mode=0o600)
    print(
        "repaired: plugin update substitutions restored"
        if repairing
        else "installed: Claude Code Agent Hooks are configured"
    )
    print(
        'logout survival: verify `loginctl show-user "$USER" -p Linger`; '
        'if it reports no, run `loginctl enable-linger "$USER"`'
    )
    return 0


def _installed_commands(plugin_root: Path) -> list[str]:
    hooks_path = plugin_root / "hooks" / "hooks.json"
    hooks, _before = _load_json(hooks_path)
    commands = []
    hook_events = hooks.get("hooks")
    if isinstance(hook_events, dict):
        for groups in hook_events.values():
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, dict):
                    continue
                for hook in group.get("hooks", []):
                    command = hook.get("command") if isinstance(hook, dict) else None
                    if isinstance(command, str):
                        commands.append(command)
    for relative in COMMAND_FILES[1:]:
        text = (plugin_root / relative).read_text(encoding="utf-8")
        commands.extend(
            line.strip()
            for line in text.splitlines()
            if "MNEMOSYNE_HOOKS_DATA_DIR=" in line
        )
    return commands


def _verify(_args: argparse.Namespace) -> int:
    home = Path.home()
    claude_dir = Path(
        os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude")
    )
    failures = []
    plugin_root = _active_plugin_path(claude_dir)
    if plugin_root is None:
        failures.append(f"{PLUGIN_ID} is not installed at user scope")
    else:
        command_roots = (
            plugin_root,
            claude_dir
            / "mnemosyne-marketplace-source"
            / "integrations"
            / "agent_hooks",
        )
        for command_root in command_roots:
            for relative in COMMAND_FILES:
                path = command_root / relative
                try:
                    content = path.read_text(encoding="utf-8")
                except OSError:
                    failures.append(f"installed command file is missing: {path}")
                    continue
                if PYTHON_TOKEN in content:
                    failures.append(
                        "installed plugin contains @MNEMOSYNE_PYTHON@; "
                        "rerun the Mnemosyne installer after plugin update"
                    )
        if not failures:
            for command in _installed_commands(plugin_root):
                words = shlex.split(command)
                executables = [
                    word for word in words if "=" not in word.split("/", 1)[0]
                ]
                if not executables or not Path(executables[0]).is_absolute():
                    failures.append(
                        f"installed command has a non-absolute interpreter: {command}"
                    )
    state_path = home / ".mnemosyne-hooks" / "install-state.json"
    if not state_path.is_file():
        failures.append(f"install state is missing: {state_path}")
    if plugin_root is not None:
        unit_path = (
            home
            / ".config"
            / "systemd"
            / "user"
            / "mnemosyne-agent-hooks-sidecar.service"
        )
        if not unit_path.is_file():
            failures.append(f"Sidecar service is missing: {unit_path}")
        elif PYTHON_TOKEN.encode() in unit_path.read_bytes() or (
            LAUNCHER_TOKEN.encode() in unit_path.read_bytes()
        ):
            failures.append(f"Sidecar service retains a token: {unit_path}")
    if failures:
        for failure in failures:
            print(f"verify failed: {failure}", file=sys.stderr)
        return 1
    print("verified: Claude Code Agent Hooks installation is healthy")
    return 0


def _restore_backup(path: Path, backup: Path) -> None:
    if backup.name.endswith(".absent"):
        path.unlink(missing_ok=True)
        return
    if not backup.is_file():
        raise RuntimeError(f"required backup is missing: {backup}")
    mode = backup.stat().st_mode & 0o777
    _atomic_write(path, backup.read_bytes(), mode=mode)


def _display_restore(path: Path, backup: Path) -> None:
    before = path.read_bytes() if path.is_file() else b""
    after = b"" if backup.name.endswith(".absent") else backup.read_bytes()
    try:
        before_lines = before.decode("utf-8").splitlines(True)
        after_lines = after.decode("utf-8").splitlines(True)
    except UnicodeError:
        print(f"restore {path} from {backup}")
        return
    sys.stdout.writelines(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=str(path),
            tofile=f"{path} (restored)",
        )
    )


def _uninstall(args: argparse.Namespace) -> int:
    home = Path.home()
    state_path = home / ".mnemosyne-hooks" / "install-state.json"
    if not state_path.is_file():
        print("already uninstalled: no install state found")
        return 0
    state, _before = _load_json(state_path)
    config_backups = state.get("config_backups")
    if not isinstance(config_backups, dict):
        raise ValueError(f"{state_path} has invalid config_backups")
    restores = [
        (Path(path), Path(backup))
        for path, backup in config_backups.items()
        if isinstance(path, str) and isinstance(backup, str)
    ]
    unit_path = Path(str(state["unit_path"]))
    unit_backup = Path(str(state["unit_backup"]))
    marketplace_root = Path(str(state.get("marketplace_root", "")))
    marketplace_backup = Path(str(state.get("marketplace_backup", "")))
    for path, backup in restores:
        _display_restore(path, backup)
    _display_restore(unit_path, unit_backup)
    for item in state.get("mode_changes", []):
        if isinstance(item, dict):
            print(
                f"mode {item['path']}: "
                f"{int(item['after']):04o} -> {int(item['before']):04o}"
            )
    print(f"plugin uninstall/restore: {PLUGIN_ID}")
    print("restore MCP service state: mnemosyne.service")
    if args.dry_run:
        print("dry-run: no changes applied")
        return 0
    if not args.yes and not sys.stdin.isatty():
        raise ValueError("non-interactive uninstall requires --yes or --dry-run")
    if not args.yes:
        answer = input("Apply these changes? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("uninstall aborted")
            return 0

    timestamp = _timestamp()
    _run_checked(
        [
            args.systemctl_bin,
            "--user",
            "disable",
            "--now",
            unit_path.name,
        ],
        action="disabling the Sidecar",
    )
    _backup(unit_path, timestamp)
    _restore_backup(unit_path, unit_backup)
    for path, backup in restores:
        _backup(path, timestamp)
        _restore_backup(path, backup)
    for item in state.get("mode_changes", []):
        if not isinstance(item, dict):
            continue
        path = Path(str(item["path"]))
        if path.exists():
            path.chmod(int(item["before"]))

    if not state.get("plugin_was_installed", False):
        _run_checked(
            [
                args.claude_bin,
                "plugin",
                "uninstall",
                PLUGIN_ID,
                "--scope",
                "user",
                "--yes",
            ],
            action="uninstalling the Claude plugin",
        )
    if marketplace_root.is_dir():
        shutil.rmtree(marketplace_root)
    if marketplace_backup.is_dir():
        os.replace(marketplace_backup, marketplace_root)
    _run_checked(
        [args.systemctl_bin, "--user", "daemon-reload"],
        action="systemd user daemon reload",
    )
    if state.get("mcp_was_enabled") and state.get("mcp_was_active"):
        _run_checked(
            [
                args.systemctl_bin,
                "--user",
                "enable",
                "--now",
                "mnemosyne.service",
            ],
            action="restoring the MCP service",
        )
    elif state.get("mcp_was_enabled"):
        _run_checked(
            [
                args.systemctl_bin,
                "--user",
                "enable",
                "mnemosyne.service",
            ],
            action="restoring MCP service enablement",
        )
    elif state.get("mcp_was_active"):
        _run_checked(
            [
                args.systemctl_bin,
                "--user",
                "start",
                "mnemosyne.service",
            ],
            action="restoring the active MCP service",
        )
    _backup(state_path, timestamp)
    state_path.unlink()
    print("uninstalled: starting state restored")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install or remove Mnemosyne Agent Hooks for Claude Code."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install")
    install.add_argument("--dry-run", action="store_true")
    install.add_argument("--yes", action="store_true")
    install.add_argument(
        "--python",
        default=sys.executable,
        help="absolute interpreter that imports the installed Mnemosyne package",
    )
    install.add_argument("--claude-bin", default="claude")
    install.add_argument("--systemctl-bin", default="systemctl")
    uninstall = commands.add_parser("uninstall")
    uninstall.add_argument("--dry-run", action="store_true")
    uninstall.add_argument("--yes", action="store_true")
    uninstall.add_argument("--claude-bin", default="claude")
    uninstall.add_argument("--systemctl-bin", default="systemctl")
    commands.add_parser("verify")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "install":
            return _install(args)
        if args.command == "uninstall":
            return _uninstall(args)
        if args.command == "verify":
            return _verify(args)
        raise NotImplementedError(f"{args.command} is not implemented")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"installer failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
