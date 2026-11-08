"""A tiny single device demo: heartbeat, apply v2, one failure, recovery.

Run from the repo root:

    PYTHONPATH=src python3 examples/demo_device.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from device_rollout import DesiredAssignment, SimulatedDevice


def show(device: SimulatedDevice) -> None:
    beat = device.heartbeat()
    if beat is None:
        print(f"{device.device_id}: offline, still on v{device.reported_version}")
    else:
        print(f"{device.device_id}: heartbeat, running v{beat.reported_version}")


def main() -> None:
    healthy = SimulatedDevice("device-healthy", 1, {"mode": "v1"})
    flaky = SimulatedDevice("device-flaky", 1, {"mode": "v1"}, fail_versions={2})

    print("Before rollout:")
    show(healthy)
    show(flaky)

    print("\nAssigning v2 (sequence 1):")
    for device in (healthy, flaky):
        ack = device.receive_desired(
            DesiredAssignment(version=2, config={"mode": "v2"}, sequence=1)
        )
        print(f"{device.device_id}: ack result={ack.result} error={ack.error}")

    print("\nDuplicate v2 assignment to the healthy device (sequence 1 again):")
    duplicate = healthy.receive_desired(
        DesiredAssignment(version=2, config={"mode": "v2"}, sequence=1)
    )
    print(f"device-healthy: second ack is {duplicate} (no repeat apply, no second ack)")

    print("\nClearing the flaky device failure and retrying v2 (sequence 2):")
    flaky.clear_version_failure(2)
    ack = flaky.receive_desired(
        DesiredAssignment(version=2, config={"mode": "v2"}, sequence=2)
    )
    print(f"device-flaky: ack result={ack.result} error={ack.error}")

    print("\nAfter rollout:")
    show(healthy)
    show(flaky)


if __name__ == "__main__":
    main()
