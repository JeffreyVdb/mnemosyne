"""Working-directory-independent entry point for the Agent Hooks Sidecar."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from integrations.agent_hooks import sidecar
elif __package__:
    from . import sidecar
else:
    repository_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    )
    sys.path.insert(0, repository_root)
    from integrations.agent_hooks import sidecar


if __name__ == "__main__":
    if "--print-import-provenance" in sys.argv:
        print(os.path.realpath(sidecar.__file__), flush=True)
    sidecar.main()
