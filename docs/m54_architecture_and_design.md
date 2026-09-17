# PROJECT AURA — MILESTONE 54 ARCHITECTURE & DESIGN SPECIFICATION
## Enterprise Webhooks & Inbound/Outbound Event Gateway
### Final Pre-Implementation Hardening: Authoritative Tenant Capacity Release Invariant, Unified Dead-Letter Ledger, Replay Immutability, Exact Inbound Attempt Semantics, M53 Maintenance Boundary & 41 Formal Invariants

**Document Version:** 1.5.0-FINAL-PROD-QUALIFIED  
**Status:** ARCHITECTURALLY QUALIFIED — READY FOR IMPLEMENTATION  
**Target Milestone:** M54  
**Authoritative Baselines:**
- M50: `fb8a3cf71ab7a5f044641afd70f1a46f25f29a8c`
- M51: `fdddbe49e28368e658fd8210ae19ce3c7de0859f` (FORENSIC AUDIT PASS)
- M52 Final Frozen Baseline: `9da9b09312d49cb4fa56c0110109d07f3f56697d` (FORENSIC RE-AUDIT PASS)
- M53 Final Frozen Baseline: `aa3dd8b55711d890c1988cde834ac7aa33adbbf4` (FORENSIC AUDIT PASS)  
**Branch:** `antigravity-work`

---

## Revision 1.5 — Final Surgical Corrections & Architectural Freeze

| Ref # | Area | Surgical Revision & Hardening Summary |
|---|---|---|
| **REV-50** | **Tenant Capacity Release Invariant (M54-F34)** | Formally established that `tenant_webhook_capacities.in_flight_count` is the sole authoritative operational counter during normal operation, mathematically satisfying $\text{in\_flight\_count} = \text{COUNT}(\text{inbound\_events WHERE user\_id} = :uid \land \text{status} \in \{\text{'accepted'}, \text{'processing'}, \text{'failed'}\})$. Guaranteed exactly-once capacity release: a decrement occurs if and only if a transition from non-terminal to terminal (`processed`, `dead_lettered`) succeeds. Repeated terminalization or stale worker signals update zero rows and trigger zero secondary decrements. Recovery reconciliation serializes on the same tenant row lock (`FOR UPDATE`), eliminating admission/reconciliation races. |
| **REV-51** | **Unified Dead-Letter Ledger Contract (M54-F36)** | Standardized `dead_letter_events` as the unified immutable historical dead-letter ledger. Added explicit discriminator `dead_letter_type VARCHAR(32)` (`'inbound'` vs `'outbound'`) and explicit source references (`inbound_event_id`, `delivery_id`) protected by database CHECK constraints ensuring exactly one source reference is populated matching the discriminator. |
| **REV-52** | **Final Replay Immutability Contract (M54-F36)** | Formally solidified that historical `dead_letter_events` rows are 100% write-once and permanently immutable. Replays create a new delivery record with identity `del_<uuid4>`, `causation_id`, and `replayed_from_dead_letter_id`, storing replay audit metadata strictly in the dedicated `dead_letter_replays` relation. Historical DLQ records remain byte-identical before and after replays. |
| **REV-53** | **Exact Test-Matrix Qualification (N=117)** | Formally clarified that the **117 planned adversarial scenarios** are required implementation-level validation specifications (100% planned invariant-to-scenario mapping), distinct from executed test evidence. Added scenarios `T-RESOURCE-07`, `T-RESOURCE-08`, `T-REPLAY-06`, `T-REPLAY-07`, and `T-MATRIX-04` with verified category arithmetic ($12 + 7 + 6 + 19 + 18 + 13 + 7 + 9 + 11 + 11 + 4 = 117$). |

---

## 1. Executive Summary & Architectural Demarcation

Milestone 54 (M54) establishes Project AURA's enterprise event gateway, providing secure, durable, cryptographically verified, and tenant-isolated event ingress and egress.

```
+-------------------------------------------------------------------------------------------------+
| INBOUND PIPELINE (External Systems -> AURA)                                                     |
|                                                                                                 |
|   External System ──► POST /v1/webhooks/{endpoint_id}                                           |
|                         │                                                                       |
|                         ├─► 1. Streaming Request Body Cap (<= 1MB, early socket abort)          |
|                         ├─► 2. Endpoint Lookup (404 if missing, disabled, or cross-tenant)       |
|                         ├─► 3. Constant-Time Dual-Key HMAC Verification (X-AURA-Signature-256)   |
|                         ├─► 4. Replay Tolerance Check (|t_req - t_server| <= 300s)              |
|                         ├─► 5. Wire SHA-256 Hash & Provider ID Collision Check                  |
|                         │     ├── Match ID + Match Hash ──► Return Cached HTTP 202 (Duplicate)  |
|                         │     └── Match ID + Differ Hash ──► Reject HTTP 409 Conflict           |
|                         ├─► 6. Atomic Tenant Capacity Check & Lock (tenant_webhook_capacities)  |
|                         │     ├── in_flight_count >= 100 ──► Reject HTTP 429 Too Many Requests  |
|                         │     └── in_flight_count < 100  ──► in_flight_count += 1               |
|                         ├─► 7. INSERT INTO inbound_events (status = 'accepted', attempts = 0)   |
|                         │                                                                       |
|                         ├─► HTTP 202 Accepted (Immediate ACK with server-generated event_id)    |
|                         │                                                                       |
|                         └─► Asynchronous Dispatch Worker Loop:                                  |
|                               ├── Claim lease (attempts += 1, status = 'processing')            |
|                               ├── Dispatch to M52 Task (idempotency_key = m54_inbound_{id})     |
|                               └── Dispatch to M53 Automation (slot = event_id, tenant quota)    |
+-------------------------------------------------------------------------------------------------+
| OUTBOUND PIPELINE (AURA -> External Systems)                                                    |
|                                                                                                 |
|   Internal AURA Event Emitted                                                                   |
|         │                                                                                       |
|         ▼                                                                                       |
|   Subscription Matcher ──► INSERT INTO event_deliveries (status = 'pending')                    |
|                                 │                                                               |
|                                 ▼                                                               |
|   WebhookDeliveryWorker ──► SELECT ... FOR UPDATE SKIP LOCKED (Lease Fenced)                    |
|                                 │                                                               |
|                                 ├─► 1. Port Validation (default 443, allowed range 443..8443)   |
|                                 ├─► 2. Multi-IP SSRF Validation (ALL candidate IPs must be safe)|
|                                 ├─► 3. Direct Socket Connection via PinnedIPTransport           |
|                                 │     (Bypasses all proxies; TLS SNI = original_hostname)       |
|                                 ├─► 4. Sign Payload (X-AURA-Signature-256, Idempotency-Key)    |
|                                 ├─► 5. Execute HTTPS POST (Bounded timeout = 10.0s)             |
|                                 │                                                               |
|                                 ├─► 2xx OK ──► status = 'delivered' (Terminal)                  |
|                                 ├─► 3xx / 4xx (non-408/409/429) / SSRF ──► status = 'failed'    |
|                                 └─► 5xx / 408 / 409 / 429 / Timeout ──► status = 'retrying'    |
|                                       (Clamped Retry-After or Exponential Backoff + Full Jitter)|
|                                       (Max 5 attempts -> dead_letter_events)                    |
+-------------------------------------------------------------------------------------------------+
```

---

## 2. Inbound Durable State Machine, Deduplication & Retries

### 2.1 State Model (5 Persisted Durable States)
The database column `inbound_events.status` strictly enforces 5 durable lifecycle states:

```
              ┌────────────────┐
              │    ACCEPTED    │ ──► Durably committed; HTTP 202 returned to caller (attempts = 0)
              └───────┬────────┘
                      │ (Dispatcher Worker Claims via Lease: attempts += 1)
                      ▼
              ┌────────────────┐
              │   PROCESSING   │
              └───────┬────────┘
         ┌────────────┴─────────────┐
         │ (Task/Auto Admitted)     │ (Downstream Handler Transient Error)
         ▼                          ▼
   ┌───────────┐              ┌───────────┐
   │ PROCESSED │              │  FAILED   │ ──► (Retry attempts < max_attempts) ──► PROCESSING
   │(Terminal) │              │           │ ──► (Attempts >= 3 exhausted)       ──► DEAD_LETTERED
   └───────────┘              └───────────┘                                        (Terminal)
```

1. **`accepted`:** Event signature, timestamp, body cap, and tenant capacity validated; durably committed to PostgreSQL before returning HTTP 202. Initialized with `attempts = 0`, `max_attempts = 3`.
2. **`processing`:** Dispatcher worker has leased the event record to route downstream to M52 tasks or M53 automations. Lease claim atomically increments `attempts = attempts + 1`.
3. **`processed` (Terminal):** Event has successfully materialized an M52 task or M53 automation admission. Capacity slot in `tenant_webhook_capacities` is atomically released. Immutable.
4. **`failed`:** Downstream dispatch encountered a transient error; occupies capacity slot and is eligible for bounded background retry up to `max_attempts` (default 3) when `attempts < max_attempts`.
5. **`dead_lettered` (Terminal):** Maximum dispatch retries exhausted (`attempts >= max_attempts`) or payload malformed for routing. Emits record to `dead_letter_events (dead_letter_type = 'inbound')`. Capacity slot in `tenant_webhook_capacities` is atomically released.

