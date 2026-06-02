"""A simple calculator module — contains a deliberate bug for demo purposes."""


def add(a: float, b: float) -> float:
    return a + b


def subtract(a: float, b: float) -> float:
    return a - b


def multiply(a: float, b: float) -> float:
    return a * b


def divide(a: float, b: float) -> float:
    """Divide a by b. BUG: uses multiplication instead of division."""
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a * b  # ← BUG: should be a / b
