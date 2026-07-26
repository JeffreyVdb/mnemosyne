---
title: "One-shot cleanup of stored pseudo-prompts and credentials"
labels: [ready-for-agent]
status: open
created: 2026-07-26
spec: docs/specs/2026-07-26-agent-hooks.md
parent: 0001-agent-hooks-automatic-memory.md
---

## What to build

Hygiene applies to what is already stored, not only to what arrives next. The
Bank currently holds about 493 rows — roughly 14% of it — that are the Host's own
`<task-notification>` payloads saved as if they were user speech, and one real
OpenRouter key pasted while configuring the sleep model, stored permanently at
global Scope.

A one-shot command that finds both, reports what it would remove, and removes it
only on confirmation. A destructive pass over someone's memory should never be a
surprise.

It reuses the detectors written for Capture rather than growing a second, drifting
copy of what counts as a pseudo-prompt or a credential shape.

Scope is deliberately narrow: this is a targeted cleanup, not a Bank migration.
Existing rows keep whatever Session id they have — provenance is not
retrofitted.

## Acceptance criteria

- [ ] Command reports pseudo-prompt rows and credential-shaped rows it would act on, with counts, and changes nothing until confirmed
- [ ] Confirmed run removes the pseudo-prompt rows and the stored credential
- [ ] A row containing a credential alongside real content is redacted rather than deleted, matching Capture's behaviour
- [ ] Detectors are shared with Capture, not duplicated
- [ ] No other rows are touched; Session ids on surviving rows are unchanged
- [ ] Run against a throwaway Bank under `tmp_path` seeded with both row kinds, asserting the report before and the rows after
- [ ] Verified against a copy of the real Bank before being run against the real one

## Blocked by

- `0006-capture-hook-and-hygiene.md` — reuses its pseudo-prompt and credential detectors.
