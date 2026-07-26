---
title: "Sidecar skeleton, unix socket, health route"
labels: [ready-for-agent]
status: open
created: 2026-07-26
spec: docs/specs/2026-07-26-agent-hooks.md
parent: 0001-agent-hooks-automatic-memory.md
---

## What to build

A long-lived Sidecar process, started by hand, that answers a health request over
a unix domain socket — and the standard-library client every Hook and plugin
skill will later use to reach it.

Nothing memory-related happens yet. This ticket exists so that every later
ticket has a warm process, a transport and a client that already work, and so
that "is memory running right now" is answerable from the very first slice.

The socket lives directly under the user's home directory, not a runtime
directory: `AF_UNIX` paths are capped at 108 bytes, the runtime-directory
variable is empty in the Hook environment as the Hosts deliver it, and macOS has
no equivalent. Its path is overridable by environment variable — that override is
also how later tests point Hooks at a stub, so it is not optional polish.

File mode `0600` on the socket is the whole authentication story. No port, no
token, nothing else on the machine can reach it.

## Acceptance criteria

- [ ] Sidecar starts from the command line and serves on a unix socket under `$HOME`, path overridable by environment variable
- [ ] Socket is created mode `0600`; a stale socket file from a previous run is replaced cleanly rather than causing a bind failure
- [ ] A health request returns the integration's own version and the count of live sessions (zero for now)
- [ ] Client module is standard library only, reachable by both Hooks and skills, and surfaces connection failure as a value the caller can act on rather than an exception that escapes
- [ ] Round trip measured and recorded: single-digit milliseconds, consistent with the 0.85–1.65ms measured during design
- [ ] Sidecar shuts down cleanly on SIGTERM and removes its socket
- [ ] Test at the socket boundary (Seam B): real Sidecar started over a socket in `tmp_path`, health asserted from a client; no test patches an internal
- [ ] Every file added is new; no existing repository file is edited

## Blocked by

None — can start immediately.
