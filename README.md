# Project AURA — Autonomous Personal Intelligence Platform

[![Milestone](https://img.shields.io/badge/milestones-M1%20to%20M60-blue.svg)](docs/MILESTONES.md)
[![M60 Qualification](https://img.shields.io/badge/M60%20Qualification-Fully%20Qualified-brightgreen.svg)](docs/m60_production_operations_manual.md)
[![Security](https://img.shields.io/badge/security-AST%20Sandboxed%20%7C%20Deterministic%20Policy-orange.svg)](docs/SECURITY_AND_GOVERNANCE.md)

**Project AURA** is an open, privacy-conscious, context-aware, goal-driven personal AI platform designed to transform high-level human objectives into persistent, observable, verified, and recoverable execution.

AURA goes beyond simple chatbot wrappers and single-turn prompt loops: it functions as a **complete personal intelligence operating system and production-qualified multi-agent orchestration runtime**.

---

## 📌 Project Status

- **Current Production Baseline**: **Milestone 60 (M60)** — Production Deployment, Reliability & Real-World Validation
- **Qualification Verdict**: **`FULLY QUALIFIED — ALL CONDITIONS CLOSED`**
- **Architecture Maturity**: Complete progression across **M1 through M60**, including product feature tracks **P1** (Web UI) and **P2** (Multi-Provider Model Gateway).

> [!NOTE]
> **Production Qualification Baseline**: M60 represents a fully verified, production-hardened engineering baseline tested against real-world PostgreSQL 16 + pgvector containers, live Gemini LLM APIs, and end-to-end operational controls. It certifies system reliability and security readiness for deployment rather than an active public SaaS hosting instance.

---

## 🏆 Verified M60 Qualification Evidence

M60 real-world qualification verified all core subsystems against live external infrastructure:

| Qualification Domain | Scope & Test Suite | Results | Status |
| :--- | :--- | :--- | :--- |
| **PostgreSQL 16 + pgvector (C1)** | 8 integration suites (M52–M59) against live PostgreSQL 16.15 + pgvector 0.8.6 | **37 / 37 passed** (0 failed, 0 errors, 0 skipped) | **CLOSED** |
| **Live External LLM (C2)** | End-to-end multi-turn, structured reasoning, policy governance & safety on live `gemini-3.6-flash` | **5 / 5 passed** (0 failed, 0 errors) | **CLOSED** |
| **Model Gateway & Routing** | Multi-provider fallback, capability routing, circuit breaking, cost tracking (M51 + P2) | **63 / 63 passed** (0 failed, 0 errors) | **CLOSED** |
| **Operational & Security Controls (C4)** | Fail-closed config, LRU token-bucket rate limiting, trusted proxy XFF, backup/restore safety | **30 / 30 passed** (0 failed, 0 errors) | **CLOSED** |
| **Full Regression Baseline** | Complete repository test suite across all architectural planes | **1,816 passed** (1,819 collected, 0 failed, 0 errors, 3 skipped*) | **VERIFIED** |

*\*Note: 3 test skips in the broad regression suite correspond to upstream rate-limited provider tests during initial batch runs; the dedicated live Gemini qualification subsequently verified all live scenarios at 5/5.*

---

## 🏛️ Layered System Architecture

AURA is organized across modular planes enforcing strict separation of concerns, deterministic governance, and observable dataflow:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   USER & CLIENT INTERFACE PLANE                        │
│        Web Product UI (P1) • Interactive CLI • REST API • Webhooks     │
├────────────────────────────────────────────────────────────────────────┤
│                         AURA FACADE (app/aura.py)                      │
├────────────────────────────────────────────────────────────────────────┤
│                         1. COGNITIVE PLANE                             │
│     Intent • Context • Reasoning • Task Planning • Reflection          │
├────────────────────────────────────────────────────────────────────────┤
│                   2. MODEL GATEWAY & ROUTING PLANE                     │
│ Multi-Provider Routing (Gemini, Groq, OpenRouter, Mistral) • Fallback  │
│ Capability Matching • Token & Cost Tracking • Circuit Breakers         │
├────────────────────────────────────────────────────────────────────────┤
│                    3. EXECUTION & MESH PLANE                           │
│ Goals • Dynamic Skills • Multi-Agent Mesh • Sagas • Distributed Fleet  │
├────────────────────────────────────────────────────────────────────────┤
│                  4. KNOWLEDGE & MEMORY PLANE                           │
│ Cognitive Memory • Contradiction Resolution • RAG & pgvector Retrieval │
│ Epistemic Knowledge Graph • Multimodal Artifacts • Dataflow Channels   │
├────────────────────────────────────────────────────────────────────────┤
│                   5. PLATFORM & DEVICE PLANE                           │
│ Desktop & Device Integration • Sandboxed Tools • Execution Services    │
├────────────────────────────────────────────────────────────────────────┤
│                  6. EVALUATION & ADAPTATION PLANE                      │
│ Trajectory Verification • Benchmarks • Closed-Loop Adaptive Optimizer  │
├────────────────────────────────────────────────────────────────────────┤
│                  7. RELIABILITY & RECOVERY PLANE                       │
│ Causal Fault Diagnosis • Multi-Tier Healing • Backup/Restore Safety    │
├────────────────────────────────────────────────────────────────────────┤
│                   8. GOVERNANCE & TRUST PLANE                          │
│ Fail-Closed Config • Deterministic Policy • Taint Tracking • HITL      │
│ LRU Rate Limiting • Trusted Proxy Parsing • AST Security Sandboxes     │
├────────────────────────────────────────────────────────────────────────┤
│                   9. PERSISTENT STORAGE LAYER                          │
│ PostgreSQL 16 + pgvector • Alembic Migrations (001–010) • CAS Storage  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## ✨ Key Capabilities

- **Intelligent Multi-Provider Model Gateway**: Dynamic routing across Google Gemini, Groq, OpenRouter, and Mistral with capability filtering, automatic failover cascades, circuit breaking, token usage tracking, and cost accounting.
- **Hierarchical Goal DAGs & Multi-Goal Scheduling**: Dynamic task planning, topological dependency resolution, token/CPU resource budgeting, and mutual exclusion locking.
- **Multi-Agent Mesh & Autonomous Runtime**: Agent delegation, execution trees, state snapshots, consensus voting, debate, and collaborative mesh topologies.
- **Cognitive Memory & Epistemic Graphs**: Multi-tier memory, contradiction detection and resolution, profile upserting, experience patterns, and semantic vector retrieval with pgvector.
- **Multimodal Processing & Artifact Pipeline**: Asynchronous media ingestion, artifact extraction, and content-addressable storage (CAS) with SHA-256 integrity verification.
- **Real-World Platform & Device Integration**: Bounded device management, peripheral capability discovery, and sandboxed platform action execution.
- **Proactive Automation & Distributed Fleet**: Background task execution, cron/scheduled triggers, webhook ingest/dispatch with replay atomicity, and distributed worker leasing with monotonic fencing tokens.
- **Dynamic Skill Synthesis**: Autonomous Python tool generation with AST syntax verification, safety policy screening, and execution sandboxing.
- **Causal Fault Diagnosis & Multi-Tier Self-Healing**: Root-cause tracing over execution graphs, automated retry/escalation policies, and compensating saga rollbacks.
- **Production-Grade Governance**: `Autonomy ≠ Authorization`—fail-closed configuration, deterministic policy boundaries, human-in-the-loop (HITL) approval gates, LRU rate limiting, and trusted-proxy validation.

---

## 🗺️ Milestone Evolution

The Project AURA engineering roadmap spans foundational research through production-grade qualification:

- **M1–M28: Core Cognitive Engine & Multi-Agent Collaboration**: Foundational architecture, prompt engineering, context management, reflection loops, dynamic skill synthesis, causal tracing, epistemic graphs, and saga execution.
- **M29–M40: Extended Architecture & System Hardening**: Distributed state, event bus, resilient execution, and failure recovery.
- **M41–M50: Production Data, Security & API Foundation**: Identity and user isolation (M41), persistent PostgreSQL data layer (M42), production RAG & vector retrieval (M43), observability & metrics (M44), FastAPI server (M45), web interface (M46), tool ecosystem (M47), bounded planning & approvals (M48), adversarial security & privacy hardening (M49), and baseline qualification (M50).
- **M51–M55: Gateway, Workflows & Fleet Scaling**: Resilient model gateway (M51), background task workflows & approval lifecycle (M52), proactive automation & scheduled triggers (M53), webhook delivery system (M54), and distributed worker fleet scaling (M55).
- **M56–M59: Cognitive Memory, Multimodal, Platform & Enterprise Mesh**: Advanced cognitive memory & contradiction resolution (M56), multimodal processing (M57), desktop and device platform integration (M58), and unified autonomous agent mesh runtime (M59).
- **Feature Tracks (P1 & P2)**: AURA Web Product UI (P1) and Expanded Multi-Provider Model Gateway (P2 M51) adding Groq, OpenRouter, Mistral, capability routing, and cost accounting.
- **M60: Production Deployment, Reliability & Real-World Validation**: Fail-closed configuration validation, LRU token-bucket rate limiting with `Retry-After`, trusted-proxy headers, disaster recovery backup/restore with SHA-256 checksums, and live PostgreSQL + Gemini real-world requalification.

---

## 🔒 Security Principles

1. **Safety Before Autonomy**: Autonomous decisions never bypass deterministic security policies.
2. **Deterministic Boundaries**: Probabilistic LLM outputs cannot directly execute system actions without passing strict AST policy inspection, sandboxing, and approval gates.
3. **Fail-Closed by Design**: Unrecognized proxy headers, missing authentication credentials, and unverified production configurations halt execution safely.
4. **Tenant Isolation**: Strict logical data separation and multi-tenant partitioning across all storage, vector embeddings, and agent mesh runs.
5. **Causal Traceability & Secret Scrubbing**: All actions, state mutations, and data derivations are causally logged with automatic redaction of sensitive API keys and tokens.

---

## 📚 Documentation

Comprehensive architectural and operational specifications are available in [`docs/`](docs/):

- [**Architecture Blueprint**](docs/ARCHITECTURE.md) — Comprehensive multi-plane architecture specification.
- [**Milestones Roadmap**](docs/MILESTONES.md) — Detailed milestone history from M1 through M60.
- [**Production Operations Manual (M60)**](docs/m60_production_operations_manual.md) — Production deployment, configuration, backup/restore, monitoring, and runbooks.
- [**Security & Governance**](docs/SECURITY_AND_GOVERNANCE.md) — Deterministic policy, AST sandboxing, taint isolation, and HITL approval.
- [**Subsystem Reference**](docs/SUBSYSTEMS.md) — Detailed technical deep dive into all core execution engines.
- [**Developer Guide**](docs/DEVELOPER_GUIDE.md) — Quickstart, testing, API facade usage, and developer setup.

---

## 🚀 Quickstart

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/ayushkumar-ai/project-aura.git
cd project-aura

# Activate virtual environment
.\.venv\Scripts\Activate.ps1  # Windows
source .venv/bin/activate     # Linux / macOS

# Install dependencies
pip install -r requirements.txt
```

### 2. Run Tests

```bash
# Run unit and core test suite
python -m pytest tests/

# Run M60 production readiness suite
python -m pytest tests/test_m60_production_readiness.py

# Run live PostgreSQL integration tests (requires active PostgreSQL instance)
pytest tests/test_m52_postgres_integration.py tests/integration/test_m53_postgres_automation_repo.py tests/integration/test_m54_postgres_webhook_integration.py tests/integration/test_m55_postgres_fleet_integration.py tests/integration/test_m56_postgres_memory_integration.py tests/integration/test_m57_postgres_multimodal_integration.py tests/integration/test_m58_postgres_platform_integration.py tests/integration/test_m59_postgres_mesh_integration.py
```

### 3. Start Production Server

```bash
# Start FastAPI production server
python -m app.server
```

### 4. Interactive CLI

```bash
python -m app.main --interactive
```

### 5. Python API Usage

```python
from app.main import create_aura

# Create full AURA instance with agentic runtime
aura = create_aura(agentic=True)

# Run an objective
response = aura.run("What is Project AURA?")
print(response.content)

# Synthesize and run a dynamic skill
skill = aura.synthesize_skill(
    name="double_num",
    description="Doubles a number",
    source_code="def execute(x): return str(int(x) * 2)",
    verify_after_synthesis=True,
)
aura.register_dynamic_skill(skill, activate=True)
print(aura.execute_dynamic_skill("double_num", "21"))  # Output: 42
```

---

## ⚠️ Qualification Boundaries & Limitations

- **Engineering Baseline**: Project AURA at M60 is an independently qualified, production-hardened codebase. It provides all architectural guarantees, security gates, and integration test coverage necessary for deployment.
- **External Dependencies**: Full execution of all multi-provider features requires active credentials for relevant external providers (Google Gemini, Groq, OpenRouter, Mistral).
- **Environment Prerequisites**: Live vector search and multi-tenant persistence require a PostgreSQL 16+ instance with the `pgvector` extension enabled.

---

## 📄 License

This project is licensed under the terms described in the repository.
