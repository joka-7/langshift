"""
Behaviour tests for the C++ → Python offline transformer.

These translate C++, then *run* the Python and compare its output to what the
C++ would have printed. The previous suite asserted on private helpers
(`_handle_braces`, `_extract_params`, ...) and was fully green while
`transform()` produced Python that did not parse — every one of the twelve
programs below failed. Testing the contract instead of the pieces is the point.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from repo_translator.offline.cpp_to_py import transform


def _run_python(source: str) -> str:
    """Execute translated Python, returning stdout (or raising with stderr)."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(source)
        path = f.name
    try:
        result = subprocess.run(
            [sys.executable, path], capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"translated Python failed:\n{result.stderr}\n--- source ---\n{source}"
            )
        return result.stdout.strip()
    finally:
        Path(path).unlink(missing_ok=True)


def _translate_and_run(cpp: str) -> str:
    python = transform(cpp)
    try:
        ast.parse(python)
    except SyntaxError as exc:
        raise AssertionError(
            f"translated Python does not parse ({exc.msg}):\n{python}"
        ) from exc
    return _run_python(python)


# Each case is (name, C++ source, exactly what running it should print).
_PROGRAMS: list[tuple[str, str, str]] = [
    ("hello_world",
     '#include <iostream>\nint main() { std::cout << "hi" << std::endl; return 0; }\n',
     "hi"),
    ("arithmetic",
     '#include <iostream>\nint main() { int a = 2; int b = 3; std::cout << a + b << std::endl; return 0; }\n',
     "5"),
    ("function_call",
     '#include <iostream>\nint add(int a, int b) { return a + b; }\nint main() { std::cout << add(2, 3) << std::endl; return 0; }\n',
     "5"),
    ("recursion",
     '#include <iostream>\nint fact(int n) { if (n <= 1) { return 1; } return n * fact(n - 1); }\nint main() { std::cout << fact(5) << std::endl; return 0; }\n',
     "120"),
    ("vector_literal",
     '#include <iostream>\n#include <vector>\nint main() { std::vector<int> v = {1, 2, 3}; std::cout << v[0] << std::endl; return 0; }\n',
     "1"),
    ("vector_push_back",
     '#include <iostream>\n#include <vector>\nint main() { std::vector<int> v; v.push_back(4); v.push_back(5); std::cout << v.size() << std::endl; return 0; }\n',
     "2"),
    ("counted_for",
     '#include <iostream>\nint main() { for (int i = 0; i < 3; i++) { std::cout << i << std::endl; } return 0; }\n',
     "0\n1\n2"),
    ("range_for",
     '#include <iostream>\n#include <vector>\nint main() { std::vector<int> v = {5, 6, 7}; int s = 0; for (int x : v) { s += x; } std::cout << s << std::endl; return 0; }\n',
     "18"),
    ("nested_loops",
     '#include <iostream>\nint main() { for (int i = 0; i < 2; i++) { for (int j = 0; j < 2; j++) { std::cout << i * 2 + j << std::endl; } } return 0; }\n',
     "0\n1\n2\n3"),
    ("if_elseif_else",
     '#include <iostream>\nint main() { int x = 2; if (x == 1) { std::cout << "one" << std::endl; } else if (x == 2) { std::cout << "two" << std::endl; } else { std::cout << "other" << std::endl; } return 0; }\n',
     "two"),
    ("while_accumulate",
     '#include <iostream>\nint main() { int i = 1; int t = 0; while (i <= 4) { t += i; i++; } std::cout << t << std::endl; return 0; }\n',
     "10"),
    ("boolean_logic",
     '#include <iostream>\nint main() { bool a = true; bool b = false; if (a && !b) { std::cout << "yes" << std::endl; } return 0; }\n',
     "yes"),
    ("string_concat",
     '#include <iostream>\n#include <string>\nint main() { std::string a = "foo"; std::string b = "bar"; std::cout << a + b << std::endl; return 0; }\n',
     "foobar"),
    ("map_subscript",
     '#include <iostream>\n#include <map>\nint main() { std::map<std::string, int> m; m["a"] = 1; std::cout << m["a"] << std::endl; return 0; }\n',
     "1"),
    ("mixed_output",
     '#include <iostream>\nint main() { int n = 7; std::cout << "n=" << n << std::endl; return 0; }\n',
     "n=7"),
    ("comments_stripped",
     '#include <iostream>\n// leading\nint main() { /* inline */ std::cout << "ok" << std::endl; // trailing\nreturn 0; }\n',
     "ok"),
    ("class_field_and_method",
     '#include <iostream>\nclass Point {\npublic:\n  int x;\n  int getX() { return x; }\n};\nint main() { Point p; p.x = 7; std::cout << p.getX() << std::endl; return 0; }\n',
     "7"),
    ("class_constructor",
     '#include <iostream>\nclass C {\npublic:\n  int v;\n  C(int a) { v = a; }\n  int get() { return v; }\n};\nint main() { C c(9); std::cout << c.get() << std::endl; return 0; }\n',
     "9"),
    ("early_return",
     '#include <iostream>\nint f(int n) { if (n > 0) { return 1; } return -1; }\nint main() { std::cout << f(5) << std::endl; std::cout << f(-5) << std::endl; return 0; }\n',
     "1\n-1"),
    ("float_division",
     '#include <iostream>\nint main() { double d = 7.0 / 2.0; std::cout << d << std::endl; return 0; }\n',
     "3.5"),
]


