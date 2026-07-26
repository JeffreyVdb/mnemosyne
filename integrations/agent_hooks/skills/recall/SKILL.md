---
name: recall
description: Search durable memory when the user asks what Mnemosyne knows.
argument-hint: "[search query]"
user-invocable: true
---

Run:

```bash
MNEMOSYNE_HOOKS_DATA_DIR="${HOME}/.mnemosyne-hooks" @MNEMOSYNE_PYTHON@ "${CLAUDE_PLUGIN_ROOT}/deliberate.py" --host claude-code recall "$ARGUMENTS"
```

Present only returned memories. If none match, say so. If the command fails,
show its one-line error and do not invent an answer. If it times out, the outcome
is unknown: check with recall before retrying or making a claim.
