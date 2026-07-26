from __future__ import annotations

import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from integrations.agent_hooks.client import SidecarClient
from integrations.agent_hooks.hygiene import (
    hygienic_prompt,
    is_pseudo_prompt,
    redact_credentials,
)
from integrations.agent_hooks.transport import DATA_DIR_ENV, SOCKET_ENV


ROOT = Path(__file__).resolve().parents[1]
SUBMIT_HOOK = ROOT / "integrations" / "agent_hooks" / "user_prompt_submit.py"
CAPTURE_HOOK = ROOT / "integrations" / "agent_hooks" / "turn_end.py"
_SAFE_PATH_ONLY = pytest.mark.skipif(
    sys.version_info < (3, 11),
    reason="-P and PYTHONSAFEPATH require Python 3.11",
)


@contextmanager
def _recording_sidecar(
    socket_path: Path,
    *,
    response_by_path: dict[str, tuple[int, dict[str, object]]] | None = None,
    hang: bool = False,
) -> Iterator[list[dict[str, object]]]:
    requests: list[dict[str, object]] = []
    ready = threading.Event()
    stop = threading.Event()

    def serve() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(socket_path))
            server.listen()
            server.settimeout(0.05)
            ready.set()
            while not stop.is_set():
                try:
                    connection, _ = server.accept()
                except TimeoutError:
                    continue
                with connection:
                    received = b""
                    while b"\r\n\r\n" not in received:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        received += chunk
                    if b"\r\n\r\n" not in received:
                        continue
                    headers, body = received.split(b"\r\n\r\n", 1)
                    request_line, *header_lines = headers.decode("ascii").split(
                        "\r\n"
                    )
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
                        stop.wait(2)
                        continue
                    status, response = (response_by_path or {}).get(
                        path, (200, {})
                    )
                    response_body = json.dumps(response).encode()
                    reason = "OK" if status < 400 else "Internal Server Error"
                    try:
                        connection.sendall(
                            f"HTTP/1.1 {status} {reason}\r\n".encode()
                            + b"Content-Type: application/json\r\n"
                            + f"Content-Length: {len(response_body)}\r\n".encode()
                            + b"Connection: close\r\n\r\n"
                            + response_body
                        )
                    except OSError:
                        pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=1)
    try:
        yield requests
    finally:
        stop.set()
        thread.join(timeout=3)


def _run_hook(
    tmp_path: Path,
    hook: Path,
    event: dict[str, object] | str,
    *,
    host: str,
    socket_path: Path,
    extra_env: dict[str, str] | None = None,
    interpreter_flags: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env[DATA_DIR_ENV] = str(tmp_path / "hook-data")
    env[SOCKET_ENV] = str(socket_path)
    env.pop("PYTHONPATH", None)
    env.update(extra_env or {})
    payload = event if isinstance(event, str) else json.dumps(event)
    return subprocess.run(
        [sys.executable, *interpreter_flags, str(hook), "--host", host],
        input=payload,
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=env,
        check=False,
        timeout=3,
    )


@pytest.mark.parametrize("host", ["claude-code", "codex"])
def test_stop_hook_pairs_redacted_prompt_with_host_assistant_field(
    tmp_path: Path,
    host: str,
) -> None:
    socket_path = tmp_path / "sidecar.sock"
    session = f"{host}-session"
    submit_event = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": session,
        "cwd": str(ROOT),
        "user_prompt" if host == "claude-code" else "prompt": (
            "Deploy with token ghp_abcdefghijklmnopqrstuvwxyz0123456789"
        ),
    }
    stop_event = {
        "hook_event_name": "Stop",
        "session_id": session,
        "cwd": str(ROOT),
        "last_assistant_message": "Deployment completed without transcript parsing.",
    }
    with _recording_sidecar(
        socket_path,
        response_by_path={
            "/prefetch": (200, {"context": ""}),
            "/capture": (202, {"accepted": True}),
        },
    ) as requests:
        submit = _run_hook(
            tmp_path, SUBMIT_HOOK, submit_event, host=host, socket_path=socket_path
        )
        capture = _run_hook(
            tmp_path, CAPTURE_HOOK, stop_event, host=host, socket_path=socket_path
        )

    assert submit.returncode == capture.returncode == 0
    assert submit.stderr == capture.stderr == ""
    capture_request = next(request for request in requests if request["path"] == "/capture")
    payload = capture_request["json"]
    assert payload["user_content"] == "Deploy with token [REDACTED:GITHUB_TOKEN]"
    assert payload["assistant_content"] == (
        "Deployment completed without transcript parsing."
    )
    assert str(payload["session_id"]).startswith(f"{host}:mnemosyne:")
    assert not list((tmp_path / "hook-data" / "pending").glob("*.json"))


