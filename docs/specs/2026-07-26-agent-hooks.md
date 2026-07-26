# Spec: Agent Hooks — automatic memory for Claude Code and Codex

**Status:** ready for implementation
**Date:** 2026-07-26
**Glossary:** `CONTEXT.md`
**Decisions:** `docs/adr/0001-hooks-talk-to-an-integration-owned-sidecar.md`,
`docs/adr/0002-detach-the-mcp-server-from-both-hosts.md`,
`docs/adr/0003-capture-writes-at-global-scope.md`

---

## Problem Statement

Memory only helps if it arrives without being asked for. Today it does not.

Mnemosyne is reachable from Claude Code as an MCP server with 36 tools. Reaching
it therefore depends on an agent deciding, mid-task, to call a memory tool — and
it mostly doesn't. Meanwhile those 36 tool schemas cost roughly 6,100 tokens in
the system prompt of every session, spent whether a memory tool is used or not.
Codex has no Mnemosyne wiring at all, so anything learned in one Host is
invisible to the other.

Automatic memory does exist on this machine, but as two hand-rolled bash scripts
outside the repository. They are unversioned, untested, Linux-only, and they
inject the wrong thing: they call raw Recall with a 0.30 relevance floor, so a
conversation about designing hooks gets a Monday ticket review, a
ChatGPT-subscription musing, and an unrelated infrastructure note — three raw
transcript rows at importance 0.20–0.30 — instead of the durable preferences that
would actually have helped.

The same scripts capture indiscriminately. 493 rows of the Bank, about 14% of it,
are the Host's own `<task-notification>` payloads stored as if they were user
prompts. One real credential, an OpenRouter key pasted while configuring the
sleep model, is stored permanently at global Scope. The Bank file is
world-readable.

So: memory that must be asked for, that costs 6,100 tokens to leave unasked, that
exists on one Host only, and that injects noise while quietly accumulating
machine chatter and secrets.

## Solution

Ship an integration in this repository that makes memory automatic on both Hosts.

Three Hook events on Claude Code and Codex — `SessionStart`, `UserPromptSubmit`,
`Stop`. On every prompt, Injection happens before the model sees the turn. On
every turn end, Capture happens without the turn waiting for it. Nothing is
asked for; nothing is skipped because an agent forgot.

Injection is not reimplemented. `MnemosyneMemoryProvider.prefetch()` already
composes exactly the right payload — multi-source recall with ranked demotion of
raw transcript rows, deterministic identity and canonical-model-slot blocks,
deduplication, content caps — and it runs without Hermes installed. Capture is
`sync_turn()`, which already truncates, filters against `ignore_patterns`, and
extracts identity signals. The integration's job is to carry Host events to those
two Provider lifecycle methods and carry the answer back, not to invent memory
logic.

A Sidecar holds initialized Providers warm behind a unix socket so a Hook costs
about a millisecond of transport instead of 1.6 seconds of interpreter and model
startup. Hooks themselves import nothing but the standard library.

With Injection and Capture automatic, the MCP server's remaining job is
deliberate memory work, which three plugin skills cover for about 140 tokens. So
the MCP server is detached from both Hosts and its process retired, leaving one
long-lived process where there were two, and 6,100 tokens per session freed.

Capture gains the hygiene it currently lacks: Host-generated pseudo-prompts are
rejected, credential shapes are redacted before anything is stored, and the Bank
is no longer world-readable.

Every file the integration adds is a new file. It reaches the Provider only
through public seams, so a rebase onto upstream cannot conflict with it.

## User Stories

### Automatic injection

1. As a developer, I want relevant memory injected into every prompt without
   asking for it, so that continuity does not depend on the agent choosing to
   call a tool.
2. As a developer, I want Injection to draw on distilled memory — preferences,
   corrections, consolidated episodes, canonical model slots — so that what
   arrives is a decision I made, not a transcript of me asking a question.
3. As a developer, I want raw conversation turns demoted rather than banned from
   Injection, so that a genuinely on-topic past prompt can still surface.
4. As a developer, I want assistant transcript rows excluded from Injection, so
   that the agent is not fed its own prior narration as context.
5. As a developer, I want near-duplicate memories collapsed before Injection, so
   that one fact restated three times does not consume the whole budget.
6. As a developer, I want the injected block to be visibly labelled as recalled
   memory, so that I can tell at a glance what came from the Bank and judge
   whether it helped.
