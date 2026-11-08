"""A simulated device.

The device owns exactly one thing: the config it is actually running.
It reports that version in heartbeats, applies a new desired version
all or nothing when the platform assigns one, and answers each apply
attempt with exactly one ack. It never decides its own desired version.

Failure injection is a test switch, not device behaviour: a version can
be marked to fail, or the next single apply can be forced to fail, so
rollout pause and rollback logic can be exercised later.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from typing import Any

from .messages import Ack, DesiredAssignment, Heartbeat


class SimulatedDevice:
    def __init__(
        self,
        device_id: str,
        initial_version: int = 1,
        initial_config: dict[str, Any] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        fail_versions: set[int] | None = None,
        online: bool = True,
    ) -> None:
        if not device_id:
            raise ValueError("device_id must not be empty")
        self.device_id = device_id
        self.reported_version = initial_version
        self.current_config: dict[str, Any] = copy.deepcopy(initial_config or {})
        self._clock = clock
        self._fail_versions: set[int] = set(fail_versions or ())
        self._fail_next_apply = False
        self.online = online
        self._highest_sequence_seen = 0
        self.ack_history: list[Ack] = []

    # Lifecycle and connectivity, both simulated.

    def start(self) -> None:
        self.online = True

    def stop(self) -> None:
        self.online = False

    def go_offline(self) -> None:
        self.online = False

    def come_online(self) -> None:
        self.online = True

    # Failure injection switches.

    def fail_on_version(self, version: int) -> None:
        self._fail_versions.add(version)

    def clear_version_failure(self, version: int) -> None:
        self._fail_versions.discard(version)

    def fail_next_apply(self) -> None:
        self._fail_next_apply = True

    def clear_failures(self) -> None:
        self._fail_versions.clear()
        self._fail_next_apply = False

    # Messages the device sends.

    def heartbeat(self) -> Heartbeat | None:
        """Return a heartbeat, or None while offline.

        An offline device sends nothing, so the platform side simply
        sees its last seen time go stale, as designed.
        """
        if not self.online:
            return None
        return Heartbeat(
            device_id=self.device_id,
            reported_version=self.reported_version,
            sent_at=self._clock(),
        )

    # Messages the device receives.

    def receive_desired(self, assignment: DesiredAssignment) -> Ack | None:
        """Handle one desired version assignment.

        Returns an ack for an apply attempt, or None when no attempt
        was made: the device is offline, the assignment is stale, or
        the device already runs the assigned version.
        """
        if not self.online:
            return None
        if assignment.sequence <= self._highest_sequence_seen:
            return None
        self._highest_sequence_seen = assignment.sequence

        if assignment.version == self.reported_version:
            return None

        ack = self._attempt_apply(assignment)
        self.ack_history.append(ack)
        return ack

    def _attempt_apply(self, assignment: DesiredAssignment) -> Ack:
        now = self._clock()

        if self._fail_next_apply:
            self._fail_next_apply = False
            return Ack(
                device_id=self.device_id,
                version=assignment.version,
                result="failed",
                applied_at=now,
                error="injected failure: next apply forced to fail",
            )
        if assignment.version in self._fail_versions:
            return Ack(
                device_id=self.device_id,
                version=assignment.version,
                result="failed",
                applied_at=now,
                error=f"injected failure for version {assignment.version}",
            )
        if not isinstance(assignment.config, dict):
            return Ack(
                device_id=self.device_id,
                version=assignment.version,
                result="failed",
                applied_at=now,
                error="config must be a JSON object",
            )

        # All or nothing: the new config is fully prepared first and
        # swapped in only once, so a failure above leaves the previous
        # config and version completely intact.
        new_config = copy.deepcopy(assignment.config)
        self.current_config = new_config
        self.reported_version = assignment.version
        return Ack(
            device_id=self.device_id,
            version=assignment.version,
            result="applied",
            applied_at=now,
        )
