# Project AURA — Developer Guide & Operations Manual

This guide explains how to install, configure, develop, test, and operate the complete Project AURA platform.

---

## 1. Quickstart & Environment Setup

### Prerequisites
- Python 3.10+ (tested on Python 3.11)
- Virtual environment (`venv`)

### Installation
```bash
# Clone or navigate to the repository
cd D:/project-aura

# Activate virtual environment
# Windows:
.\.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 2. Running Tests & Benchmarks

### Run Full Test Suite
```bash
# Run all unit and integration tests
python -m pytest tests/
```

### Run Integration Tests Only
```bash
# Run end-to-end integration workflows
python -m pytest tests/integration/
```

### Run Milestone-Specific Tests
```bash
# Milestone 26: Dynamic Skill Synthesis & Sandboxing
python -m pytest tests/unit/test_code_sandbox_m26.py tests/unit/test_dynamic_skill_registry_m26.py tests/integration/test_m26_skill_synthesis_integration.py

# Milestone 27: Causal Fault Diagnosis & Self-Healing
python -m pytest tests/unit/test_causal_fault_analyzer_m27.py tests/unit/test_self_healing_orchestrator_m27.py tests/integration/test_m27_self_healing_integration.py

# Milestone 28: Epistemic Knowledge Graph & Semantic Queries
python -m pytest tests/unit/test_epistemic_graph_m28.py tests/unit/test_experience_distiller_m28.py tests/integration/test_m28_epistemic_knowledge_integration.py
```

---

## 3. Working with the AURA Facade API

The primary entry point for applications is the `AURA` class in `app/aura.py`.

### Initializing AURA
```python
from app.main import create_aura

# Initialize persistent AURA runtime with full agentic capabilities
aura = create_aura(agentic=True)
```

### Executing a Multi-Step Task
```python
result = aura.run_task("Analyze dataset, extract features, and train baseline model")
print(result)
```

### Submitting a Multi-Phase Mission Campaign
```python
from core.campaign_types import CampaignDefinition, CampaignPhase, CampaignMilestone

campaign = CampaignDefinition(
    campaign_id="camp_data_pipeline",
    title="Data Processing Pipeline",
    phases=(
        CampaignPhase(phase_id="phase_ingest", name="Ingest Data", goal_ids=("g_ingest",)),
        CampaignPhase(phase_id="phase_transform", name="Transform Data", goal_ids=("g_transform",), depends_on_phase_ids=("phase_ingest",)),
    ),
    auto_heal_on_failure=True,  # Enables M27 autonomous self-healing
)

aura.submit_campaign(campaign)
result = aura.execute_campaign("camp_data_pipeline")
print(f"Campaign Status: {result.status.value}")
```

### Synthesizing a Dynamic Python Skill
```python
from core.skill_types import TestVector

skill = aura.synthesize_skill(
    name="celsius_to_fahrenheit",
    description="Converts Celsius temperatures to Fahrenheit",
    source_code="def execute(c_str):\n    c = float(c_str.strip())\n    return str((c * 9/5) + 32)",
    test_vectors=[
        TestVector(input_data="0", expected_output_contains=("32",)),
        TestVector(input_data="100", expected_output_contains=("212",)),
    ],
    verify_after_synthesis=True,
)

# Register and execute in sandbox
aura.register_dynamic_skill(skill, activate=True)
output = aura.execute_dynamic_skill("celsius_to_fahrenheit", "25")
print(f"Result: {output}")  # "77.0"
```

### Querying the Epistemic Knowledge Graph
```python
# Retrieve recommended remediation recipes for network timeouts
recipes = aura.recommend_remediation(fault_category="TRANSIENT_INFRASTRUCTURE")
print(f"Recommended Recipes: {recipes}")

# Retrieve skills matching required capabilities
skills = aura.recommend_skills(required_capabilities=("web_scraping", "html_parsing"))
print(f"Matching Skills: {skills}")
```

---

## 4. Configuration & Model Providers

Configure AURA via environment variables or `.env`:

| Variable | Default | Description |
|---|---|---|
| `AURA_APP_NAME` | `AURA` | Application identifier |
| `AURA_LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `AURA_MODEL_PROVIDER` | `fake` | Model provider: `fake`, `openai`, `generic` |
| `AURA_MODEL_NAME` | `fake-model-v1` | Model name (e.g. `gpt-4o`, `gemini-1.5-pro`) |
| `AURA_API_KEY` | None | API key for model provider |
| `AURA_CHECKPOINT_DIR` | `.aura_checkpoints`| Directory for crash-recovery checkpoints |
| `AURA_ARTIFACT_DIR` | `.aura_artifacts` | Directory for content-addressable artifact blobs |
