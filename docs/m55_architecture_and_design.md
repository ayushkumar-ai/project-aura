# PROJECT AURA — MILESTONE 55 ARCHITECTURE & DESIGN SPECIFICATION
## Distributed Execution Scaling & Worker Fleet Coordination
### Production-Grade • Multi-Worker • Monotonic Fencing • Tenant Fairness • Crash Recovery • Audit-Ready

**Document Version:** 1.0.0-PROD-QUALIFIED  
**Status:** ARCHITECTURALLY QUALIFIED — READY FOR IMPLEMENTATION  
**Target Milestone:** M55  
**Authoritative Baselines:**
- M50: `fb8a3cf71ab7a5f044641afd70f1a46f25f29a8c`
- M51: `fdddbe49e28368e658fd8210ae19ce3c7de0859f` (FORENSIC AUDIT PASS)
- M52 Final Frozen Baseline: `9da9b09312d49cb4fa56c0110109d07f3f56697d` (FORENSIC RE-AUDIT PASS)
- M53 Final Frozen Baseline: `aa3dd8b55711d890c1988cde834ac7aa33adbbf4` (FORENSIC AUDIT PASS)
- M54 Final Frozen Baseline: `436c0a85613c73e42c18ccc9837a23cc4f967743` (FORENSIC AUDIT PASS)  
**Branch:** `antigravity-work`

---

## 1. Executive Summary & Architectural Demarcation

Milestone 55 (M55) delivers **Distributed Execution Scaling & Worker Fleet Coordination** for Project AURA. It transforms AURA from a single-process background task execution engine into a resilient, horizontally scalable, multi-worker fleet coordinated through PostgreSQL row-level locks, monotonic fencing tokens, and tenant-fair scheduling.

```
+===================================================================================================+
|                                    AURA WORKER FLEET COORDINATION                                 |
|                                                                                                   |
|   +-------------------------------------------------------------------------------------------+   |
|   | POSTGRESQL 16 AUTHORITATIVE COORDINATION & DURABILITY STORE                              |   |
|   |                                                                                           |   |
|   |  [workers]                [worker_leases]             [tenant_worker_limits]               |   |
|   |   - worker_id              - lease_id (UUID)           - tenant_id (user_id)              |   |
|   |   - instance_id            - resource_type / id        - max_active_tasks                 |   |
|   |   - incarnation_token      - worker_id                 - guaranteed_slots                 |   |
|   |   - status (HEALTHY/...)   - fencing_token (BIGINT)    - active_task_count                |   |
|   |   - last_heartbeat_at      - expires_at / lease_state  - burst_capacity                   |   |
|   |                                                                                           |   |
|   |  [tasks (M52)]             [execution_attempts]        [dead_letter_events (M54)]         |   |
|   +-------------------------------------------------------------------------------------------+   |
|                                          ▲                               ▲                        |
|                     Heartbeat & Renewal  │                               │ Atomic Claim & Fence   |
|                     (every 5.0s)         │                               │ (SKIP LOCKED)          |
|                                          │                               │                        |
|   +──────────────────────────────────────┴─────────+   +─────────────────┴────────────────────+   |
|   | WORKER NODE 1 (wkr_01, Incarnation inc_A)      |   | WORKER NODE 2 (wkr_02, Incarnation inc_B)   |
|   |                                                |   |                                      |   |
|   |  [DistributedFleetWorker]                      |   |  [DistributedFleetWorker]            |   |
|   |   ├── HeartbeatManager (atomic token update)   |   |   ├── HeartbeatManager               |   |
|   |   ├── FairClaimEngine (deficit round-robin)    |   |   ├── FairClaimEngine                |   |
|   |   ├── ExecutionEngine (bounded thread pool)    |   |   ├── ExecutionEngine                |   |
|   |   └── FencingValidator (rejection of zombies)  |   |   └── FencingValidator               |   |
|   +────────────────────────────────────────────────+   +──────────────────────────────────────+   |
|                                          ▲                                                        |
|                                          │ Crash Sweeper & Orphan Recovery                         |
|   +──────────────────────────────────────┴────────────────────────────────────────────────────+   |
|   | FLEET RECOVERY SERVICE (Active on Fleet Coordinators / Background Sweepers)                |   |
|   |   ├── Expired Worker Reaper (last_heartbeat_at < NOW() - 15s -> status = 'expired')       |   |
|   |   ├── Abandoned Lease Sweeper (releases orphaned tasks, marks attempt = 'recovered')      |   |
|   |   └── Tenant Capacity Reconciler (repairs active_task_count drifts)                       |   |
|   +───────────────────────────────────────────────────────────────────────────────────────────+   |
+===================================================================================================+
```