*Ingress Response Outcome Rule:* `duplicate` is **NOT** a mutated state in the database. When an incoming request matches an existing `(endpoint_id, provider_event_id)` with identical payload hash, the server immediately returns HTTP 202 Accepted with body `{"status": "accepted", "event_id": "<existing_id>", "duplicate": true}` without altering the existing record's state.

### 2.2 Mathematically Exact Inbound Attempt Semantics (M54-F37)
- **Definition:** `attempts` is the exact integer count of dispatch executions already initiated by a worker.
- **State Progression:**
  - On Ingress: `attempts = 0`.
  - 1st Dispatch Start: Worker claims lease $\to$ `attempts = 1`, `status = 'processing'`. If dispatch fails $\to$ `status = 'failed'`.
  - 2nd Dispatch Start: Worker claims lease $\to$ `attempts = 2`, `status = 'processing'`. If dispatch fails $\to$ `status = 'failed'`.
  - 3rd Dispatch Start: Worker claims lease $\to$ `attempts = 3`, `status = 'processing'`. If dispatch fails $\to$ `status = 'dead_lettered'`.
  - **Hard Bound:** `attempts` never exceeds `max_attempts` (3). No 4th dispatch attempt can ever start.
- **Crash Recovery Rule:** If a worker crashes after incrementing `attempts` but before recording downstream task creation, the lease expires after 60s. The reconciler checks whether an M52 task exists with `idempotency_key = f"m54_inbound_{event.id}"`. If found, it links `task_id` and marks `status = 'processed'`. If not found, the event remains at its current `attempts` count and can be retried only if `attempts < max_attempts`, else transitions directly to `dead_lettered`.

### 2.3 Authoritative Payload Hash & Collision Defense (M54-F35)
- **Canonical Payload Hash Definition:** `canonical_payload_bytes = raw_request_body` (the exact byte sequence received over the HTTP wire and verified by HMAC signature) hashed via SHA-256 $\to$ `payload_sha256`.
- Persisted in PostgreSQL `inbound_events.payload_sha256`.

```
Incoming Request at /v1/webhooks/{endpoint_id}
                   │
                   ▼
     Compute: incoming_payload_hash = SHA-256(raw_request_body)
                   │
                   ▼
     Does provider_event_id exist in request headers/body?
         ├── YES ──► candidate_key = provider_event_id
         └── NO  ──► candidate_key = f"sha256_{incoming_payload_hash}_{int(now // 900) * 900}"
                   │
                   ▼
     Query DB: SELECT id, payload_sha256, status FROM inbound_events 
               WHERE endpoint_id = :endpoint_id AND provider_event_id = :candidate_key
                   │
     ┌─────────────┴───────────────────────────┐
     │ Found Record?                           │
     ▼                                         ▼
   [ NO ]                                    [ YES ]
     │                                         │
     ▼                                         ├── IF incoming_payload_hash == stored_payload_hash:
   (Proceed to Capacity Check                     │   Return HTTP 202 Accepted {"duplicate": true, "event_id": id}
    & Inbound Insertion)                       │
                                               └── IF incoming_payload_hash != stored_payload_hash:
                                                   REJECT immediately with HTTP 409 Conflict
                                                   Emit SecurityEventType.WEBHOOK_PAYLOAD_COLLISION_REJECTED
                                                   Create ZERO downstream tasks; Create ZERO M53 triggers
```

- **Absent ID Deduplication Window:** Legitimate identical payloads occurring $> 15\text{ minutes}$ apart are accepted as distinct events because `time_bucket_15min` advances, preventing permanent false-positive deduplication across days/weeks.

### 2.4 Authoritative Tenant Capacity & Exactly-Once Release Invariant (M54-F34)
- **Operational Counter Invariant:** During normal operation, `tenant_webhook_capacities.in_flight_count` is the sole authoritative operational counter and satisfies:
  $$\text{in\_flight\_count} = \text{COUNT}(\text{inbound\_events WHERE user\_id} = :user\_id \land \text{status} \in \{\text{'accepted'}, \text{'processing'}, \text{'failed'}\})$$
- **Atomic Reservation Protocol:**
  Admission concurrency is serialized via PostgreSQL row locks on `tenant_webhook_capacities`:
  ```sql
  BEGIN;
  -- 1. Acquire exclusive row lock on tenant capacity record
  SELECT in_flight_count, max_capacity 
  FROM tenant_webhook_capacities 
  WHERE user_id = :user_id 
  FOR UPDATE;

  -- 2. Evaluate capacity constraint
  IF in_flight_count >= max_capacity THEN
      ROLLBACK;
      -- Return HTTP 429 Too Many Requests (tenant_in_flight_capacity_exceeded)
  ELSE
      -- 3. Atomically reserve slot and insert accepted event
      UPDATE tenant_webhook_capacities 
      SET in_flight_count = in_flight_count + 1, updated_at = CURRENT_TIMESTAMP 
      WHERE user_id = :user_id;

      INSERT INTO inbound_events (
          id, endpoint_id, user_id, provider_event_id, payload_sha256,
          event_type, status, attempts, max_attempts, next_attempt_at,
          payload, headers, signature
      ) VALUES (
          :id, :endpoint_id, :user_id, :candidate_key, :incoming_payload_hash,
          :event_type, 'accepted', 0, 3, CURRENT_TIMESTAMP,
          :payload, :headers, :signature
      );
      COMMIT;
      -- Return HTTP 202 Accepted
  END IF;
  ```
- **Exactly-Once Release Protocol:**
  When an inbound event transitions to a terminal state (`processed` or `dead_lettered`), capacity release is strictly conditional on successful state mutation from a non-terminal state:
  ```sql
  BEGIN;
  -- 1. Atomically update status from non-terminal to terminal
  UPDATE inbound_events 
  SET status = :terminal_status, processed_at = CURRENT_TIMESTAMP 
  WHERE id = :event_id 
    AND status IN ('accepted', 'processing', 'failed')
    AND (lease_token = :worker_lease_token OR :worker_lease_token IS NULL);

  -- 2. Exactly-once release check
  IF FOUND THEN
      UPDATE tenant_webhook_capacities 
      SET in_flight_count = GREATEST(0, in_flight_count - 1), updated_at = CURRENT_TIMESTAMP 
      WHERE user_id = :user_id;
  END IF;
  COMMIT;
  ```
  - **Terminal-Transition Invariant:** A given `inbound_event` may release exactly one capacity reservation. Repeated terminalization attempts, stale worker updates, duplicate completion signals, or reconciler retries match zero rows (`FOUND = FALSE`) and produce zero secondary decrements.
- **Reconciliation Protocol (Crash Repair Only):**
  Startup or periodic reconciliation is a repair mechanism for crash inconsistency, not a secondary admission path:
  ```sql
  BEGIN;
  SELECT user_id, in_flight_count, max_capacity 
  FROM tenant_webhook_capacities 
  WHERE user_id = :user_id 
  FOR UPDATE;

  SELECT COUNT(*) INTO actual_count 
  FROM inbound_events 
  WHERE user_id = :user_id AND status IN ('accepted', 'processing', 'failed');

  IF in_flight_count != actual_count THEN
      UPDATE tenant_webhook_capacities 
      SET in_flight_count = actual_count, updated_at = CURRENT_TIMESTAMP 
      WHERE user_id = :user_id;
  END IF;
  COMMIT;
  ```
  Because reconciliation locks `tenant_webhook_capacities` with `FOR UPDATE`, admission, release, and reconciliation are strictly serialized.

---

## 3. Cryptographic Lineage Protocol & Event Loop Defense

### 3.1 Trust Demarcation
External webhook request bodies and headers are **inherently untrusted**. AURA never accepts raw client-asserted `user_id`, `event_id`, `causation_id`, or `lineage` arrays.

### 3.2 Cryptographically Verifiable AURA Lineage Protocol
When AURA emits an outbound webhook that might later return as an inbound callback (e.g. multi-hop integrations), AURA signs its own event governance context:

1. **Outbound Lineage Header:** AURA generates:
   ```
   X-AURA-Lineage-Token: v1.<base64(json_lineage_context)>.<hmac_sha256(tenant_master_key, json_lineage_context)>
   ```
   where `json_lineage_context` contains `{"user_id": user_id, "root_event_id": root_id, "causation_id": parent_id, "depth": depth}`.
2. **Inbound Lineage Ingestion:**
   - If `X-AURA-Lineage-Token` is present and cryptographically verified against the tenant's master key: AURA trusts the causation chain and increments `depth = depth + 1`.
   - If `X-AURA-Lineage-Token` is missing, invalid, or forged: AURA **creates a new root causation chain** (`causation_id = NULL`, `lineage = []`, `depth = 1`). External callers cannot spoof lineage.
