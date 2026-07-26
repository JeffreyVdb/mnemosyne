# Agent Hooks managed-service verification

Verified on 2026-07-26 from the `feat/agent-hooks-service` worktree.

## Template and installer contract

The copyable service definitions are:

- `integrations/agent_hooks/services/mnemosyne-agent-hooks-sidecar.service.in`
- `integrations/agent_hooks/services/com.mnemosyne.agent-hooks-sidecar.plist.in`

The installer must replace `@MNEMOSYNE_PYTHON@` with an absolute interpreter
path and `@MNEMOSYNE_SIDECAR_LAUNCHER@` with the absolute installed path of
`run_sidecar.py`, rejecting non-absolute substitutions. Both definitions pass
`-I`, so Python ignores `PYTHONPATH` and does not prepend the working directory.
The launcher derives its trusted integration root from its own real path. This
does not depend on the editable install used in the development worktree.

The systemd unit follows the existing Mnemosyne house style: `Type=simple`,
`Restart=on-failure`, `RestartSec=3`, and `WantedBy=default.target`. It uses the
same `%h/.config/mnemosyne/llm.env` path, but deliberately prefixes it with `-`:
the Sidecar can answer health without LLM configuration, so an absent optional
file must not prevent startup.

On this machine, lingering was already enabled:

```text
$ loginctl show-user "$(id -un)"
UID=1000
GID=1000
Name=jeff
...
Linger=yes
```

The installer must run `loginctl enable-linger "$USER"` when needed, or tell the
operator to run that exact command if it lacks authority.

The rendered Linux verification unit passed:

```text
$ systemd-analyze verify ~/.config/systemd/user/mnemosyne-agent-hooks-sidecar-verification.service
[no output; exit 0]
```

`systemd-analyze --user verify` could not initialize its manager in this shell
(`Failed to lookup RuntimeDirectory path: No such device or address`), so the
successful manager-independent verification above was used. The content tests
parse the launchd template with Python's `plistlib`; no launchd daemon is needed.

## Working-directory-independent imports

The Seam B test started two real Sidecar processes with the absolute interpreter,
`-I`, and the absolute launcher. One working directory was the repository; the
other contained a hostile `integrations/agent_hooks/sidecar.py` that raises
`HOSTILE SIDECAR RAN`. Both answered health and printed the same provenance:

```text
/home/jeff/.herdr/worktrees/mnemosyne/feat-agent-hooks-service:
/home/jeff/.herdr/worktrees/mnemosyne/feat-agent-hooks-service/integrations/agent_hooks/sidecar.py

/tmp/pytest-of-jeff/pytest-1084/test_isolated_launcher_ignores0/hostile:
/home/jeff/.herdr/worktrees/mnemosyne/feat-agent-hooks-service/integrations/agent_hooks/sidecar.py

1 passed in 3.41s
```

The hostile file was not imported. This demonstration does not depend on the
editable-install finder: isolated mode discards `PYTHONPATH`, and the launcher
inserts the root derived from its own file.

## Departed peers and startup failures

Ten aborted Prefetch requests were sent to the pre-change Sidecar from commit
`bcf80ae`, then to this change. The same real-process probe produced:

```text
before module=/tmp/mnemosyne-sidecar-before.Br2xHu/integrations/agent_hooks/sidecar.py
before traceback_markers=10 stderr_lines=350
after module=/home/jeff/.herdr/worktrees/mnemosyne/feat-agent-hooks-service/integrations/agent_hooks/sidecar.py
after traceback_markers=0 stderr_lines=10
```

`_SidecarServer.handle_error` now reduces each request failure to at most one
line. A separate real-process test supplies a Provider dependency whose public
`register_profile` refuses startup. It exits 1 with exactly:

```text
Sidecar failed to start: provider registration refused
```

There is no traceback. `ProviderLRU()` is inside the existing startup guard, and
the guarded `finally` still shuts it down only when construction completed.

