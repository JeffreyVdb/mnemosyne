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

from integrations.agent_hooks.client import SidecarClient
from integrations.agent_hooks.transport import (
    DATA_DIR_ENV,
    HOOK_TIMEOUT_SECONDS,
    MAX_INJECTION_CHARS,
    SOCKET_ENV,
)


ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = ROOT / "integrations" / "agent_hooks" / "user_prompt_submit.py"


@contextmanager
def _recording_sidecar(
    socket_path: Path,
    *,
    status: int = 200,
    response: dict[str, object] | None = None,
    hang: bool = False,
    trickle_interval: float | None = None,
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
                wire_response = (
                    f"HTTP/1.1 {status} {reason}\r\n".encode()
                    + b"Content-Type: application/json\r\n"
                    + f"Content-Length: {len(response_body)}\r\n".encode()
                    + b"Connection: close\r\n\r\n"
                    + response_body
                )
                if trickle_interval is None:
                    try:
                        connection.sendall(wire_response)
                    except OSError:
                        return
                else:
                    try:
                        for offset in range(0, len(wire_response), 8):
                            connection.sendall(wire_response[offset : offset + 8])
                            time.sleep(trickle_interval)
                    except OSError:
                        return

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
    python_safe_path: bool = False,
    hook_path: Path = HOOK_PATH,
    interpreter_flags: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env[DATA_DIR_ENV] = str(tmp_path / "hook-data")
    env.pop("PYTHONPATH", None)
    if python_safe_path:
        env["PYTHONSAFEPATH"] = "1"
    env[SOCKET_ENV] = str(socket_path or tmp_path / "missing.sock")
    payload = event if isinstance(event, str) else json.dumps(event)
    return subprocess.run(
        [sys.executable, *interpreter_flags, str(hook_path), "--host", host],
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
                "# Recalled memory\n\nA durable preference from another project."
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
    main_checkout = tmp_path / "sample-repository"
    linked_worktree = tmp_path / "linked-worktree"
    subprocess.run(
        ["git", "init", str(main_checkout)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(main_checkout), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(main_checkout), "config", "user.name", "Test User"],
        check=True,
    )
    (main_checkout / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(main_checkout), "add", "tracked.txt"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(main_checkout), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(main_checkout),
            "worktree",
            "add",
            "-b",
            "linked",
            str(linked_worktree),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    first = _run_and_record_session_id(
        tmp_path,
        event={
            "session_id": "same-host-session",
            "cwd": str(linked_worktree),
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
    assert first.split(":")[1] == "sample-repository"
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


@pytest.mark.parametrize(
    ("injection_size", "expected_injection"),
    [
        (MAX_INJECTION_CHARS, True),
        (MAX_INJECTION_CHARS + 1, False),
    ],
)
def test_hook_enforces_final_injection_size_boundary(
    tmp_path: Path,
    injection_size: int,
    expected_injection: bool,
) -> None:
    label = "# Recalled memory\n\n"
    socket_path = tmp_path / f"hook-boundary-{injection_size}.sock"
    with _recording_sidecar(
        socket_path,
        response={"context": "x" * (injection_size - len(label))},
    ):
        result = _run_hook(
            tmp_path,
            {
                "session_id": "hook-boundary",
                "cwd": str(ROOT),
                "user_prompt": "Check the final Injection size",
            },
            socket_path=socket_path,
        )

    assert result.returncode == 0
    if expected_injection:
        output = json.loads(result.stdout)
        assert (
            len(output["hookSpecificOutput"]["additionalContext"])
            == MAX_INJECTION_CHARS
        )
        assert result.stderr == ""
    else:
        assert result.stdout == ""
        assert result.stderr.count("\n") == 1


@pytest.mark.parametrize(
    ("context_size", "expected_ok"),
    [
        (MAX_INJECTION_CHARS, True),
        (MAX_INJECTION_CHARS + 1, False),
    ],
)
def test_client_enforces_context_size_boundary(
    tmp_path: Path,
    context_size: int,
    expected_ok: bool,
) -> None:
    socket_path = tmp_path / f"client-boundary-{context_size}.sock"
    with _recording_sidecar(
        socket_path,
        response={"context": "x" * context_size},
    ):
        result = SidecarClient(socket_path=socket_path).prefetch(
            "Check the client context size",
            "codex:repository:boundary",
        )

    assert result.ok is expected_ok
    if expected_ok:
        assert result.data is not None
        assert len(result.data["context"]) == MAX_INJECTION_CHARS
    else:
        assert result.error == "Injection exceeds size cap"


@pytest.mark.parametrize(
    ("response_size", "expected_ok"),
    [
        (65_536, True),
        (65_537, False),
    ],
)
def test_client_enforces_response_body_read_boundary(
    tmp_path: Path,
    response_size: int,
    expected_ok: bool,
) -> None:
    socket_path = tmp_path / f"response-boundary-{response_size}.sock"
    empty_response_size = len(json.dumps({"context": "", "padding": ""}).encode())
    with _recording_sidecar(
        socket_path,
        response={
            "context": "",
            "padding": "x" * (response_size - empty_response_size),
        },
    ):
        result = SidecarClient(socket_path=socket_path).prefetch(
            "Check the response read limit",
            "codex:repository:oversized",
        )

    assert result.ok is expected_ok
    if expected_ok:
        assert result.data is not None
        assert result.data["context"] == ""
    else:
        assert result.error == "response too large"


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


def test_hook_wall_clock_deadline_stops_a_trickling_peer(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "trickling.sock"
    started = time.monotonic()
    with _recording_sidecar(
        socket_path,
        response={"context": "slow context " * 40},
        trickle_interval=0.1,
    ) as requests:
        result = _run_hook(
            tmp_path,
            {
                "session_id": "trickling-sidecar",
                "cwd": str(ROOT),
                "user_prompt": "Bound the whole Hook",
            },
            socket_path=socket_path,
        )
    elapsed = time.monotonic() - started

    assert requests
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert elapsed < HOOK_TIMEOUT_SECONDS + 0.5


def test_absolute_hook_path_cannot_be_replaced_by_working_directory_package(
    tmp_path: Path,
) -> None:
    shadow_directory = tmp_path / "shadow"
    hostile_package = shadow_directory / "integrations" / "agent_hooks"
    hostile_package.mkdir(parents=True)
    (shadow_directory / "integrations" / "__init__.py").write_text(
        "",
        encoding="utf-8",
    )
    (hostile_package / "__init__.py").write_text("", encoding="utf-8")
    (hostile_package / "user_prompt_submit.py").write_text(
        "print('HOSTILE HOOK RAN')\n",
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


def test_hook_exits_zero_with_python_safe_path(tmp_path: Path) -> None:
    result = _run_hook(
        tmp_path,
        {
            "session_id": "safe-path",
            "cwd": str(ROOT),
            "prompt": "Continue without recalled memory",
        },
        host="codex",
        python_safe_path=True,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == "Mnemosyne recalled memory unavailable.\n"
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    ("interpreter_flags", "python_safe_path"),
    [
        ((), False),
        (("-P",), False),
        (("-I",), False),
        ((), True),
    ],
)
def test_symlinked_hook_loads_real_siblings_in_all_safe_path_modes(
    tmp_path: Path,
    interpreter_flags: tuple[str, ...],
    python_safe_path: bool,
) -> None:
    symlink_directory = tmp_path / "symlink-entrypoint"
    symlink_directory.mkdir()
    symlink_path = symlink_directory / "user_prompt_submit.py"
    symlink_path.symlink_to(HOOK_PATH)
    for module in ("client.py", "identity.py", "transport.py"):
        (symlink_directory / module).write_text(
            "raise RuntimeError('HOSTILE SIBLING LOADED')\n",
            encoding="utf-8",
        )
    socket_path = tmp_path / "symlink-safe.sock"
    with _recording_sidecar(
        socket_path,
        response={"context": "Real sibling modules loaded."},
    ) as requests:
        result = _run_hook(
            tmp_path,
            {
                "session_id": "symlink-safe",
                "cwd": str(ROOT),
                "prompt": "Load only real siblings",
            },
            host="codex",
            socket_path=socket_path,
            cwd=symlink_directory,
            python_safe_path=python_safe_path,
            hook_path=symlink_path,
            interpreter_flags=interpreter_flags,
        )

    assert requests
    assert result.returncode == 0
    assert result.stderr == ""
    assert "Real sibling modules loaded." in result.stdout
    assert "HOSTILE" not in result.stdout
