# PROJECT AURA — MILESTONE 58 ARCHITECTURE & DESIGN SPECIFICATION
## Real-World Device, Desktop OS & Platform Integration
### Production-Grade • Multi-Tenant • Risk-Tiered Capabilities • Human Approval Gating • Path-Traversal-Proof • Audit-Ready

**Document Version:** 1.0.0-PROD-QUALIFIED  
**Status:** ARCHITECTURALLY QUALIFIED — READY FOR IMPLEMENTATION  
**Target Milestone:** M58  
**Authoritative Parent Commit (M57):** `49d6fe2eaefaa7016552a658b14c8bfae042efef`  
**Grandparent Baseline (M56):** `d0afb213c01ca1f835563317af038ba51cc92329`  
**Branch:** `antigravity-work`  

---

## 1. Executive Summary & System Overview

Milestone 58 (M58) establishes the **Production Platform & Real-World Device Integration Substrate** for Project AURA. It enables secure, policy-governed, human-approval-gated interactions with host operating systems (Windows, WSL2, Linux) and external user-authorized devices.

```
+===================================================================================================+
|                                  AURA PLATFORM INTEGRATION GATEWAY                                |
|                                                                                                   |
|   +-------------------------------------------------------------------------------------------+   |
|   | CLIENT / API INTAKE LAYER                                                                 |   |
|   |  - Authentication (Bearer Token / API Key) -> Principal Context (tenant_id, UserRole)     |   |
|   |  - Device Registration (/v1/devices, /v1/devices/{id}/authorize)                         |   |
|   |  - Capability Execution (/v1/devices/{id}/execute)                                        |   |
|   +-------------------------------------------------------------------------------------------+   |
|                                          │                                                        |
|                                          ▼                                                        |
|   +-------------------------------------------------------------------------------------------+   |
|   | SECURITY, RISK & POLICY ENFORCEMENT ENGINE                                                |   |
|   |  - Tenant Scoping & Device Trust State (PENDING, VERIFIED, AUTHORIZED, SUSPENDED, REVOKED)|   |
|   |  - Capability Authorization State (DECLARED, VERIFIED, AUTHORIZED, DISABLED, REVOKED)     |   |
|   |  - Risk Classification (LOW, MEDIUM, HIGH, CRITICAL)                                      |   |
|   |  - M48 Human Approval Gateway: Required for HIGH / CRITICAL Risk Operations                |   |
|   |  - Fail-Closed Policy Evaluation                                                          |   |
|   +-------------------------------------------------------------------------------------------+   |
|                                          │                                                        |
|                                          ▼                                                        |
|   +-------------------------------------------------------------------------------------------+   |
|   | PLATFORM INTEGRATION GATEWAY / EXECUTOR                                                   |   |
|   |  - Deterministic Capability Resolution                                                     |   |
|   |  - Idempotency & Replay Resistance                                                        |   |
|   |  - Safe Path Normalization & Traversal Prevention                                         |   |
|   |  - Bounded Timeouts & Resource Ceilings                                                   |   |
|   +-------------------------------------------------------------------------------------------+   |
|                     │                                            │                                |
|                     ▼                                            ▼                                |
|   +─────────────────────────────────────+      +──────────────────────────────────────────────+   |
|   | TYPED PLATFORM ADAPTERS             |      | PERSISTENCE & LIFECYCLE (Migration 009)      |   |
|   |  - DesktopOSAdapter (Windows / WSL) |      |  - devices                                   |   |
|   |  - LinuxOSAdapter                   |      |  - device_capabilities                      |   |
|   |  - SimulatedPlatformAdapter (Test)  |      |  - device_sessions                           |   |
|   |  - Execution Modes: REAL, SIMULATED |      |  - device_execution_records                  |   |
|   +─────────────────────────────────────+      |  - device_audit_events                       |   |
|                     │                          |  - Tenant-Scoped Cascade Deletion            |   |
|                     ▼                          +──────────────────────────────────────────────+   |
|   +─────────────────────────────────────+                        │                                |
|   | RESULT NORMALIZATION & TRUST        |                        │                                |
|   |  - Output Tagged: DEVICE_OBSERVED   |                        │                                |
|   |  - Subordinate to User / Policy     |                        │                                |
|   |  - Zero Self-Authority Escalation   |                        │                                |
|   +─────────────────────────────────────+                        │                                |
|                     │                                            │                                |
|                     ▼                                            ▼                                |
|   +───────────────────────────────────────────────────────────────────────────────────────────+   |
|   | DOWNSTREAM COGNITIVE & TASK INTEGRATIONS                                                  |   |
|   |  - M56 Cognitive Memory: Bridges observations with tool_observed provenance                |   |
|   |  - M57 Multimodal Pipeline: Media / screenshots routed to M57 processor                   |   |
|   |  - M52 Tasks / M53 Automation: Asynchronous platform task scheduling                      |   |
|   |  - M44 Observability: Low-cardinality telemetry (aura_device_operations_total)            |   |
|   +───────────────────────────────────────────────────────────────────────────────────────────+   |
+===================================================================================================+
```

