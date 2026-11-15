"""The platform backend skeleton: registry and config storage.

This module is the platform side of the design in docs/design.md. It
owns two kinds of state, and nothing else yet:

- The device registry: one record per device holding its
  ``desired_version`` (written only by the platform) and its
  ``reported_version`` (written only from device messages, here the
  heartbeat). The platform never sets reported state directly.
- The config store: every published config body under a monotonically
  increasing integer version. Versions are never reused and never
  edited in place; publishing a fix means publishing a new version.

Both live in SQLite so state survives a restart and can be inspected
with plain queries. There is deliberately no delivery, no ack
processing, and no rollout engine in this module: setting a desired
version only records the intent. Moving a device to that version is
the sync loop, which arrives in a later step.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .messages import Heartbeat

DEFAULT_HEARTBEAT_TIMEOUT = 30.0


@dataclass(frozen=True)
class ConfigVersion:
    """One published config version, as stored by the platform."""

    version: int
    body: dict[str, Any]
    created_at: float
    previous_version: int | None


@dataclass(frozen=True)
class DeviceRecord:
    """One registry row: what a device should run vs. what it runs."""

    device_id: str
    desired_version: int | None
    reported_version: int | None
    last_seen_at: float | None


class Platform:
    """SQLite backed registry and config store.

    ``db_path`` may be a file path or ``":memory:"`` for tests.
    ``clock`` stamps config creation and heartbeat receipt; it is the
    platform's own clock, so ``last_seen_at`` is when the platform saw
    the device, not the device side send time.
    """

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        *,
        clock: Callable[[], float] = time.time,
        heartbeat_timeout: float = DEFAULT_HEARTBEAT_TIMEOUT,
    ) -> None:
        self._clock = clock
        self.heartbeat_timeout = heartbeat_timeout
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS configs (
                    version INTEGER PRIMARY KEY,
                    body TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    previous_version INTEGER
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    desired_version INTEGER,
                    reported_version INTEGER,
                    last_seen_at REAL
                )
                """
            )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Platform:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # Config versions.

    def publish_config(self, body: dict[str, Any]) -> ConfigVersion:
        """Store ``body`` as the next version and return it.

        The new version is one above the highest existing version and
        records that version as its ``previous_version``, which is the
        link rollback later follows. The body is stored as JSON and is
        never mutated afterwards.
        """
        if not isinstance(body, dict):
            raise TypeError("config body must be a JSON object (dict)")
        row = self._conn.execute(
            "SELECT MAX(version) AS latest FROM configs"
        ).fetchone()
        latest = row["latest"]
        version = 1 if latest is None else int(latest) + 1
        created_at = self._clock()
        with self._conn:
            self._conn.execute(
                "INSERT INTO configs (version, body, created_at, previous_version)"
                " VALUES (?, ?, ?, ?)",
                (version, json.dumps(body, sort_keys=True), created_at, latest),
            )
        return ConfigVersion(
            version=version,
            body=json.loads(json.dumps(body)),
            created_at=created_at,
            previous_version=latest,
        )

    def get_config(self, version: int) -> ConfigVersion:
        row = self._conn.execute(
            "SELECT version, body, created_at, previous_version"
            " FROM configs WHERE version = ?",
            (version,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown config version: {version}")
        return ConfigVersion(
            version=int(row["version"]),
            body=json.loads(row["body"]),
            created_at=float(row["created_at"]),
            previous_version=row["previous_version"],
        )

    def list_versions(self) -> list[ConfigVersion]:
        rows = self._conn.execute(
            "SELECT version FROM configs ORDER BY version"
        ).fetchall()
        return [self.get_config(int(row["version"])) for row in rows]

    def latest_version(self) -> int | None:
        row = self._conn.execute(
            "SELECT MAX(version) AS latest FROM configs"
        ).fetchone()
        return row["latest"]

    # Device registry.

    def register_device(
        self, device_id: str, initial_version: int | None = None
    ) -> DeviceRecord:
        """Register a device, or return its existing record unchanged.

        Registration is idempotent, as designed: repeating it does not
        reset desired or reported state that has moved on since. A new
        device starts in sync with itself, desired equal to reported
        equal to ``initial_version``, and has never been seen.
        """
        if not device_id:
            raise ValueError("device_id must not be empty")
        existing = self._find_device(device_id)
        if existing is not None:
            return existing
        with self._conn:
            self._conn.execute(
                "INSERT INTO devices"
                " (device_id, desired_version, reported_version, last_seen_at)"
                " VALUES (?, ?, ?, NULL)",
                (device_id, initial_version, initial_version),
            )
        return DeviceRecord(
            device_id=device_id,
            desired_version=initial_version,
            reported_version=initial_version,
            last_seen_at=None,
        )

    def record_heartbeat(self, heartbeat: Heartbeat) -> DeviceRecord:
        """Apply one heartbeat: refresh reported version and last seen.

        This is the only path in this module that writes
        ``reported_version``, and it writes exactly what the device
        said. Desired state is never adopted from a heartbeat, even if
        the device reports a version the platform did not expect.
        """
        self.get_device(heartbeat.device_id)
        seen_at = self._clock()
        with self._conn:
            self._conn.execute(
                "UPDATE devices SET reported_version = ?, last_seen_at = ?"
                " WHERE device_id = ?",
                (heartbeat.reported_version, seen_at, heartbeat.device_id),
            )
        return self.get_device(heartbeat.device_id)

    def set_desired_version(self, device_id: str, version: int) -> DeviceRecord:
        """Record that a device should run ``version``.

        Only the intent is stored; reported state does not move, and
        nothing is delivered to the device here. The version must be a
        published config version.
        """
        self.get_device(device_id)
        self.get_config(version)
        with self._conn:
            self._conn.execute(
                "UPDATE devices SET desired_version = ? WHERE device_id = ?",
                (version, device_id),
            )
        return self.get_device(device_id)

    def get_device(self, device_id: str) -> DeviceRecord:
        record = self._find_device(device_id)
        if record is None:
            raise KeyError(f"unknown device: {device_id}")
        return record

    def list_devices(self) -> list[DeviceRecord]:
        rows = self._conn.execute(
            "SELECT device_id FROM devices ORDER BY device_id"
        ).fetchall()
        return [self.get_device(row["device_id"]) for row in rows]

    def device_status(self, device_id: str, *, at: float | None = None) -> str:
        """Derive a device's status from its record, never stored.

        ``offline`` when the device has never been seen or its last
        heartbeat is older than the timeout, ``in_sync`` when desired
        equals reported, and ``pending`` otherwise. The ``failed``
        status from the design needs ack history and arrives with the
        sync loop.
        """
        record = self.get_device(device_id)
        now = self._clock() if at is None else at
        if record.last_seen_at is None:
            return "offline"
        if now - record.last_seen_at > self.heartbeat_timeout:
            return "offline"
        if record.desired_version == record.reported_version:
            return "in_sync"
        return "pending"

    def _find_device(self, device_id: str) -> DeviceRecord | None:
        row = self._conn.execute(
            "SELECT device_id, desired_version, reported_version, last_seen_at"
            " FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if row is None:
            return None
        return DeviceRecord(
            device_id=row["device_id"],
            desired_version=row["desired_version"],
            reported_version=row["reported_version"],
            last_seen_at=row["last_seen_at"],
        )