### 1.1 Scope & Non-Goals
- **In Scope:**
  - Durable worker identity, generation tracking, and incarnation tokens.
  - Worker lifecycle management (`starting`, `healthy`, `draining`, `unhealthy`, `stopped`, `expired`).
  - Distributed lease acquisition with row-level locks (`SELECT ... FOR UPDATE SKIP LOCKED`).
  - Monotonic fencing tokens protecting every mutation against stale/zombie workers.
  - Periodic heartbeat and dynamic lease renewal protocol.
  - Crash recovery sweeper for ungraceful node failures and network partitions.
  - Multi-tenant concurrency quotas and deficit round-robin fairness scheduling.
  - Graceful worker draining with in-flight task drain timeouts.
  - Additive schema migration `006_distributed_execution_scaling_and_worker_fleet.sql`.
- **Strict Non-Goals:**
  - External brokers (Redis, RabbitMQ, Kafka) — PostgreSQL is the single source of truth.
  - Distributed consensus frameworks (Raft, Paxos, Zookeeper, etcd).
  - Kubernetes operators or cloud infrastructure autoscalers.
  - Modification of frozen M50–M54 behavior or migrations 001–005.

---

## 2. Worker Lifecycle & State Machine

```
              ┌──────────────────┐
              │     STARTING     │ ──► Registration in DB, UUID assigned, generation logged
              └────────┬─────────┘
                       │ (Initial Heartbeat & Readiness Verified)
                       ▼
              ┌──────────────────┐
        ┌───► │     HEALTHY      │ ◄─── (Missed heartbeats recovered before threshold)
        │     └────────┬─────────┘
        │              │                 │
        │ (Heartbeat   │ (SIGTERM /      │ (Missed Heartbeat > Threshold / Liveness Fail)
        │  Restored)   │  Drain Request) │
        │              ▼                 ▼
        │     ┌──────────────────┐     ┌──────────────────┐
        │     │     DRAINING     │     │    UNHEALTHY     │
        │     └────────┬─────────┘     └────────┬─────────┘
        │              │ (In-Flight Task        │ (Sweeper detects expiry)
        │              │  Drain Complete)       │
        │              ▼                        ▼
        │     ┌──────────────────┐     ┌──────────────────┐
        └──── │     STOPPED      │     │     EXPIRED      │ ──► (Terminal: Leases fenced,
              │    (Terminal)    │     │    (Terminal)    │      tasks recovered)
              └──────────────────┘     └──────────────────┘
```

### 2.1 State Descriptions
1. **`starting`:** Worker is initializing, generating its `incarnation_token`, registering hardware/process metadata, and establishing database connection pools. It does not accept or claim task leases.
2. **`healthy`:** Worker actively emits heartbeats at `heartbeat_interval_seconds` (default 5.0s) and participates in fair task claiming up to its local `concurrency_limit`.
3. **`draining`:** Worker has received a shutdown signal. It rejects new task claims, permits in-flight tasks to complete within `drain_timeout_seconds`, and flushes checkpoints.
4. **`unhealthy`:** Worker has missed one or more heartbeats ($t_{\text{missed}} < \text{threshold}$) or reported internal health check degradation. New claims are disabled.
5. **`stopped` (Terminal):** Worker has completed graceful draining and unregistered cleanly with zero active tasks.
6. **`expired` (Terminal):** Worker has exceeded the dead-worker threshold ($t_{\text{now}} - t_{\text{heartbeat}} > \text{missed\_threshold} \times \text{interval}$). The recovery sweeper fences all active leases held by this worker and marks them for recovery.

