# PROJECT AURA — P2 SURGICAL ENHANCEMENT
## M51 Multi-Provider Model Gateway Expansion Architecture & Specification

---

### 1. Executive Summary

This specification documents the **P2 Surgical Enhancement** expanding the existing **M51 ModelGateway** into an enterprise multi-provider inference router. Project AURA now supports **Groq**, **OpenRouter**, **Mistral**, **Google Gemini**, **OpenAI**, **Generic OpenAI-Compatible**, and **Fake** model endpoints within the single authoritative M51 architecture.

All M1–M59 security boundaries, fail-closed semantics, rate-limiting classifiers, circuit breakers, and telemetry contracts remain strictly intact and backward compatible.

---

### 2. Architecture & Design Principles

```
                              ┌────────────────────────────────────────┐
                              │           AURA Applications            │
                              │ (Orchestrator / AgentMesh / Workflows) │
                              └───────────────────┬────────────────────┘
                                                  │
                                                  │ ModelInterface.generate(prompt, request_id, ...)
                                                  ▼
                              ┌────────────────────────────────────────┐
                              │            M51 ModelGateway            │
                              │ ┌────────────────────────────────────┐ │
                              │ │         ProviderCatalog            │ │
                              │ │ (Strategy: Priority, Cost, Latency)│ │
                              │ └────────────────────────────────────┘ │
                              │ ┌────────────────────────────────────┐ │
                              │ │         FailureClassifier          │ │
                              │ │ (Transient vs Fatal vs Hard Quota) │ │
                              │ └────────────────────────────────────┘ │
                              └─┬──────────────┬──────────────┬────────┘
                                │              │              │
                   ┌────────────┴──┐    ┌──────┴──────┐    ┌──┴─────────────┐
                   │  Groq (Paid)  │    │ OpenRouter  │    │ Mistral /      │
                   │ openai/gpt-oss│    │ (Free/Paid) │    │ Gemini / OpenAI│
                   │    -120b      │    │ openai/gpt- │    │ Generic / Fake │
                   │  (CB #1)      │    │oss-120b:free│    │  (CB #3..N)    │
                   │               │    │  (CB #2)    │    │                │
                   └───────────────┘    └─────────────┘    └────────────────┘
```

#### Key Architecture Tenets:
1. **Single Router Invariance**: The M51 `ModelGateway` is the only model router in Project AURA. No secondary or bypassing router exists.
2. **Deterministic Candidate Selection**: The `ProviderCatalog` filters candidates by required capabilities (`tool_calling`, `structured_output`, `reasoning`, `multimodal`) and sorts them deterministically by routing strategy (`priority`, `cost`, or `latency`).
3. **Provider Circuit Breaker Isolation**: Each provider registration maintains an independent, isolated `CircuitBreaker`. A cascade or outage in one provider (e.g. Groq 500s) does not degrade or trip circuit breakers for other providers.
4. **Non-Guaranteed Free Availability Fallback**: Providers marked with `PricingMode.FREE` (such as `openai/gpt-oss-120b:free` on OpenRouter) are automatically protected with fallback to secondary or paid providers upon encountering transient 429 / 503 errors.
5. **Fail-Closed Security Boundaries**: Security errors (401/403 auth rejections, policy denials, prompt injections, SSRF violations, schema errors, capability mismatches) immediately abort with 0 fallback attempts.

---

### 3. Multi-Provider Capability & Pricing Matrix

