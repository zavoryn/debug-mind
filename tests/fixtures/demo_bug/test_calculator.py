"""Tests for calculator.py — will FAIL until the divide bug is fixed."""

import pytest
from calculator import add, subtract, multiply, divide


def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0


def test_subtract():
    assert subtract(10, 4) == 6


def test_multiply():
    assert multiply(3, 4) == 12


def test_divide():
    assert divide(10, 2) == 5.0  # FAILS with bug: returns 20.0 instead of 5.0
    assert divide(9, 3) == 3.0
    assert divide(7, 1) == 7.0


def test_divide_by_zero():
    with pytest.raises(ValueError, match="Cannot divide by zero"):
        divide(5, 0)
