import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from device_rollout import Ack, Platform, SimulatedDevice, SyncLoop


def make_setup(fail_versions=None, online=True):
    platform = Platform(":memory:", clock=lambda: 1000.0, heartbeat_timeout=30.0)
    platform.publish_config({"mode": "v1"})
    platform.publish_config({"mode": "v2"})
    platform.register_device("device-1", initial_version=1)
    device = SimulatedDevice(
        "device-1",
        1,
        {"mode": "v1"},
        fail_versions=fail_versions or set(),
        online=online,
    )
    loop = SyncLoop(platform, {"device-1": device})
    return platform, device, loop


class TestSyncLoop(unittest.TestCase):
    def test_sync_applies_desired_and_updates_reported(self):
        platform, device, loop = make_setup()
        platform.set_desired_version("device-1", 2)
        ack = loop.sync_device("device-1")
        self.assertEqual(ack.result, "applied")
        self.assertEqual(device.reported_version, 2)
        record = platform.get_device("device-1")
        self.assertEqual(record.reported_version, 2)
        self.assertEqual(platform.device_status("device-1"), "in_sync")
        self.assertEqual(len(platform.list_acks("device-1")), 1)

    def test_sync_without_desired_change_does_nothing(self):
        platform, device, loop = make_setup()
        ack = loop.sync_device("device-1")
        self.assertIsNone(ack)
        self.assertEqual(platform.list_acks(), [])
        self.assertEqual(device.ack_history, [])

    def test_duplicate_sync_is_idempotent(self):
        platform, device, loop = make_setup()
        platform.set_desired_version("device-1", 2)
        first = loop.sync_device("device-1")
        second = loop.sync_device("device-1")
        third = loop.sync_device("device-1")
        self.assertEqual(first.result, "applied")
        self.assertIsNone(second)
        self.assertIsNone(third)
        self.assertEqual(len(device.ack_history), 1)
        self.assertEqual(len(platform.list_acks()), 1)
        self.assertEqual(platform.get_device("device-1").reported_version, 2)

    def test_failed_apply_keeps_reported_and_marks_failed(self):
        platform, device, loop = make_setup(fail_versions={2})
        platform.set_desired_version("device-1", 2)
        ack = loop.sync_device("device-1")
        self.assertEqual(ack.result, "failed")
        self.assertEqual(platform.get_device("device-1").reported_version, 1)
        self.assertEqual(device.reported_version, 1)
        self.assertEqual(platform.device_status("device-1"), "failed")

    def test_retry_after_clearing_failure_succeeds(self):
        platform, device, loop = make_setup(fail_versions={2})
        platform.set_desired_version("device-1", 2)
        loop.sync_device("device-1")
        device.clear_version_failure(2)
        ack = loop.sync_device("device-1")
        self.assertEqual(ack.result, "applied")
        self.assertEqual(platform.device_status("device-1"), "in_sync")
        self.assertEqual(len(platform.list_acks()), 2)

    def test_offline_device_is_skipped_and_stays_reported(self):
        platform, device, loop = make_setup(online=False)
        platform.set_desired_version("device-1", 2)
        ack = loop.sync_device("device-1")
        self.assertIsNone(ack)
        self.assertEqual(platform.get_device("device-1").reported_version, 1)
        self.assertEqual(platform.list_acks(), [])

    def test_sync_all_covers_every_device(self):
        platform = Platform(":memory:", clock=lambda: 1000.0)
        platform.publish_config({"mode": "v1"})
        platform.publish_config({"mode": "v2"})
        devices = {}
        for device_id in ("device-b", "device-a"):
            platform.register_device(device_id, initial_version=1)
            platform.set_desired_version(device_id, 2)
            devices[device_id] = SimulatedDevice(device_id, 1, {"mode": "v1"})
        loop = SyncLoop(platform, devices)
        results = loop.sync_all()
        self.assertEqual(list(results), ["device-a", "device-b"])
        self.assertTrue(all(a.result == "applied" for a in results.values()))
        self.assertTrue(
            all(platform.device_status(d) == "in_sync" for d in devices)
        )


class TestAckProcessing(unittest.TestCase):
    def setUp(self):
        self.platform = Platform(":memory:", clock=lambda: 1000.0)
        self.platform.publish_config({"mode": "v1"})
        self.platform.publish_config({"mode": "v2"})
        self.platform.register_device("device-1", initial_version=1)
        self.platform.set_desired_version("device-1", 2)

    def test_duplicate_ack_is_not_double_counted(self):
        ack = Ack(device_id="device-1", version=2, result="applied", applied_at=10.0)
        first = self.platform.record_ack(ack)
        second = self.platform.record_ack(ack)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(self.platform.list_acks()), 1)
        self.assertEqual(self.platform.get_device("device-1").reported_version, 2)

    def test_stale_applied_ack_is_logged_but_does_not_move_reported(self):
        # Desired moved back to v1 before a late v2 ack arrives.
        self.platform.set_desired_version("device-1", 1)
        stale = Ack(device_id="device-1", version=2, result="applied", applied_at=10.0)
        self.platform.record_ack(stale)
        self.assertEqual(self.platform.get_device("device-1").reported_version, 1)
        self.assertEqual(len(self.platform.list_acks()), 1)

    def test_failed_ack_does_not_move_reported(self):
        failed = Ack(
            device_id="device-1",
            version=2,
            result="failed",
            applied_at=10.0,
            error="boom",
        )
        self.platform.record_ack(failed)
        self.assertEqual(self.platform.get_device("device-1").reported_version, 1)
        self.assertEqual(self.platform.device_status("device-1"), "failed")

    def test_ack_for_unknown_device_is_rejected(self):
        with self.assertRaises(KeyError):
            self.platform.record_ack(
                Ack(device_id="ghost", version=2, result="applied", applied_at=1.0)
            )


if __name__ == "__main__":
    unittest.main()