7. As a developer, I want Injection to cost a fraction of a second, so that the
   model starts working on my prompt without a perceptible stall.
8. As a developer working in any repository, I want memory formed in a different
   repository to be available, because cross-project recall is the whole reason
   to run Mnemosyne rather than the Host's own per-project memory.
9. As a developer, I want Injection to be capped in size, so that memory cannot
   crowd out the actual task context.
10. As an agent starting a session, I want a short orienting brief before the
    first prompt, so that turn one is not blind.

### Automatic capture

11. As a developer, I want each completed turn captured without being asked, so
    that today's decisions are available tomorrow.
12. As a developer, I want Capture to never delay the end of a turn, so that
    memory is invisible in normal use.
13. As a developer, I want the assistant's final message captured from the field
    the Host already provides, so that no component in this integration has to
    parse a transcript format that both Hosts document as unstable.
14. As a developer, I want captured turns to remain in the Bank as raw evidence,
    so that Consolidation has input and a better extraction prompt can be
    re-applied later.
15. As a developer, I want Capture to write at global Scope, so that nothing I
    learn is confined to the project I happened to be in.
16. As a developer, I want every captured memory to record which Host, which
    repository and which session produced it, so that I can audit or analyse
    provenance later without having partitioned anything.
17. As a developer, I want Capture to survive the Sidecar being restarted
    mid-turn, so that a deploy or upgrade does not corrupt a session's memory.

### Hygiene and privacy

18. As a developer, I want Host-generated pseudo-prompts rejected at Capture, so
    that machine chatter stops accumulating as if it were something I said.
19. As a developer, I want credential shapes redacted before storage, so that a
    key pasted into a prompt does not become a permanent, globally-scoped memory.
20. As a developer, I want redaction to preserve the rest of the turn, so that a
    turn containing one secret is not lost entirely.
21. As a developer, I want my existing ignore patterns honoured, so that the
    command noise I already filter stays filtered.
22. As a developer, I want the Bank readable only by me, so that 79MB of every
    prompt I have typed is not world-readable.
23. As a developer, I want a one-shot cleanup for what is already stored, so that
    the existing pseudo-prompt rows and the one leaked credential can be removed.
24. As a developer, I want cleanup to report what it would remove before removing
    it, so that a destructive pass over my memory is never a surprise.
25. As a developer, I want a way to suppress Capture for a session or a
    directory, so that I can work on something sensitive without it being
    remembered.

### Token cost

26. As a developer, I want the 36-tool MCP schema out of my system prompt, so
    that I stop paying roughly 6,100 tokens per session for tools I rarely call.
27. As a developer, I want deliberate memory writes still available, so that
    detaching the tools does not cost me the ability to say "remember this".
28. As a developer, I want deliberate recall available on demand, so that I can
    interrogate memory when automatic Injection did not surface what I needed.
29. As a developer, I want deliberate forgetting available, so that I can remove
    a memory I can see is wrong.
30. As a developer, I want administrative memory operations to remain reachable
    through the CLI, so that detaching the tools costs me nothing operationally.

### Both hosts

31. As a developer, I want the same three Hook events wired on Claude Code and
    Codex, so that memory behaves identically whichever agent I reach for.
32. As a developer, I want one implementation serving both Hosts, so that a fix
    or a filtering change does not have to be made twice.
33. As a developer, I want Codex to get memory it has never had, so that work
    done there stops being invisible.
34. As a developer, I want each Host's event payload quirks absorbed by the
    integration, so that neither Host needs a special-case story I have to
    remember.

### Reliability

35. As a developer, I want a Hook to exit successfully even when everything
    behind it has failed, so that memory can never break my session.
36. As a developer, I want a failed Hook to say so on stderr, so that a silent
    degradation is still a visible one.
37. As a developer, I want the session to continue normally when the Sidecar is
    down, so that a stopped service costs me memory for that turn and nothing
    else.
38. As a developer, I want the Sidecar to restart itself on failure, so that a
    transient crash heals without me noticing.
39. As a developer, I want several concurrent agents to share the Sidecar without
    interfering, so that my normal multi-agent workflow is the supported case
    rather than an edge case.
40. As a developer, I want a health check I can run by hand, so that "is memory
    working right now" is a question with a quick answer.
41. As a developer, I want Injection bounded by a timeout, so that a pathological
    query cannot stall a prompt.

### Installation and operation

