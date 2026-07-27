from __future__ import annotations

import os
import socket
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from integrations.agent_hooks.client import ClientResult, SidecarClient
from integrations.agent_hooks.transport import DEFAULT_SOCKET_NAME, SOCKET_ENV

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def _running_sidecar(
    *,
    socket_path: Path | None = None,
    home: Path | None = None,
) -> Iterator[subprocess.Popen[str]]:
    env = os.environ.copy()
    if socket_path is None:
        env.pop(SOCKET_ENV, None)
    else:
        env[SOCKET_ENV] = str(socket_path)
    if home is not None:
        env["HOME"] = str(home)
    process = subprocess.Popen(
        [sys.executable, "-m", "integrations.agent_hooks.sidecar"],
        cwd=ROOT,
        env=env,
        preexec_fn=lambda: os.umask(0),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def _assert_process_running(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        stdout, stderr = process.communicate()
        raise AssertionError(
            f"Sidecar exited with {process.returncode}: stdout={stdout!r}, stderr={stderr!r}"
        )


def _wait_for_health(process: subprocess.Popen[str], client: SidecarClient) -> ClientResult:
    deadline = time.monotonic() + 3
    result = client.health()
    while not result.ok and time.monotonic() < deadline:
        _assert_process_running(process)
        time.sleep(0.01)
        result = client.health()
    return result


def _wait_for_socket(process: subprocess.Popen[str], path: Path) -> None:
    deadline = time.monotonic() + 3
    while not path.exists() and time.monotonic() < deadline:
        _assert_process_running(process)
        time.sleep(0.01)


@contextmanager
def _serving_raw_response(socket_path: Path, response: bytes) -> Iterator[None]:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.listen()

    def serve_response() -> None:
        connection, _ = server.accept()
        with connection:
            connection.recv(65536)
            connection.sendall(response)

    thread = threading.Thread(target=serve_response, daemon=True)
    thread.start()
    try:
        yield
    finally:
        thread.join(timeout=1)
        server.close()


def test_connection_failure_is_returned(tmp_path: Path) -> None:
    client = SidecarClient(socket_path=tmp_path / "missing.sock")

    result = client.health()

    assert result.ok is False
    assert result.status is None
    assert result.data is None
    assert result.error


def test_invalid_utf8_response_is_returned_as_failure(tmp_path: Path) -> None:
    socket_path = tmp_path / "invalid-response.sock"
    body = b'{"a": "\xff"}'
    response = (
        b"HTTP/1.1 200 OK\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Connection: close\r\n\r\n"
        + body
    )

    with _serving_raw_response(socket_path, response):
        result = SidecarClient(socket_path=socket_path).health()

    assert result.ok is False
    assert result.error


def test_http_error_is_returned_before_parsing_body(tmp_path: Path) -> None:
    socket_path = tmp_path / "http-error.sock"
    body = b"<html>not JSON</html>"
    response = (
        b"HTTP/1.1 404 Not Found\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Connection: close\r\n\r\n"
        + body
    )

    with _serving_raw_response(socket_path, response):
        result = SidecarClient(socket_path=socket_path).health()

    assert result.ok is False
    assert result.status == 404
    assert result.data is None
    assert result.error == "HTTP 404"


def test_health_reports_version_and_zero_live_sessions(tmp_path: Path) -> None:
    socket_path = tmp_path / "sidecar.sock"
    home = tmp_path / "home"
    home.mkdir()
    with _running_sidecar(socket_path=socket_path, home=home) as process:
        client = SidecarClient(socket_path=socket_path)
        result = _wait_for_health(process, client)

        assert result.ok is True
        assert result.status == 200
        assert result.data == {
            "status": "ok",
            "version": "0.1.0",
            "live_sessions": 0,
        }
        assert result.error is None


def test_idle_peer_does_not_block_health_request(tmp_path: Path) -> None:
    socket_path = tmp_path / "sidecar.sock"
    with _running_sidecar(socket_path=socket_path) as process:
        client = SidecarClient(socket_path=socket_path, timeout=0.2)
        assert _wait_for_health(process, client).ok is True

        idle_peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        idle_peer.connect(str(socket_path))
        try:
            result = client.health()
        finally:
            idle_peer.close()

        assert result.ok is True


def test_socket_is_owner_only(tmp_path: Path) -> None:
    socket_path = tmp_path / "sidecar.sock"
    with _running_sidecar(socket_path=socket_path) as process:
        _wait_for_socket(process, socket_path)

        assert socket_path.exists()
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600


def test_stale_socket_is_replaced(tmp_path: Path) -> None:
    socket_path = tmp_path / "sidecar.sock"
    stale_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale_socket.bind(str(socket_path))
    stale_socket.close()

    with _running_sidecar(socket_path=socket_path) as process:
        client = SidecarClient(socket_path=socket_path)
        result = _wait_for_health(process, client)

        assert result.ok is True


def test_running_sidecar_is_not_replaced(tmp_path: Path) -> None:
    socket_path = tmp_path / "sidecar.sock"
    client = SidecarClient(socket_path=socket_path)

    with _running_sidecar(socket_path=socket_path) as first_process:
        assert _wait_for_health(first_process, client).ok is True
        socket_inode = socket_path.stat().st_ino

        with _running_sidecar(socket_path=socket_path) as second_process:
            deadline = time.monotonic() + 3
            while second_process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)

            _stdout, stderr = second_process.communicate()
            assert second_process.returncode == 1
            assert stderr == (
                "Sidecar failed to start: RuntimeError: "
                f"Sidecar is already running at {socket_path}\n"
            )
            assert socket_path.stat().st_ino == socket_inode
            assert first_process.poll() is None
            assert client.health().ok is True


def test_client_uses_socket_environment_override(tmp_path: Path, monkeypatch) -> None:
    socket_path = tmp_path / "sidecar.sock"
    monkeypatch.setenv(SOCKET_ENV, str(socket_path))
    with _running_sidecar(socket_path=socket_path) as process:
        client = SidecarClient()
        result = _wait_for_health(process, client)

        assert result.ok is True


def test_sigterm_removes_socket(tmp_path: Path) -> None:
    socket_path = tmp_path / "sidecar.sock"
    with _running_sidecar(socket_path=socket_path) as process:
        _wait_for_socket(process, socket_path)
        assert socket_path.exists()

        process.terminate()
        process.wait(timeout=3)

        assert process.returncode == 0
        assert not socket_path.exists()


def test_default_socket_is_directly_under_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    expected_socket = home / DEFAULT_SOCKET_NAME
    with _running_sidecar(home=home) as process:
        _wait_for_socket(process, expected_socket)

        assert expected_socket.exists()
