# PROJECT AURA — MILESTONE 56 ARCHITECTURE & DESIGN SPECIFICATION
## Advanced Cognitive Memory, Continuous Learning & Personalization
### Production-Grade • Multi-Tier Memory • Contradiction Resolution • Dynamic Personalization • Continuous Learning • Tenant Isolation • Audit-Ready

**Document Version:** 1.0.0-PROD-QUALIFIED  
**Status:** ARCHITECTURALLY QUALIFIED — READY FOR IMPLEMENTATION  
**Target Milestone:** M56  
**Authoritative Baselines:**
- M50: `fb8a3cf71ab7a5f044641afd70f1a46f25f29a8c`
- M51: `fdddbe49e28368e658fd8210ae19ce3c7de0859f` (FORENSIC AUDIT PASS)
- M52 Final Frozen Baseline: `9da9b09312d49cb4fa56c0110109d07f3f56697d` (FORENSIC RE-AUDIT PASS)
- M53 Final Frozen Baseline: `aa3dd8b55711d890c1988cde834ac7aa33adbbf4` (FORENSIC AUDIT PASS)
- M54 Final Frozen Baseline: `436c0a85613c73e42c18ccc9837a23cc4f967743` (FORENSIC AUDIT PASS)
- M55 Final Frozen Baseline: `d10c32a9e4f6e0b9fc14c7829c397ce9d61d4458` (FORENSIC RE-AUDIT PASS)  
**Branch:** `antigravity-work`

---

## 1. Executive Summary & Architectural Demarcation

Milestone 56 (M56) delivers the **Advanced Cognitive Memory, Continuous Learning & Personalization Layer** for Project AURA. It transforms AURA from an execution-focused runtime into an adaptive, self-evolving cognitive platform.

```
+===================================================================================================+
|                              AURA COGNITIVE MEMORY & LEARNING LAYER                               |
|                                                                                                   |
|   +-------------------------------------------------------------------------------------------+   |
|   | POSTGRESQL 16 AUTHORITATIVE COGNITIVE PERSISTENCE STORE                                   |   |
|   |                                                                                           |   |
|   |  [cognitive_memories]         [memory_contradictions]      [user_cognitive_profiles]      |   |
|   |   - memory_id (UUID/PK)        - contradiction_id (UUID)    - profile_id / tenant_id       |   |
|   |   - tenant_id (user_id)        - tenant_id                  - preferences (JSONB)         |   |
|   |   - memory_type (5 categories) - memory_a_id / memory_b_id  - inferred_traits (JSONB)     |   |
|   |   - content / structured_data  - contradiction_type         - interaction_metrics (JSONB) |   |
|   |   - confidence [0.0, 1.0]      - resolution_status          - version / updated_at        |   |
|   |   - provenance_type (5 types)  - resolution_strategy                                      |   |
|   |   - lifecycle_state (5 states) - resolved_by / resolved_at   [memory_feedback_events]     |   |
|   |   - version / supersedes_id                                 - event_id / target_memory_id |   |
|   |   - taint_status / source_urls [experience_patterns]        - feedback_type / correction  |   |
|   |   - embedding (Vector/JSONB)   - pattern_id / context_key   - applied / created_at        |   |
|   |   - access_count / timestamps  - success/failure counts                                   |   |
|   |                                - optimal_tools / latency                                  |   |
|   +-------------------------------------------------------------------------------------------+   |
|                                          ▲                               ▲                        |
|                     State Transitions    │                               │ Vector Synchronization |
|                     & Decay Updates      │                               │ (Exclude Stale/Dead)   |
|                                          │                               │                        |
|   +──────────────────────────────────────┴─────────+   +─────────────────┴────────────────────+   |
|   | COGNITIVE DOMAIN ENGINES                       |   | CONTINUOUS LEARNING & PERSONALIZATION|   |
|   |                                                |   |                                      |   |
|   |  [MemoryLifecycleManager]                      |   |  [FeedbackLearningLoop]              |   |
|   |   ├── Confidence Decay (half-life per category)|   |   ├── Explicit User Feedback (1.0)   |   |
|   |   ├── State Transitions (Active->Stale->Purge) |   |   ├── Reinforcement Weight Adjuster  |   |
|   |   └── Vector Index Invalidation Filter         |   |   └── Failure-Mode Pattern Update    |   |
|   |                                                |   |                                      |   |
|   |  [ContradictionEngine]                         |   |  [PersonalizationEngine]             |   |
|   |   ├── Provenance Hierarchy Arbitrator          |   |   ├── Relevance & Recency Scorer     |   |
|   |   ├── User Explicit Override Authority         |   |   ├── Dynamic Context Injector       |   |
|   |   └── Escalation & Auto-Resolution             |   |   └── Tool Selection Recommender     |   |
|   |                                                |   |                                      |   |
|   |  [MemoryConsolidationEngine]                   |   |                                      |   |
|   |   ├── Episodic Trace Distillation              |   |                                      |   |
|   |   └── Experience Pattern Extraction            |   |                                      |   |
|   +────────────────────────────────────────────────+   +──────────────────────────────────────+   |
|                                          ▲                                                        |
|                                          │ Context Injection & Experience Replay                  |
|   +──────────────────────────────────────┴────────────────────────────────────────────────────+   |
|   | RUNTIME & ORCHESTRATION INTEGRATION (M51 Model Gateway • M52 Tasks • M55 Workers)         |   |
|   |   ├── Agent Prompt Construction with Personalized Context Block                           |   |
|   |   ├── Post-Execution Episodic Trace & Feedback Ingestion                                  |   |
|   |   └── Multi-Tenant RBAC Security Gateway (memory:read, memory:write, profile:manage)       |   |
|   +───────────────────────────────────────────────────────────────────────────────────────────+   |
+===================================================================================================+
```