42. As an operator, I want one command to install everything on a machine, so
    that setting up a second machine is not an exercise in reconstruction.
43. As an operator, I want the installer to show me every change to a config file
    before making it, so that I consent to each edit to my environment.
44. As an operator, I want a timestamped backup of every file the installer
    touches, so that I can revert by hand.
45. As an operator, I want re-running the installer to be safe, so that upgrading
    is not a question of whether I already ran it.
46. As an operator, I want my existing Hooks on both Hosts left intact,
    including their recorded trust state, so that installing memory does not
    silently disable unrelated automation.
47. As an operator, I want the legacy bash Hooks removed as part of installation,
    so that the old and new paths cannot both fire and double-capture.
48. As an operator, I want the MCP entry removed from the Host config as part of
    installation, so that the token saving actually materialises.
49. As an operator, I want a symmetric uninstaller, so that I can back this out
    completely.
50. As an operator, I want the Consolidation timer left alone, so that scheduled
    memory maintenance keeps working across the change.
51. As an operator, I want the Sidecar to survive logout, so that scheduled
    memory work does not depend on my being logged in.
52. As an operator on macOS, I want the same installer to work, so that my second
    machine is not a manual special case.
53. As an operator, I want to be told when Codex needs me to approve new Hooks,
    so that I am not left wondering why memory is silent after installing.

### Maintaining the fork

54. As a maintainer, I want this integration to add files and never edit existing
    ones, so that rebasing onto upstream stays cheap.
55. As a maintainer, I want the Provider contract asserted by a test, so that an
    upstream rename fails loudly instead of degrading memory silently.
56. As a maintainer, I want a deliberate promotion step before repository changes
    become live, so that a half-finished working tree cannot break my memory.
57. As a maintainer, I want the integration's version reported by its health
    check, so that I can tell what is actually running.
58. As a maintainer, I want the Sidecar immune to which directory a Hook fired
    from, so that working inside this repository does not silently swap the code
    that runs.

## Implementation Decisions

### Modules

- **Hook entry points** — one per event: session start, injection-and-prompt-capture,
  and turn capture. Each reads the event JSON from stdin, emits JSON on stdout,
  and exits 0 unconditionally. The Host invokes each entry point by absolute file
  path as a plain script, using an absolute interpreter path; it never uses
  `python -m`. The entry point explicitly puts its own directory first on
  `sys.path` before loading sibling modules, resolving that path through
  `__file__` (including symlinks) rather than using the Hook's working directory
  or the directory containing a symlinked entry point. This also holds when
  `-P`, `-I`, or `PYTHONSAFEPATH=1` disables Python's implicit path prepend. A
  Hook's transitive imports are the standard library and sibling integration
  modules only, never Mnemosyne or the Provider. Together these properties make
  working-directory and symlink-directory shadowing impossible.
- **Sidecar client** — a small standard-library HTTP client over `AF_UNIX`.
  Shared by all Hooks and by the plugin skills.
- **Turn hygiene** — pseudo-prompt detection, credential redaction, prompt-field
  extraction. Pure functions on strings, no I/O, shared by Hooks.
- **Session identity** — derives the Session id from Host name, repository, and
  a short random suffix, and caches it per Host session.
- **Sidecar** — long-lived process. Owns an LRU of initialized Providers keyed by
  Session id, serves the request routes, and runs Capture on a worker thread.
- **Installer** — configures both Hosts, the service definitions, and file
  permissions; and uninstalls.
- **Plugin manifest and skills** — Claude Code plugin identity, Hook event
  registration, and the three deliberate-memory skills.
- **Cleanup command** — one-shot pass over an existing Bank for pseudo-prompt
  rows and credential shapes.

Nothing outside the integration's own directory is modified, other than the
repository-root marketplace manifest that Claude Code needs in order to find the
plugin, plus the spec and decision documents.

### Reaching the memory engine

The automatic path calls three Provider lifecycle methods:
`initialize`, `prefetch`, `sync_turn`. Deliberate remember, recall, and forget
operations use the Provider's public `has_tool` / `handle_tool_call` seam rather
than reaching into its Beam or memory internals. The integration configures the
lifecycle methods through public seams —
`initialize` keyword arguments, `register_profile`, `register_prefetch_source`,
and the `MNEMOSYNE_PREFETCH_*` / `MNEMOSYNE_SYNC_TURN_*` environment variables.
The Provider's Hermes base class import is guarded upstream and degrades to
`object`, so no Hermes installation is required.

