"""Milestone 26: AST Security Sandbox & Static Code Verification Engine.

Provides AST-based static code analysis, forbidden module/dunder blocking,
standard library whitelisting, and isolated bounded execution for dynamically synthesized tools.
"""

from __future__ import annotations

import ast
import collections
import concurrent.futures
import copy
import dataclasses
import datetime
import decimal
import enum
import fractions
import hashlib
import itertools
import json
import logging
import math
import re
import string
import time
import typing
import urllib
import urllib.parse
import uuid
from typing import Any, Callable

from core.provenance import TaintedValue, is_tainted, unwrap_tainted, wrap_tainted
from core.skill_types import SecurityAuditReport, compute_code_hash

logger = logging.getLogger("aura.code_sandbox")

# Strictly allowed standard library modules for dynamic synthesis
ALLOWED_STDLIB_MODULES = frozenset({
    "math",
    "re",
    "json",
    "datetime",
    "collections",
    "itertools",
    "string",
    "typing",
    "dataclasses",
    "urllib.parse",
    "decimal",
    "fractions",
    "hashlib",
    "uuid",
    "copy",
    "enum",
})

# Explicitly forbidden dangerous modules
FORBIDDEN_MODULES = frozenset({
    "os",
    "sys",
    "subprocess",
    "socket",
    "shutil",
    "ctypes",
    "importlib",
    "multiprocessing",
    "threading",
    "builtins",
    "posix",
    "nt",
    "pty",
    "commands",
    "platform",
    "signal",
    "tempfile",
    "pathlib",
    "inspect",
    "pdb",
    "gc",
    "code",
    "codeop",
    "dis",
    "pickle",
    "shelve",
    "dbm",
    "sqlite3",
    "asyncio",
    "http",
    "urllib.request",
    "urllib.error",
    "urllib.robotparser",
    "xmlrpc",
    "ftplib",
    "smtplib",
    "poplib",
    "imaplib",
    "telnetlib",
    "webbrowser",
    "posixpath",
    "ntpath",
    "genericpath",
})

# Forbidden built-in call / identifier names
FORBIDDEN_BUILTINS = frozenset({
    "eval",
    "exec",
    "compile",
    "__import__",
    "open",
    "input",
    "globals",
    "locals",
    "vars",
    "breakpoint",
    "memoryview",
    "getattr",
    "setattr",
    "delattr",
    "hasattr",
})

# Forbidden dunder attributes that could lead to sandbox escape or introspection
FORBIDDEN_DUNDER_ATTRS = frozenset({
    "__class__",
    "__bases__",
    "__subclasses__",
    "__mro__",
    "__globals__",
    "__code__",
    "__closure__",
    "__builtins__",
    "__import__",
    "__dict__",
    "__module__",
    "__qualname__",
    "__wrapped__",
    "__loader__",
    "__spec__",
})


