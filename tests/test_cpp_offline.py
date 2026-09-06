"""Unit tests for C++ → Python offline transformer."""

from repo_translator.offline.cpp_to_py import (
    _extract_params,
    _handle_braces,
    _is_function_def,
    _is_var_declaration,
    _transform_assert,
    _transform_class_def,
    _transform_function_def,
    _transform_include,
    _transform_iostream,
    _transform_std_calls,
    _transform_using,
    _transform_var_declaration,
    transform,
)


class TestTransformInclude:
    """Tests for include → import transformation."""

    def test_iostream_include_becomes_import_sys(self) -> None:
        result, imports = _transform_include("#include <iostream>")
        assert "import sys" in imports

    def test_vector_include_becomes_list_typing(self) -> None:
        result, imports = _transform_include("#include <vector>")
        assert "from typing import List" in imports

    def test_string_include(self) -> None:
        result, imports = _transform_include("#include <string>")
        # string is built-in in Python
        assert len(imports) == 0

    def test_unknown_include_commented(self) -> None:
        result, imports = _transform_include("#include <unknown.h>")
        assert "# " in result
        assert "TODO" in result

    def test_quote_style_include(self) -> None:
        result, imports = _transform_include('#include "myheader.h"')
        assert "# " in result


class TestTransformUsing:
    """Tests for using namespace transformation."""

    def test_using_namespace_std(self) -> None:
        result = _transform_using("using namespace std;")
        assert result.startswith("# ")

    def test_using_std_specific(self) -> None:
        result = _transform_using("using std::vector;")
        assert result.startswith("# ")


class TestTransformFunctionDef:
    """Tests for function definition transformation."""

    def test_simple_function(self) -> None:
        result = _transform_function_def("int add(int a, int b) {")
        assert result == "def add(a, b):"

    def test_function_no_params(self) -> None:
        result = _transform_function_def("int main() {")
        assert result == "def main():"

    def test_function_with_const(self) -> None:
        result = _transform_function_def("int getValue() const {")
        assert "def getValue" in result

    def test_void_function(self) -> None:
        result = _transform_function_def("void printMessage(const string& msg) {")
        assert "def printMessage(msg):" in result


class TestExtractParams:
    """Tests for parameter extraction."""

    def test_no_params(self) -> None:
        result = _extract_params("")
        assert result == []

    def test_single_param(self) -> None:
        result = _extract_params("int x")
        assert result == ["x"]

    def test_multiple_params(self) -> None:
        result = _extract_params("int a, int b, string c")
        assert result == ["a", "b", "c"]

    def test_params_with_defaults(self) -> None:
        result = _extract_params("int x = 5, int y = 10")
        assert result == ["x", "y"]

    def test_const_refs(self) -> None:
        result = _extract_params("const string& name, int age")
        assert result == ["name", "age"]

    def test_template_params(self) -> None:
        result = _extract_params("vector<int> v, map<string, int> m")
        assert "v" in result
        assert "m" in result


class TestTransformClassDef:
    """Tests for class definition transformation."""

    def test_simple_class(self) -> None:
        result = _transform_class_def("class MyClass {")
        assert result == "class MyClass:"

    def test_class_with_inheritance(self) -> None:
        result = _transform_class_def("class Derived : public Base {")
        assert "class Derived:" in result


class TestTransformVarDeclaration:
    """Tests for variable declaration transformation."""

    def test_int_declaration(self) -> None:
        result = _transform_var_declaration("int x = 5;")
        assert "x = 5" in result

    def test_string_declaration(self) -> None:
        result = _transform_var_declaration('std::string name = "Alice";')
        assert 'name = "Alice"' in result or "name = " in result

    def test_vector_declaration(self) -> None:
        result = _transform_var_declaration("std::vector<int> v;")
        assert "v = []" in result

    def test_map_declaration(self) -> None:
        result = _transform_var_declaration("std::map<string, int> m;")
        assert "m = {}" in result

    def test_const_declaration(self) -> None:
        result = _transform_var_declaration("const int MAX = 100;")
        assert "MAX = 100" in result

    def test_uninitialized_declaration(self) -> None:
        result = _transform_var_declaration("int x;")
        assert "x = 0" in result


