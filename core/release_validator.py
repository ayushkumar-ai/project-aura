"""M29 — Production Deployment & Release Validation Utility for Project AURA.

Provides automated preflight validation, security boundary verification, configuration
inspection, and persistent storage readiness checks.
"""

from __future__ import annotations

import importlib
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from app.config import Settings, settings as default_settings

logger = logging.getLogger("aura.release_validator")


class ReleaseValidationCategory(str, Enum):
    CONFIG = "CONFIG"
    DEPENDENCIES = "DEPENDENCIES"
    STORAGE = "STORAGE"
    SECURITY = "SECURITY"
    HEALTH = "HEALTH"
    MODULES = "MODULES"


class ValidationSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class ValidationCheckResult:
    check_id: str
    category: ReleaseValidationCategory
    name: str
    passed: bool
    severity: ValidationSeverity
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "category": self.category.value,
            "name": self.name,
            "passed": self.passed,
            "severity": self.severity.value,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class ReleaseValidationReport:
    timestamp: float
    environment: str
    is_production_ready: bool
    checks: list[ValidationCheckResult] = field(default_factory=list)
    summary_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "environment": self.environment,
            "is_production_ready": self.is_production_ready,
            "checks": [c.to_dict() for c in self.checks],
            "summary_counts": self.summary_counts,
        }

    def render_summary(self) -> str:
        passed_count = sum(1 for c in self.checks if c.passed)
        total_count = len(self.checks)
        status_str = "PRODUCTION READY" if self.is_production_ready else "NOT READY"
        lines = [
            f"=== AURA Release Validation Report [{status_str}] ===",
            f"Environment: {self.environment} | Timestamp: {self.timestamp}",
            f"Checks: {passed_count}/{total_count} Passed",
        ]
        for c in self.checks:
            icon = "✓" if c.passed else "✗"
            lines.append(f"  [{icon}] ({c.category.value}) {c.name}: {c.message}")
        return "\n".join(lines)


