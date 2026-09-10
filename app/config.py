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

    # M12 Hierarchical Goals & Orchestration Configuration
    aura_max_subgoal_depth: int = 3
    aura_max_subgoals_per_parent: int = 5
    aura_max_goal_dependencies: int = 10
    aura_max_goal_observations: int = 50

    # M13 Multi-Tier Agent Memory Configuration
    aura_max_working_memory_entries: int = 50
    aura_max_semantic_facts: int = 200
    aura_max_episodic_records: int = 500
    aura_max_memory_search_results: int = 5
    aura_max_memory_fact_chars: int = 4000
    aura_memory_storage_dir: str = ""

    # M14 Agent Self-Reflection & Memory Consolidation Configuration
    aura_max_reflection_passes: int = 1
    aura_max_reflection_chars: int = 2000
    aura_max_consolidation_batch: int = 10
    aura_max_distilled_facts_per_run: int = 5
    aura_max_research_ingested_claims: int = 10
    aura_consolidation_timeout_seconds: float = 15.0
    aura_belief_revision_threshold: float = 0.85

    # M15 Long-Term Memory Lifecycle, Decay & Heuristic Calibration Configuration
    aura_memory_decay_enabled: bool = True
    aura_memory_default_half_life_days: float = 30.0
    aura_memory_compaction_threshold: float = 0.85
    aura_max_compaction_facts_per_run: int = 20
    aura_rule_min_trials_for_promotion: int = 3
    aura_rule_deprecation_failure_rate: float = 0.60
    aura_lifecycle_batch_timeout_seconds: float = 10.0

    # M16 Goal Strategy Adaptation, Meta-Policy & Stagnation Configuration
    aura_max_strategy_retries: int = 3
    aura_max_goal_stagnation_evaluations: int = 5
    aura_max_strategy_history_per_goal: int = 10
    aura_strategy_selection_timeout_seconds: float = 5.0

    # M17 Multi-Goal Resource Arbitration, Scheduling & Event Dispatch Configuration
    aura_max_concurrent_active_goals: int = 4
    aura_global_max_tool_calls_per_minute: int = 120
    aura_global_max_tokens_per_minute: int = 100000
    aura_resource_lock_default_ttl_seconds: float = 30.0
    aura_event_queue_max_size: int = 1000
    aura_clarification_timeout_seconds: float = 600.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