---

## 2. Domain Types & Enums

### 2.1 Device Type, Platform & Trust States
```python
class PlatformType(str, Enum):
    WINDOWS = "windows"
    WSL = "wsl"
    LINUX = "linux"
    MACOS = "macos"
    ANDROID = "android"
    GENERIC = "generic"

class DeviceType(str, Enum):
    DESKTOP = "desktop"
    MOBILE = "mobile"
    SERVER = "server"
    IOT_DEVICE = "iot_device"
    VIRTUAL_ENVIRONMENT = "virtual_environment"

class DeviceTrustState(str, Enum):
    UNREGISTERED = "unregistered"
    PENDING_VERIFICATION = "pending_verification"
    VERIFIED = "verified"
    AUTHORIZED = "authorized"
    SUSPENDED = "suspended"
    REVOKED = "revoked"

class CapabilityRiskLevel(str, Enum):
    LOW = "low"            # Read-only telemetry, clock, battery, system info
    MEDIUM = "medium"      # Sandboxed file read/write, screenshot, notifications
    HIGH = "high"          # File deletion, service restart, shell tool, outbound messaging
    CRITICAL = "critical"  # Credential access, security settings, irreversible actions

class CapabilityAuthStatus(str, Enum):
    DECLARED = "declared"
    VERIFIED = "verified"
    AUTHORIZED = "authorized"
    DISABLED = "disabled"
    REVOKED = "revoked"

class ExecutionMode(str, Enum):
    REAL = "real"
    SIMULATED = "simulated"
    MOCK = "mock"
    UNAVAILABLE = "unavailable"
```

---

## 3. Database Schema: Migration 009

```sql
-- Migration 009: Real-World Device, Desktop OS & Platform Integration

-- 1. Devices Table
CREATE TABLE IF NOT EXISTS devices (
    device_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(256) NOT NULL,
    device_type VARCHAR(32) NOT NULL,
    platform VARCHAR(32) NOT NULL,
    platform_version VARCHAR(128) NOT NULL DEFAULT '',
    trust_state VARCHAR(32) NOT NULL DEFAULT 'pending_verification',
    hostname VARCHAR(256) NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_device_trust CHECK (trust_state IN ('unregistered', 'pending_verification', 'verified', 'authorized', 'suspended', 'revoked'))
);

CREATE INDEX IF NOT EXISTS idx_devices_tenant_trust ON devices(tenant_id, trust_state);

-- 2. Device Capabilities Table
CREATE TABLE IF NOT EXISTS device_capabilities (
    capability_id VARCHAR(128) PRIMARY KEY,
    device_id VARCHAR(128) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(128) NOT NULL,
    risk_level VARCHAR(32) NOT NULL DEFAULT 'low',
    auth_status VARCHAR(32) NOT NULL DEFAULT 'declared',
    description TEXT NOT NULL DEFAULT '',
    parameters_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    requires_approval BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_cap_risk CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    CONSTRAINT chk_cap_auth CHECK (auth_status IN ('declared', 'verified', 'authorized', 'disabled', 'revoked'))
);

CREATE INDEX IF NOT EXISTS idx_device_caps_tenant ON device_capabilities(tenant_id, device_id);

-- 3. Device Sessions Table
CREATE TABLE IF NOT EXISTS device_sessions (
    session_id VARCHAR(128) PRIMARY KEY,
    device_id VARCHAR(128) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    ip_address VARCHAR(64) NOT NULL DEFAULT '',
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NULL,
    last_heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_device_sessions_tenant ON device_sessions(tenant_id, device_id);

-- 4. Device Execution Records Table
CREATE TABLE IF NOT EXISTS device_execution_records (
    execution_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id VARCHAR(128) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    capability_name VARCHAR(128) NOT NULL,
    risk_level VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'requested',
    execution_mode VARCHAR(32) NOT NULL DEFAULT 'real',
    idempotency_key VARCHAR(128) NULL,
    approval_token VARCHAR(128) NULL,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_detail TEXT NULL,
    duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_device_exec_tenant_device ON device_execution_records(tenant_id, device_id);
CREATE INDEX IF NOT EXISTS idx_device_exec_idemp ON device_execution_records(tenant_id, idempotency_key);

-- 5. Device Audit Events Table
CREATE TABLE IF NOT EXISTS device_audit_events (
    event_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id VARCHAR(128) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    action VARCHAR(128) NOT NULL,
    principal_id VARCHAR(128) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    risk_level VARCHAR(32) NOT NULL DEFAULT 'low',
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_device_audit_tenant ON device_audit_events(tenant_id, created_at);
```

