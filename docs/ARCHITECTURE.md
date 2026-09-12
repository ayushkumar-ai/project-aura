# PROJECT AURA — Architectural Blueprint & System Specification

## 1. System Vision & Purpose

**Project AURA** is an open, privacy-conscious, context-aware, goal-driven personal AI platform designed to transform high-level human intent into persistent, observable, verified, and recoverable execution.

Unlike traditional chat wrappers and single-turn LLM loops that optimize solely for conversational responses, AURA is built from the ground up as a **complete personal intelligence and autonomous execution operating system**.

```
Traditional AI:
User ──► Question ──► LLM ──► Answer

Project AURA:
User
  │ (Intent)
  ▼
Cognitive Plane (Context, Goals, Planning, Reasoning)
  │
  ▼
Execution Plane (Tools, Dynamic Skills, Multi-Agent Teams, Mission Campaigns)
  │
  ▼
Knowledge & Memory Plane (Multi-tier Memory, Epistemic KG, Artifact Lineage)
  │
  ▼
Evaluation & Adaptation Plane (Verification, Benchmarks, Feedback Optimization)
  │
  ▼
Reliability & Governance Plane (Policy, HITL Approval, Tracing, Checkpoints, Self-Healing)
  │
  ▼
Verified Deliverables & Durable Outcomes
```

---

## 2. Core Architectural Principles

1. **Safety Before Autonomy (`Autonomy != Authorization`)**:
   An AI agent may decide *what* needs to be done, but deterministic policy and authorization rules decide *if* it is allowed.
2. **Deterministic Boundaries Around Probabilistic Intelligence**:
   Probabilistic LLM outputs never directly invoke side-effects without passing through strict policy gates, schema validation, and sandboxing.
3. **Causal Observability**:
   Every span, goal, delegation, tool call, artifact, and error is causally linked in a directed execution graph.
4. **Resilient Recoverability**:
   Failures trigger causal diagnosis, multi-tier remediation, saga compensation, or crash-resilient restoration rather than cascading termination.
5. **Data Lineage & Provenance**:
   Every fact, claim, evidence item, and artifact maintains an explicit provenance trail and taint tracking state.
6. **Controlled Epistemic Memory**:
   Memory is categorized by relevance, confidence, temporal decay, and semantic graph relationships rather than unbounded accumulation.
7. **Sandboxed Capability Evolution**:
   Dynamically synthesized tools and skills undergo strict AST validation, isolated sandboxed execution, and trajectory verification before dynamic registration.

---

## 3. The Six Architectural Planes

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

### Plane 1: Cognitive Plane
- **Intent Recognition & Decomposition**: Converts high-level prompts into structured goals, constraints, and success criteria.
- **Context Management**: Scopes user session history, working context, environmental state, and proactive event dispatching.
- **Goal DAG Engine**: Formulates multi-goal directed acyclic graphs with topological dependency resolution and dynamic replanning.
- **Reflection & Calibration**: Periodically analyzes historical execution traces to adjust heuristic weights and strategy rankings.

### Plane 2: Execution Plane
- **Orchestrator & Agentic Runtime**: Coordinates model providers, tool executions, and multi-turn loops.
- **Tool & Skill Registries**: Manages static tools (echo, calculator, research) and dynamically synthesized Python tools.
- **Multi-Agent Mesh**: Coordinates specialized agent roles (Leader, Researcher, Coder, Reviewer, Verifier) across hierarchical, sequential, consensus, and debate topologies.
- **Mission Campaign Engine**: Drives multi-phase, long-horizon missions with gating milestones and cross-phase artifact dataflow.

### Plane 3: Knowledge & Memory Plane
- **Multi-Tier Memory**:
  - *Working Memory*: Task-scoped scratchpads and ephemeral buffers.
  - *Episodic Memory*: Historical interaction sessions and past goal trajectories.
  - *Semantic / Epistemic Graph*: Cross-mission entity-relation graph capturing verified skills, remediation recipes, role alignments, and goal domain patterns.