### 1.1 Scope & Boundaries
- **In Scope:**
  - 5 Memory Categories: `episodic`, `semantic`, `preference`, `experience`, `user_profile`.
  - 5 Provenance Types: `user_explicit`, `tool_observed`, `system_derived`, `model_inferred`, `external_imported` with strict precedence rules.
  - 5 Lifecycle States: `ACTIVE`, `STALE`, `SUPERSEDED`, `ARCHIVED`, `DELETED` with decay, compaction, and vector sync.
  - Automated contradiction detection and multi-strategy resolution engine.
  - Continuous learning feedback loop and experience pattern distillation without LLM weight fine-tuning.
  - Contextual personalization engine with dynamic prompt injection.
  - Dedicated schema migration `007_cognitive_memory_and_continuous_learning.sql`.
  - In-Memory and PostgreSQL repository implementations wired into `RepositoryContainer`.
  - RESTful APIs in `app/server.py` and contracts in `core/api_contracts.py`.
- **Strict Non-Goals:**
  - Model weights backpropagation or LLM fine-tuning — continuous learning is achieved strictly via structured memory, ranking, and prompt personalization.
  - Cross-tenant memory sharing or collaborative filtering across tenants.
  - Modification of frozen M50–M55 code, database migrations `001`–`006`, or existing tests.

---

## 2. Memory Hierarchy & Domain Models

### 2.1 Memory Categories
1. **Episodic (`episodic`)**: Captures historical traces of discrete task runs, plans, executed steps, outcomes, and error recoveries with timestamps and metadata.
2. **Semantic (`semantic`)**: Durable facts, entity relationships, domain rules, and structured system assertions.
3. **Preference (`preference`)**: User preferences, style guidelines, verbosity choices, and operational constraints (explicit vs inferred).
4. **Experience (`experience`)**: Tool success/failure rates, parameter recommendations, latency patterns, and failure avoidance patterns.
5. **User Profile (`user_profile`)**: Structured, evolving cognitive portrait of the user containing verified preferences, inferred traits, and interaction metrics.

