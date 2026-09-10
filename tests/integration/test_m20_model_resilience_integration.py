import json
import pytest
from unittest.mock import MagicMock
from uuid import UUID, uuid4

from app.aura import AURA
from app.config import settings
from core.agentic_runtime import AgenticRuntime, ExecutionMode
from core.capability_registry import CapabilityRegistry, ModelCapability, ModelDescriptor
from core.circuit_breaker import CircuitBreakerConfig, CircuitState
from core.model_router import TaskRequirements
from core.models import AURARequest, AURAResponse
from core.orchestrator import Orchestrator
from core.policy import Policy
from core.provider_health import ProviderHealthTracker, ProviderHealthStatus
from core.provider_registry import ProviderRegistry
from core.resilient_router import (
    FallbackStrategy,
    ModelRoutingExhaustedError,
    ResilientModelRouter,
    RoutingOutcome,
)
from core.skill_registry import Skill, SkillRegistry
from core.task_planner import TaskPlanner, PlanStep, ExecutionPlan
from interfaces.model import ModelInterface
from providers.fake_model import FakeModelProvider
from providers.generic_provider import GenericOpenAICompatibleProvider, validate_endpoint_url


class FailingMockProvider(ModelInterface):
    """Mock model provider that always fails."""

    def __init__(self, name: str = "failing_provider", error_msg: str = "API connection timed out"):
        self._name = name
        self._error_msg = error_msg
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    def generate(self, prompt: str, request_id: UUID | None = None) -> AURAResponse:
        self.call_count += 1
        raise ConnectionError(f"{self._name}: {self._error_msg}")


class WorkingMockProvider(ModelInterface):
    """Mock model provider that succeeds and returns structured plan content."""

    def __init__(self, name: str = "working_provider", response_text: str | None = None):
        self._name = name
        self._response_text = response_text or json.dumps({
            "steps": [
                {"step_id": "s1", "skill_name": "analyze_skill", "description": "Run analysis", "dependencies": []},
                {"step_id": "s2", "skill_name": "report_skill", "description": "Report summary", "dependencies": ["s1"]},
            ]
        })
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    def generate(self, prompt: str, request_id: UUID | None = None) -> AURAResponse:
        self.call_count += 1
        return AURAResponse(
            request_id=request_id or uuid4(),
            content=self._response_text,
            metadata={"call_count": str(self.call_count), "provider": str(self._name)},
        )


def test_m20_e2e_primary_failure_fallback_cascade():
    """End-to-end integration: primary provider fails, resilient router falls back to working secondary."""
    cap_reg = CapabilityRegistry()
    prov_reg = ProviderRegistry()

    # Primary model (failing)
    cap_reg.register_model(
        ModelDescriptor(
            model_id="gpt-4o-primary",
            provider_id="primary_prov",
            capabilities=[ModelCapability.REASONING, ModelCapability.CODING],
        )
    )
    failing_prov = FailingMockProvider(name="primary_prov")
    prov_reg.register("primary_prov", failing_prov)

    # Fallback model (working)
    cap_reg.register_model(
        ModelDescriptor(
            model_id="claude-3-fallback",
            provider_id="fallback_prov",
            capabilities=[ModelCapability.REASONING, ModelCapability.CODING],
        )
    )
    working_prov = WorkingMockProvider(
        name="fallback_prov",
        response_text="Strategy generated successfully",
    )
    prov_reg.register("fallback_prov", working_prov)

    health_tracker = ProviderHealthTracker(
        default_cb_config=CircuitBreakerConfig(failure_threshold=3, recovery_timeout_seconds=60.0)
    )
    router = ResilientModelRouter(
        capability_registry=cap_reg,
        provider_registry=prov_reg,
        health_tracker=health_tracker,
        max_fallback_attempts=2,
        fallback_enabled=True,
    )

    # Execute generation with fallback
    req_id = uuid4()
    response, telemetry = router.generate_with_fallback(
        prompt="Develop an execution strategy",
        request_id=req_id,
        requirements=TaskRequirements(preferred_provider="primary_prov"),
    )

    assert response is not None
    assert response.content == "Strategy generated successfully"
    assert telemetry.outcome == RoutingOutcome.SUCCESS_FALLBACK
    assert telemetry.selected_primary_provider_id == "primary_prov"
    assert telemetry.attempted_provider_ids == ["primary_prov", "fallback_prov"]
    assert telemetry.successful_provider_id == "fallback_prov"
    assert telemetry.fallback_count == 1
    assert failing_prov.call_count == 1
    assert working_prov.call_count == 1

    # Verify health metrics updated
    primary_metrics = health_tracker.get_metrics("primary_prov")
    assert primary_metrics.failed_requests == 1
    assert primary_metrics.consecutive_failures == 1

    fallback_metrics = health_tracker.get_metrics("fallback_prov")
    assert fallback_metrics.successful_requests == 1
    assert fallback_metrics.consecutive_failures == 0


