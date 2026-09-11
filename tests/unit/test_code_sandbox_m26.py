"""Unit tests for Milestone 26 AST Security Sandbox and Code Sandbox Validator."""

import pytest
from core.provenance import wrap_tainted, is_tainted, unwrap_tainted
from core.code_sandbox import (
    CodeSandboxValidator,
    SandboxedToolExecutor,
    ALLOWED_STDLIB_MODULES,
    FORBIDDEN_MODULES,
    FORBIDDEN_BUILTINS,
)


def test_validator_safe_code():
    code = """
import math
import json
import re

def execute(input_data: str) -> str:
    val = float(input_data.strip())
    res = math.sqrt(val)
    return json.dumps({"result": res})
"""
    validator = CodeSandboxValidator()
    report = validator.validate_source(code)
    assert report.is_safe is True
    assert len(report.violations) == 0
    assert "math" in report.allowed_imports
    assert "json" in report.allowed_imports
    assert "re" in report.allowed_imports


def test_validator_rejects_forbidden_imports():
    forbidden_snippets = [
        "import os\ndef execute(x): return 'bad'",
        "import sys\ndef execute(x): return 'bad'",
        "import subprocess\ndef execute(x): return 'bad'",
        "import socket\ndef execute(x): return 'bad'",
        "import shutil\ndef execute(x): return 'bad'",
        "import ctypes\ndef execute(x): return 'bad'",
        "import importlib\ndef execute(x): return 'bad'",
        "import multiprocessing\ndef execute(x): return 'bad'",
        "import threading\ndef execute(x): return 'bad'",
        "import asyncio\ndef execute(x): return 'bad'",
        "import sqlite3\ndef execute(x): return 'bad'",
        "import urllib.request\ndef execute(x): return 'bad'",
        "from os import path\ndef execute(x): return 'bad'",
        "from subprocess import Popen\ndef execute(x): return 'bad'",
    ]
    validator = CodeSandboxValidator()
    for snip in forbidden_snippets:
        report = validator.validate_source(snip)
        assert report.is_safe is False, f"Snippet should have failed: {snip}"
        assert len(report.violations) > 0


def test_validator_rejects_forbidden_builtins():
    snippets = [
        "def execute(x): return eval('1 + 1')",
        "def execute(x): return exec('a = 1')",
        "def execute(x): return compile('1+1', '', 'eval')",
        "def execute(x): return __import__('os').getcwd()",
        "def execute(x): return open('file.txt', 'r').read()",
        "def execute(x): return globals()",
        "def execute(x): return locals()",
        "def execute(x): return vars()",
        "def execute(x): return getattr(x, '__class__')",
        "def execute(x): return setattr(x, 'a', 1)",
    ]
    validator = CodeSandboxValidator()
    for snip in snippets:
        report = validator.validate_source(snip)
        assert report.is_safe is False, f"Snippet should have failed: {snip}"


def test_validator_rejects_dunder_escapes():
    snippets = [
        "def execute(x): return [].__class__.__bases__[0].__subclasses__()",
        "def execute(x): return x.__dict__",
        "def execute(x): return execute.__code__",
        "def execute(x): return execute.__globals__",
        "def execute(x): return execute.__closure__",
        "def execute(x): return x.__class__.__mro__",
    ]
    validator = CodeSandboxValidator()
    for snip in snippets:
        report = validator.validate_source(snip)
        assert report.is_safe is False, f"Dunder snippet should have failed: {snip}"


def test_validator_syntax_error_and_missing_entrypoint():
    validator = CodeSandboxValidator()
    # Syntax error
    rep1 = validator.validate_source("def execute(x) return x")
    assert rep1.is_safe is False
    assert any("syntax error" in v.lower() for v in rep1.violations)

    # Missing entrypoint
    rep2 = validator.validate_source("def other_func(x): return x")
    assert rep2.is_safe is False
    assert any("required entrypoint" in v.lower() for v in rep2.violations)


def test_executor_successful_execution():
    code = """
import math

def execute(input_data: str) -> str:
    n = int(input_data.strip())
    return str(math.factorial(n))
"""
    executor = SandboxedToolExecutor()
    out = executor.execute(code, "5")
    assert out == "120"


def test_executor_timeout_containment():
    code = """
def execute(input_data: str) -> str:
    # Simulates an infinite loop
    while True:
        pass
    return "done"
"""
    executor = SandboxedToolExecutor(default_timeout=0.5)
    with pytest.raises(TimeoutError) as exc_info:
        executor.execute(code, "test", timeout=0.5)
    assert "timed out" in str(exc_info.value).lower()


def test_executor_taint_preservation():
    code = """
def execute(input_data: str) -> str:
    return "Output for: " + input_data
"""
    executor = SandboxedToolExecutor()
    tainted_in = wrap_tainted("untrusted_payload", is_untrusted=True, source_type="external_prompt")
    out = executor.execute(code, tainted_in)
    assert is_tainted(out)
    assert unwrap_tainted(out) == "Output for: untrusted_payload"


def test_executor_output_truncation():
    code = """
def execute(input_data: str) -> str:
    return "A" * 1000
"""
    executor = SandboxedToolExecutor(max_output_chars=50)
    out = executor.execute(code, "go")
    assert len(out) > 50
    assert "[TRUNCATED_MAX_OUTPUT_SIZE]" in out
