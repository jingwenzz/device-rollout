import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from device_rollout import DesiredAssignment, SimulatedDevice


def make_device(**kwargs):
    ticks = iter(range(1000, 2000))
    kwargs.setdefault("clock", lambda: float(next(ticks)))
    return SimulatedDevice("device-1", 1, {"mode": "v1"}, **kwargs)


class TestHeartbeat(unittest.TestCase):
    def test_heartbeat_reports_current_version(self):
        device = make_device()
        beat = device.heartbeat()
        self.assertIsNotNone(beat)
        self.assertEqual(beat.device_id, "device-1")
        self.assertEqual(beat.reported_version, 1)

    def test_offline_device_sends_no_heartbeat(self):
        device = make_device(online=False)
        self.assertIsNone(device.heartbeat())
        device.come_online()
        self.assertIsNotNone(device.heartbeat())


class TestApply(unittest.TestCase):
    def test_new_version_is_applied_and_acked(self):
        device = make_device()
        ack = device.receive_desired(
            DesiredAssignment(version=2, config={"mode": "v2"}, sequence=1)
        )
        self.assertEqual(ack.result, "applied")
        self.assertEqual(ack.version, 2)
        self.assertIsNone(ack.error)
        self.assertEqual(device.reported_version, 2)
        self.assertEqual(device.current_config, {"mode": "v2"})
        self.assertEqual(device.heartbeat().reported_version, 2)

    def test_duplicate_assignment_is_idempotent(self):
        device = make_device()
        assignment = DesiredAssignment(version=2, config={"mode": "v2"}, sequence=1)
        first = device.receive_desired(assignment)
        second = device.receive_desired(assignment)
        self.assertEqual(first.result, "applied")
        self.assertIsNone(second)
        self.assertEqual(len(device.ack_history), 1)
        self.assertEqual(device.reported_version, 2)

    def test_same_version_with_newer_sequence_still_sends_no_ack(self):
        device = make_device()
        device.receive_desired(
            DesiredAssignment(version=2, config={"mode": "v2"}, sequence=1)
        )
        again = device.receive_desired(
            DesiredAssignment(version=2, config={"mode": "v2"}, sequence=2)
        )
        self.assertIsNone(again)
        self.assertEqual(len(device.ack_history), 1)

    def test_stale_assignment_is_ignored(self):
        device = make_device()
        device.receive_desired(
            DesiredAssignment(version=2, config={"mode": "v2"}, sequence=5)
        )
        stale = device.receive_desired(
            DesiredAssignment(version=3, config={"mode": "v3"}, sequence=3)
        )
        self.assertIsNone(stale)
        self.assertEqual(device.reported_version, 2)
        self.assertEqual(device.current_config, {"mode": "v2"})

    def test_rollback_to_lower_version_with_newer_sequence_is_applied(self):
        device = make_device()
        device.receive_desired(
            DesiredAssignment(version=2, config={"mode": "v2"}, sequence=1)
        )
        ack = device.receive_desired(
            DesiredAssignment(version=1, config={"mode": "v1"}, sequence=2)
        )
        self.assertEqual(ack.result, "applied")
        self.assertEqual(device.reported_version, 1)
        self.assertEqual(device.current_config, {"mode": "v1"})


class TestFailureInjection(unittest.TestCase):
    def test_marked_version_fails_and_keeps_previous_config(self):
        device = make_device(fail_versions={2})
        ack = device.receive_desired(
            DesiredAssignment(version=2, config={"mode": "v2"}, sequence=1)
        )
        self.assertEqual(ack.result, "failed")
        self.assertIn("injected failure", ack.error)
        self.assertEqual(device.reported_version, 1)
        self.assertEqual(device.current_config, {"mode": "v1"})

    def test_fail_next_apply_only_fails_once(self):
        device = make_device()
        device.fail_next_apply()
        first = device.receive_desired(
            DesiredAssignment(version=2, config={"mode": "v2"}, sequence=1)
        )
        second = device.receive_desired(
            DesiredAssignment(version=2, config={"mode": "v2"}, sequence=2)
        )
        self.assertEqual(first.result, "failed")
        self.assertEqual(second.result, "applied")
        self.assertEqual(device.reported_version, 2)

    def test_clearing_failure_lets_retry_succeed(self):
        device = make_device(fail_versions={2})
        device.receive_desired(
            DesiredAssignment(version=2, config={"mode": "v2"}, sequence=1)
        )
        device.clear_version_failure(2)
        ack = device.receive_desired(
            DesiredAssignment(version=2, config={"mode": "v2"}, sequence=2)
        )
        self.assertEqual(ack.result, "applied")


class TestOffline(unittest.TestCase):
    def test_offline_device_does_not_apply_and_recovers_when_online(self):
        device = make_device(online=False)
        skipped = device.receive_desired(
            DesiredAssignment(version=2, config={"mode": "v2"}, sequence=1)
        )
        self.assertIsNone(skipped)
        self.assertEqual(device.reported_version, 1)
        device.come_online()
        ack = device.receive_desired(
            DesiredAssignment(version=2, config={"mode": "v2"}, sequence=1)
        )
        self.assertEqual(ack.result, "applied")
        self.assertEqual(device.reported_version, 2)


if __name__ == "__main__":
    unittest.main()
