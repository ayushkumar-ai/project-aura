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

    # M18 Autonomous Runtime Supervision & Checkpoint Configuration
    aura_daemon_enabled: bool = False
    aura_daemon_heartbeat_interval_seconds: float = 1.0
    aura_daemon_scheduler_interval_seconds: float = 2.0
    aura_daemon_event_interval_seconds: float = 1.0
    aura_daemon_lock_prune_interval_seconds: float = 10.0
    aura_daemon_clarification_interval_seconds: float = 10.0
    aura_daemon_memory_interval_seconds: float = 300.0
    aura_daemon_checkpoint_interval_seconds: float = 30.0
    aura_daemon_shutdown_timeout_seconds: float = 5.0
    aura_checkpoint_dir: str = ".aura_checkpoints"
    aura_checkpoint_retention_count: int = 5
    aura_task_state_storage_dir: str = ""

    # M19 Multi-Session, Real-Time Streaming & Operator Bridge Configuration
    aura_session_ttl_seconds: float = 3600.0
    aura_max_active_sessions: int = 100
    aura_max_session_history_turns: int = 100
    aura_session_storage_dir: str = ""
    aura_streaming_queue_max_size: int = 1000
    aura_streaming_replay_buffer_size: int = 1000
    aura_operator_timeout_seconds: float = 300.0
    aura_session_lock_timeout_seconds: float = 10.0
    aura_max_event_payload_chars: int = 50000

    # M20 Distributed / Externalized Model & Provider Intelligence Configuration
    aura_model_fallback_enabled: bool = True
    aura_max_model_fallback_attempts: int = 2
    aura_circuit_breaker_failure_threshold: int = 5
    aura_circuit_breaker_recovery_timeout_seconds: float = 30.0
    aura_model_request_timeout_seconds: float = 30.0
    aura_generic_model_endpoint_url: str = ""
    aura_generic_model_api_key: str = ""
    aura_generic_model_name: str = ""
    aura_local_model_endpoint_url: str = ""
    aura_allow_local_model_endpoints: bool = True

    # M51 Multi-Provider Model Gateway & Intelligent Provider Routing Configuration
    aura_model_gateway_enabled: bool = True
    aura_model_fallback_providers: str = ""
    aura_gemini_endpoint_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    aura_gemini_api_key: str = ""
    aura_gemini_model_name: str = "gemini-2.5-flash"
    aura_groq_endpoint_url: str = "https://api.groq.com/openai/v1"
    aura_groq_api_key: str = ""
    aura_groq_model_name: str = "llama-3.3-70b-versatile"
    aura_openai_endpoint_url: str = "https://api.openai.com/v1"
    aura_openai_api_key: str = ""
    aura_openai_model_name: str = "gpt-4o-mini"
    aura_gateway_retry_on_rate_limit: bool = True

    # M21 Distributed Multi-Agent Team Collaboration Configuration
    aura_max_team_members: int = 20
    aura_max_delegation_depth: int = 3
    aura_message_bus_max_queue_size: int = 1000
    aura_message_bus_max_history_size: int = 5000

    # Production Server & HTTP Deployment Configuration
    aura_server_host: str = "0.0.0.0"
    aura_server_port: int = 8000
    aura_server_api_key: str = ""
    aura_api_key_auth_enabled: bool = False
    aura_cors_allowed_origins: str = "*"
    aura_max_request_body_bytes: int = 1048576
    aura_shutdown_grace_period_seconds: float = 10.0

    # Persistent Storage Root Directories
    aura_artifact_storage_dir: str = ".aura_artifacts"
    aura_trace_storage_dir: str = ".aura_traces"
    aura_skills_storage_dir: str = ".aura_skills"
    aura_knowledge_storage_dir: str = ".aura_knowledge"

    # M42 Relational Persistence & Database Configuration
    aura_database_url: str = ""
    aura_database_pool_min_connections: int = 1
    aura_database_pool_max_connections: int = 10
    aura_database_pool_timeout_seconds: float = 30.0
    aura_database_auto_migrate: bool = True
    aura_persistence_backend: str = "auto"
    aura_legacy_migration_owner_id: str = "default"

    # M43 Production RAG & Vector Retrieval Configuration
    aura_embedding_provider: str = "mock"
    aura_embedding_model_name: str = "gemini-embedding-001"
    aura_embedding_dimension: int = 1536
    aura_embedding_endpoint_url: str = ""
    aura_embedding_api_key: str = ""
    aura_embedding_batch_size: int = 32
    aura_rag_hybrid_alpha: float = 0.7
    aura_rag_max_chunks_per_doc: int = 100
    aura_rag_chunk_size: int = 600
    aura_rag_chunk_overlap: int = 100

    # Self-Healing & Epistemic Distillation Defaults
    aura_auto_heal_on_failure: bool = True
    aura_max_healing_attempts_per_phase: int = 3

    # M44 Production Observability & Operational Reliability Configuration
    aura_structured_logging_enabled: bool = True
    aura_log_format: str = "json"
    aura_metrics_enabled: bool = True
    aura_security_audit_log_path: str = ""

    # M52 Asynchronous Background Tasks & Human Approval Configuration
    aura_task_worker_enabled: bool = True
    aura_task_worker_concurrency: int = 4
    aura_task_poll_interval_ms: int = 500
    aura_task_default_timeout_seconds: int = 600
    aura_approval_default_expiry_seconds: int = 1800
    aura_task_sse_ping_interval_seconds: int = 15


    
    # M53 Proactive Automation & Autonomous Supervisor Configuration
    aura_automations_enabled: bool = True
    aura_automation_scheduler_poll_interval_seconds: float = 5.0
    aura_automation_scheduler_batch_size: int = 10
    aura_automation_lease_ttl_seconds: int = 120
    aura_automation_lease_heartbeat_interval_seconds: float = 30.0
    aura_automation_reconciler_interval_seconds: float = 60.0
    aura_automation_max_runs_per_hour: int = 60
    aura_automation_max_recursion_depth: int = 3
    aura_automation_max_condition_chars: int = 4000
    aura_automation_max_per_tenant: int = 50
    aura_automation_lock_timeout_ms: int = 3000
    aura_automation_statement_timeout_ms: int = 3000
    aura_automation_transaction_deadline_seconds: float = 5.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()

