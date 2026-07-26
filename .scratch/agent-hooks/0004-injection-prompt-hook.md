---
title: "Injection — prefetch route and the UserPromptSubmit Hook"
labels: [ready-for-agent]
status: qa
created: 2026-07-26
spec: docs/specs/2026-07-26-agent-hooks.md
parent: 0001-agent-hooks-automatic-memory.md
---

## What to build

The first end-to-end memory: a developer types a prompt into Claude Code and
relevant memory is already in the model's context when it reads the turn. Nothing
was asked for.

The Hook reads the event JSON from stdin, extracts the prompt field, asks the
Sidecar for an Injection, and emits it as `hookSpecificOutput.additionalContext`.
It imports nothing but the standard library — that is what makes `sys.path`
shadowing impossible, because a Hook that never imports Mnemosyne cannot pick up
a different copy of it depending on the working directory.

The Sidecar answers from a warm Provider held in a small LRU keyed by Session id,
initialized with `default_scope="global"` and `agent_context="primary"`. That
second argument is load-bearing: Prefetch and Capture both return early for
contexts the Provider treats as skippable, so a wrong value here is a silent
no-op, not an error.

Session identity is derived here and cached per Host session: Host name,
repository, and a short random suffix. Repository is resolved through git's
common directory so that worktrees collapse onto their parent repository — this
machine runs several worktrees of the same repository concurrently.

Injection filtering is the Provider's job, not this integration's. Prefetch
already ranks candidates by

```
(score · 0.65 + topic_signal · 0.35 + importance · 0.05) · source_quality
```

with `source_quality` 0.0 for assistant transcript rows, 0.72 for
conversation-sourced rows and a further 0.68 for user-prefixed ones, and 1.12 for
distilled sources — so raw transcript rows are demoted by a ranking multiplier,
not excluded by an importance floor. (Transcribed from the shipped Provider
during design; an earlier reading of the code had this wrong.) At most, this
ticket registers a named profile that adjusts those knobs. It does not rank,
filter or truncate anything itself.

The Provider-contract guard test belongs here — the canary for the one realistic
upstream breakage.

## Acceptance criteria

- [ ] Claude Code pointed at the working tree injects recalled memory into a real prompt, visibly labelled as recalled memory
- [ ] Injection draws on distilled sources ahead of raw transcript rows, excludes assistant rows entirely, and collapses near-duplicates
- [ ] Memory formed in a different repository surfaces in this one
- [ ] Injection is capped in size and bounded by a Hook-level timeout below the Host's default; on timeout the turn proceeds with no memory
- [ ] Warm Injection completes in a fraction of a second, consistent with the 0.16s warm Prefetch measured during design
- [ ] Hook imports only the standard library and exits 0 on every path, including unhandled exceptions, a stub that errors, a stub that hangs, and no socket present at all
- [ ] A Hook that cannot reach the Sidecar writes exactly one line to stderr and produces no Injection
- [ ] Session id derivation covers the worktree case: two worktrees of one repository resolve to the same repository component
- [ ] Concurrent sessions are served from independent warm Providers without interfering
- [ ] Prompt-field extraction handles both Hosts' payload shapes, including their fallbacks
- [ ] Guard test asserts the Provider still exposes `initialize`, `prefetch` and `sync_turn` with the signatures the Sidecar calls
- [ ] Tests exercise the Hook as a subprocess against a recording stub (Seam A) and the Sidecar over a socket with `$HOME` and Bank redirected into `tmp_path` (Seam B)

## Blocked by

- `0002-sidecar-skeleton-and-health.md` — needs the transport and client.
