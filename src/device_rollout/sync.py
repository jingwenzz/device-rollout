"""The sync loop: delivery from platform desired state to a device.

The platform alone only records intent. This module closes the loop
for one device at a time:

1. Read the device record. If desired equals reported, do nothing:
   no assignment is sent and no ack is produced.
2. Otherwise build a DesiredAssignment for the desired version with
   the config body stored on the platform and a per device sequence
   that increases on every delivery attempt.
3. Hand it to the simulated device. An offline device returns no ack
   and is left pending or offline for a later attempt.
4. If the device answers with an ack, pass it to the platform, which
   is the only place reported state moves on an ack.

There is no batching, no failure threshold and no rollback here.
Grouping devices into batches is the rollout engine, a later step.
"""

from __future__ import annotations

from .device import SimulatedDevice
from .messages import Ack, DesiredAssignment
from .platform import Platform


class SyncLoop:
    """Deliver desired versions from one platform to known devices."""

    def __init__(
        self, platform: Platform, devices: dict[str, SimulatedDevice]
    ) -> None:
        self._platform = platform
        self._devices = dict(devices)
        self._sequences: dict[str, int] = {device_id: 0 for device_id in devices}

    def sync_device(self, device_id: str) -> Ack | None:
        """Try once to move one device to its desired version.

        Returns the ack from the apply attempt, or None when no
        attempt was made: the device is already in sync, or it is
        offline and answered nothing.
        """
        record = self._platform.get_device(device_id)
        if device_id not in self._devices:
            raise KeyError(f"unknown simulated device: {device_id}")
        if record.desired_version is None:
            return None
        if record.desired_version == record.reported_version:
            return None
        device = self._devices[device_id]
        # A heartbeat first keeps last seen honest for online devices
        # and lets the platform see the version the device runs now.
        heartbeat = device.heartbeat()
        if heartbeat is not None:
            self._platform.record_heartbeat(heartbeat)
            record = self._platform.get_device(device_id)
            if record.desired_version == record.reported_version:
                return None
        config = self._platform.get_config(record.desired_version)
        self._sequences[device_id] += 1
        assignment = DesiredAssignment(
            version=config.version,
            config=config.body,
            sequence=self._sequences[device_id],
        )
        ack = device.receive_desired(assignment)
        if ack is None:
            return None
        self._platform.record_ack(ack)
        return ack

    def sync_all(self) -> dict[str, Ack | None]:
        """Try once for every known device, in device id order."""
        return {
            device_id: self.sync_device(device_id)
            for device_id in sorted(self._devices)
        }
