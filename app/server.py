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
from uuid import UUID
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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
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
        if path == "/health":
            uptime = time.time() - self.start_time
            self._send_json_response({
                "status": "healthy",
                "app_name": self.config.aura_app_name,
                "version": "0.28.0",
                "environment": self.config.aura_env,
                "uptime_seconds": round(uptime, 2),
                "timestamp": time.time(),
            })
            self._record_http_metric("GET", path, 200, req_start)
            return

        if path == "/ready":
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

        self._send_error_response(HTTPStatus.NOT_FOUND, "not_found", f"Path '{path}' not found")

    def do_POST(self) -> None:
        """Route POST requests with M41 authentication, user isolation, and M44 correlation."""
        req_start = time.time()
        self._setup_request_context()
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")

        # Check authentication
        is_auth, identity, err_msg, status_code = self._resolve_identity()
        if not is_auth:
            self._send_error_response(HTTPStatus(status_code), "unauthorized", err_msg or "Unauthorized")
            return

        body = self._read_json_body()
        if body is None:
            return

        user_id = identity.user_id if identity else "default"

        if path == "/v1/run":
            user_input = body.get("user_input") or body.get("prompt")
            if not user_input or not isinstance(user_input, str):
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", "Field 'user_input' or 'prompt' is required")
                return

            metadata = body.get("metadata", {})
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
            task = body.get("task")
            if not task or not isinstance(task, str):
                self._send_error_response(HTTPStatus.BAD_REQUEST, "invalid_request", "Field 'task' is required")
                return

            task_id = body.get("task_id")
            timeout = body.get("timeout")
            try:
                result = self.aura.run_task(task=task, task_id=task_id, timeout=timeout)
                self._send_json_response({
                    "task": task,
                    "task_id": task_id,
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
                updated = self.aura.update_user_preferences(body, user_id=user_id)
                self._send_json_response(updated.to_dict() if hasattr(updated, "to_dict") else updated)
            except Exception as e:
                self._send_error_response(HTTPStatus.BAD_REQUEST, "preference_update_error", str(e))
            return

        if path == "/v1/rag":
            query = body.get("query", "")
            max_chars = int(body.get("max_chars", 4000))
            try:
                bundle = self.aura.retrieve_rag_context(query=query, max_chars=max_chars, user_id=user_id)
                self._send_json_response(bundle.to_dict() if hasattr(bundle, "to_dict") else bundle)
            except Exception as e:
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "rag_error", str(e))
            return

        if path == "/v1/plan":
            goal = body.get("goal", "")
            try:
                plan = self.aura.create_structured_plan(goal=goal)
                self._send_json_response(plan.to_dict() if hasattr(plan, "to_dict") else plan)
            except Exception as e:
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "planning_error", str(e))
            return

        if path == "/v1/tools/execute":
            tool_name = body.get("tool_name", "")
            parameters = body.get("parameters", {})
            caller = body.get("caller", "agent")
            try:
                result = self.aura.execute_ecosystem_tool(tool_name=tool_name, parameters=parameters, caller=caller)
                self._send_json_response(result.to_dict() if hasattr(result, "to_dict") else result)
            except Exception as e:
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "tool_execution_error", str(e))
            return

        if path == "/v1/proactive/approve":
            proposal_id = body.get("proposal_id", "")
            try:
                prop = self.aura.approve_proactive_proposal(proposal_id)
                self._send_json_response(prop.to_dict() if hasattr(prop, "to_dict") else prop)
            except Exception as e:
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "approval_error", str(e))
            return

        if path == "/v1/proactive/reject":
            proposal_id = body.get("proposal_id", "")
            reason = body.get("reason", "")
            try:
                prop = self.aura.reject_proactive_proposal(proposal_id, reason=reason)
                self._send_json_response(prop.to_dict() if hasattr(prop, "to_dict") else prop)
            except Exception as e:
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "rejection_error", str(e))
            return

        if path == "/v1/devices/action":
            if identity and not (identity.has_role(UserRole.ADMIN) or identity.has_role(UserRole.OPERATOR) or identity.has_scope(UserScope.DEVICE_EXEC.value)):
                self._send_error_response(HTTPStatus.FORBIDDEN, "forbidden", "Insufficient permission for device execution")
                return
            device_id = body.get("device_id", "")
            capability = body.get("capability", "")
            parameters = body.get("parameters", {})
            try:
                from core.device_integration_types import DeviceCapability
                cap_enum = DeviceCapability(capability) if isinstance(capability, str) else capability
                act_res = self.aura.execute_device_action(device_id=device_id, capability=cap_enum, parameters=parameters)
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
            user_input = body.get("user_input") or body.get("prompt") or body.get("goal", "")
            task_id = body.get("task_id")
            auto_sync = body.get("auto_sync", True)
            try:
                cycle_res = self.aura.execute_integrated_cycle(user_input=user_input, task_id=task_id, auto_sync=auto_sync)
                self._send_json_response(cycle_res.to_dict() if hasattr(cycle_res, "to_dict") else cycle_res)
            except Exception as e:
                self._send_error_response(HTTPStatus.INTERNAL_SERVER_ERROR, "cycle_execution_error", str(e))
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

    def start(self, block: bool = True) -> None:
        """Start the HTTP server on configured host and port."""
        server_address = (self.host, self.port)
        self._server = ThreadedHTTPServer(server_address, AURAHTTPRequestHandler)
        self._server.aura = self.aura  # type: ignore
        self._server.config = self.config  # type: ignore
        self._server.start_time = self.start_time  # type: ignore
        self._server.authenticator = self.authenticator  # type: ignore
        self._server.repository_container = self.repository_container  # type: ignore
        self._is_running = True

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