| Provider | Default Model | Context Window | Default Pricing Mode | Capabilities | Custom Headers |
|---|---|---|---|---|---|
| **Groq** | `openai/gpt-oss-120b` | 131,072 tokens | `PAID` ($0.15 in / $0.60 out / 1M tokens) | `general_chat`, `reasoning`, `tool_calling`, `structured_output` | None |
| **OpenRouter** | `openai/gpt-oss-120b:free` | 131,072 tokens | `FREE` ($0.00 / $0.00 / 1M tokens) | `general_chat`, `reasoning`, `tool_calling`, `structured_output` | `HTTP-Referer`, `X-Title` |
| **Mistral** | `mistral-small-latest` | 32,768 tokens | `PAID` ($0.20 in / $0.60 out / 1M tokens) | `general_chat`, `reasoning`, `tool_calling`, `structured_output` | None |
| **Gemini** | `gemini-2.5-flash` | 1,048,576 tokens | `PAID` ($0.075 in / $0.30 out / 1M tokens) | `general_chat`, `reasoning`, `tool_calling`, `structured_output`, `multimodal` | None |
| **OpenAI** | `gpt-4o` | 128,000 tokens | `PAID` ($2.50 in / $10.00 out / 1M tokens) | `general_chat`, `reasoning`, `tool_calling`, `structured_output`, `multimodal` | None |
| **Generic** | `generic-model` | 32,768 tokens | `UNKNOWN` | `general_chat` | Configurable |
| **Fake** | `fake-model-v1` | 32,768 tokens | `FREE` ($0.00 / $0.00) | `general_chat`, `reasoning`, `tool_calling`, `structured_output`, `multimodal` | None |

---

### 4. Configuration Reference

```env
# Multi-Provider Model Selection & Routing
AURA_MODEL_PROVIDER=groq
AURA_MODEL_NAME=openai/gpt-oss-120b
AURA_MODEL_FALLBACK_PROVIDERS=openrouter,mistral,gemini,openai,fake
AURA_MODEL_FALLBACK_ENABLED=true
AURA_MODEL_ROUTING_STRATEGY=priority
AURA_MODEL_MAX_FALLBACK_PROVIDERS=3
AURA_MODEL_MAX_RETRIES_PER_PROVIDER=1

# Groq Configuration
AURA_GROQ_API_KEY=gsk_...
AURA_GROQ_MODEL_NAME=openai/gpt-oss-120b
AURA_GROQ_ENDPOINT_URL=https://api.groq.com/openai/v1

# OpenRouter Configuration
AURA_OPENROUTER_API_KEY=sk-or-v1-...
AURA_OPENROUTER_MODEL_NAME=openai/gpt-oss-120b:free
AURA_OPENROUTER_ENDPOINT_URL=https://openrouter.ai/api/v1
AURA_OPENROUTER_HTTP_REFERER=https://github.com/project-aura
AURA_OPENROUTER_TITLE=Project AURA

# Mistral Configuration
AURA_MISTRAL_API_KEY=...
AURA_MISTRAL_MODEL_NAME=mistral-small-latest
AURA_MISTRAL_ENDPOINT_URL=https://api.mistral.ai/v1

# Gemini Configuration
AURA_GEMINI_API_KEY=AIzaSy...
AURA_GEMINI_MODEL_NAME=gemini-2.5-flash
AURA_GEMINI_ENDPOINT_URL=https://generativelanguage.googleapis.com/v1beta/openai/

# OpenAI Configuration
AURA_OPENAI_API_KEY=sk-proj-...
AURA_OPENAI_MODEL_NAME=gpt-4o
AURA_OPENAI_ENDPOINT_URL=https://api.openai.com/v1
```

---

### 5. Provenance & Telemetry Metadata Schema

Every generation response from `ModelGateway.generate()` includes enriched provenance metadata scrubbed of credentials:

```json
{
  "gateway_provider": "groq",
  "gateway_model": "openai/gpt-oss-120b",
  "gateway_attempt_count": 1,
  "gateway_fallback_occurred": false,
  "gateway_attempted_chain": ["groq"],
  "gateway_total_latency_seconds": 0.4281,
  "gateway_pricing_mode": "paid",
  "gateway_cost_usd": 0.00015,
  "gateway_context_window": 131072,
  "gateway_capabilities": ["general_chat", "reasoning", "structured_output", "tool_calling"],
  "quota_state": "unknown"
}
```

---

### 6. Formal Invariant Verification Matrix (P2-M51-F01 to P2-M51-F30)

