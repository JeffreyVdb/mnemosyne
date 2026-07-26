---
title: "Installer — Codex, trust-preserving Hook wiring"
labels: [ready-for-agent]
status: open
created: 2026-07-26
spec: docs/specs/2026-07-26-agent-hooks.md
parent: 0001-agent-hooks-automatic-memory.md
---

## What to build

The same three Hook events on Codex, installed by the same command, so memory
behaves identically whichever agent is reached for — and so work done in Codex
stops being invisible. Codex has no Mnemosyne wiring at all today.

Codex gets entries appended to its user-level Hook configuration file. Appending
is mandatory, not stylistic: Codex records Hook trust against a positional key
derived from file, event and index within the event, so inserting a group ahead
of an existing one renumbers it and silently revokes its trust. Unrelated
automation would stop running and nothing would say why. The tests assert exactly
this.

Codex sets the same plugin-root and data-directory environment variables Claude
Code does, so the Hooks themselves need no Codex-specific branch beyond payload
quirks, which the integration absorbs. Codex parses the background-execution
field but does not implement it — already handled, since the capture Hook posts
without waiting.

The operator is told that Codex needs new Hooks approved, so nobody is left
wondering why memory is silent after installing.

## Acceptance criteria

- [ ] The install command wires all three Hook events on Codex; Injection and Capture work there end to end
- [ ] Hook groups are appended only; existing positional trust keys are unchanged, asserted by test
- [ ] Two unrelated pre-existing session-start groups and their recorded trust hashes survive installation
- [ ] Host payload quirks are absorbed by the integration — no Codex special case a developer has to remember
- [ ] Operator is told, at install time, that Codex requires Hook approval before memory becomes active
- [ ] Turn capture returns promptly on Codex despite the background field being ignored there
- [ ] Uninstaller removes the Codex entries and restores the starting state, including trust keys
- [ ] Seam C fixtures mirror this machine: a Codex Hook file already containing two unrelated session-start groups and a config carrying their trust hashes
- [ ] A memory formed in Codex is injected in Claude Code and vice versa

## Blocked by

- `0008-installer-claude-code.md` — shares the diff-preview, backup, atomic-apply and uninstall machinery.
