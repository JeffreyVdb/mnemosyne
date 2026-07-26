---
title: "Agent Hooks — automatic memory for Claude Code and Codex"
labels: [ready-for-agent]
status: doing
created: 2026-07-26
spec: docs/specs/2026-07-26-agent-hooks.md
---

Make Mnemosyne memory automatic on both Hosts instead of something an agent has
to remember to call.

Three Hook events — `SessionStart`, `UserPromptSubmit`, `Stop` — on Claude Code
and Codex. Injection and Capture are delegated to the Provider lifecycle methods
that already implement them (`prefetch`, `sync_turn`); a Sidecar behind a unix
socket holds initialized Providers warm so a Hook costs about a millisecond
instead of 1.6 seconds. The 36-tool MCP server is detached from both Hosts,
freeing roughly 6,100 tokens per session, with deliberate memory access moving to
three plugin skills. Capture gains the hygiene it currently lacks: pseudo-prompt
rejection, credential redaction, and a Bank that is no longer world-readable.

Every file added is new — the integration reaches the Provider only through
public seams, so rebasing this fork onto upstream cannot conflict with it.

**Full spec:** [`docs/specs/2026-07-26-agent-hooks.md`](../../docs/specs/2026-07-26-agent-hooks.md)

**Decisions:**
- [ADR 0001 — Hooks talk to an integration-owned sidecar](../../docs/adr/0001-hooks-talk-to-an-integration-owned-sidecar.md)
- [ADR 0002 — Detach the MCP server from both hosts](../../docs/adr/0002-detach-the-mcp-server-from-both-hosts.md)
- [ADR 0003 — Capture writes at global scope](../../docs/adr/0003-capture-writes-at-global-scope.md)

**Glossary:** [`CONTEXT.md`](../../CONTEXT.md)
