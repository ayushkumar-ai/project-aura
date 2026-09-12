"""Unit tests for M29 Release Validation Subsystem."""

import pytest
from app.config import Settings
from core.release_validator import (
    ReleaseValidationCategory,
    ReleaseValidator,
    ValidationSeverity,
)


def test_validator_initialization():
    validator = ReleaseValidator()
    assert validator.config is not None


def test_validate_configuration_dev():
    cfg = Settings(aura_env="development", aura_server_port=8000)
    validator = ReleaseValidator(config=cfg)
    results = validator.validate_configuration(cfg)
    assert len(results) >= 3
    assert all(r.passed for r in results)


def test_validate_configuration_prod_missing_auth():
    cfg = Settings(
        aura_env="production",
        aura_api_key_auth_enabled=False,
        aura_server_api_key="",
    )
    validator = ReleaseValidator(config=cfg)
    results = validator.validate_configuration(cfg)
    auth_check = next(r for r in results if r.check_id == "cfg_prod_auth")
    assert not auth_check.passed
    assert auth_check.severity == ValidationSeverity.CRITICAL


def test_validate_configuration_prod_valid_auth():
    cfg = Settings(
        aura_env="production",
        aura_api_key_auth_enabled=True,
        aura_server_api_key="strong-production-api-key-12345",
    )
    validator = ReleaseValidator(config=cfg)
    results = validator.validate_configuration(cfg)
    auth_check = next(r for r in results if r.check_id == "cfg_prod_auth")
    assert auth_check.passed


def test_validate_critical_modules():
    validator = ReleaseValidator()
    results = validator.validate_critical_modules()
    assert len(results) == len(ReleaseValidator.CRITICAL_MODULES)
    assert all(r.passed for r in results)


def test_run_preflight_checks_report():
    validator = ReleaseValidator()
    report = validator.run_preflight_checks()
    assert report.is_production_ready is True
    assert report.summary_counts["total"] > 0
    assert report.summary_counts["failed"] == 0
    rendered = report.render_summary()
    assert "AURA Release Validation Report" in rendered
    dict_repr = report.to_dict()
    assert "checks" in dict_repr
