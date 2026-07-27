# Mnemosyne Agent Hooks

This integration carries Host Hook events to an integration-owned Sidecar. The
Sidecar keeps one initialized Provider warm per Session id and serves health and
Prefetch requests over an owner-only Unix socket.

## Run the Sidecar

Use the managed-service launcher with an absolute interpreter and launcher path:

```text
/absolute/path/to/python -I /absolute/path/to/integrations/agent_hooks/run_sidecar.py
```

`-I` enables isolated mode, which discards `PYTHONPATH` and does not prepend the
working directory. The launcher resolves the launcher target's real path, derives
the repository root from that resolved path, and adds the root before importing
the Sidecar package. Its imports therefore do not depend on the directory from
which it was started, the path of a symlink used to invoke it, or an editable
package install.

For an import-provenance check, append `--print-import-provenance`; the launcher
prints the resolved `sidecar.py` path once before serving requests.

The Sidecar binds `$HOME/.mnemosyne-hooks.sock` by default. Set
`MNEMOSYNE_HOOKS_SOCKET` to override the socket path:

```text
MNEMOSYNE_HOOKS_SOCKET=/tmp/mnemosyne-hooks.sock \
  /absolute/path/to/python -I /absolute/path/to/integrations/agent_hooks/run_sidecar.py
```

The socket is created with mode `0600`. A stale socket is removed at startup,
and SIGTERM shuts the Sidecar down cleanly and removes the socket.

## Managed-service templates

Copyable templates for both supported service managers live in `services/`:

- `mnemosyne-agent-hooks-sidecar.service.in` is a systemd user unit. It follows
  the local Mnemosyne service style with `Type=simple`, the optional
  `%h/.config/mnemosyne/llm.env` environment file,
  `Restart=on-failure`, and `WantedBy=default.target`.
- `com.mnemosyne.agent-hooks-sidecar.plist.in` is a launchd agent definition with
  `RunAtLoad` and `KeepAlive` after an unsuccessful exit.

The installer must replace `@MNEMOSYNE_PYTHON@` with the absolute path of the
installed environment's interpreter and `@MNEMOSYNE_SIDECAR_LAUNCHER@` with the
absolute path of the installed `run_sidecar.py`. It must reject non-absolute
substitutions. The templates deliberately contain tokens rather than paths for
this development machine.

On Linux, the installer must also enable lingering with
`loginctl enable-linger "$USER"` when needed, or print that exact operator action
if it cannot do so. A user unit under `default.target` otherwise stops at logout
on a machine where lingering is disabled.

`GET /health` reports the integration version and the current number of cached
Session ids:

```json
{"status": "ok", "version": "0.1.0", "live_sessions": 3}
```

`POST /prefetch` accepts a JSON object containing `prompt` and `session_id`, and
returns a JSON object containing `context`. Providers are cached in an
eight-entry LRU by default. Set `MNEMOSYNE_HOOKS_PROVIDER_CACHE_SIZE` to change
that capacity.

The Sidecar registers and selects the `agent-hooks` Provider profile. It requests
at most five memories and caps each memory's content at 1,200 characters.
Non-empty Provider output that still exceeds the integration's aggregate limit
is omitted rather than truncated.

`POST /remember`, `POST /recall`, and `POST /forget` accept the Provider tool's
required argument (`content`, `query`, or `memory_id`) plus `session_id`.
Remember always forces global Scope and redacts recognized credentials before
calling the Provider's public tool-call interface. Pseudo-prompt rejection is
intentionally limited to automatic Capture: a deliberate remember is an explicit
request to store the supplied text. The client exposes matching non-raising
`remember`, `recall`, and `forget` methods. A deliberate request can time out
after its Provider operation commits. The CLI therefore reports a timeout as an
unknown outcome and requires reconciliation with recall before retrying; it never
reports that a timed-out write was not stored.

## Claude Code plugin

The repository marketplace manifest points Claude Code at this directory as the
plugin root. `.claude-plugin/plugin.json` discovers the three skills under
`skills/`; `hooks/hooks.json` registers only the implemented `UserPromptSubmit`
and `Stop` events. There is no `SessionStart` entry point or registration.

