"""
C++ → Python offline rule-based transformer.

Covers ~70% of common C++ patterns:
- Include directives → imports
- std:: types → Python equivalents
- Function definitions
- Class definitions
- Assertions
- Main function
- Variable declarations

Complex constructs (templates, smart pointers, RAII, etc.) emit # TODO comments.
"""
from __future__ import annotations

import re


def transform(code: str) -> str:
    """Transform C++ code to Python using rule-based patterns."""
    lines = code.splitlines()
    output: list[str] = []
    needed_imports: set[str] = set()
    indent_stack: list[int] = []

    for line in lines:
        # Skip empty lines and comments
        if not line.strip():
            output.append("")
            continue

        # Convert C++ comments to Python
        if "//" in line:
            line = line.replace("//", "#", 1)

        # Handle includes → imports
        if line.strip().startswith("#include"):
            transformed, imports = _transform_include(line)
            output.append(transformed)
            needed_imports.update(imports)
            continue

        # Handle namespace using
        if "using namespace" in line or "using std::" in line:
            output.append(_transform_using(line))
            continue

        # Handle main function
        if re.search(r"int\s+main\s*\(", line):
            output.append('if __name__ == "__main__":')
            continue

        # Handle function definitions
        if _is_function_def(line):
            transformed = _transform_function_def(line)
            output.append(transformed)
            continue

        # Handle class definitions
        if line.strip().startswith("class "):
            transformed = _transform_class_def(line)
            output.append(transformed)
            continue

        # Handle range-based for loops
        if "for " in line and ":" in line and "{" not in line:
            line = _transform_range_for(line)

        # Handle braces and indentation
        if "{" in line or "}" in line:
            # Adjust indentation for C++ braces
            line = _handle_braces(line, indent_stack)

        # Transform variable declarations
        if _is_var_declaration(line):
            line = _transform_var_declaration(line)

        # Transform std:: calls
        line = _transform_std_calls(line)

        # Transform iostream operations
        line = _transform_iostream(line)

        # Transform assertions
        if "assert(" in line:
            line = _transform_assert(line)

        # Transform return statements
        if "return" in line:
            line = _transform_return(line)

        # Transform C++ initialization lists
        line = _transform_init_list(line)

        output.append(line)

    result = "\n".join(output)

    # Add imports at the top
    if needed_imports:
        import_lines = sorted(needed_imports)
        result = "\n".join(import_lines) + "\n\n" + result

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Include/Import transformation
# ─────────────────────────────────────────────────────────────────────────────

_INCLUDE_MAP: dict[str, list[str]] = {
    "iostream": ["import sys"],
    "vector": ["from typing import List"],
    "string": [],  # str is built-in
    "map": ["from typing import Dict"],
    "set": [],  # set is built-in
    "list": ["from typing import List"],
    "deque": ["from collections import deque"],
    "queue": ["from collections import deque"],
    "stack": [],  # Can use list as stack
    "algorithm": [],  # Python has built-in functions
    "cassert": ["# assert is built-in"],
    "cmath": ["import math"],
    "cstdlib": [],
    "cctype": [],  # Use str methods instead
    "cstring": [],  # Use str methods instead
    "functional": [],  # Use lambda/functools
    "memory": [],  # Python has automatic memory management
    "utility": [],  # Various utilities
    "stdexcept": [],  # Use built-in exceptions
}


def _transform_include(line: str) -> tuple[str, set[str]]:
    """Transform #include to Python imports."""
    # Extract header name
    match = re.search(r'#include\s*[<"]([^>"]+)[>"]', line)
    if not match:
        return f"# {line}", set()

    header = match.group(1)
    # Remove .h extension if present
    header = header.replace(".h", "").replace(".hpp", "").replace(".hxx", "")

    imports = _INCLUDE_MAP.get(header, [])
    if not imports:
        return f"# {line}  # TODO: verify equivalent import", set()

    return f"# {line}", set(imports)


def _transform_using(line: str) -> str:
    """Transform using namespace std; → pass."""
    if "using namespace" in line or "using std::" in line:
        return "# " + line
    return line


# ─────────────────────────────────────────────────────────────────────────────
# Function and class transformation
# ─────────────────────────────────────────────────────────────────────────────