3. **Loop Detection Invariant (M54-F20):**
   $$\text{depth} > 3 \quad \lor \quad \text{root\_event\_id} \in \text{active\_causation\_stack} \implies \text{REJECT}$$
   Violations trigger immediate rejection with `EventLoopDetectedError` and log `SecurityEventType.WEBHOOK_LOOP_DETECTED`.

---

## 4. SSRF Defense, Multi-IP Validation, Port Constraints & Pinned Transport

### 4.1 Security Invariant & Target Binding (M54-F10, M54-F41)
> **INVARIANT M54-F41:** The outbound transport requires an explicit 3-tuple `(hostname, port, pinned_ip)`. Target URLs must specify HTTPS. Ports default to 443; custom ports are strictly restricted to the range `443..8443`. Ports outside this range are rejected with `InvalidWebhookPortError`.

### 4.2 Conservative Multi-IP DNS Validation Rule
When `socket.getaddrinfo(hostname, port)` resolves multiple candidate IP addresses:
1. **Conservative Rule:** **EVERY candidate IP in the resolved set must pass all SSRF validation criteria.**
2. If **ANY** resolved IP is private (`10/8`, `172.16/12`, `192.168/16`), loopback (`127/8`, `::1`), link-local (`169.254/16`, `fe80::/10`), cloud metadata (`169.254.169.254`, `100.100.100.200`), multicast, or IPv4-mapped IPv6:
   - **The ENTIRE hostname is immediately rejected.**
   - Delivery transitions to `failed` with error `SSRFMultiAddressViolationError`.
   - Mitigates DNS split-horizon, DNS rebinding, and mixed-record attack surfaces.
3. If **ALL** candidate IPs are valid:
   - Select the primary IP: `pinned_ip`.
   - If connection to `pinned_ip` fails with TCP connection reset or timeout, the worker may fail-over to the second pre-validated candidate IP *within the same pre-resolution set* without re-resolving DNS.

### 4.3 Dedicated `PinnedIPTransport` Adapter
Outbound HTTP execution bypasses all standard library proxy mechanisms:
```python
class PinnedIPTransport:
    """Dedicated HTTP transport connecting directly to pre-validated pinned IPs."""
    
    def __init__(self, pinned_ip: str, hostname: str, port: int = 443, timeout: float = 10.0):
        if not (443 <= port <= 8443):
            raise InvalidWebhookPortError(f"Webhook port {port} is outside authorized range 443..8443")
        self.pinned_ip = pinned_ip
        self.hostname = hostname
        self.port = port
        self.timeout = timeout

    def connect(self) -> ssl.SSLSocket:
        # 1. Establish raw TCP stream directly to validated pinned IP and port
        sock = socket.create_connection((self.pinned_ip, self.port), timeout=self.timeout)
        # 2. Wrap socket in TLS with SNI set to original hostname
        context = ssl.create_default_context()
        tls_sock = context.wrap_socket(sock, server_hostname=self.hostname)
        return tls_sock
```
- **Proxy Bypass:** Ignores `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and system registry proxies.
- **Redirect Denial:** Outbound transport enforces `follow_redirects = False`. 3xx responses transition immediately to `failed`.

---

## 5. Secret Encryption Key Lifecycle & Dual-Key Rotation (AES-256-GCM)

```
External Configuration
  └── AURA_WEBHOOK_MASTER_KEY (256-bit Base64 Key)
           │
           ▼
  HKDF-SHA256 (salt = user_id, info = b"aura_webhook_v1")
           │
           ▼
  Tenant Encryption Key (256-bit Symmetric Key)
           │
  ┌────────┴────────────────────────────────────────────────────────┐
  │ AES-256-GCM Authenticated Envelope Encryption                   │
  │                                                                 │
  │  - Nonce: 96-bit random IV (os.urandom(12) per encryption)      │
  │  - Tag: 128-bit authentication tag                              │
  │  - AAD: f"endpoint_id:{endpoint_id}:user_id:{user_id}"          │
  │  - Ciphertext: Base64-encoded encrypted secret                  │
  └─────────────────────────────────────────────────────────────────┘
           │
           ▼
  Stored in PostgreSQL: webhook_signing_keys.encrypted_secret
```

### 5.1 Deterministic Dual-Key Rotation Protocol (M54-F38)
When rotating signing secrets:
1. The incoming request signature header is inspected: `X-AURA-Signature-256: t=<timestamp>,v1=<sig>[,kid=<key_id>]`.
2. **Verification Order:**
   - Step 1: Active Key (`key_status = 'active'`). If match $\to$ ACCEPT.
   - Step 2: Retiring Key (`key_status = 'retiring'`). If within 24-hour grace window ($t_{\text{now}} - t_{\text{rotated}} \le 86400\text{s}$) and match $\to$ ACCEPT.
   - Step 3: Revoked / Expired Keys (`key_status IN ('revoked', 'expired')`). Immediate REJECT with HTTP 401.
3. Decryption occurs purely in-memory at verification time using `hmac.compare_digest`. Secrets are masked as `****` in all logs and API responses.

---

## 6. Outbound Delivery Semantics & State Transitions

### 6.1 State Transitions (M54-F39)
The outbound delivery lifecycle is strictly governed by the following valid transitions:

```
               ┌─────────────┐
               │   PENDING   │
               └──────┬──────┘
                      │ (Worker Claims via Lease: attempts += 1)
                      ▼
               ┌─────────────┐
        ┌─────►│ DELIVERING  │◄─────────────────┐
        │      └──────┬──────┘                  │
        │             │                         │
  (Transient   ┌──────┴──────────────────┐ (Retry Due: attempts += 1)
    Error)     │                         │      │
        │      ▼                         ▼      │
        │ ┌──────────┐             ┌────────────┴┐
        └─┤ RETRYING │             │  DELIVERED  │ (Terminal)
          └────┬─────┘             └─────────────┘
               │ (Attempts >= 5
               ▼  or expired 24h)
        ┌──────────────┐           ┌─────────────┐
        │ DEAD_LETTERED│(Terminal) │   FAILED    │ (Terminal - Non-retryable / SSRF)
        └──────────────┘           └─────────────┘
```

- **Terminal States:** `delivered`, `failed`, `dead_lettered` are strictly immutable.
- **At-Least-Once Delivery Guarantee:** Every delivery transmits a stable `Idempotency-Key: del_<uuid4>`. Retries never alter the idempotency key.

### 6.2 Outbound Response Classification Matrix

| Response Status / Error | Retryable? | Attempt Increment | `Retry-After` Handling | Backoff Delay | Terminal State |
|---|:---:|:---:|---|---|---|
| `200–299` OK | NO | +1 | Ignored | None | `delivered` (Terminal) |
| `300–399` Redirect | NO | +1 | Ignored | None | `failed` (Terminal) |
| `400–407, 410–428` | NO | +1 | Ignored | None | `failed` (Terminal) |
| `408, 409` | **YES** | +1 | Honored if present | Exponential + Jitter | `retrying` $\to$ `dead_lettered` |
| `429` Rate Limited | **YES** | +1 | **Clamped:** $\max(1\text{s}, \min(3600\text{s}, \Delta t))$ | Clamped Delay | `retrying` $\to$ `dead_lettered` |
| `500, 502, 503, 504` | **YES** | +1 | Honored if present | Exponential + Jitter | `retrying` $\to$ `dead_lettered` |
| Timeout / Socket Reset | **YES** | +1 | N/A | Exponential + Jitter | `retrying` $\to$ `dead_lettered` |
| TLS / SSRF Violation | NO | +1 | Ignored | None | `failed` (Terminal) |

---

## 7. Unified Dead-Letter Ledger & Replay Immutability Contract (M54-F36)

### 7.1 Unified Dead-Letter Ledger (`dead_letter_events`)
`dead_letter_events` serves as the authoritative, unified, 100% write-once immutable historical dead-letter ledger for both inbound and outbound failures:
- **Discriminator:** `dead_letter_type VARCHAR(32)` strictly constrained to `'inbound'` or `'outbound'`.
- **Inbound Dead Letters:** `dead_letter_type = 'inbound'`, `inbound_event_id IS NOT NULL`, `delivery_id IS NULL`.
- **Outbound Dead Letters:** `dead_letter_type = 'outbound'`, `delivery_id IS NOT NULL`, `inbound_event_id IS NULL`.
- **CHECK Constraints:** Enforces mutually exclusive source references based on discriminator.

### 7.2 Replay Execution & Historical Immutability
1. **Historical Records Write-Once & Permanently Immutable:** Rows in `dead_letter_events` and historical terminal `event_deliveries` records are 100% write-once and permanently immutable. No field in `dead_letter_events` is ever updated.
2. **Replay Execution:**
   - An administrative replay creates a **NEW** delivery record in `event_deliveries`:
     ```sql
     INSERT INTO event_deliveries (
         id, subscription_id, user_id, event_id, target_url,
         status, attempts, max_attempts, next_attempt_at, payload,
         causation_id, replayed_from_dead_letter_id
     ) VALUES (
         'del_' || gen_random_uuid(), :subscription_id, :user_id, :event_id, :target_url,
         'pending', 0, 5, CURRENT_TIMESTAMP, :payload,
         :original_event_id, :dead_letter_id
     );
     ```
   - Replay audit metadata is recorded in a separate dedicated table `dead_letter_replays`:
     ```sql
     INSERT INTO dead_letter_replays (
         id, dead_letter_id, new_delivery_id, user_id, replayed_by, replayed_at, reason
     ) VALUES (
         'dlr_' || gen_random_uuid(), :dead_letter_id, :new_delivery_id, :user_id, :replayed_by, CURRENT_TIMESTAMP, :reason
     );
     ```
   - The historical dead-letter record and original terminal delivery remain permanently immutable and unchanged across repeated, concurrent, failed, or successful replays.

---

## 8. Single Maintenance Task Boundary & Resource Bounds (M54-F40)

### 8.1 Single Maintenance Loop Boundary
- Project AURA strictly enforces a single scheduler boundary.
- M54 creates **ZERO** background cron daemons, competing timers, or standalone scheduler loops.
- M54 exposes an explicit, bounded, idempotent maintenance service interface invoked strictly by M53's scheduler loop:

```python
class WebhookMaintenanceService:
    """Bounded, idempotent maintenance operations invoked exclusively by M53 maintenance loop."""
    
    async def run_maintenance_once(
        self, 
        now: datetime, 
        retention_days: int = 30, 
        batch_limit: int = 100
    ) -> MaintenanceReport:
        """Executes bounded maintenance tasks:
        1. Prunes dead-letter records older than retention_days (in bounded batches <= batch_limit).
        2. Reclaims expired leases for inbound_events and event_deliveries.
        3. Reconciles tenant_webhook_capacities counters against active in-flight event rows.
        """
