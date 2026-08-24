import pytest

from tools.calculator import CalculatorTool


def test_calculator_addition():
    tool = CalculatorTool()

    assert tool.execute("2 + 2") == "4"


def test_calculator_subtraction():
    tool = CalculatorTool()

    assert tool.execute("10 - 3") == "7"


def test_calculator_multiplication():
    tool = CalculatorTool()

    assert tool.execute("4 * 5") == "20"


def test_calculator_division():
    tool = CalculatorTool()

    assert tool.execute("20 / 4") == "5"


def test_calculator_supports_decimal_result():
    tool = CalculatorTool()

    assert tool.execute("5 / 2") == "2.5"


def test_calculator_supports_parentheses():
    tool = CalculatorTool()

    assert tool.execute("(2 + 3) * 4") == "20"


def test_calculator_supports_negative_numbers():
    tool = CalculatorTool()

    assert tool.execute("-5 + 2") == "-3"


def test_calculator_rejects_empty_input():
    tool = CalculatorTool()

    with pytest.raises(ValueError, match="Calculator input cannot be empty"):
        tool.execute("")


def test_calculator_rejects_unsupported_expression():
    tool = CalculatorTool()

    with pytest.raises(ValueError, match="Unsupported arithmetic expression"):
        tool.execute("import os")


def test_calculator_rejects_python_function_calls():
    tool = CalculatorTool()

    with pytest.raises(ValueError, match="Unsupported arithmetic expression"):
        tool.execute("abs(-5)")


def test_calculator_rejects_division_by_zero():
    tool = CalculatorTool()

    with pytest.raises(ValueError, match="Unsupported arithmetic expression"):
        tool.execute("10 / 0")


def test_calculator_has_description():
    tool = CalculatorTool()

    assert tool.description == "Calculator tool"