---

## 3. Distributed Lease Protocol & Monotonic Fencing

### 3.1 Split-Brain & Zombie Worker Threat Model
In distributed execution, a worker may experience a long garbage-collection pause, process freeze, or network partition. During this pause:
1. The coordination sweeper declares the worker dead and expires its lease.
2. The task is reassigned to a second healthy worker.
3. The original worker unfreezes and attempts to write completion status or state changes.

Without fencing, the zombie worker would overwrite the healthy worker's progress, corrupting durable state.

### 3.2 Monotonic Fencing Contract
Every resource lease maintains a monotonically increasing integer `fencing_token`.
- When a lease is first acquired or reassigned to a new worker, `fencing_token` is incremented:
  $$\text{fencing\_token}_{n+1} = \text{fencing\_token}_n + 1$$
- Every state update (e.g. task progress, checkpoint, completion, failure) **MUST** include the `fencing_token` in the SQL predicate:
  ```sql
  UPDATE tasks
  SET status = :new_status, result = :result, updated_at = CURRENT_TIMESTAMP
  WHERE id = :task_id
    AND id IN (
        SELECT resource_id FROM worker_leases
        WHERE resource_type = 'task'
          AND resource_id = :task_id
          AND fencing_token = :fencing_token
          AND worker_id = :worker_id
          AND incarnation_token = :incarnation_token
          AND expires_at > CURRENT_TIMESTAMP
          AND lease_state = 'active'
    );
  ```
- If zero rows are updated, the worker immediately aborts execution and raises `FencingTokenMismatchError`.

---

## 4. Multi-Tenant Fairness & Concurrency Limiting

### 4.1 Tenant Quota Invariants
Each tenant is provisioned in `tenant_worker_limits`:
- `max_active_tasks`: Hard ceiling on concurrent in-flight tasks across the entire worker fleet.
- `guaranteed_slots`: Guaranteed capacity reserved even under heavy global cluster contention.
- `burst_capacity`: Short-term burst allowance for high-priority automations.
- `active_task_count`: Authoritative live count of tasks currently leased and executing.

### 4.2 Deficit Round-Robin Task Claiming
To prevent a single tenant with 50,000 tasks from monopolizing all worker capacity:
1. Candidate pending tasks are selected using PostgreSQL `FOR UPDATE SKIP LOCKED`.
2. The claim query joins `tenant_worker_limits` and filters out tenants where `active_task_count >= max_active_tasks`.
3. Order of admission is prioritized by lowest tenant capacity utilization ratio:
   $$\text{utilization\_ratio} = \frac{\text{active\_task\_count}}{\max(1, \text{max\_active\_tasks})}$$
4. Atomic lease acquisition increments `tenant_worker_limits.active_task_count += 1` within the same transaction.
5. Task completion/failure/recovery atomically decrements `tenant_worker_limits.active_task_count -= 1`.

---

## 5. PostgreSQL Schema Definition (Migration 006)

