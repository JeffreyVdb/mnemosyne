# Context

Ubiquitous language for this repository. Glossary only — no implementation
details, no specs, no decisions. Decisions live in `docs/adr/`.

## Agent integration

**Host** — an agent CLI that runs Mnemosyne as a memory backend by executing
external commands at lifecycle points. Claude Code and Codex CLI are hosts.
Hermes is *not* a host in this sense: it loads Mnemosyne in-process. Contrast
**Provider**.

**Hook** — a command a Host executes at a lifecycle point, receiving the event
as JSON on stdin and answering through its exit code and stdout. Always a
Host-side concept. When this repository says "hook" without qualification it
means this.

**Provider lifecycle method** — a Python method on `MnemosyneMemoryProvider`
that Hermes calls in-process (`initialize`, `prefetch`, `sync_turn`,
`on_session_end`, …). Historically also called "Hermes hooks", which invited
confusion with **Hook**; prefer this term. These are not processes and cannot
be invoked by a Host.

**Hook event** — the lifecycle point that triggers a Hook, named by the Host:
`SessionStart`, `UserPromptSubmit`, `Stop`. Both Hosts use the same names and
the same wire format.

**Injection** — placing recalled memory into the model's context for the
current turn. A Hook performs Injection by emitting
`hookSpecificOutput.additionalContext`. The engine that decides *what* to
inject is **Prefetch**.

**Prefetch** — the Provider lifecycle method that composes the Injection
payload: multi-source recall, importance and quality filtering, deduplication,
plus deterministic identity and canonical-model-slot blocks. Distinct from
**Recall**, which is the unfiltered search primitive underneath it.

**Capture** — writing a completed conversation turn into a Bank. The engine is
the `sync_turn` Provider lifecycle method. Capture is never blocking: a Host
turn must not wait on it.

**Sidecar** — the long-lived process owned by this integration that holds an
initialized Provider warm so a Hook does not pay interpreter and model startup
cost. Distinct from the **MCP server**, which serves the tool protocol and is
not part of the Hook path.

## Memory

**Bank** — a named SQLite database isolating a set of memories. `default` is
the unnamed bank; others live under `data/banks/<name>/`.

**Scope** — whether a memory is visible outside the session that wrote it.
`global` crosses every session and project; `session` is confined to its
`session_id`. Recall admits a row when `session_id` matches *or* Scope is
`global`.

**Session id** — provenance label for the origin of a memory: which Host, which
repository, which run. It is not a partitioning mechanism here, because Capture
writes at `global` Scope.

**Trust tier** — a memory's epistemic standing (`STATED` and above), recorded
per row and surfaced during Injection.

**Consolidation** — the scheduled pass (`sleep`) that compresses raw captured
turns into higher-importance episodic memories and derived facts. Raw turns are
its input, so Capture must not discard them.
