---
name: forget
description: Remove a specific memory when the user asks Mnemosyne to forget it.
argument-hint: "[memory ID]"
user-invocable: true
---

First use the recall skill to find the exact memory ID and show the matching
memory. Obtain explicit confirmation before deletion. Then run:

```bash
MNEMOSYNE_HOOKS_DATA_DIR="${HOME}/.mnemosyne-hooks" @MNEMOSYNE_PYTHON@ "${CLAUDE_PLUGIN_ROOT}/deliberate.py" --host claude-code forget "$ARGUMENTS"
```

Report `deleted` or `not_found` exactly. If the command fails, show its one-line
error and do not claim anything was removed.
