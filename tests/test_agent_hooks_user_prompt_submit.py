from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from integrations.agent_hooks.transport import DATA_DIR_ENV, SOCKET_ENV


ROOT = Path(__file__).resolve().parents[1]
HOOK_MODULE = "integrations.agent_hooks.user_prompt_submit"
@contextmanager
def _recording_sidecar(
    socket_path: Path,
    *,
    status: int = 200,
    response: dict[str, object] | None = None,
    hang: bool = False,
) -> Iterator[list[dict[str, object]]]:
    requests: list[dict[str, object]] = []
    ready = threading.Event()

    def serve() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(socket_path))
            server.listen()
            ready.set()
            connection, _ = server.accept()
            with connection:
                received = b""
                while b"\r\n\r\n" not in received:
                    chunk = connection.recv(65536)
                    if not chunk:
                        return
                    received += chunk
                headers, body = received.split(b"\r\n\r\n", 1)
                request_line, *header_lines = headers.decode("ascii").split("\r\n")
                content_length = 0
                for line in header_lines:
                    name, value = line.split(":", 1)
                    if name.lower() == "content-length":
                        content_length = int(value.strip())
                while len(body) < content_length:
                    body += connection.recv(65536)
                method, path, _version = request_line.split(" ", 2)
                requests.append(
                    {
                        "method": method,
                        "path": path,
                        "json": json.loads(body[:content_length]),
                    }
                )
                if hang:
                    threading.Event().wait(2)
                    return
                response_body = json.dumps(response or {}).encode()
                reason = "OK" if status == 200 else "Internal Server Error"
                connection.sendall(
                    f"HTTP/1.1 {status} {reason}\r\n".encode()
                    + b"Content-Type: application/json\r\n"
                    + f"Content-Length: {len(response_body)}\r\n".encode()
                    + b"Connection: close\r\n\r\n"
                    + response_body
                )

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=1)
    try:
        yield requests
    finally:
        thread.join(timeout=3)


def _run_hook(
    tmp_path: Path,
    event: dict[str, object] | str,
    *,
    host: str = "claude-code",
    socket_path: Path | None = None,
    cwd: Path = ROOT,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env[DATA_DIR_ENV] = str(tmp_path / "hook-data")
    env["PYTHONPATH"] = str(ROOT)
    env[SOCKET_ENV] = str(socket_path or tmp_path / "missing.sock")
    payload = event if isinstance(event, str) else json.dumps(event)
    return subprocess.run(
        [sys.executable, "-m", HOOK_MODULE, "--host", host],
        input=payload,
        text=True,
        capture_output=True,
        cwd=cwd,
        env=env,
        check=False,
        timeout=3,
    )


@pytest.mark.parametrize(
    ("host", "event", "expected_prompt"),
    [
        (
            "claude-code",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "claude-session",
                "cwd": str(ROOT),
                "user_prompt": "Claude primary prompt",
                "prompt": "Claude fallback prompt",
            },
            "Claude primary prompt",
        ),
        (
            "claude-code",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "claude-fallback-session",
                "cwd": str(ROOT),
                "prompt": "Claude fallback prompt",
            },
            "Claude fallback prompt",
        ),
        (
            "codex",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "codex-session",
                "cwd": str(ROOT),
                "prompt": "Codex primary prompt",
                "user_prompt": "Codex fallback prompt",
            },
            "Codex primary prompt",
        ),
        (
            "codex",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "codex-fallback-session",
                "cwd": str(ROOT),
                "user_prompt": "Codex fallback prompt",
            },
            "Codex fallback prompt",
        ),
    ],
)
def test_hook_injects_recalled_memory_for_both_host_payloads(
    tmp_path: Path,
    host: str,
    event: dict[str, object],
    expected_prompt: str,
) -> None:
    socket_path = tmp_path / "sidecar.sock"
    with _recording_sidecar(
        socket_path,
        response={"context": "A durable preference from another project."},
    ) as requests:
        result = _run_hook(
            tmp_path,
            event,
            host=host,
            socket_path=socket_path,
        )

    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                "# Recalled memory\n\n"
                "A durable preference from another project."
            ),
        }
    }
    assert len(requests) == 1
    request = requests[0]
    assert request["method"] == "POST"
    assert request["path"] == "/prefetch"
    assert request["json"]["prompt"] == expected_prompt
    session_id = request["json"]["session_id"]
    assert isinstance(session_id, str)
    host_name, repository, suffix = session_id.split(":")
    assert host_name == host
    assert repository == "mnemosyne"
    assert len(suffix) == 6


