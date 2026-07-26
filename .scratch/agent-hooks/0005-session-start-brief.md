---
title: "SessionStart orienting brief"
labels: [ready-for-agent]
status: doing
created: 2026-07-26
spec: docs/specs/2026-07-26-agent-hooks.md
parent: 0001-agent-hooks-automatic-memory.md
---

## What to build

A short brief in the agent's context before the first prompt of a session, so
that turn one is not blind.

This ticket carries a real decision, not just an implementation. The spec's open
question is whether the brief is worth injecting at all, or whether it is merely
Prefetch with the repository name as the query — in which case it is noise
occupying the top of every session. Build it, look at what it actually produces
against the real Bank, and decide. Shipping nothing is an acceptable outcome
here, provided the finding is written down.

Whatever ships, it is the third Hook entry point and it obeys the same posture as
the other two: standard library only, exit 0 on every path, fail open.

## Acceptance criteria

- [ ] Session start Hook emits an orienting brief as recalled-memory context, or is deliberately dropped with the reasoning recorded in the spec's notes
- [ ] If shipped: the brief's output was inspected against the real Bank and is materially different from repository-name Prefetch
- [ ] If shipped: same failure posture as the other Hooks — exit 0 on every path, stdlib only, timeout-bounded, one stderr line when the Sidecar is unreachable
- [ ] Seam A subprocess tests cover the event payload shapes both Hosts send for session start
- [ ] Identity Injection being empty is expected and not treated as a bug: it is strictly Session-id scoped and there are currently zero identity rows, so it stays empty until Capture's identity-signal extraction has produced some

## Blocked by

- `0004-injection-prompt-hook.md` — needs session identity, the warm Provider LRU and the injection route.