_FUNC_DEF_RE = re.compile(
    r"^\s*(static\s+)?(\w+(?:::)?[\w:]*)\s+(\w+)\s*\((.*?)\)\s*(?:const)?\s*(?:noexcept)?\s*(?:{|;)",
    re.VERBOSE,
)


def _is_function_def(line: str) -> bool:
    """Check if line is a function definition."""
    # Simple heuristic: contains type, name, and parens
    stripped = line.strip()
    # Skip if it's just a function call (no return type visible)
    if stripped.startswith("return ") or stripped.startswith("if ") or stripped.startswith("for "):
        return False
    # Check for function pattern
    return bool(re.search(r"^\s*[\w:]+\s+\w+\s*\(", line))


def _transform_function_def(line: str) -> str:
    """Transform C++ function definition to Python."""
    # Remove return type, keep name and params
    # int add(int a, int b) → def add(a, b):
    match = re.search(r"(\w+)\s*\((.*?)\)\s*(?:const)?\s*(?:noexcept)?", line)
    if not match:
        return line

    func_name = match.group(1)
    params_str = match.group(2)

    # Parse parameters (remove types)
    params = _extract_params(params_str)

    return f"def {func_name}({', '.join(params)}):"


def _extract_params(params_str: str) -> list[str]:
    """Extract parameter names from C++ parameter list."""
    if not params_str.strip():
        return []

    # Split by comma, but respect nested brackets
    params = []
    current = ""
    depth = 0

    for char in params_str:
        if char in "<(":
            depth += 1
        elif char in ">)":
            depth -= 1
        elif char == "," and depth == 0:
            params.append(current.strip())
            current = ""
            continue
        current += char

    if current.strip():
        params.append(current.strip())

    # Extract just the variable names (last word after type)
    result = []
    for param in params:
        # Handle default values: int x = 5 → x
        param = param.split("=")[0].strip()
        # Get the last word (the variable name)
        words = param.split()
        if words:
            result.append(words[-1])

    return result


def _transform_class_def(line: str) -> str:
    """Transform class definition."""
    # class MyClass : public Base { → class MyClass:
    match = re.search(r"class\s+(\w+)\s*(?::.*)?", line)
    if match:
        class_name = match.group(1)
        return f"class {class_name}:"
    return line


# ─────────────────────────────────────────────────────────────────────────────
# Statement transformation
# ─────────────────────────────────────────────────────────────────────────────

def _handle_braces(line: str, indent_stack: list[int]) -> str:
    """Handle C++ braces → Python indentation."""
    # Remove closing braces, adjust indent
    if "}" in line and "{" not in line:
        return line.replace("}", "").rstrip()
    if "{" in line:
        line = line.replace("{", ":").rstrip()
    return line


_VAR_DECL_RE = re.compile(
    r"^\s*(const\s+)?([\w:]+(?:<[^>]+(?:<[^>]+>)?[^>]*>)?)\s+(\w+)\s*(?:=\s*(.+?))?;?$"
)


def _is_var_declaration(line: str) -> bool:
    """Check if line is a variable declaration."""
    stripped = line.strip()
    # Skip if it's an assignment in a condition or loop
    if stripped.startswith("if ") or stripped.startswith("for ") or stripped.startswith("while "):
        return False
    # Look for type name pattern
    return bool(re.search(r"^\s*(const\s+)?\w+[\w:<>,\s]*\s+\w+\s*(?:=|;)", line))


def _transform_var_declaration(line: str) -> str:
    """Transform variable declaration."""
    # int x = 5; → x = 5
    # std::vector<int> v; → v = []
    stripped = line.strip()

    # Simple regex to match: [const] type var [= value] [;]
    # Handle template types with nested brackets
    match = re.match(
        r"(const\s+)?([\w:]+(?:<[^<>]*(?:<[^<>]*>[^<>]*)?>)?)\s+(\w+)\s*(?:=\s*(.+?))?;?$",
        stripped,
    )
    if not match:
        return line

    type_str = match.group(2)
    var_name = match.group(3)
    value = match.group(4)

    # Infer Python type and default value
    # NOTE: Order matters! Check longer/more specific types first to avoid partial matches
    type_map = [
        ("std::vector", "[]"),
        ("std::map", "{}"),
        ("std::set", "set()"),
        ("std::string", '""'),
        ("vector", "[]"),
        ("map", "{}"),
        ("set", "set()"),
        ("double", "0.0"),
        ("float", "0.0"),
        ("bool", "False"),
        ("char", '""'),
        ("int", "0"),
    ]

    # Get default value
    if value:
        value = _transform_std_calls(value).strip(";")
    else:
        # Infer from type - check for patterns (longest first)
        value = None
        for key, default in type_map:
            if key in type_str:
                value = default
                break

        if value is None:
            value = "None  # TODO: infer default for type " + type_str

    indent = len(line) - len(line.lstrip())
    return " " * indent + f"{var_name} = {value}"


