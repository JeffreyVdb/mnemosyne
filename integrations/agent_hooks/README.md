# Mnemosyne Agent Hooks

This integration carries Host Hook events to an integration-owned Sidecar. The
Sidecar keeps one initialized Provider warm per Session id and serves health and
Prefetch requests over an owner-only Unix socket.

## Run the Sidecar

Use the managed-service launcher with an absolute interpreter and launcher path:

```text
/absolute/path/to/python -I /absolute/path/to/integrations/agent_hooks/run_sidecar.py
```

`-I` enables isolated mode, which discards `PYTHONPATH` and does not prepend the
working directory. The launcher resolves the launcher target's real path, derives
the repository root from that resolved path, and adds the root before importing
the Sidecar package. Its imports therefore do not depend on the directory from
which it was started, the path of a symlink used to invoke it, or an editable
package install.

For an import-provenance check, append `--print-import-provenance`; the launcher
prints the resolved `sidecar.py` path once before serving requests.

The Sidecar binds `$HOME/.mnemosyne-hooks.sock` by default. Set
`MNEMOSYNE_HOOKS_SOCKET` to override the socket path:

```text
MNEMOSYNE_HOOKS_SOCKET=/tmp/mnemosyne-hooks.sock \
  /absolute/path/to/python -I /absolute/path/to/integrations/agent_hooks/run_sidecar.py
```

The socket is created with mode `0600`. A stale socket is removed at startup,
and SIGTERM shuts the Sidecar down cleanly and removes the socket.

## Managed-service templates

Copyable templates for both supported service managers live in `services/`:

- `mnemosyne-agent-hooks-sidecar.service.in` is a systemd user unit. It follows
  the local Mnemosyne service style with `Type=simple`, the optional
  `%h/.config/mnemosyne/llm.env` environment file,
  `Restart=on-failure`, and `WantedBy=default.target`.
- `com.mnemosyne.agent-hooks-sidecar.plist.in` is a launchd agent definition with
  `RunAtLoad` and `KeepAlive` after an unsuccessful exit.

The installer must replace `@MNEMOSYNE_PYTHON@` with the absolute path of the
installed environment's interpreter and `@MNEMOSYNE_SIDECAR_LAUNCHER@` with the
absolute path of the installed `run_sidecar.py`. It must reject non-absolute
substitutions. The templates deliberately contain tokens rather than paths for
this development machine.

On Linux, the installer must also enable lingering with
`loginctl enable-linger "$USER"` when needed, or print that exact operator action
if it cannot do so. A user unit under `default.target` otherwise stops at logout
on a machine where lingering is disabled.

`GET /health` reports the integration version and the current number of cached
Session ids:

```json
{"status": "ok", "version": "0.1", "live_sessions": 3}
```

`POST /prefetch` accepts a JSON object containing `prompt` and `session_id`, and
returns a JSON object containing `context`. Providers are cached in an
eight-entry LRU by default. Set `MNEMOSYNE_HOOKS_PROVIDER_CACHE_SIZE` to change
that capacity.

The Sidecar registers and selects the `agent-hooks` Provider profile. It requests
at most five memories and caps each memory's content at 1,200 characters.
Non-empty Provider output that still exceeds the integration's aggregate limit
is omitted rather than truncated.

## Run the UserPromptSubmit Hook

The Host must invoke the Hook with an absolute interpreter and the Hook's
absolute file path:

```text
/abs/python /abs/integrations/agent_hooks/user_prompt_submit.py --host claude-code
```

Use `--host codex` for Codex. Do not invoke the Hook with `python -m`. Plain-script
execution explicitly puts the Hook directory first on `sys.path` before loading
sibling modules. The path is derived from the entry point's `__file__`, never the
Host's working directory, including when `-P`, `-I`, or `PYTHONSAFEPATH=1`
disables Python's implicit path prepend. This exact argv rule applies to all later
Hook entry points.

The Hook reads a Host event from stdin, calls `POST /prefetch`, and emits recalled
memory as visibly labelled `hookSpecificOutput.additionalContext`. It exits zero
on every path. An unavailable Sidecar, a deadline, or an oversized Injection
produces no stdout and one diagnostic line on stderr.

The Hook has a 0.75-second wall-clock deadline once it is running; interpreter
startup falls outside that deadline. Final Injection is capped at 12,000
characters. Set `MNEMOSYNE_HOOKS_DATA_DIR` to relocate the owner-only cache that
keeps the random Session-id suffix stable for one Host session.

## Use the client

The standard-library client returns a `ClientResult` value on success and
failure:

```python
from integrations.agent_hooks.client import SidecarClient

health = SidecarClient().health()
if not health.ok:
    print(health.error)
```

## Measured round-trip time

Measured on 2026-07-26 on the development machine with embeddings disabled,
using 10 warm-up calls followed by 200 sequential health calls and 100 sequential
Prefetch calls against one warm Provider and an empty Bank:

| Route | Minimum | Median | p95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| `GET /health` | 0.564 ms | 0.688 ms | 0.946 ms | 1.155 ms |
| `POST /prefetch` | 1.088 ms | 1.243 ms | 1.511 ms | 1.645 ms |

These are socket-to-Sidecar measurements. They do not include starting the Hook
interpreter.

## Layout

- `client.py` — standard-library HTTP client over `AF_UNIX`
- `identity.py` — worktree-aware Session-id derivation and suffix cache
- `provider_cache.py` — warm per-session Provider LRU and `agent-hooks` profile
- `run_sidecar.py` — isolated, working-directory-independent Sidecar launcher
- `services/` — copyable systemd and launchd service-definition templates
- `sidecar.py` — Sidecar command, health route, and Prefetch route
- `transport.py` — shared limits, socket path, and environment overrides
- `user_prompt_submit.py` — `UserPromptSubmit` Injection Hook
- `tests/test_agent_hooks_sidecar.py` — real-process transport tests (Seam B)
- `tests/test_agent_hooks_prefetch.py` — real Sidecar and Provider tests (Seam B)
- `tests/test_agent_hooks_user_prompt_submit.py` — Hook subprocess tests (Seam A)
