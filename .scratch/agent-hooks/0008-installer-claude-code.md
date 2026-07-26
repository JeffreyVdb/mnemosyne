---
title: "Installer and uninstaller — Claude Code, MCP detach, Bank permissions"
labels: [ready-for-agent]
status: open
created: 2026-07-26
spec: docs/specs/2026-07-26-agent-hooks.md
parent: 0001-agent-hooks-automatic-memory.md
---

## What to build

One command that sets up memory on a machine, shows every change before making
it, and can be run again safely. Setting up a second machine stops being an
exercise in reconstruction.

For Claude Code the installer: installs the plugin, installs the Sidecar service
definition from ticket 0003, removes the legacy bash Hooks so the old and new
paths cannot both fire and double-capture, removes the MCP entry from the Host
config and disables the MCP service, and restricts the Bank file and its
directory to the owner — 79MB of every prompt ever typed is currently
world-readable.

The token saving only materialises when the MCP entry is actually gone, so that
removal is part of installation rather than a follow-up instruction.

Every edit is previewed as a diff and consented to, every touched file gets a
timestamped backup, and the apply is atomic. Unrelated Hooks survive. Uninstall
is symmetric — the whole thing can be backed out.

Prior art worth reading first: pull requests 1 and 2 on this fork added a Claude
Code Hook installer in May 2026 — Hook scripts, settings fragment, verifier,
uninstaller, tests and CI. They are absent from `main` because the fork's history
was reset onto upstream, but they remain fetchable from the remote's
pull-request refs, and the diff-preview-backup-atomic-apply shape they used is
the shape this ticket asks for. PMB is the reference for marker-based idempotent
config editing.

## Acceptance criteria

- [ ] One command installs the plugin, the Sidecar service and the file permissions on a clean machine
- [ ] Every config-file change is shown as a diff and consented to before it is applied
- [ ] Every touched file gets a timestamped backup that can be restored by hand
- [ ] Legacy bash Hooks are removed; old and new paths cannot both fire
- [ ] MCP entry is removed from Claude Code's config and the MCP service is disabled; a session started afterwards no longer carries the 36 tool schemas
- [ ] Bank file and directory are owner-only afterwards
- [ ] Unrelated Claude Code Hooks survive untouched
- [ ] Consolidation timer is left alone
- [ ] A second run changes nothing
- [ ] Uninstaller restores the starting state
- [ ] Seam C: installer run against a fake home under `tmp_path`, pre-populated with fixtures mirroring this machine — a Claude Code settings file containing both unrelated Hooks and the legacy bash memory Hooks, and a config containing the MCP entry
- [ ] Promotion documented: repository changes to the Mnemosyne package are promoted by reinstalling the tool, so a half-finished working tree cannot break memory or the Sidecar

## Blocked by

- `0003-sidecar-as-managed-service.md` — the installer installs the service definitions.
- `0007-plugin-package-and-skills.md` — the installer installs the plugin.
