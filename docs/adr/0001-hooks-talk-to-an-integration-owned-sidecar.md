---
status: accepted
---

# Host hooks talk to an integration-owned sidecar, not to the MCP server

Claude Code and Codex hooks need memory Injection on every prompt, and this fork
must stay cheap to rebase onto upstream. A cold Python process that imports
Mnemosyne costs ~1.6–1.9s, of which 1.07s is import alone — paid before every
prompt reaches the model. The already-running MCP/SSE server answers a raw
`mnemosyne_recall` in ~0.24s from a stdlib client, but raw Recall is not what we
want to inject: the Provider's `prefetch` is, and no MCP tool exposes it. Adding
one would mean editing `mnemosyne/mcp_tools.py`, which is exactly the kind of
upstream-file change that makes rebases painful. So Hooks talk over a unix socket
to a Sidecar owned by this integration, which holds initialized Providers warm
and calls `prefetch` and `sync_turn` directly.

## Considered options

- **Cold in-process per hook.** ~1.6s of dead time per prompt, but zero
  infrastructure. Retained as nothing more than a documented fallback posture:
  when the Sidecar is unreachable a Hook prints one stderr line and exits 0.
- **MCP-over-SSE to the existing server.** ~0.24s and no new process, but it can
  only reach raw Recall. Measured output on the real bank was dominated by
  `[USER]`-prefixed transcript rows at importance 0.20–0.30, versus curated
  0.60–0.80 rows from `prefetch` on the same query.
- **New MCP tools wrapping `prefetch`.** Right latency and right filtering, but
  requires editing upstream files. Rejected on rebase cost alone.
- **Loopback TCP plus a bearer token.** Needs a reserved port, a token file, and
  a stale-port failure mode; a unix socket gets authentication from `0600` file
  permissions instead. Measured round trip 0.85–1.65ms.

## Consequences

The integration is additive: every file it adds is new, so an upstream rebase
cannot conflict with it. In exchange, the only realistic way it breaks is an
upstream rename of a Provider lifecycle method — covered by a contract test.

The socket path is `$HOME/.mnemosyne-hooks.sock` rather than something under a
runtime directory: `AF_UNIX` paths cap at 108 bytes, `XDG_RUNTIME_DIR` is empty
in the Hook environment, and macOS has no `/run/user`.

Provider instances are cached in a small LRU keyed by `session_id`, because
several agents run concurrently here and re-initializing per request would cost
53ms and churn the beam. SQLite is in WAL mode with `check_same_thread=False`,
so concurrent Providers over one bank are safe.