class ReleaseValidator:
    """Automated validator for production release readiness and preflight validation."""

    CRITICAL_MODULES = (
        "app.aura",
        "app.config",
        "app.server",
        "core.agentic_runtime",
        "core.orchestrator",
        "core.goal_engine",
        "core.runtime_checkpoint",
        "core.artifact_store",
        "core.epistemic_graph",
        "core.security_scrubber",
        "core.code_sandbox",
        "core.self_healing_orchestrator",
    )

    def __init__(self, config: Settings | None = None):
        self.config = config or default_settings

    def validate_configuration(self, config: Settings | None = None) -> list[ValidationCheckResult]:
        cfg = config or self.config
        results: list[ValidationCheckResult] = []
        is_prod = cfg.aura_env.lower() == "production"

        # Check 1: Port range validity
        port_valid = 1 <= cfg.aura_server_port <= 65535
        results.append(
            ValidationCheckResult(
                check_id="cfg_port_range",
                category=ReleaseValidationCategory.CONFIG,
                name="Server Port Range",
                passed=port_valid,
                severity=ValidationSeverity.ERROR if not port_valid else ValidationSeverity.INFO,
                message=f"Server port {cfg.aura_server_port} is valid" if port_valid else f"Invalid port {cfg.aura_server_port}",
            )
        )

        # Check 2: Production Auth enforcement
        if is_prod:
            auth_ok = cfg.aura_api_key_auth_enabled and bool(cfg.aura_server_api_key.strip())
            results.append(
                ValidationCheckResult(
                    check_id="cfg_prod_auth",
                    category=ReleaseValidationCategory.SECURITY,
                    name="Production API Key Authentication",
                    passed=auth_ok,
                    severity=ValidationSeverity.CRITICAL if not auth_ok else ValidationSeverity.INFO,
                    message="API key authentication enabled with non-empty key" if auth_ok else "Production mode requires aura_api_key_auth_enabled=True and a valid aura_server_api_key",
                )
            )
        else:
            results.append(
                ValidationCheckResult(
                    check_id="cfg_dev_auth",
                    category=ReleaseValidationCategory.CONFIG,
                    name="Development Auth Configuration",
                    passed=True,
                    severity=ValidationSeverity.INFO,
                    message=f"Running in {cfg.aura_env} mode (Auth enabled: {cfg.aura_api_key_auth_enabled})",
                )
            )

        # Check 3: Request body limit sanity
        body_limit_ok = 1024 <= cfg.aura_max_request_body_bytes <= 104857600  # 1KB to 100MB
        results.append(
            ValidationCheckResult(
                check_id="cfg_body_limit",
                category=ReleaseValidationCategory.CONFIG,
                name="Max Request Body Limit",
                passed=body_limit_ok,
                severity=ValidationSeverity.WARNING if not body_limit_ok else ValidationSeverity.INFO,
                message=f"Request body limit is set to {cfg.aura_max_request_body_bytes} bytes",
            )
        )

        return results

    def validate_storage_directories(self, config: Settings | None = None) -> list[ValidationCheckResult]:
        cfg = config or self.config
        results: list[ValidationCheckResult] = []

        dirs_to_check = [
            ("artifact_dir", cfg.aura_artifact_storage_dir),
            ("trace_dir", cfg.aura_trace_storage_dir),
            ("skills_dir", cfg.aura_skills_storage_dir),
            ("knowledge_dir", cfg.aura_knowledge_storage_dir),
        ]

        for name, dir_path_str in dirs_to_check:
            if not dir_path_str:
                results.append(
                    ValidationCheckResult(
                        check_id=f"storage_{name}",
                        category=ReleaseValidationCategory.STORAGE,
                        name=f"Storage Path '{name}'",
                        passed=True,
                        severity=ValidationSeverity.INFO,
                        message=f"Using in-memory/default storage for {name}",
                    )
                )
                continue

            try:
                p = Path(dir_path_str)
                p.mkdir(parents=True, exist_ok=True)
                # Test write and delete
                test_file = p / f".write_test_{int(time.time())}.tmp"
                test_file.write_text("aura_write_probe", encoding="utf-8")
                test_file.unlink(missing_ok=True)
                results.append(
                    ValidationCheckResult(
                        check_id=f"storage_{name}",
                        category=ReleaseValidationCategory.STORAGE,
                        name=f"Storage Path '{name}'",
                        passed=True,
                        severity=ValidationSeverity.INFO,
                        message=f"Directory '{dir_path_str}' is writable and accessible",
                        details={"path": str(p.resolve())},
                    )
                )
            except Exception as e:
                results.append(
                    ValidationCheckResult(
                        check_id=f"storage_{name}",
                        category=ReleaseValidationCategory.STORAGE,
                        name=f"Storage Path '{name}'",
                        passed=False,
                        severity=ValidationSeverity.ERROR,
                        message=f"Failed to access directory '{dir_path_str}': {e}",
                        details={"error": str(e)},
                    )
                )

        return results

    def validate_critical_modules(self) -> list[ValidationCheckResult]:
        results: list[ValidationCheckResult] = []
        for mod_name in self.CRITICAL_MODULES:
            try:
                importlib.import_module(mod_name)
                results.append(
                    ValidationCheckResult(
                        check_id=f"mod_{mod_name.replace('.', '_')}",
                        category=ReleaseValidationCategory.MODULES,
                        name=f"Module Import: {mod_name}",
                        passed=True,
                        severity=ValidationSeverity.INFO,
                        message=f"Successfully imported {mod_name}",
                    )
                )
            except Exception as e:
                results.append(
                    ValidationCheckResult(
                        check_id=f"mod_{mod_name.replace('.', '_')}",
                        category=ReleaseValidationCategory.MODULES,
                        name=f"Module Import: {mod_name}",
                        passed=False,
                        severity=ValidationSeverity.CRITICAL,
                        message=f"Failed to import {mod_name}: {e}",
                        details={"error": str(e)},
                    )
                )
        return results

    def run_preflight_checks(
        self,
        aura_instance: Any | None = None,
        config: Settings | None = None,
    ) -> ReleaseValidationReport:
        """Run complete preflight inspection and return comprehensive validation report."""
        cfg = config or self.config
        all_checks: list[ValidationCheckResult] = []

        # 1. Config validation
        all_checks.extend(self.validate_configuration(cfg))

        # 2. Storage directories
        all_checks.extend(self.validate_storage_directories(cfg))

        # 3. Critical modules
        all_checks.extend(self.validate_critical_modules())

        # 4. Runtime health if instance provided
        if aura_instance is not None:
            try:
                is_ready = bool(
                    getattr(aura_instance, "orchestrator", None) is not None
                    or getattr(aura_instance, "workflow_executor", None) is not None
                    or getattr(aura_instance, "goal_engine", None) is not None
                    or getattr(aura_instance, "agentic_runtime", None) is not None
                )
                all_checks.append(
                    ValidationCheckResult(
                        check_id="runtime_instance_ready",
                        category=ReleaseValidationCategory.HEALTH,
                        name="AURA Runtime Instance Readiness",
                        passed=is_ready,
                        severity=ValidationSeverity.CRITICAL if not is_ready else ValidationSeverity.INFO,
                        message="AURA runtime instance initialized and ready" if is_ready else "AURA instance degraded or uninitialized",
                    )
                )
            except Exception as e:
                all_checks.append(
                    ValidationCheckResult(
                        check_id="runtime_instance_ready",
                        category=ReleaseValidationCategory.HEALTH,
                        name="AURA Runtime Instance Readiness",
                        passed=False,
                        severity=ValidationSeverity.ERROR,
                        message=f"Runtime inspection failed: {e}",
                    )
                )

        # Summary calculations
        passed_count = sum(1 for c in all_checks if c.passed)
        critical_failures = sum(1 for c in all_checks if not c.passed and c.severity == ValidationSeverity.CRITICAL)
        error_failures = sum(1 for c in all_checks if not c.passed and c.severity == ValidationSeverity.ERROR)

        is_prod = cfg.aura_env.lower() == "production"
        if is_prod:
            is_ready = critical_failures == 0 and error_failures == 0
        else:
            is_ready = critical_failures == 0

        summary = {
            "total": len(all_checks),
            "passed": passed_count,
            "failed": len(all_checks) - passed_count,
            "critical_failures": critical_failures,
            "error_failures": error_failures,
        }

        return ReleaseValidationReport(
            timestamp=time.time(),
            environment=cfg.aura_env,
            is_production_ready=is_ready,
            checks=all_checks,
            summary_counts=summary,
        )
