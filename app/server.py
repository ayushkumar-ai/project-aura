"""Production HTTP Server for Project AURA.

Provides a robust, zero-external-dependency HTTP REST API service supporting:
- Liveness probe: GET /health
- Readiness probe: GET /ready
- Health telemetry: GET /metrics, GET /v1/telemetry
- Request execution: POST /v1/run
- Autonomous task execution: POST /v1/task
- Dynamic skills catalog: GET /v1/skills
- Mission campaigns: GET /v1/campaigns
- Epistemic knowledge queries: GET /v1/knowledge/query
- Multi-user authentication & principal isolation (M41)
- Payload size limiting, CORS headers, and graceful shutdown signal handling
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import parse_qs, urlparse

from enum import Enum
from pathlib import Path
from uuid import UUID, uuid4
from app.aura import AURA
from app.config import Settings, settings
from app.main import create_aura
from core.models import AURARequest
from core.security_scrubber import sanitize_error_message, scrub_dict
from core.identity import UserIdentity, UserRole, UserScope, create_anonymous_identity, create_dev_identity
from core.auth import BaseAuthenticator, TokenAuthenticator, create_token_authenticator
from core.repositories.factory import create_repository_container, RepositoryContainer
from core.telemetry_context import (
    validate_or_generate_request_id,
    set_correlation_context,
    clear_correlation_context,
    get_current_request_id,
    get_current_trace_id,
)
from core.metrics import get_metrics_registry
from core.security_audit import SecurityEventType, get_security_audit_logger
from core.structured_logger import configure_structured_logging
from pydantic import ValidationError
from core.api_contracts import (
    RunRequestSchema,
    TaskRequestSchema,
    PreferencesUpdateRequestSchema,
    RAGQueryRequestSchema,
    PlanRequestSchema,
    ToolExecutionRequestSchema,
    ProactiveActionRequestSchema,
    DeviceActionRequestSchema,
    CycleRequestSchema,
    AsyncTaskSubmissionSchema,
    ApprovalDecisionRequestSchema,
    AutomationCreateSchema,
    AutomationUpdateSchema,
)
from core.background import BackgroundTaskWorker, TaskEventBroadcaster, WorkflowOrchestrator
from core.automations import AutonomousSupervisor, validate_cron, calculate_next_fire
from core.repositories.base import BaseApprovalRepository, BaseTaskRepository, BaseAutomationRepository
from core.repositories.base_webhook import BaseWebhookRepository
from core.repositories.base_fleet import BaseFleetRepository
from core.fleet import (
    DistributedFleetWorker,
    WorkerFleetCoordinator,
    TenantFairnessScheduler,
    FleetRecoveryService,
    HeartbeatManager,
    WorkerStatus,
)
from core.api_contracts import (
    TenantQuotaUpdateSchema,
    FleetDrainRequestSchema,
)
from core.webhooks import (
    WebhookIngressService,
    EventDispatcherService,
    OutboundDeliveryWorker,
    DeadLetterReplayService,
    WebhookMaintenanceService,
)
from core.api_contracts import (
    WebhookEndpointCreateSchema,
    WebhookEndpointUpdateSchema,
    EventSubscriptionCreateSchema,
    EventSubscriptionUpdateSchema,
    DeadLetterReplaySchema,
    CognitiveMemoryRecordSchema,
    CognitiveMemoryUpdateSchema,
    CognitiveProfileUpdateSchema,
    MemoryFeedbackRequestSchema,
    ContradictionResolveRequestSchema,
    MultimodalArtifactUploadSchema,
    MultimodalProcessRequestSchema,
    MultimodalArtifactUpdateSchema,
)
from core.repositories.base_cognitive_memory import BaseCognitiveMemoryRepository
from core.repositories.base_multimodal import BaseMultimodalRepository
from core.multimodal import (
    MultimodalProcessor,
    IObjectStorageService,
    LocalStorageService,
    InMemoryStorageService,
    MultimodalCapabilityRegistry,
    MultimodalMemoryBridge,
    MultimodalDeletionCascade,
    MultimodalArtifact,
    MultimodalResult,
    MultimodalMediaType,
    MediaFormat,
    ArtifactLifecycleState,
    MultimodalProvenance,
    SecurityClassification,
)
from core.cognitive_memory import (
    CognitiveMemory,
    CognitiveMemoryType,
    ProvenanceType,
    LifecycleState,
    ContradictionStatus,
    ResolutionStrategy,
    FeedbackType,
    PersonalizationEngine,
    FeedbackLearningLoop,
    MemoryConsolidationEngine,
    MemoryFeedbackEvent,
    UserCognitiveProfile,
)
import queue
from core.config_validator import validate_production_config

logger = logging.getLogger("aura.server")


def _default_json_encoder(obj: Any) -> Any:
    """Serialize non-standard JSON types including UUID, Path, Enum, and custom models."""
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server enabling concurrent request handling."""
    daemon_threads = True
    allow_reuse_address = True
    authenticator: BaseAuthenticator


