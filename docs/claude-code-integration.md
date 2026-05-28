# Mnemosyne <-> Claude Code Integration

Two Claude Code hooks (`UserPromptSubmit`, `Stop`) wired to Mnemosyne for automatic memory capture and recall across sessions. Per-turn store, per-prompt recall, no extra prompt or tool call required.

## What this is

```
┌────────────────────┐   prompt    ┌────────────────────────────┐
│  User in Claude    │ ──────────▶ │  UserPromptSubmit hook     │
│  Code              │             │   1. store [USER] turn     │
└────────────────────┘             │   2. recall top-5 memories │
        ▲                          │   3. inject as context     │
        │                          └──────────────┬─────────────┘
        │                                         │ additionalContext
        │                                         ▼
        │                          ┌────────────────────────────┐
        │           response       │  Claude responds           │
        └──────────────────────────│                            │
                                   └──────────────┬─────────────┘
                                                  │ Stop event
                                                  ▼
                                   ┌────────────────────────────┐
                                   │  Stop hook                 │
                                   │   store [ASSISTANT] turn   │
                                   └────────────────────────────┘
```

## Prerequisites

- Claude Code installed (any recent build that supports `UserPromptSubmit` + `Stop` hooks).
- `jq` 1.6+ — `brew install jq` or `apt install jq`.
- Mnemosyne CLI on PATH — `pip install mnemosyne-memory` (or pipx).

The hooks default to a shared SQLite DB at `~/.hermes/mnemosyne/data/mnemosyne.db`. If you also run the [Hermes Agent](https://github.com/NousResearch/hermes-agent), recall is cross-agent for free. If you don't, the file is created on first run and used by Claude Code alone — nothing to set up.

## Install

```bash
git clone https://github.com/AxDSan/mnemosyne
cd mnemosyne
./scripts/install-claude-code-hooks.sh
```

By default the installer:
1. Runs preflight checks (`jq`, `mnemosyne`, `~/.claude/` writable, repo files present).
2. Shows a unified diff of the planned changes to `~/.claude/settings.json`.
3. Prints the backup target path.
4. Prompts `Apply? [y/N]` — defaults to **No**.
5. On approval, writes a timestamped `~/.claude/settings.json.bak.<unix-ts>` and patches the live file atomically.
6. Copies hook scripts into `~/.claude/hooks/`.
7. Copies `mnemosyne-ignore-patterns` if missing (keeps existing otherwise).

### Flags

| Flag | Purpose |
|---|---|
| `--dry-run` | Print diff, exit, modify nothing. Takes precedence over `--yes`. |
| `--yes` | Skip prompt. Required for non-tty / CI use. |
| `--force-replace` | Overwrite an existing entry that has the same command but different shape (e.g., different `timeout`). |
| `--reset-ignore-patterns` | Replace `~/.claude/mnemosyne-ignore-patterns` from the repo default. Existing file is backed up. |
| `--mnemosyne-bin=<path>` | Use a specific `mnemosyne` binary instead of PATH lookup (useful for pipx / venv installs). |

## What gets modified

```
~/.claude/hooks/mnemosyne-user-prompt              (new, 755)
~/.claude/hooks/mnemosyne-stop                     (new, 755)
~/.claude/mnemosyne-ignore-patterns                (new, 644 — skipped if you already have one)
~/.claude/settings.json                            (patched — backup at .bak.<ts>)
~/.claude/settings.json.bak.<ts>                   (created)
```

Sample diff:

```diff
--- ~/.claude/settings.json (existing)
+++ ~/.claude/settings.json (after install)
 {
+  "hooks": {
+    "UserPromptSubmit": [
+      {
+        "matcher": "*",
+        "hooks": [
+          { "type": "command", "command": "~/.claude/hooks/mnemosyne-user-prompt", "timeout": 15 }
+        ]
+      }
+    ],
+    "Stop": [
+      {
+        "matcher": "*",
+        "hooks": [
+          { "type": "command", "command": "~/.claude/hooks/mnemosyne-stop", "timeout": 15 }
+        ]
+      }
+    ]
+  }
 }
```

## Verifying it works

```bash
./scripts/verify-claude-code-hooks.sh
```

Five checks: CLI alive, hook bash syntax, UserPromptSubmit round-trip, settings.json shape, recent log clean. Exit 0 means healthy.

Or, in an interactive session: start `claude`, type any prompt, and watch the `## Mnemosyne Context` block appear in the next response if relevant memories exist.

## Uninstall

```bash
./scripts/uninstall-claude-code-hooks.sh             # remove settings.json entries only
./scripts/uninstall-claude-code-hooks.sh --purge-files  # also delete hook scripts and ignore-patterns
```

The DB at `~/.hermes/mnemosyne/data/mnemosyne.db` is **never** touched. Delete it manually if you want a clean slate:

```bash
rm ~/.hermes/mnemosyne/data/mnemosyne.db
```

## Hermes coupling

The default DB path is the same one used by [`hermes_memory_provider`](https://github.com/NousResearch/hermes-agent) — both processes safely share the file via SQLite's WAL mode. If you only use Claude Code, nothing changes; if you run both, your memory store is unified.

Override the DB path by exporting `MNEMOSYNE_DATA_DIR` in your shell rc:

```bash
export MNEMOSYNE_DATA_DIR="$HOME/.mnemosyne/data"
```

## Limitations

- **No SessionStart hook.** Memories surface on the first user prompt, not on session resume.
- **No SubagentStop hook.** Sub-agent (Task tool) inner turns are not captured. v2 candidate.
- **No LLM fact extraction.** Raw turns stored verbatim; no `extract=True` summarization yet.
- **Shared bank only.** No per-project isolation. v2 candidate.
- **bash 3.2 + missing both `timeout`/`gtimeout`.** Degrades to fail-open silently. On macOS, install via `brew install coreutils` for `gtimeout`.
- **First-call cold start ~4s.** The `timeout 5` wrapper means the very first prompt after a long idle may drop silently. Subsequent prompts are warm.

## Troubleshooting

**Hook log.** All hook errors are written here:

```bash
tail -f ~/.claude/mnemosyne-hook.log
```

If the log is empty, hooks succeeded. Most fail-open paths write a one-line reason.

**Verify with debug:**

```bash
MNEMOSYNE_HOOK_LOG=/tmp/dbg.log \
  echo '{"prompt":"smoke","session_id":"x","transcript_path":"/tmp/none","cwd":"/tmp"}' \
  | ~/.claude/hooks/mnemosyne-user-prompt
cat /tmp/dbg.log
```

**Settings.json got corrupted.** Restore from the timestamped backup:

```bash
ls ~/.claude/settings.json.bak.*  # find your backup
mv ~/.claude/settings.json.bak.<ts> ~/.claude/settings.json
```

**`mnemosyne` not on PATH.** Pass `--mnemosyne-bin=<path>` to the installer, or set `MNEMOSYNE_BIN=<path>` in your shell rc so the hooks find it at runtime.

**Memory recall returns nothing.** Verify the DB exists and has rows:

```bash
ls -la ~/.hermes/mnemosyne/data/mnemosyne.db
mnemosyne recall "hello world" 3
```

If the DB is empty, hooks haven't stored anything yet — send a few prompts and try again.
