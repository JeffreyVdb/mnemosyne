# Agent Hooks managed-service contract

## Template and installer contract

The copyable service definitions are:

- `integrations/agent_hooks/services/mnemosyne-agent-hooks-sidecar.service.in`
- `integrations/agent_hooks/services/com.mnemosyne.agent-hooks-sidecar.plist.in`

The installer must replace `@MNEMOSYNE_PYTHON@` with an absolute interpreter
path and `@MNEMOSYNE_SIDECAR_LAUNCHER@` with the absolute installed path of
`run_sidecar.py`, rejecting non-absolute substitutions. Both definitions pass
`-I`, so Python ignores `PYTHONPATH` and does not prepend the working directory.
The launcher resolves the launcher target's real path before deriving and
prepending the repository root. This remains true when the launcher is invoked
through a symlink and does not depend on an editable install.

The systemd unit follows the existing Mnemosyne service style: `Type=simple`,
`Restart=on-failure`, `RestartSec=3`, and `WantedBy=default.target`. It uses the
same `%h/.config/mnemosyne/llm.env` path as the other units, but deliberately
prefixes it with `-`: the Sidecar can answer health without LLM configuration,
so an absent optional file must not prevent startup.

The launchd definition uses `RunAtLoad` and
`KeepAlive={SuccessfulExit: false}`, the launchd analogue of restarting only
after an unsuccessful exit. Installing and smoke-testing that definition belongs
to the macOS installer ticket.

## Lingering contract

The Linux installer must run:

```text
loginctl enable-linger "$USER"
```

when lingering is disabled. If it lacks authority, it must tell the operator to
run that exact command. A user unit under `default.target` otherwise stops at
logout.

## Request backlog rationale

`socket.SOMAXCONN` remains unchanged. A service manager restarts a failed process
but does not enlarge its listen backlog, so managed-service operation does not
alter the earlier 128-entry assessment. The first observed losses were above the
normal concurrent-agent workload, and no acceptance criterion calls for queue
tuning.

The service tests provide the reproducible verification for template parsing,
working-directory and symlink-safe imports, one-line failures, and real Sidecar
health. Machine-specific process IDs, temporary paths, timer timestamps, and
service-manager transcripts belong in the ticket completion record rather than
this durable contract.
