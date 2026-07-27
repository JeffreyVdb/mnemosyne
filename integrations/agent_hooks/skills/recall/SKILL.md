---
name: recall
description: Search durable memory for requests such as "what do you know about X" or "recall X".
argument-hint: "[search query]"
user-invocable: true
---

Run:

```bash
MNEMOSYNE_HOOKS_DATA_DIR="${HOME}/.mnemosyne-hooks" @MNEMOSYNE_PYTHON@ "${CLAUDE_PLUGIN_ROOT}/deliberate.py" --host claude-code recall "$ARGUMENTS"
```

Present only returned memories. If none match, say so. If the command fails,
show its one-line error and do not invent an answer. If it times out, retry the
lookup once or report that the lookup did not complete.
