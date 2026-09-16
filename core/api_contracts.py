"""M45 — Typed API Contracts & Request/Response Schemas for Project AURA.

Defines Pydantic v2 schemas for all HTTP endpoints with explicit input validation,
length constraints, type coercion guards, and consistent JSON representations.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field, field_validator


class HealthResponseSchema(BaseModel):
    """Schema for GET /health liveness probe."""
    model_config = ConfigDict(extra="ignore")

    status: str = Field(default="healthy", description="Liveness status")
    app_name: str = Field(default="AURA", description="Application name")
    version: str = Field(default="0.28.0", description="Semantic service version")
    environment: str = Field(default="production", description="Runtime environment")
    uptime_seconds: float = Field(default=0.0, description="Process uptime in seconds")
    timestamp: float = Field(default_factory=time.time, description="Epoch timestamp")


class ReadyResponseSchema(BaseModel):
    """Schema for GET /ready readiness probe."""
    model_config = ConfigDict(extra="ignore")

    status: str = Field(default="ready", description="Readiness status")
    ready: bool = Field(default=True, description="Boolean readiness flag")
    model_provider: str = Field(default="default", description="Configured LLM provider")
    agentic_enabled: bool = Field(default=True, description="Whether agentic loop is active")
    database: str = Field(default="ok", description="Database connection health status")
    timestamp: float = Field(default_factory=time.time, description="Epoch timestamp")


class ErrorDetailSchema(BaseModel):
    """Structured error payload details."""
    model_config = ConfigDict(extra="ignore")

    code: str = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable sanitized error description")
    status: int = Field(..., description="HTTP status code")
    timestamp: float = Field(default_factory=time.time, description="Epoch timestamp")
    details: dict[str, Any] | None = Field(default=None, description="Optional diagnostic details")


class ErrorResponseSchema(BaseModel):
    """Standardized top-level API error envelope."""
    model_config = ConfigDict(extra="ignore")

    error: ErrorDetailSchema


class RunRequestSchema(BaseModel):
    """Schema for POST /v1/run request execution."""
    model_config = ConfigDict(extra="ignore")

    user_input: str | None = Field(default=None, max_length=100000, description="User prompt text")
    prompt: str | None = Field(default=None, max_length=100000, description="Alternative prompt field name")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary request metadata")

    @field_validator("user_input", mode="after")
    @classmethod
    def validate_content_non_empty(cls, v: str | None, info: Any) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("user_input cannot be blank or whitespace-only")
        return v

    def get_prompt_text(self) -> str:
        """Resolve either user_input or prompt field safely."""
        text = self.user_input or self.prompt
        if not text or not text.strip():
            raise ValueError("Either 'user_input' or 'prompt' must be provided and non-empty")
        return text.strip()


class RunResponseSchema(BaseModel):
    """Schema for POST /v1/run execution result."""
    model_config = ConfigDict(extra="ignore")

    request_id: str | UUID = Field(..., description="Request correlation identifier")
    content: str = Field(..., description="AURA response text")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Execution metadata")
    timestamp: float = Field(default_factory=time.time, description="Epoch timestamp")


class TaskRequestSchema(BaseModel):
    """Schema for POST /v1/task execution."""
    model_config = ConfigDict(extra="ignore")

    task: str = Field(..., min_length=1, max_length=100000, description="Task instruction string")
    task_id: str | None = Field(default=None, max_length=128, description="Optional caller task ID")
    timeout: float | None = Field(default=None, ge=0.1, le=3600.0, description="Optional timeout seconds")


class TaskResponseSchema(BaseModel):
    """Schema for POST /v1/task execution result."""
    model_config = ConfigDict(extra="ignore")

    task: str
    task_id: str | None = None
    result: str
    status: str = "completed"
    timestamp: float = Field(default_factory=time.time)


class PreferencesUpdateRequestSchema(BaseModel):
    """Schema for POST /v1/preferences update."""
    model_config = ConfigDict(extra="ignore")

    preferred_name: str | None = Field(default=None, max_length=100)
    communication_style: str | None = Field(default=None, max_length=50)
    proactivity_level: str | None = Field(default=None, max_length=50)
    privacy_mode: str | None = Field(default=None, max_length=50)
    custom_instructions: str | None = Field(default=None, max_length=5000)
    extra_preferences: dict[str, Any] = Field(default_factory=dict)


class RAGQueryRequestSchema(BaseModel):
    """Schema for POST /v1/rag context retrieval."""
    model_config = ConfigDict(extra="ignore")

    query: str = Field(..., min_length=1, max_length=50000, description="Search query string")
    max_chars: int = Field(default=4000, ge=10, le=100000, description="Maximum characters to retrieve")


class PlanRequestSchema(BaseModel):
    """Schema for POST /v1/plan generation."""
    model_config = ConfigDict(extra="ignore")

    goal: str = Field(..., min_length=1, max_length=50000, description="Goal description to plan")


class ToolExecutionRequestSchema(BaseModel):
    """Schema for POST /v1/tools/execute invocation."""
    model_config = ConfigDict(extra="ignore")

    tool_name: str = Field(..., min_length=1, max_length=128, description="Target tool name")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Invocation parameter dictionary")
    caller: str = Field(default="agent", max_length=64, description="Caller identifier")


class ProactiveActionRequestSchema(BaseModel):
    """Schema for approving or rejecting proactive proposals."""
    model_config = ConfigDict(extra="ignore")

    proposal_id: str = Field(..., min_length=1, max_length=128, description="Proposal unique ID")
    reason: str | None = Field(default=None, max_length=1000, description="Optional rejection reason")


class DeviceActionRequestSchema(BaseModel):
    """Schema for POST /v1/devices/action execution."""
    model_config = ConfigDict(extra="ignore")

    device_id: str = Field(..., min_length=1, max_length=128, description="Target device ID")
    capability: str = Field(..., min_length=1, max_length=128, description="Device capability")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Device action parameters")


class CycleRequestSchema(BaseModel):
    """Schema for POST /v1/cycle integrated cycle execution."""
    model_config = ConfigDict(extra="ignore")

    prompt: str | None = Field(default=None, max_length=100000)
    user_input: str | None = Field(default=None, max_length=100000)
    goal: str | None = Field(default=None, max_length=100000)
    task_id: str | None = Field(default=None, max_length=128)
    auto_sync: bool = Field(default=True)

    def get_input_text(self) -> str:
        text = self.user_input or self.prompt or self.goal or ""
        return text.strip()


class AsyncTaskSubmissionSchema(BaseModel):
    """Schema for POST /v1/tasks asynchronous submission."""
    model_config = ConfigDict(extra="ignore")

    title: str = Field(..., min_length=1, max_length=255, description="Short title for the task")
    goal: str = Field(..., min_length=1, max_length=100000, description="Task objective or instruction")
    context: dict[str, Any] = Field(default_factory=dict, description="Context parameters or initial state")
    timeout_seconds: int | None = Field(default=None, ge=1, le=86400, description="Optional task timeout limit in seconds")


class ApprovalDecisionRequestSchema(BaseModel):
    """Schema for POST /v1/approvals/{id}/decide."""
    model_config = ConfigDict(extra="ignore")

    decision: str = Field(..., description="Decision outcome: approved or rejected")
    nonce: str = Field(..., min_length=1, max_length=128, description="Cryptographic nonce provided with the approval request")
    reason: str | None = Field(default="", max_length=2000, description="Optional justification or decision reason")



class AutomationCreateSchema(BaseModel):
    """Schema for POST /v1/automations creation."""
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=255, description="Human-readable automation name")
    description: str = Field(default="", max_length=2000, description="Optional description")
    trigger_type: str = Field(..., description="Trigger type: one_time, recurring, time_window, condition, event")
    trigger_config: dict[str, Any] = Field(default_factory=dict, description="Trigger configuration (cron, run_at, etc.)")
    condition_config: dict[str, Any] | None = Field(default=None, description="Optional condition configuration")
    action_template: dict[str, Any] = Field(..., description="Action template for M52 task instantiation")
    max_runs: int | None = Field(default=None, ge=1, description="Optional max execution runs limit")
    cooldown_seconds: int = Field(default=60, ge=0, description="Cooldown seconds between consecutive triggers")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata")


class AutomationUpdateSchema(BaseModel):
    """Schema for PATCH /v1/automations/{id}."""
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    trigger_config: dict[str, Any] | None = None
    condition_config: dict[str, Any] | None = None
    action_template: dict[str, Any] | None = None
    max_runs: int | None = Field(default=None, ge=1)
    cooldown_seconds: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] | None = None
