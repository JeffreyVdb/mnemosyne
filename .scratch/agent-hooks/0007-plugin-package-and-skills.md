---
title: "Claude Code plugin package and deliberate-memory skills"
labels: [ready-for-agent]
status: qa
created: 2026-07-26
spec: docs/specs/2026-07-26-agent-hooks.md
parent: 0001-agent-hooks-automatic-memory.md
---

## What to build

The integration as something Claude Code loads as a plugin, rather than a set of
scripts a developer points the Host at by hand — and the deliberate memory
operations that must exist before the 36-tool MCP server can be detached.

Three skills: remember, recall, forget. Each posts to a Sidecar route. Together
they cost roughly 140 tokens against the MCP schemas' 6,100, and they are what
makes "remember this", "what do you know about X" and "that's wrong, drop it"
still work once the tools are gone. Everything else administrative stays on the
CLI, which already covers it.

The plugin manifest supplies the plugin root and a persistent data directory as
environment variables, and registers the Hook events. The repository-root
marketplace manifest is the one file outside the integration's own directory that
this work adds — Claude Code needs it in order to find the plugin at all.

Worth settling while building: whether the plugin's development loop reloads Hook
script edits without a reinstall. The answer shapes how the next two tickets are
worked.

## Acceptance criteria

- [ ] Claude Code loads the integration as a plugin and the Hook events fire from the plugin, not from a hand-pointed path
- [ ] Remember, recall and forget skills each work end to end against the Sidecar with the MCP server not running
- [ ] Skills reuse the existing socket client rather than reimplementing transport
- [ ] Deliberate writes land in the Bank at global Scope, and a deliberate forget removes what it names
- [ ] Marketplace manifest added at the repository root; nothing else outside the integration's directory is edited beyond `docs/`
- [ ] Health check reports the integration's version, so what is actually running is answerable
- [ ] Development loop documented: whether Hook script edits are picked up without reinstalling, and what to do if not

## Blocked by

- `0005-session-start-brief.md` — the manifest registers whichever Hook events survive that ticket's decision.
- `0006-capture-hook-and-hygiene.md` — deliberate writes go through the same hygiene and Scope rules as Capture.
