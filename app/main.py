import logging

from app.aura import AURA
from app.config import Settings, settings
from core.history import ConversationHistory
from core.models import AURARequest
from core.orchestrator import Orchestrator
from core.policy import Policy
from core.tool_registry import ToolRegistry
from interfaces.tool_executor import ToolExecutor
from interfaces.tool_selector import ToolSelector
from interfaces.knowledge import KnowledgeInterface
from memory.in_memory import InMemoryStore
from providers.factory import create_model_provider
from tools.echo import EchoTool
from tools.calculator import CalculatorTool



def create_orchestrator(
    knowledge: KnowledgeInterface | None = None,
) -> Orchestrator:
    """Create the default AURA orchestration pipeline."""

    config = Settings()
    logging.getLogger("aura").setLevel(config.aura_log_level.upper())

    registry = ToolRegistry()
    registry.register("echo", EchoTool())
    registry.register("calculator", CalculatorTool())

    policy = Policy()
    selector = ToolSelector(registry)
    executor = ToolExecutor(registry, policy=policy)

    return Orchestrator(
        model=create_model_provider(
            provider=config.aura_model_provider,
            model_name=config.aura_model_name,
            api_key=config.aura_api_key,
        ),
        policy=policy,
        memory=InMemoryStore(),
        history=ConversationHistory(),
        knowledge=knowledge,
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