The source plugin deliberately carries `@MNEMOSYNE_PYTHON@` in every command
file that launches Python: `hooks/hooks.json` and
`skills/{remember,recall,forget}/SKILL.md`. Ticket 0008's installer must first
let Claude Code install or update the plugin, then resolve the active cache
copy's `installPath` from `installed_plugins.json` and replace the token in all
four files in that installed copy. Substitution in this source tree is
insufficient because Claude Code loads a copy. A later `plugin update` refreshes
that copy and discards the substitution, so the installer must re-substitute
after every update. Its verifier must reject any non-absolute interpreter value,
reject a token in any of those four command files, and tell the operator to
rerun the Mnemosyne installer after a direct `plugin update`. Service templates
elsewhere in the plugin deliberately retain their tokens until the installer
renders them.

After substitution each command argv is exactly the absolute interpreter, the
`${CLAUDE_PLUGIN_ROOT}` absolute script path, and `--host claude-code`. Each Hook
command supplies
`MNEMOSYNE_HOOKS_DATA_DIR="${HOME}/.mnemosyne-hooks"`; Session-id suffixes are
stored in its `sessions/` child.

Claude Code copies installed plugins into versioned directories under
`~/.claude/plugins/cache/`. On this development machine those cache directories
are distinct from the marketplace clone (different inodes, with observed
content drift). Editing a Hook in a marketplace checkout or working tree
therefore does not change the active installed copy; reinstall or update the
plugin to refresh it. A real Mnemosyne plugin install and live reload test are
deferred to ticket 0008.

## Run the UserPromptSubmit Hook

The Host must invoke the Hook with an absolute interpreter and the Hook's
absolute file path:

```text
/abs/python /abs/integrations/agent_hooks/user_prompt_submit.py --host claude-code
```

Use `--host codex` for Codex. Do not invoke the Hook with `python -m`. Plain-script
execution explicitly puts the Hook directory first on `sys.path` before loading
sibling modules. The path is resolved through the entry point's `__file__`,
including symlinks. It therefore never comes from the Host's working directory
or the directory containing a symlinked entry point, including when `-P`, `-I`,
or `PYTHONSAFEPATH=1` disables Python's implicit path prepend. This exact argv
rule applies to all later Hook entry points.

The Hook reads a Host event from stdin, calls `POST /prefetch`, and emits recalled
memory as visibly labelled `hookSpecificOutput.additionalContext`. It exits zero
on every path. An unavailable Sidecar, a deadline, or an oversized Injection
produces no stdout and one diagnostic line on stderr.

The Hook has a 0.75-second wall-clock deadline once it is running; interpreter
startup falls outside that deadline. Final Injection is capped at 12,000
characters. Set `MNEMOSYNE_HOOKS_DATA_DIR` to relocate the owner-only cache that
keeps the random Session-id suffix stable for one Host session.

## Use the client

The standard-library client returns a `ClientResult` value on success and
failure:

```python
from integrations.agent_hooks.client import SidecarClient

health = SidecarClient().health()
if not health.ok:
    print(health.error)
```

## Install, verify, and uninstall

Run the installer with the absolute interpreter from the installed Mnemosyne
environment:

```text
/absolute/python -m integrations.agent_hooks.installer install \
  --python /absolute/python
```

The command previews unified diffs for Claude Code settings and MCP config,
lists every permission change, and describes the plugin and service actions.
It prompts before applying; use `--dry-run` for a read-only preview or `--yes`
for a non-interactive approved run. Existing files receive timestamped
`.bak.<UTC timestamp>` copies, newly created files receive matching
`.bak.<timestamp>.absent` markers, and replacements use atomic renames.

Installation uses Claude Code to install or update `mnemosyne@mnemosyne`,
resolves the active cache copy through `installed_plugins.json`, substitutes
the absolute interpreter in the four command files, renders the Sidecar unit,
removes only the two legacy bash Hook groups, removes any top-level or
project-scoped `mnemosyne` MCP entry, disables only `mnemosyne.service`, and
restricts the complete Bank data tree to owner-only modes. The legacy scripts
remain on disk for hand recovery; only their active registrations are removed.
The Consolidation service and timer are never addressed.

After a direct `claude plugin update`, rerun the installer because Claude Code
replaces the cache copy and restores the source tokens. Check an installation
or restore its recorded starting state with:

```text
/absolute/python -m integrations.agent_hooks.installer verify
/absolute/python -m integrations.agent_hooks.installer uninstall
```