```

- M53 remains the sole scheduler of execution cadence.
- M54 operations are bounded (`LIMIT batch_limit`), idempotent, restart-safe, and safe to execute multiple times.

### 8.2 Resource Bounds & Abuse Control Semantics

| Resource Dimension | Hard Limit | Enforcement Point | Storage / Mechanism | Multi-Worker & Restart Resilience |
|---|---|---|---|---|
| **Inbound Request Body** | `1,048,576` bytes (1 MB) | HTTP Ingress Stream | In-Memory Streaming Reader | Enforced per connection; early socket abort. |
| **Outbound Response Body**| `102,400` bytes (100 KB) | Response Reader | Truncated in DB delivery log | Memory-safe; bounded audit footprint. |
| **Endpoint Inbound Rate** | `120` req/min | Gateway Filter | Memory Token Bucket / DB Counter | Per-endpoint throttling; returns HTTP 429. |
| **Tenant Concurrent Events**| `100` in-flight | Ingress Admission | `tenant_webhook_capacities` Row Lock | **Durable DB Transaction:** Cannot be bypassed by multiple workers or node restarts. |
| **Max Retry Attempts (In)**| `3` attempts | Inbound Dispatcher | `inbound_events.attempts` | Durable column; survives node restarts. |
| **Max Retry Attempts (Out)**| `5` attempts | Delivery Worker | `event_deliveries.attempts` | Durable column; survives node restarts. |
| **Max Delivery Lifetime** | `86,400` seconds (24h) | Delivery Worker | `event_deliveries.created_at` | Durable timestamp; drops expired retries to dead-letter. |
| **Dead-Letter Retention** | `30` days | M53 Maintenance Task | `dead_letter_events.created_at` | Durable bounded database cleanup query. |

---

## 9. Prompt Injection & Instruction Hierarchy Boundary

External webhook payloads are hostile untrusted data:
1. **Instruction Hierarchy Rule:** External payload strings are **never** evaluated as system instructions, developer prompts, or trusted tool commands.
2. **Syntactic Containment:** Payloads injected into task context are enclosed in `<untrusted_source_content>` tags.
3. **Execution Security Boundary:**
   - PolicyEngine strictly evaluates every tool call attempted by the agent.
   - Sensitive actions trigger M48 Human Approval.
   - LLM reasoning operates strictly through M51 `ModelGateway` with fail-closed policy enforcement.

---

## 10. Database Schema (`migrations/005_enterprise_webhooks_and_event_gateway.sql`)

```sql
-- M54 — Enterprise Webhooks & Inbound/Outbound Event Gateway
-- Schema for inbound endpoints, encrypted signing keys, event persistence, capacities, and delivery queues.

-- 1. Tenant Webhook Capacities (Authoritative Admission Row Lock & Operational Counter)
CREATE TABLE IF NOT EXISTS tenant_webhook_capacities (
    user_id VARCHAR(128) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    in_flight_count INTEGER NOT NULL DEFAULT 0,
    max_capacity INTEGER NOT NULL DEFAULT 100,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_in_flight_bounds CHECK (in_flight_count >= 0 AND in_flight_count <= max_capacity)
);

-- 2. Webhook Endpoints
CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    path_suffix VARCHAR(128) NOT NULL,
    allowed_event_types JSONB NOT NULL DEFAULT '["*"]'::jsonb,
    rate_limit_per_minute INTEGER NOT NULL DEFAULT 120,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_endpoint_status CHECK (status IN ('active', 'disabled', 'revoked')),
    CONSTRAINT uq_user_endpoint_path UNIQUE (user_id, path_suffix)
);

CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_user ON webhook_endpoints(user_id, status);

-- 3. Webhook Signing Keys (Encrypted Secrets & Dual-Key Rotation)
CREATE TABLE IF NOT EXISTS webhook_signing_keys (
    id VARCHAR(128) PRIMARY KEY,
    endpoint_id VARCHAR(128) NOT NULL REFERENCES webhook_endpoints(id) ON DELETE CASCADE,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_version INTEGER NOT NULL DEFAULT 1,
    encrypted_secret JSONB NOT NULL,
    key_status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NULL,
    revoked_at TIMESTAMPTZ NULL,
    CONSTRAINT chk_key_status CHECK (key_status IN ('active', 'retiring', 'revoked', 'expired'))
);

CREATE INDEX IF NOT EXISTS idx_signing_keys_endpoint ON webhook_signing_keys(endpoint_id, key_status);

-- 4. Inbound Events (Durable Persistence, Retry State & Collision Defense)
CREATE TABLE IF NOT EXISTS inbound_events (
    id VARCHAR(128) PRIMARY KEY,
    endpoint_id VARCHAR(128) NOT NULL REFERENCES webhook_endpoints(id) ON DELETE CASCADE,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider_event_id VARCHAR(255) NOT NULL,
    payload_sha256 VARCHAR(64) NOT NULL,
    event_type VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'accepted',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_attempt_at TIMESTAMPTZ NULL,
    payload JSONB NOT NULL,
    headers JSONB NOT NULL DEFAULT '{}'::jsonb,
    signature VARCHAR(255) NOT NULL,
    lease_owner VARCHAR(128) NULL,
    lease_token VARCHAR(128) NULL,
    claimed_at TIMESTAMPTZ NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    task_id VARCHAR(128) NULL REFERENCES tasks(id) ON DELETE SET NULL,
    error_message TEXT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMPTZ NULL,
    CONSTRAINT chk_inbound_status CHECK (status IN ('accepted', 'processing', 'processed', 'failed', 'dead_lettered')),
    CONSTRAINT uq_inbound_events_dedup UNIQUE (endpoint_id, provider_event_id)
);

