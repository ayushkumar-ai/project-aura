# Project AURA — Production Deployment & Operations Guide

This guide provides end-to-end instructions for deploying, configuring, monitoring, and operating **Project AURA** in production environments.

---

## 1. Quickstart: Canonical Startup Commands

### Run as Standalone Production HTTP Service
```bash
# Start server listening on 0.0.0.0:8000
python -m app.server --host 0.0.0.0 --port 8000

# Or via the unified CLI
python -m app.main --server --host 0.0.0.0 --port 8000
```

### Run in Interactive CLI Mode
```bash
python -m app.main --interactive
```

### Run an Autonomous Task from CLI
```bash
python -m app.main --task "Analyze churn dataset and synthesize prediction pipeline"
```

---

## 2. Environment Configuration

Configure AURA using environment variables or a `.env` file in the project root:

| Variable | Default | Purpose |
|---|---|---|
| `AURA_ENV` | `development` | Environment mode (`production`, `development`, `testing`). In `production`, internal tracebacks and sensitive details are masked from API error responses. |
| `AURA_LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `AURA_SERVER_HOST` | `0.0.0.0` | Host interface to bind. |
| `AURA_SERVER_PORT` | `8000` | Port to listen on. |
| `AURA_SERVER_API_KEY` | `""` | Bearer token required for API access when auth is enabled. |
| `AURA_API_KEY_AUTH_ENABLED`| `false` | When `true`, all endpoints (except `/health` and `/ready`) require `Authorization: Bearer <key>`. |
| `AURA_MAX_REQUEST_BODY_BYTES` | `1048576` (1MB) | Maximum allowed payload size for HTTP requests. |
| `AURA_MODEL_PROVIDER` | `fake` | Model provider: `fake`, `openai`, `generic`. |
| `AURA_MODEL_NAME` | `fake-model-v1` | LLM model identifier (e.g. `gpt-4o`, `claude-3-5-sonnet`). |
| `AURA_API_KEY` | `""` | Model provider API credential. |
| `AURA_CHECKPOINT_DIR` | `.aura_checkpoints` | Atomic session state crash-recovery snapshots directory. |
| `AURA_ARTIFACT_STORAGE_DIR` | `.aura_artifacts` | Content-Addressable Storage (CAS) blob and manifest directory. |
| `AURA_TRACE_STORAGE_DIR` | `.aura_traces` | Distributed execution trace directory. |
| `AURA_SKILLS_STORAGE_DIR` | `.aura_skills` | Dynamic Python skill catalog persistence. |
| `AURA_KNOWLEDGE_STORAGE_DIR` | `.aura_knowledge` | Epistemic knowledge graph storage directory. |

---

## 3. Production HTTP REST API Endpoints

### 🩺 Health & Probes
- **Liveness Probe**: `GET /health`
  - Unauthenticated probe returning HTTP 200 and uptime status when process is responsive.
  - Response: `{"status": "healthy", "version": "0.28.0", "uptime_seconds": 12.34}`
- **Readiness Probe**: `GET /ready`
  - Returns HTTP 200 when model provider and core orchestration runtime are initialized and ready to serve requests.
  - Response: `{"status": "ready", "ready": true, "model_provider": "openai"}`
- **Telemetry & Metrics**: `GET /metrics` or `GET /v1/telemetry`
  - Aggregated system diagnostics: worker counts, goals stepped, lock counts, scheduler queue depth, and provider circuit breaker health.

### ⚡ Execution Endpoints
- **Run Prompt / Request**: `POST /v1/run`
  - Header: `Content-Type: application/json`
  - Body: `{"user_input": "Calculate compound interest", "metadata": {}}`
  - Response: `{"request_id": "req_...", "content": "...", "metadata": {}}`
- **Run Autonomous Task**: `POST /v1/task`
  - Body: `{"task": "Build machine learning pipeline", "timeout": 300}`
  - Response: `{"task_id": "...", "status": "completed", "result": "..."}`

### 📚 Dynamic Catalogs & Knowledge
- **List Dynamic Skills**: `GET /v1/skills`
- **List Campaigns**: `GET /v1/campaigns`
- **List Artifacts**: `GET /v1/artifacts`
- **Query Epistemic Knowledge**: `GET /v1/knowledge/query?capability=web_scraping`

---

## 4. Docker Deployment

### Build Container Image
```bash
docker build -t project-aura:0.28.0 .
```

### Run Container
```bash
docker run -d \
  --name aura-service \
  -p 8000:8000 \
  -e AURA_ENV=production \
  -e AURA_MODEL_PROVIDER=openai \
  -e AURA_API_KEY=sk-... \
  -e AURA_SERVER_API_KEY=secret-token-123 \
  -e AURA_API_KEY_AUTH_ENABLED=true \
  -v aura_checkpoints:/app/.aura_checkpoints \
  -v aura_artifacts:/app/.aura_artifacts \
  -v aura_knowledge:/app/.aura_knowledge \
  project-aura:0.28.0
```

### Run via Docker Compose
```bash
docker compose up -d
```

---

## 5. Systemd Service Deployment (Linux)

Create `/etc/systemd/system/aura.service`:

```ini
[Unit]
Description=Project AURA Autonomous Personal Intelligence Service
After=network.target

[Service]
Type=simple
User=aura
Group=aura
WorkingDirectory=/opt/project-aura
EnvironmentFile=/opt/project-aura/.env
ExecStart=/opt/project-aura/.venv/bin/python -m app.server --host 0.0.0.0 --port 8000
ExecStop=/bin/kill -s SIGINT $MAINPID
TimeoutStopSec=15
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable aura
sudo systemctl start aura
sudo systemctl status aura
```

---

## 6. Backup & Recovery

### Persistence Volumes
1. **Checkpoints (`.aura_checkpoints/`)**: Atomic state snapshots. Can be copied or backed up while running.
2. **Artifacts (`.aura_artifacts/`)**: Content-addressable immutable blobs (`blobs/`) and JSON manifests (`manifests/`).
3. **Epistemic Knowledge Graph (`.aura_knowledge/`)**: Entity-relation graph persistence.

### Recovery from Backup
To restore state on a new instance:
1. Place checkpoint folder into `.aura_checkpoints/`.
2. Start AURA. The runtime automatically discovers the latest valid checkpoint and restores active goals, resource budgets, locks, and knowledge graphs.
