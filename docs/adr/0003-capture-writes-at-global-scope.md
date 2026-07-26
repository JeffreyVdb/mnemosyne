---
status: accepted
---

# Capture writes at global scope; session id is provenance only

The reason to run Mnemosyne at all — rather than relying on Claude Code's and
Codex's own per-project memory files — is that it remembers across every project
and every session. So Capture calls `initialize(session_id, default_scope="global")`
and every written memory is visible everywhere. `session_id` is set to
`{host}:{repo}:{short-uuid}` purely as a provenance breadcrumb: because Recall
admits a row when `session_id` matches *or* Scope is `global`, and every row is
`global`, the id never restricts what comes back.

## Considered options

- **Per-project Session id with `session` Scope.** Would confine turn captures to
  the project that produced them while curated memories stayed global. Rejected
  because it reproduces, inside Mnemosyne, the per-project memory the Hosts
  already provide, and discards the differentiator.
- **A Bank per project.** Stronger, physical isolation and smaller databases, but
  the same objection plus Bank sprawl and a Consolidation pass per Bank.
- **Keep everything under the literal id `default`,** as the previous hand-rolled
  bash hooks did. Behaves as one pool, but records nothing about which Host or
  repository a memory came from, so no later audit or analysis is possible.

## Consequences

Cross-project noise is not prevented by scoping, so it must be prevented by
Injection filtering. That is `prefetch`'s job and it already does it — by ranked
demotion rather than by an importance floor. `[ASSISTANT]` rows are excluded
outright (source quality 0.0). `source="conversation"` rows are classified raw:
quality ×0.72, a further ×0.68 for a `[USER]` prefix, and they must clear a
topic-signal threshold of 0.18 instead of the 0.08 asked of distilled rows.
Distilled sources — `preference`, `correction`, `fact`, `identity`, `insight`,
`sleep_consolidation` — get ×1.12. Ranking is
`(score·0.65 + signal·0.35 + importance·0.05) · quality`.

So a raw turn still injects when it is genuinely, lexically on-topic, and is
otherwise outranked roughly 2:1 by distilled memory. That is the behaviour we
want, and it is why Capture may keep storing raw turns for Consolidation to eat.
The score/importance gate itself is an OR (`score < min_score AND importance <
min_importance` to drop), so it is a floor on relevance, not on importance.

Because every prompt is stored globally and durably, the Capture path is
responsible for hygiene: redacting credential shapes, rejecting Host-generated
pseudo-prompts such as `<task-notification>`, and honouring the Provider's
`ignore_patterns`. A leaked credential here is a permanent, globally-scoped
memory.
