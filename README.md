# Project AURA — Autonomous Personal Intelligence Platform

[![Build Status](https://img.shields.io/badge/tests-1189%20passed-brightgreen.svg)](docs/MILESTONES.md)
[![Milestone](https://img.shields.io/badge/milestones-M1%20to%20M28-blue.svg)](docs/MILESTONES.md)
[![Security](https://img.shields.io/badge/security-AST%20Sandboxed%20%7C%20Deterministic%20Policy-orange.svg)](docs/SECURITY_AND_GOVERNANCE.md)

**Project AURA** is an open, privacy-conscious, context-aware, goal-driven personal AI platform designed to transform high-level human objectives into persistent, observable, verified, and recoverable execution.

AURA goes beyond simple chatbot wrappers and single-turn prompt loops: it functions as a **complete personal intelligence and autonomous execution operating system**.

---

## 🏛️ Six-Plane Layered Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                          USER / CLIENT API                             │
├────────────────────────────────────────────────────────────────────────┤
│                          AURA FACADE (app/aura.py)                     │
├────────────────────────────────────────────────────────────────────────┤
│                         1. COGNITIVE PLANE                             │
│      Intent • Context • Reasoning • Task Planning • Reflection         │
├────────────────────────────────────────────────────────────────────────┤
│                         2. EXECUTION PLANE                             │
│   Goals • Dynamic Skills • Multi-Agent Mesh • Mission Campaigns        │
├────────────────────────────────────────────────────────────────────────┤
│                    3. KNOWLEDGE & MEMORY PLANE                         │
│  Working/Epistemic Memory • Research • Artifacts • Dataflow Channels   │
├────────────────────────────────────────────────────────────────────────┤
│                   4. EVALUATION & ADAPTATION PLANE                     │
│ Trajectory Verification • Benchmarks • Closed-Loop Adaptive Optimizer  │
├────────────────────────────────────────────────────────────────────────┤
│                   5. RELIABILITY & RECOVERY PLANE                      │
│ Causal Fault Diagnosis • Multi-Tier Self-Healing • Saga Rollbacks      │
├────────────────────────────────────────────────────────────────────────┤
│                   6. GOVERNANCE & TRUST PLANE                          │
│ Policy Enforcement • Taint Tracking • HITL Approval • AST Sandboxes    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## ✨ Key Capabilities

- **Hierarchical Goal DAGs & Multi-Goal Scheduling**: Dynamic task planning, topological dependency resolution, token/CPU resource budgeting, and mutual exclusion locking.
- **Multi-Agent Team Collaboration**: Specialized agent roles coordinated across Hierarchical, Sequential, Consensus, and Debate topologies.
- **Multi-Phase Mission Campaigns & Sagas**: Multi-phase long-horizon campaigns with gating milestones, cross-phase artifact dataflow, and reversible saga rollbacks.
- **Autonomous Dynamic Skill Synthesis**: Self-generating Python tools validated through AST security analysis, isolated sandboxing, and trajectory verification.
- **Causal Fault Diagnosis & Self-Healing**: Root-cause analysis over causal trace graphs, 8-class fault taxonomy, and closed-loop multi-tier self-healing.
- **Epistemic Knowledge Graph Memory**: Continuous experience distillation across missions, semantic queries, and proven pattern recommendations.
- **Unified Causal Tracing & Content-Addressable Artifacts**: OpenTelemetry-compatible causal spans and immutable SHA-256 CAS storage with full lineage tracking.
- **Deterministic Governance & Safety**: `Autonomy ≠ Authorization`—hard deterministic policy boundaries, taint propagation, and graduated Human-in-the-Loop approval gateways.

---

## 📚 Documentation

Detailed documentation is available in the [`docs/`](docs/) directory:
- [**Architecture Blueprint**](docs/ARCHITECTURE.md) — Comprehensive 6-plane architecture specification.
- [**Milestones Roadmap**](docs/MILESTONES.md) — Chronological progression from M1 through M28 and beyond.
- [**Security & Governance**](docs/SECURITY_AND_GOVERNANCE.md) — Deterministic policy, AST sandboxing, taint isolation, and HITL approval.
- [**Subsystem Reference**](docs/SUBSYSTEMS.md) — Detailed technical deep dive into all core execution engines.
- [**Developer Guide**](docs/DEVELOPER_GUIDE.md) — Quickstart, testing, API facade usage, and operational manual.

---

## 🚀 Quickstart

### 1. Installation
```bash
# Clone the repository
cd D:/project-aura

# Activate virtual environment
.\.venv\Scripts\Activate.ps1  # Windows
source .venv/bin/activate     # Linux / macOS

# Install dependencies
pip install -r requirements.txt
```

### 2. Run the Full Test Suite
```bash
python -m pytest tests/
```

### 3. Run Interactive CLI
```bash
python -m app.main --interactive
```

### 4. Python API Example
```python
from app.main import create_aura

# Create full AURA instance with agentic runtime
aura = create_aura(agentic=True)

# Run a task
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

## 🔒 Security Principles

1. **Safety Before Autonomy**: Autonomous decisions never bypass deterministic security policies.
2. **Deterministic Boundaries**: Probabilistic models cannot directly execute code without passing AST inspection and sandboxing.
3. **Traceability**: All actions, state mutations, and data derivations are causally traced.
4. **Resilience**: Failures are diagnosed, healed, compensated, or escalated gracefully.
