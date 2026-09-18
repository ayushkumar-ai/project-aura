# PROJECT AURA — M60 PRODUCTION OPERATIONS MANUAL
## Master Operator Runbook for Production Deployment, Reliability & Disaster Recovery

---

### 1. Executive Overview

This operations manual governs the deployment, maintenance, security hardening, key rotation, incident triage, and disaster recovery procedures for **Project AURA** running in production environments.

The AURA production service combines:
- **HTTP/REST Ingress API**: Multi-threaded request routing, trusted proxy filtering, CORS pinning, and token-bucket rate limiting (`app/server.py`, `core/rate_limiter.py`).
- **Autonomous AgentMesh Runtime (M59)**: Multi-agent orchestration, intent verification, deterministic budgeting, and reflection loops (`core/agent_mesh/`).
- **Multi-Provider ModelGateway (M51 / P2)**: Resilient LLM routing across Groq, OpenRouter, Mistral, Gemini, OpenAI with circuit breakers and fallback cascades.
- **Enterprise Asynchronous Infrastructure (M52–M55)**: Distributed background task workers, cron schedulers, webhook ingress/egress with HMAC signatures, and worker fleet lease fencing.
- **Cognitive & Multimodal State (M56–M58)**: Episodic/semantic memory graphs, content-addressable storage, perception caching, and platform device registries.
- **Durable Relational Persistence (M42–M50)**: PostgreSQL 16 + pgvector migrations (`001` through `010`).

---

### 2. Production Deployment Architecture

```
Internet Traffic
       │ (HTTPS / TLS)
       ▼
[ Reverse Proxy / Ingress ] ── (AWS ALB / Cloudflare / Nginx / Traefik)
       │ (Terminates TLS, sets X-Forwarded-For & X-Forwarded-Proto)
       ▼ (Internal Private Network CIDR)
[ AURA Production Server ] ── (Bound to 0.0.0.0:8000)
       ├── 1. Trusted Proxy Header Verification (AURA_TRUSTED_PROXY_CIDRS)
       ├── 2. CORS Allowed Origin Matching (AURA_CORS_ALLOWED_ORIGINS)
       ├── 3. In-Process Token Bucket Rate Limiting (core/rate_limiter.py)
       ├── 4. Principal Authentication & Tenant Isolation (M41)
       └── 5. Liveness (/health) & Readiness (/ready) Probes
               │
               ├─────────────────────────────────────────┐
               ▼                                         ▼
   [ PostgreSQL 16 + pgvector ]              [ Persistent Volumes ]
   - Schema Migrations 001–010               - .aura_artifacts/
   - Connection Pooling (min:1, max:10)      - .aura_checkpoints/
                                             - .aura_knowledge/
```

---

### 3. Environment Configuration & Secret Management

#### 3.1 Mandatory Production Environment Variables

| Variable | Description | Production Requirement | Example Value |
|:---|:---|:---|:---|
| `AURA_ENV` | Environment identifier | Must be `production` | `production` |
| `AURA_SERVER_API_KEY` | Master API Key for Bearer token auth | Required $\ge 16$ chars; high entropy; non-default | `prod_aura_sec_k89f...` |
| `AURA_API_KEY_AUTH_ENABLED`| Enforce authentication | Must be `true` | `true` |
| `AURA_CORS_ALLOWED_ORIGINS`| Allowed web origins | Explicit URLs; wildcard `*` strictly prohibited | `https://aura.example.com` |
| `AURA_TRUSTED_PROXY_CIDRS` | CIDRs of upstream proxies | Explicit CIDRs | `10.0.0.0/8,172.16.0.0/12` |
| `AURA_DATABASE_URL` | PostgreSQL connection string | `postgresql://` scheme with valid credentials | `postgresql://aura:pass@db:5432/aura_db` |
| `AURA_WEBHOOK_MASTER_KEY` | HMAC signature root key | Required $\ge 16$ chars; non-default | `prod_wh_master_...` |
| `AURA_RATE_LIMIT_ENABLED` | Public API rate limiter | Must be `true` | `true` |

