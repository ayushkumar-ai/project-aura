"""M53 — Three-Tier Condition Evaluation Engine.

Tier 1: Deterministic local AST predicate evaluation (0 tokens, zero side effects).
Tier 2: Read-only context injection via ReadOnlyContextProvider.
Tier 3: Bounded semantic evaluation via ModelGateway (fail-closed).
"""

from __future__ import annotations

import ast
import json
import logging
from typing import Any

from core.automations.context_provider import ReadOnlyContextProvider
from core.automations.types import AutomationValidationError, ConditionEvaluationError

logger = logging.getLogger("aura.automations.condition")

# Permitted AST nodes for safe deterministic evaluation
ALLOWED_AST_NODES = {
    ast.Expression,
    ast.BoolOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Name,
    ast.Constant,
    ast.Attribute,
    ast.Subscript,
    ast.Index if hasattr(ast, "Index") else type(None),
    ast.Slice if hasattr(ast, "Slice") else type(None),
    ast.Load,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
    ast.List,
    ast.Tuple,
    ast.Dict,
}


class SafeASTVisitor(ast.NodeVisitor):
    """Validates that an AST expression contains only safe, whitelisted operations."""

    def generic_visit(self, node: ast.AST) -> None:
        if type(node) not in ALLOWED_AST_NODES:
            raise AutomationValidationError(
                f"Disallowed AST construct '{type(node).__name__}' in condition predicate. Code execution is strictly forbidden."
            )
        super().generic_visit(node)


def validate_predicate_ast(predicate: str) -> None:
    """Parse and validate predicate expression syntax and safety."""
    if not predicate or not predicate.strip():
        return
    if len(predicate) > 4000:
        raise AutomationValidationError("Predicate length exceeds 4000 characters limit.")
    try:
        parsed = ast.parse(predicate.strip(), mode="eval")
    except SyntaxError as e:
        raise AutomationValidationError(f"Invalid predicate syntax: {e}") from e

    visitor = SafeASTVisitor()
    visitor.visit(parsed)


def _eval_node(node: ast.AST, scope: dict[str, Any]) -> Any:
    """Safely evaluate a validated AST expression tree against a scope dictionary."""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, scope)
    elif isinstance(node, ast.Constant):
        return node.value
    elif isinstance(node, ast.Name):
        if node.id in scope:
            return scope[node.id]
        if node.id == "True":
            return True
        if node.id == "False":
            return False
        if node.id == "None":
            return None
        return None
    elif isinstance(node, ast.Attribute):
        val = _eval_node(node.value, scope)
        if isinstance(val, dict):
            return val.get(node.attr)
        return getattr(val, node.attr, None)
    elif isinstance(node, ast.Subscript):
        val = _eval_node(node.value, scope)
        slice_val = _eval_node(node.slice, scope)
        if isinstance(val, (dict, list, tuple)) and slice_val is not None:
            try:
                return val[slice_val]
            except (KeyError, IndexError, TypeError):
                return None
        return None
    elif isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for v in node.values:
                if not bool(_eval_node(v, scope)):
                    return False
            return True
        elif isinstance(node.op, ast.Or):
            for v in node.values:
                if bool(_eval_node(v, scope)):
                    return True
            return False
    elif isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not bool(_eval_node(node.operand, scope))
    elif isinstance(node, ast.Compare):
        left = _eval_node(node.left, scope)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, scope)
            if isinstance(op, ast.Eq):
                if not (left == right):
                    return False
            elif isinstance(op, ast.NotEq):
                if not (left != right):
                    return False
            elif isinstance(op, ast.Lt):
                if not (left < right):
                    return False
            elif isinstance(op, ast.LtE):
                if not (left <= right):
                    return False
            elif isinstance(op, ast.Gt):
                if not (left > right):
                    return False
            elif isinstance(op, ast.GtE):
                if not (left >= right):
                    return False
            elif isinstance(op, ast.In):
                if right is None or not (left in right):
                    return False
            elif isinstance(op, ast.NotIn):
                if right is not None and (left in right):
                    return False
            elif isinstance(op, ast.Is):
                if not (left is right):
                    return False
            elif isinstance(op, ast.IsNot):
                if not (left is not right):
                    return False
            left = right
        return True
    elif isinstance(node, ast.List):
        return [_eval_node(elt, scope) for elt in node.elts]
    elif isinstance(node, ast.Tuple):
        return tuple(_eval_node(elt, scope) for elt in node.elts)
    elif isinstance(node, ast.Dict):
        return {_eval_node(k, scope): _eval_node(v, scope) for k, v in zip(node.keys, node.values)}

    return None