`initialize` is called with `default_scope="global"` and `agent_context="primary"`.
The latter matters: Prefetch and Capture both return early for contexts the
Provider treats as skippable, so a wrong value here yields silent no-ops.

Injection filtering is left to the Provider's profile mechanism rather than
reimplemented in the integration. Prefetch ranks candidate rows by

```
(score · 0.65 + topic_signal · 0.35 + importance · 0.05) · source_quality
```

with `source_quality` of 0.0 for assistant transcript rows, 0.72 for
conversation-sourced rows and a further 0.68 for user-prefixed ones, and 1.12 for
distilled sources. Raw rows must additionally clear a higher topic-signal
threshold. The integration ships at most a named profile that adjusts those
knobs; it does not filter rows itself. (Formula transcribed from the shipped
Provider during design, to record that the demotion is a ranking multiplier
rather than an importance floor — an earlier reading of the code had this wrong.)

### Transport

A unix domain socket in the user's home directory. File mode `0600` is the
authentication mechanism: no port to reserve, no token to generate or rotate, no
stale-port failure mode, and nothing else on the machine can reach it.

The socket path lives directly under the home directory rather than a runtime
directory, for three converging reasons: `AF_UNIX` paths are capped at 108 bytes,
the runtime-directory environment variable is empty in the Hook environment as
delivered by the Host, and macOS has no equivalent path. The path is overridable
by environment variable, which is also how tests point Hooks at a stub.

Routes: an injection request returning a context string; a capture request
acknowledged immediately and performed on a worker thread; a health request
returning version and live session count. The plugin skills add deliberate
remember, recall and forget requests.

### Provider lifecycle in the Sidecar

Providers are cached in a small LRU keyed by Session id. Several agents run
concurrently in this environment, so requests interleave by default;
re-initializing a single Provider per request would both cost measurable time and
churn the underlying store. The Bank is in WAL mode with cross-thread connections
permitted and a busy timeout configured, so concurrent Providers over one Bank
are safe.

The Sidecar is launched with an absolute interpreter and with `sys.path`
sanitisation enabled, so its imports do not depend on a working directory.

### Host wiring

Both Hosts receive the same three events and the same wire format. Claude Code
loads the integration as a plugin, which supplies the plugin root and a
persistent data directory as environment variables. Codex — which sets those same
two variables for compatibility — receives entries appended to its user-level
Hook configuration file.

Codex records Hook trust against a positional key derived from file, event, and
index within the event. Appending is therefore mandatory: inserting a Hook group
ahead of an existing one renumbers it and silently revokes its trust. The
installer appends only, and the installer's tests assert exactly this.

Turn capture is marked to run in the background on Claude Code, which supports
it. Codex parses the same field but does not implement it, so the capture Hook
must return promptly on its own — which it does, since it posts and does not wait
for a response.

The assistant's final message is taken from the field both Hosts supply on the
turn-end event. No component reads a transcript file. Both Hosts document their
transcript format as unstable, and Claude Code's documentation explicitly directs
Hooks to prefer the field.

### Memory model

One Bank, one global pool. Capture writes at global Scope; the Session id is a
provenance breadcrumb of the form host, repository, short random suffix.
Repository is resolved through git's common directory so that worktrees collapse
onto their parent repository. Because Recall admits a row when its Session id
matches or its Scope is global, and every written row is global, the Session id
never restricts what comes back. Rationale and rejected alternatives are in ADR
0003.

### Hygiene

Applied in the Hook, before anything crosses the socket, in this order:
pseudo-prompt rejection, then redaction, then the Provider's own ignore patterns.
Rejection targets the Host-generated wrappers that are not user speech —
task notifications, system reminders, local command output and command-name
markers. Redaction substitutes a labelled placeholder for recognised credential
shapes, preserving the surrounding turn.

Capture uses cheap local entity extraction, not the LLM extraction path, so a
captured turn costs no API call. Consolidation continues to do the expensive
distillation on its own schedule.

The installer restricts the Bank file and its directory to the owner. A separate
cleanup command handles rows already stored, reporting before removing.

### Token cost and deliberate access

The MCP server is removed from Claude Code's configuration and never added to
Codex; its service is disabled. The plugin ships remember, recall and forget as
skills that post to the Sidecar. Remaining administrative operations stay on the
CLI. Rationale in ADR 0002.

### Failure posture

