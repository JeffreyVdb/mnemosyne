"""Standard-library client for the Agent Hooks Sidecar."""

from __future__ import annotations

import http.client
import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .transport import socket_path as configured_socket_path


@dataclass(frozen=True)
class ClientResult:
    """The outcome of a request to the Sidecar."""

    ok: bool
    status: int | None = None
    data: dict[str, Any] | None = None
    error: str | None = None


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: Path, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(self.timeout)
            connection.connect(str(self._socket_path))
        except BaseException:
            connection.close()
            raise
        self.sock = connection


class SidecarClient:
    """Send HTTP requests to the Sidecar over a unix domain socket."""

    def __init__(self, socket_path: Path | None = None, timeout: float = 1.0) -> None:
        self._socket_path = socket_path if socket_path is not None else configured_socket_path()
        self._timeout = timeout

    def health(self) -> ClientResult:
        """Return the Sidecar's health response or an actionable failure."""

        connection = _UnixHTTPConnection(self._socket_path, self._timeout)
        try:
            connection.request("GET", "/health")
            response = connection.getresponse()
            body = response.read()
            data = json.loads(body)
            if not isinstance(data, dict):
                return ClientResult(ok=False, status=response.status, error="invalid JSON response")
            return ClientResult(
                ok=200 <= response.status < 300,
                status=response.status,
                data=data,
                error=None if 200 <= response.status < 300 else f"HTTP {response.status}",
            )
        except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
            return ClientResult(ok=False, error=str(exc))
        finally:
            connection.close()