#### 3.2 Secret Management Best Practices
- Never commit secrets to version control or Docker images.
- Inject secrets exclusively via environment variables or secret vaults (e.g. AWS Secrets Manager, HashiCorp Vault, Kubernetes Secrets).
- Use distinct API keys per deployment environment (development, staging, production).

---

### 4. Zero-Downtime Key & Credential Rotation

1. **Master API Key (`AURA_SERVER_API_KEY`) Rotation**:
   - Generate a new 32-character cryptographically random secret.
   - For blue-green or rolling deployments, update the standby instances first.
   - Update client applications to send the new Bearer token.
   - Decommission legacy tokens via the token management repository.
2. **Webhook Master Key (`AURA_WEBHOOK_MASTER_KEY`) Rotation**:
   - Deploy new key to AURA server instances.
   - Regenerate webhook endpoint signatures for outbound endpoints.
3. **Database Credentials**:
   - Create a secondary database user with identical privileges in PostgreSQL.
   - Update `AURA_DATABASE_URL` on AURA server.
   - Restart AURA instances; verify `/ready` returns HTTP 200.
   - Drop the deprecated database user.

---

### 5. Trusted Proxy & Forwarded Header Security

- **Proxy Trust Contract**: Forwarded headers (`X-Forwarded-For`, `X-Forwarded-Proto`) are trusted **only** when the direct socket connection originates from a peer IP within `AURA_TRUSTED_PROXY_CIDRS`.
- **Untrusted Traffic**: If traffic connects directly from an IP outside the trusted CIDRs, all forwarded headers are ignored, preventing IP and protocol spoofing attacks.
- **Configuration**:
  ```bash
  export AURA_TRUSTED_PROXY_CIDRS="10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1/32"
  ```

---

### 6. Public API Rate Limiting

The in-process rate limiter enforces category-scoped token buckets with bounded memory (max 50,000 buckets with LRU eviction):

- **Categories**:
  - `auth`: 10 requests/min, burst 5
  - `general_api`: 60 requests/min, burst 10
  - `agent_execution`: 20 requests/min, burst 5
  - `multimodal`: 30 requests/min, burst 5
  - `admin`: 30 requests/min, burst 5
- **Rate Limit Headers**:
  - `X-RateLimit-Limit`: Bucket burst capacity.
  - `X-RateLimit-Remaining`: Available tokens in current bucket.
  - `X-RateLimit-Reset`: Unix timestamp when bucket refills to full capacity.
  - `Retry-After`: Emitted on HTTP 429 indicating seconds to wait for token availability.

---

### 7. Health & Readiness Semantics

#### 7.1 Liveness Probe (`GET /health`)
- **Purpose**: Process vitality probe for supervisors (systemd, Kubernetes, container orchestrators).
- **Behavior**: Returns HTTP 200 `{"status": "healthy", ...}` as long as the Python process and event loop are responsive. Does **not** depend on database or external LLM availability.

#### 7.2 Readiness Probe (`GET /ready`)
- **Purpose**: Ingress routing probe determining if the instance can safely accept user requests.
- **States**:
  - `READY` (HTTP 200): All critical dependencies healthy (PostgreSQL pool connected, migrations 001–010 intact, storage writable, production config valid).
  - `DEGRADED` (HTTP 200): Critical dependencies healthy, but 1 or more optional dependencies (e.g. an LLM provider circuit open) are degraded.
  - `NOT_READY` (HTTP 503): Any critical dependency failed (database pool disconnected, storage unwritable, migration checksum mismatch). Ingress proxies must stop routing traffic to this instance.

---

### 8. Graceful Shutdown & Lifecycle Drain