def _run_and_record_session_id(
    tmp_path: Path,
    *,
    event: dict[str, object],
    socket_name: str,
) -> str:
    socket_path = tmp_path / socket_name
    with _recording_sidecar(socket_path, response={"context": ""}) as requests:
        result = _run_hook(
            tmp_path,
            event,
            host="codex",
            socket_path=socket_path,
        )
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    return str(requests[0]["json"]["session_id"])


def test_session_id_cache_collapses_worktree_to_parent_repository(
    tmp_path: Path,
) -> None:
    main_checkout = Path("/home/jeff/devel/jeffreyvdb/mnemosyne")
    assert main_checkout.is_dir()
    first = _run_and_record_session_id(
        tmp_path,
        event={
            "session_id": "same-host-session",
            "cwd": str(ROOT),
            "prompt": "first prompt",
        },
        socket_name="worktree.sock",
    )
    second = _run_and_record_session_id(
        tmp_path,
        event={
            "session_id": "same-host-session",
            "cwd": str(main_checkout),
            "prompt": "second prompt",
        },
        socket_name="main-checkout.sock",
    )
    other_session = _run_and_record_session_id(
        tmp_path,
        event={
            "session_id": "different-host-session",
            "cwd": str(ROOT),
            "prompt": "third prompt",
        },
        socket_name="other-session.sock",
    )

    assert first == second
    assert first.split(":")[1] == "mnemosyne"
    assert first.split(":")[2] != other_session.split(":")[2]


@pytest.mark.parametrize(
    "bad_event",
    [
        "{not valid JSON",
        "[]",
    ],
)
def test_hook_exits_zero_when_event_processing_raises(
    tmp_path: Path,
    bad_event: str,
) -> None:
    result = _run_hook(tmp_path, bad_event)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert "Traceback" not in result.stderr


def test_hook_reports_one_line_when_sidecar_is_absent(tmp_path: Path) -> None:
    result = _run_hook(
        tmp_path,
        {
            "session_id": "missing-sidecar",
            "cwd": str(ROOT),
            "user_prompt": "Will memory failure block this prompt?",
        },
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert "Traceback" not in result.stderr


def test_hook_exits_zero_when_sidecar_returns_error(tmp_path: Path) -> None:
    socket_path = tmp_path / "error.sock"
    with _recording_sidecar(socket_path, status=500) as requests:
        result = _run_hook(
            tmp_path,
            {
                "session_id": "error-sidecar",
                "cwd": str(ROOT),
                "prompt": "Codex prompt",
            },
            host="codex",
            socket_path=socket_path,
        )

    assert requests
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1


def test_hook_timeout_proceeds_without_injection(tmp_path: Path) -> None:
    socket_path = tmp_path / "hanging.sock"
    started = time.monotonic()
    with _recording_sidecar(socket_path, hang=True) as requests:
        result = _run_hook(
            tmp_path,
            {
                "session_id": "hanging-sidecar",
                "cwd": str(ROOT),
                "user_prompt": "Do not stall this prompt",
            },
            socket_path=socket_path,
        )
    elapsed = time.monotonic() - started

    assert requests
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert elapsed < 2.5


def test_hook_path_never_imports_a_working_directory_mnemosyne(
    tmp_path: Path,
) -> None:
    shadow_directory = tmp_path / "shadow"
    shadow_directory.mkdir()
    (shadow_directory / "mnemosyne.py").write_text(
        "raise RuntimeError('working-directory shadow imported')\n",
        encoding="utf-8",
    )
    socket_path = tmp_path / "shadow-safe.sock"
    with _recording_sidecar(
        socket_path,
        response={"context": "Shadow-safe recalled context."},
    ):
        result = _run_hook(
            tmp_path,
            {
                "session_id": "shadow-safe",
                "cwd": str(ROOT),
                "prompt": "Use recalled context",
            },
            host="codex",
            socket_path=socket_path,
            cwd=shadow_directory,
        )

    assert result.returncode == 0
    assert "Shadow-safe recalled context." in result.stdout
    assert result.stderr == ""
