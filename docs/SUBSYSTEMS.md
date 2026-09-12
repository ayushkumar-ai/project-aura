# Project AURA — Subsystem Technical Reference

This guide provides deep technical documentation for the primary execution subsystems in Project AURA.

---

## 1. Goal Engine & Multi-Goal Scheduler (M10–M12, M17, M22)

```
                       Goal Engine
                           │
                 ┌─────────┴─────────┐
                 ▼                   ▼
           Goal Definition       Goal DAG
                 │                   │
                 ▼                   ▼
           Task Planner       Topological Sorter
                 │                   │
                 └─────────┬─────────┘
                           ▼
                  Multi-Goal Scheduler
                           │
       ┌───────────────────┼───────────────────┐
       ▼                   ▼                   ▼
Resource Budget      Resource Locks      Event Dispatcher
 (Tokens / CPU)       (Mutex/Shared)      (Proactive PubSub)
```

- **`GoalDAG`**: Maintains goal hierarchies, prerequisites, and dynamic replanning triggers. Prevents cyclic dependencies.
- **`MultiGoalScheduler`**: Dispatches goals based on priority, resource budget availability, and lock acquisition.
- **`TeamAwareScheduler`**: Allocates subgoals to specialized multi-agent teams based on role capability matrices.

---

## 2. Multi-Agent Mesh & Consensus Topologies (M21–M22)

AURA organizes agents into specialized roles using `RoleRegistry` and coordinates them over `AgentMessageBus`.

### Supported Topologies
1. **Hierarchical**: Leader coordinates and delegates tasks to sub-agents.
2. **Sequential Pipeline**: Output of Agent A becomes input to Agent B.
3. **Consensus Voting**: Multiple agents independently evaluate options and submit votes with quorum thresholds.
4. **Round-Robin Debate**: Agents iteratively refine proposals through structured dialectic turns.

---

## 3. Mission Campaign Engine & Artifact Dataflow (M25)

```
                            Mission Campaign
                                   │
                ┌──────────────────┼──────────────────┐
                ▼                  ▼                  ▼
             Phase 1            Phase 2            Phase 3
          (Research Goal)    (Analysis Goal)     (Report Goal)
                │                  │                  │
                ▼                  ▼                  ▼
         Evidence Artifact   Analysis Artifact   Final Report
                │                  ▲
                └─ Dataflow Channel┘
```

- **`CampaignDefinition`**: High-level mission containing ordered phases, gating milestones, and dataflow bindings.
- **`ArtifactPipelineRouter`**: Typed channels connecting producer goals to downstream consumer goals. Validates artifacts against declared `ArtifactContract` schemas.
- **`SagaCoordinator`**: Tracks forward actions and executes compensating rollback actions if a critical phase fails unrecoverably.

---

## 4. Autonomous Dynamic Skill Synthesis & Sandboxing (M26)

```
Need Missing Capability
          │
          ▼
   SkillSynthesizer ──► Generates Python Source Code
          │
          ▼
ASTSecurityPolicyVisitor ──► Scans AST for Forbidden Modules/Builtins
          │
          ▼
SandboxedToolExecutor ──► Executes in Isolated Namespace
          │
          ▼
SkillVerificationHarness ──► Evaluates Test Vectors & Safety Invariants
          │
          ▼
DynamicSkillRegistry ──► Registered as Usable Dynamic Tool
```

- **`SynthesizedSkill`**: Encapsulates code, AST hash, capability tags, test vectors, and lifecycle states (`SYNTHESIZED`, `VERIFIED`, `ACTIVE`, `DEPRECATED`, `REJECTED_SECURITY`).
- **`CompositeSkill`**: Declaratively chains multiple dynamic or static tools into a sequential execution pipeline.

---

## 5. Causal Fault Diagnosis & Autonomous Self-Healing (M27)

```
                      Campaign Phase Fails
                                │
                                ▼
                       Tracer / Span Record
                                │
                                ▼
                      CausalExecutionGraph
                                │
                                ▼
                       CausalFaultAnalyzer
                                │
                 ┌──────────────┴──────────────┐
                 ▼                             ▼
         Fault Classification         Root Cause Span ID
      (8 Distinct Fault Categories)            │
                 │                             │
                 └──────────────┬──────────────┘
                                ▼
                       RemediationPlanner
                                │
                 ┌──────────────┴──────────────┐
                 ▼                             ▼
          Tiered Action Plan            Budget Limits
       (Retry, Tool Mutation, etc.)            │
                 │                             │
                 └──────────────┬──────────────┘
                                ▼
                     SelfHealingOrchestrator
                                │
                 ┌──────────────┴──────────────┐
                 ▼                             ▼
             [REPAIRED]                  [UNRECOVERABLE]
                 │                             │
                 ▼                             ▼
          Resume Campaign              Saga Compensation
```

### The 8 Fault Categories
1. `TRANSIENT_INFRASTRUCTURE`: Network timeouts, rate limits (action: exponential backoff retry).
2. `TOOL_INTERFACE_ERROR`: Bad parameters, type errors (action: argument adapter / format mutation).
3. `MISSING_CAPABILITY`: Tool unavailable (action: dynamic skill synthesis).
4. `ARTIFACT_SCHEMA_VIOLATION`: Malformed output (action: schema reparator / regenerating artifact).
5. `POLICY_DENIAL`: Disallowed action (action: alternative policy-compliant strategy).
6. `AGENT_ROLE_MISALIGNMENT`: Assigned role lacks skill (action: delegate to alternative role).
7. `RESOURCE_EXHAUSTION`: Budget exhausted (action: request budget increase or prune context).
8. `GOAL_STAGNATION`: Goal stuck in loop (action: replan goal with altered strategy).

---

## 6. Epistemic Knowledge Graph & Semantic Memory (M28)

```
             Experiences, Verified Skills & Healing Events
                                   │
                                   ▼
                          ExperienceDistiller
                                   │
                                   ▼
                       EpistemicKnowledgeGraph
                                   │
                     Entities ◄────┼────► Relations
               (Skills, Recipes,   │    (SOLVES, REQUIRES,
                Roles, Patterns)   │     PROVEN_FOR, etc.)
                                   ▼
                         EpistemicQueryEngine
                                   │
         ┌─────────────────┬───────┴─────────┬─────────────────┐
         ▼                 ▼                 ▼                 ▼
   Recommend Skill  Recommend Role   Recommend Recipe  Proven Patterns
```

- **`EpistemicKnowledgeGraph`**: Graph database representing structured system knowledge across missions.
- **`ExperienceDistiller`**: Automatically distills insights from successful campaigns, verified dynamic skills, and recovered self-healing events.
- **`EpistemicQueryEngine`**: Provides high-performance semantic retrieval to inform future goal planning, skill selection, and failure recovery.