# Safe builtins dictionary for sandbox execution environment
def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Restricted __import__ implementation enforcing module whitelist."""
    mod_root = name.split(".")[0].strip().lower()
    full_mod = name.strip().lower()
    if mod_root in FORBIDDEN_MODULES or full_mod in FORBIDDEN_MODULES:
        raise ImportError(f"Import of forbidden module '{name}' is blocked by sandbox policy.")
    if mod_root not in ALLOWED_STDLIB_MODULES and full_mod not in ALLOWED_STDLIB_MODULES:
        raise ImportError(f"Import of unauthorized module '{name}' is blocked by sandbox policy.")
    return __import__(name, globals, locals, fromlist, level)

SAFE_BUILTINS: dict[str, Any] = {
    "__import__": _safe_import,
    "abs": abs,
    "all": all,
    "any": any,
    "ascii": ascii,
    "bin": bin,
    "bool": bool,
    "bytes": bytes,
    "bytearray": bytearray,
    "chr": chr,
    "complex": complex,
    "dict": dict,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "format": format,
    "frozenset": frozenset,
    "hash": hash,
    "hex": hex,
    "int": int,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "iter": iter,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "next": next,
    "oct": oct,
    "ord": ord,
    "pow": pow,
    "print": lambda *args, **kwargs: None,  # Suppress direct stdout pollution
    "range": range,
    "repr": repr,
    "reversed": reversed,
    "round": round,
    "set": set,
    "slice": slice,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "zip": zip,
    "None": None,
    "True": True,
    "False": False,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "AttributeError": AttributeError,
    "Exception": Exception,
    "RuntimeError": RuntimeError,
    "ZeroDivisionError": ZeroDivisionError,
    "ArithmeticError": ArithmeticError,
    "LookupError": LookupError,
    "AssertionError": AssertionError,
}


class ASTSecurityPolicyVisitor(ast.NodeVisitor):
    """AST visitor that checks syntax nodes against strict security invariants."""

    def __init__(self):
        self.violations: list[str] = []
        self.allowed_imports: set[str] = set()
        self.node_count: int = 0
        self.complexity_score: int = 0
        self.defined_functions: set[str] = set()

    def generic_visit(self, node: ast.AST) -> None:
        self.node_count += 1
        super().generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.node_count += 1
        for alias in node.names:
            mod_root = alias.name.split(".")[0].strip().lower()
            full_mod = alias.name.strip().lower()
            if mod_root in FORBIDDEN_MODULES or full_mod in FORBIDDEN_MODULES:
                self.violations.append(f"Forbidden module import: '{alias.name}'")
            elif mod_root in ALLOWED_STDLIB_MODULES or full_mod in ALLOWED_STDLIB_MODULES:
                self.allowed_imports.add(alias.name)
            else:
                self.violations.append(f"Unauthorized module import not in whitelist: '{alias.name}'")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.node_count += 1
        if node.module:
            mod_root = node.module.split(".")[0].strip().lower()
            full_mod = node.module.strip().lower()
            if mod_root in FORBIDDEN_MODULES or full_mod in FORBIDDEN_MODULES:
                self.violations.append(f"Forbidden module import from: '{node.module}'")
            elif mod_root in ALLOWED_STDLIB_MODULES or full_mod in ALLOWED_STDLIB_MODULES:
                self.allowed_imports.add(node.module)
            else:
                self.violations.append(f"Unauthorized module import from not in whitelist: '{node.module}'")
        else:
            self.violations.append("Relative imports without module name are forbidden.")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self.node_count += 1
        self.complexity_score += 1
        # Check direct calls like eval(), exec()
        if isinstance(node.func, ast.Name):
            func_name = node.func.id.strip().lower()
            if func_name in FORBIDDEN_BUILTINS:
                self.violations.append(f"Forbidden built-in function call: '{node.func.id}'")
        # Check method calls like obj.__subclasses__()
        elif isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr.strip().lower()
            if attr_name in FORBIDDEN_DUNDER_ATTRS or attr_name.startswith("__"):
                self.violations.append(f"Forbidden dunder method invocation: '{node.func.attr}'")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.node_count += 1
        attr_name = node.attr.strip().lower()
        if attr_name in FORBIDDEN_DUNDER_ATTRS:
            self.violations.append(f"Forbidden dunder attribute access: '{node.attr}'")
        elif attr_name.startswith("__") and attr_name.endswith("__"):
            self.violations.append(f"Forbidden dunder attribute access: '{node.attr}'")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.node_count += 1
        self.complexity_score += 2
        self.defined_functions.add(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.node_count += 1
        self.violations.append("Async function definitions are not permitted in synchronous sandbox.")
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.node_count += 1
        self.violations.append("Global statements are not permitted in sandboxed functions.")
        self.generic_visit(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.node_count += 1
        self.violations.append("Nonlocal statements are not permitted in sandboxed functions.")
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.node_count += 1
        self.complexity_score += 2
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.node_count += 1
        self.complexity_score += 3
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self.node_count += 1
        self.complexity_score += 1
        self.generic_visit(node)


class CodeSandboxValidator:
    """Static AST security validator for dynamically synthesized code."""

    def __init__(self, max_complexity_score: int = 100, max_code_bytes: int = 32768):
        self.max_complexity_score = max_complexity_score
        self.max_code_bytes = max_code_bytes

    def validate_source(
        self,
        source_code: str,
        entrypoint_function: str = "execute",
    ) -> SecurityAuditReport:
        """Parse source code, run AST security policy visitor, and produce SecurityAuditReport."""
        if not isinstance(source_code, str):
            raise TypeError("source_code must be a string.")

        if len(source_code.encode("utf-8")) > self.max_code_bytes:
            return SecurityAuditReport(
                is_safe=False,
                ast_hash=compute_code_hash(source_code[:100]),
                violations=(f"Source code exceeds maximum allowed size ({self.max_code_bytes} bytes).",),
            )

        ast_hash = compute_code_hash(source_code)

        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            return SecurityAuditReport(
                is_safe=False,
                ast_hash=ast_hash,
                violations=(f"Syntax error during AST parsing: {e}",),
            )

        visitor = ASTSecurityPolicyVisitor()
        visitor.visit(tree)

        violations = list(visitor.violations)

        if entrypoint_function not in visitor.defined_functions:
            violations.append(
                f"Required entrypoint function '{entrypoint_function}' is not defined in source code."
            )

        if visitor.complexity_score > self.max_complexity_score:
            violations.append(
                f"AST complexity score ({visitor.complexity_score}) exceeds threshold ({self.max_complexity_score})."
            )

        is_safe = len(violations) == 0

        return SecurityAuditReport(
            is_safe=is_safe,
            ast_hash=ast_hash,
            violations=tuple(violations),
            allowed_imports=tuple(sorted(visitor.allowed_imports)),
            complexity_score=visitor.complexity_score,
            checked_nodes_count=visitor.node_count,
            metadata={
                "defined_functions": list(sorted(visitor.defined_functions)),
                "source_length": len(source_code),
            },
        )


class SandboxedToolExecutor:
    """Executes validated Python tool code in a bounded, isolated sandbox dictionary scope."""

    def __init__(
        self,
        validator: CodeSandboxValidator | None = None,
        default_timeout: float = 2.0,
        max_output_chars: int = 65536,
    ):
        self.validator = validator if validator is not None else CodeSandboxValidator()
        self.default_timeout = max(0.1, float(default_timeout))
        self.max_output_chars = max(100, int(max_output_chars))

    def _prepare_sandbox_globals(self) -> dict[str, Any]:
        """Construct the restricted global dictionary with safe builtins and standard modules."""
        sandbox_globals: dict[str, Any] = {
            "__builtins__": dict(SAFE_BUILTINS),
            "math": math,
            "re": re,
            "json": json,
            "datetime": datetime,
            "collections": collections,
            "itertools": itertools,
            "string": string,
            "typing": typing,
            "dataclasses": dataclasses,
            "urllib": urllib,
            "decimal": decimal,
            "fractions": fractions,
            "hashlib": hashlib,
            "uuid": uuid,
            "copy": copy,
            "enum": enum,
        }
        return sandbox_globals

    def execute(
        self,
        source_code: str,
        input_data: str,
        entrypoint_function: str = "execute",
        timeout: float | None = None,
    ) -> str:
        """Execute the synthesized tool inside the isolated sandbox."""
        if not isinstance(source_code, str) or not source_code.strip():
            raise ValueError("source_code must be a non-empty string.")

        # Taint preservation check
        was_tainted = is_tainted(input_data)
        raw_input = unwrap_tainted(input_data) if was_tainted else input_data
        if not isinstance(raw_input, str):
            raw_input = str(raw_input)

        # 1. Validate AST Security
        report = self.validator.validate_source(source_code, entrypoint_function=entrypoint_function)
        if not report.is_safe:
            violation_summary = "; ".join(report.violations)
            raise PermissionError(
                f"SandboxedToolExecutor rejected code execution due to security violations: {violation_summary}"
            )

        effective_timeout = timeout if timeout is not None else self.default_timeout

        def _run() -> str:
            sandbox_globals = self._prepare_sandbox_globals()
            local_scope: dict[str, Any] = {}

            # Compile source code safely
            compiled_code = compile(source_code, "<sandboxed_tool>", "exec")
            exec(compiled_code, sandbox_globals, local_scope)

            fn = local_scope.get(entrypoint_function) or sandbox_globals.get(entrypoint_function)
            if fn is None or not callable(fn):
                raise RuntimeError(
                    f"Entrypoint function '{entrypoint_function}' could not be located or is not callable."
                )

            res = fn(raw_input)
            res_str = str(res) if res is not None else ""
            if len(res_str) > self.max_output_chars:
                res_str = res_str[: self.max_output_chars] + "... [TRUNCATED_MAX_OUTPUT_SIZE]"
            return res_str

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(_run)
            output = future.result(timeout=effective_timeout)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(
                f"Sandboxed tool execution timed out after {effective_timeout:.2f} seconds."
            )
        except Exception as e:
            if isinstance(e, (PermissionError, TimeoutError)):
                raise
            raise RuntimeError(f"Sandboxed tool execution failed with error: {type(e).__name__}: {e}") from e
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        if was_tainted:
            return wrap_tainted(output, is_untrusted=True, source_type="dynamic_tool")
        return output
