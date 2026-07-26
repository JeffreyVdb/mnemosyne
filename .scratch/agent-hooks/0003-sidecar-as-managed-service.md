---
title: "Sidecar as a managed service"
labels: [ready-for-agent]
status: open
created: 2026-07-26
spec: docs/specs/2026-07-26-agent-hooks.md
parent: 0001-agent-hooks-automatic-memory.md
---

## What to build

The Sidecar as something the machine keeps alive, rather than something the
operator remembers to start. A transient crash heals without anyone noticing, and
scheduled memory work does not depend on being logged in.

Two service definitions: a systemd user unit on Linux with lingering enabled so
it survives logout, and a launchd definition on macOS. Both restart on failure.
The installer will consume these later; here they are installed and verified by
hand.

The Sidecar is launched with an absolute interpreter and with `sys.path`
sanitisation enabled. This is the point of the ticket as much as the restart
policy is: several agents run concurrently in this environment and one of them is
frequently working inside this repository, so an import that resolves relative to
a working directory would silently swap the code that runs.

macOS is verified by hand over the existing private network — the divergences
here are service-manager and path shaped, not logic shaped.

## Acceptance criteria

- [ ] systemd user unit starts the Sidecar with an absolute interpreter and `sys.path` sanitisation, and restarts it on failure
- [ ] Lingering is enabled (or the operator is told to enable it) so the Sidecar survives logout
- [ ] launchd definition provides the same start, restart and path guarantees on macOS
- [ ] Killing the Sidecar results in it being restarted and answering health again, without manual intervention
- [ ] Starting the Sidecar from inside this repository's working directory yields the same imports as starting it from anywhere else — demonstrated, not assumed
- [ ] Service definitions are files the installer can copy, not instructions in a README
- [ ] macOS behaviour smoke-tested by hand and the result recorded; the spec's open question about whether that machine has Mnemosyne installed at all is answered
- [ ] The existing Consolidation timer is untouched

## Blocked by

- `0002-sidecar-skeleton-and-health.md` — there must be a process worth keeping alive.
