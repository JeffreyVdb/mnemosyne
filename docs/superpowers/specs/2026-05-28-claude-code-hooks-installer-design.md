# Claude Code Hooks Installer — Design

**Date:** 2026-05-28
**Status:** Approved (brainstorming sections 1–5)
**Author:** Jeffrey Vandenborne
**Audience:** Implementer of the install/uninstall/verify scripts and supporting docs

## Context

A prior session installed Mnemosyne hooks (`UserPromptSubmit`, `Stop`) into Claude Code on the author's laptop. The integration works end-to-end (auto-store + auto-recall + inject, shared SQLite bank with Hermes). Hook scripts, ignore patterns, and the `~/.claude/settings.json` edits live only on that one machine.

This spec turns that ad-hoc install into a repeatable, public-facing installation procedure shipped in the Mnemosyne repository so colleagues at Kiswe and upstream community users can adopt it.

### Reference materials

- Handoff doc: `/tmp/handoff-claude-code-hooks-2026-05-28.md`
- Original plan: `/tmp/mnemosyne-claude-code-plan.md`
- Reference implementation: [`vectorize-io/hindsight` / `hindsight-integrations/claude-code/`](https://github.com/vectorize-io/hindsight/blob/main/hindsight-integrations/claude-code/README.md) — ships as a proper Claude plugin with marketplace catalog; informs the future-plugin shape
- Claude Code plugin marketplace docs: https://code.claude.com/docs/en/plugin-marketplaces

## Locked decisions

| # | Decision | Source |
|---|---|---|
| 1 | Audience: colleagues + upstream community (public-friendly) | User answer Q1 |
| 2 | Distribution: install script primary, plugin-ready layout, plugin deferred to v2 | User answer Q2 (Other) |
| 3 | v1 hook scope: parity with shipped (`UserPromptSubmit` + `Stop` only); SessionStart/SubagentStop deferred | User answer Q3 (option 1) |
| 4 | Default DB path: `~/.hermes/mnemosyne/data/mnemosyne.db` (shared with Hermes) | User answer Q3 (option 1) |
| 5 | `settings.json` patcher must: (a) show diff before mutating, (b) print backup path | User direct instruction |
| 6 | `--dry-run` and `--yes` flags required | User direct instruction |
| 7 | Default behavior: interactive confirmation before writing | User direct instruction |

## Non-goals

- A Claude Code plugin (`.claude-plugin/marketplace.json`) in v1. Layout is forward-compatible; actual plugin packaging is a separate later effort.
- SessionStart hook (warm-context inject on resume).
- SubagentStop hook (capture inner subagent turns).
- LLM fact extraction (`extract=True`) via auxiliary LLM client.
- Per-project bank isolation. Shared bank only in v1.
- Multi-channel support (Telegram/Discord/Slack — Hindsight's territory, not ours).
- Live Claude Code session round-trip in CI (cannot run `claude` headless).
- Modifying the `mnemosyne` Python package, CLI, or DB schema. Pure host-side integration.

## Architecture

### Repo layout (Section 1)

```
mnemosyne/
├── claude_code_hooks/                # new — peer of hermes_plugin/
│   ├── README.md                     # one paragraph + pointer to docs/claude-code-integration.md
│   ├── hooks/
│   │   ├── mnemosyne-user-prompt     # ported from ~/.claude/hooks/, CLI path de-hardcoded
│   │   └── mnemosyne-stop            # ported from ~/.claude/hooks/, CLI path de-hardcoded
│   ├── mnemosyne-ignore-patterns     # ERE patterns, ported verbatim
│   └── settings.fragment.json        # canonical hook-entry shape, source of truth for patcher
├── scripts/
│   ├── install-claude-code-hooks.sh    # new — orchestrator
│   ├── uninstall-claude-code-hooks.sh  # new — symmetric reversal
│   └── verify-claude-code-hooks.sh     # new — standalone post-install check
├── tests/
│   └── install/
│       ├── test_install_claude_code_hooks.bats  # bats test suite
│       └── fixtures/                            # sample settings.json files
├── docs/
│   └── claude-code-integration.md    # new — install/upgrade/uninstall/troubleshoot
└── .github/workflows/
    └── claude-code-hooks.yml         # new — macos + ubuntu matrix
```

Rationale: `claude_code_hooks/` mirrors the existing `hermes_plugin/` shape, so the repo reads as "Mnemosyne + per-host integrations." `settings.fragment.json` is the single source of truth for the hook entries — patcher reads it, future plugin migration inlines it into `plugin.json`. Hook scripts ship verbatim except the hardcoded CLI path is replaced with a runtime resolver.

### Component responsibilities

| Component | Responsibility |
|---|---|
| `claude_code_hooks/hooks/mnemosyne-user-prompt` | Read event JSON from stdin, store user prompt to Mnemosyne, recall relevant memories, emit `hookSpecificOutput.additionalContext` JSON on stdout. Fail-open. |
| `claude_code_hooks/hooks/mnemosyne-stop` | Read event JSON from stdin, parse transcript JSONL for last assistant message, store as `[ASSISTANT]` row. Fail-open. |
| `claude_code_hooks/mnemosyne-ignore-patterns` | ERE patterns, one per line. Mirrors Hermes `_should_filter` semantics. |
| `claude_code_hooks/settings.fragment.json` | Exact JSON to be merged into `~/.claude/settings.json`. Canonical shape. |
| `scripts/install-claude-code-hooks.sh` | Preflight → materialize hook files → build target settings.json in tmp → diff → confirm → backup → commit → smoke. |
| `scripts/uninstall-claude-code-hooks.sh` | Same flow, reversed. Optional `--purge-files`. Never touches DB. |
| `scripts/verify-claude-code-hooks.sh` | Five-step verification block from handoff doc. Standalone, re-runnable. |
| `docs/claude-code-integration.md` | User-facing guide. |

## settings.json patcher (Section 2 — highest risk)

Single-file orchestrator in bash + `jq`. No Python install dependency. Idempotent. Fail-closed on any ambiguity. Backup-first, patch-second, verify-third, prompt-fourth, commit-fifth.

### Flow

1. **Preflight** — any failure aborts before any mutation, exit code ≠ 0.
   - Require `jq`, `bash` (3.2+ acceptable for macOS default).
   - Require `mnemosyne` CLI on `PATH` or via `--mnemosyne-bin=<path>`. Smoke test: `mnemosyne --help` exits 0 within 5s.
   - Require `~/.claude/` writable. Create if absent (not an error).
   - Require hook source files readable inside repo.

2. **Materialize hook artifacts** — reversible by uninstall.
   - Copy `claude_code_hooks/hooks/mnemosyne-user-prompt` → `~/.claude/hooks/`.
   - Copy `claude_code_hooks/hooks/mnemosyne-stop` → `~/.claude/hooks/`.
   - Copy `claude_code_hooks/mnemosyne-ignore-patterns` → `~/.claude/`.
   - `chmod +x` both hook scripts.
   - If destination exists and content differs from source: rename existing → `.bak.<unix-ts>` first, log the path.
   - `~/.claude/mnemosyne-ignore-patterns`: if absent, copy default. If present, leave alone and print `Existing ignore-patterns kept. Repo default at <path> for diffing.` `--reset-ignore-patterns` overrides (backup-then-replace).

3. **Build target settings.json** — in a tmpfile, never mutates source yet.
   - If `~/.claude/settings.json` absent: seed tmp with `{"hooks":{}}`.
   - Else: copy source → tmp.
   - Read `claude_code_hooks/settings.fragment.json`.
   - `jq` merge: for each event in fragment (`UserPromptSubmit`, `Stop`):
     - If matcher+command pair already present at `.hooks[$event][*].hooks[*]`: idempotent — skip, log `already installed`.
     - Else: append.
   - Validate result parses as JSON and has expected shape (2 entries per event).

4. **Diff + confirm** — user-visible, gated.
   - Backup name: `~/.claude/settings.json.bak.<unix-ts>`.
   - Compute: `diff -u <source> <tmp>`. Auto-use `colordiff` if on PATH and stdout is a tty.
   - Print to stderr:
     ```
     ┌──────────────────────────────────────────────────────────┐
     │ The following changes will be made to ~/.claude/settings.json
     │ Backup will be saved to: ~/.claude/settings.json.bak.<ts>
     └──────────────────────────────────────────────────────────┘
     <unified diff>
     ```
   - If `--yes` absent and stdin is tty: prompt `Apply? [y/N]`. Default = No. Abort cleanly otherwise.
   - If `--yes` present: skip prompt.
   - If stdin is not a tty AND `--yes` is absent: refuse with an explicit error (`non-tty run requires --yes or --dry-run`). Never silently proceed.
   - If `--dry-run` present: print diff, exit 0, never write anything to disk (assert mtime of source unchanged after run). `--dry-run` takes precedence over `--yes` when both are passed.

5. **Commit** — atomic, rollback baked in.
   - `cp ~/.claude/settings.json ~/.claude/settings.json.bak.<ts>` (only if source existed).
   - `mv tmp ~/.claude/settings.json` (atomic on same filesystem).
   - `jq` sanity-check committed file; if it fails, restore from backup and exit non-zero.
   - Print backup path and exact rollback command:
     ```
     Backup: ~/.claude/settings.json.bak.<ts>
     Rollback: mv ~/.claude/settings.json.bak.<ts> ~/.claude/settings.json
     ```

6. **Smoke test** — non-fatal, informational.
   - Synthesize fake event JSON, pipe through `~/.claude/hooks/mnemosyne-user-prompt`.
   - Expect exit 0 + valid JSON on stdout.
   - On failure: WARN, do not auto-rollback (hook bug is not a settings.json bug).

### Rules

- **Idempotency.** Fragment matcher uses exact string match on the `command` field of each inner `hooks[]` entry. Re-running install on an already-installed system prints `already installed (matched entries: 2)`, zero diff, exits 0 without prompting.
- **Edge case — hand-modified entries.** If an entry exists with the same `command` but a different surrounding shape (e.g., different `timeout` or `type`), the patcher refuses to "fix" it. Prints WARN with the offending entry, exits with code 2. `--force-replace` overrides.
- **Backup naming.** `~/.claude/settings.json.bak.<unix-epoch>`. Multiple installs accumulate distinct backups. Uninstall reverses the most recent or a user-named backup via `--restore=<path>`.

### Why bash + jq

`jq` is already standard in the Hindsight/Claude Code ecosystem. Bash matches existing `scripts/install.sh` convention. No venv decisions. Same code runs in CI smoke.

## Preflight, CLI prereq, docs (Section 3)

### Preflight UX

```
[1/5] Bash 3.2+ ........................ ok (3.2.57)
[2/5] jq ............................... ok (1.7.1)
[3/5] mnemosyne CLI .................... ok (/Users/x/.local/bin/mnemosyne, v3.1.0)
[4/5] ~/.claude/ writable .............. ok
[5/5] Hook source files in repo ........ ok
```

Failures print fix commands:
- `jq` missing → `Install with: brew install jq  # or: apt install jq`
- `mnemosyne` missing → `Install with: pip install mnemosyne-memory  # then re-run`. Honors `--mnemosyne-bin=<path>` for pipx/venv installs.

### Version check

`mnemosyne --version` parsed and compared against `MIN_MNEMOSYNE_VERSION=3.1.0` baked into install script. Below minimum: abort with upgrade command. `--version` not implemented on older builds: warn, proceed.

### Hook-script parameterization

The two scripts on the author's disk hardcode `~/.local/bin/mnemosyne`. Repo versions resolve the CLI at runtime, in this order, inside the hook:

1. `$MNEMOSYNE_BIN` env var if set.
2. `command -v mnemosyne` (PATH lookup).
3. Fallback list: `~/.local/bin/mnemosyne`, `~/.local/venvs/mnemosyne/bin/mnemosyne`, `/usr/local/bin/mnemosyne`.
4. None found → fail-open (write to `~/.claude/mnemosyne-hook.log`, exit 0). Turn must never break.

Install script writes the resolved path it found at install time into a `# Resolved at install: <path>` comment header for debugging. Runtime still uses the resolution order above.

### Ignore-patterns deployment

Per-user policy, not per-version:
- Absent → copy default from repo verbatim.
- Present → leave alone, point at repo default for diffing.
- `--reset-ignore-patterns` → backup + replace.

### DB location handling

Default `~/.hermes/mnemosyne/data/mnemosyne.db`. Install script does NOT create the directory or DB file — `mnemosyne` CLI does it lazily on first write. Install log:

```
DB will be created on first hook run at: ~/.hermes/mnemosyne/data/mnemosyne.db
Override with: export MNEMOSYNE_DB_PATH=/path/to/db.sqlite  (add to your shell rc)
```

### Docs

New file `docs/claude-code-integration.md`. Sections:
1. What this is (1 paragraph + diagram of `UserPromptSubmit → recall → inject → Stop → store` loop).
2. Prerequisites (`mnemosyne` CLI, `jq`, Claude Code).
3. Install (`./scripts/install-claude-code-hooks.sh [--dry-run] [--yes]`).
4. What gets modified (exact paths + sample diff).
5. Verifying it works (`./scripts/verify-claude-code-hooks.sh`).
6. Uninstall.
7. Hermes coupling note (cross-agent recall via shared DB).
8. Limitations (per handoff: no SessionStart, no SubagentStop, no identity signals, bash 3.2 + missing `timeout`/`gtimeout` edge case, first-call cold start ~4s).
9. Troubleshooting (hook log location, common failures).

`README.md` Hermes-Plugin section gets an adjacent "Claude Code Hooks" subsection — one line + link to the doc.

`claude_code_hooks/README.md` is a thin pointer (paragraph + link) so the directory is self-describing on GitHub but content isn't duplicated.

## Testing (Section 4)

### Three layers

**1. Unit (bats, CI)** — `tests/install/test_install_claude_code_hooks.bats`
- jq-merge logic against fixtures in `tests/install/fixtures/`:
  - Fresh install (no settings.json) → expected shape.
  - Idempotent re-install → zero diff.
  - Pre-existing unrelated hooks preserved.
  - Hand-modified same-command-different-timeout → exit code 2.
  - Backup file created with `.bak.<ts>` name.
- Preflight: mock missing `jq`, missing `mnemosyne`, unwritable `~/.claude/`.
- `--dry-run` prints diff, never mutates filesystem (assert mtime unchanged).
- `--yes` skips prompt.
- Hook CLI-resolution: shim `mnemosyne` in tmpdir, assert hook finds it via `PATH` / `MNEMOSYNE_BIN` / fallback list.

Tool choice: `bats-core`. Matches bash idiom, ships via brew/apt, keeps install-script tests separate from Python pytest world.

**2. Smoke (manual, documented in `CONTRIBUTING.md`)**
- Clean macOS + clean Linux VM: full install → fake event JSON → recall → uninstall → diff clean.
- Bash 3.2 (macOS default) and bash 5 (Homebrew) both pass.

**3. CI (GitHub Actions)** — `.github/workflows/claude-code-hooks.yml`
- Matrix: `macos-latest`, `ubuntu-latest`.
- Install `jq`, install `mnemosyne` from PyPI (or local wheel).
- Run install `--dry-run` → assert exit 0 + diff non-empty.
- Run install `--yes` → assert exit 0.
- Re-run install `--yes` → assert idempotent (zero diff).
- Run `verify-claude-code-hooks.sh` → assert all five checks pass.
- Run uninstall `--yes` → assert settings.json restored to baseline.

### Out of scope for tests

- Live Claude Code session round-trip (cannot run `claude` headless in CI).
- Cross-agent recall with Hermes (separate repo).
- Cold-start latency / 5s timeout edge case (timing-dependent, manual smoke only).

## Plugin migration path (Section 5 — forward-compat)

The Section 1 layout makes a future plugin migration mechanical:

```
mnemosyne/
├── claude_code_hooks/                # unchanged
├── .claude-plugin/                   # added in v2
│   ├── marketplace.json
│   └── plugin.json                   # references claude_code_hooks/hooks/* via ${CLAUDE_PLUGIN_ROOT}
```

Sketch (locked when migration happens):

```json
{
  "name": "mnemosyne-memory",
  "version": "0.1.0",
  "hooks": {
    "UserPromptSubmit": [{
      "matcher": ".*",
      "hooks": [{"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/claude_code_hooks/hooks/mnemosyne-user-prompt", "timeout": 10}]
    }],
    "Stop": [{
      "matcher": ".*",
      "hooks": [{"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/claude_code_hooks/hooks/mnemosyne-stop", "timeout": 10}]
    }]
  }
}
```

### Migration deltas

- Plugin install bypasses `~/.claude/settings.json` mutation entirely — Claude Code wires hook entries via plugin runtime.
- `~/.claude/mnemosyne-ignore-patterns` still per-user policy → plugin ships default; persistent user override under `${CLAUDE_PLUGIN_DATA}`.
- Hook CLI-resolution logic unchanged.
- Marketplace install via `AxDSan/mnemosyne` (`git-subdir` source pointing at `.claude-plugin/` subdir, or full-repo source — decided at migration time).

### Coexistence

Install-script approach + future plugin can coexist briefly on a user's machine during migration. Both would register the same hook entries; without intervention, hooks would fire twice. Uninstall script is the prerequisite to switching modes — documented as such in the migration guide when the plugin ships.

## Risks and open questions

| # | Risk | Mitigation |
|---|---|---|
| 1 | `jq` not installed on target host | Preflight aborts before any mutation, with the exact install command for the host's package manager |
| 2 | User has hand-modified hook entries with same command but different shape | Patcher refuses to overwrite; exit 2 with `--force-replace` escape hatch |
| 3 | `mnemosyne` CLI moves on user's host between install and runtime | Hooks resolve CLI at runtime, not install time; install-time path is just a comment for debugging |
| 4 | Two Claude Code sessions writing simultaneously to shared SQLite | Mnemosyne uses SQLite WAL; not stress-tested in v1, flagged as known limitation in docs |
| 5 | Hook scripts fail silently in production | `~/.claude/mnemosyne-hook.log` always-on; `verify-claude-code-hooks.sh` produces actionable diagnostic |
| 6 | bash 3.2 + missing both `timeout` and `gtimeout` | Degrades to fail-open silently; documented as known limitation; Homebrew install covers macOS in practice |
| 7 | First-call cold start ~4s exceeds 5s hook timeout under load | Documented known limitation; first-prompt-after-idle may drop silently. v2 could add a SessionStart warm-up |

## Acceptance criteria

1. Fresh-clone colleague on macOS or Ubuntu can run `./scripts/install-claude-code-hooks.sh` and end up with a working integration matching what is on the author's machine today.
2. `--dry-run` prints a diff and exits 0 without writing anything.
3. Default interactive run prints diff + backup-target line, prompts, defaults to No.
4. `--yes` skips prompt; safe for CI.
5. Re-running install on an already-installed system is a no-op (zero diff, exit 0).
6. `verify-claude-code-hooks.sh` returns exit 0 on a healthy install.
7. `uninstall-claude-code-hooks.sh` reverses `install` cleanly — `settings.json` matches the pre-install baseline.
8. CI matrix (macos-latest, ubuntu-latest) is green.
9. `docs/claude-code-integration.md` covers install, verify, uninstall, troubleshoot, limitations.

## Implementation order suggested for the plan

1. Port hook scripts into `claude_code_hooks/hooks/` (de-hardcode CLI path).
2. Port ignore-patterns into `claude_code_hooks/`.
3. Write `claude_code_hooks/settings.fragment.json`.
4. Write `install-claude-code-hooks.sh` against bats fixtures.
5. Write `uninstall-claude-code-hooks.sh`.
6. Write `verify-claude-code-hooks.sh`.
7. Write bats test suite + fixtures.
8. Write `docs/claude-code-integration.md` + `claude_code_hooks/README.md` + `README.md` link.
9. Wire `.github/workflows/claude-code-hooks.yml`.
10. Manual smoke pass on a clean VM (macOS + Linux).
