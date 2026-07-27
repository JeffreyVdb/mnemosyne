---
name: remember
description: Store durable memory for requests such as "remember this" or "keep this for later".
argument-hint: "[what to remember]"
user-invocable: true
---

Preserve the user's meaning and run:

```bash
MNEMOSYNE_HOOKS_DATA_DIR="${HOME}/.mnemosyne-hooks" @MNEMOSYNE_PYTHON@ "${CLAUDE_PLUGIN_ROOT}/deliberate.py" --host claude-code remember "$ARGUMENTS"
```

Report the returned memory ID, or the pending approval ID when write approval
stages the request instead of committing it. If the command fails, show its
one-line error; do not claim the memory was stored. If it times out, the outcome
is unknown: check with recall before retrying, because the write may already
have committed.