```sql
-- M55 — Distributed Execution Scaling & Worker Fleet Coordination

-- 1. Worker Fleet Nodes
CREATE TABLE IF NOT EXISTS workers (
    worker_id VARCHAR(128) PRIMARY KEY,
    instance_id VARCHAR(128) NOT NULL,
    hostname VARCHAR(255) NOT NULL,
    process_id INTEGER NOT NULL,
    incarnation_token VARCHAR(128) NOT NULL UNIQUE,
    generation INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL DEFAULT 'starting',
    capabilities JSONB NOT NULL DEFAULT '["*"]'::jsonb,
    concurrency_limit INTEGER NOT NULL DEFAULT 4,
    active_task_count INTEGER NOT NULL DEFAULT 0,
    heartbeat_interval_seconds FLOAT NOT NULL DEFAULT 5.0,
    missed_heartbeats_threshold INTEGER NOT NULL DEFAULT 3,
    last_heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    draining_since TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_worker_status CHECK (status IN ('starting', 'healthy', 'draining', 'unhealthy', 'stopped', 'expired')),
    CONSTRAINT chk_worker_counts CHECK (active_task_count >= 0 AND concurrency_limit > 0)
);

CREATE INDEX IF NOT EXISTS idx_workers_heartbeat ON workers(status, last_heartbeat_at);

-- 2. Distributed Resource Leases & Monotonic Fencing
CREATE TABLE IF NOT EXISTS worker_leases (
    lease_id VARCHAR(128) PRIMARY KEY,
    resource_type VARCHAR(64) NOT NULL,
    resource_id VARCHAR(128) NOT NULL,
    worker_id VARCHAR(128) NOT NULL REFERENCES workers(worker_id) ON DELETE CASCADE,
    incarnation_token VARCHAR(128) NOT NULL,
    fencing_token BIGINT NOT NULL,
    lease_state VARCHAR(32) NOT NULL DEFAULT 'active',
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    renewed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_lease_state CHECK (lease_state IN ('active', 'renewed', 'released', 'expired', 'fenced')),
    CONSTRAINT uq_resource_lease UNIQUE (resource_type, resource_id)
);

CREATE INDEX IF NOT EXISTS idx_worker_leases_expiry ON worker_leases(lease_state, expires_at);
CREATE INDEX IF NOT EXISTS idx_worker_leases_worker ON worker_leases(worker_id, incarnation_token);

-- 3. Execution Attempts & Audit Ledger
CREATE TABLE IF NOT EXISTS execution_attempts (
    attempt_id VARCHAR(128) PRIMARY KEY,
    resource_type VARCHAR(64) NOT NULL DEFAULT 'task',
    resource_id VARCHAR(128) NOT NULL,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    worker_id VARCHAR(128) NOT NULL REFERENCES workers(worker_id) ON DELETE CASCADE,
    incarnation_token VARCHAR(128) NOT NULL,
    fencing_token BIGINT NOT NULL,
    attempt_number INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ NULL,
    error_detail TEXT NOT NULL DEFAULT '',
    CONSTRAINT chk_attempt_status CHECK (status IN ('running', 'completed', 'failed', 'timed_out', 'fenced', 'recovered'))
);

CREATE INDEX IF NOT EXISTS idx_execution_attempts_resource ON execution_attempts(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_execution_attempts_tenant ON execution_attempts(tenant_id, status);

-- 4. Tenant Concurrency Limits & Live Capacities
CREATE TABLE IF NOT EXISTS tenant_worker_limits (
    tenant_id VARCHAR(128) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    max_active_tasks INTEGER NOT NULL DEFAULT 10,
    guaranteed_slots INTEGER NOT NULL DEFAULT 2,
    burst_capacity INTEGER NOT NULL DEFAULT 20,
    active_task_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_tenant_worker_counts CHECK (active_task_count >= 0 AND max_active_tasks > 0)
);

CREATE INDEX IF NOT EXISTS idx_tenant_worker_limits_utilization ON tenant_worker_limits(active_task_count, max_active_tasks);
```

---

## 6. Formal Architectural Invariants (M55-F01 through M55-F35)