`socket.SOMAXCONN` remains unchanged. A service manager restarts a failed process
but does not enlarge its listen backlog, so managed-service operation does not
alter the earlier 128-entry assessment. The first observed losses were above the
normal concurrent-agent workload, and no acceptance criterion calls for queue
tuning; changing it here would be unrelated scope.

## Linux restart proof

A distinct real user unit,
`mnemosyne-agent-hooks-sidecar-verification.service`, used the rendered absolute
paths plus a throwaway socket under `/tmp`. It was enabled and started, answered
health, and reported:

```text
{"status":"ok","version":"0.1","live_sessions":0}
MainPID=350762
NRestarts=0
ActiveState=active
SubState=running
```

The main process was then killed only through the service manager:

```text
$ systemctl --user kill --signal=KILL --kill-whom=main \
    mnemosyne-agent-hooks-sidecar-verification.service
```

Without manually starting another process, health answered again:

```text
{"status":"ok","version":"0.1","live_sessions":0}
MainPID=350892
NRestarts=1
ActiveState=active
SubState=running
```

The journal attributed the recovery to systemd:

```text
Failed with result 'signal'.
Scheduled restart job, restart counter is at 1.
Started mnemosyne-agent-hooks-sidecar-verification.service.
```

After verification, `systemctl --user disable --now` left the exact final state:

```text
mnemosyne-agent-hooks-sidecar-verification.service: disabled, inactive
/home/jeff/.config/systemd/user/mnemosyne-agent-hooks-sidecar-verification.service: exists
/tmp/mnemosyne-agent-hooks-sidecar-verification.sock: absent
```

Enabling the real service belongs to the installer ticket.

## Consolidation before and after

Before the proof:

```text
mnemosyne-sleep.service   static
mnemosyne.service         enabled
mnemosyne-sleep.timer     enabled

NEXT                         LAST                               UNIT
Mon 2026-07-27 12:30:00 CEST Sun 2026-07-26 12:30:16 CEST      mnemosyne-sleep.timer
```

After the proof:

```text
mnemosyne-agent-hooks-sidecar-verification.service disabled
mnemosyne-sleep.service                            static
mnemosyne.service                                  enabled
mnemosyne-sleep.timer                              enabled

NEXT                         LAST                               UNIT
Mon 2026-07-27 12:30:00 CEST Sun 2026-07-26 12:30:16 CEST      mnemosyne-sleep.timer
```

The existing MCP service, Consolidation service, and Consolidation timer were not
stopped, started, disabled, enabled, edited, or masked.

## macOS result

The target was active on the private network:

```text
100.97.162.82  jeffrey-vandenborne-mbp  JeffreyVdb@  macOS  active; direct ...
```

Read-only SSH was attempted first, before any launchd action:

```text
$ ssh -o BatchMode=yes -o ConnectTimeout=10 jeffrey-vandenborne-mbp '...read-only checks...'
ssh: connect to host jeffrey-vandenborne-mbp port 22: Connection refused

$ tailscale ssh jeff@100.97.162.82 '...read-only checks...'
Connection closed by UNKNOWN port 65535
```

No remote command ran. Therefore whether Mnemosyne or this repository is present
on that Mac remains unanswered, launchd was not smoke-tested, and AC7 is not met
in this environment. Nothing was installed, modified, loaded, or unloaded on the
Mac, and its Bank was not touched.

## Acceptance criteria

1. Met: the systemd user template has absolute-path tokens, `-I`, and restart on
   failure.
2. Met: `Linger=yes` is confirmed, and the installer/operator contract is
   explicit.
3. Met as a definition: the launchd template has the same path, isolation, and
   unsuccessful-exit restart guarantees.
4. Met: a killed Sidecar was restarted by systemd and answered health again.
5. Met: two real processes reported identical trusted import provenance and
   ignored a hostile working-directory package.
6. Met: both definitions are copyable template files.
7. Not met: the active Mac refused both SSH paths, so no smoke test or
   installation-presence check was possible.
8. Met: the existing Consolidation timer was unchanged.

Deferred as directed: CI Ruff wiring, the package version, `CHANGELOG.md`, and
`docs/integrations/README.md` remain for the installer ticket.
