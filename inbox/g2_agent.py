"""Deprecated shim.

The G2 ambient copilot has been split into the `g2/` package. This module
re-exports the public API for backward compatibility — please import
`g2` directly going forward.
"""

from __future__ import annotations

import warnings

from g2 import (  # noqa: F401
    Blackboard,
    DeviceState,
    G2WebSocketManager,
    Signal,
    apply_demo_arbitration,
    blackboard,
    device_state,
    start_g2_agents,
    transcript_bus,
    ws_manager,
)

warnings.warn(
    "g2_agent module is deprecated; import from `g2` package instead.",
    DeprecationWarning,
    stacklevel=2,
)