| Invariant ID | Title | Mathematical / Operational Rule |
|---|---|---|
| **M55-F01** | **Unique Worker Incarnation** | Every worker process invocation MUST generate a cryptographically unique `incarnation_token`. No two processes may share the same token. |
| **M55-F02** | **Monotonic Generation** | Node generation MUST strictly increment upon worker restart: $\text{gen}_{t+1} > \text{gen}_t$. |
| **M55-F03** | **Single Active Lease Owner** | Exactly one active lease row exists per `(resource_type, resource_id)` enforced by database UNIQUE constraint. |
| **M55-F04** | **Strict Fencing Token Progression** | Fencing token MUST monotonically increase: $\text{fencing\_token}_{k+1} > \text{fencing\_token}_k$. Reassigned leases always receive a strictly higher token. |
| **M55-F05** | **Zombie Write Rejection** | Any state mutation where `fencing_token != current_lease.fencing_token` or `incarnation_token != current_lease.incarnation_token` MUST update 0 rows and raise `FencingTokenMismatchError`. |
| **M55-F06** | **Atomic Lease Claim** | Task claims MUST utilize `SELECT ... FOR UPDATE SKIP LOCKED` combined with atomic insertion into `worker_leases` and `execution_attempts`. |
| **M55-F07** | **Healthy Claim Gate** | A worker with `status NOT IN ('healthy')` MUST NOT claim or be assigned any new tasks. |
| **M55-F08** | **Worker Concurrency Upper Bound** | A worker MUST NOT execute more concurrent tasks than its local `concurrency_limit`: $\text{active\_tasks} \le \text{concurrency\_limit}$. |
| **M55-F09** | **Tenant Quota Enforcement** | A worker MUST NOT claim a task for tenant $T$ if $T$'s $\text{active\_task\_count} \ge \text{max\_active\_tasks}$. |
| **M55-F10** | **Deficit Fairness Admission** | Under multi-tenant contention, tasks belonging to tenants with lowest utilization ratio $\frac{\text{active}}{\text{max}}$ MUST be claimed before higher-utilization tenants. |
| **M55-F11** | **Heartbeat Cadence** | Active workers MUST emit heartbeats at intervals $\le \text{heartbeat\_interval\_seconds}$ (default 5.0s). |
| **M55-F12** | **Missed Heartbeat Threshold** | A worker missing $\ge \text{missed\_heartbeats\_threshold}$ consecutive heartbeats is transitioned to `unhealthy`. |
| **M55-F13** | **Dead Worker Expiry** | Workers with $t_{\text{now}} - t_{\text{heartbeat}} > \text{threshold} \times \text{interval}$ MUST be transitioned to `expired` by the sweeper. |
| **M55-F14** | **Orphan Lease Fencing** | Leases held by `expired` workers MUST be set to `fenced` or `expired` before task recovery. |
| **M55-F15** | **Deterministic Task Requeuing** | Stale leased tasks with $\text{retry\_count} < \text{max\_retries}$ MUST be reset to `pending` with $\text{retry\_count} = \text{retry\_count} + 1$. |
| **M55-F16** | **Dead-Letter Terminalization** | Stale tasks with exhausted retries MUST be transitioned to `failed` and logged to dead-letter audit ledger. |
| **M55-F17** | **Zero Counter Drift** | Every task claim MUST increment tenant $\text{active\_task\_count}$ by 1; every terminal transition (completed/failed/cancelled/recovered) MUST decrement by exactly 1. |
| **M55-F18** | **Non-Negative Counters** | Database CHECK constraint `active_task_count >= 0` MUST prevent underflow under all concurrency edge cases. |
| **M55-F19** | **Graceful Drain Non-Acceptance** | When transitioning to `draining`, the worker MUST immediately stop polling for new work. |
| **M55-F20** | **Drain Timeout Bounding** | Draining workers MUST force-cancel lingering tasks after `drain_timeout_seconds` (default 30.0s). |
| **M55-F21** | **Clean Unregister on Stop** | Gracefully terminated workers with 0 active tasks MUST transition to `stopped` and release all transient resources. |
| **M55-F22** | **Crash Recovery Idempotency** | The orphan lease sweeper MUST be safe to run concurrently on multiple coordinator nodes without duplicate requeuing. |
| **M55-F23** | **Execution Attempt Immutability** | Finished `execution_attempts` rows (`completed`, `failed`, `fenced`, `recovered`) MUST never be modified. |
| **M55-F24** | **Preserved M52 Task Semantics** | Task lifecycle states (`pending`, `running`, `waiting_approval`, `completed`, `failed`, `cancelled`) remain 100% backward compatible. |
| **M55-F25** | **Preserved M53 Scheduler Authority** | M53 Autonomous Supervisor remains the sole scheduler of *when* automated tasks are generated; M55 coordinates *where* they execute. |
| **M55-F26** | **Preserved M54 Webhook Gateway** | Outbound delivery worker and inbound webhook ingress utilize M55 lease coordination without altering M54 cryptographic contracts. |
| **M55-F27** | **Cross-Tenant Isolation** | Workers executing tenant $A$'s tasks MUST NOT read, mutate, or leak memory/context of tenant $B$. |
| **M55-F28** | **Low-Cardinality Metrics** | Fleet telemetry labels MUST NOT include unbounded identifiers (`worker_id`, `task_id`, `user_id`); only bounded status strings (`healthy`, `draining`, `fenced`). |
| **M55-F29** | **Fail-Closed Repository Wiring** | When `AURA_PERSISTENCE_BACKEND=postgres`, failure to connect to PostgreSQL or initialize fleet repositories MUST fail closed. |
| **M55-F30** | **In-Memory Fleet Parity** | `InMemoryFleetRepository` MUST implement identical locking, fencing, lease expiration, and tenant fairness semantics for unit testing. |
| **M55-F31** | **Lease Renewal Atomicity** | Lease renewals MUST update `expires_at` only if the lease has not been fenced or expired by another worker. |
| **M55-F32** | **Cancellation Propagation** | A cancellation signal issued to a running task MUST propagate to the executing worker's `CancellationToken` within 500ms. |
| **M55-F33** | **Hardware Metadata Recording** | Registered workers MUST log hostname, OS PID, CPU core allocation, and configured capability tags on startup. |
| **M55-F34** | **Reconciliation Self-Healing** | Periodic reconciliation sweeper MUST audit and repair any discrepancy between live active leases and `tenant_worker_limits.active_task_count`. |
| **M55-F35** | **Zero-Downtime Schema Migration** | Schema migration `006` MUST be strictly additive, using `IF NOT EXISTS` and backwards-compatible table defaults. |