When receiving `SIGTERM` or `SIGINT`:
1. The server ceases accepting new incoming TCP connections.
2. Background task workers (`BackgroundTaskWorker`, `DistributedFleetWorker`, `OutboundDeliveryWorker`) are signaled to drain in-flight jobs.
3. Runtime checkpoints are saved to `.aura_checkpoints/`.
4. Database connection pools are closed gracefully.
5. All teardown is bounded by `AURA_SHUTDOWN_GRACE_PERIOD_SECONDS` (default: 10s, max 300s).

---

### 9. Backup, Isolated Verification & Disaster Recovery

#### 9.1 Creating a Verified Backup
Run the automated backup utility to create a cryptographically signed backup tarball:
```bash
python scripts/backup_restore.py backup --output /var/backups/aura_backup_$(date +%Y%m%d_%H%M%S).tar.gz
```
The archive includes:
- PostgreSQL schema and data dump
- `.aura_checkpoints/`
- `.aura_knowledge/`
- `.aura_artifacts/`
- `backup_manifest.json` with SHA-256 hashes of every file

#### 9.2 Isolated Restore Verification (Default Sandbox Flow)
To verify backup integrity without risking production data, execute the restore utility against an isolated sandbox database:
```bash
python scripts/backup_restore.py restore \
  --archive /var/backups/aura_backup_latest.tar.gz \
  --target-db aura_restore_verify_db
```
*The default target `aura_restore_verify_db` ensures zero write operations are executed against the active production database.*

#### 9.3 Destructive Production Restore (Emergency Disaster Recovery Only)
In an emergency disaster recovery scenario where the active production database must be rebuilt from backup:
```bash
python scripts/backup_restore.py restore \
  --archive /var/backups/aura_backup_latest.tar.gz \
  --target-db postgresql://aura:pass@db:5432/aura_db \
  --active-prod-db aura_db \
  --force-destructive-production-restore
```
> [!CAUTION]
> The `--force-destructive-production-restore` flag is strictly required to overwrite `aura_db`. Without this explicit flag, the restore tool halts immediately.

---

### 10. Operational Incident Triage & Troubleshooting

#### 10.1 Startup Fails Closed
- **Symptom**: Server exits immediately on startup with `ValueError: Production configuration validation failed`.
- **Diagnosis**: Inspect logs for validation errors:
  - Check `AURA_SERVER_API_KEY` (must be $\ge 16$ characters, not a default string).
  - Check `AURA_CORS_ALLOWED_ORIGINS` (must not be `*` or empty).
  - Check `AURA_DATABASE_URL` (must use `postgresql://` scheme).
  - Check `AURA_WEBHOOK_MASTER_KEY` (must be non-default).

#### 10.2 `/ready` Returns HTTP 503 (`NOT_READY`)
- **Diagnosis**: Check the `critical_failures` list in the `/ready` JSON response:
  - If `database_unreachable`: verify PostgreSQL container/host is running and accepting TCP connections on port 5432.
  - If `storage_unwritable`: verify volume mount permissions on `.aura_artifacts`, `.aura_checkpoints`, and `.aura_knowledge`.
  - If `migration_integrity_failure`: check if any migration file in `migrations/` was modified post-application.

#### 10.3 `/ready` Returns HTTP 200 with State `DEGRADED`
- **Diagnosis**: Inspect `providers` and `degraded_reasons` in the response:
  - An optional LLM provider (e.g. Groq, OpenRouter, Mistral) has exceeded failure thresholds and tripped its circuit breaker.
  - AURA will automatically route requests to available fallback providers configured in `AURA_MODEL_FALLBACK_PROVIDERS`.

#### 10.4 HTTP 429 Too Many Requests
- **Diagnosis**: Client is exceeding category rate limits. Check `Retry-After` header and configure client retry backoff.

---

### 11. Rollback Procedures

If a newly deployed application build fails health checks:
1. Revert deployment traffic to the previous known-good container image or release commit.
2. Schema migrations `001` through `010` are backward-compatible.
3. Verify `/ready` returns `READY` or `DEGRADED` (HTTP 200) across all reverted instances.
