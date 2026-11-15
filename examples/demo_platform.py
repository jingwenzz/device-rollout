"""A tiny platform demo: registry, config versions, desired vs. reported.

Run from the repo root:

    PYTHONPATH=src python3 examples/demo_platform.py

This demo only exercises the backend skeleton. Setting a desired
version records intent on the platform; no device is contacted and no
reported version moves. Delivery is the sync loop, a later step.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from device_rollout import Heartbeat, Platform


def show(platform: Platform) -> None:
    for device in platform.list_devices():
        status = platform.device_status(device.device_id)
        print(
            f"{device.device_id}: desired=v{device.desired_version} "
            f"reported=v{device.reported_version} status={status}"
        )


def main() -> None:
    platform = Platform(":memory:", clock=lambda: 1000.0)

    print("Publishing config v1 and v2:")
    v1 = platform.publish_config({"mode": "v1"})
    v2 = platform.publish_config({"mode": "v2", "level": 3})
    print(f"v{v1.version} (previous={v1.previous_version}), "
          f"v{v2.version} (previous={v2.previous_version})")

    print("\nRegistering three devices on v1:")
    for device_id in ("device-a", "device-b", "device-c"):
        platform.register_device(device_id, initial_version=1)
    # device-c never sends a heartbeat, so the platform never sees it.
    for device_id in ("device-a", "device-b"):
        platform.record_heartbeat(
            Heartbeat(device_id=device_id, reported_version=1, sent_at=999.0)
        )
    show(platform)

    print("\nSetting desired v2 for device-a and device-b (intent only):")
    platform.set_desired_version("device-a", 2)
    platform.set_desired_version("device-b", 2)
    show(platform)

    print(
        "\nNothing has been delivered yet: reported is still v1 for both "
        "online devices,\nand device-c is offline. The sync loop closes "
        "this gap in a later step."
    )
    platform.close()


if __name__ == "__main__":
    main()