Uninstall removes a plugin and marketplace registration that installation
created, restores the recorded config bytes and permission modes, and returns
the MCP service to its recorded active/enabled state.

Linux logout survival requires user lingering. The installer reports the exact
operator check and `loginctl enable-linger "$USER"` action rather than enabling
lingering silently.

## Capture state and failure boundaries

The Hook data directory, its `sessions/` and `pending/` children, and their
entries are owner-only. Pending prompt state is pruned only after the Sidecar
acknowledges the matching Capture. This successful-delivery policy can retain
abandoned sessions indefinitely, but it cannot discard a Capture that may
legitimately still receive a matching `Stop`; age- or count-based pruning would.

`MNEMOSYNE_CAPTURE_SUPPRESS` recognizes only `1`, `true`, `yes`, and `on`
(case-insensitive). Any unrecognized value, including `banana`, fails open and
does not suppress Capture.

The Sidecar forces `sync_roles=("user", "assistant")` for Agent Hook Capture.
That integration profile intentionally overrides the operator's ordinary
configuration and `MNEMOSYNE_SYNC_ROLES`.

SIGTERM drains every acknowledged Capture before shutdown. SIGKILL cannot be
drained: a Capture acknowledged with HTTP 202 and then lost to `kill -9` does
not replay, because the Hook has already removed its pending state.

## Existing-Bank cleanup

Cleanup is a separate, never-automatic operation. The default invocation is a
read-only report:

```text
python -m integrations.agent_hooks.cleanup
python -m integrations.agent_hooks.cleanup --apply --confirm CLEANUP
```

It removes detected stored user pseudo-prompts and redacts supported credential
shapes transactionally across the relevant Bank tables. Its detector is
conservative: it does not inspect non-`[USER]` rows, does not promise to find
every possible secret, and redaction does not recompute an existing embedding.
The installer never invokes cleanup or infers permission to mutate Bank rows.

## Promotion

Repository changes to the Mnemosyne package become live only after reinstalling
the tool, so a half-finished working tree cannot break memory or the Sidecar.
During integration development a Host may point directly at working-tree Hook
scripts. A normal plugin install is different: Claude Code takes a versioned
copy, so source edits require plugin reinstall/update followed by the Mnemosyne
installer's interpreter substitution.

## Measured round-trip time

Measured on 2026-07-26 on the development machine with embeddings disabled,
using 10 warm-up calls followed by 200 sequential health calls and 100 sequential
Prefetch calls against one warm Provider and an empty Bank:

| Route | Minimum | Median | p95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| `GET /health` | 0.564 ms | 0.688 ms | 0.946 ms | 1.155 ms |
| `POST /prefetch` | 1.088 ms | 1.243 ms | 1.511 ms | 1.645 ms |

These are socket-to-Sidecar measurements. They do not include starting the Hook
interpreter.

## Layout

- `__init__.py` — integration version shared by the plugin and health response
- `client.py` — standard-library HTTP client over `AF_UNIX`
- `cleanup.py` — explicit one-shot cleanup for existing Bank rows
- `deliberate.py` — visible-failure CLI used by remember, recall, and forget skills
- `hooks/` — Claude Code Hook registration manifest
- `hygiene.py` — shared pseudo-prompt detection and credential redaction
- `identity.py` — worktree-aware Session-id derivation and suffix cache
- `installer.py` — diff-first Claude Code install, verify, and uninstall command
- `provider_cache.py` — warm per-session Provider LRU and `agent-hooks` profile
- `run_sidecar.py` — isolated, working-directory-independent Sidecar launcher
- `services/` — copyable systemd and launchd service-definition templates
- `sidecar.py` — Sidecar command and health, Prefetch, Capture, and deliberate routes
- `skills/` — deliberate remember, recall, and forget commands
- `transport.py` — shared limits, socket path, and environment overrides
- `turn_end.py` — `Stop` Capture Hook
- `turn_state.py` — owner-only pending prompt pairing state
- `user_prompt_submit.py` — `UserPromptSubmit` Injection Hook

All tests live in repository-root `tests/test_agent_hooks_*.py`: Hook subprocess
tests exercise Seam A, real Sidecar/Provider tests exercise Seam B, and
`test_agent_hooks_installer.py` exercises Seam C against fake Host homes.