---

## 4. Formal Architectural Invariants (M58-F01 through M58-F35)

| Invariant ID | Title | Specification |
| :--- | :--- | :--- |
| **M58-F01** | Tenant Ownership | Every device, capability, session, execution, and audit record MUST have a non-null `tenant_id` referencing a valid user principal. |
| **M58-F02** | Strict Tenant Isolation | Cross-tenant device queries, registration, capability updates, or executions are strictly prohibited and fail closed (HTTP 404/403). |
| **M58-F03** | Authenticated Device Identity | Device identity is resolved against persistent server records for the authenticated tenant; unverified client IDs are rejected. |
| **M58-F04** | Capability Authorization | Capability existence does NOT confer permission. Execution requires explicit `AUTHORIZED` status. |
| **M58-F05** | Device Trust Enforcement | Devices in `REVOKED`, `SUSPENDED`, or `PENDING_VERIFICATION` states cannot execute any operations. |
| **M58-F06** | Revocation Immutability | A revoked device cannot execute operations or re-declare capabilities without complete re-registration. |
| **M58-F07** | Policy Enforcement | All device operations evaluate against AURA policy; policy denials fail closed with zero adapter fallback. |
| **M58-F08** | Human Approval Requirement | Operations classified as `HIGH` or `CRITICAL` risk require valid M48 human approval tokens before dispatch. |
| **M58-F09** | No Model Self-Approval | Language models cannot approve device actions or generate synthetic authorization tokens. |
| **M58-F10** | No Multimodal Self-Authorization | Multimodal data or OCR text cannot self-authorize or trigger platform operations. |
| **M58-F11** | Command Boundary | Raw shell commands from untrusted model outputs are strictly prohibited; execution is restricted to typed, allowlisted capabilities. |
| **M58-F12** | Shell Safety & Sandboxing | Where safe commands run, arguments are sanitized and executed without shell interpolation (`shell=False`). |
| **M58-F13** | Path Traversal Protection | File paths are normalized and verified against configured sandbox directories; traversal attempts (`..`, UNC escapes, symlink escapes) are rejected. |
| **M58-F14** | Transport Authentication | Transports require authenticated tenant tokens; unauthenticated transport messages are discarded. |
| **M58-F15** | Replay Resistance | Execution requests support nonce/timestamp validation and idempotency keys. |
| **M58-F16** | Bounded Execution | Every platform operation is bounded by configurable hard execution timeouts (default 30s). |
| **M58-F17** | Bounded Retry | Transient transport failures retry up to a hard maximum of 3 attempts; fatal/policy errors never retry. |
| **M58-F18** | Idempotency | Repeated execution requests with matching `(tenant_id, idempotency_key)` return cached results without duplicate execution. |
| **M58-F19** | Failure-State Correctness | Execution failures record explicit `FAILED` status with sanitized error details; no hanging states. |
| **M58-F20** | Unknown-Outcome Correctness | If an operation timeout occurs mid-flight, status is marked `UNKNOWN` or `TIMED_OUT`, never `SUCCEEDED`. |
| **M58-F21** | Device Result Trust | Device output is tagged as `DEVICE_OBSERVED` passive observation and remains subordinate to User and Policy authority. |
| **M58-F22** | Memory Provenance | Device observations admitted to M56 cognitive memory are tagged with `tool_observed` provenance and capped confidence. |
| **M58-F23** | Memory Deletion Sync | Deleting a device cascades to the deletion of all derived cognitive memories with zero resurrection. |
| **M58-F24** | M57 Boundary Preservation | Screenshots and media from devices are processed via M57 multimodal processor; M58 does not duplicate multimodal engines. |
| **M58-F25** | M48 Approval Preservation | M58 reuses M48 approval infrastructure without creating a parallel approval mechanism. |
| **M58-F26** | M52 Task Boundary | Asynchronous platform executions use M52 task infrastructure. |
| **M58-F27** | M53 Automation Boundary | Scheduled platform triggers use M53 automation triggers. |
| **M58-F28** | M55 Fleet Boundary | Distributed worker dispatch for platform tasks respects M55 worker fleet constraints. |
| **M58-F29** | Secret Protection | Device credentials and sensitive tokens are scrubbed from logs, errors, and database strings. |
| **M58-F30** | Observability Privacy | Metrics use low-cardinality labels (`device_type`, `platform`, `operation`, `status`); raw command text and paths are excluded. |
| **M58-F31** | Auditability | All device registrations, capability authorizations, and executions record immutable audit events. |
| **M58-F32** | Capability Determinism | Capability resolution is deterministic; unregistered or unsupported capabilities fail closed. |
| **M58-F33** | Concurrent Execution Safety | In-memory and PostgreSQL repositories use thread locks and database transactions to ensure race freedom. |
| **M58-F34** | Revocation Race Safety | Mid-flight operations check device trust status before and after execution; revoked devices cannot commit results. |
| **M58-F35** | Stale Session Safety | Expired device sessions are invalidated automatically during authentication. |