---

## 7. Adversarial Test Matrix (100% Traceability)

The M55 test suite covers unit, integration, concurrency, split-brain, and crash-recovery scenarios across PostgreSQL and in-memory backends:

- `test_m55_worker_lifecycle_unit.py`: Worker state transitions, incarnation generation, heartbeat timing, missed heartbeat degradation, graceful draining, unregister.
- `test_m55_fencing_and_leases_unit.py`: Monotonic fencing token increments, zombie write rejection, lease expiry, renewal atomicity, double-claim rejection.
- `test_m55_tenant_fairness_unit.py`: Multi-tenant concurrency limits, heavy tenant throttling, deficit round-robin starvation prevention, capacity release.
- `test_m55_crash_recovery_unit.py`: Orphan lease sweeping, dead worker expiry, task requeuing, attempt state tracking, counter self-healing.
- `test_m55_postgres_fleet_integration.py`: Live PostgreSQL row-level locking (`FOR UPDATE SKIP LOCKED`), concurrent worker races, cross-process fencing.
- `test_m55_fleet_coordination_integration.py`: End-to-end multi-worker fleet coordination, task execution, graceful draining, server lifecycle integration.

---

**AUTHORITATIVE ARCHITECTURAL SPECIFICATION APPROVED FOR PROJECT AURA MILESTONE 55.**
