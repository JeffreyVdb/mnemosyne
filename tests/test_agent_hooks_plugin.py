from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from integrations.agent_hooks.client import SidecarClient
from integrations.agent_hooks import __version__
from integrations.agent_hooks.transport import SOCKET_ENV

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def _running_sidecar(
    tmp_path: Path,
) -> Iterator[tuple[SidecarClient, Path, Path]]:
    socket_path = tmp_path / "sidecar.sock"
    data_dir = tmp_path / "bank"
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env["MNEMOSYNE_DATA_DIR"] = str(data_dir)
    env["MNEMOSYNE_NO_EMBEDDINGS"] = "1"
    env["MNEMOSYNE_AUTO_SLEEP_ENABLED"] = "0"
    env[SOCKET_ENV] = str(socket_path)
    process = subprocess.Popen(
        [sys.executable, "-m", "integrations.agent_hooks.sidecar"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    client = SidecarClient(socket_path=socket_path, timeout=3)
    try:
        deadline = time.monotonic() + 8
        health = client.health()
        while not health.ok and time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError((process.returncode, stdout, stderr))
            time.sleep(0.02)
            health = client.health()
        assert health.ok, health.error
        yield client, data_dir / "mnemosyne.db", socket_path
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)


@contextmanager
def _recording_prefetch_server(socket_path: Path) -> Iterator[list[bytes]]:
    requests: list[bytes] = []
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.listen()

    def serve() -> None:
        connection, _ = server.accept()
        with connection:
            request = connection.recv(65_536)
            requests.append(request)
            body = b'{"context":""}'
            connection.sendall(
                b"HTTP/1.1 200 OK\r\n"
                + f"Content-Length: {len(body)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
                + body
            )

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield requests
    finally:
        thread.join(timeout=3)
        server.close()


@pytest.mark.parametrize(
    ("operation", "argument"),
    (
        ("remember", "Keep this durable fact"),
        ("recall", "durable fact"),
        ("forget", "memory-id"),
    ),
)
def test_deliberate_client_failure_is_returned(
    tmp_path: Path,
    operation: str,
    argument: str,
) -> None:
    client = SidecarClient(socket_path=tmp_path / "missing.sock")

    result = getattr(client, operation)(argument, "claude-code:project:abc123")

    assert result.ok is False
    assert result.status is None
    assert result.data is None
    assert result.error


def test_deliberate_routes_store_recall_and_forget_global_memory(
    tmp_path: Path,
) -> None:
    session_id = "claude-code:project:abc123"
    secret = "sk-ant-abcdefghijklmnopqrstuvwxyz123456"
    with _running_sidecar(tmp_path) as (client, db_path, _socket_path):
        first = client.remember("Neighbour alpha fact", session_id)
        target = client.remember(f"Target credential is {secret}", session_id)
        third = client.remember("Neighbour omega fact", session_id)

        assert first.ok and first.data is not None
        assert target.ok and target.data is not None
        assert third.ok and third.data is not None
        target_id = target.data["memory_id"]

        recalled = client.recall("Target credential", session_id)
        assert recalled.ok and recalled.data is not None
        assert recalled.data["count"] >= 1
        target_row = next(
            row for row in recalled.data["results"] if row["id"] == target_id
        )
        assert target_row["content"] == (
            "Target credential is [REDACTED:ANTHROPIC_API_KEY]"
        )

        forgotten = client.forget(str(target_id), session_id)
        assert forgotten.ok
        assert forgotten.data == {
            "status": "deleted",
            "memory_id": target_id,
        }

        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(
                "SELECT id, content, scope FROM working_memory ORDER BY rowid"
            ).fetchall()

    assert rows == [
        (first.data["memory_id"], "Neighbour alpha fact", "global"),
        (third.data["memory_id"], "Neighbour omega fact", "global"),
    ]
    assert all(secret not in content for _memory_id, content, _scope in rows)


def test_deliberate_client_rejects_provider_error_payload(tmp_path: Path) -> None:
    with _running_sidecar(tmp_path) as (client, _db_path, _socket_path):
        result = client.forget("   ", "claude-code:project:abc123")

    assert result.ok is False
    assert result.status == 200
    assert result.data is None
    assert result.error == "memory_id is required"


def test_committed_remember_timeout_is_reported_as_unknown(
    tmp_path: Path,
) -> None:
    runner = ROOT / "integrations" / "agent_hooks" / "deliberate.py"
    prefix = "FALSENEGATIVE"
    content = prefix + ("x" * (16_021 - len(prefix)))
    env = os.environ.copy()
    env["MNEMOSYNE_HOOKS_DATA_DIR"] = str(tmp_path / "hook-data")

    with _running_sidecar(tmp_path) as (_client, db_path, socket_path):
        env[SOCKET_ENV] = str(socket_path)
        warmup = subprocess.run(
            [
                sys.executable,
                str(runner),
                "--host",
                "claude-code",
                "remember",
                "Warm the deliberate Provider",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert warmup.returncode == 0, warmup.stderr
        remember = subprocess.run(
            [
                sys.executable,
                str(runner),
                "--host",
                "claude-code",
                "remember",
                content,
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

        deadline = time.monotonic() + 20
        committed = False
        while time.monotonic() < deadline:
            with sqlite3.connect(db_path) as connection:
                committed = (
                    connection.execute(
                        "SELECT content FROM working_memory WHERE content = ?",
                        (content,),
                    ).fetchone()
                    is not None
                )
            if committed:
                break
            time.sleep(0.1)

    assert committed, "the timed-out remember did not commit within 20 seconds"
    assert remember.returncode == 1
    assert remember.stdout == ""
    assert remember.stderr == (
        "Mnemosyne remember outcome unknown: request timed out; "
        "check with recall before retrying\n"
    )


def test_deliberate_skill_runner_works_without_mcp_server(tmp_path: Path) -> None:
    runner = ROOT / "integrations" / "agent_hooks" / "deliberate.py"
    env = os.environ.copy()
    env["MNEMOSYNE_HOOKS_DATA_DIR"] = str(tmp_path / "hook-data")

    with _running_sidecar(tmp_path) as (_client, _db_path, socket_path):
        env[SOCKET_ENV] = str(socket_path)

        remember = subprocess.run(
            [
                sys.executable,
                str(runner),
                "--host",
                "claude-code",
                "remember",
                "Skill runner durable fact",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert remember.returncode == 0, remember.stderr
        remembered = json.loads(remember.stdout)
        memory_id = remembered["memory_id"]

        recall = subprocess.run(
            [
                sys.executable,
                str(runner),
                "--host",
                "claude-code",
                "recall",
                "Skill runner durable",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert recall.returncode == 0, recall.stderr
        recalled = json.loads(recall.stdout)
        assert any(row["id"] == memory_id for row in recalled["results"])

        forget = subprocess.run(
            [
                sys.executable,
                str(runner),
                "--host",
                "claude-code",
                "forget",
                memory_id,
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert forget.returncode == 0, forget.stderr
        assert json.loads(forget.stdout) == {
            "status": "deleted",
            "memory_id": memory_id,
        }


def test_claude_plugin_manifests_register_exact_runtime_surface() -> None:
    marketplace = json.loads(
        (ROOT / ".claude-plugin" / "marketplace.json").read_text()
    )
    plugin_root = ROOT / marketplace["plugins"][0]["source"]
    plugin = json.loads(
        (plugin_root / ".claude-plugin" / "plugin.json").read_text()
    )
    hooks = json.loads((plugin_root / "hooks" / "hooks.json").read_text())

    assert marketplace["name"] == "mnemosyne"
    assert marketplace["plugins"] == [
        {
            "name": "mnemosyne",
            "description": "Automatic and deliberate memory for coding agents",
            "source": "./integrations/agent_hooks",
        }
    ]
    assert plugin["version"] == __version__ == "0.1"
    assert plugin["skills"] == ["./skills/"]
    assert set(hooks["hooks"]) == {"UserPromptSubmit", "Stop"}

    expected_scripts = {
        "UserPromptSubmit": "user_prompt_submit.py",
        "Stop": "turn_end.py",
    }
    for event_name, script_name in expected_scripts.items():
        command = hooks["hooks"][event_name][0]["hooks"][0]["command"]
        assert command == (
            'MNEMOSYNE_HOOKS_DATA_DIR="${HOME}/.mnemosyne-hooks" '
            f'@MNEMOSYNE_PYTHON@ "${{CLAUDE_PLUGIN_ROOT}}/{script_name}" '
            "--host claude-code"
        )
        assert hooks["hooks"][event_name][0]["hooks"][0]["type"] == "command"
        assert (plugin_root / script_name).is_file()

    for skill_name in ("remember", "recall", "forget"):
        skill = plugin_root / "skills" / skill_name / "SKILL.md"
        assert skill.is_file()
        skill_text = skill.read_text()
        normalized_skill_text = " ".join(skill_text.split())
        assert "@MNEMOSYNE_PYTHON@" in skill_text
        assert "outcome is unknown" in normalized_skill_text
        assert "recall" in normalized_skill_text


@pytest.mark.parametrize(
    ("mode", "flags", "safe_path"),
    (
        ("plain", (), False),
        pytest.param(
            "-P",
            ("-P",),
            False,
            marks=pytest.mark.skipif(
                sys.version_info < (3, 11),
                reason="-P is available on Python 3.11+",
            ),
        ),
        ("-I", ("-I",), False),
        pytest.param(
            "PYTHONSAFEPATH=1",
            (),
            True,
            marks=pytest.mark.skipif(
                sys.version_info < (3, 11),
                reason="PYTHONSAFEPATH is enforced on Python 3.11+",
            ),
        ),
    ),
)
def test_flat_installed_hook_uses_sibling_imports_in_all_modes(
    tmp_path: Path,
    mode: str,
    flags: tuple[str, ...],
    safe_path: bool,
) -> None:
    del mode
    plugin_root = tmp_path / "mnemosyne"
    shutil.copytree(
        ROOT / "integrations" / "agent_hooks",
        plugin_root,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    import_trap = plugin_root / "integrations"
    import_trap.mkdir()
    (import_trap / "__init__.py").write_text(
        "raise RuntimeError('editable install import branch used')\n"
    )
    socket_path = tmp_path / "hook.sock"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["MNEMOSYNE_HOOKS_DATA_DIR"] = str(tmp_path / "hook-data")
    env[SOCKET_ENV] = str(socket_path)
    if safe_path:
        env["PYTHONSAFEPATH"] = "1"
    else:
        env.pop("PYTHONSAFEPATH", None)

    # The worktree venv has an editable finder that can resolve the mutation this
    # test guards against. Its base interpreter has no editable install, so every
    # mode must load solely from the flat plugin copy.
    base_candidates = (
        Path(getattr(sys, "_base_executable", "")),
        Path(sys.base_prefix)
        / "bin"
        / f"python{sys.version_info.major}.{sys.version_info.minor}",
        Path(sys.base_prefix) / "bin" / "python3",
    )
    base_executable = None
    for candidate in dict.fromkeys(base_candidates):
        if not candidate.is_file():
            continue
        editable_probe = subprocess.run(
            [
                str(candidate),
                "-c",
                (
                    "import sys; "
                    "print(any(getattr(f, '__module__', '').startswith("
                    "'__editable__') for f in sys.meta_path))"
                ),
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if (
            editable_probe.returncode == 0
            and editable_probe.stdout == "False\n"
        ):
            base_executable = candidate
            break
    if base_executable is None:
        pytest.skip(
            "no base interpreter without the worktree editable install: "
            f"{base_candidates}"
        )

    with _recording_prefetch_server(socket_path) as requests:
        result = subprocess.run(
            [
                str(base_executable),
                *flags,
                str(plugin_root / "user_prompt_submit.py"),
                "--host",
                "claude-code",
            ],
            cwd=tmp_path,
            env=env,
            input=json.dumps(
                {
                    "user_prompt": "Exercise the flat plugin Hook",
                    "session_id": "flat-install",
                    "cwd": str(tmp_path),
                }
            ),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert len(requests) == 1
    assert requests[0].startswith(b"POST /prefetch HTTP/1.1\r\n")