def _transform_std_calls(line: str) -> str:
    """Transform std:: function calls."""
    # std::vector<int> → list
    line = re.sub(r"std::vector\s*<([^>]+)>", r"list[\1]", line)
    line = re.sub(r"std::map\s*<([^>]+)>", r"dict", line)
    line = re.sub(r"std::set\s*<([^>]+)>", r"set", line)
    line = re.sub(r"std::string", "str", line)
    line = re.sub(r"std::pair\s*<([^>]+)>", r"tuple", line)

    # std::cout, std::cin, std::cerr
    line = re.sub(r"std::cout", "print", line)
    line = re.sub(r"std::cin", "input", line)
    line = re.sub(r"std::cerr", "sys.stderr.write", line)

    # std::max, std::min
    line = re.sub(r"std::max", "max", line)
    line = re.sub(r"std::min", "min", line)
    line = re.sub(r"std::abs", "abs", line)

    # Remove std:: prefix if still present
    line = line.replace("std::", "")

    return line


def _transform_iostream(line: str) -> str:
    """Transform C++ iostream operations."""
    # cout << x << endl; → print(x)
    if "<<" in line and ("cout" in line or "cerr" in line):
        # Simple case: cout << value << endl;
        match = re.search(r"(cout|cerr)\s*<<\s*(.+?)\s*(?:<<\s*endl)?;?$", line)
        if match:
            stream = match.group(1)
            expr = match.group(2).strip()
            # Remove << endl at end
            expr = re.sub(r"\s*<<\s*endl\s*$", "", expr).strip()
            # Handle multiple <<
            if "<<" in expr:
                parts = [p.strip() for p in expr.split("<<")]
                expr = ', '.join(parts)
            indent = len(line) - len(line.lstrip())
            func = "print" if stream == "cout" else "sys.stderr.write"
            return " " * indent + f"{func}({expr})"

    # cin >> x; → x = input()
    if ">>" in line and "cin" in line:
        match = re.search(r"cin\s*>>\s*(\w+)", line)
        if match:
            var_name = match.group(1)
            indent = len(line) - len(line.lstrip())
            return " " * indent + f"{var_name} = input()"

    return line


def _transform_assert(line: str) -> str:
    """Transform C++ assert to Python assert."""
    # assert(x == 5); → assert x == 5
    line = re.sub(r"assert\(\s*(.+?)\s*\)\s*;?", r"assert \1", line)
    return line


def _transform_return(line: str) -> str:
    """Transform return statements."""
    # return x; → return x
    # But avoid transforming inside strings or comments
    if "#" in line:
        # Don't touch comment portion
        code_part = line.split("#")[0]
        comment_part = "#" + "#".join(line.split("#")[1:])
        code_part = re.sub(r"return\s+(.+?)\s*;$", r"return \1", code_part)
        return code_part + comment_part
    else:
        line = re.sub(r"return\s+(.+?)\s*;$", r"return \1", line)
    return line


def _transform_range_for(line: str) -> str:
    """Transform C++ range-based for loops to Python."""
    # for (type var : container) → for var in container:
    match = re.search(r"for\s*\(\s*(?:const\s+)?(?:auto|[\w:]+)\s+(\w+)\s*:\s*(\w+)\s*\)", line)
    if match:
        var_name = match.group(1)
        container = match.group(2)
        indent = len(line) - len(line.lstrip())
        return " " * indent + f"for {var_name} in {container}:"
    return line


def _transform_init_list(line: str) -> str:
    """Transform C++ initialization lists to Python lists."""
    # {"item1", "item2"} → ["item1", "item2"]
    # Preserve if it's inside a comment
    if "#" in line:
        code_part = line.split("#")[0]
        comment_part = "#" + "#".join(line.split("#")[1:])
        code_part = code_part.replace("{", "[").replace("}", "]")
        return code_part + comment_part
    else:
        line = line.replace("{", "[").replace("}", "]")
    return line
