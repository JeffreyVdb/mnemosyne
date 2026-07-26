"""Standard-library client for the Agent Hooks Sidecar."""

from __future__ import annotations

import http.client
import json
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from integrations.agent_hooks.transport import MAX_INJECTION_CHARS
    from integrations.agent_hooks.transport import (
        socket_path as configured_socket_path,
    )
elif __package__:
    from .transport import MAX_INJECTION_CHARS
    from .transport import socket_path as configured_socket_path
else:
    integration_dir = os.path.dirname(os.path.realpath(__file__))
    if integration_dir not in sys.path:
        sys.path.insert(0, integration_dir)
    from transport import MAX_INJECTION_CHARS
    from transport import socket_path as configured_socket_path


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
        self._socket_path = (
            socket_path if socket_path is not None else configured_socket_path()
        )
        self._timeout = timeout

    def health(self) -> ClientResult:
        """Return the Sidecar's health response or an actionable failure."""

        connection = _UnixHTTPConnection(self._socket_path, self._timeout)
        try:
            connection.request("GET", "/health")
            response = connection.getresponse()
            body = response.read(65536)
            if not 200 <= response.status < 300:
                return ClientResult(
                    ok=False,
                    status=response.status,
                    error=f"HTTP {response.status}",
                )
            data = json.loads(body)
            if not isinstance(data, dict):
                return ClientResult(
                    ok=False, status=response.status, error="invalid JSON response"
                )
            return ClientResult(
                ok=True,
                status=response.status,
                data=data,
            )
        except (OSError, http.client.HTTPException, ValueError) as exc:
            return ClientResult(ok=False, error=str(exc))
        finally:
            connection.close()

    def prefetch(self, prompt: str, session_id: str) -> ClientResult:
        """Return recalled context for a prompt, or a non-raising failure."""

        connection = _UnixHTTPConnection(self._socket_path, self._timeout)
        try:
            request_body = json.dumps(
                {"prompt": prompt, "session_id": session_id},
                separators=(",", ":"),
            ).encode("utf-8")
            connection.request(
                "POST",
                "/prefetch",
                body=request_body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            body = response.read(65537)
            if len(body) > 65536:
                return ClientResult(
                    ok=False,
                    status=response.status,
                    error="response too large",
                )
            if not 200 <= response.status < 300:
                return ClientResult(
                    ok=False,
                    status=response.status,
                    error=f"HTTP {response.status}",
                )
            data = json.loads(body)
            if not isinstance(data, dict) or not isinstance(data.get("context"), str):
                return ClientResult(
                    ok=False,
                    status=response.status,
                    error="invalid JSON response",
                )
            if len(data["context"]) > MAX_INJECTION_CHARS:
                return ClientResult(
                    ok=False,
                    status=response.status,
                    error="Injection exceeds size cap",
                )
            return ClientResult(ok=True, status=response.status, data=data)
        except (
            OSError,
            http.client.HTTPException,
            TypeError,
            UnicodeError,
            ValueError,
        ) as exc:
            return ClientResult(ok=False, error=str(exc))
        finally:
            connection.close()