Every Hook exits 0 on every path, including unhandled exceptions. A Hook that
cannot reach the Sidecar writes one line to stderr and produces no Injection for
that turn. The Sidecar's service definition restarts it on failure. Injection is
bounded by a Hook-level timeout below each Host's default, so a pathological
query degrades to no memory rather than a stalled prompt.

### Promotion

Repository changes to the Mnemosyne package are promoted deliberately by
reinstalling the tool, so a half-finished tree cannot break memory or the
Sidecar. Integration Hook scripts are iterated by pointing the Host at the
working tree during development; the installed plugin is a copy taken at install
time.

## Testing Decisions

A good test here exercises a process or I/O boundary and asserts observable
behaviour: what a Hook wrote to stdout, what exit code it returned, what request
arrived at the socket, what rows exist in the Bank afterwards, what a config file
looks like on disk. No test patches a Provider internal, asserts a call count, or
reaches into the LRU. If a refactor of the integration's internals breaks a test,
the test was wrong.

Three seams, all confirmed with the developer, all process or I/O boundaries.

**Seam A — the Hook process boundary.** Each Hook is run as a subprocess with a
fixture event payload on stdin and the socket path pointed at a recording stub.
Assertions cover the emitted JSON and its event name, exit code 0 on every path
including a stub that errors or hangs, the request the stub observed, prompt-field
fallbacks across both Hosts' payload shapes, pseudo-prompt rejection, credential
redaction with the surrounding turn intact, Session id derivation including the
worktree case, and fail-open when no socket exists at all. This is the Host's real
contract and it exercises hygiene, identity and client code through one door.

**Seam B — the Sidecar socket boundary.** The real Sidecar is started over a
socket in a temporary directory with the home directory and Bank redirected into
`tmp_path`. Requests are posted and the responses asserted, along with the rows
that appear in the throwaway Bank: that Capture writes at global Scope, that
pseudo-prompts never arrive, that concurrent Session ids are served
independently, and that LRU eviction does not lose a write. A guard test in this
file asserts the Provider still exposes the three lifecycle methods with the
signatures the Sidecar calls — the canary for the one realistic upstream
breakage.

**Seam C — the installer against a fake home directory.** The installer runs with
the home directory redirected into `tmp_path`, pre-populated with fixture Host
configs that mirror this machine: a Codex Hook file already containing two
unrelated session-start groups, a Codex config carrying their recorded trust
hashes, a Claude Code settings file containing both unrelated Hooks and the legacy
bash memory Hooks, and a Claude Code config containing the MCP entry. Assertions:
Codex groups are appended so existing positional trust keys are unchanged;
unrelated Hooks on both Hosts survive; the legacy memory Hooks and the MCP entry
are removed; backups exist; a second run changes nothing; and the uninstaller
restores the starting state.

**Prior art.** The repository already drives the CLI as a subprocess with the home
directory redirected into `tmp_path` and embeddings disabled by environment
variable — the pattern Seams A and B follow, and the source of the environment
hygiene those tests need. Existing tests that build throwaway Banks under
`tmp_path` are the model for Seam B's fixtures. The autouse fixtures in the test
suite's shared configuration that reset per-thread connections and disable local
model inference apply to Seam B and must not be bypassed.

Seams A and C require no Mnemosyne import and run anywhere. Seam B imports the
Provider and runs with embeddings disabled for speed. All three run in CI on
Linux. macOS is smoke-tested by hand over the existing private network, since the
divergences are service-manager and path shaped rather than logic shaped.

## Out of Scope

- **Tool-level Hooks.** No pre- or post-tool events. Capture of edits and commands
  belongs to the raw-trace layer described in the layered-memory roadmap, which
  does not exist yet; storing tool calls in a prose Bank would flood it.
- **Compaction events.** Turns are already persisted as they happen, so nothing is
  lost when a Host compacts.
- **Session-end events.** The daily Consolidation timer already covers this, and
  Codex's very short default budget for the event makes it awkward.
- **Subagent events.** Deferred until the primary path has been lived with.
- **Changing Injection or Capture logic.** The Provider owns both. The integration
  may register a profile; it does not filter, rank, truncate or extract.
- **Editing any existing repository file** beyond the marketplace manifest that
  Claude Code requires and the documents in `docs/`.
- **New MCP tools.** Explicitly rejected: it would mean editing the upstream MCP
  module, which is the rebase cost this design exists to avoid.
