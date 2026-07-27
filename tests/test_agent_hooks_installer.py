from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "agent_hooks"


def _seed_host(tmp_path: Path, config_fixture: str) -> tuple[Path, Path]:
    home = tmp_path / "home"
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True)
    shutil.copyfile(
        FIXTURES / "claude-settings-host.json",
        claude_dir / "settings.json",
    )
    shutil.copyfile(FIXTURES / config_fixture, home / ".claude.json")
    bank_dir = home / ".hermes" / "mnemosyne" / "data"
    bank_dir.mkdir(parents=True)
    (bank_dir / "mnemosyne.db").write_bytes(b"fixture Bank")
    (bank_dir / "mnemosyne.db-wal").write_bytes(b"fixture WAL")
    bank_dir.chmod(0o755)
    for path in bank_dir.iterdir():
        path.chmod(0o644)
    return home, claude_dir


def _run_installer(
    home: Path,
    claude_dir: Path,
    *arguments: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_CONFIG_DIR"] = str(claude_dir)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "integrations.agent_hooks.installer",
            *arguments,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _host_commands(tmp_path: Path) -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude_log = tmp_path / "claude.log"
    systemctl_log = tmp_path / "systemctl.log"
    claude = bin_dir / "claude"
    claude.write_text(
        "#!" + sys.executable + "\n"
        + textwrap.dedent(
            """
            import json
            import os
            import shutil
            import sys
            from pathlib import Path

            config_dir = Path(os.environ["CLAUDE_CONFIG_DIR"])
            log = Path(os.environ["FAKE_CLAUDE_LOG"])
            with log.open("a") as stream:
                print(json.dumps(sys.argv[1:]), file=stream)
            if sys.argv[1:3] in (["plugin", "install"], ["plugin", "update"]):
                source = Path(os.environ["FAKE_PLUGIN_SOURCE"])
                install_path = (
                    config_dir / "plugins" / "cache" / "mnemosyne"
                    / "mnemosyne" / "0.1.0"
                )
                if install_path.exists():
                    shutil.rmtree(install_path)
                shutil.copytree(source, install_path)
                registry = config_dir / "plugins" / "installed_plugins.json"
                registry.parent.mkdir(parents=True, exist_ok=True)
                registry.write_text(json.dumps({
                    "version": 2,
                    "plugins": {
                        "mnemosyne@mnemosyne": [{
                            "scope": "user",
                            "installPath": str(install_path),
                            "version": "0.1.0"
                        }]
                    }
                }))
            elif sys.argv[1:3] == ["plugin", "uninstall"]:
                registry = config_dir / "plugins" / "installed_plugins.json"
                if registry.exists():
                    data = json.loads(registry.read_text())
                    entries = data.get("plugins", {}).pop(
                        "mnemosyne@mnemosyne", []
                    )
                    registry.write_text(json.dumps(data))
                    for entry in entries:
                        install_path = Path(entry["installPath"])
                        if install_path.exists():
                            shutil.rmtree(install_path)
            sys.exit(0)
            """
        ).lstrip()
    )
    claude.chmod(0o755)
    systemctl = bin_dir / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$FAKE_SYSTEMCTL_LOG"\n'
        "exit 0\n"
    )
    systemctl.chmod(0o755)
    return claude, systemctl, claude_log


def test_install_dry_run_previews_every_change_without_writing(
    tmp_path: Path,
) -> None:
    home, claude_dir = _seed_host(
        tmp_path,
        "claude-config-with-mcp.json",
    )
    settings_path = claude_dir / "settings.json"
    config_path = home / ".claude.json"
    before = {
        settings_path: settings_path.read_bytes(),
        config_path: config_path.read_bytes(),
    }

    result = _run_installer(
        home,
        claude_dir,
        "install",
        "--dry-run",
        "--python",
        sys.executable,
    )

    assert result.returncode == 0, result.stderr
    assert f"--- {settings_path}" in result.stdout
    assert f"+++ {settings_path} (planned)" in result.stdout
    assert "~/.claude/hooks/mnemosyne-stop" in result.stdout
    assert f"--- {config_path}" in result.stdout
    assert '"mnemosyne"' in result.stdout
    assert "0644 -> 0600" in result.stdout
    assert "0755 -> 0700" in result.stdout
    assert "plugin install/update: mnemosyne@mnemosyne" in result.stdout
    assert "disable MCP service: mnemosyne.service" in result.stdout
    assert "install Sidecar service" in result.stdout
    assert "dry-run: no changes applied" in result.stdout
    assert {path: path.read_bytes() for path in before} == before
    assert settings_path.stat().st_mode & 0o777 == 0o644
    assert config_path.stat().st_mode & 0o777 == 0o644
    assert not (home / ".mnemosyne-hooks").exists()

    settings = json.loads(settings_path.read_text())
    assert settings["hooks"]["Stop"][0]["hooks"][0]["command"].endswith(
        "mnemosyne-stop"
    )


