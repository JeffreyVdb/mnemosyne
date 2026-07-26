---
title: "Capture — Stop Hook, capture route, turn hygiene and suppression"
labels: [ready-for-agent]
status: doing
created: 2026-07-26
spec: docs/specs/2026-07-26-agent-hooks.md
parent: 0001-agent-hooks-automatic-memory.md
---

## What to build

Every completed turn written to the Bank without being asked — and never at the
cost of the turn's latency, and never carrying a secret or a piece of machine
chatter.

Hygiene ships in the same slice as Capture rather than after it. Capture writes
at global Scope, permanently, so a slice that stores turns before it can redact
them would be a slice that leaks a credential into every future session by
design. The order is fixed: pseudo-prompt rejection, then redaction, then the
Provider's own `ignore_patterns` — all applied inside the Hook, before anything
crosses the socket.

Rejection targets Host-generated wrappers that are not user speech: task
notifications, system reminders, local command output, command-name markers.
About 14% of the current Bank — 493 rows — is exactly this, stored as if the
developer had typed it.

Redaction substitutes a labelled placeholder for recognised credential shapes and
leaves the rest of the turn intact. A turn containing one secret is worth
keeping.

The assistant's final message comes from the field both Hosts supply on the
turn-end event. No component in this integration reads a transcript file — both
Hosts document that format as unstable and Claude Code's documentation explicitly
directs Hooks to prefer the field.

Captured turns stay in the Bank as raw evidence. Consolidation needs them as
input, and a better extraction prompt can be re-applied later. Capture uses cheap
local entity extraction, not the LLM path, so a captured turn costs no API call.

Suppression lands here too: a way to turn Capture off for a session or a
directory, so sensitive work can happen without being remembered.

## Acceptance criteria

- [ ] Finishing a turn in Claude Code writes it to the Bank at global Scope, with Host, repository and Session id recorded as provenance
- [ ] The turn does not wait on Capture: the Hook posts and returns without waiting for a response, so it is prompt on Codex too, which parses the background field but does not implement it
- [ ] Assistant content is taken from the turn-end event field; nothing in the integration opens a transcript file
- [ ] Host-generated pseudo-prompts — task notifications, system reminders, local command output, command-name markers — never reach the Bank
- [ ] A prompt containing a credential shape is stored with a labelled placeholder and the rest of the turn intact
- [ ] Existing `ignore_patterns` are still honoured
- [ ] Raw turns remain in the Bank rather than being replaced by an extraction
- [ ] Capture survives the Sidecar restarting mid-turn without corrupting the session's memory
- [ ] Suppression switch silences Capture for a session or a directory, and Injection still works while suppressed
- [ ] Seam A: hygiene, both Hosts' payload shapes and exit-0-always asserted by running the Hook as a subprocess against a recording stub
- [ ] Seam B: rows asserted in a throwaway Bank under `tmp_path` — global Scope, no pseudo-prompts, concurrent Session ids independent, LRU eviction does not lose a write

## Blocked by

- `0004-injection-prompt-hook.md` — needs session identity, the warm Provider LRU and the prompt captured at submit time to pair with the assistant message.