- **Artifact Lifecycle & Content-Addressable Storage (CAS)**:
  - Immutable blobs stored by SHA-256 hash.
  - Full versioning, MIME typing, taint status tracking, and lineage DAG derivations.
- **Research Engine**: Deconstructs research queries, crawls web sources, extracts structured evidence, evaluates contradictions, and produces synthesized dossiers.

### Plane 4: Evaluation & Adaptation Plane
- **Evaluation Engine**: Evaluates execution outputs across structural, semantic, quality, and constraint dimensions.
- **Trajectory Verifier**: Validates execution step sequences against safety invariants, token budgets, and step limits.
- **Adaptive Policy Optimizer & Feedback Bridge**: Translates evaluation reports into closed-loop heuristic tuning, strategy weights, and model routing adjustments.

### Plane 5: Reliability & Recovery Plane
- **Unified Distributed Tracing**: Emits structured spans with parent-child causal links, metadata, and OpenTelemetry compatibility.
- **Causal Fault Analyzer**: Analyzes execution trace graphs to locate root-cause spans and classify faults into 8 distinct categories.
- **Remediation Planner**: Synthesizes multi-tier recovery plans (retry, strategy mutation, dynamic skill synthesis, replanning, team escalation).
- **Self-Healing Orchestrator**: Executes closed-loop remediation cycles to repair and resume failed campaign phases.
- **Saga Compensation Coordinator**: Maintains reversible rollback logs to compensate side-effects upon unrecoverable mission failure.
- **Crash Checkpoint Manager**: Periodically snapshots atomic runtime state for instant recovery across process restarts.

### Plane 6: Governance & Trust Plane
- **Policy Enforcement Engine**: Enforces deterministic allow/deny rules, capability restrictions, and rate limits.
- **Taint Propagation**: Tracks untrusted data across memory, artifacts, tools, and dynamic skills.
- **Graduated HITL Approval Gateway**: Intercepts high-risk operations and requests cryptographically auditable operator approval.
- **AST Security Sandbox**: Parses dynamic Python ASTs, restricting forbidden modules, builtins, and dunder introspection.

---

## 4. Fundamental Autonomous Execution Lifecycle

```
                    ┌────────────────────────┐
                    │      USER REQUEST      │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │       INTERPRET        │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │    BUILD CONTEXT       │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │   CREATE / UPDATE GOAL │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │          PLAN          │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │    SELECT STRATEGY     │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │        SCHEDULE        │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │        EXECUTE         │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │        OBSERVE         │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │ STORE MEMORY/ARTIFACTS │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │         VERIFY         │
                    └───────────┬────────────┘
                                │
                       ┌────────┴────────┐
                       ▼                 ▼
                   [SUCCESS]          [FAILURE]
                       │                 │
                       ▼                 ▼
                     DONE            DIAGNOSE (Causal Trace)
                                         │
                                         ▼
                                     REMEDIATE (Plan)
                                         │
                                         ▼
                                       REPAIR (Action)
                                         │
                                         ▼
                                       RETRY / RESUME
```

---

## 5. Security & Isolation Architecture

```
                 USER REQUEST / PROACTIVE EVENT
                               │
                               ▼
                        ORCHESTRATOR
                               │
                               ▼
                         POLICY ENGINE
                               │
                   ┌───────────┴───────────┐
                   ▼                       ▼
                 DENY                   ALLOW
                                           │
                                  Approval Required?
                                    ┌──────┴──────┐
                                   YES            NO
                                    │              │
                                    ▼              ▼
                              HITL GATEWAY   ToolExecutor / Sandbox
                                    │              │
                                 Approve           ▼
                                    └────────► Execute Tool
```

- **Zero Privilege Escalation**: Metadata or model suggestions cannot bypass deterministic policy checks.
- **Untrusted Input Tainting**: All external web data, file inputs, and unverified API responses are tagged tainted.
- **Sandboxed Capability Synthesis**: Dynamically synthesized code runs inside a restricted namespace without access to `os`, `subprocess`, `sys`, `socket`, or file-system modifications outside designated sandboxes.
