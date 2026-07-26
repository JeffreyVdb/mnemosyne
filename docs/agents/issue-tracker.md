# Issue tracker

**Tracker:** Local markdown.

Issues live as files under `.scratch/<feature>/` in this repository. GitHub
Issues are disabled on `JeffreyVdb/mnemosyne`, and this is a solo fork, so there
is no remote tracker to publish to.

Conventions:

- One directory per feature: `.scratch/<feature>/`.
- One file per issue, numbered: `.scratch/<feature>/NNNN-<slug>.md`.
- Each issue carries YAML frontmatter with `title`, `labels`, and `status`.
- Long-form specs live in `docs/specs/` and are committed with the code. An
  issue that has a spec is a short pointer to it, so the content exists once.

**PRs as a request surface:** off.

## Triage labels

The five canonical roles, label strings equal to their names:

`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`

## Domain docs

Single context. `CONTEXT.md` and `docs/adr/` at the repository root.