### 2.2 Provenance Hierarchy & Authority
Provenance strictly dictates confidence initialization and conflict resolution authority:
1. `user_explicit` (Initial Confidence = 1.0): Direct, explicit instruction or confirmation from the user. Holds absolute override authority.
2. `tool_observed` (Initial Confidence = 0.90): Deterministic tool output or direct environment observation.
3. `system_derived` (Initial Confidence = 0.85): Deterministically computed facts, aggregations, or system rules.
4. `model_inferred` (Initial Confidence = 0.70): Probabilistic inferences derived by an LLM or heuristic pattern matcher.
5. `external_imported` (Initial Confidence = 0.60): Untrusted or external documents/web sources (taint tracked).

---

## 3. Lifecycle State Machine & Contradiction Resolution

### 3.1 Lifecycle State Machine

```
              ┌──────────────────┐
              │      ACTIVE      │ ──► Memory freshly created or validated; indexed for retrieval
              └────────┬─────────┘
                       │
          ┌────────────┼────────────┐
          │ (Decay/    │ (Direct    │ (Contradiction /
          │  Inactive) │  Update)   │  Supersession)
          ▼            │            ▼
   ┌─────────────┐     │     ┌──────────────┐
   │    STALE    │     │     │  SUPERSEDED  │ ──► Inactive, excluded from standard RAG recall
   └──────┬──────┘     │     └──────────────┘
          │ (Purge/    │
          │  Archive)  │
          ▼            ▼
   ┌─────────────┐   ┌─────────────┐
   │  ARCHIVED   │   │   DELETED   │ ──► Soft or hard deleted; purged from vector indexes
   └─────────────┘   └─────────────┘
```

### 3.2 Contradiction Resolution Policies
When two active memories $M_A$ and $M_B$ within the same tenant and namespace assert conflicting facts:
1. **Provenance Precedence**: If $\text{Authority}(M_A) > \text{Authority}(M_B)$, $M_A$ is retained as `ACTIVE` and $M_B$ transitions to `SUPERSEDED`.
2. **User Explicit Authority**: Any `user_explicit` memory unconditionally supersedes `model_inferred` or `external_imported` memories.
3. **Recency Arbitration**: If provenance is identical, the newer memory with higher or equal confidence supersedes the older memory.
4. **Manual Escalation**: If confidence/provenance is ambiguous and conflict severity is high, a contradiction record is created with `resolution_status = 'manual_pending'`.

---

## 4. Continuous Learning & Personalization Engine

### 4.1 Feedback Learning Loop
- Captures explicit user signals: `positive` (thumbs up), `negative` (thumbs down), `correction` (user provides corrected fact), `override` (user forces new preference).
- Adjusts memory confidence: $\text{conf}_{\text{new}} = \min(1.0, \text{conf}_{\text{curr}} + 0.1)$ on positive; $\text{conf}_{\text{new}} = \max(0.1, \text{conf}_{\text{curr}} - 0.2)$ on negative.
- Automatically generates superseded versions upon receiving explicit corrections.
- Updates `experience_patterns` with tool success/failure counts and latency adjustments.

### 4.2 Contextual Personalization & Dynamic Injection
- Evaluates incoming task / conversation context against active memories.
- Computes composite relevance score:
  $$\text{Score}(M) = w_r \cdot \text{Similarity}(M, Q) + w_c \cdot \text{Confidence}(M) + w_t \cdot \text{Recency}(M)$$
- Assembles top-ranked preferences, verified facts, and tool patterns into a structured, sandboxed `PersonalizationContext` block injected into model prompts.

---

## 5. Database Schema Specification (Migration 007)

