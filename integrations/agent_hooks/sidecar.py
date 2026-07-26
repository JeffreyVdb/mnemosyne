"""Long-lived Sidecar process for agent Hook requests."""

from __future__ import annotations

import json
import os
import signal
import socket
import socketserver
import stat
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from . import __version__
from .provider_cache import ProviderLRU
from .transport import socket_path


class _HealthHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 5
    _MAX_REQUEST_BYTES = 65_536

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        self._send_json(
            200,
            {
                "status": "ok",
                "version": __version__,
                "live_sessions": self.server.provider_cache.live_sessions,
            },
        )

    def do_POST(self) -> None:
        if self.path != "/prefetch":
            self.send_error(404)
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_json(400, {"error": "invalid Content-Length"})
            return
        if not 0 <= content_length <= self._MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "request too large"})
            return
        try:
            payload = json.loads(self.rfile.read(content_length))
        except (UnicodeError, ValueError):
            self._send_json(400, {"error": "invalid JSON request"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "request must be a JSON object"})
            return
        prompt = payload.get("prompt")
        session_id = payload.get("session_id")
        if not isinstance(prompt, str) or not isinstance(session_id, str):
            self._send_json(
                400,
                {"error": "prompt and session_id must be strings"},
            )
            return
        try:
            context = self.server.provider_cache.prefetch(prompt, session_id)
        except Exception:
            self._send_json(500, {"error": "prefetch failed"})
            return
        self._send_json(200, {"context": context})

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def log_message(self, format: str, *args: Any) -> None:
        return


class _SidecarServer(socketserver.ThreadingUnixStreamServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = socket.SOMAXCONN

    def __init__(
        self,
        server_address: str,
        handler: type[_HealthHandler],
        provider_cache: ProviderLRU,
    ) -> None:
        self.provider_cache = provider_cache
        super().__init__(server_address, handler)

    def server_bind(self) -> None:
        previous_umask = os.umask(0o177)
        try:
            super().server_bind()
        finally:
            os.umask(previous_umask)
        os.chmod(str(self.server_address), 0o600)

    def handle_error(self, _request: object, _client_address: object) -> None:
        error = sys.exception()
        message = str(error).replace("\r", " ").replace("\n", " ")
        print(f"Sidecar request failed: {message}", file=sys.stderr)


class _ShutdownRequested(BaseException):
    pass


def _socket_metadata(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _remove_stale_socket(path: Path) -> None:
    metadata = _socket_metadata(path)
    if metadata is None:
        return
    if not stat.S_ISSOCK(metadata.st_mode):
        raise FileExistsError(f"refusing to replace non-socket path: {path}")

    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.25)
    try:
        probe.connect(str(path))
    except ConnectionRefusedError:
        pass
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RuntimeError(f"cannot prove existing socket is stale: {path}") from exc
    else:
        raise RuntimeError(f"Sidecar is already running at {path}")
    finally:
        probe.close()

    current = _socket_metadata(path)
    if current is None:
        return
    if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
        raise RuntimeError(
            f"socket changed while checking whether it was stale: {path}"
        )
    path.unlink()


def _remove_owned_socket(path: Path, identity: tuple[int, int] | None) -> None:
    if identity is None:
        return
    metadata = _socket_metadata(path)
    if metadata is None:
        return
    if (
        stat.S_ISSOCK(metadata.st_mode)
        and (metadata.st_dev, metadata.st_ino) == identity
    ):
        path.unlink()


def _request_shutdown(_signum: int, _frame: object) -> None:
    raise _ShutdownRequested


def main() -> None:
    """Serve Sidecar requests until the process is stopped."""

    path = socket_path()
    socket_identity: tuple[int, int] | None = None
    previous_sigterm = signal.signal(signal.SIGTERM, _request_shutdown)
    try:
        try:
            provider_cache = ProviderLRU()
            _remove_stale_socket(path)
            server = _SidecarServer(
                str(path),
                _HealthHandler,
                provider_cache,
            )
        except (OSError, RuntimeError) as exc:
            print(f"Sidecar failed to start: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        with server:
            metadata = path.stat()
            socket_identity = (metadata.st_dev, metadata.st_ino)
            server.serve_forever()
    except _ShutdownRequested:
        pass
    finally:
        if "provider_cache" in locals():
            provider_cache.shutdown()
        _remove_owned_socket(path, socket_identity)
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    main()