@pytest.mark.parametrize(
    "prompt",
    [
        "<task-notification><status>completed</status></task-notification>",
        "<system-reminder>machine context</system-reminder>",
        "<local-command-stdout>command output</local-command-stdout>",
        "<command-name>/compact</command-name>",
    ],
)
def test_pseudo_prompt_never_reaches_capture_stub(
    tmp_path: Path,
    prompt: str,
) -> None:
    socket_path = tmp_path / "sidecar.sock"
    event = {
        "session_id": "pseudo-session",
        "cwd": str(ROOT),
        "user_prompt": prompt,
    }
    with _recording_sidecar(
        socket_path,
        response_by_path={"/prefetch": (200, {"context": ""})},
    ) as requests:
        submit = _run_hook(
            tmp_path,
            SUBMIT_HOOK,
            event,
            host="claude-code",
            socket_path=socket_path,
        )
        stop = _run_hook(
            tmp_path,
            CAPTURE_HOOK,
            {
                "session_id": "pseudo-session",
                "cwd": str(ROOT),
                "last_assistant_message": "Machine acknowledgement.",
            },
            host="claude-code",
            socket_path=socket_path,
        )

    assert submit.returncode == stop.returncode == 0
    assert requests == []


def test_capture_suppression_keeps_injection_working(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "sidecar.sock"
    event = {
        "session_id": "sensitive-session",
        "cwd": str(ROOT),
        "user_prompt": "Sensitive but useful query",
    }
    env = {"MNEMOSYNE_CAPTURE_SUPPRESS": "1"}
    with _recording_sidecar(
        socket_path,
        response_by_path={"/prefetch": (200, {"context": "Safe recalled context."})},
    ) as requests:
        submit = _run_hook(
            tmp_path,
            SUBMIT_HOOK,
            event,
            host="claude-code",
            socket_path=socket_path,
            extra_env=env,
        )
        stop = _run_hook(
            tmp_path,
            CAPTURE_HOOK,
            {
                "session_id": "sensitive-session",
                "cwd": str(ROOT),
                "last_assistant_message": "Sensitive answer",
            },
            host="claude-code",
            socket_path=socket_path,
            extra_env=env,
        )

    assert json.loads(submit.stdout)["hookSpecificOutput"]["additionalContext"].endswith(
        "Safe recalled context."
    )
    assert stop.returncode == 0
    assert [request["path"] for request in requests] == ["/prefetch"]
    assert not (tmp_path / "hook-data" / "pending").exists()


def test_directory_capture_suppression_uses_resolved_ancestor(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "sidecar.sock"
    sensitive = tmp_path / "sensitive"
    child = sensitive / "child"
    child.mkdir(parents=True)
    env = {"MNEMOSYNE_CAPTURE_SUPPRESS_DIRS": str(sensitive)}
    with _recording_sidecar(
        socket_path,
        response_by_path={"/prefetch": (200, {"context": ""})},
    ) as requests:
        submit = _run_hook(
            tmp_path,
            SUBMIT_HOOK,
            {
                "session_id": "directory-session",
                "cwd": str(child),
                "user_prompt": "Do not persist this",
            },
            host="claude-code",
            socket_path=socket_path,
            extra_env=env,
        )
        stop = _run_hook(
            tmp_path,
            CAPTURE_HOOK,
            {
                "session_id": "directory-session",
                "cwd": str(child),
                "last_assistant_message": "Also private",
            },
            host="claude-code",
            socket_path=socket_path,
            extra_env=env,
        )
    assert submit.returncode == stop.returncode == 0
    assert [request["path"] for request in requests] == ["/prefetch"]


@pytest.mark.parametrize(
    ("event", "expected_lines"),
    [
        ("not-json", 1),
        ({"session_id": "missing-prompt", "cwd": str(ROOT)}, 0),
    ],
)
def test_stop_hook_exits_zero_on_every_input_path(
    tmp_path: Path,
    event: dict[str, object] | str,
    expected_lines: int,
) -> None:
    result = _run_hook(
        tmp_path,
        CAPTURE_HOOK,
        event,
        host="claude-code",
        socket_path=tmp_path / "missing.sock",
    )
    assert result.returncode == 0
    assert len(result.stderr.splitlines()) == expected_lines


def test_stop_hook_unreachable_sidecar_reports_exactly_one_line(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "submit.sock"
    event = {
        "session_id": "unreachable-session",
        "cwd": str(ROOT),
        "user_prompt": "Remember this durable prompt",
    }
    with _recording_sidecar(
        socket_path,
        response_by_path={"/prefetch": (200, {"context": ""})},
    ):
        _run_hook(
            tmp_path,
            SUBMIT_HOOK,
            event,
            host="claude-code",
            socket_path=socket_path,
        )
    result = _run_hook(
        tmp_path,
        CAPTURE_HOOK,
        {
            "session_id": "unreachable-session",
            "cwd": str(ROOT),
            "last_assistant_message": "Durable answer",
        },
        host="claude-code",
        socket_path=tmp_path / "missing.sock",
    )
    assert result.returncode == 0
    assert result.stderr.splitlines() == ["Mnemosyne capture unavailable."]


def test_submit_persists_only_redacted_owner_only_pairing_state(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "sidecar.sock"
    secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    with _recording_sidecar(
        socket_path,
        response_by_path={"/prefetch": (200, {"context": ""})},
    ):
        result = _run_hook(
            tmp_path,
            SUBMIT_HOOK,
            {
                "session_id": "durable-redaction-session",
                "cwd": str(ROOT),
                "user_prompt": f"Keep this turn but replace {secret} safely.",
            },
            host="claude-code",
            socket_path=socket_path,
        )
    pending_dir = tmp_path / "hook-data" / "pending"
    state_files = list(pending_dir.glob("*.json"))
    assert result.returncode == 0
    assert len(state_files) == 1
    state = state_files[0].read_text(encoding="utf-8")
    assert secret not in state
    assert "[REDACTED:GITHUB_TOKEN]" in state
    assert stat.S_IMODE(pending_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(state_files[0].stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("status", "hang"),
    [(500, False), (200, True)],
)
def test_stop_hook_exits_zero_when_sidecar_errors_or_hangs(
    tmp_path: Path,
    status: int,
    hang: bool,
) -> None:
    submit_socket = tmp_path / "submit.sock"
    event = {
        "session_id": f"failure-{status}-{hang}",
        "cwd": str(ROOT),
        "user_prompt": "Durable prompt for a failing Capture request.",
    }
    with _recording_sidecar(
        submit_socket,
        response_by_path={"/prefetch": (200, {"context": ""})},
    ):
        _run_hook(
            tmp_path,
            SUBMIT_HOOK,
            event,
            host="codex",
            socket_path=submit_socket,
        )
    capture_socket = tmp_path / "capture.sock"
    with _recording_sidecar(
        capture_socket,
        response_by_path={"/capture": (status, {"accepted": status < 400})},
        hang=hang,
    ):
        result = _run_hook(
            tmp_path,
            CAPTURE_HOOK,
            {
                "session_id": event["session_id"],
                "cwd": str(ROOT),
                "last_assistant_message": "Assistant response.",
            },
            host="codex",
            socket_path=capture_socket,
        )
    assert result.returncode == 0
    assert result.stderr.splitlines() == ["Mnemosyne capture unavailable."]


@pytest.mark.parametrize(
    ("secret", "label"),
    [
        ("AKIAABCDEFGHIJKLMNOP", "AWS_ACCESS_KEY"),
        ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "GITHUB_TOKEN"),
        ("sk-ant-abcdefghijklmnopqrstuvwxyz012345", "ANTHROPIC_API_KEY"),
        ("sk-proj-abcdefghijklmnopqrstuvwxyz012345", "OPENAI_API_KEY"),
        ("xoxb-" + "1234567890-" + "abcdefghijklmnop", "SLACK_TOKEN"),
        (
            "eyJabcdefghij.abcdefghijklmno.abcdefghijklmnop",
            "JWT",
        ),
        ("Bearer abcdefghijklmnopqrstuvwxyz012345", "BEARER_TOKEN"),
        ("api_key=abcdefghijklmnopqrstuvwxyz", "ASSIGNED_SECRET"),
        ("MNEMOSYNE_LLM_API_KEY=abcdefghijklmnopqrstuvwxyz", "ASSIGNED_SECRET"),
        ("AWS_SECRET_ACCESS_KEY=abcdefghijklmnopqrstuvwxyz", "ASSIGNED_SECRET"),
    ],
)
def test_shared_hygiene_redacts_recognized_credential_shapes(
    secret: str,
    label: str,
) -> None:
    content = f"Keep the useful prefix; use {secret}; keep the useful suffix."
    redacted = redact_credentials(content)
    assert secret not in redacted
    assert f"[REDACTED:{label}]" in redacted
    assert redacted.startswith("Keep the useful prefix;")
    assert redacted.endswith("keep the useful suffix.")


def test_shared_hygiene_rejects_bank_observed_task_wrapper() -> None:
    prompt = (
        "<task-notification><task-id>abc</task-id><status>completed</status>"
        "<output-file>/tmp/result</output-file><summary>done</summary>"
        "</task-notification>"
    )
    assert is_pseudo_prompt(prompt)
    assert hygienic_prompt(prompt) == ""


@_SAFE_PATH_ONLY
def test_all_top_level_hook_imports_pin_real_sibling_directory(
    tmp_path: Path,
) -> None:
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    loader = tmp_path / "loader"
    loader.mkdir()
    for module in ("client", "identity", "transport"):
        (hostile / f"{module}.py").write_text(
            f"raise RuntimeError('PWNED hostile {module}')\n",
            encoding="utf-8",
        )
    integration_dir = ROOT / "integrations" / "agent_hooks"
    for module in ("client", "identity", "user_prompt_submit", "turn_end"):
        (loader / f"{module}.py").symlink_to(integration_dir / f"{module}.py")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(loader), str(hostile)))
    for module in ("client", "identity", "user_prompt_submit", "turn_end"):
        result = subprocess.run(
            [sys.executable, "-P", "-c", f"import {module}"],
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=3,
        )
        assert result.returncode == 0, (module, result.stderr)


def _sidecar_env(tmp_path: Path, socket_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env["MNEMOSYNE_DATA_DIR"] = str(tmp_path / "bank")
    env["MNEMOSYNE_NO_EMBEDDINGS"] = "1"
    env["MNEMOSYNE_AUTO_SLEEP_ENABLED"] = "0"
    env[SOCKET_ENV] = str(socket_path)
    return env


@contextmanager
def _running_sidecar(
    tmp_path: Path,
    *,
    capacity: int | None = None,
) -> Iterator[tuple[subprocess.Popen[str], SidecarClient, Path]]:
    socket_path = tmp_path / "sidecar.sock"
    env = _sidecar_env(tmp_path, socket_path)
    if capacity is not None:
        env["MNEMOSYNE_HOOKS_PROVIDER_CACHE_SIZE"] = str(capacity)
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
        while not client.health().ok and time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError((process.returncode, stdout, stderr))
            time.sleep(0.02)
        assert client.health().ok
        yield process, client, tmp_path / "bank" / "mnemosyne.db"
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)


def _captured_rows(
    db_path: Path,
    *,
    minimum: int = 1,
) -> list[tuple[str, str, float, str]]:
    deadline = time.monotonic() + 8
    rows: list[tuple[str, str, float, str]] = []
    while time.monotonic() < deadline:
        if db_path.exists():
            try:
                with sqlite3.connect(db_path) as connection:
                    rows = connection.execute(
                        "SELECT content, session_id, importance, scope "
                        "FROM working_memory WHERE source = 'conversation' "
                        "ORDER BY rowid"
                    ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if len(rows) >= minimum:
                return rows
        time.sleep(0.02)
    return rows


def test_capture_route_writes_raw_global_rows_with_provenance(
    tmp_path: Path,
) -> None:
    session_id = "claude-code:mnemosyne:a1b2c3"
    with _running_sidecar(tmp_path) as (_process, client, db_path):
        result = client.capture(
            "User raw evidence for the Capture route.",
            "Assistant raw evidence for Consolidation.",
            session_id,
        )
        rows = _captured_rows(db_path, minimum=2)
    assert result.ok, result.error
    assert result.status == 202
    assert rows == [
        (
            "[USER] User raw evidence for the Capture route.",
            f"hermes_{session_id}",
            0.5,
            "global",
        ),
        (
            "[ASSISTANT] Assistant raw evidence for Consolidation.",
            f"hermes_{session_id}",
            0.15,
            "global",
        ),
    ]


def test_provider_ignore_patterns_still_filter_capture_rows(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "bank"
    data_dir.mkdir()
    (data_dir / "config.yaml").write_text(
        "ignore_patterns:\n  - '^Ignore this exact user noise'\n",
        encoding="utf-8",
    )
    with _running_sidecar(tmp_path) as (_process, client, db_path):
        result = client.capture(
            "Ignore this exact user noise forever.",
            "Assistant content remains raw evidence.",
            "codex:mnemosyne:1a2b3c",
        )
        rows = _captured_rows(db_path, minimum=1)
    assert result.ok
    assert [row[0] for row in rows] == [
        "[ASSISTANT] Assistant content remains raw evidence."
    ]


def test_real_sidecar_bank_never_receives_pseudo_prompt(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "sidecar.sock"
    with _running_sidecar(tmp_path) as (_process, client, db_path):
        assert client.capture(
            "Control user evidence remains present.",
            "Control assistant evidence remains present.",
            "codex:mnemosyne:c0ffee",
        ).ok
        assert len(_captured_rows(db_path, minimum=2)) == 2
        event = {
            "session_id": "pseudo-bank-session",
            "cwd": str(ROOT),
            "prompt": (
                "<task-notification><status>completed</status>"
                "<summary>machine output</summary></task-notification>"
            ),
        }
        submit = _run_hook(
            tmp_path,
            SUBMIT_HOOK,
            event,
            host="codex",
            socket_path=socket_path,
        )
        stop = _run_hook(
            tmp_path,
            CAPTURE_HOOK,
            {
                "session_id": event["session_id"],
                "cwd": str(ROOT),
                "last_assistant_message": "Machine acknowledgement.",
            },
            host="codex",
            socket_path=socket_path,
        )
        time.sleep(0.1)
        rows = _captured_rows(db_path, minimum=2)
    assert submit.returncode == stop.returncode == 0
    assert len(rows) == 2
    assert all("<task-notification>" not in row[0] for row in rows)


def test_capture_survives_sidecar_restart_between_submit_and_stop(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "sidecar.sock"
    event = {
        "session_id": "restart-session",
        "cwd": str(ROOT),
        "user_prompt": "Prompt survives a Sidecar restart.",
    }
    with _running_sidecar(tmp_path) as (first, _client, _db):
        submit = _run_hook(
            tmp_path,
            SUBMIT_HOOK,
            event,
            host="claude-code",
            socket_path=socket_path,
        )
        first.terminate()
        first.wait(timeout=10)
    with _running_sidecar(tmp_path) as (_second, _client, db_path):
        stop = _run_hook(
            tmp_path,
            CAPTURE_HOOK,
            {
                "session_id": "restart-session",
                "cwd": str(ROOT),
                "last_assistant_message": "Restart-safe assistant response.",
            },
            host="claude-code",
            socket_path=socket_path,
        )
        rows = _captured_rows(db_path, minimum=2)
    assert submit.returncode == stop.returncode == 0
    assert [row[0] for row in rows] == [
        "[USER] Prompt survives a Sidecar restart.",
        "[ASSISTANT] Restart-safe assistant response.",
    ]


def test_sigterm_drains_acknowledged_capture(
    tmp_path: Path,
) -> None:
    with _running_sidecar(tmp_path) as (process, client, db_path):
        result = client.capture(
            "Acknowledged user write survives SIGTERM.",
            "Acknowledged assistant write survives SIGTERM.",
            "codex:mnemosyne:dead00",
        )
        assert result.ok
        process.terminate()
        process.wait(timeout=10)
        rows = _captured_rows(db_path, minimum=2)
    assert len(rows) == 2


def test_ten_hook_captures_emit_no_departed_peer_stderr(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "sidecar.sock"
    hook_stderr: list[str] = []
    with _running_sidecar(tmp_path) as (process, _client, db_path):
        for index in range(10):
            event = {
                "session_id": f"quiet-session-{index}",
                "cwd": str(ROOT),
                "user_prompt": f"Quiet user Capture number {index}.",
            }
            submit = _run_hook(
                tmp_path,
                SUBMIT_HOOK,
                event,
                host="claude-code",
                socket_path=socket_path,
            )
            stop = _run_hook(
                tmp_path,
                CAPTURE_HOOK,
                {
                    "session_id": event["session_id"],
                    "cwd": str(ROOT),
                    "last_assistant_message": (
                        f"Quiet assistant Capture number {index}."
                    ),
                },
                host="claude-code",
                socket_path=socket_path,
            )
            hook_stderr.extend((submit.stderr, stop.stderr))
        process.terminate()
        process.wait(timeout=10)
        _stdout, sidecar_stderr = process.communicate()
        rows = _captured_rows(db_path, minimum=20)
    assert hook_stderr == [""] * 20
    assert sidecar_stderr == ""
    assert len(rows) == 20


def test_lru_eviction_and_concurrent_session_ids_do_not_lose_writes(
    tmp_path: Path,
) -> None:
    sessions = [f"codex:repository:{index:06d}" for index in range(8)]
    with _running_sidecar(tmp_path, capacity=1) as (_process, _client, db_path):
        barrier = threading.Barrier(len(sessions))

        def capture(session_id: str) -> bool:
            barrier.wait(timeout=3)
            return SidecarClient(
                socket_path=tmp_path / "sidecar.sock", timeout=3
            ).capture(
                f"User evidence for {session_id}.",
                f"Assistant evidence for {session_id}.",
                session_id,
            ).ok

        with ThreadPoolExecutor(max_workers=len(sessions)) as executor:
            acknowledgements = list(executor.map(capture, sessions))
        assert all(acknowledgements)
    rows = _captured_rows(db_path, minimum=len(sessions) * 2)
    assert len(rows) == len(sessions) * 2
    assert {row[1] for row in rows} == {f"hermes_{session}" for session in sessions}