```sql
-- Migration 007: Cognitive Memory, Continuous Learning & Personalization

-- 1. Cognitive Memories Table
CREATE TABLE IF NOT EXISTS cognitive_memories (
    memory_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    memory_type VARCHAR(32) NOT NULL,
    category VARCHAR(64) NOT NULL DEFAULT 'general',
    key VARCHAR(256) NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    structured_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    provenance_type VARCHAR(32) NOT NULL DEFAULT 'system_derived',
    lifecycle_state VARCHAR(32) NOT NULL DEFAULT 'active',
    version INTEGER NOT NULL DEFAULT 1,
    supersedes_id VARCHAR(128) NULL,
    taint_status BOOLEAN NOT NULL DEFAULT FALSE,
    source_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    embedding JSONB NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NULL,
    CONSTRAINT chk_memory_type CHECK (memory_type IN ('episodic', 'semantic', 'preference', 'experience', 'user_profile')),
    CONSTRAINT chk_provenance_type CHECK (provenance_type IN ('user_explicit', 'tool_observed', 'system_derived', 'model_inferred', 'external_imported')),
    CONSTRAINT chk_lifecycle_state CHECK (lifecycle_state IN ('active', 'stale', 'superseded', 'archived', 'deleted')),
    CONSTRAINT chk_confidence_bounds CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

CREATE INDEX IF NOT EXISTS idx_cog_memories_tenant_type ON cognitive_memories(tenant_id, memory_type, lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_cog_memories_tenant_key ON cognitive_memories(tenant_id, key);
CREATE INDEX IF NOT EXISTS idx_cog_memories_lifecycle_expiry ON cognitive_memories(lifecycle_state, expires_at);

-- 2. Memory Contradictions Table
CREATE TABLE IF NOT EXISTS memory_contradictions (
    contradiction_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    memory_a_id VARCHAR(128) NOT NULL REFERENCES cognitive_memories(memory_id) ON DELETE CASCADE,
    memory_b_id VARCHAR(128) NOT NULL REFERENCES cognitive_memories(memory_id) ON DELETE CASCADE,
    contradiction_type VARCHAR(64) NOT NULL DEFAULT 'fact_conflict',
    resolution_status VARCHAR(32) NOT NULL DEFAULT 'detected',
    resolution_strategy VARCHAR(32) NOT NULL DEFAULT 'provenance_precedence',
    resolved_by VARCHAR(128) NULL,
    resolution_details JSONB NOT NULL DEFAULT '{}'::jsonb,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMPTZ NULL,
    CONSTRAINT chk_contradiction_status CHECK (resolution_status IN ('detected', 'auto_resolved', 'manual_pending', 'resolved')),
    CONSTRAINT chk_contradiction_strategy CHECK (resolution_strategy IN ('provenance_precedence', 'user_override', 'recency', 'confidence_threshold', 'manual'))
);

CREATE INDEX IF NOT EXISTS idx_contradictions_tenant_status ON memory_contradictions(tenant_id, resolution_status);

-- 3. User Cognitive Profiles Table
CREATE TABLE IF NOT EXISTS user_cognitive_profiles (
    profile_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
    inferred_traits JSONB NOT NULL DEFAULT '{}'::jsonb,
    interaction_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_user_cognitive_profiles_tenant ON user_cognitive_profiles(tenant_id);

-- 4. Experience Patterns Table
CREATE TABLE IF NOT EXISTS experience_patterns (
    pattern_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    context_key VARCHAR(256) NOT NULL,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    average_latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    optimal_tools JSONB NOT NULL DEFAULT '[]'::jsonb,
    failure_modes JSONB NOT NULL DEFAULT '[]'::jsonb,
    recommendations JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_tenant_context_pattern UNIQUE (tenant_id, context_key),
    CONSTRAINT chk_pattern_counts CHECK (success_count >= 0 AND failure_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_experience_patterns_tenant_context ON experience_patterns(tenant_id, context_key);

-- 5. Memory Feedback Events Table
CREATE TABLE IF NOT EXISTS memory_feedback_events (
    event_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_memory_id VARCHAR(128) NULL REFERENCES cognitive_memories(memory_id) ON DELETE CASCADE,
    feedback_type VARCHAR(32) NOT NULL,
    correction_content TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    applied BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_feedback_type CHECK (feedback_type IN ('positive', 'negative', 'correction', 'override'))
);

CREATE INDEX IF NOT EXISTS idx_feedback_events_tenant_target ON memory_feedback_events(tenant_id, target_memory_id);
```

