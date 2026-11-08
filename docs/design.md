# Device Rollout: Design

## 1. Problem

Sending a command from the backend does not mean a device has applied it.
Devices go offline, messages arrive twice or out of order, and applying a
config can fail. This system manages that gap explicitly: it tracks what
each device should run, what it actually runs, and the progress of moving
a fleet from one config version to the next.

v1 uses simulated devices only. No real hardware, no firmware updates,
no multi-tenancy, and no permissions system. See the project plan for the
full scope boundary.

## 2. Core Concepts

### 2.1 Config document and version

A config is a single JSON document owned by the platform. Every published
config gets a version: a monotonically increasing integer scoped to the
fleet (v1, v2, v3, ...). Versions are never reused and never edited in
place. Publishing a fix means publishing a new version.

Each version stores:

- `version` (integer, primary identity of the config)
- `body` (the JSON document itself)
- `created_at`
- `previous_version` (the version it was published on top of, used by rollback)

Version identity is the integer, not a hash of the body. Two versions with
identical bodies are still different versions, because rollout state is
tracked per version.

### 2.2 Desired vs. reported

Every device has two version fields, and the difference between them is
the heart of the system:

- `desired_version`: the version the platform wants this device to run.
  Written only by the platform, when a device is included in a rollout
  batch or when it reconnects and needs to catch up.
- `reported_version`: the version the device says it is actually running.
  Written only from device messages (heartbeat and ack). The platform
  never sets it directly.

A device is in sync when `desired_version == reported_version`. A device
with `desired_version > reported_version` is pending. A device is never
expected to report a version higher than its desired version; if it does,
that is treated as an anomaly, logged, and not adopted as desired state.

Status is derived, not stored separately, wherever possible:

| Condition | Derived status |
| --- | --- |
| device offline (heartbeat stale) | `offline` |
| desired == reported | `in_sync` |
| desired > reported, no failed ack for desired | `pending` |
| last ack for desired version was a failure | `failed` |

### 2.3 Device registry

One record per simulated device:

- `device_id` (stable string chosen at registration)
- `desired_version`, `reported_version` (as above)
- `last_seen_at` (updated on every heartbeat or ack)
- `last_ack` (most recent ack payload for the current desired version)

Online vs. offline is derived from `last_seen_at` against a heartbeat
timeout. There is no separate online flag to get out of sync.

### 2.4 Heartbeat

Each online device periodically sends a heartbeat containing its
`device_id` and its current `reported_version`. Heartbeats do two jobs:
they keep `last_seen_at` fresh, and they let the platform detect drift
if a device reports a version the platform did not expect.

Heartbeats carry no desired state. The platform response to a heartbeat
may include the device's current `desired_version` so a reconnected
device can discover it is behind (see Section 6).

### 2.5 Ack

After a device attempts to apply a config, it sends exactly one ack per
apply attempt:

- `device_id`
- `version` (the version it attempted)
- `result`: `applied` or `failed`
- `error` (short string, present only when `result` is `failed`)
- `applied_at` (device-side timestamp, informational only)

Semantics:

- An `applied` ack for version V sets `reported_version = V`, but only if
  V still equals the device's current `desired_version` and V is newer
  than the current `reported_version`. Stale acks are recorded in the
  ack log but do not move state (see Section 5).
- A `failed` ack does not change `reported_version`. It marks the device
  as `failed` for that version and counts toward the rollout failure
  threshold (see Section 4).
- Acks are append-only history. Current state is derived from the device
  record; the ack log is the audit trail and the source for rollout
  statistics.

## 3. Sync Loop

The normal path for one device:

1. A rollout includes the device in a batch and sets its
   `desired_version` to the target version.
2. The device learns its desired version (pushed by the platform, or
   returned in a heartbeat response).
3. The device compares desired vs. its local version. If equal, it does
   nothing and sends no ack. If desired is newer, it applies the config.
4. On success it updates its local version and sends an `applied` ack.
   On failure it keeps its old version and sends a `failed` ack.
5. The platform processes the ack and updates `reported_version` and
   rollout progress.

Applying a config on the device is all-or-nothing in v1: the simulated
device validates the whole document first, then swaps it in. A failed
apply leaves the previous config fully intact.

## 4. Rollout and State Machine

A rollout is one attempt to move a set of devices to one target version.
It has a target version, a batch size (count or percentage), a failure
threshold (fraction of attempted devices in the current batch), and a
state:

```
draft -> rolling -> completed
rolling -> paused            (failure threshold hit, or manual pause)
paused -> rolling            (resume after fixing or accepting failures)
paused -> rolled_back        (rollback decided from paused)
rolling -> rolled_back       (rollback decided mid-rollout)
```

State meanings:

- `draft`: created but no device has been assigned the target version yet.
  Editable (target, batch size, threshold).
