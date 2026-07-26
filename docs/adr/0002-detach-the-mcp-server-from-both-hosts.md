---
status: accepted
---

# Detach the MCP server from both hosts

Mnemosyne's MCP server advertises 36 tools whose schemas total 24,271 characters —
roughly 6,100 tokens, and likely more once JSON's punctuation density is
accounted for. That is paid into the system prompt of every session whether a
memory tool is called or not, and in practice they rarely were. Once Hooks make
Injection and Capture automatic, the tools' remaining job is *deliberate* memory
work, which three plugin skills can do for about 140 tokens of description.
So the MCP server is removed from Claude Code's config, never added to Codex, and
`mnemosyne.service` is disabled — leaving one long-lived process (the Sidecar)
where there were previously two.

## Considered options

- **Keep MCP attached alongside hooks.** Nothing regresses and all 36 doors stay
  open, but the 6,100 tokens are spent every session for tools that were not
  being used.
- **A slimmed 3-tool MCP server.** ~1,200 tokens and tools stay first-class and
  model-discoverable, but it is a whole new server to build and keep alive — more
  divergence than the skills route, for a cost still ~9x the skills.
- **CLI only, no skills.** Zero always-on cost, but discoverability collapses to
  a line in `CLAUDE.md`, and soft instructions being skipped is precisely what
  motivated using Hooks in the first place.

## Consequences

The 30-odd tools beyond remember/recall/forget become CLI-only. That is
acceptable because they are administrative — `hygiene_audit`, `import`,
`validate`, `sync_*` — and are run deliberately, at a terminal, not mid-turn.

Other MCP clients (the VS Code extension, the Obsidian plugin, openclaw) lose
their server until it is re-enabled. Nothing on this machine uses them today.

The daily Consolidation timer is a separate oneshot unit and is unaffected.