def test_install_applies_plugin_service_config_and_permissions(
    tmp_path: Path,
) -> None:
    home, claude_dir = _seed_host(
        tmp_path,
        "claude-config-with-mcp.json",
    )
    claude, systemctl, claude_log = _host_commands(tmp_path)
    env = {
        "FAKE_CLAUDE_LOG": str(claude_log),
        "FAKE_PLUGIN_SOURCE": str(ROOT / "integrations" / "agent_hooks"),
        "FAKE_SYSTEMCTL_LOG": str(tmp_path / "systemctl.log"),
    }

    result = _run_installer(
        home,
        claude_dir,
        "install",
        "--yes",
        "--python",
        sys.executable,
        "--claude-bin",
        str(claude),
        "--systemctl-bin",
        str(systemctl),
        extra_env=env,
    )

    assert result.returncode == 0, result.stderr
    settings = json.loads((claude_dir / "settings.json").read_text())
    assert "Stop" not in settings["hooks"]
    assert "UserPromptSubmit" not in settings["hooks"]
    assert list(settings["hooks"]) == [
        "PreToolUse",
        "SessionStart",
        "SubagentStart",
    ]
    config = json.loads((home / ".claude.json").read_text())
    assert "mnemosyne" not in config["mcpServers"]
    assert "mnemosyne" not in config["projects"]["/work/example"]["mcpServers"]
    assert config["mcpServers"]["context7"] == {"command": "context7"}
    assert config["projects"]["/work/example"]["mcpServers"]["other"] == {
        "command": "other"
    }

    registry = json.loads(
        (claude_dir / "plugins" / "installed_plugins.json").read_text()
    )
    plugin_root = Path(
        registry["plugins"]["mnemosyne@mnemosyne"][0]["installPath"]
    )
    command_files = [
        plugin_root / "hooks" / "hooks.json",
        *[
            plugin_root / "skills" / name / "SKILL.md"
            for name in ("remember", "recall", "forget")
        ],
    ]
    for path in command_files:
        text = path.read_text()
        assert "@MNEMOSYNE_PYTHON@" not in text
        assert sys.executable in text
        assert list(path.parent.glob(path.name + ".bak.*"))

    unit = (
        home
        / ".config"
        / "systemd"
        / "user"
        / "mnemosyne-agent-hooks-sidecar.service"
    )
    unit_text = unit.read_text()
    assert f"ExecStart={sys.executable} -I {plugin_root / 'run_sidecar.py'}" in (
        unit_text
    )
    assert list(unit.parent.glob(unit.name + ".bak.*.absent"))
    assert (home / ".hermes/mnemosyne/data").stat().st_mode & 0o777 == 0o700
    assert (
        home / ".hermes/mnemosyne/data/mnemosyne.db"
    ).stat().st_mode & 0o777 == 0o600
    assert (
        home / ".hermes/mnemosyne/data/mnemosyne.db-wal"
    ).stat().st_mode & 0o777 == 0o600
    assert list(claude_dir.glob("settings.json.bak.*"))
    assert list(home.glob(".claude.json.bak.*"))
    assert (home / ".mnemosyne-hooks" / "install-state.json").is_file()

    claude_calls = [json.loads(line) for line in claude_log.read_text().splitlines()]
    assert [
        "plugin",
        "marketplace",
        "add",
        str(claude_dir / "mnemosyne-marketplace-source"),
    ] in claude_calls
    assert ["plugin", "install", "mnemosyne@mnemosyne", "--scope", "user"] in (
        claude_calls
    )
    runtime_plugin_root = (
        claude_dir
        / "mnemosyne-marketplace-source"
        / "integrations"
        / "agent_hooks"
    )
    for path in [
        runtime_plugin_root / "hooks/hooks.json",
        *[
            runtime_plugin_root / "skills" / name / "SKILL.md"
            for name in ("remember", "recall", "forget")
        ],
    ]:
        assert "@MNEMOSYNE_PYTHON@" not in path.read_text()
        assert sys.executable in path.read_text()
    systemctl_calls = (tmp_path / "systemctl.log").read_text().splitlines()
    assert "--user daemon-reload" in systemctl_calls
    assert (
        "--user enable --now mnemosyne-agent-hooks-sidecar.service"
        in systemctl_calls
    )
    assert "--user disable --now mnemosyne.service" in systemctl_calls


