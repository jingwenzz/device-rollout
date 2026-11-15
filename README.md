# device-rollout

Config version management and staged rollout for a simulated device fleet.

The platform tracks what each device should run (desired) and what it
actually runs (reported). Devices apply a new config version all or
nothing and answer with an ack, so a sent command is never confused
with an applied one. See `docs/design.md` for the full design: the
desired and reported model, the ack semantics, the rollout state
machine, and how duplicate and out of order messages are handled.

v1 uses simulated devices only. No real hardware, no firmware updates,
no multi tenancy, and no permissions system.

## Current state

The device simulator is in place. A simulated device can start, send
heartbeats carrying its current version, receive a desired version,
apply it and ack, or fail the apply when a failure is injected.

The platform backend skeleton is in place too. It stores config
versions and the device registry in SQLite: every device record holds
its desired and reported version, desired is written only by the
platform, reported only from device heartbeats, and a device status
(offline, in sync, pending) is derived from the record and the
heartbeat timeout instead of being stored.

The sync loop and the staged rollout engine are not built yet; they
arrive in later steps of the project plan.

## Run the tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The tests use only the standard library. They cover heartbeats,
successful applies, duplicate and stale assignments, rollback to a
lower version carried by a newer sequence, injected failures, and
offline behaviour, plus the platform side: config publishing and
version links, registry behaviour, desired versus reported queries,
derived status, and persistence across reopening the database.

## Run the demos

```bash
PYTHONPATH=src python3 examples/demo_device.py
PYTHONPATH=src python3 examples/demo_platform.py
```

The device demo walks one healthy device and one device that is set
to fail version 2 through an assignment, a duplicate delivery, and a
retry after the failure is cleared.

The platform demo publishes two config versions, registers three
devices, and sets a new desired version for two of them. It shows the
gap the rest of the system exists to close: desired moves on intent
alone, reported only moves when a device actually applies.
