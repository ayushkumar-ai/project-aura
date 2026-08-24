from app.aura import AURA
from app.config import settings
from core.history import ConversationHistory
from core.models import AURARequest
from core.orchestrator import Orchestrator
from core.policy import Policy
from core.tool_registry import ToolRegistry
from interfaces.tool_executor import ToolExecutor
from interfaces.tool_selector import ToolSelector
from memory.in_memory import InMemoryStore
from providers.fake_model import FakeModelProvider
from tools.echo import EchoTool
from tools.calculator import CalculatorTool



def create_orchestrator() -> Orchestrator:
    """Create the default AURA orchestration pipeline."""

    registry = ToolRegistry()
    registry.register("echo", EchoTool())
    registry.register("calculator", CalculatorTool())

    selector = ToolSelector(registry)
    executor = ToolExecutor(registry)

    return Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        memory=InMemoryStore(),
        history=ConversationHistory(),
        tool_registry=registry,
        tool_executor=executor,
        tool_selector=selector,
    )


def create_aura() -> AURA:
    """Create a persistent AURA runtime."""
    return AURA(create_orchestrator())


def run_aura(user_input: str):
    """Run a single input through a new AURA runtime."""
    aura = create_aura()
    return aura.run(user_input)


if __name__ == "__main__":
    aura = create_aura()
    response = aura.run("Hello AURA")

    print(f"{settings.aura_app_name}: {response.content}")
    print(f"Metadata: {response.metadata}")