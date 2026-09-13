import argparse
import logging
import sys

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
    config: Settings | None = None,
) -> Orchestrator:
    """Create the default AURA orchestration pipeline."""

    cfg = config or Settings()
    logging.getLogger("aura").setLevel(cfg.aura_log_level.upper())

    registry = ToolRegistry()
    registry.register("echo", EchoTool())
    registry.register("calculator", CalculatorTool())

    policy = Policy()
    selector = ToolSelector(registry)
    executor = ToolExecutor(registry, policy=policy)

    model_name = cfg.aura_model_name or cfg.aura_generic_model_name
    api_key = cfg.aura_api_key or cfg.aura_generic_model_api_key
    base_url = cfg.aura_generic_model_endpoint_url or cfg.aura_local_model_endpoint_url

    return Orchestrator(
        model=create_model_provider(
            provider=cfg.aura_model_provider,
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
            timeout=cfg.aura_model_request_timeout_seconds,
            allow_local_endpoints=cfg.aura_allow_local_model_endpoints,
        ),
        policy=policy,
        memory=InMemoryStore(),
        history=ConversationHistory(),
        knowledge=knowledge,
        tool_registry=registry,
        tool_executor=executor,
        tool_selector=selector,
    )


def create_aura(agentic: bool = False, config: Settings | None = None) -> AURA:
    """Create a persistent AURA runtime.
    
    Args:
        agentic: If True, wires a full AgenticRuntime with Goal Engine, Dynamic Skills,
                 Self-Healing, and Epistemic Knowledge Graph.
        config: Optional custom Settings instance.
    """
    cfg = config or Settings()
    orchestrator = create_orchestrator(config=cfg)
    if agentic:
        from core.agentic_runtime import AgenticRuntime
        runtime = AgenticRuntime(
            model=orchestrator.model,
            policy=orchestrator.policy,
            tool_executor=orchestrator.tool_executor,
        )
        return AURA(orchestrator=orchestrator, agentic_runtime=runtime)
    return AURA(orchestrator=orchestrator)


def run_aura(user_input: str):
    """Run a single input through a new AURA runtime."""
    aura = create_aura()
    return aura.run(user_input)


def main():
    """Main CLI entrypoint for Project AURA."""
    parser = argparse.ArgumentParser(
        prog="aura",
        description="Project AURA — Autonomous Personal Intelligence Platform",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help="Direct prompt or instruction for AURA",
    )
    parser.add_argument(
        "--task",
        type=str,
        default=None,
        help="Execute an autonomous multi-step agentic task",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Inspect system health, supervisor telemetry, and active resources",
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Start interactive conversational REPL shell",
    )
    parser.add_argument(
        "--list-skills",
        action="store_true",
        help="List all registered dynamic skills in the catalog",
    )
    parser.add_argument(
        "--agentic",
        action="store_true",
        default=True,
        help="Enable full agentic runtime subsystems (enabled by default)",
    )
    parser.add_argument(
        "--server",
        action="store_true",
        help="Start the production HTTP REST API server",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Host interface for HTTP server (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for HTTP server (default: 8000)",
    )

    args = parser.parse_args()

    if args.server:
        from app.server import AURAHTTPServer
        config = Settings()
        server = AURAHTTPServer(config=config, host=args.host, port=args.port)
        server.start(block=True)
        return

    aura = create_aura(agentic=args.agentic)

    if args.health:
        print(f"=== {settings.aura_app_name} System Health Diagnostics ===")
        health = aura.get_health_status()
        for k, v in health.items():
            print(f"  {k}: {v}")
        return

    if args.list_skills:
        print(f"=== {settings.aura_app_name} Dynamic Skills Catalog ===")
        skills = aura.list_dynamic_skills()
        if not skills:
            print("  No dynamic skills currently registered.")
        for s in skills:
            print(f"  - {s.name} (v{s.version}): {s.description} [{s.lifecycle_state.value}]")
        return

    if args.task:
        print(f"Executing autonomous task: {args.task}")
        result = aura.run_task(args.task)
        print(f"\nResult:\n{result}")
        return

    if args.interactive:
        print(f"==================================================")
        print(f"  Welcome to {settings.aura_app_name} Interactive Shell")
        print(f"  Type 'exit', 'quit', or Ctrl+C to terminate.")
        print(f"==================================================\n")
        try:
            while True:
                user_input = input("AURA> ").strip()
                if not user_input:
                    continue
                if user_input.lower() in ("exit", "quit", "q"):
                    print("Goodbye!")
                    break
                response = aura.run(user_input)
                print(f"\n{response.content}\n")
        except (KeyboardInterrupt, EOFError):
            print("\nSession terminated.")
        return

    if args.prompt:
        response = aura.run(args.prompt)
        print(f"{settings.aura_app_name}: {response.content}")
        return

    # Default fallback
    response = aura.run("Hello AURA")
    print(f"{settings.aura_app_name}: {response.content}")
    print(f"Metadata: {response.metadata}")


if __name__ == "__main__":
    main()