CREATE INDEX IF NOT EXISTS idx_inbound_events_user ON inbound_events(user_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_inbound_events_lease ON inbound_events(status, next_attempt_at ASC, lease_expires_at)
    WHERE status IN ('accepted', 'processing', 'failed');

-- 5. Event Subscriptions
CREATE TABLE IF NOT EXISTS event_subscriptions (
    id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    event_type_filter VARCHAR(128) NOT NULL,
    target_type VARCHAR(32) NOT NULL,
    target_url TEXT NULL,
    signing_secret_encrypted JSONB NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_sub_target CHECK (target_type IN ('webhook', 'task', 'automation')),
    CONSTRAINT chk_sub_status CHECK (status IN ('active', 'paused', 'disabled'))
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user_type ON event_subscriptions(user_id, event_type_filter, status);

-- 6. Event Deliveries (Outbound Queue, Lineage & Lease Fencing)
CREATE TABLE IF NOT EXISTS event_deliveries (
    id VARCHAR(128) PRIMARY KEY,
    subscription_id VARCHAR(128) NOT NULL REFERENCES event_subscriptions(id) ON DELETE CASCADE,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id VARCHAR(128) NOT NULL,
    target_url TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_attempt_at TIMESTAMPTZ NULL,
    last_response_status INTEGER NULL,
    last_error TEXT NULL,
    lease_owner VARCHAR(128) NULL,
    lease_token VARCHAR(128) NULL,
    claimed_at TIMESTAMPTZ NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    payload JSONB NOT NULL,
    causation_id VARCHAR(128) NULL,
    replayed_from_dead_letter_id VARCHAR(128) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ NULL,
    CONSTRAINT chk_delivery_status CHECK (status IN ('pending', 'delivering', 'delivered', 'retrying', 'failed', 'dead_lettered'))
);

CREATE INDEX IF NOT EXISTS idx_deliveries_due_lease ON event_deliveries(status, next_attempt_at ASC, lease_expires_at)
    WHERE status IN ('pending', 'retrying');
CREATE INDEX IF NOT EXISTS idx_deliveries_user_created ON event_deliveries(user_id, created_at DESC);

-- 7. Dead Letter Events (Unified 100% Write-Once Immutable Historical Ledger)
CREATE TABLE IF NOT EXISTS dead_letter_events (
    id VARCHAR(128) PRIMARY KEY,
    dead_letter_type VARCHAR(32) NOT NULL,
    inbound_event_id VARCHAR(128) NULL REFERENCES inbound_events(id) ON DELETE CASCADE,
    delivery_id VARCHAR(128) NULL REFERENCES event_deliveries(id) ON DELETE CASCADE,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason VARCHAR(64) NOT NULL,
    final_error TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_dl_type CHECK (dead_letter_type IN ('inbound', 'outbound')),
    CONSTRAINT chk_dl_source CHECK (
        (dead_letter_type = 'inbound' AND inbound_event_id IS NOT NULL AND delivery_id IS NULL) OR
        (dead_letter_type = 'outbound' AND delivery_id IS NOT NULL AND inbound_event_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_dead_letter_user ON dead_letter_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dead_letter_type ON dead_letter_events(dead_letter_type, created_at DESC);

-- 8. Dead Letter Replays (Dedicated Replay Audit Trail)
CREATE TABLE IF NOT EXISTS dead_letter_replays (
    id VARCHAR(128) PRIMARY KEY,
    dead_letter_id VARCHAR(128) NOT NULL REFERENCES dead_letter_events(id) ON DELETE CASCADE,
    new_delivery_id VARCHAR(128) NOT NULL REFERENCES event_deliveries(id) ON DELETE CASCADE,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    replayed_by VARCHAR(128) NOT NULL,
    replayed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reason TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_dead_letter_replays_dl ON dead_letter_replays(dead_letter_id);
```

---

## 11. Complete 41 Formal Architectural Invariants

- **M54-F01 (Tenant Isolation):** Every webhook endpoint, inbound event, signing key, and outbound delivery is strictly scoped to an authenticated `user_id`. Cross-tenant queries return uniform HTTP 404 responses.
- **M54-F02 (Authentication Fail-Closed):** Inbound requests lacking valid signatures, active endpoints, or matching tenants are rejected immediately with HTTP 401/404 before state alteration.
- **M54-F03 (Signature Verification):** All cryptographic signature comparisons must use `hmac.compare_digest` to prevent timing side-channel attacks.
- **M54-F04 (Replay Protection):** Inbound webhooks with timestamps outside the authorized window ($|t_{\text{req}} - t_{\text{server}}| > 300\text{s}$) must be rejected with HTTP 400.
- **M54-F05 (Durable Inbound Acceptance):** An inbound webhook acknowledged with HTTP 202 Accepted must be durably committed to the `inbound_events` table before the HTTP response is completed.
- **M54-F06 (Inbound Idempotency):** The composite key `(endpoint_id, provider_event_id)` is globally unique. Replays return the cached acceptance status without generating duplicate M52 tasks or M53 triggers.
- **M54-F07 (Outbound At-Least-Once Semantics):** Outbound delivery is explicitly specified as at-least-once with delivery-level idempotency headers (`X-AURA-Event-Id`, `X-AURA-Delivery-Id`, `Idempotency-Key`).
- **M54-F08 (No Global Exactly-Once Overclaim):** AURA does not claim global exactly-once delivery across external HTTP networks.
- **M54-F09 (SSRF-Safe Outbound Delivery):** Outbound requests must validate target URLs against forbidden schemes, private CIDRs, loopback, link-local, and cloud metadata IPs.
- **M54-F10 (DNS / IP Pinning Correctness):** The exact IP address validated against SSRF rules must be the IP used for the socket connection. Independent transport DNS resolution is forbidden.
- **M54-F11 (Bounded Retries):** Outbound webhooks are retried at most 5 times across a 24-hour lifetime with full jitter exponential backoff.
- **M54-F12 (Bounded Resource Usage):** Inbound bodies are capped at 1MB, outbound responses at 100KB, rate limits at 120 req/min, and tenant in-flight events at 100.
- **M54-F13 (Secret Confidentiality):** Webhook signing secrets must be encrypted at rest using AES-256-GCM with external master key derivation and never exposed in plaintext.
- **M54-F14 (Key Rotation Safety):** Dual-key acceptance is enforced during rotation grace periods, enabling zero-downtime key rotation.
- **M54-F15 (M52 Boundary Preservation):** Inbound events creating tasks must use standard M52 task repositories with deterministic idempotency keys (`f"m54_inbound_{event_id}"`).
- **M54-F16 (M53 Boundary Preservation):** Inbound events triggering automations must route through M53 scheduler interfaces under tenant quota without parallel schedulers.
- **M54-F17 (M48 Approval Preservation):** Sensitive actions initiated by webhook events require M48 human approval before execution.
- **M54-F18 (ModelGateway Preservation):** All LLM reasoning on event content must route exclusively through M51 `ModelGateway`.
- **M54-F19 (Prompt-Injection Containment):** Webhook payloads injected into agent context must be enclosed within `<untrusted_source_content>` boundary tags.
- **M54-F20 (Event-Loop Prevention):** Causation depth in event lineage is strictly capped at $\le 3$. Cyclic causation chains are rejected with `EventLoopDetectedError`.
- **M54-F21 (Immutable Terminal State):** Delivery records in terminal states (`delivered`, `dead_lettered`, `failed`) cannot be modified except via explicit administrative replay.
- **M54-F22 (Low-Cardinality Telemetry):** Telemetry dimensions must not include high-cardinality values (`user_id`, `event_id`, `delivery_id`, `target_url`, payload keys).
- **M54-F23 (Crash Recovery):** Background reconcilers must detect and recover stale leases and unassigned accepted events without silent event loss.
- **M54-F24 (No Stale-Worker Mutation):** Outbound delivery updates must enforce lease token fencing to prevent stale workers from corrupting delivery state.
- **M54-F25 (Future M55 Compatibility):** Delivery worker, lease repository, and event queue interfaces must remain decoupled from in-process threading to allow drop-in scaling by M55 distributed worker fleets.
- **M54-F26 (Untrusted External Lineage):** External request bodies cannot establish trusted AURA governance metadata without an authoritative `X-AURA-Lineage-Token`.
- **M54-F27 (Payload Collision Rejection):** Duplicate `provider_event_id` presenting a different canonical payload hash is rejected with HTTP 409 Conflict and creates zero downstream actions.
- **M54-F28 (Validated IP Binding):** The validated SSRF IP is the exact IP used for the outbound connection.
- **M54-F29 (Proxy Isolation):** Outbound delivery transport cannot route requests through ambient system or environment proxies.
- **M54-F30 (Single Task Record):** One inbound event produces at most one logical M52 task record.
- **M54-F31 (Single Automation Admission):** One event/automation pair produces at most one logical M53 admission.
- **M54-F32 (Durably Recoverable Ingress):** Accepted inbound events remain durably recoverable until reaching terminal status.
- **M54-F33 (Instruction Authority Non-Elevation):** External webhook content cannot gain system or developer instruction authority.
- **M54-F34 (Atomic Tenant Admission Capacity & Exactly-Once Release):** Inbound admission is gated by `tenant_webhook_capacities` row lock (`FOR UPDATE`) maintaining operational invariant $\text{in\_flight\_count} = \text{COUNT}(\text{inbound\_events WHERE status} \in \{\text{'accepted'}, \text{'processing'}, \text{'failed'}\}) \le 100$. Capacity is released exactly once upon successful transition from non-terminal to terminal state (`processed`, `dead_lettered`).
- **M54-F35 (Payload Hash Determinism):** The canonical payload hash is computed deterministically strictly on the raw HTTP wire request bytes verified by the HMAC signature.
- **M54-F36 (Unified Dead-Letter Ledger & Replay Lineage):** `dead_letter_events` is a unified, 100% write-once immutable historical ledger (`dead_letter_type` $\in$ `{'inbound', 'outbound'}`). Administrative replays create a new delivery record linked via `replayed_from_dead_letter_id` and audit record in `dead_letter_replays`, leaving historical records byte-immutable.
- **M54-F37 (Durable Inbound Retry State):** Inbound retry attempts count dispatch executions started (`attempts`), scheduled with exponential backoff up to max 3 attempts, with deterministic crash-recovery convergence.
- **M54-F38 (Deterministic Dual-Key Rotation):** Dual-key signature verification strictly evaluates the primary active key first, retiring key second during grace window, and unconditionally rejects revoked/expired keys.
- **M54-F39 (Outbound State Transition Integrity):** Outbound deliveries obey a formal state machine where terminal states (`delivered`, `failed`, `dead_lettered`) are strictly immutable.
- **M54-F40 (Single Scheduler Boundary):** Maintenance and retention cleanup operations execute strictly within the existing application / M53 lifecycle loop via `WebhookMaintenanceService.run_maintenance_once()` without standalone background schedulers.
- **M54-F41 (Validated Target Binding):** Outbound HTTP transport requires an explicit `(hostname, port, pinned_ip)` binding with HTTPS scheme and port restricted strictly to the authorized range `443..8443`.

---

## 12. Complete Adversarial Test Matrix (Exact Total: 117 Planned Scenarios)

> [!IMPORTANT]
> **Architectural Test-Planning Demarcation:** The 117 scenarios below represent **required implementation-level validation specifications** (100% planned invariant-to-scenario architectural mapping). They define the complete verification obligations for M54 implementation and do not imply that test execution has already occurred.

### 12.1 Category Breakdown & Verification Arithmetic

| Category # | Scenario Category Name | Scenario ID Range | Category Total |
|:---:|---|---|:---:|
| 1 | **Inbound Verification & Parsing** | `T-IN-01` .. `T-IN-12` | **12** |
| 2 | **Collision & Payload Integrity** | `T-COLLISION-01` .. `T-COLLISION-03`, `T-HASH-01` .. `T-HASH-04` | **7** |
| 3 | **Cryptographic Lineage & Loop Prevention** | `T-LINEAGE-01` .. `T-LINEAGE-03`, `T-LOOP-01` .. `T-LOOP-03` | **6** |
| 4 | **SSRF / DNS / Pinned Transport** | `T-SSRF-01` .. `T-SSRF-10`, `T-DNS-01` .. `T-DNS-03`, `T-TARGET-01` .. `T-TARGET-03`, `T-PROXY-01` .. `T-PROXY-03` | **19** |
| 5 | **Outbound Delivery & Backoff** | `T-OUT-01` .. `T-OUT-14`, `T-STATE-01` .. `T-STATE-04` | **18** |
| 6 | **Replay & Durable Retries** | `T-REPLAY-01` .. `T-REPLAY-07`, `T-INRETRY-01` .. `T-INRETRY-06` | **13** |
| 7 | **Key Lifecycle & Security** | `T-KEYROT-01` .. `T-KEYROT-04`, `T-SEC-01` .. `T-SEC-03` | **7** |
| 8 | **Internal Gateway Integration & Recovery** | `T-INT-01` .. `T-INT-06`, `T-RECOVERY-01` .. `T-RECOVERY-03` | **9** |
| 9 | **Multi-Tenant Isolation & Concurrency** | `T-RESOURCE-01` .. `T-RESOURCE-08`, `T-TENANT-01` .. `T-TENANT-03` | **11** |
| 10 | **Observability / Concurrency / Maintenance** | `T-OBS-01` .. `T-OBS-05`, `T-CONC-01` .. `T-CONC-02`, `T-TXN-01`, `T-MAINT-01` .. `T-MAINT-03` | **11** |
| 11 | **Matrix Consistency Meta-Tests** | `T-MATRIX-01` .. `T-MATRIX-04` | **4** |
| **TOTAL** | **Sum of all 11 categories** | **$12+7+6+19+18+13+7+9+11+11+4$** | **117** |

---

### 12.2 Detailed Scenario Specifications

| Scenario ID | Test Description | Invariant | Expected Architectural Behavior |
|---|---|---|---|
| **T-IN-01** | Valid HMAC-SHA256 signature accepted | M54-F02 | Valid HMAC $\to$ HTTP 202; `status = 'accepted'`. |
| **T-IN-02** | 1-bit modified payload fails signature | M54-F03 | Single byte change $\to$ HTTP 401; 0 events saved. |
| **T-IN-03** | Missing signature header rejected | M54-F02 | Request without header $\to$ HTTP 401. |
| **T-IN-04** | Stale timestamp ($> 300\text{s}$) rejected | M54-F04 | Timestamp 301s old $\to$ HTTP 400 (`timestamp_out_of_bounds`). |
| **T-IN-05** | Future timestamp ($> 60\text{s}$) rejected | M54-F04 | Timestamp 61s future $\to$ HTTP 400 (`timestamp_out_of_bounds`). |
| **T-IN-06** | Non-existent `endpoint_id` returns 404 | M54-F01 | Unknown ID $\to$ HTTP 404 with 0 info leakage. |
| **T-IN-07** | Disabled endpoint returns 404 | M54-F01 | Disabled endpoint $\to$ HTTP 404. |
| **T-IN-08** | Body $> 1\text{MB}$ streaming aborted | M54-F12 | Stream aborted at 1MB $\to$ HTTP 413. |
| **T-IN-09** | Malformed JSON payload rejected | M54-F02 | Non-JSON body $\to$ HTTP 400. |
| **T-IN-10** | Dual-key rotation secondary key valid | M54-F14 | Retiring key $\to$ HTTP 202. |
| **T-IN-11** | Revoked key rejected | M54-F14 | Revoked key $\to$ HTTP 401. |
| **T-IN-12** | Constant-time comparison verified | M54-F03 | `hmac.compare_digest` used; timing variance flat. |
| **T-COLLISION-01**| Same ID + Same payload $\to$ duplicate | M54-F06 | Exact duplicate $\to$ HTTP 202 (`duplicate: true`). |
| **T-COLLISION-02**| Same ID + Altered payload $\to$ 409 | M54-F27 | Different payload hash $\to$ HTTP 409 Conflict. |
| **T-COLLISION-03**| 10 concurrent altered payload races | M54-F27 | All 10 rejected with HTTP 409 Conflict; 0 tasks created. |
| **T-HASH-01** | Canonical payload hash computed strictly on raw bytes | M54-F35 | Wire byte exact SHA-256 matches persisted `payload_sha256`. |
| **T-HASH-02** | JSON key reordering alters raw wire hash | M54-F35 | Key reordering produces different hash; verifies raw integrity. |
| **T-HASH-03** | Trailing whitespace alters raw hash | M54-F35 | Trailing newline detected and preserved in raw byte hash. |
| **T-HASH-04** | Collision rejected on byte mismatch | M54-F35 | Re-transmitted event with altered whitespace returns HTTP 409. |
| **T-LINEAGE-01**| Unsigned external lineage ignored | M54-F26 | Inbound lineage discarded; creates new root chain. |
| **T-LINEAGE-02**| Valid AURA-signed lineage accepted | M54-F20 | Valid `X-AURA-Lineage-Token` verified; depth incremented. |
| **T-LINEAGE-03**| Cross-tenant signed lineage rejected | M54-F01 | Lineage signed by User B rejected by User A endpoint. |
| **T-SSRF-01** | `169.254.169.254` cloud metadata blocked | M54-F09 | Delivery rejected; `status = 'failed'`. |
| **T-SSRF-02** | `metadata.google.internal` blocked | M54-F09 | Delivery rejected; `status = 'failed'`. |
| **T-SSRF-03** | Localhost IPv4 `127.0.0.1` blocked | M54-F09 | Delivery rejected; `status = 'failed'`. |
| **T-SSRF-04** | Localhost hostname `localhost` blocked | M54-F09 | Delivery rejected; `status = 'failed'`. |
| **T-SSRF-05** | Private CIDR `10.0.0.0/8` blocked | M54-F09 | Delivery rejected; `status = 'failed'`. |
| **T-SSRF-06** | Private CIDR `172.16.0.0/12` blocked | M54-F09 | Delivery rejected; `status = 'failed'`. |
| **T-SSRF-07** | Private CIDR `192.168.0.0/16` blocked| M54-F09 | Delivery rejected; `status = 'failed'`. |
| **T-SSRF-08** | File URI scheme `file:///` blocked | M54-F09 | Delivery rejected; `status = 'failed'`. |
| **T-SSRF-09** | IPv6 loopback `[::1]` blocked | M54-F09 | Delivery rejected; `status = 'failed'`. |
| **T-SSRF-10** | IPv4-mapped IPv6 `::ffff:127.0.0.1` | M54-F09 | Delivery rejected; `status = 'failed'`. |
| **T-DNS-01** | DNS Rebinding blocked via IP pinning | M54-F10 | Pinned socket connects directly to validated IP. |
| **T-DNS-02** | Multi-IP DNS with 1 private IP rejected | M54-F28 | Entire hostname rejected; delivery `failed`. |
| **T-DNS-03** | Retry cannot perform unvalidated DNS | M54-F10 | Retry re-validates all IPs before pinning. |
| **T-TARGET-01** | Outbound socket binds to `(pinned_ip, port)` | M54-F41 | Transport opens direct socket to pinned IP and port. |
| **T-TARGET-02** | Custom port within 443..8443 allowed | M54-F41 | Delivery succeeds on validated non-standard port 8443. |
| **T-TARGET-03** | Custom port outside 443..8443 rejected | M54-F41 | Port 22, 80, 9000 rejected with `InvalidWebhookPortError`. |
| **T-PROXY-01** | `HTTP_PROXY` env cannot intercept | M54-F29 | Direct socket connection; proxy ignored. |
| **T-PROXY-02** | `HTTPS_PROXY` env cannot intercept | M54-F29 | Direct socket connection; proxy ignored. |
| **T-PROXY-03** | SOCKS proxy env cannot intercept | M54-F29 | Direct socket connection; proxy ignored. |
| **T-OUT-01** | 200 OK delivery marks `delivered` | M54-F21 | Terminal `delivered` reached. |
| **T-OUT-02** | 302 Redirect marks `failed` | M54-F09 | Redirect not followed; marks `failed`. |
| **T-OUT-03** | 400 Bad Request marks `failed` | M54-F11 | Immediate terminal `failed` (no retry). |
| **T-OUT-04** | 404 Not Found marks `failed` | M54-F11 | Immediate terminal `failed` (no retry). |
| **T-OUT-05** | 429 with `Retry-After: 30` honored | M54-F11 | Next attempt scheduled at `now + 30s`. |
| **T-OUT-06** | 429 with excessive `Retry-After: 999999`| M54-F12 | Clamped to 3600s max bound. |
| **T-OUT-07** | 429 with malformed `Retry-After: bad` | M54-F11 | Falls back to default exponential backoff. |
| **T-OUT-08** | 500 Internal Error triggers retry | M54-F11 | Status transitions to `retrying`. |
| **T-OUT-09** | Connection timeout triggers retry | M54-F11 | Status transitions to `retrying`. |
| **T-OUT-10** | 5 consecutive failures $\to$ dead-letter| M54-F11 | Inserts record into `dead_letter_events`. |
| **T-OUT-11** | Stable `Idempotency-Key` across retries| M54-F07 | Attempts 1, 2, 3 transmit identical header. |
| **T-OUT-12** | Distinct deliveries have distinct keys | M54-F07 | Distinct deliveries transmit unique keys. |
| **T-OUT-13** | Terminal delivery state immutable | M54-F21 | Modification of `delivered` record rejected. |
| **T-OUT-14** | Administrative replay creates new delivery| M54-F21 | Replay creates new record; archives old. |
| **T-STATE-01** | `pending` $\to$ `delivering` $\to$ `delivered` | M54-F39 | Standard happy-path state progression. |
| **T-STATE-02** | `delivering` $\to$ `failed` on non-retryable | M54-F39 | Terminal failed reached without intermediate retry. |
| **T-STATE-03** | `delivering` $\to$ `retrying` $\to$ `delivering` | M54-F39 | Bounded retry loop executes correctly. |
| **T-STATE-04** | Terminal state mutation rejected | M54-F39 | Attempted mutation of terminal state raises exception. |
| **T-REPLAY-01**| Replaying dead-letter creates new delivery | M54-F36 | Creates delivery linked via `replayed_from_dead_letter_id`. |
| **T-REPLAY-02**| Dead-letter replay recorded in audit table | M54-F36 | Inserts audit row into `dead_letter_replays`. |
| **T-REPLAY-03**| Historical dead-letter payload immutable | M54-F36 | Original dead-letter row remains 100% unchanged. |
| **T-REPLAY-04**| Schema fields match replay contract | M54-F36 | `causation_id` & `replayed_from_dead_letter_id` match DB schema. |
| **T-REPLAY-05**| Mutation of dead-letter event rejected | M54-F36 | Attempted update on historical row raises database error. |
| **T-REPLAY-06**| Unified dead-letter discriminator integrity| M54-F36 | `dead_letter_type` CHECK enforces exact source reference. |
| **T-REPLAY-07**| Historical DLQ byte-identical post-replay | M54-F36 | Multiple replays leave DLQ record byte-identical. |
| **T-INRETRY-01**| Inbound dispatch failure increments attempts | M54-F37 | Increments `attempts = 1`, schedules `next_attempt_at`. |
| **T-INRETRY-02**| Inbound event succeeds on retry | M54-F37 | Transitions to `processed`, links created `task_id`. |
| **T-INRETRY-03**| Inbound event reaching max 3 attempts dead-letters| M54-F37| Transitions to terminal `dead_lettered`. |
| **T-INRETRY-04**| Inbound retry worker claims eligible failed events | M54-F37| Fenced lease claims only due failed events. |
| **T-INRETRY-05**| Crash after attempt reservation converges | M54-F37 | Reconciler reclaims expired lease; no extra attempt. |
| **T-INRETRY-06**| Retry cannot start when attempts == max_attempts | M54-F37 | Event transitions to `dead_lettered` without 4th dispatch. |
| **T-KEYROT-01** | Active primary key verified first | M54-F38 | Active key signature validates immediately. |
| **T-KEYROT-02** | Retiring key verified during 24h grace | M54-F38 | Retiring key validates within grace window. |
| **T-KEYROT-03** | Expired key rejected outside grace window | M54-F38 | Key older than 24h grace rejected with 401. |
| **T-KEYROT-04** | Revoked key rejected unconditionally | M54-F38 | Revoked key rejected even if timestamp is fresh. |
| **T-INT-01** | Inbound event enqueues M52 task | M54-F15 | Task created with `m54_inbound_{id}` key. |
| **T-INT-02** | Prompt injection enclosed in tags | M54-F19 | Enclosed in `<untrusted_source_content>`. |
| **T-INT-03** | PolicyEngine blocks unauthorized action| M54-F17 | Tool execution denied by PolicyEngine. |
| **T-INT-04** | Sensitive action halts at M48 approval | M54-F17 | Task transitions to `awaiting_approval`. |
| **T-INT-05** | Inbound event triggers M53 automation | M54-F16 | Triggers automation; charges 1 quota unit. |
| **T-INT-06** | Quota-exhausted tenant skips automation | M54-F16 | Automation skipped; 0 quota charged. |
| **T-LOOP-01** | Direct ping-pong cycle ($A \to B \to A$) | M54-F20 | Cycle rejected with `EventLoopDetectedError`. |
| **T-LOOP-02** | 3-hop cycle ($A \to B \to C \to A$) | M54-F20 | Origin fingerprint match triggers rejection. |
| **T-LOOP-03** | Causation depth $> 3$ rejected | M54-F20 | Depth = 4 rejected by depth bound. |
| **T-RECOVERY-01**| Accepted event with no task recovered | M54-F32 | Reconciler materializes M52 task. |
| **T-RECOVERY-02**| Task exists but link missing converges | M54-F32 | Reconciler links existing `task_id`. |
| **T-RECOVERY-03**| M53 admission exists, event unmarked | M54-F32 | Reconciler updates event to `processed`. |
| **T-RESOURCE-01**| 100 tenant in-flight limit enforced | M54-F12 | 101st event rejected with HTTP 429 via row lock. |
| **T-RESOURCE-02**| Process restart preserves limits | M54-F12 | Post-restart counter evaluates durable DB rows. |
| **T-RESOURCE-03**| Concurrent burst admits 100, rejects 50 | M54-F34 | 150 concurrent requests $\to$ exactly 100 admitted, 50 429s. |
| **T-RESOURCE-04**| Completion frees in-flight capacity | M54-F34 | Event transition to `processed` permits next admission. |
| **T-RESOURCE-05**| Cross-tenant independent limits | M54-F34 | User A at 100 capacity does not block User B. |
| **T-RESOURCE-06**| Concurrent admission + terminal release | M54-F34 | Capacity never becomes negative and never exceeds 100. |
| **T-RESOURCE-07**| Concurrent capacity reconciliation + admission | M54-F34 | Row lock serializes operations; zero race/drift. |
| **T-RESOURCE-08**| Duplicate terminalization release immunity | M54-F34 | Second terminal call updates 0 rows; 0 double release. |
| **T-TENANT-01**| User B querying User A's endpoint $\to$ 404| M54-F01 | Lookup returns 404 with 0 data leaked. |
| **T-TENANT-02**| User B cannot rotate User A's key | M54-F01 | Rotation endpoint returns 404. |
| **T-TENANT-03**| Encrypted secret masked as `****` | M54-F13 | GET endpoint masks secret field. |
| **T-OBS-01** | Prometheus labels have low cardinality | M54-F22 | Zero IDs or URLs in metric dimensions. |
| **T-OBS-02** | Security audit logs `WEBHOOK_SIGNATURE_FAILED`| M54-F02 | Structured audit log emitted. |
| **T-OBS-03** | Security audit logs `WEBHOOK_SSRF_BLOCKED`| M54-F09 | Structured audit log emitted. |
| **T-OBS-04** | Security audit logs `WEBHOOK_LOOP_DETECTED`| M54-F20 | Structured audit log emitted. |
| **T-OBS-05** | Security audit logs `WEBHOOK_PAYLOAD_COLLISION`| M54-F27| Structured audit log emitted on collision. |
| **T-CONC-01** | Concurrent delivery workers claim disjoint| M54-F24 | `SKIP LOCKED` guarantees zero worker overlap. |
| **T-CONC-02** | Stale delivery lease recovered after TTL | M54-F23 | Reconciler recovers lease after 60s timeout. |
| **T-TXN-01** | Mid-dispatch crash rolls back cleanly | M54-F05 | PostgreSQL transaction rolls back without side effects. |
| **T-MAINT-01** | Dead-letter cleanup via M53 maintenance loop | M54-F40 | Bounded retention task runs without standalone cron. |
| **T-MAINT-02** | Bounded batch maintenance execution | M54-F40 | Prunes at most `batch_limit` items per run. |
| **T-MAINT-03** | Idempotent multiple maintenance invocations | M54-F40 | Multiple calls produce identical safe outcomes. |
| **T-MATRIX-01** | Unique scenario ID assertion | Meta | All 117 scenario IDs are globally unique. |
| **T-MATRIX-02** | 100% planned invariant test coverage | Meta | Invariants M54-F01 to M54-F41 mapped to $\ge 1$ test. |
| **T-MATRIX-03** | Negative test coverage for P0/P1 invariants | Meta | Negative adversarial tests exist for all P0/P1 properties. |
| **T-MATRIX-04** | Category sum & uniqueness arithmetic integrity | Meta | Verified category counts sum exactly to 117. |
| **T-SEC-01** | AES-256-GCM envelope encryption decrypts | M54-F13 | Valid key decrypts secret payload cleanly. |
| **T-SEC-02** | Missing master key fails closed with 500 | M54-F13 | Key absence halts encryption with fail-closed error. |
| **T-SEC-03** | Tampered ciphertext/tag fails GCM decryption | M54-F13 | Tampered ciphertext raises cryptographic exception. |

---

## 13. Comprehensive Security & Failure-Mode Analysis

| # | Failure / Attack Scenario | Authoritative Durable State | Recovery Action | Idempotency Mechanism | Security Boundary | Terminal Outcome |
|---|---|---|---|---|---|---|
| 1 | **Concurrent Admission Burst** | `tenant_webhook_capacities` row lock (`FOR UPDATE`) | Rejects 101st+ request with HTTP 429 | Atomically locked transaction | Ingress Gateway Admission | At most 100 events admitted; excess rejected. |
| 2 | **Crash During Admission** | Uncommitted database transaction | Transaction rolls back automatically | Client retries with identical body/signature | Ingress Gateway | Zero partial records in `inbound_events`. |
| 3 | **Crash After Attempt Reservation** | `inbound_events.status = 'processing'`, `attempts = N` | Lease expires; reconciler reclaims or dead-letters | Deduplication via `m54_inbound_{id}` key | Inbound Lease Fencing | At most 3 dispatches; zero duplicate tasks. |
| 4 | **Stale Worker Mutation** | Expired `lease_token` | Update rejected by `WHERE lease_token = :token` | Lease token check | PostgreSQL Fenced Update | State untouched by stale worker. |
| 5 | **Concurrent Replay Race** | Historical row in `dead_letter_events` | Second replay creates distinct delivery record | Unique delivery ID generation | Replay Service | Independent deliveries; audit trail intact. |
| 6 | **Terminal State Mutation** | `status IN ('processed', 'delivered', 'failed', 'dead_lettered')` | Database rejects update via CHECK / invariant | Immutable row policy | Database Schema Engine | Mutation raises fatal application error. |
| 7 | **Tenant Boundary Crossing** | `webhook_endpoints.user_id != session.user_id` | Endpoint lookup query returns empty | Scoped `WHERE user_id = :uid` query | Tenant Isolation Filter | Returns HTTP 404 with zero info leakage. |
| 8 | **Key Rotation Grace Window** | `webhook_signing_keys.key_status = 'retiring'` | Secondary trial verifies valid signature | Dual-key sequential verification | Cryptographic Key Engine | Valid webhook accepted without downtime. |
| 9 | **Provider ID Collision** | Existing `(endpoint_id, provider_event_id)` record | If payload SHA-256 matches: return 202 duplicate | Cached status return | Inbound Idempotency Filter | No duplicate task or automation created. |
| 10 | **Payload Hash Collision Attack** | Same ID with different SHA-256 | Rejects with HTTP 409 Conflict | Hash comparison check | Security Event Filter | HTTP 409 Conflict; audit event emitted. |
| 11 | **External Lineage Forgery** | Unsigned or invalid `X-AURA-Lineage-Token` | Lineage discarded; creates new root chain | HMAC verification on lineage token | Trust Demarcation Boundary | Caller cannot spoof causation or depth. |
| 12 | **Multi-Hop Event Loop** | Verified lineage token with `depth > 3` | Ingestion aborted with `EventLoopDetectedError` | Depth counter validation | Loop Detection Engine | Webhook rejected; loop audit log emitted. |
| 13 | **DNS Rebinding Attack** | Hostname resolves to public IP, rebinds to 127.0.0.1 | Transport connects strictly to pre-validated `pinned_ip` | Socket IP pinning | `PinnedIPTransport` | Socket connects only to pre-validated IP. |
| 14 | **Multi-IP Mixed DNS Set** | 1 public IP + 1 private IP returned | Entire hostname rejected with `SSRFMultiAddressViolationError` | Conservative multi-IP filter | SSRF Validator | Delivery fails terminal; 0 packets sent. |
| 15 | **Proxy Interception** | `HTTP_PROXY` / `HTTPS_PROXY` environment vars | Dedicated socket transport ignores all proxies | Direct TCP socket stream | Transport Layer Isolation | Packets bypass external proxy infrastructure. |
| 16 | **HTTP Redirect (3xx)** | Remote server returns 301/302 Redirect | Transport enforces `follow_redirects = False` | Immediate status evaluation | Transport Layer Isolation | Delivery transitions to terminal `failed`. |
| 17 | **Non-Standard Port Attack** | URL specifies port 22 (SSH) or 80 (HTTP) | Port validation rejects URL outside `443..8443` | Port whitelist range check | Port Validator | Rejection with `InvalidWebhookPortError`. |
| 18 | **Outbound Retry Exhaustion** | 5 consecutive HTTP 500 errors | Moves delivery to `dead_letter_events` | Exponential backoff retry loop | Outbound Retry Worker | Terminal `dead_lettered` state reached. |
| 19 | **Concurrent Maintenance Runs**| Bounded maintenance task invoked twice | `WHERE created_at < :cutoff LIMIT :batch` prunes safely | Idempotent cleanup query | Single Scheduler Boundary | Exact pruning without race conditions. |
| 20 | **Node Process Restart** | Database rows in `tenant_webhook_capacities` | Startup reconciler reconciles in-flight count | Durable PostgreSQL state | Application Lifecycle Manager | Exact capacity restored without leakage. |

---

## 14. Definition of Done (DoD) & Implementation Readiness

Milestone 54 is complete when:
1. Migration `005_enterprise_webhooks_and_event_gateway.sql` is applied on live PostgreSQL 16 with durable capacities, retry, lineage, unified dead-letter ledger, and replay schemas.
2. `PostgresWebhookRepository` and `InMemoryWebhookRepository` pass 100% of contract tests.
3. Inbound webhook endpoint validates HMAC-SHA256 signatures in constant time with dual-key rotation and rejects replays outside $\pm 300\text{s}$.
4. Outbound delivery worker delivers events with cryptographic headers and obeys bounded exponential backoff with full jitter.
5. SSRF protection blocks all private IPs, loopback, cloud metadata, and DNS rebinding via dedicated `PinnedIPTransport` (ports 443..8443).
6. Multi-hop event loops are detected and rejected at depth $\le 3$.
7. Inbound events create M52 tasks with untrusted payload containment (`<untrusted_source_content>`).
8. All 117 planned scenarios in the M54 test matrix are implemented and verified passing.
9. Full regression suite across M1–M54 passes with zero regressions.
10. Working tree clean and synchronized with `origin/antigravity-work`.

---

## 15. Final Architectural Decision

```
============================================================
M54 ARCHITECTURE: READY FOR IMPLEMENTATION
============================================================
```

- **Specification Completeness:** 100%
- **Baseline Invariance:** Verified (M50, M51, M52, M53 remain untouched)
- **Security Posture:** Fail-closed, SSRF-hardened with socket IP pinning, Port bounded (443..8443), Replay-immune, AES-256-GCM encrypted, Multi-tenant isolated, Dual-key rotatable, Authoritative row-locked capacity gated, Unified write-once dead-letter ledger.