---

## 5. Adversarial Security Scenarios

1. `TEST-M58-SEC-01`: Tenant B attempting to execute an action on Tenant A's device (Cross-tenant rejection).
2. `TEST-M58-SEC-02`: Executing an action on a `REVOKED` or `SUSPENDED` device (Trust state rejection).
3. `TEST-M58-SEC-03`: Executing an unauthorized capability (`auth_status != 'authorized'`).
4. `TEST-M58-SEC-04`: Attempting a `HIGH` risk action without M48 human approval token.
5. `TEST-M58-SEC-05`: Attempting a `CRITICAL` risk action with an invalid/expired approval token.
6. `TEST-M58-SEC-06`: Path traversal attack attempting to read outside sandbox (`../../windows/system32/cmd.exe` or `/etc/shadow`).
7. `TEST-M58-SEC-07`: Command injection attack via parameter tampering (`safe_arg; rm -rf /`).
8. `TEST-M58-SEC-08`: Prompt injection in model output attempting raw shell execution.
9. `TEST-M58-SEC-09`: SSRF attempt via local device URL parameters (`http://169.254.169.254`).
10. `TEST-M58-SEC-10`: Replay attack with duplicate request (Idempotency verification).
11. `TEST-M58-SEC-11`: Unauthenticated device registration attempt.
12. `TEST-M58-SEC-12`: Device output attempting to inject system instructions.
13. `TEST-M58-SEC-13`: Cascade deletion of device permanently wiping derived cognitive memories.
14. `TEST-M58-SEC-14`: Secret masking in device execution error outputs.
15. `TEST-M58-SEC-15`: Execution timeout bounding mid-flight long-running operation.

---

**AUTHORITATIVE ARCHITECTURAL SPECIFICATION APPROVED FOR PROJECT AURA MILESTONE 58.**
