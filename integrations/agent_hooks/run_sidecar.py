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
    integration_root = os.path.realpath(
        os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)
    )
    if integration_root not in sys.path:
        sys.path.insert(0, integration_root)
    from integrations.agent_hooks import sidecar


if __name__ == "__main__":
    if "--print-import-provenance" in sys.argv:
        print(os.path.realpath(sidecar.__file__), flush=True)
    sidecar.main()