def test_m20_e2e_circuit_breaker_trips_and_skips_failed_provider():
    """Circuit breaker trips to OPEN after threshold failures and subsequent routes skip it immediately."""
    cap_reg = CapabilityRegistry()
    prov_reg = ProviderRegistry()

    cap_reg.register_model(
        ModelDescriptor(
            model_id="bad-model",
            provider_id="bad_prov",
            capabilities=[ModelCapability.REASONING],
        )
    )
    failing_prov = FailingMockProvider(name="bad_prov")
    prov_reg.register("bad_prov", failing_prov)

    cap_reg.register_model(
        ModelDescriptor(
            model_id="good-model",
            provider_id="good_prov",
            capabilities=[ModelCapability.REASONING],
        )
    )
    working_prov = WorkingMockProvider(name="good_prov", response_text="Success")
    prov_reg.register("good_prov", working_prov)

    health_tracker = ProviderHealthTracker(
        default_cb_config=CircuitBreakerConfig(failure_threshold=3, recovery_timeout_seconds=100.0)
    )
    router = ResilientModelRouter(
        capability_registry=cap_reg,
        provider_registry=prov_reg,
        health_tracker=health_tracker,
        max_fallback_attempts=2,
    )

    # Fail 3 times to trip bad_prov circuit
    for _ in range(3):
        router.generate_with_fallback("Test prompt", requirements=TaskRequirements(preferred_provider="bad_prov"))

    bad_cb = health_tracker.get_or_create_circuit_breaker("bad_prov")
    assert bad_cb.state == CircuitState.OPEN
    assert failing_prov.call_count == 3
    assert working_prov.call_count == 3

    # Next call should select good_prov without calling bad_prov at all
    prev_failing_calls = failing_prov.call_count
    resp, telem = router.generate_with_fallback("New prompt", requirements=TaskRequirements(preferred_provider="bad_prov"))
    assert resp.content == "Success"
    assert failing_prov.call_count == prev_failing_calls  # Skipped!
    assert working_prov.call_count == 4