def test_second_install_is_a_filesystem_and_host_action_noop(
    tmp_path: Path,
) -> None:
    home, claude_dir = _seed_host(
        tmp_path,
        "claude-config-without-mcp.json",
    )
    claude, systemctl, claude_log = _host_commands(tmp_path)
    env = {
        "FAKE_CLAUDE_LOG": str(claude_log),
        "FAKE_PLUGIN_SOURCE": str(ROOT / "integrations" / "agent_hooks"),
        "FAKE_SYSTEMCTL_LOG": str(tmp_path / "systemctl.log"),
    }
    arguments = (
        "install",
        "--yes",
        "--python",
        sys.executable,
        "--claude-bin",
        str(claude),
        "--systemctl-bin",
        str(systemctl),
    )
    first = _run_installer(
        home,
        claude_dir,
        *arguments,
        extra_env=env,
    )
    assert first.returncode == 0, first.stderr
    tracked = {
        path: (path.read_bytes(), path.stat().st_mode & 0o777)
        for path in home.rglob("*")
        if path.is_file()
    }
    claude_calls = claude_log.read_bytes()
    systemctl_calls = (tmp_path / "systemctl.log").read_bytes()

    second = _run_installer(
        home,
        claude_dir,
        *arguments,
        extra_env=env,
    )

    assert second.returncode == 0, second.stderr
    assert "already installed: no changes required" in second.stdout
    assert {
        path: (path.read_bytes(), path.stat().st_mode & 0o777)
        for path in home.rglob("*")
        if path.is_file()
    } == tracked
    assert claude_log.read_bytes() == claude_calls
    assert (tmp_path / "systemctl.log").read_bytes() == systemctl_calls


def test_uninstall_restores_exact_starting_state(tmp_path: Path) -> None:
    home, claude_dir = _seed_host(
        tmp_path,
        "claude-config-with-mcp.json",
    )
    claude, systemctl, claude_log = _host_commands(tmp_path)
    env = {
        "FAKE_CLAUDE_LOG": str(claude_log),
        "FAKE_PLUGIN_SOURCE": str(ROOT / "integrations" / "agent_hooks"),
        "FAKE_SYSTEMCTL_LOG": str(tmp_path / "systemctl.log"),
    }
    settings_path = claude_dir / "settings.json"
    config_path = home / ".claude.json"
    bank_dir = home / ".hermes/mnemosyne/data"
    starting = {
        settings_path: settings_path.read_bytes(),
        config_path: config_path.read_bytes(),
    }
    starting_modes = {
        path: path.stat().st_mode & 0o777
        for path in (bank_dir, *bank_dir.iterdir())
    }
    install = _run_installer(
        home,
        claude_dir,
        "install",
        "--yes",
        "--python",
        sys.executable,
        "--claude-bin",
        str(claude),
        "--systemctl-bin",
        str(systemctl),
        extra_env=env,
    )
    assert install.returncode == 0, install.stderr

    uninstall = _run_installer(
        home,
        claude_dir,
        "uninstall",
        "--yes",
        "--claude-bin",
        str(claude),
        "--systemctl-bin",
        str(systemctl),
        extra_env=env,
    )

    assert uninstall.returncode == 0, uninstall.stderr
    assert "uninstalled: starting state restored" in uninstall.stdout
    assert {path: path.read_bytes() for path in starting} == starting
    assert {
        path: path.stat().st_mode & 0o777 for path in starting_modes
    } == starting_modes
    assert not (
        home
        / ".config/systemd/user/mnemosyne-agent-hooks-sidecar.service"
    ).exists()
    assert not (claude_dir / "mnemosyne-marketplace-source").exists()
    registry = json.loads(
        (claude_dir / "plugins/installed_plugins.json").read_text()
    )
    assert "mnemosyne@mnemosyne" not in registry["plugins"]
    assert not (home / ".mnemosyne-hooks/install-state.json").exists()
    claude_calls = [json.loads(line) for line in claude_log.read_text().splitlines()]
    assert [
        "plugin",
        "uninstall",
        "mnemosyne@mnemosyne",
        "--scope",
        "user",
        "--yes",
    ] in claude_calls
    systemctl_calls = (tmp_path / "systemctl.log").read_text().splitlines()
    assert (
        "--user disable --now mnemosyne-agent-hooks-sidecar.service"
        in systemctl_calls
    )
    assert "--user enable --now mnemosyne.service" in systemctl_calls