class TestTransform:
    """transform(): C++ in, runnable and behaviourally equivalent Python out."""

    @pytest.mark.parametrize(
        "cpp,expected",
        [pytest.param(c, e, id=n) for n, c, e in _PROGRAMS],
    )
    def test_translated_program_produces_the_same_output(self, cpp: str, expected: str) -> None:
        assert _translate_and_run(cpp) == expected


    @pytest.mark.parametrize(
        "cpp,expected",
        [
            pytest.param(
                '#include <iostream>\nint main() { for (int i = 3; i > 0; i--) { std::cout << i << std::endl; } return 0; }\n',
                "3\n2\n1", id="decrementing_for"),
            pytest.param(
                '#include <iostream>\nint main() { for (int i = 0; i <= 2; i++) { std::cout << i << std::endl; } return 0; }\n',
                "0\n1\n2", id="inclusive_bound_for"),
            pytest.param(
                '#include <iostream>\nint main() { for (int i = 0; i < 6; i += 2) { std::cout << i << std::endl; } return 0; }\n',
                "0\n2\n4", id="strided_for"),
            pytest.param(
                '#include <iostream>\nint main() { int i = 5; --i; std::cout << i << std::endl; return 0; }\n',
                "4", id="pre_decrement"),
            pytest.param(
                '#include <iostream>\n#include <vector>\nint main() { std::vector<int> v = {1,2}; v.pop_back(); std::cout << v.size() << std::endl; return 0; }\n',
                "1", id="pop_back"),
            pytest.param(
                '#include <iostream>\n#include <vector>\nint main() { std::vector<int> v; if (v.empty()) { std::cout << "empty" << std::endl; } return 0; }\n',
                "empty", id="empty_check"),
            pytest.param(
                '#include <iostream>\n#include <vector>\nint main() { std::vector<int> v = {8,9}; std::cout << v.at(1) << std::endl; return 0; }\n',
                "9", id="at_accessor"),
            pytest.param(
                '#include <iostream>\nint main() { double d = 3.9; std::cout << static_cast<int>(d) << std::endl; return 0; }\n',
                "3", id="static_cast_int"),
            pytest.param(
                '#include <iostream>\nint main() { std::cout << "she said \\"hi\\"" << std::endl; return 0; }\n',
                'she said "hi"', id="escaped_quotes"),
            pytest.param(
                '#include <iostream>\nint main() { assert(1 + 1 == 2); std::cout << "ok" << std::endl; return 0; }\n',
                "ok", id="assert_passes"),
            pytest.param(
                '#include <iostream>\nclass Base {\npublic:\n  int v;\n};\nclass Derived : public Base {\npublic:\n  int twice() { return v * 2; }\n};\nint main() { Derived d; d.v = 4; std::cout << d.twice() << std::endl; return 0; }\n',
                "8", id="inheritance"),
            pytest.param(
                '#include <iostream>\n#include <cmath>\nint main() { std::cout << "x" << std::endl; return 0; }\n',
                "x", id="cmath_include"),
            pytest.param(
                '#include <iostream>\n#define MAX 10\n#pragma once\nint main() { std::cout << "y" << std::endl; return 0; }\n',
                "y", id="preprocessor_ignored"),
            pytest.param(
                '#include <iostream>\nint main() { int a = 1; int b = 2; std::cout << a << "," << b << std::endl; return 0; }\n',
                "1,2", id="multi_operand_output"),
            pytest.param(
                '#include <iostream>\nint main() { std::cout << "no newline"; std::cout << "!" << std::endl; return 0; }\n',
                "no newline!", id="output_without_endl"),
        ],
    )
    def test_more_constructs_translate_and_run(self, cpp: str, expected: str) -> None:
        assert _translate_and_run(cpp) == expected

    @pytest.mark.parametrize(
        "cpp,marker",
        [
            pytest.param("int main() { do { x++; } while (x < 3); return 0; }\n",
                         "do-while", id="do_while"),
            pytest.param("int main() { int* p = new int(5); delete p; return 0; }\n",
                         "Python manages memory", id="delete"),
        ],
    )
    def test_constructs_without_a_python_equivalent_are_marked(self, cpp: str, marker: str) -> None:
        """These have no faithful translation. They must leave a marker and still parse."""
        python = transform(cpp)
        ast.parse(python)
        assert marker in python

    def test_cin_becomes_input(self) -> None:
        python = transform('#include <iostream>\nint main() { int n; std::cin >> n; return 0; }\n')
        ast.parse(python)
        assert "input()" in python

    def test_try_catch_translates_to_python_exception_handling(self) -> None:
        python = transform(
            'int main() { try { f(); } catch (std::exception e) { g(); } return 0; }\n'
        )
        ast.parse(python)
        assert "try:" in python and "except Exception:" in python

    def test_namespace_block_does_not_add_indentation(self) -> None:
        """A namespace has no Python counterpart; its contents stay top-level."""
        python = transform("namespace app { int add(int a, int b) { return a + b; } }\n")
        ast.parse(python)
        assert "def add(a, b):" in python
        assert not python.startswith(" ")

    def test_untranslatable_construct_is_flagged_not_dropped(self) -> None:
        """A construct with no Python equivalent must leave a visible marker —
        silently emitting nothing would look like a clean translation."""
        python = transform("int main() { switch (x) { case 1: break; } return 0; }\n")
        assert "TODO(cpp)" in python
        ast.parse(python)

    def test_string_literals_survive_operator_rewriting(self) -> None:
        """`&&` becomes `and`, but not inside a string."""
        out = _translate_and_run(
            '#include <iostream>\nint main() { std::cout << "a && b" << std::endl; return 0; }\n'
        )
        assert out == "a && b"

    def test_output_is_syntactically_valid_even_when_empty(self) -> None:
        ast.parse(transform(""))
