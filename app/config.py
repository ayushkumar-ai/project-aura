from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration for Project AURA."""

    aura_env: str = "development"
    aura_app_name: str = "AURA"
    aura_log_level: str = "INFO"

    aura_model_provider: str = ""
    aura_model_name: str = ""

    aura_api_key: str = ""

    # M8 Tool Configuration
    aura_tool_default_timeout: float = 5.0

    # M8.8 Approval & Safety Configuration
    aura_auto_approve_safe_actions: bool = True

    # M9 Web & Research Configuration
    aura_search_provider: str = "fake"
    aura_search_api_key: str = ""
    aura_search_endpoint: str = ""
    aura_fetch_timeout: float = 10.0
    aura_max_search_results: int = 5
    aura_max_fetch_sources: int = 3
    aura_max_document_chars: int = 10000

    # M9.4 Browser & Dynamic Intelligence Configuration
    aura_browser_provider: str = "fake"
    aura_browser_timeout: float = 15.0
    aura_browser_render_wait: float = 1.0

    # M10 Autonomous Agent Configuration
    aura_max_plan_steps: int = 20
    aura_max_execution_iterations: int = 50
    aura_max_retries_per_step: int = 2
    aura_max_total_execution_time: float = 300.0
    aura_max_tool_calls: int = 50
    aura_max_replan_depth: int = 3

    # M9.9 Decomposition, Iterative Research & Coverage Configuration
    aura_max_sub_questions: int = 3
    aura_max_research_rounds: int = 2
    aura_max_queries_total: int = 6
    aura_research_min_coverage_ratio: float = 0.7

    # M11 Proactive & Goal-Oriented Configuration
    aura_max_active_goals: int = 10
    aura_max_evaluations_per_goal: int = 50
    aura_max_actions_per_goal: int = 20
    aura_goal_cooldown_seconds: float = 5.0
    aura_goal_timeout_seconds: float = 3600.0


    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