- `rolling`: batches are being assigned. Each batch sets
  `desired_version` for its devices and waits for acks before the next
  batch is assigned.
- `paused`: no new batches are assigned. Devices already assigned keep
  their desired version and may still ack; acks are still processed.
  Entered automatically when the failure rate in the current batch
  reaches the threshold, or manually.
- `rolled_back`: terminal. Every device that had been assigned the target
  version gets `desired_version` set back to the rollout's
  `previous_version`, and those devices sync back through the normal
  loop. Devices that never left the previous version are untouched.
- `completed`: terminal. Every targeted device reports the target version,
  or was explicitly skipped as offline and later caught up (Section 6).

Batch completion rule: a batch is done when every device in it has acked
(applied or failed) or is offline. The failure rate for the pause check
is `failed / (applied + failed)` within the current batch, evaluated
when the batch completes. Offline devices do not count as failures.

Rollback is a desired-state change, not a special device command. The
platform sets desired back; devices apply the older config through the
same apply and ack path. This works because config bodies for all recent
versions are retained on the platform.

## 5. Duplicates and Out-of-Order Messages

The transport in v1 is simulated and may deliver messages more than once
or out of order. The rules below keep both cases harmless. They apply to
desired-version notifications, apply attempts, and acks.

### 5.1 Duplicates are idempotent

- Receiving the same desired version twice: the device compares versions
  first. If it already runs that version, it does nothing and sends no
  second ack for the duplicate. If it is mid-apply, the duplicate is
  ignored.
- Receiving the same ack twice at the platform: ack processing is keyed
  by (`device_id`, `version`, `result`). A repeat that matches the
  device's current state changes nothing and does not double-count in
  rollout statistics.
- Registration and heartbeat repeats are naturally idempotent: they
  overwrite the same fields with the same values.

The principle: every handler decides from current state and version
comparison, never from "this message looks new."

### 5.2 Older never overwrites newer

- A device ignores a desired version that is lower than its local
  version, except when that lower version is exactly its current
  `desired_version` set by a rollback. Rollback is recognizable because
  the platform is the only writer of desired state: the device accepts
  a lower target only when it arrives as the current desired version
  from the platform, not from a stale in-flight message. Each desired
  assignment carries a rollout-scoped sequence number; the device keeps
  the highest sequence it has seen and ignores older assignments.
- A stale ack (for a version that is no longer the device's desired
  version, or older than its reported version) is logged but does not
  change `reported_version` or device status.
- Version comparison is the single ordering rule: higher version wins
  for reported state, and desired state only moves when the platform
  explicitly moves it.

Known limit, accepted for v1: there is no global clock ordering across
devices, and none is needed. All ordering decisions are per device,
based on that device's versions and assignment sequence.

## 6. Offline Devices and Reconnect

- A device that is offline when its batch is assigned is skipped for
  that batch round and marked `offline`. It does not block the batch
  and does not count toward the failure rate.
- The rollout may reach `completed` for online devices while some
  devices are still behind; those devices remain `pending` in the
  registry view until they catch up.
- On reconnect, the device sends a heartbeat with its
  `reported_version`. The platform replies with the current
  `desired_version`. If desired is newer, the device runs the normal
  sync loop and acks. No special catch-up protocol is needed.
- If a rollback happened while the device was offline, its desired
  version is already the rolled-back version when it reconnects, so it
  simply stays on (or returns to) that version.

## 7. Observability

v1 surfaces state through a CLI or a simple page:

- Rollout view: state, target version, counts by device status
  (in sync, pending, failed, offline), and per-batch progress.
- Device view: one row per device with desired, reported, derived
  status, and last seen time, so a stuck device is visible at a glance.
- Ack log: recent acks with result and error text for failed applies.

No metrics backend or alerting in v1. Printed state from an actual run
is the evidence; no performance numbers are claimed in advance.

## 8. Demo Scenario (target for the final step)

20 simulated devices on config v1. Publish v2 and start a rollout.
Two devices are set to fail applying v2, the failure threshold pauses
the rollout, and the operator then either fixes the failures and resumes
to completion, or rolls back to v1. Throughout, the rollout and device
views show which devices are in sync, pending, failed, or offline. One
device stays offline during the rollout and catches up on reconnect.

## 9. Out of Scope for v1

- Real hardware and firmware (OTA) updates; config only.
- Multi-tenancy, accounts, and permissions.
- A polished frontend; CLI or a minimal page only.
- Config signing, encryption, and schema migration between versions.
- Performance targets; conclusions come only from actual runs.

## 10. Open Decisions

- Transport between platform and simulated devices: in-process calls
  first, HTTP only if the demo needs separate processes. Decided at the
  simulator step, kept behind a thin interface so it can change.
- Storage: SQLite for the registry and config versions, per the plan.
- Batch sizing: support both a fixed count and a percentage, but the
  demo uses a fixed count.