| Invariant ID | Description | Test Name | Status |
|---|---|---|---|
| `P2-M51-F01` | Groq Default Model is `openai/gpt-oss-120b` (131k ctx, reasoning, tool use) | `test_f01_groq_default_model_selection` | **PASSED** |
| `P2-M51-F02` | OpenRouter Default Model is `openai/gpt-oss-120b:free` | `test_f02_openrouter_default_model_selection` | **PASSED** |
| `P2-M51-F03` | OpenRouter Custom Headers Injection (`HTTP-Referer`, `X-Title`) | `test_f03_openrouter_custom_headers_injection` | **PASSED** |
| `P2-M51-F04` | Circuit Breaker Isolation Across Providers | `test_f04_circuit_breaker_isolation_across_providers` | **PASSED** |
| `P2-M51-F05` | Transient Fallback Cascade (Free $\to$ Paid) | `test_f05_transient_fallback_cascade_free_to_paid` | **PASSED** |
| `P2-M51-F06` | Strict Fail-Closed on 401/403 Authentication Failure | `test_f06_strict_fail_closed_on_auth_failure` | **PASSED** |
| `P2-M51-F07` | Strict Fail-Closed on Policy Deny / Injection | `test_f07_strict_fail_closed_on_policy_deny` | **PASSED** |
| `P2-M51-F08` | Strict Fail-Closed on SSRF Violation | `test_f08_strict_fail_closed_on_ssrf_violation` | **PASSED** |
| `P2-M51-F09` | Strict Fail-Closed on Schema / Bad Request | `test_f09_strict_fail_closed_on_bad_request` | **PASSED** |
| `P2-M51-F10` | Secret Scrubbing in Gateway Provenance and Diagnostics | `test_f10_secret_scrubbing_in_gateway` | **PASSED** |
| `P2-M51-F11` | Capability-Aware Routing | `test_f11_capability_aware_routing` | **PASSED** |
| `P2-M51-F12` | Capability Mismatch Fail-Closed | `test_f12_capability_mismatch_fail_closed` | **PASSED** |
| `P2-M51-F13` | Context Window Tracking and Metadata Enrichment | `test_f13_context_window_tracking` | **PASSED** |
| `P2-M51-F14` | Pricing Mode Free with Zero Cost Attribution | `test_f14_pricing_mode_free_zero_cost` | **PASSED** |
| `P2-M51-F15` | Pricing Mode Paid with Input/Output Cost Attribution | `test_f15_pricing_mode_paid_cost_calculation` | **PASSED** |
| `P2-M51-F16` | Non-Guaranteed Free Availability Fallback | `test_f16_non_guaranteed_free_availability_fallback` | **PASSED** |
| `P2-M51-F17` | Rate-Limit vs Hard Quota Classification | `test_f17_rate_limit_vs_hard_quota_classification` | **PASSED** |
| `P2-M51-F18` | Quota State Unknown Preservation (`quota_state: "unknown"`) | `test_f18_quota_state_unknown_preservation` | **PASSED** |
| `P2-M51-F19` | Provenance Metadata Tracking | `test_f19_provenance_metadata_tracking` | **PASSED** |
| `P2-M51-F20` | Prometheus Metrics Emission (`requests_total`, `fallbacks_total`) | `test_f20_metrics_emission` | **PASSED** |
| `P2-M51-F21` | Distributed Trace Context Propagation | `test_f21_trace_context_propagation` | **PASSED** |
| `P2-M51-F22` | Factory Multi-Provider Construction from `Settings` | `test_f22_factory_multi_provider_construction` | **PASSED** |
| `P2-M51-F23` | Multi-Provider Routing Strategy (`priority`, `cost`, `latency`) | `test_f23_routing_strategy_cost_and_priority` | **PASSED** |
| `P2-M51-F24` | Max Fallback Providers and Per-Provider Retry Bounds | `test_f24_max_fallback_providers_limit` | **PASSED** |
| `P2-M51-F25` | Circuit Breaker Half-Open State Recovery | `test_f25_circuit_breaker_half_open_recovery` | **PASSED** |
| `P2-M51-F26` | Health Check Snapshot Multi-Provider Reporting | `test_f26_health_check_snapshot` | **PASSED** |
| `P2-M51-F27` | Single Router Invariance | `test_f27_single_router_invariance` | **PASSED** |
| `P2-M51-F28` | Downstream Runtime Preserves Multi-Provider Provenance | `test_f28_downstream_preserves_provenance` | **PASSED** |
| `P2-M51-F29` | Exhaustion Diagnostics Details | `test_f29_exhaustion_diagnostics` | **PASSED** |
| `P2-M51-F30` | Thread-Safe Concurrency Under Multi-Provider Load | `test_f30_thread_safe_concurrency` | **PASSED** |
