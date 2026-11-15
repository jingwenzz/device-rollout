import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from device_rollout import Heartbeat, Platform


def make_platform(**kwargs):
    # A fixed platform clock keeps last seen and status checks exact.
    kwargs.setdefault("clock", lambda: 1000.0)
    kwargs.setdefault("heartbeat_timeout", 30.0)
    return Platform(":memory:", **kwargs)


class TestConfigStore(unittest.TestCase):
    def test_first_publish_is_version_1_without_previous(self):
        platform = make_platform()
        config = platform.publish_config({"mode": "v1"})
        self.assertEqual(config.version, 1)
        self.assertIsNone(config.previous_version)
        self.assertEqual(config.body, {"mode": "v1"})
        self.assertEqual(config.created_at, 1000.0)
        self.assertEqual(platform.latest_version(), 1)

    def test_versions_increase_and_link_to_previous(self):
        platform = make_platform()
        platform.publish_config({"mode": "v1"})
        second = platform.publish_config({"mode": "v2", "level": 3})
        self.assertEqual(second.version, 2)
        self.assertEqual(second.previous_version, 1)
        stored = platform.get_config(2)
        self.assertEqual(stored.body, {"mode": "v2", "level": 3})
        self.assertEqual([c.version for c in platform.list_versions()], [1, 2])

    def test_stored_body_is_not_changed_by_caller_mutation(self):
        platform = make_platform()
        body = {"mode": "v1"}
        platform.publish_config(body)
        body["mode"] = "tampered"
        returned = platform.get_config(1)
        returned.body["mode"] = "tampered again"
        self.assertEqual(platform.get_config(1).body, {"mode": "v1"})

    def test_unknown_version_and_non_object_body_are_rejected(self):
        platform = make_platform()
        with self.assertRaises(KeyError):
            platform.get_config(99)
        with self.assertRaises(TypeError):
            platform.publish_config(["not", "an", "object"])
        self.assertIsNone(platform.latest_version())


class TestRegistry(unittest.TestCase):
    def test_register_starts_in_sync_and_never_seen(self):
        platform = make_platform()
        record = platform.register_device("device-1", initial_version=1)
        self.assertEqual(record.desired_version, 1)
        self.assertEqual(record.reported_version, 1)
        self.assertIsNone(record.last_seen_at)
        self.assertEqual(platform.device_status("device-1"), "offline")

    def test_registration_repeat_does_not_reset_state(self):
        platform = make_platform()
        platform.publish_config({"mode": "v1"})
        platform.publish_config({"mode": "v2"})
        platform.register_device("device-1", initial_version=1)
        platform.set_desired_version("device-1", 2)
        again = platform.register_device("device-1", initial_version=1)
        self.assertEqual(again.desired_version, 2)
        self.assertEqual(again.reported_version, 1)

    def test_empty_id_and_unknown_device_are_rejected(self):
        platform = make_platform()
        with self.assertRaises(ValueError):
            platform.register_device("")
        with self.assertRaises(KeyError):
            platform.get_device("ghost")

    def test_heartbeat_updates_reported_and_last_seen_only(self):
        platform = make_platform()
        platform.register_device("device-1", initial_version=1)
        record = platform.record_heartbeat(
            Heartbeat(device_id="device-1", reported_version=1, sent_at=5.0)
        )
        # Last seen is stamped by the platform clock, not the device clock.
        self.assertEqual(record.last_seen_at, 1000.0)
        self.assertEqual(record.reported_version, 1)
        self.assertEqual(record.desired_version, 1)
        self.assertEqual(platform.device_status("device-1"), "in_sync")

    def test_heartbeat_for_unknown_device_is_rejected(self):
        platform = make_platform()
        with self.assertRaises(KeyError):
            platform.record_heartbeat(
                Heartbeat(device_id="ghost", reported_version=1, sent_at=5.0)
            )

    def test_list_devices_is_sorted_by_id(self):
        platform = make_platform()
        platform.register_device("device-b", initial_version=1)
        platform.register_device("device-a", initial_version=1)
        self.assertEqual(
            [d.device_id for d in platform.list_devices()],
            ["device-a", "device-b"],
        )


class TestDesiredAndStatus(unittest.TestCase):
    def setUp(self):
        self.platform = make_platform()
        self.platform.publish_config({"mode": "v1"})
        self.platform.publish_config({"mode": "v2"})
        self.platform.register_device("device-1", initial_version=1)
        self.platform.record_heartbeat(
            Heartbeat(device_id="device-1", reported_version=1, sent_at=5.0)
        )

    def test_set_desired_moves_intent_but_not_reported(self):
        record = self.platform.set_desired_version("device-1", 2)
        self.assertEqual(record.desired_version, 2)
        self.assertEqual(record.reported_version, 1)
        self.assertEqual(self.platform.device_status("device-1"), "pending")

    def test_set_desired_requires_published_version_and_known_device(self):
        with self.assertRaises(KeyError):
            self.platform.set_desired_version("device-1", 99)
        with self.assertRaises(KeyError):
            self.platform.set_desired_version("ghost", 2)

    def test_status_turns_offline_after_heartbeat_timeout(self):
        self.assertEqual(
            self.platform.device_status("device-1", at=1030.0), "in_sync"
        )
        self.assertEqual(
            self.platform.device_status("device-1", at=1030.1), "offline"
        )


class TestPersistence(unittest.TestCase):
    def test_state_survives_reopening_the_database_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "platform.db"
            with Platform(db_path, clock=lambda: 1000.0) as platform:
                platform.publish_config({"mode": "v1"})
                platform.publish_config({"mode": "v2"})
                platform.register_device("device-1", initial_version=1)
                platform.set_desired_version("device-1", 2)
            with Platform(db_path, clock=lambda: 1000.0) as reopened:
                self.assertEqual(reopened.latest_version(), 2)
                self.assertEqual(reopened.get_config(2).body, {"mode": "v2"})
                record = reopened.get_device("device-1")
                self.assertEqual(record.desired_version, 2)
                self.assertEqual(record.reported_version, 1)


if __name__ == "__main__":
    unittest.main()