class TestIsVarDeclaration:
    """Tests for variable declaration detection."""

    def test_recognizes_int_declaration(self) -> None:
        assert _is_var_declaration("int x = 5;")

    def test_recognizes_string_declaration(self) -> None:
        assert _is_var_declaration("std::string name = \"test\";")

    def test_ignores_for_loop(self) -> None:
        assert not _is_var_declaration("for (int i = 0; i < n; ++i)")

    def test_ignores_if_statement(self) -> None:
        assert not _is_var_declaration("if (int x = getValue())")


class TestIsFunctionDef:
    """Tests for function definition detection."""

    def test_recognizes_function_def(self) -> None:
        assert _is_function_def("int add(int a, int b) {")

    def test_recognizes_void_function(self) -> None:
        assert _is_function_def("void printMessage() {")

    def test_ignores_function_call(self) -> None:
        assert not _is_function_def("add(5, 3);")

    def test_ignores_return_statement(self) -> None:
        assert not _is_function_def("return value;")


class TestTransformStdCalls:
    """Tests for std:: transformation."""

    def test_vector_to_list(self) -> None:
        result = _transform_std_calls("std::vector<int> v")
        assert "list[int]" in result

    def test_string_to_str(self) -> None:
        result = _transform_std_calls("std::string s")
        assert "str s" in result

    def test_map_to_dict(self) -> None:
        result = _transform_std_calls("std::map<string, int> m")
        assert "dict" in result

    def test_max_function(self) -> None:
        result = _transform_std_calls("std::max(a, b)")
        assert "max(a, b)" in result

    def test_min_function(self) -> None:
        result = _transform_std_calls("std::min(x, y)")
        assert "min(x, y)" in result

    def test_cout_to_print(self) -> None:
        result = _transform_std_calls("std::cout")
        assert "print" in result

    def test_cin_to_input(self) -> None:
        result = _transform_std_calls("std::cin")
        assert "input" in result


class TestTransformIOStream:
    """Tests for iostream operation transformation."""

    def test_cout_with_endl(self) -> None:
        result = _transform_iostream('cout << "hello" << endl;')
        assert "print(" in result
        assert '"hello"' in result

    def test_cout_multiple_values(self) -> None:
        result = _transform_iostream('cout << x << " " << y << endl;')
        assert "print(" in result

    def test_cin_extraction(self) -> None:
        result = _transform_iostream("cin >> x;")
        assert "x = input()" in result


class TestTransformAssert:
    """Tests for assertion transformation."""

    def test_assert_equality(self) -> None:
        result = _transform_assert("assert(x == 5);")
        assert "assert x == 5" in result

    def test_assert_no_semicolon(self) -> None:
        result = _transform_assert("assert(a > b)")
        assert "assert a > b" in result


class TestFullTransformation:
    """Integration tests for full code transformation."""

    def test_simple_program(self) -> None:
        code = """
#include <iostream>
using namespace std;

int main() {
    cout << "Hello, World!" << endl;
    return 0;
}
"""
        result = transform(code)
        assert "if __name__" in result or "main" in result
        assert "print(" in result

    def test_function_with_test(self) -> None:
        code = """
#include <cassert>

int add(int a, int b) {
    return a + b;
}

int main() {
    assert(add(2, 3) == 5);
    return 0;
}
"""
        result = transform(code)
        assert "def add(a, b):" in result
        assert "assert " in result

    def test_vector_and_loop(self) -> None:
        code = """
#include <vector>
#include <iostream>
using namespace std;

int main() {
    vector<int> nums = {1, 2, 3};
    for (int n : nums) {
        cout << n << endl;
    }
    return 0;
}
"""
        result = transform(code)
        assert "nums" in result
        assert "for " in result

    def test_class_definition(self) -> None:
        code = """
class Point {
    int x;
    int y;
};
"""
        result = transform(code)
        assert "class Point:" in result

    def test_preserves_comments(self) -> None:
        code = """
// This is a comment
int x = 5;  // inline comment
"""
        result = transform(code)
        # Comments should be preserved or at least not break transformation
        assert "x" in result

    def test_handles_empty_file(self) -> None:
        result = transform("")
        assert result == ""

    def test_handles_whitespace_only(self) -> None:
        result = transform("   \n\n   ")
        assert result.strip() == ""


class TestHandleBraces:
    """Tests for brace handling."""

    def test_closing_brace_removed(self) -> None:
        stack: list[int] = []
        result = _handle_braces("}", stack)
        assert "}" not in result

    def test_opening_brace_becomes_colon(self) -> None:
        stack: list[int] = []
        result = _handle_braces("def foo() {", stack)
        assert ":" in result
        assert "{" not in result