class AURAHTTPRequestHandler(BaseHTTPRequestHandler):
    """Production request handler for Project AURA REST API with M41 identity & authorization."""

    server_version = "AURA-HTTP/0.28.0"

    def log_message(self, format: str, *args: Any) -> None:
        """Route standard HTTP server logging to standard logger."""
        logger.info(f"{self.client_address[0]} - {format % args}")

    @property
    def aura(self) -> AURA:
        return self.server.aura  # type: ignore

    @property
    def config(self) -> Settings:
        return self.server.config  # type: ignore

    @property
    def start_time(self) -> float:
        return self.server.start_time  # type: ignore

    @property
    def authenticator(self) -> BaseAuthenticator:
        return self.server.authenticator  # type: ignore

    @property
    def broadcaster(self) -> TaskEventBroadcaster | None:
        return getattr(self.server, "broadcaster", None)

    @property
    def workflow_orchestrator(self) -> WorkflowOrchestrator | None:
        return getattr(self.server, "workflow_orchestrator", None)

    @property
    def task_worker(self) -> BackgroundTaskWorker | None:
        return getattr(self.server, "task_worker", None)

    @property
    def task_repo(self) -> BaseTaskRepository | None:
        rc = getattr(self.server, "repository_container", None)
        return getattr(rc, "tasks", None) if rc else None

    @property
    def approval_repo(self) -> BaseApprovalRepository | None:
        rc = getattr(self.server, "repository_container", None)
        return getattr(rc, "approvals", None) if rc else None

    @property
    def automation_repo(self) -> BaseAutomationRepository | None:
        rc = getattr(self.server, "repository_container", None)
        return getattr(rc, "automations", None) if rc else None

    @property
    def supervisor(self) -> AutonomousSupervisor | None:
        return getattr(self.server, "supervisor", None)

    @property
    def webhook_repo(self) -> BaseWebhookRepository | None:
        rc = getattr(self.server, "repository_container", None)
        return getattr(rc, "webhooks", None) if rc else None

    @property
    def ingress_service(self) -> WebhookIngressService | None:
        srv = getattr(self.server, "ingress_service", None)
        if srv is None and self.webhook_repo is not None:
            master_key = getattr(self.config, "aura_webhook_master_key", "aura-default-master-key-32bytes!!")
            srv = WebhookIngressService(
                webhook_repo=self.webhook_repo,
                task_repo=self.task_repo,
                automation_repo=self.automation_repo,
                master_key=master_key,
            )
            setattr(self.server, "ingress_service", srv)
        return srv

    @property
    def dispatcher_service(self) -> EventDispatcherService | None:
        srv = getattr(self.server, "dispatcher_service", None)
        if srv is None and self.webhook_repo is not None:
            master_key = getattr(self.config, "aura_webhook_master_key", "aura-default-master-key-32bytes!!")
            srv = EventDispatcherService(
                webhook_repo=self.webhook_repo,
                master_key=master_key,
            )
            setattr(self.server, "dispatcher_service", srv)
        return srv

    @property
    def replay_service(self) -> DeadLetterReplayService | None:
        srv = getattr(self.server, "replay_service", None)
        if srv is None and self.webhook_repo is not None:
            master_key = getattr(self.config, "aura_webhook_master_key", "aura-default-master-key-32bytes!!")
            srv = DeadLetterReplayService(
                webhook_repo=self.webhook_repo,
                master_key=master_key,
            )
            setattr(self.server, "replay_service", srv)
        return srv

    @property
    def delivery_worker(self) -> OutboundDeliveryWorker | None:
        return getattr(self.server, "delivery_worker", None)

    @property
    def fleet_repo(self) -> BaseFleetRepository | None:
        rc = getattr(self.server, "repository_container", None)
        return getattr(rc, "fleet", None) if rc else None

    @property
    def fleet_coordinator(self) -> WorkerFleetCoordinator | None:
        return getattr(self.server, "fleet_coordinator", None)

    @property
    def fleet_worker(self) -> DistributedFleetWorker | None:
        return getattr(self.server, "fleet_worker", None)

    @property
    def cognitive_memory_repo(self) -> BaseCognitiveMemoryRepository | None:
        rc = getattr(self.server, "repository_container", None)
        return getattr(rc, "cognitive_memories", None) if rc else None

    @property
    def personalization_engine(self) -> PersonalizationEngine:
        pe = getattr(self.server, "personalization_engine", None)
        if pe is None:
            pe = PersonalizationEngine()
            setattr(self.server, "personalization_engine", pe)
        return pe

    @property
    def feedback_loop(self) -> FeedbackLearningLoop:
        fl = getattr(self.server, "feedback_loop", None)
        if fl is None:
            fl = FeedbackLearningLoop()
            setattr(self.server, "feedback_loop", fl)
        return fl

    @property
    def consolidation_engine(self) -> MemoryConsolidationEngine:
        ce = getattr(self.server, "consolidation_engine", None)
        if ce is None:
            ce = MemoryConsolidationEngine()
            setattr(self.server, "consolidation_engine", ce)
        return ce

    @property
    def multimodal_repo(self) -> BaseMultimodalRepository | None:
        rc = getattr(self.server, "repository_container", None)
        return getattr(rc, "multimodal", None) if rc else None

    @property
    def multimodal_storage(self) -> IObjectStorageService:
        st = getattr(self.server, "multimodal_storage", None)
        if st is None:
            st = InMemoryStorageService()
            setattr(self.server, "multimodal_storage", st)
        return st

    @property
    def multimodal_processor(self) -> MultimodalProcessor:
        mp = getattr(self.server, "multimodal_processor", None)
        if mp is None:
            mp = MultimodalProcessor(
                repository=self.multimodal_repo,
                storage=self.multimodal_storage,
                model_gateway=getattr(self.server.aura, "model_gateway", None) if hasattr(self.server, "aura") else None,
            )
            setattr(self.server, "multimodal_processor", mp)
        return mp

    @property
    def multimodal_bridge(self) -> MultimodalMemoryBridge:
        mb = getattr(self.server, "multimodal_bridge", None)
        if mb is None:
            mb = MultimodalMemoryBridge(
                memory_repo=self.cognitive_memory_repo,
                multimodal_repo=self.multimodal_repo,
            )
            setattr(self.server, "multimodal_bridge", mb)
        return mb

    @property
    def multimodal_deletion_cascade(self) -> MultimodalDeletionCascade:
        dc = getattr(self.server, "multimodal_deletion_cascade", None)
        if dc is None:
            dc = MultimodalDeletionCascade(
                multimodal_repo=self.multimodal_repo,
                memory_repo=self.cognitive_memory_repo,
                storage=self.multimodal_storage,
            )
            setattr(self.server, "multimodal_deletion_cascade", dc)
        return dc

    def _read_raw_body(self) -> bytes | None:
        """Safely read raw binary body within size limits (M54)."""
        content_length_header = self.headers.get("Content-Length")
        if not content_length_header:
            self._send_error_response(HTTPStatus.BAD_REQUEST, "missing_content_length", "Content-Length header required")
            return None

        try:
            length = int(content_length_header)
        except ValueError:
            self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_content_length", "Invalid Content-Length header")
            return None

        max_bytes = getattr(self.config, "aura_webhook_max_payload_bytes", 1048576)
        if length > max_bytes:
            self._send_error_response(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "payload_too_large",
                f"Payload size {length} bytes exceeds limit of {max_bytes} bytes",
            )
            return None

        try:
            raw_data = self.rfile.read(length)
            self._body_read = True
            return raw_data
        except Exception as e:
            self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "read_error", f"Failed to read payload: {e}")
            return None

    def _setup_request_context(self) -> tuple[str, str, Any]:
        """Initialize correlation context from headers (X-Request-ID, W3C traceparent)."""
        incoming_req_id = self.headers.get("X-Request-ID")
        req_id = validate_or_generate_request_id(incoming_req_id)

        incoming_tp = self.headers.get("traceparent")
        parent_ctx = None
        if incoming_tp:
            try:
                from core.trace_types import TraceContext
                parent_ctx = TraceContext.from_traceparent(incoming_tp)
            except Exception:
                parent_ctx = None

        from uuid import uuid4
        tr_id = parent_ctx.trace_id if parent_ctx else uuid4().hex.lower()[:32].rjust(32, "0")
        set_correlation_context(request_id=req_id, trace_id=tr_id)
        self.request_id = req_id
        self.trace_id = tr_id
        self.req_start = time.time()
        return req_id, tr_id, parent_ctx

    def _set_cors_headers(self) -> None:
        """Apply CORS and correlation headers from configuration."""
        origin = self.config.aura_cors_allowed_origins
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Request-ID, traceparent")
        req_id = getattr(self, "request_id", None) or get_current_request_id()
        if req_id:
            self.send_header("X-Request-ID", str(req_id))
        tr_id = getattr(self, "trace_id", None) or get_current_trace_id()
        if tr_id:
            self.send_header("X-Trace-ID", str(tr_id))

    def _send_json_response(
        self,
        data: Any,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        """Send a formatted JSON response with security headers and record metrics."""
        stat_code = status.value if isinstance(status, HTTPStatus) else int(status)
        self._record_http_metric(
            getattr(self, "command", "GET"),
            getattr(self, "path", "/"),
            stat_code,
            getattr(self, "req_start", time.time()),
        )

        try:
            body = json.dumps(
                scrub_dict(data) if isinstance(data, dict) else data,
                default=_default_json_encoder,
                indent=2,
            ).encode("utf-8")
        except Exception as e:
            logger.error(f"JSON serialization error: {e}")
            self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "serialization_error", "Failed to encode response")
            return

        try:
            self.send_response(stat_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._set_cors_headers()
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as e:
            logger.debug(f"Client disconnected before response could be sent: {e}")


    def _drain_body(self) -> None:
        """Safely drain bounded unread request body from socket to prevent TCP RST on early error."""
        if getattr(self, "_body_read", False):
            return
        self._body_read = True
        try:
            cl = self.headers.get("Content-Length")
            if cl:
                length = int(cl)
                max_bytes = self.config.aura_max_request_body_bytes
                if 0 < length <= max_bytes:
                    self.rfile.read(length)
        except Exception:
            pass

    def _send_error_response(
        self,
        status: HTTPStatus,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Send structured JSON error response."""
        if status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE:
            self.close_connection = True
        else:
            self._drain_body()
        is_prod = self.config.aura_env.lower() == "production"
        safe_message = sanitize_error_message(message, is_production=is_prod)
        payload = {
            "error": {
                "code": code,
                "message": safe_message,
                "status": status.value,
                "timestamp": time.time(),
            }
        }
        if details and not is_prod:
            payload["error"]["details"] = scrub_dict(details)
        self._send_json_response(payload, status=status)

    def _resolve_identity(self) -> tuple[bool, UserIdentity | None, str | None, int]:
        """Verify credentials and resolve canonical UserIdentity principal (M41).
        
        Returns (is_authenticated, identity, error_message, http_status_code).
        """
        if not self.config.aura_api_key_auth_enabled:
            # When auth is disabled:
            # Dev/test/non-prod environments map to a dev principal; production explicitly maps to anonymous
            if self.config.aura_env.lower() != "production":
                return True, create_dev_identity(), None, 200
            return True, create_anonymous_identity(), None, 200

        auth_header = self.headers.get("Authorization", "").strip()
        if not auth_header or not auth_header.startswith("Bearer "):
            return False, None, "Invalid or missing Bearer token", 401

        token = auth_header[7:].strip()
        if not token:
            return False, None, "Bearer token cannot be empty", 401

        res = self.authenticator.authenticate(token)
        if not res.success:
            return False, None, res.error_message or "Authentication failed", res.status_code

        return True, res.identity, None, 200

    def _read_json_body(self) -> dict[str, Any] | None:
        """Safely read and validate JSON request body within size limits."""
        content_length_header = self.headers.get("Content-Length")
        if not content_length_header:
            self._send_error_response(HTTPStatus.BAD_REQUEST, "missing_content_length", "Content-Length header required")
            return None

        try:
            length = int(content_length_header)
        except ValueError:
            self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_content_length", "Invalid Content-Length header")
            return None

        max_bytes = self.config.aura_max_request_body_bytes
        if length > max_bytes:
            self._send_error_response(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "payload_too_large",
                f"Payload size {length} bytes exceeds limit of {max_bytes} bytes",
            )
            return None

        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            self._send_error_response(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "unsupported_media_type",
                "Content-Type must be application/json",
            )
            return None

        try:
            raw_data = self.rfile.read(length)
            self._body_read = True
            return json.loads(raw_data.decode("utf-8"))
        except json.JSONDecodeError as e:
            self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_json", f"Malformed JSON: {e.msg}")
            return None
        except Exception as e:
            self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "read_error", f"Failed to read payload: {e}")
            return None

    def _serve_static(self, rel_path: str) -> None:
        """Serve static web client assets safely from app/static directory."""
        static_root = (Path(__file__).resolve().parent / "static").resolve()
        if not static_root.exists():
            self._send_error_response(HTTPStatus.NOT_FOUND, "not_found", "Static assets directory not found")
            return

        clean_rel = rel_path.lstrip("/")
        if not clean_rel or clean_rel in ("ui", "index.html"):
            clean_rel = "index.html"
        elif clean_rel.startswith("static/"):
            clean_rel = clean_rel[7:]

        try:
            target_file = (static_root / clean_rel).resolve()
            # Security: Prevent directory/path traversal attacks
            if not str(target_file).startswith(str(static_root)):
                self._send_error_response(HTTPStatus.FORBIDDEN, "forbidden", "Access denied")
                return

            if not target_file.is_file():
                self._send_error_response(HTTPStatus.NOT_FOUND, "not_found", f"File '{clean_rel}' not found")
                return

            content_types = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".json": "application/json; charset=utf-8",
                ".svg": "image/svg+xml",
                ".png": "image/png",
                ".ico": "image/x-icon",
            }
            content_type = content_types.get(target_file.suffix.lower(), "application/octet-stream")

            body = target_file.read_bytes()
            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self._set_cors_headers()
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as ce:
                logger.debug(f"Client disconnected during static file delivery: {ce}")
        except Exception as e:
            logger.error(f"Error serving static file {clean_rel}: {e}")
            self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "file_error", "Error reading static file")

    def _record_http_metric(self, method: str, path: str, status_code: int, start_time: float) -> None:
        """Record HTTP request counter and latency histogram."""
        try:
            duration = time.time() - start_time
            metrics = get_metrics_registry()
            # Normalize path for low cardinality
            norm_path = path.split("?")[0]
            if len(norm_path) > 32:
                norm_path = norm_path[:32]
            metrics.get_counter("aura_http_requests_total").inc(
                labels={"method": method, "path": norm_path, "status_code": str(status_code)}
            )
            metrics.get_histogram("aura_http_request_duration_seconds").observe(
                duration, labels={"method": method, "path": norm_path, "status_code": str(status_code)}
            )
        except Exception:
            pass

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self.send_response(HTTPStatus.NO_CONTENT)
        self._set_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        """Route GET requests with M41 authentication and authorization and M44 telemetry."""
        req_start = time.time()
        self._setup_request_context()
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        if not path:
            path = "/"

        # Unauthenticated health probes and Prometheus metrics
        if path in ("/health", "/healthz", "/live", "/livez"):
            uptime = time.time() - self.start_time
            self._send_json_response({
                "status": "healthy",
                "live": True,
                "app_name": self.config.aura_app_name,
                "version": "0.28.0",
                "environment": self.config.aura_env,
                "uptime_seconds": round(uptime, 2),
                "timestamp": time.time(),
            })
            self._record_http_metric("GET", path, 200, req_start)
            return

        if path in ("/ready", "/readyz"):
            is_ready = self.aura is not None and self.aura.orchestrator is not None
            db_status = "ok"
            rep_container = getattr(self.server, "repository_container", None)
            if rep_container is not None and rep_container.db_pool is not None:
                db_health = rep_container.db_pool.check_health()
                if not db_health.get("connected", False):
                    is_ready = False
                    db_status = db_health.get("error", "database_unreachable")

            # In production or when DB URL is set, fail closed if DB is unreachable
            is_prod = self.config.aura_env.lower() == "production"
            if (is_prod or self.config.aura_database_url) and db_status != "ok":
                is_ready = False

            if is_ready:
                self._send_json_response({
                    "status": "ready",
                    "ready": True,
                    "model_provider": self.config.aura_model_provider or "default",
                    "agentic_enabled": self.aura.agentic_runtime is not None,
                    "database": db_status,
                    "timestamp": time.time(),
                })
                self._record_http_metric("GET", path, 200, req_start)
            else:
                self._send_error_response(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "not_ready",
                    f"AURA runtime is initializing or degraded (database: {db_status})",
                )
                self._record_http_metric("GET", path, 503, req_start)
            return

        if path == "/metrics":
            # Prometheus text exposition format (unauthenticated standard pull endpoint)
            metrics_text = get_metrics_registry().to_prometheus_text()
            body = metrics_text.encode("utf-8")
            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._set_cors_headers()
                self.end_headers()
                self.wfile.write(body)
                self._record_http_metric("GET", path, 200, req_start)
            except Exception as e:
                logger.error(f"Error serving metrics: {e}")
            return

        # Web UI and Static Assets
        if path in ("/", "/ui", "/index.html") or path.startswith("/static/"):
            self._serve_static(path)
            self._record_http_metric("GET", path, 200, req_start)
            return

        # Authenticated endpoints
        is_auth, identity, err_msg, status_code = self._resolve_identity()
        if not is_auth:
            self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
            self._record_http_metric("GET", path, status_code, req_start)
            return

        user_id = identity.user_id if identity else "default"

        if path == "/v1/telemetry":
            # Operational JSON telemetry summary (Admin-only)
            if not (identity and (identity.has_role(UserRole.ADMIN) or identity.has_scope(UserScope.ADMIN.value))):
                self._send_error_response(HTTPStatus.FORBIDDEN, "forbidden", "Administrative privileges required to view operational telemetry")
                self._record_http_metric("GET", path, 403, req_start)
                return
            health = self.aura.get_health_status()
            provider_health = self.aura.get_provider_health()
            metrics_dict = get_metrics_registry().to_dict()
            self._send_json_response({
                "system": health,
                "providers": provider_health,
                "metrics": metrics_dict,
                "timestamp": time.time(),
            })
            self._record_http_metric("GET", path, 200, req_start)
            return

        if path == "/v1/skills":
            skills = self.aura.list_dynamic_skills()
            skills_data = [
                {
                    "name": s.name,
                    "version": s.version,
                    "description": s.description,
                    "lifecycle_state": s.lifecycle_state.value,
                    "capabilities": list(s.required_capabilities),
                    "invocation_count": s.invocation_count,
                }
                for s in skills
            ]
            self._send_json_response({"skills": skills_data, "count": len(skills_data)})
            return

        if path == "/v1/campaigns":
            campaigns = self.aura.list_campaigns()
            self._send_json_response({"campaigns": campaigns, "count": len(campaigns)})
            return

        if path == "/v1/artifacts":
            artifacts = self.aura.list_artifacts()
            art_data = [
                {
                    "artifact_id": a.artifact_id,
                    "name": a.name,
                    "version": a.version,
                    "artifact_type": a.artifact_type.value if hasattr(a.artifact_type, "value") else str(a.artifact_type),
                    "size_bytes": a.size_bytes,
                    "content_hash": a.content_hash,
                    "is_tainted": a.is_tainted,
                }
                for a in artifacts
            ]
            self._send_json_response({"artifacts": art_data, "count": len(art_data)})
            return

        if path == "/v1/knowledge/query":
            params = parse_qs(parsed_url.query)
            capabilities = tuple(params.get("capability", []))
            domain = params.get("domain", [""])[0]
            if capabilities:
                skills = self.aura.recommend_skills(required_capabilities=capabilities)
                self._send_json_response({"recommended_skills": skills})
                return
            if domain:
                patterns = self.aura.find_proven_goal_patterns(goal_domain=domain)
                self._send_json_response({"patterns": patterns})
                return
            self._send_json_response({"message": "Provide ?capability=... or ?domain=... to query knowledge graph"})
            return

        if path == "/v1/preferences":
            prefs = self.aura.get_user_preferences(user_id=user_id)
            self._send_json_response(prefs.to_dict() if hasattr(prefs, "to_dict") else prefs)
            return

        if path == "/v1/tools":
            tools = self.aura.list_ecosystem_tools()
            self._send_json_response({
                "tools": [t.to_dict() if hasattr(t, "to_dict") else t.__dict__ for t in tools],
                "count": len(tools),
            })
            return

        if path == "/v1/proactive/proposals":
            proposals = self.aura.evaluate_proactive_triggers()
            self._send_json_response({
                "proposals": [p.to_dict() if hasattr(p, "to_dict") else p.__dict__ for p in proposals],
                "count": len(proposals),
            })
            return

        if path == "/v1/learning/report":
            report = self.aura.get_learning_report()
            self._send_json_response(report.to_dict() if hasattr(report, "to_dict") else report)
            return

        if path == "/v1/devices":
            devices = self.aura.list_devices()
            self._send_json_response({
                "devices": [d.to_dict() if hasattr(d, "to_dict") else d.__dict__ for d in devices],
                "count": len(devices),
            })
            return

        if path == "/v1/sync":
            status_report = self.aura.get_cross_device_sync_status()
            self._send_json_response(status_report.to_dict() if hasattr(status_report, "to_dict") else status_report)
            return

        if path == "/v1/release/validation":
            if identity and not (identity.has_role(UserRole.ADMIN) or identity.has_scope(UserScope.ADMIN.value)):
                self._send_error_response(HTTPStatus.FORBIDDEN, "forbidden", "Administrative privileges required")
                return
            validation_report = self.aura.validate_release(self.config)
            self._send_json_response(validation_report.to_dict() if hasattr(validation_report, "to_dict") else validation_report)
            return

        # M52: Task Event Stream (SSE)
        if path.startswith("/v1/tasks/") and path.endswith("/events"):
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            task_id = path[len("/v1/tasks/"):-len("/events")].strip()
            if not self.task_repo or not self.broadcaster:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Task system not initialized")
                return
            task = self.task_repo.get_task(task_id, user_id=user_id)
            if not task:
                self._send_error_response(HTTPStatus.NOT_FOUND, "task_not_found", f"Task '{task_id}' not found")
                return

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._set_cors_headers()
            self.end_headers()

            init_evt = self.broadcaster.format_sse("task_status", {
                "task_id": task["id"],
                "status": task["status"],
                "title": task["title"],
            })
            try:
                self.wfile.write(init_evt.encode("utf-8"))
                self.wfile.flush()
            except Exception:
                return

            if task["status"] in ("completed", "failed", "cancelled", "timed_out"):
                term_evt = self.broadcaster.format_sse(f"task_{task['status']}", task)
                try:
                    self.wfile.write(term_evt.encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    pass
                return

            event_queue = self.broadcaster.subscribe(task_id)
            ping_interval = getattr(self.config, "aura_task_sse_ping_interval_seconds", 15)
            try:
                while True:
                    try:
                        msg = event_queue.get(timeout=ping_interval)
                        evt_str = self.broadcaster.format_sse(msg["event"], msg["data"])
                        self.wfile.write(evt_str.encode("utf-8"))
                        self.wfile.flush()
                        if msg["event"] in ("task_completed", "task_failed", "task_cancelled", "task_timed_out"):
                            break
                    except queue.Empty:
                        ping_str = self.broadcaster.format_ping()
                        self.wfile.write(ping_str.encode("utf-8"))
                        self.wfile.flush()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError, Exception):
                logger.debug(f"SSE client disconnected for task '{task_id}'")
            finally:
                self.close_connection = True
                self.broadcaster.unsubscribe(task_id, event_queue)
            return

        # M52: Task Status & Details
        if path.startswith("/v1/tasks/"):
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            task_id = path[len("/v1/tasks/"):].strip()
            if not self.task_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Task repository unavailable")
                return
            task = self.task_repo.get_task(task_id, user_id=user_id)
            if not task:
                self._send_error_response(HTTPStatus.NOT_FOUND, "task_not_found", f"Task '{task_id}' not found")
                return
            steps = self.task_repo.get_steps(task_id, user_id=user_id)
            task["steps"] = steps
            self._send_json_response(task)
            return

        # M52: List Pending Approvals
        if path == "/v1/approvals":
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            if not self.approval_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Approval repository unavailable")
                return
            approvals = self.approval_repo.list_pending_approvals(user_id=user_id)
            self._send_json_response({"approvals": approvals, "count": len(approvals)})
            return

        # M52: Get Approval Detail
        if path.startswith("/v1/approvals/"):
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            approval_id = path[len("/v1/approvals/"):].strip()
            if not self.approval_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Approval repository unavailable")
                return
            approval = self.approval_repo.get_approval(approval_id, user_id=user_id)
            if not approval:
                self._send_error_response(HTTPStatus.NOT_FOUND, "approval_not_found", f"Approval '{approval_id}' not found")
                return
            self._send_json_response(approval)
            return

        
        # M53: List Automations
        if path == "/v1/automations":
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            if not self.automation_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Automation repository unavailable")
                return
            params = parse_qs(parsed_url.query)
            st_filter = params.get("status", [None])[0]
            limit = int(params.get("limit", [50])[0])
            offset = int(params.get("offset", [0])[0])
            autos = self.automation_repo.list_automations(user_id=user_id, status=st_filter, limit=limit, offset=offset)
            total = self.automation_repo.count_automations(user_id=user_id, status=st_filter)
            self._send_json_response({"automations": autos, "count": len(autos), "total": total})
            return

        # M53: Automation Runs
        if path.startswith("/v1/automations/") and path.endswith("/runs"):
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            auto_id = path[len("/v1/automations/"):-len("/runs")].strip()
            if not self.automation_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Automation repository unavailable")
                return
            auto = self.automation_repo.get_automation(auto_id, user_id=user_id)
            if not auto:
                self._send_error_response(HTTPStatus.NOT_FOUND, "automation_not_found", "Automation not found")
                return
            params = parse_qs(parsed_url.query)
            limit = int(params.get("limit", [50])[0])
            offset = int(params.get("offset", [0])[0])
            runs = self.automation_repo.list_runs(automation_id=auto_id, user_id=user_id, limit=limit, offset=offset)
            self._send_json_response({"runs": runs, "count": len(runs)})
            return

        # M53: Automation Detail
        if path.startswith("/v1/automations/"):
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            auto_id = path[len("/v1/automations/"):].strip()
            if not self.automation_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Automation repository unavailable")
                return
            auto = self.automation_repo.get_automation(auto_id, user_id=user_id)
            if not auto:
                self._send_error_response(HTTPStatus.NOT_FOUND, "automation_not_found", "Automation not found")
                return
            self._send_json_response(auto)
            return

        # ============================================================
        # M54: Enterprise Webhooks & Event Gateway (GET Routes)
        # ============================================================

        # M54: List Webhook Endpoints
        if path == "/v1/webhooks/endpoints":
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            params = parse_qs(parsed_url.query)
            limit = int(params.get("limit", [50])[0])
            offset = int(params.get("offset", [0])[0])
            endpoints = self.webhook_repo.list_endpoints(tenant_id=user_id, limit=limit, offset=offset)
            self._send_json_response({"endpoints": [ep.to_dict() if hasattr(ep, "to_dict") else ep for ep in endpoints], "count": len(endpoints)})
            return

        # M54: Get Webhook Endpoint Detail
        if path.startswith("/v1/webhooks/endpoints/"):
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            ep_id = path[len("/v1/webhooks/endpoints/"):].strip()
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            ep = self.webhook_repo.get_endpoint(ep_id, tenant_id=user_id)
            if not ep:
                self._send_error_response(HTTPStatus.NOT_FOUND, "endpoint_not_found", f"Webhook endpoint '{ep_id}' not found")
                return
            self._send_json_response(ep.to_dict() if hasattr(ep, "to_dict") else ep)
            return

        # M54: List Event Subscriptions
        if path == "/v1/events/subscriptions":
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            params = parse_qs(parsed_url.query)
            limit = int(params.get("limit", [50])[0])
            offset = int(params.get("offset", [0])[0])
            event_type = params.get("event_type", [None])[0]
            subs = self.webhook_repo.list_subscriptions(tenant_id=user_id, event_type=event_type, limit=limit, offset=offset)
            self._send_json_response({"subscriptions": [s.to_dict() if hasattr(s, "to_dict") else s for s in subs], "count": len(subs)})
            return

        # M54: Get Event Subscription Detail
        if path.startswith("/v1/events/subscriptions/"):
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            sub_id = path[len("/v1/events/subscriptions/"):].strip()
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            sub = self.webhook_repo.get_subscription(sub_id, tenant_id=user_id)
            if not sub:
                self._send_error_response(HTTPStatus.NOT_FOUND, "subscription_not_found", f"Subscription '{sub_id}' not found")
                return
            self._send_json_response(sub.to_dict() if hasattr(sub, "to_dict") else sub)
            return

        # M54: List Event Deliveries
        if path == "/v1/events/deliveries":
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            params = parse_qs(parsed_url.query)
            limit = int(params.get("limit", [50])[0])
            offset = int(params.get("offset", [0])[0])
            subscription_id = params.get("subscription_id", [None])[0]
            status_filter = params.get("status", [None])[0]
            dels = self.webhook_repo.list_deliveries(tenant_id=user_id, subscription_id=subscription_id, status=status_filter, limit=limit, offset=offset)
            self._send_json_response({"deliveries": [d.to_dict() if hasattr(d, "to_dict") else d for d in dels], "count": len(dels)})
            return

        # M54: Get Event Delivery Detail
        if path.startswith("/v1/events/deliveries/"):
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            del_id = path[len("/v1/events/deliveries/"):].strip()
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            delivery = self.webhook_repo.get_delivery(del_id, tenant_id=user_id)
            if not delivery:
                self._send_error_response(HTTPStatus.NOT_FOUND, "delivery_not_found", f"Delivery '{del_id}' not found")
                return
            self._send_json_response(delivery.to_dict() if hasattr(delivery, "to_dict") else delivery)
            return

        # M54: List Inbound Events
        if path == "/v1/events/inbound":
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            params = parse_qs(parsed_url.query)
            limit = int(params.get("limit", [50])[0])
            offset = int(params.get("offset", [0])[0])
            endpoint_id = params.get("endpoint_id", [None])[0]
            status_filter = params.get("status", [None])[0]
            inbounds = self.webhook_repo.list_inbound_events(tenant_id=user_id, endpoint_id=endpoint_id, status=status_filter, limit=limit, offset=offset)
            self._send_json_response({"inbound_events": [ev.to_dict() if hasattr(ev, "to_dict") else ev for ev in inbounds], "count": len(inbounds)})
            return

        # M54: Get Inbound Event Detail
        if path.startswith("/v1/events/inbound/"):
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            ev_id = path[len("/v1/events/inbound/"):].strip()
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            ev = self.webhook_repo.get_inbound_event(ev_id, tenant_id=user_id)
            if not ev:
                self._send_error_response(HTTPStatus.NOT_FOUND, "event_not_found", f"Inbound event '{ev_id}' not found")
                return
            self._send_json_response(ev.to_dict() if hasattr(ev, "to_dict") else ev)
            return

        # M54: List Dead-Letter Replays for a specific dead letter
        if path.startswith("/v1/events/dead-letter/") and path.endswith("/replays"):
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            dl_id = path[len("/v1/events/dead-letter/"):-len("/replays")].strip()
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            replays = self.webhook_repo.list_dead_letter_replays(dead_letter_id=dl_id, tenant_id=user_id)
            self._send_json_response({"replays": [r.to_dict() if hasattr(r, "to_dict") else r for r in replays], "count": len(replays)})
            return

        # M54: List Dead-Letter Events
        if path == "/v1/events/dead-letter":
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            params = parse_qs(parsed_url.query)
            limit = int(params.get("limit", [50])[0])
            offset = int(params.get("offset", [0])[0])
            dead_letter_type = params.get("type", [None])[0]
            dls = self.webhook_repo.list_dead_letters(tenant_id=user_id, dead_letter_type=dead_letter_type, limit=limit, offset=offset)
            self._send_json_response({"dead_letter_events": [dl.to_dict() if hasattr(dl, "to_dict") else dl for dl in dls], "count": len(dls)})
            return

        # M54: Get Dead-Letter Event Detail
        if path.startswith("/v1/events/dead-letter/"):
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            dl_id = path[len("/v1/events/dead-letter/"):].strip()
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            dl = self.webhook_repo.get_dead_letter(dl_id, tenant_id=user_id)
            if not dl:
                self._send_error_response(HTTPStatus.NOT_FOUND, "dead_letter_not_found", f"Dead-letter event '{dl_id}' not found")
                return
            self._send_json_response(dl.to_dict() if hasattr(dl, "to_dict") else dl)
            return


        # ============================================================
        # M55: Distributed Worker Fleet & Coordination (GET Routes)
        # ============================================================

        if path == "/v1/fleet/status":
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            if not (identity and (identity.has_role(UserRole.ADMIN) or identity.has_scope(UserScope.ADMIN.value))):
                self._send_error_response(HTTPStatus.FORBIDDEN, "forbidden", "Administrative privileges required to view fleet status")
                return
            if not self.fleet_coordinator:
                self._send_error_response(HTTPStatus.NOT_IMPLEMENTED, "fleet_disabled", "Fleet coordinator is not active")
                return
            self._send_json_response(self.fleet_coordinator.get_fleet_status())
            return

        if path == "/v1/fleet/workers":
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            if not (identity and (identity.has_role(UserRole.ADMIN) or identity.has_scope(UserScope.ADMIN.value))):
                self._send_error_response(HTTPStatus.FORBIDDEN, "forbidden", "Administrative privileges required to list workers")
                return
            if not self.fleet_repo:
                self._send_error_response(HTTPStatus.NOT_IMPLEMENTED, "fleet_disabled", "Fleet repository is not active")
                return
            workers = self.fleet_repo.list_workers()
            self._send_json_response({"workers": [w.to_dict() for w in workers], "count": len(workers)})
            return

        if path.startswith("/v1/fleet/tenants/") and path.endswith("/quota"):
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            tenant_id = path[len("/v1/fleet/tenants/"): -len("/quota")].strip()
            # Allow user to view their own quota, or admin to view any
            is_admin = identity and (identity.has_role(UserRole.ADMIN) or identity.has_scope(UserScope.ADMIN.value))
            if not is_admin and identity and identity.user_id != tenant_id:
                self._send_error_response(HTTPStatus.FORBIDDEN, "forbidden", "Cannot view quota for other tenants")
                return
            if not self.fleet_repo:
                self._send_error_response(HTTPStatus.NOT_IMPLEMENTED, "fleet_disabled", "Fleet repository is not active")
                return
            limits = self.fleet_repo.get_tenant_limits(tenant_id)
            self._send_json_response(limits.to_dict())
            return

        # ============================================================
        # M56: Cognitive Memory & Continuous Learning (GET Routes)
        # ============================================================

        if path == "/v1/cognitive-memory/query":
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            if not self.cognitive_memory_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Cognitive memory repository unavailable")
                return
            params = parse_qs(parsed_url.query)
            m_type = params.get("memory_type", [None])[0]
            category = params.get("category", [None])[0]
            key = params.get("key", [None])[0]
            lifecycle_state = params.get("lifecycle_state", ["active"])[0]
            min_conf = float(params.get("min_confidence", [0.0])[0])
            query_str = params.get("query", [""])[0]
            limit = int(params.get("limit", [50])[0])
            offset = int(params.get("offset", [0])[0])

            mems = self.cognitive_memory_repo.query_memories(
                tenant_id=user_id,
                memory_type=m_type,
                category=category,
                key=key,
                lifecycle_state=lifecycle_state,
                min_confidence=min_conf,
                query=query_str,
                limit=limit,
                offset=offset,
            )
            self._send_json_response({"memories": [m.to_dict() for m in mems], "count": len(mems)})
            return

        if path == "/v1/cognitive-memory/profile":
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            if not self.cognitive_memory_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Cognitive memory repository unavailable")
                return
            prof = self.cognitive_memory_repo.get_profile(tenant_id=user_id)
            if not prof:
                prof = UserCognitiveProfile(profile_id=str(uuid4()), tenant_id=user_id)
            self._send_json_response(prof.to_dict())
            return

        if path == "/v1/cognitive-memory/contradictions":
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            if not self.cognitive_memory_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Cognitive memory repository unavailable")
                return
            params = parse_qs(parsed_url.query)
            status_param = params.get("status", [None])[0]
            limit = int(params.get("limit", [50])[0])
            contras = self.cognitive_memory_repo.list_contradictions(tenant_id=user_id, status=status_param, limit=limit)
            self._send_json_response({"contradictions": [c.to_dict() for c in contras], "count": len(contras)})
            return

        if path == "/v1/cognitive-memory/experience-patterns":
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            if not self.cognitive_memory_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Cognitive memory repository unavailable")
                return
            params = parse_qs(parsed_url.query)
            limit = int(params.get("limit", [50])[0])
            patterns = self.cognitive_memory_repo.list_experience_patterns(tenant_id=user_id, limit=limit)
            self._send_json_response({"experience_patterns": [p.to_dict() for p in patterns], "count": len(patterns)})
            return

        if path == "/v1/cognitive-memory/context":
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            if not self.cognitive_memory_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Cognitive memory repository unavailable")
                return
            params = parse_qs(parsed_url.query)
            q_text = params.get("query", [""])[0]
            prof = self.cognitive_memory_repo.get_profile(tenant_id=user_id)
            active_mems = self.cognitive_memory_repo.query_memories(tenant_id=user_id, lifecycle_state="active", limit=20)
            patterns = self.cognitive_memory_repo.list_experience_patterns(tenant_id=user_id, limit=10)
            ctx = self.personalization_engine.build_personalization_context(
                tenant_id=user_id,
                profile=prof,
                memories=active_mems,
                experience_patterns=patterns,
                query=q_text,
            )
            self._send_json_response({
                "context": ctx.formatted_prompt_block,
                "explicit_preferences": ctx.explicit_preferences,
                "inferred_traits": ctx.inferred_traits,
                "relevant_facts": ctx.relevant_facts,
                "recommended_tools": ctx.recommended_tools,
            })
            return

        if path.startswith("/v1/cognitive-memory/"):
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            m_id = path[len("/v1/cognitive-memory/"):].strip()
            if not self.cognitive_memory_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Cognitive memory repository unavailable")
                return
            mem = self.cognitive_memory_repo.get_memory(m_id, tenant_id=user_id)
            if not mem:
                self._send_error_response(HTTPStatus.NOT_FOUND, "memory_not_found", f"Cognitive memory '{m_id}' not found")
                return
            self._send_json_response(mem.to_dict())
            return

        # M57: List Multimodal Capabilities
        if path == "/v1/multimodal/capabilities":
            registry = MultimodalCapabilityRegistry()
            caps = registry.list_capabilities()
            self._send_json_response({"capabilities": [c.to_dict() for c in caps]})
            return

        # M57: List Multimodal Artifacts
        if path == "/v1/multimodal/artifacts":
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            if not self.multimodal_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Multimodal repository unavailable")
                return
            params = parse_qs(parsed_url.query)
            state_filter = params.get("lifecycle_state", [None])[0]
            limit = int(params.get("limit", [50])[0])
            offset = int(params.get("offset", [0])[0])
            artifacts = self.multimodal_repo.list_artifacts(tenant_id=user_id, lifecycle_state=state_filter, limit=limit, offset=offset)
            self._send_json_response({"artifacts": [a.to_dict() for a in artifacts], "count": len(artifacts)})
            return

        # M57: Get Single Multimodal Artifact
        if path.startswith("/v1/multimodal/artifacts/"):
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            art_id = path[len("/v1/multimodal/artifacts/"):].strip()
            if not self.multimodal_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Multimodal repository unavailable")
                return
            art = self.multimodal_repo.get_artifact(art_id, tenant_id=user_id)
            if not art:
                self._send_error_response(HTTPStatus.NOT_FOUND, "artifact_not_found", f"Artifact '{art_id}' not found")
                return
            self._send_json_response(art.to_dict())
            return

        # M57: List Multimodal Results
        if path == "/v1/multimodal/results":
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            user_id = identity.user_id if identity else "default"
            params = parse_qs(parsed_url.query)
            art_id = params.get("artifact_id", [""])[0]
            if not art_id:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "missing_artifact_id", "artifact_id query parameter is required")
                return
            if not self.multimodal_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Multimodal repository unavailable")
                return
            results = self.multimodal_repo.list_results(art_id, tenant_id=user_id)
            self._send_json_response({"results": [r.to_dict() for r in results], "count": len(results)})
            return


        self._send_error_response(HTTPStatus.NOT_FOUND, "not_found", f"Path '{path}' not found")

    def do_POST(self) -> None:
        """Route POST requests with M41 authentication, user isolation, and M44 correlation."""
        req_start = time.time()
        self._setup_request_context()
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")

        # M54: Public Webhook Ingress (POST /v1/webhooks/{endpoint_id})
        # Note: Must bypass AURA user Bearer token auth; validated via endpoint HMAC / signatures
        if path.startswith("/v1/webhooks/") and not path.startswith("/v1/webhooks/endpoints"):
            ep_id = path[len("/v1/webhooks/"):].strip()
            if not ep_id:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_endpoint_id", "Endpoint ID required")
                return
            if not self.webhook_repo or not self.ingress_service:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook ingress service unavailable")
                return

            raw_bytes = self._read_raw_body()
            if raw_bytes is None:
                return

            params = {k: (v[0] if len(v) == 1 else v) for k, v in parse_qs(parsed_url.query).items()}
            headers_dict = dict(self.headers)
            client_ip = self.client_address[0] if self.client_address else "127.0.0.1"

            try:
                resp = self.ingress_service.handle_inbound_request(
                    endpoint_id=ep_id,
                    payload_bytes=raw_bytes,
                    headers=headers_dict,
                    query_params=params,
                    client_ip=client_ip,
                )
                self._send_json_response(resp.to_dict(), status=HTTPStatus(resp.status_code))
            except Exception as e:
                logger.error(f"Error handling webhook ingress: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "ingress_error", str(e))
            return

        # Check authentication for all authenticated endpoints
        is_auth, identity, err_msg, status_code = self._resolve_identity()
        if not is_auth:
            self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
            return

        body = self._read_json_body()
        if body is None:
            return

        user_id = identity.user_id if identity else "default"

        if path == "/v1/run":
            try:
                parsed_req = RunRequestSchema.model_validate(body)
                user_input = parsed_req.get_prompt_text()
            except (ValidationError, ValueError) as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            metadata = parsed_req.metadata
            import uuid
            try:
                eff_uuid = UUID(self.request_id)
            except Exception:
                eff_uuid = uuid.uuid4()
            req = AURARequest(request_id=eff_uuid, user_input=user_input, metadata=metadata, identity=identity, user_id=user_id)
            try:
                response = self.aura.run_request(req)
                self._send_json_response({
                    "request_id": response.request_id,
                    "content": response.content,
                    "metadata": response.metadata,
                    "timestamp": time.time(),
                })
            except Exception as e:
                logger.error(f"Error executing run request: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "execution_error", str(e))
            return

        if path == "/v1/task":
            try:
                task_schema = TaskRequestSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            try:
                result = self.aura.run_task(task=task_schema.task, task_id=task_schema.task_id, timeout=task_schema.timeout)
                self._send_json_response({
                    "task": task_schema.task,
                    "task_id": task_schema.task_id,
                    "result": str(result),
                    "status": "completed",
                    "timestamp": time.time(),
                })
            except Exception as e:
                logger.error(f"Error executing task: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "task_execution_error", str(e))
            return

        if path == "/v1/preferences":
            try:
                PreferencesUpdateRequestSchema.model_validate(body)
                updated = self.aura.update_user_preferences(body, user_id=user_id)
                self._send_json_response(updated.to_dict() if hasattr(updated, "to_dict") else updated)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
            except Exception as e:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "preference_update_error", str(e))
            return

        if path == "/v1/rag":
            try:
                rag_schema = RAGQueryRequestSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            try:
                bundle = self.aura.retrieve_rag_context(query=rag_schema.query, max_chars=rag_schema.max_chars, user_id=user_id)
                self._send_json_response(bundle.to_dict() if hasattr(bundle, "to_dict") else bundle)
            except Exception as e:
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "rag_error", str(e))
            return

        if path == "/v1/plan":
            try:
                plan_schema = PlanRequestSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            try:
                plan = self.aura.create_structured_plan(goal=plan_schema.goal)
                self._send_json_response(plan.to_dict() if hasattr(plan, "to_dict") else plan)
            except Exception as e:
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "planning_error", str(e))
            return

        if path == "/v1/tools/execute":
            try:
                tool_schema = ToolExecutionRequestSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            try:
                result = self.aura.execute_ecosystem_tool(tool_name=tool_schema.tool_name, parameters=tool_schema.parameters, caller=tool_schema.caller)
                self._send_json_response(result.to_dict() if hasattr(result, "to_dict") else result)
            except Exception as e:
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "tool_execution_error", str(e))
            return

        if path == "/v1/proactive/approve":
            try:
                act_schema = ProactiveActionRequestSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            try:
                prop = self.aura.approve_proactive_proposal(act_schema.proposal_id)
                self._send_json_response(prop.to_dict() if hasattr(prop, "to_dict") else prop)
            except Exception as e:
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "approval_error", str(e))
            return

        if path == "/v1/proactive/reject":
            try:
                act_schema = ProactiveActionRequestSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            try:
                prop = self.aura.reject_proactive_proposal(act_schema.proposal_id, reason=act_schema.reason or "")
                self._send_json_response(prop.to_dict() if hasattr(prop, "to_dict") else prop)
            except Exception as e:
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "rejection_error", str(e))
            return

        if path == "/v1/devices/action":
            if identity and not (identity.has_role(UserRole.ADMIN) or identity.has_role(UserRole.OPERATOR) or identity.has_scope(UserScope.DEVICE_EXEC.value)):
                self._send_error_response(HTTPStatus.FORBIDDEN, "forbidden", "Insufficient permission for device execution")
                return

            try:
                dev_schema = DeviceActionRequestSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            try:
                from core.device_integration_types import DeviceCapability
                cap_enum = DeviceCapability(dev_schema.capability) if isinstance(dev_schema.capability, str) else dev_schema.capability
                act_res = self.aura.execute_device_action(device_id=dev_schema.device_id, capability=cap_enum, parameters=dev_schema.parameters)
                self._send_json_response(act_res.to_dict() if hasattr(act_res, "to_dict") else act_res)
            except Exception as e:
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "device_action_error", str(e))
            return

        if path == "/v1/sync":
            try:
                count = self.aura.sync_cross_device_state()
                self._send_json_response({"status": "synced", "synced_deltas_count": count})
            except Exception as e:
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "sync_error", str(e))
            return

        if path == "/v1/cycle":
            try:
                cycle_schema = CycleRequestSchema.model_validate(body)
                user_input = cycle_schema.get_input_text()
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            try:
                cycle_res = self.aura.execute_integrated_cycle(user_input=user_input, task_id=cycle_schema.task_id, auto_sync=cycle_schema.auto_sync)
                self._send_json_response(cycle_res.to_dict() if hasattr(cycle_res, "to_dict") else cycle_res)
            except Exception as e:
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "cycle_execution_error", str(e))
            return

        # M52: Submit Asynchronous Task
        if path == "/v1/tasks":
            try:
                task_sub = AsyncTaskSubmissionSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            if not self.task_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Task repository unavailable")
                return

            idempotency_key = self.headers.get("Idempotency-Key")
            timeout_sec = task_sub.timeout_seconds or getattr(self.config, "aura_task_default_timeout_seconds", 600)

            try:
                task = self.task_repo.create_task(
                    user_id=user_id,
                    title=task_sub.title,
                    goal=task_sub.goal,
                    context=task_sub.context,
                    idempotency_key=idempotency_key,
                    timeout_seconds=timeout_sec,
                )
                if self.broadcaster:
                    self.broadcaster.publish(task["id"], "task_enqueued", {
                        "task_id": task["id"],
                        "title": task["title"],
                        "status": task["status"],
                    })

                self._send_json_response(
                    {
                        "task_id": task["id"],
                        "status": task["status"],
                        "title": task["title"],
                        "created_at": task["created_at"],
                        "events_url": f"/v1/tasks/{task['id']}/events",
                    },
                    status=HTTPStatus.ACCEPTED,
                )
            except Exception as e:
                logger.error(f"Error submitting async task: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "task_submission_error", str(e))
            return

        # M52: Cancel Asynchronous Task
        if path.startswith("/v1/tasks/") and path.endswith("/cancel"):
            task_id = path[len("/v1/tasks/"):-len("/cancel")].strip()
            if not self.task_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Task repository unavailable")
                return

            task = self.task_repo.get_task(task_id, user_id=user_id)
            if not task:
                self._send_error_response(HTTPStatus.NOT_FOUND, "task_not_found", f"Task '{task_id}' not found")
                return

            cancelled = False
            if self.task_worker:
                cancelled = self.task_worker.cancel_task(task_id, user_id=user_id)
            else:
                cancelled = self.task_repo.cancel_task(task_id, user_id=user_id)

            if self.broadcaster:
                self.broadcaster.publish(task_id, "task_cancelled", {"task_id": task_id})

            self._send_json_response({
                "task_id": task_id,
                "status": "cancelled",
                "cancelled": cancelled,
                "message": "Cancellation signal processed",
            })
            return

        # M52: Decide Approval Request
        if path.startswith("/v1/approvals/") and path.endswith("/decide"):
            approval_id = path[len("/v1/approvals/"):-len("/decide")].strip()
            try:
                dec_schema = ApprovalDecisionRequestSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            if not self.approval_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Approval repository unavailable")
                return

            success, msg, updated_appr = self.approval_repo.decide_approval(
                approval_id=approval_id,
                user_id=user_id,
                decision=dec_schema.decision,
                nonce=dec_schema.nonce,
                reason=dec_schema.reason or "",
            )

            if not success:
                if "not found" in msg.lower():
                    self._send_error_response(HTTPStatus.NOT_FOUND, "approval_not_found", msg)
                elif "expired" in msg.lower():
                    self._send_error_response(HTTPStatus.GONE, "approval_expired", msg)
                elif "nonce" in msg.lower():
                    self._send_error_response(HTTPStatus.FORBIDDEN, "invalid_nonce", msg)
                else:
                    self._send_error_response(HTTPStatus.BAD_REQUEST, "decision_failed", msg)
                return

            get_security_audit_logger().record_event(
                event_type=SecurityEventType.AUTH_SUCCESS,
                outcome="success",
                user_id=user_id,
                reason=f"Approval {dec_schema.decision}: {dec_schema.reason}",
                metadata={"approval_id": approval_id, "decision": dec_schema.decision},
            )

            try:
                get_metrics_registry().get_counter("aura_approvals_total").inc(labels={"decision": dec_schema.decision})
            except Exception:
                pass

            task_id = updated_appr.get("task_id") if updated_appr else None
            if self.broadcaster and task_id:
                self.broadcaster.publish(task_id, "approval_decided", {
                    "task_id": task_id,
                    "approval_id": approval_id,
                    "decision": dec_schema.decision,
                })

            self._send_json_response({
                "approval_id": approval_id,
                "status": dec_schema.decision,
                "task_status": "pending" if dec_schema.decision == "approved" else "failed",
                "resumed": (dec_schema.decision == "approved"),
                "reason": dec_schema.reason,
            })
            return

        # M53: Create Proactive Automation
        if path == "/v1/automations":
            if not self.automation_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Automation repository unavailable")
                return
            try:
                auto_schema = AutomationCreateSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            trigger_cfg = auto_schema.trigger_config or {}
            next_fire = None
            if auto_schema.trigger_type in ("recurring", "cron") or "cron" in trigger_cfg:
                cron_expr = trigger_cfg.get("cron")
                if cron_expr:
                    try:
                        validate_cron(cron_expr)
                        next_fire = calculate_next_fire(cron_expr)
                    except Exception as e:
                        self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_cron", str(e))
                        return

            try:
                created = self.automation_repo.create_automation(
                    user_id=user_id,
                    name=auto_schema.name,
                    trigger_type=auto_schema.trigger_type,
                    trigger_config=trigger_cfg,
                    condition_config=auto_schema.condition_config or {},
                    action_template=auto_schema.action_template or {},
                    description=auto_schema.description or "",
                    max_runs=auto_schema.max_runs,
                    cooldown_seconds=auto_schema.cooldown_seconds,
                    metadata=auto_schema.metadata or {},
                    next_fire_at=next_fire,
                )
                self._send_json_response(created)
            except Exception as e:
                logger.error(f"Error creating automation: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "automation_creation_error", str(e))
            return

        # M53: Pause Automation
        if path.startswith("/v1/automations/") and path.endswith("/pause"):
            auto_id = path[len("/v1/automations/"):-len("/pause")].strip()
            if not self.automation_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Automation repository unavailable")
                return
            paused = self.automation_repo.pause_automation(auto_id, user_id=user_id)
            if not paused:
                self._send_error_response(HTTPStatus.NOT_FOUND, "automation_not_found", f"Automation '{auto_id}' not found")
                return
            self._send_json_response(paused)
            return

        # M53: Resume Automation
        if path.startswith("/v1/automations/") and path.endswith("/resume"):
            auto_id = path[len("/v1/automations/"):-len("/resume")].strip()
            if not self.automation_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Automation repository unavailable")
                return
            existing = self.automation_repo.get_automation(auto_id, user_id=user_id)
            if not existing:
                self._send_error_response(HTTPStatus.NOT_FOUND, "automation_not_found", f"Automation '{auto_id}' not found")
                return
            next_fire = None
            if existing.get("trigger_config") and "cron" in existing["trigger_config"]:
                try:
                    next_fire = calculate_next_fire(existing["trigger_config"]["cron"])
                except Exception:
                    pass
            resumed = self.automation_repo.resume_automation(auto_id, user_id=user_id, next_fire_at=next_fire)
            if not resumed:
                self._send_error_response(HTTPStatus.NOT_FOUND, "automation_not_found", f"Automation '{auto_id}' not found")
                return
            self._send_json_response(resumed)
            return

        # M53: Trigger Automation Manually
        if path.startswith("/v1/automations/") and path.endswith("/trigger"):
            auto_id = path[len("/v1/automations/"):-len("/trigger")].strip()
            if not self.automation_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Automation repository unavailable")
                return
            existing = self.automation_repo.get_automation(auto_id, user_id=user_id)
            if not existing:
                self._send_error_response(HTTPStatus.NOT_FOUND, "automation_not_found", f"Automation '{auto_id}' not found")
                return
            # Schedule immediate fire
            updated = self.automation_repo.update_automation(auto_id, user_id=user_id, next_fire_at=time.time())
            self._send_json_response({"id": auto_id, "status": "triggered", "automation": updated})
            return

        # ============================================================
        # M54: Enterprise Webhooks & Event Gateway (POST Routes)
        # ============================================================

        # M54: Create Webhook Endpoint
        if path == "/v1/webhooks/endpoints":
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            try:
                schema = WebhookEndpointCreateSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return
            try:
                master_key = getattr(self.config, "aura_webhook_master_key", "aura-default-master-key-32bytes!!")
                created = self.webhook_repo.create_endpoint(
                    tenant_id=user_id,
                    name=schema.name,
                    description=schema.description,
                    raw_secret=schema.secret,
                    is_active=schema.is_active,
                    allowed_events=schema.allowed_events,
                    rate_limit_per_minute=schema.rate_limit_per_minute,
                    metadata=schema.metadata,
                    master_key=master_key,
                )
                self._send_json_response(created.to_dict() if hasattr(created, "to_dict") else created, status=HTTPStatus.CREATED)
            except Exception as e:
                logger.error(f"Error creating webhook endpoint: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "endpoint_creation_error", str(e))
            return

        # M54: Rotate Endpoint Secret
        if path.startswith("/v1/webhooks/endpoints/") and path.endswith("/rotate-secret"):
            ep_id = path[len("/v1/webhooks/endpoints/"):-len("/rotate-secret")].strip()
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            try:
                master_key = getattr(self.config, "aura_webhook_master_key", "aura-default-master-key-32bytes!!")
                raw_secret = body.get("new_secret") if isinstance(body, dict) else None
                new_key = self.webhook_repo.rotate_signing_key(
                    endpoint_id=ep_id,
                    tenant_id=user_id,
                    new_raw_secret=raw_secret,
                    master_key=master_key,
                )
                self._send_json_response(new_key.to_dict() if hasattr(new_key, "to_dict") else new_key)
            except Exception as e:
                logger.error(f"Error rotating webhook signing key: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "key_rotation_error", str(e))
            return

        # M54: Create Event Subscription
        if path == "/v1/events/subscriptions":
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            try:
                schema = EventSubscriptionCreateSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return
            try:
                master_key = getattr(self.config, "aura_webhook_master_key", "aura-default-master-key-32bytes!!")
                created = self.webhook_repo.create_subscription(
                    tenant_id=user_id,
                    event_type=schema.event_type,
                    target_url=schema.target_url,
                    raw_secret=schema.secret,
                    max_retries=schema.max_retries,
                    backoff_initial_seconds=schema.backoff_initial_seconds,
                    backoff_max_seconds=schema.backoff_max_seconds,
                    timeout_seconds=schema.timeout_seconds,
                    custom_headers=schema.custom_headers,
                    is_active=schema.is_active,
                    metadata=schema.metadata,
                    master_key=master_key,
                )
                self._send_json_response(created.to_dict() if hasattr(created, "to_dict") else created, status=HTTPStatus.CREATED)
            except Exception as e:
                logger.error(f"Error creating subscription: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "subscription_creation_error", str(e))
            return

        # M54: Redeliver Event Delivery
        if path.startswith("/v1/events/deliveries/") and path.endswith("/redeliver"):
            del_id = path[len("/v1/events/deliveries/"):-len("/redeliver")].strip()
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            try:
                redelivered = self.webhook_repo.redeliver_event(delivery_id=del_id, tenant_id=user_id)
                if not redelivered:
                    self._send_error_response(HTTPStatus.NOT_FOUND, "delivery_not_found", f"Delivery '{del_id}' not found")
                    return
                self._send_json_response(redelivered.to_dict() if hasattr(redelivered, "to_dict") else redelivered, status=HTTPStatus.ACCEPTED)
            except Exception as e:
                logger.error(f"Error redelivering event: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "redelivery_error", str(e))
            return

        # M54: Retry Inbound Event
        if path.startswith("/v1/events/inbound/") and path.endswith("/retry"):
            ev_id = path[len("/v1/events/inbound/"):-len("/retry")].strip()
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            try:
                ev = self.webhook_repo.get_inbound_event(ev_id, tenant_id=user_id)
                if not ev:
                    self._send_error_response(HTTPStatus.NOT_FOUND, "event_not_found", f"Inbound event '{ev_id}' not found")
                    return
                # Transition status back to processing for replay
                retried = self.webhook_repo.update_inbound_event(
                    event_id=ev_id,
                    tenant_id=user_id,
                    status="processing",
                )
                self._send_json_response(retried.to_dict() if hasattr(retried, "to_dict") else retried, status=HTTPStatus.ACCEPTED)
            except Exception as e:
                logger.error(f"Error retrying inbound event: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "retry_error", str(e))
            return

        # M54: Replay Dead-Letter Event
        if path.startswith("/v1/events/dead-letter/") and path.endswith("/replay"):
            dl_id = path[len("/v1/events/dead-letter/"):-len("/replay")].strip()
            if not self.webhook_repo or not self.replay_service:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Dead-letter replay service unavailable")
                return
            try:
                schema = DeadLetterReplaySchema.model_validate(body if isinstance(body, dict) else {})
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return
            try:
                replay_rec = self.replay_service.replay_dead_letter(
                    dead_letter_id=dl_id,
                    tenant_id=user_id,
                    replayed_by=f"user:{user_id}",
                    reason=schema.reason,
                    target_url_override=schema.target_url_override,
                )
                self._send_json_response(replay_rec.to_dict() if hasattr(replay_rec, "to_dict") else replay_rec, status=HTTPStatus.ACCEPTED)
            except Exception as e:
                logger.error(f"Error replaying dead-letter event: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "replay_error", str(e))
            return


        # ============================================================
        # M55: Distributed Worker Fleet & Coordination (POST Routes)
        # ============================================================

        if path.startswith("/v1/fleet/workers/") and path.endswith("/drain"):
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            if not (identity and (identity.has_role(UserRole.ADMIN) or identity.has_scope(UserScope.ADMIN.value))):
                self._send_error_response(HTTPStatus.FORBIDDEN, "forbidden", "Administrative privileges required to drain workers")
                return
            if not self.fleet_coordinator:
                self._send_error_response(HTTPStatus.NOT_IMPLEMENTED, "fleet_disabled", "Fleet coordinator is not active")
                return
            worker_id = path[len("/v1/fleet/workers/"): -len("/drain")].strip()
            success = self.fleet_coordinator.drain_worker(worker_id)
            if not success:
                self._send_error_response(HTTPStatus.NOT_FOUND, "worker_not_found", f"Worker '{worker_id}' not found")
                return
            self._send_json_response({"status": "draining", "worker_id": worker_id})
            return

        if path == "/v1/fleet/sweep":
            is_auth, identity, err_msg, status_code = self._resolve_identity()
            if not is_auth:
                self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
                return
            if not (identity and (identity.has_role(UserRole.ADMIN) or identity.has_scope(UserScope.ADMIN.value))):
                self._send_error_response(HTTPStatus.FORBIDDEN, "forbidden", "Administrative privileges required to trigger recovery sweep")
                return
            if not self.fleet_coordinator:
                self._send_error_response(HTTPStatus.NOT_IMPLEMENTED, "fleet_disabled", "Fleet coordinator is not active")
                return
            res = self.fleet_coordinator.trigger_recovery_sweep()
            self._send_json_response(res)
            return

        # ============================================================
        # M56: Cognitive Memory & Continuous Learning (POST Routes)
        # ============================================================

        if path == "/v1/cognitive-memory/record":
            if not self.cognitive_memory_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Cognitive memory repository unavailable")
                return
            try:
                schema = CognitiveMemoryRecordSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            try:
                mem, contra = self.cognitive_memory_repo.record_memory(
                    tenant_id=user_id,
                    content=schema.content,
                    memory_type=schema.memory_type,
                    category=schema.category,
                    key=schema.key,
                    structured_data=schema.structured_data,
                    confidence=schema.confidence,
                    provenance_type=schema.provenance_type,
                    tags=schema.tags,
                    source_urls=schema.source_urls,
                    metadata=schema.metadata,
                    auto_resolve_contradictions=schema.auto_resolve_contradictions,
                )
                resp = {
                    "memory": mem.to_dict(),
                    "contradiction": contra.to_dict() if contra else None,
                }
                self._send_json_response(resp, status=HTTPStatus.CREATED)
            except Exception as e:
                logger.error(f"Error recording cognitive memory: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "record_error", str(e))
            return

        if path == "/v1/cognitive-memory/feedback":
            if not self.cognitive_memory_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Cognitive memory repository unavailable")
                return
            try:
                schema = MemoryFeedbackRequestSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            try:
                target_mem = None
                if schema.target_memory_id:
                    target_mem = self.cognitive_memory_repo.get_memory(schema.target_memory_id, tenant_id=user_id)

                event = MemoryFeedbackEvent(
                    event_id=str(uuid4()),
                    tenant_id=user_id,
                    target_memory_id=schema.target_memory_id,
                    feedback_type=FeedbackType(schema.feedback_type),
                    correction_content=schema.correction_content,
                    metadata=schema.metadata,
                )

                updated_target, new_mem, applied_event = self.feedback_loop.process_feedback(event, target_memory=target_mem)

                # Persist state changes
                if updated_target is not None:
                    self.cognitive_memory_repo.update_memory_state(
                        memory_id=updated_target.memory_id,
                        tenant_id=user_id,
                        lifecycle_state=updated_target.lifecycle_state,
                        confidence=updated_target.confidence,
                        reason=f"feedback:{event.feedback_type.value}",
                    )
                if new_mem is not None:
                    self.cognitive_memory_repo.record_memory(
                        tenant_id=user_id,
                        content=new_mem.content,
                        memory_type=new_mem.memory_type,
                        category=new_mem.category,
                        key=new_mem.key,
                        structured_data=new_mem.structured_data,
                        confidence=new_mem.confidence,
                        provenance_type=new_mem.provenance_type,
                        lifecycle_state=new_mem.lifecycle_state,
                        tags=list(new_mem.tags),
                        source_urls=list(new_mem.source_urls),
                        metadata=new_mem.metadata,
                        auto_resolve_contradictions=False,
                    )
                self.cognitive_memory_repo.record_feedback(applied_event)

                self._send_json_response({
                    "feedback_event": applied_event.to_dict(),
                    "updated_target_memory": updated_target.to_dict() if updated_target else None,
                    "new_memory": new_mem.to_dict() if new_mem else None,
                })
            except Exception as e:
                logger.error(f"Error processing memory feedback: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "feedback_error", str(e))
            return

        if path.startswith("/v1/cognitive-memory/contradictions/") and path.endswith("/resolve"):
            contra_id = path[len("/v1/cognitive-memory/contradictions/"): -len("/resolve")].strip()
            if not self.cognitive_memory_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Cognitive memory repository unavailable")
                return
            try:
                schema = ContradictionResolveRequestSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            try:
                success = self.cognitive_memory_repo.resolve_contradiction(
                    contradiction_id=contra_id,
                    tenant_id=user_id,
                    resolution_strategy=schema.resolution_strategy,
                    resolved_by=f"user:{user_id}",
                    winning_memory_id=schema.winning_memory_id,
                )
                if not success:
                    self._send_error_response(HTTPStatus.NOT_FOUND, "contradiction_not_found", f"Contradiction '{contra_id}' not found")
                    return
                self._send_json_response({"contradiction_id": contra_id, "resolved": True})
            except Exception as e:
                logger.error(f"Error resolving contradiction: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "contradiction_resolution_error", str(e))
            return

        if path == "/v1/cognitive-memory/consolidate":
            if not self.cognitive_memory_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Cognitive memory repository unavailable")
                return
            try:
                episodes = self.cognitive_memory_repo.query_memories(
                    tenant_id=user_id,
                    memory_type=CognitiveMemoryType.EPISODIC,
                    lifecycle_state=LifecycleState.ACTIVE,
                    limit=50,
                )
                synthesized = self.consolidation_engine.extract_semantic_facts_from_episodes(
                    tenant_id=user_id,
                    episodes=episodes,
                )
                for fact in synthesized:
                    self.cognitive_memory_repo.record_memory(
                        tenant_id=fact.tenant_id,
                        content=fact.content,
                        memory_type=fact.memory_type,
                        category=fact.category,
                        key=fact.key,
                        structured_data=fact.structured_data,
                        confidence=fact.confidence,
                        provenance_type=fact.provenance_type,
                        lifecycle_state=fact.lifecycle_state,
                        tags=list(fact.tags),
                        auto_resolve_contradictions=True,
                    )
                self._send_json_response({"synthesized_facts_count": len(synthesized)})
            except Exception as e:
                logger.error(f"Error consolidating cognitive memory: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "consolidation_error", str(e))
            return

        # M57: Upload Multimodal Artifact
        if path == "/v1/multimodal/artifacts":
            if not self.multimodal_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Multimodal repository unavailable")
                return
            try:
                schema = MultimodalArtifactUploadSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            import base64
            if schema.content_base64:
                try:
                    raw_bytes = base64.b64decode(schema.content_base64)
                except Exception:
                    self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_base64", "Invalid base64 payload")
                    return
            elif schema.content_text:
                raw_bytes = schema.content_text.encode("utf-8")
            else:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "missing_content", "Either content_base64 or content_text must be provided")
                return

            try:
                artifact = self.multimodal_processor.ingest_artifact(
                    tenant_id=user_id,
                    data=raw_bytes,
                    filename=schema.filename,
                    declared_format=schema.format,
                    provenance=schema.provenance,
                    metadata=schema.metadata,
                )
                self._send_json_response(artifact.to_dict(), status=HTTPStatus.CREATED)
            except ValueError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "validation_failed", str(ve))
            except Exception as e:
                logger.error(f"Error ingesting multimodal artifact: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "ingestion_error", str(e))
            return

        # M57: Process Multimodal Artifact
        if path == "/v1/multimodal/process":
            if not self.multimodal_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Multimodal repository unavailable")
                return
            try:
                schema = MultimodalProcessRequestSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            try:
                result, job = self.multimodal_processor.process_artifact(
                    tenant_id=user_id,
                    artifact_id=schema.artifact_id,
                    operation=schema.operation,
                    user_prompt=schema.user_prompt,
                    idempotency_key=schema.idempotency_key,
                )
                if schema.admit_to_memory and result and self.multimodal_bridge and self.multimodal_repo:
                    art = self.multimodal_repo.get_artifact(schema.artifact_id, tenant_id=user_id)
                    if art:
                        self.multimodal_bridge.admit_to_cognitive_memory(
                            tenant_id=user_id,
                            artifact=art,
                            result=result,
                            explicit_user_confirmed=schema.user_confirmed_memory,
                        )

                self._send_json_response({
                    "job": job.to_dict() if job else None,
                    "result": result.to_dict() if result else None,
                })
            except ValueError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_process_request", str(ve))
            except PermissionError as pe:
                self._send_error_response(HTTPStatus.FORBIDDEN, "forbidden", str(pe))
            except Exception as e:
                logger.error(f"Error processing multimodal artifact: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "processing_error", str(e))
            return

        self._send_error_response(HTTPStatus.NOT_FOUND, "not_found", f"Path '{path}' not found")




    def do_PUT(self) -> None:
        """Route PUT requests with authentication and administrative validation."""
        req_start = time.time()
        self._setup_request_context()
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")

        is_auth, identity, err_msg, status_code = self._resolve_identity()
        if not is_auth:
            self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
            return

        body = self._read_json_body()
        if body is None:
            return

        # M55: Configure Tenant Concurrency Quota
        if path.startswith("/v1/fleet/tenants/") and path.endswith("/quota"):
            if not (identity and (identity.has_role(UserRole.ADMIN) or identity.has_scope(UserScope.ADMIN.value))):
                self._send_error_response(HTTPStatus.FORBIDDEN, "forbidden", "Administrative privileges required to update tenant quotas")
                return
            if not self.fleet_repo:
                self._send_error_response(HTTPStatus.NOT_IMPLEMENTED, "fleet_disabled", "Fleet repository is not active")
                return
            tenant_id = path[len("/v1/fleet/tenants/"): -len("/quota")].strip()
            try:
                schema = TenantQuotaUpdateSchema.model_validate(body)
                from core.fleet.types import TenantWorkerLimitRecord
                record = TenantWorkerLimitRecord(
                    tenant_id=tenant_id,
                    max_active_tasks=schema.max_active_tasks,
                    guaranteed_slots=schema.guaranteed_slots,
                    burst_capacity=schema.burst_capacity,
                )
                saved = self.fleet_repo.set_tenant_limits(record)
                self._send_json_response(saved.to_dict())
            except Exception as e:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_quota_payload", str(e))
            return

        # M56: Update Cognitive User Profile
        if path == "/v1/cognitive-memory/profile":
            user_id = identity.user_id if identity else "default"
            if not self.cognitive_memory_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Cognitive memory repository unavailable")
                return
            try:
                schema = CognitiveProfileUpdateSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            try:
                prof = self.cognitive_memory_repo.get_profile(tenant_id=user_id)
                if not prof:
                    prof = UserCognitiveProfile(
                        profile_id=str(uuid4()),
                        tenant_id=user_id,
                        preferences=schema.preferences or {},
                        inferred_traits=schema.inferred_traits or {},
                        interaction_metrics=schema.interaction_metrics or {},
                    )
                else:
                    if schema.preferences is not None:
                        prof.preferences.update(schema.preferences)
                    if schema.inferred_traits is not None:
                        prof.inferred_traits.update(schema.inferred_traits)
                    if schema.interaction_metrics is not None:
                        prof.interaction_metrics.update(schema.interaction_metrics)
                saved = self.cognitive_memory_repo.save_profile(prof)
                self._send_json_response(saved.to_dict())
            except Exception as e:
                logger.error(f"Error updating cognitive profile: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "profile_update_error", str(e))
            return

        self._send_error_response(HTTPStatus.NOT_FOUND, "not_found", f"Path '{path}' not found")

    def do_PATCH(self) -> None:
        """Route PATCH requests with authentication and tenant isolation."""
        req_start = time.time()
        self._setup_request_context()
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")

        is_auth, identity, err_msg, status_code = self._resolve_identity()
        if not is_auth:
            self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
            return

        user_id = identity.user_id if identity else "default"
        body = self._read_json_body()
        if body is None:
            return

        if path.startswith("/v1/automations/"):
            auto_id = path[len("/v1/automations/"):].strip()
            if not self.automation_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Automation repository unavailable")
                return
            try:
                update_schema = AutomationUpdateSchema.model_validate(body)
                update_dict = {k: v for k, v in update_schema.model_dump().items() if v is not None}
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return

            if "trigger_config" in update_dict and "cron" in update_dict["trigger_config"]:
                try:
                    validate_cron(update_dict["trigger_config"]["cron"])
                    update_dict["next_fire_at"] = calculate_next_fire(update_dict["trigger_config"]["cron"])
                except Exception as e:
                    self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_cron", str(e))
                    return

            updated = self.automation_repo.update_automation(auto_id, user_id=user_id, **update_dict)
            if not updated:
                self._send_error_response(HTTPStatus.NOT_FOUND, "automation_not_found", "Automation not found")
                return
            self._send_json_response(updated)
            return

        # M54: Update Webhook Endpoint
        if path.startswith("/v1/webhooks/endpoints/"):
            ep_id = path[len("/v1/webhooks/endpoints/"):].strip()
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            try:
                update_schema = WebhookEndpointUpdateSchema.model_validate(body)
                update_dict = {k: v for k, v in update_schema.model_dump().items() if v is not None}
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return
            try:
                updated = self.webhook_repo.update_endpoint(ep_id, tenant_id=user_id, **update_dict)
                if not updated:
                    self._send_error_response(HTTPStatus.NOT_FOUND, "endpoint_not_found", f"Webhook endpoint '{ep_id}' not found")
                    return
                self._send_json_response(updated.to_dict() if hasattr(updated, "to_dict") else updated)
            except Exception as e:
                logger.error(f"Error updating webhook endpoint: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "endpoint_update_error", str(e))
            return

        # M54: Update Event Subscription
        if path.startswith("/v1/events/subscriptions/"):
            sub_id = path[len("/v1/events/subscriptions/"):].strip()
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            try:
                update_schema = EventSubscriptionUpdateSchema.model_validate(body)
                update_dict = {k: v for k, v in update_schema.model_dump().items() if v is not None}
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return
            try:
                updated = self.webhook_repo.update_subscription(sub_id, tenant_id=user_id, **update_dict)
                if not updated:
                    self._send_error_response(HTTPStatus.NOT_FOUND, "subscription_not_found", f"Subscription '{sub_id}' not found")
                    return
                self._send_json_response(updated.to_dict() if hasattr(updated, "to_dict") else updated)
            except Exception as e:
                logger.error(f"Error updating subscription: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "subscription_update_error", str(e))
            return

        # M56: Update Cognitive Memory State
        if path.startswith("/v1/cognitive-memory/"):
            m_id = path[len("/v1/cognitive-memory/"):].strip()
            if not self.cognitive_memory_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Cognitive memory repository unavailable")
                return
            try:
                schema = CognitiveMemoryUpdateSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return
            try:
                updated = self.cognitive_memory_repo.update_memory_state(
                    memory_id=m_id,
                    tenant_id=user_id,
                    lifecycle_state=schema.lifecycle_state or "active",
                    confidence=schema.confidence,
                    reason=schema.reason,
                )
                if not updated:
                    self._send_error_response(HTTPStatus.NOT_FOUND, "memory_not_found", f"Cognitive memory '{m_id}' not found")
                    return
                self._send_json_response(updated.to_dict())
            except Exception as e:
                logger.error(f"Error updating cognitive memory: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "memory_update_error", str(e))
            return

        # M57: Update Multimodal Artifact State
        if path.startswith("/v1/multimodal/artifacts/"):
            art_id = path[len("/v1/multimodal/artifacts/"):].strip()
            if not self.multimodal_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Multimodal repository unavailable")
                return
            try:
                schema = MultimodalArtifactUpdateSchema.model_validate(body)
            except ValidationError as ve:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", str(ve))
                return
            try:
                updated = self.multimodal_repo.update_artifact_state(
                    artifact_id=art_id,
                    tenant_id=user_id,
                    lifecycle_state=schema.lifecycle_state,
                    security_classification=schema.security_classification,
                    reason=schema.reason,
                )
                if not updated:
                    self._send_error_response(HTTPStatus.NOT_FOUND, "artifact_not_found", f"Multimodal artifact '{art_id}' not found")
                    return
                self._send_json_response(updated.to_dict())
            except Exception as e:
                logger.error(f"Error updating multimodal artifact: {e}", exc_info=True)
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "artifact_update_error", str(e))
            return

        self._send_error_response(HTTPStatus.NOT_FOUND, "not_found", f"Path '{path}' not found")

    def do_DELETE(self) -> None:
        """Route DELETE requests with authentication and tenant isolation."""
        req_start = time.time()
        self._setup_request_context()
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")

        is_auth, identity, err_msg, status_code = self._resolve_identity()
        if not is_auth:
            self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
            return

        user_id = identity.user_id if identity else "default"

        if path.startswith("/v1/automations/"):
            auto_id = path[len("/v1/automations/"):].strip()
            if not self.automation_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Automation repository unavailable")
                return
            deleted = self.automation_repo.delete_automation(auto_id, user_id=user_id)
            if not deleted:
                self._send_error_response(HTTPStatus.NOT_FOUND, "automation_not_found", "Automation not found")
                return
            self._send_json_response({"id": auto_id, "deleted": True})
            return

        # M54: Delete Webhook Endpoint
        if path.startswith("/v1/webhooks/endpoints/"):
            ep_id = path[len("/v1/webhooks/endpoints/"):].strip()
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            deleted = self.webhook_repo.delete_endpoint(ep_id, tenant_id=user_id)
            if not deleted:
                self._send_error_response(HTTPStatus.NOT_FOUND, "endpoint_not_found", f"Webhook endpoint '{ep_id}' not found")
                return
            self._send_json_response({"id": ep_id, "deleted": True})
            return

        # M54: Delete Event Subscription
        if path.startswith("/v1/events/subscriptions/"):
            sub_id = path[len("/v1/events/subscriptions/"):].strip()
            if not self.webhook_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Webhook repository unavailable")
                return
            deleted = self.webhook_repo.delete_subscription(sub_id, tenant_id=user_id)
            if not deleted:
                self._send_error_response(HTTPStatus.NOT_FOUND, "subscription_not_found", f"Subscription '{sub_id}' not found")
                return
            self._send_json_response({"id": sub_id, "deleted": True})
            return

        # M56: Purge Tenant Cognitive Memories (GDPR compliance)
        if path == "/v1/cognitive-memory/purge":
            if not self.cognitive_memory_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Cognitive memory repository unavailable")
                return
            purged = self.cognitive_memory_repo.clear_tenant_memories(tenant_id=user_id)
            self._send_json_response({"purged_count": purged, "tenant_id": user_id, "success": True})
            return

        # M56: Delete Single Cognitive Memory
        if path.startswith("/v1/cognitive-memory/"):
            m_id = path[len("/v1/cognitive-memory/"):].strip()
            if not self.cognitive_memory_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Cognitive memory repository unavailable")
                return
            params = parse_qs(parsed_url.query)
            hard = params.get("hard", ["false"])[0].lower() in ("true", "1")
            deleted = self.cognitive_memory_repo.delete_memory(m_id, tenant_id=user_id, hard_delete=hard)
            if not deleted:
                self._send_error_response(HTTPStatus.NOT_FOUND, "memory_not_found", f"Cognitive memory '{m_id}' not found")
                return
            self._send_json_response({"memory_id": m_id, "deleted": True, "hard_delete": hard})
            return

        # M57: Purge Tenant Multimodal Data (GDPR compliance)
        if path.startswith("/v1/multimodal/tenants/") and path.endswith("/purge"):
            t_id = path[len("/v1/multimodal/tenants/"): -len("/purge")].strip()
            if not self.multimodal_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Multimodal repository unavailable")
                return
            if t_id != user_id and not (identity and (identity.has_role(UserRole.ADMIN) or identity.has_scope(UserScope.ADMIN.value))):
                self._send_error_response(HTTPStatus.FORBIDDEN, "forbidden", "Cannot purge another tenant's data")
                return
            purged = self.multimodal_repo.purge_tenant_data(tenant_id=t_id)
            self.multimodal_storage.purge_tenant(tenant_id=t_id)
            self._send_json_response({"purged_count": purged, "tenant_id": t_id, "success": True})
            return

        # M57: Delete Single Multimodal Artifact (Cascading)
        if path.startswith("/v1/multimodal/artifacts/"):
            art_id = path[len("/v1/multimodal/artifacts/"):].strip()
            if not self.multimodal_repo:
                self._send_error_response(HTTPStatus.SERVICE_UNAVAILABLE, "service_unavailable", "Multimodal repository unavailable")
                return
            deleted = self.multimodal_deletion_cascade.delete_artifact_cascade(tenant_id=user_id, artifact_id=art_id)
            if not deleted:
                self._send_error_response(HTTPStatus.NOT_FOUND, "artifact_not_found", f"Artifact '{art_id}' not found")
                return
            self._send_json_response({"artifact_id": art_id, "deleted": True})
            return


        self._send_error_response(HTTPStatus.NOT_FOUND, "not_found", f"Path '{path}' not found")


class AURAHTTPServer:
    """Lifecycle controller for the production AURA HTTP service."""

    def __init__(
        self,
        aura: AURA | None = None,
        config: Settings | None = None,
        host: str | None = None,
        port: int | None = None,
        authenticator: BaseAuthenticator | None = None,
        repository_container: RepositoryContainer | None = None,
    ):
        self.config = config or settings
        # Validate production configuration fail-closed
        validate_production_config(
            self.config,
            raise_on_error=(self.config.aura_env.lower() == "production"),
            authenticator_provided=(authenticator is not None),
        )
        self.aura = aura or create_aura(agentic=True, config=self.config)
        self.host = host or self.config.aura_server_host
        self.port = port if port is not None else self.config.aura_server_port
        self.repository_container = (
            repository_container
            or create_repository_container(
                config=self.config,
                auto_migrate=getattr(self.config, "aura_database_auto_migrate", True),
            )
        )
        self.authenticator = authenticator or create_token_authenticator(
            master_key=self.config.aura_server_api_key,
            token_repo=self.repository_container.tokens,
        )
        self.start_time = time.time()
        self._server: ThreadedHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._is_running = False

        # M52 Asynchronous Background Subsystem
        self.broadcaster = TaskEventBroadcaster()
        self.workflow_orchestrator = WorkflowOrchestrator(
            task_repo=self.repository_container.tasks,
            approval_repo=self.repository_container.approvals,
            broadcaster=self.broadcaster,
            model_gateway=getattr(self.aura, "model_gateway", None),
            policy_engine=getattr(self.aura, "policy", None),
            tool_registry=getattr(self.aura, "tool_registry", None),
        )
        if getattr(self.config, "aura_task_worker_enabled", True) and self.repository_container.tasks:
            self.task_worker = BackgroundTaskWorker(
                task_repo=self.repository_container.tasks,
                approval_repo=self.repository_container.approvals,
                orchestrator=self.workflow_orchestrator,
                concurrency=getattr(self.config, "aura_task_worker_concurrency", 4),
                poll_interval_ms=getattr(self.config, "aura_task_poll_interval_ms", 500),
            )
        else:
            self.task_worker = None

        # M53 Autonomous Supervisor
        if getattr(self.config, "aura_automations_enabled", True) and self.repository_container.automations:
            self.supervisor = AutonomousSupervisor(
                repositories=self.repository_container,
                poll_interval=getattr(self.config, "aura_automation_scheduler_poll_interval_seconds", 5.0),
                reconcile_interval=getattr(self.config, "aura_automation_reconciler_interval_seconds", 60.0),
                max_recursion_depth=getattr(self.config, "aura_automation_max_recursion_depth", 3),
            )
        else:
            self.supervisor = None

        # M54 Webhook Services and Outbound Delivery Worker
        if getattr(self.config, "aura_webhooks_enabled", True) and self.repository_container.webhooks:
            master_key = getattr(self.config, "aura_webhook_master_key", "aura-default-master-key-32bytes!!")
            self.delivery_worker = OutboundDeliveryWorker(
                webhook_repo=self.repository_container.webhooks,
                concurrency=getattr(self.config, "aura_webhook_delivery_concurrency", 4),
                poll_interval=getattr(self.config, "aura_webhook_delivery_poll_interval_seconds", 0.5),
            )
            self.ingress_service = WebhookIngressService(
                webhook_repo=self.repository_container.webhooks,
                task_repo=self.repository_container.tasks,
                automation_repo=self.repository_container.automations,
                master_key=master_key,
            )
            self.dispatcher_service = EventDispatcherService(
                webhook_repo=self.repository_container.webhooks,
                task_repo=self.repository_container.tasks,
                automation_repo=self.repository_container.automations,
            )
            self.replay_service = DeadLetterReplayService(
                webhook_repo=self.repository_container.webhooks,
            )
        else:
            self.delivery_worker = None
            self.ingress_service = None
            self.dispatcher_service = None
            self.replay_service = None

        # M55 Distributed Worker Fleet & Coordination
        if getattr(self.config, "aura_fleet_enabled", False) and self.repository_container.fleet and self.repository_container.tasks:
            self.fleet_coordinator = WorkerFleetCoordinator(
                fleet_repo=self.repository_container.fleet,
                sweep_interval_seconds=getattr(self.config, "aura_fleet_recovery_sweep_interval_seconds", 10.0),
                worker_expiry_seconds=getattr(self.config, "aura_fleet_worker_expiry_seconds", 15.0),
                lease_expiry_seconds=getattr(self.config, "aura_fleet_lease_duration_seconds", 30.0),
            )
            self.fleet_worker = DistributedFleetWorker(
                fleet_repo=self.repository_container.fleet,
                task_repo=self.repository_container.tasks,
                approval_repo=self.repository_container.approvals,
                orchestrator=self.workflow_orchestrator,
                concurrency=getattr(self.config, "aura_fleet_worker_concurrency", 4),
                poll_interval_ms=getattr(self.config, "aura_task_poll_interval_ms", 500),
                heartbeat_interval_seconds=getattr(self.config, "aura_fleet_heartbeat_interval_seconds", 5.0),
                missed_heartbeats_threshold=getattr(self.config, "aura_fleet_missed_heartbeats_threshold", 3),
                lease_duration_seconds=getattr(self.config, "aura_fleet_lease_duration_seconds", 30.0),
                drain_timeout_seconds=getattr(self.config, "aura_fleet_drain_timeout_seconds", 30.0),
            )
        else:
            self.fleet_coordinator = None
            self.fleet_worker = None

    def start(self, block: bool = True) -> None:
        """Start the HTTP server on configured host and port."""
        server_address = (self.host, self.port)
        self._server = ThreadedHTTPServer(server_address, AURAHTTPRequestHandler)
        self._server.aura = self.aura  # type: ignore
        self._server.config = self.config  # type: ignore
        self._server.start_time = self.start_time  # type: ignore
        self._server.authenticator = self.authenticator  # type: ignore
        self._server.repository_container = self.repository_container  # type: ignore
        self._server.broadcaster = self.broadcaster  # type: ignore
        self._server.workflow_orchestrator = self.workflow_orchestrator  # type: ignore
        self._server.task_worker = self.task_worker  # type: ignore
        self._server.supervisor = self.supervisor  # type: ignore
        self._server.delivery_worker = self.delivery_worker  # type: ignore
        self._server.ingress_service = self.ingress_service  # type: ignore
        self._server.dispatcher_service = self.dispatcher_service  # type: ignore
        self._server.replay_service = self.replay_service  # type: ignore
        self._server.fleet_coordinator = self.fleet_coordinator  # type: ignore
        self._server.fleet_worker = self.fleet_worker  # type: ignore
        self._is_running = True

        if self.task_worker:
            self.task_worker.start()
        if self.delivery_worker:
            self.delivery_worker.start()
        if self.fleet_coordinator:
            self.fleet_coordinator.start()
        if self.fleet_worker:
            self.fleet_worker.start()

        logger.info(
            f"Starting {self.config.aura_app_name} HTTP Server on http://{self.host}:{self.port} (env: {self.config.aura_env})"
        )

        if block:
            self._setup_signals()
            try:
                self._server.serve_forever()
            except (KeyboardInterrupt, SystemExit):
                self.stop()
        else:
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Gracefully stop the HTTP server and flush persistent state."""
        if not self._is_running:
            return

        logger.info("Initiating graceful server shutdown...")
        self._is_running = False

        if getattr(self, "task_worker", None):
            try:
                self.task_worker.stop()
            except Exception as e:
                logger.warning(f"Error stopping BackgroundTaskWorker: {e}")

        if getattr(self, "delivery_worker", None):
            try:
                self.delivery_worker.stop()
            except Exception as e:
                logger.warning(f"Error stopping OutboundDeliveryWorker: {e}")

        if getattr(self, "fleet_worker", None):
            try:
                self.fleet_worker.stop()
            except Exception as e:
                logger.warning(f"Error stopping DistributedFleetWorker: {e}")

        if getattr(self, "fleet_coordinator", None):
            try:
                self.fleet_coordinator.stop()
            except Exception as e:
                logger.warning(f"Error stopping WorkerFleetCoordinator: {e}")

        # Flush runtime state checkpoint if agentic runtime is available
        if self.aura and self.aura.agentic_runtime:
            try:
                self.aura.save_state_checkpoint(is_clean_shutdown=True)
                logger.info("Runtime checkpoint successfully saved upon shutdown.")
            except Exception as e:
                logger.warning(f"Failed to persist checkpoint on shutdown: {e}")

            try:
                self.aura.stop_daemon(timeout=self.config.aura_shutdown_grace_period_seconds)
            except Exception:
                pass

        if self.repository_container and self.repository_container.db_pool:
            try:
                self.repository_container.db_pool.close()
            except Exception as e:
                logger.warning(f"Error closing db_pool: {e}")

        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.config.aura_shutdown_grace_period_seconds)

        logger.info("AURA HTTP Server stopped cleanly.")

    def _setup_signals(self) -> None:
        """Configure graceful signal handling for SIGINT and SIGTERM."""
        def _handle_signal(sig: int, frame: Any) -> None:
            logger.info(f"Received signal {sig}; shutting down...")
            threading.Thread(target=self.stop, daemon=True).start()

        try:
            signal.signal(signal.SIGINT, _handle_signal)
            if hasattr(signal, "SIGTERM"):
                signal.signal(signal.SIGTERM, _handle_signal)
        except (ValueError, AttributeError):
            pass


def main() -> None:
    """CLI entrypoint to run the production AURA HTTP server."""
    parser = argparse.ArgumentParser(
        prog="aura-server",
        description="Run Project AURA Production HTTP Server",
    )
    parser.add_argument("--host", type=str, default=None, help="Host interface to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Port to listen on (default: 8000)")
    parser.add_argument("--env", type=str, default=None, help="Environment name (development, production)")

    args = parser.parse_args()

    config = Settings()
    if args.env:
        config.aura_env = args.env

    if getattr(config, "aura_structured_logging_enabled", True):
        configure_structured_logging(
            level=config.aura_log_level,
            json_format=(getattr(config, "aura_log_format", "json").lower() == "json"),
            service_name=config.aura_app_name,
            environment=config.aura_env,
        )
    else:
        logging.basicConfig(
            level=getattr(logging, config.aura_log_level.upper(), logging.INFO),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

    server = AURAHTTPServer(config=config, host=args.host, port=args.port)
    server.start(block=True)


if __name__ == "__main__":
    main()