class ConditionEngine:
    """Evaluates three-tier automation conditions with strict tenant isolation and fail-closed semantics."""

    def __init__(
        self,
        context_provider: ReadOnlyContextProvider | None = None,
        model_gateway: Any | None = None,
    ) -> None:
        self.context_provider = context_provider or ReadOnlyContextProvider()
        self.model_gateway = model_gateway

    def evaluate(
        self,
        user_id: str,
        condition_config: dict[str, Any] | None,
    ) -> tuple[bool, dict[str, Any]]:
        """Evaluate condition configuration for a tenant.
        
        Returns:
            tuple[bool, dict]: (passed, evaluation_metadata)
        """
        if not condition_config:
            # No condition configured -> unconditional pass
            return True, {"tier": 1, "passed": True, "reason": "unconditional"}

        tier = int(condition_config.get("tier", 1))
        predicate = condition_config.get("predicate")
        context_keys = condition_config.get("context_keys", [])
        llm_prompt = condition_config.get("llm_prompt")
        raw_context = self.context_provider.get_context(user_id=user_id, keys=context_keys) if context_keys else {}

        # Tier 1 & 2: AST Predicate Evaluation
        if predicate and predicate.strip():
            validate_predicate_ast(predicate)
            
            # Prepare flattened / structured scope for AST evaluation
            scope: dict[str, Any] = {}
            for k, v in raw_context.items():
                scope[k.replace(".", "_")] = v
                parts = k.split(".")
                curr = scope
                for part in parts[:-1]:
                    if part not in curr or not isinstance(curr[part], dict):
                        curr[part] = {}
                    curr = curr[part]
                curr[parts[-1]] = v

            try:
                parsed = ast.parse(predicate.strip(), mode="eval")
                ast_result = bool(_eval_node(parsed, scope))
            except Exception as e:
                logger.error(f"Error evaluating predicate '{predicate}': {e}")
                return False, {"tier": tier, "passed": False, "error": str(e), "predicate": predicate}

            if not ast_result:
                return False, {
                    "tier": tier,
                    "passed": False,
                    "reason": "predicate_evaluated_false",
                    "predicate": predicate,
                    "context": raw_context,
                }

        # Tier 3: LLM-assisted Evaluation if configured
        if tier == 3 or (llm_prompt and llm_prompt.strip()):
            if not self.model_gateway:
                logger.warning("Tier 3 condition requested but ModelGateway is unavailable. Failing closed.")
                return False, {"tier": 3, "passed": False, "error": "ModelGateway unavailable; fail-closed"}

            prompt_text = (
                f"You are an automated condition evaluator for Project AURA.\n"
                f"Task: Evaluate if the condition is MET based on the context.\n"
                f"Respond with EXACTLY 'YES' if the condition is satisfied, or 'NO' if not.\n\n"
                f"Condition: {llm_prompt}\n"
                f"Context Data:\n{json.dumps(raw_context, default=str)}\n\n"
                f"Answer:"
            )
            try:
                # Call ModelGateway under tenant context
                response = self.model_gateway.generate_response(
                    prompt=prompt_text,
                    user_id=user_id,
                    max_tokens=10,
                    temperature=0.0,
                )
                raw_answer = (response.text if hasattr(response, "text") else str(response)).strip().upper()
                passed = "YES" in raw_answer or "TRUE" in raw_answer
                return passed, {
                    "tier": 3,
                    "passed": passed,
                    "model_response": raw_answer,
                    "prompt": llm_prompt,
                }
            except Exception as e:
                logger.error(f"Tier 3 LLM condition evaluation failed: {e}")
                return False, {"tier": 3, "passed": False, "error": f"LLM evaluation failed: {e}"}

        return True, {"tier": tier, "passed": True, "reason": "predicate_passed"}
