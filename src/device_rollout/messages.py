"""Message types exchanged between the platform and a simulated device.

These are plain data holders. They carry no transport: in v1 the platform
and the devices live in the same process and pass these objects directly.
See docs/design.md sections 2.4 and 2.5 for the field semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Heartbeat:
    """A device saying it is alive and which version it actually runs."""

    device_id: str
    reported_version: int
    sent_at: float


@dataclass(frozen=True)
class Ack:
    """The result of one apply attempt. Exactly one ack per attempt."""

    device_id: str
    version: int
    result: str  # "applied" or "failed"
    applied_at: float
    error: str | None = None


@dataclass(frozen=True)
class DesiredAssignment:
    """The platform telling a device which version it should run.

    ``sequence`` is a rollout scoped, monotonically increasing number for
    this device. It is how the device tells a fresh assignment apart from
    a stale one that arrives late, and how a rollback (a lower version
    carried by a newer sequence) is recognised as legitimate.
    """

    version: int
    config: dict[str, Any]
    sequence: int