def test_m20_e2e_agentic_runtime_integration_and_facade():
    """AgenticRuntime and AURA facade full integration with ResilientModelRouter and ProviderHealthTracker."""
    cap_reg = CapabilityRegistry()
    prov_reg = ProviderRegistry()

    cap_reg.register_model(
        ModelDescriptor(
            model_id="primary-llm",
            provider_id="primary_p",
            capabilities=[ModelCapability.REASONING],
        )
    )
    failing_p = FailingMockProvider(name="primary_p")
    prov_reg.register("primary_p", failing_p)

    cap_reg.register_model(
        ModelDescriptor(
            model_id="backup-llm",
            provider_id="backup_p",
            capabilities=[ModelCapability.REASONING],
        )
    )
    plan_json = json.dumps({
        "steps": [
            {"step_id": "s1", "skill_name": "analyze_skill", "description": "Run analysis", "dependencies": []},
            {"step_id": "s2", "skill_name": "report_skill", "description": "Report summary", "dependencies": ["s1"]},
        ]
    })
    working_p = WorkingMockProvider(name="backup_p", response_text=plan_json)
    prov_reg.register("backup_p", working_p)

    health_tracker = ProviderHealthTracker(
        default_cb_config=CircuitBreakerConfig(failure_threshold=2, recovery_timeout_seconds=30.0)
    )
    resilient_router = ResilientModelRouter(
        capability_registry=cap_reg,
        provider_registry=prov_reg,
        health_tracker=health_tracker,
        max_fallback_attempts=2,
    )

    skill_reg = SkillRegistry()
    skill_reg.register(Skill(name="analyze_skill", description="Analyze telemetry"))
    skill_reg.register(Skill(name="report_skill", description="Report summary"))

    planner = TaskPlanner(
        skill_registry=skill_reg,
        model_router=resilient_router,
    )

    agentic_runtime = AgenticRuntime(
        skill_registry=skill_reg,
        planner=planner,
        model_router=resilient_router,
        health_tracker=health_tracker,
        resilient_router=resilient_router,
    )

    fake_model = FakeModelProvider()
    policy = Policy()
    orchestrator = Orchestrator(model=fake_model, policy=policy, agentic_runtime=agentic_runtime)
    aura_app = AURA(orchestrator=orchestrator, agentic_runtime=agentic_runtime)

    # Check facade provider health
    health = aura_app.get_provider_health()
    assert isinstance(health, dict)

    # Execute workflow task through planner using resilient router fallback
    plan = planner.plan(
        task="Analyze system telemetry",
        task_requirements=TaskRequirements(preferred_provider="primary_p"),
    )
    assert len(plan.steps) == 2
    assert plan.steps[0].skill_name == "analyze_skill"
    assert plan.steps[1].skill_name == "report_skill"
    assert failing_p.call_count == 1
    assert working_p.call_count == 1

    # Check updated provider health from AURA facade
    health = aura_app.get_provider_health()
    prov_dict = health.get("providers", health)
    assert "primary_p" in prov_dict
    assert prov_dict["primary_p"]["failed_requests"] == 1
    assert "backup_p" in prov_dict
    assert prov_dict["backup_p"]["successful_requests"] == 1


def test_m20_generic_openai_provider_ssrf_and_secret_safety():
    """Verify GenericOpenAICompatibleProvider SSRF restrictions and secret isolation."""
    # 1. SSRF block cloud metadata
    with pytest.raises(ValueError, match="SSRF violation"):
        GenericOpenAICompatibleProvider(
            base_url="http://169.254.169.254/v1",
            model_name="test-model",
            api_key="super-secret-key-123",
            allow_local_endpoints=False,
        )

    # 2. SSRF block localhost when allow_local_endpoints=False
    with pytest.raises(ValueError, match="SSRF violation"):
        GenericOpenAICompatibleProvider(
            base_url="http://127.0.0.1:11434/v1",
            model_name="test-model",
            api_key="super-secret-key-123",
            allow_local_endpoints=False,
        )

    # 3. Allow localhost when allow_local_endpoints=True
    mock_client = MagicMock()
    provider = GenericOpenAICompatibleProvider(
        base_url="http://127.0.0.1:11434/v1",
        model_name="llama3.1",
        api_key="super-secret-key-123",
        allow_local_endpoints=True,
        client=mock_client,
    )
    assert provider.base_url == "http://127.0.0.1:11434/v1"
    assert provider.model_name == "llama3.1"

    # 4. Secret redaction during client failure
    mock_client.chat.completions.create.side_effect = Exception("Auth failed with super-secret-key-123")
    if hasattr(mock_client, "responses"):
        mock_client.responses.create.side_effect = Exception("Auth failed with super-secret-key-123")

    with pytest.raises(RuntimeError) as exc_info:
        provider.generate("Hello world", request_id=uuid4())
    err_msg = str(exc_info.value)
    assert "super-secret-key-123" not in err_msg
    assert "[REDACTED_API_KEY]" in err_msg
