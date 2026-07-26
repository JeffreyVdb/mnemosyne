# Mnemosyne Agent Hooks

This integration will carry Host Hook events to an integration-owned Sidecar.
Ticket 0002 provides only the transport foundation: the Sidecar process, its
Unix-socket HTTP client, and `GET /health`. No Provider is loaded yet, so the
health response always reports zero live Sessions.

## Run the Sidecar

From the repository root:

```bash
python -m integrations.agent_hooks.sidecar
```

The Sidecar binds `$HOME/.mnemosyne-hooks.sock` by default. Set
`MNEMOSYNE_HOOKS_SOCKET` to override that path, including when pointing a Hook
or test at a stub:

```bash
MNEMOSYNE_HOOKS_SOCKET=/tmp/mnemosyne-hooks.sock \
  python -m integrations.agent_hooks.sidecar
```

The socket is created with mode `0600`. A stale socket is removed at startup,
and SIGTERM shuts the Sidecar down cleanly and removes the socket.

The standard-library client returns a `ClientResult` value on both success and
failure:

```python
from integrations.agent_hooks.client import SidecarClient

health = SidecarClient().health()
if not health.ok:
    print(health.error)
```

On success, `GET /health` returns:

```json
{"status": "ok", "version": "0.1", "live_sessions": 0}
```

## Measured round-trip time

Measured on 2026-07-26 on the development machine, using 10 warm-up requests
followed by 200 sequential `SidecarClient.health()` calls over a socket in a
temporary directory:

- minimum: 0.289 ms
- median: 0.339 ms
- p95: 0.385 ms
- maximum: 0.409 ms

This is single-digit milliseconds and is consistent with the 0.85–1.65 ms
design measurement.

## Layout

- `client.py` — standard-library HTTP client over `AF_UNIX`
- `sidecar.py` — Sidecar command and health route
- `transport.py` — shared socket path and environment override
- `tests/test_sidecar.py` — real-process tests at the socket boundary (Seam B)
