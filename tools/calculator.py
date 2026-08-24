import ast
import operator

from interfaces.tool import ToolInterface


class CalculatorTool(ToolInterface):
    """Safely evaluates basic arithmetic expressions."""

    _operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
    }

    @property
    def description(self) -> str:
        return "Calculator tool"

    @property
    def keywords(self) -> tuple[str, ...]:
        """Return keywords associated with calculator intent."""
        return (
            "calculate",
            "calculation",
            "compute",
            "math",
            "arithmetic",
        )

    def execute(self, input_data: str) -> str:
        expression = input_data.strip()

        if not expression:
            raise ValueError("Calculator input cannot be empty.")

        try:
            tree = ast.parse(expression, mode="eval")
            result = self._evaluate(tree.body)
        except (SyntaxError, ValueError, TypeError, ZeroDivisionError):
            raise ValueError(
                f"Unsupported arithmetic expression: {input_data}"
            )

        if isinstance(result, float) and result.is_integer():
            return str(int(result))

        return str(result)

    def _evaluate(self, node: ast.AST) -> int | float:
        if isinstance(node, ast.Constant) and isinstance(
            node.value, (int, float)
        ):
            return node.value

        if isinstance(node, ast.BinOp) and type(node.op) in self._operators:
            left = self._evaluate(node.left)
            right = self._evaluate(node.right)

            return self._operators[type(node.op)](left, right)

        if isinstance(node, ast.UnaryOp) and isinstance(
            node.op, (ast.UAdd, ast.USub)
        ):
            value = self._evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value

        raise ValueError("Unsupported arithmetic expression.")