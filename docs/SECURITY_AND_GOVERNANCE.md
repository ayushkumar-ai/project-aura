# Project AURA — Security, Governance & Trust Model

## 1. Security Philosophy: Autonomy ≠ Authorization

In Project AURA, the core premise of the security model is:
> **An AI model or agent may reason about what should be done, but deterministic policy and authorization boundaries decide if it is allowed.**

Probabilistic intelligence is strictly decoupled from authoritative execution boundaries.

---

## 2. Multi-Layer Security Architecture

```
                       USER REQUEST / INCOMING EVENT
                                     │
                                     ▼
                        [ LAYER 0: Policy Engine ]
                                     │
                       ┌─────────────┴─────────────┐
                       ▼                           ▼
                   DENY (403)                  ALLOW (200)
                                                   │
                                     [ LAYER 1: Approval Gateway ]
                                                   │
                                         Requires Operator Gate?
                                           ┌───────┴───────┐
                                          YES              NO
                                           │               │
                                           ▼               │
                                   [ Human Review ]        │
                                     Approve/Deny          │
                                           │               │
                                           └───────┬───────┘
                                                   │
                                                   ▼
                                     [ LAYER 2: AST Sandbox ]
                                        (For dynamic code)
                                                   │
                                                   ▼
                                     [ LAYER 3: ToolExecutor ]
                                                   │
                                                   ▼
                                              Actual Tool
```

---

## 3. AST Security Policy Visitor & Code Sandboxing

When AURA synthesizes dynamic Python skills (Milestone 26), the code is never executed blindly. It is parsed into an Abstract Syntax Tree (AST) and scanned by `ASTSecurityPolicyVisitor`.

### Forbidden Modules
The AST visitor explicitly blocks imports of dangerous modules, including:
- `os`, `sys`, `subprocess`, `shutil`, `pty`, `commands`
- `socket`, `http`, `urllib`, `requests`, `httpx` (unless wrapped via trusted interfaces)
- `importlib`, `builtins`, `ctypes`, `gc`, `multiprocessing`, `threading`
- `pickle`, `marshal`, `shelve`

### Forbidden Builtin Functions & Operations
- `exec()`, `eval()`, `compile()`, `__import__()`
- `open()`, `input()`, `breakpoint()`
- `globals()`, `locals()`, `vars()`, `dir()`

### Forbidden Dunder Introspection
- `__subclasses__`, `__bases__`, `__mro__`, `__globals__`, `__code__`, `__builtins__`, `__dict__`

### Execution Isolation
Execution takes place in `SandboxedToolExecutor` using a restricted global dictionary with only safe built-ins (e.g., `abs`, `min`, `max`, `len`, `int`, `float`, `str`, `dict`, `list`, `math`, `json`, `re`, `datetime`).

---

## 4. Taint Tracking & Provenance Isolation

Project AURA treats all data originating from external or uncontrolled sources as **tainted**:
- Web crawl outputs & fetched HTML pages
- External API responses
- User-supplied raw files
- Synthesized dynamic skills prior to verification

### Taint Propagation Rules
1. Any artifact created using tainted inputs inherits the `is_tainted=True` state.
2. Tainted data cannot be fed into sensitive administrative or code execution tools without explicit sanitization.
3. Every artifact stores full lineage parent IDs so taint can be back-traced to the originating root.

---

## 5. Graduated Human-in-the-Loop (HITL) Approval

AURA classifies actions into graduated risk tiers:

| Risk Tier | Examples | Execution Path |
|---|---|---|
| **Low Risk** | Pure reasoning, memory search, math calculations | Autonomous execution |
| **Medium Risk** | Dynamic skill synthesis, caching, read-only web crawl | Autonomous execution with tracing & logging |
| **High Risk** | State-modifying operations, sensitive file writes, external API dispatch | HITL Gateway triggers pending approval request |
| **Critical** | Policy adjustments, system configuration overrides | Mandatory cryptographically signed human authorization |

Operator approvals and clarifications are handled via `OperatorBridge` and `ClarificationGateway`, ensuring no task hangs indefinitely while awaiting authorization.