---

## 6. Formal Architectural Invariants (M56-F01 through M56-F35)

| Invariant ID | Title | Mathematical / Operational Specification |
|---|---|---|
| **M56-F01** | **Strict Multi-Tenant Isolation** | Every cognitive memory, contradiction, profile, and pattern MUST be keyed by `tenant_id`. Queries MUST include `tenant_id` in `WHERE` clauses. Zero cross-tenant data leakage. |
| **M56-F02** | **Category Partition Invariance** | Every cognitive memory MUST belong to exactly one valid `CognitiveMemoryType` (`episodic`, `semantic`, `preference`, `experience`, `user_profile`). |
| **M56-F03** | **Confidence Range Bounding** | Confidence $C$ is strictly bounded: $0.0 \le C \le 1.0$. Any input exceeding bounds MUST be clamped or rejected. |
| **M56-F04** | **Provenance Authority Ordering** | Provenance authority is strictly ordered: $\text{user\_explicit} > \text{tool\_observed} > \text{system\_derived} > \text{model\_inferred} > \text{external\_imported}$. |
| **M56-F05** | **User Override Infallibility** | A `user_explicit` memory MUST unconditionally supersede any conflicting `model_inferred` or `external_imported` memory. |
| **M56-F06** | **Atomic Supersession** | Superseding memory $M_A$ with $M_B$ MUST atomically set $M_A.\text{lifecycle\_state} = \text{SUPERSEDED}$, $M_B.\text{supersedes\_id} = M_A.\text{id}$, and $M_B.\text{version} = M_A.\text{version} + 1$. |
| **M56-F07** | **Vector Retrieval Exclusion** | Vector similarity searches for active context MUST filter `lifecycle_state = 'active'`. Superseded, archived, and deleted entries MUST NOT be returned in active RAG context. |
| **M56-F08** | **Taint Envelope Propagation** | Memories derived from untrusted external sources MUST set `taint_status = True` and retain source URLs. Untrusted memories MUST NOT bypass security gates. |
| **M56-F09** | **Deterministic Temporal Decay** | Unaccessed memories decay according to exponential half-life: $C(t) = C_0 \cdot 2^{-\Delta t / t_{1/2}}$. Explicit user memories decay at $0.0 \times$ rate (no decay). |
| **M56-F10** | **Stale State Transition Threshold** | If confidence decays below `stale_threshold` (default 0.3), memory lifecycle state transitions from `ACTIVE` to `STALE`. |
| **M56-F11** | **Contradiction Detection Completeness** | When saving a memory with existing active key $K$ in tenant $T$, contradiction detector MUST evaluate semantic compatibility before committing state. |
| **M56-F12** | **Contradiction Ledger Auditability** | Every detected contradiction MUST be recorded in `memory_contradictions` with timestamps, strategy, and participants. |
| **M56-F13** | **Feedback Loop Idempotency** | Applying a feedback event with ID $E$ multiple times MUST produce identical state ($\text{applied} = \text{True}$) without repeated confidence drift. |
| **M56-F14** | **Experience Pattern Distillation** | Episodic task outcomes MUST be consolidated into `experience_patterns` updating $\text{success\_count}$, $\text{failure\_count}$, and optimal tool rankings. |
| **M56-F15** | **Profile Schema Versioning** | Every update to `user_cognitive_profiles` MUST increment `version` by 1 and record `updated_at`. |
| **M56-F16** | **Hard Deletion / GDPR Purge** | Invoking `purge_tenant_memories(tenant_id)` MUST completely delete all memories, contradictions, profiles, patterns, and feedback events for that tenant. |
| **M56-F17** | **PII & Secret Scrubbing** | Memory ingest pipelines MUST scrub API keys, bearer tokens, private keys, and passwords before database write. |
| **M56-F18** | **Personalization Context Bounds** | Context injector MUST enforce maximum token / character limits on injected memory blocks to prevent prompt overflow. |
| **M56-F19** | **Zero Model Retraining Requirement** | Continuous learning MUST NOT modify LLM weights; all behavioral adaptation is mediated via cognitive memory, patterns, and prompt context. |
| **M56-F20** | **Fail-Closed Repository Semantics** | In production mode (`aura_env = 'production'`), database unavailability MUST raise fail-closed exceptions with zero unsafe in-memory fallback. |
| **M56-F21** | **In-Memory Store Parity** | `InMemoryCognitiveMemoryRepository` MUST implement identical semantics, lifecycle states, contradiction resolution, and tenant isolation as PostgreSQL. |
| **M56-F22** | **Deterministic Relevance Ranking** | Composite ranking formula MUST produce deterministic ordering for identical queries, timestamps, and confidence scores. |
| **M56-F23** | **Access Metric Tracking** | Successful memory retrieval in active context MUST atomically increment `access_count` and update `last_accessed_at`. |
| **M56-F24** | **RBAC Read Permission Gate** | Endpoint `/v1/cognitive-memory/query` MUST require authenticated identity with `memory:read` or `admin` role. |
| **M56-F25** | **RBAC Write Permission Gate** | Endpoint `/v1/cognitive-memory/record` MUST require authenticated identity with `memory:write` or `admin` role. |
| **M56-F26** | **RBAC Profile Management Gate** | Profile endpoints MUST require `profile:manage` or `admin` role. |
| **M56-F27** | **Zero Frozen Code Modification** | M50–M55 production code and migrations 001–006 MUST NOT be modified or weakened. |
| **M56-F28** | **Backward Compatibility** | Existing `MemoryManager` and `UnifiedMemoryRecord` interfaces MUST remain operational without regression. |
| **M56-F29** | **Low-Cardinality Metrics** | Memory metrics emitted to telemetry MUST use bounded label values (`category`, `lifecycle_state`, `provenance_type`), never unbounded IDs. |
| **M56-F30** | **Non-Negative Pattern Metrics** | `experience_patterns` counters (`success_count`, `failure_count`) MUST satisfy $C \ge 0$, enforced by DB constraint. |
| **M56-F31** | **Unique Profile Constraint** | Exactly one profile row exists per tenant in `user_cognitive_profiles`, enforced by database UNIQUE constraint on `tenant_id`. |
| **M56-F32** | **Unique Context Pattern Constraint** | Exactly one pattern row exists per `(tenant_id, context_key)` in `experience_patterns`. |
| **M56-F33** | **Cascade Deletion on Tenant Removal** | Deleting a user in `users` MUST cascade delete all cognitive memories, contradictions, profiles, and patterns. |
| **M56-F34** | **Safe Deserialization** | Deserialization of memory entries MUST prohibit executable callables and unsafe Python bytecode. |
| **M56-F35** | **Zero-Downtime Schema Migration** | Schema migration `007` MUST be strictly additive, using `IF NOT EXISTS` and backwards-compatible column defaults. |

---

## 7. Adversarial Test Matrix

The M56 test suite will validate:
1. `test_m56_cognitive_memory_unit.py`: Domain models, types, serialization, taint envelopes, confidence bounding, and PII scrubbing.
2. `test_m56_lifecycle_and_contradiction_unit.py`: State machine transitions, decay calculation, contradiction detection, and provenance-based resolution hierarchy.
3. `test_m56_consolidation_and_learning_unit.py`: Episodic trace distillation, feedback loops, experience pattern aggregation, and profile updates.
4. `test_m56_personalization_unit.py`: Dynamic context injector, relevance scoring, tool selection guidance, and prompt block generation.
5. `test_m56_postgres_memory_integration.py`: Live PostgreSQL migration 007 execution, CRUD operations, multi-tenant isolation, contradiction persistence, and cascade deletes.
6. `test_m56_memory_api_integration.py`: FastAPI server endpoints, RBAC permission enforcement, payload validation, and error handling.

---

**AUTHORITATIVE ARCHITECTURAL SPECIFICATION APPROVED FOR PROJECT AURA MILESTONE 56.**