def test_direct_plugin_update_is_detected_and_repaired(
    tmp_path: Path,
) -> None:
    home, claude_dir = _seed_host(
        tmp_path,
        "claude-config-without-mcp.json",
    )
    claude, systemctl, claude_log = _host_commands(tmp_path)
    env = {
        "FAKE_CLAUDE_LOG": str(claude_log),
        "FAKE_PLUGIN_SOURCE": str(ROOT / "integrations" / "agent_hooks"),
        "FAKE_SYSTEMCTL_LOG": str(tmp_path / "systemctl.log"),
    }
    install_arguments = (
        "install",
        "--yes",
        "--python",
        sys.executable,
        "--claude-bin",
        str(claude),
        "--systemctl-bin",
        str(systemctl),
    )
    first = _run_installer(
        home,
        claude_dir,
        *install_arguments,
        extra_env=env,
    )
    assert first.returncode == 0, first.stderr
    update = subprocess.run(
        [str(claude), "plugin", "update", "mnemosyne@mnemosyne"],
        env={**os.environ, "HOME": str(home), "CLAUDE_CONFIG_DIR": str(claude_dir), **env},
        capture_output=True,
        text=True,
        check=False,
    )
    assert update.returncode == 0, update.stderr

    verify = _run_installer(
        home,
        claude_dir,
        "verify",
        extra_env=env,
    )

    assert verify.returncode == 1
    assert "rerun the Mnemosyne installer after plugin update" in verify.stderr

    repair = _run_installer(
        home,
        claude_dir,
        *install_arguments,
        extra_env=env,
    )
    assert repair.returncode == 0, repair.stderr
    registry = json.loads(
        (claude_dir / "plugins/installed_plugins.json").read_text()
    )
    plugin_root = Path(
        registry["plugins"]["mnemosyne@mnemosyne"][0]["installPath"]
    )
    for relative in (
        "hooks/hooks.json",
        "skills/remember/SKILL.md",
        "skills/recall/SKILL.md",
        "skills/forget/SKILL.md",
    ):
        assert "@MNEMOSYNE_PYTHON@" not in (plugin_root / relative).read_text()


def test_install_rejects_relative_interpreter_before_any_write(
    tmp_path: Path,
) -> None:
    home, claude_dir = _seed_host(
        tmp_path,
        "claude-config-without-mcp.json",
    )
    before = {
        path: path.read_bytes()
        for path in home.rglob("*")
        if path.is_file()
    }

    result = _run_installer(
        home,
        claude_dir,
        "install",
        "--yes",
        "--python",
        "python3",
    )

    assert result.returncode == 1
    assert "interpreter must be absolute" in result.stderr
    assert {
        path: path.read_bytes()
        for path in home.rglob("*")
        if path.is_file()
    } == before


def test_codex_shared_patcher_appends_without_renumbering_trust_fixture() -> None:
    from integrations.agent_hooks.installer import append_hook_groups

    original = json.loads((FIXTURES / "codex-hooks-host.json").read_text())
    trust = (FIXTURES / "codex-trust-host.toml").read_text()
    original_groups = original["hooks"]["SessionStart"]

    result = append_hook_groups(
        original,
        {
            "SessionStart": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "/abs/python /abs/session_start.py",
                        }
                    ]
                }
            ],
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "/abs/python /abs/user_prompt_submit.py",
                        }
                    ]
                }
            ],
        },
    )

    assert result["hooks"]["SessionStart"][:2] == original_groups
    assert result["hooks"]["SessionStart"][2]["hooks"][0]["command"] == (
        "/abs/python /abs/session_start.py"
    )
    assert original["hooks"]["SessionStart"] == original_groups
    assert trust.count("session_start:0:0") == 1
    assert trust.count("session_start:1:0") == 1
    assert "session_start:2:0" not in trust
