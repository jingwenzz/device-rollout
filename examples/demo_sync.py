"""A tiny sync loop demo: desired moves, devices apply, acks return.

Run from the repo root:

    PYTHONPATH=src python3 examples/demo_sync.py

Three devices start on v1 and get desired v2. One applies cleanly,
one is set to fail v2 and stays on v1 with a failed status, and one
is offline and is skipped. A duplicate sync for the healthy device
produces no second ack. After the failure is cleared, a retry moves
the failed device to v2 and in sync.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from device_rollout import Platform, SimulatedDevice, SyncLoop


def show(platform: Platform) -> None:
    for device in platform.list_devices():
        status = platform.device_status(device.device_id)
        print(
            f"{device.device_id}: desired=v{device.desired_version} "
            f"reported=v{device.reported_version} status={status}"
        )


def main() -> None:
    platform = Platform(":memory:", clock=lambda: 1000.0)
    platform.publish_config({"mode": "v1"})
    platform.publish_config({"mode": "v2"})

    devices = {
        "device-a": SimulatedDevice("device-a", 1, {"mode": "v1"}),
        "device-b": SimulatedDevice(
            "device-b", 1, {"mode": "v1"}, fail_versions={2}
        ),
        "device-c": SimulatedDevice("device-c", 1, {"mode": "v1"}, online=False),
    }
    for device_id in devices:
        platform.register_device(device_id, initial_version=1)
        platform.set_desired_version(device_id, 2)

    loop = SyncLoop(platform, devices)

    print("First sync pass (desired v2 for all three):")
    for device_id, ack in loop.sync_all().items():
        result = "no ack (skipped)" if ack is None else ack.result
        print(f"{device_id}: {result}")
    show(platform)

    print("\nDuplicate sync for device-a:")
    duplicate = loop.sync_device("device-a")
    print(f"device-a: second ack is {duplicate}, acks recorded: "
          f"{len(platform.list_acks('device-a'))}")

    print("\nClearing device-b failure and retrying:")
    devices["device-b"].clear_version_failure(2)
    ack = loop.sync_device("device-b")
    print(f"device-b: ack result={ack.result}")
    show(platform)

    print(f"\nTotal acks in the log: {len(platform.list_acks())} "
          "(device-c offline, still pending a later step to catch up)")
    platform.close()


if __name__ == "__main__":
    main()