- **Upstreaming.** This targets the fork. Whether any of it goes to
  `mnemosyne-oss` is a later question.
- **Windows.** Linux and macOS only. There is no `AF_UNIX` story worth writing
  here and no machine to test on.
- **Encryption at rest.** The Bank stays plaintext SQLite; this spec only fixes
  its permissions. Sync-transport encryption already exists and is untouched.
- **Retrofitting provenance.** Existing rows keep whatever Session id they have.
- **Migrating the existing Bank** beyond the targeted cleanup of pseudo-prompt
  rows and stored credentials.
- **A recall-quality benchmark.** Injection quality was assessed by inspecting
  real output during design; a reproducible benchmark is separate work.

## Further Notes

**Measurements taken during design, on this machine.** Cold interpreter with a
Mnemosyne import is 1.6–1.9 seconds, of which 1.07 is the import itself. Prefetch
against the real Bank is 0.49 seconds cold and 0.16 warm. The existing MCP server
answers a raw recall in 0.19–0.23 seconds from a standard-library client over its
event stream. A unix-socket round trip is 0.85–1.65 milliseconds. The 36 MCP tool
schemas measured during design have since grown: `get_tool_schemas()` returns 40
tools and 24,976 compact-JSON characters on 2026-07-26.

**A correction worth remembering.** A first latency probe of the MCP server
reported 1.14 seconds per call and no warm-up. That was an artefact of buffered
line iteration in the probe, not server behaviour; reading the stream line by line
gave 0.19 seconds. Any future latency claim about that transport should be
measured with an unbuffered reader.

**Prior art in this repository's own history.** Pull requests 1 and 2 added a
Claude Code Hook installer in May 2026 — Hook scripts, a settings fragment,
default ignore patterns, a verifier, an uninstaller, bats tests and CI. They are
absent from `main` because the fork's history was later reset onto upstream, but
they remain fetchable from the remote's pull-request refs. Worth reading before
building the installer; the diff-preview-backup-atomic-apply shape it used is the
shape this spec asks for.

**Prior art elsewhere.** Hindsight's Claude Code and Codex integrations are the
closest external reference and are cloned on this machine. Their event map is the
one adopted here. Two things they need that this design does not: a large
transcript parser for each Host, made unnecessary by taking the assistant message
from the event payload; and a server-side extraction pipeline, since the Provider
already owns that. Their retention model — re-upserting a whole session document
and detecting compaction by transcript shrinkage — does not transfer, because
Capture appends memories rather than upserting documents. Their bank derivation
from git's common directory does transfer and is adopted for the repository
component of the Session id. PMB is the reference for warm-daemon Hook transport
and for marker-based idempotent config editing; its published claim that Codex has
no Hook system is out of date.

**Plugin development loop.** Claude Code's installed-plugin registry points at
versioned directories under `~/.claude/plugins/cache/`, not at the marketplace
clone. On this machine the cache and marketplace copies have distinct inodes and
already differ, so edits to a marketplace or working-tree Hook script are not
picked up by an installed plugin. Reinstall or update the plugin to refresh the
cache. A real Mnemosyne plugin install and live Hook reload remain deliberately
unverified here and are assigned to ticket 0008. macOS service semantics for
keeping the Sidecar alive, and whether that machine has Mnemosyne installed at
all, also remain open.

**Session-start brief decision.** Deliberately dropped after evaluating Prefetch
against a read-only backup of the real Bank. A repository-name query for
`mnemosyne` returned one general Mnemosyne-use preference. A candidate orienting
query for the repository's architecture, decisions, conventions, and current
work returned unrelated infra-manager model context plus general operating
preferences; narrower `mnemosyne agent hooks` queries were empty or similarly
generic. The candidate was different, but not useful orientation, so injecting
it at the top of every session would add noise rather than prevent a blind first
turn. No SessionStart Hook or new Sidecar route ships.

The Bank holds one `preference` row and zero distilled rows about this repository;
all six `[model:project]` slots belong to a different project, so no query or
profile posture can produce repository orientation. Once Capture has produced
distilled rows about this repository, the brief is worth re-measuring: the
current limitation is the corpus, not the query.

**Two facts that will look wrong later if not recorded.** Identity Injection is
strictly Session-id scoped and there are currently zero identity rows, so that
block will be empty until Capture's identity-signal extraction has produced some.
And the Provider's score-and-importance gate is a disjunction — a row is dropped
only when it fails both — so it is a floor on relevance, not on importance.
