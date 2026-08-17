"""
Tests for offline rule-based transformer (ts_to_py) and OfflineProvider.
No API calls or network required.
"""
from __future__ import annotations

import pytest

from repo_translator.offline.transformer import OfflineTransformer
from repo_translator.offline.ts_to_py import transform
from repo_translator.providers.offline import OfflineProvider, _extract_code

# ─────────────────────────────────────────────────────────────────────────────
# Imports
# ─────────────────────────────────────────────────────────────────────────────

class TestImports:
    def test_named_import(self):
        r = transform("import { foo, bar } from 'mymodule'")
        assert "from mymodule import foo, bar" in r

    def test_named_import_with_alias(self):
        r = transform("import { readFile as read } from 'fs'")
        assert "from fs import" in r
        assert "read" in r

    def test_star_import(self):
        r = transform("import * as path from 'path'")
        assert "import path as path" in r

    def test_default_import(self):
        r = transform("import React from 'react'")
        assert "from react import React" in r

    def test_type_import_removed(self):
        r = transform("import type { Foo } from './types'")
        assert "type import" in r.lower() or r.startswith("#")

    def test_relative_import(self):
        r = transform("import { helper } from './utils/helper'")
        assert "from" in r and "helper" in r

    def test_side_effect_import(self):
        r = transform("import 'some-polyfill'")
        assert "#" in r   # commented out


# ─────────────────────────────────────────────────────────────────────────────
# Exports
# ─────────────────────────────────────────────────────────────────────────────

class TestExports:
    def test_export_default_function(self):
        r = transform("export default function greet() {}")
        assert "def greet" in r
        assert "export" not in r

    def test_export_named(self):
        r = transform("export const x = 5")
        assert "x = 5" in r
        assert "export" not in r

    def test_export_list(self):
        r = transform("export { foo, bar }")
        assert "__all__" in r

    def test_export_class(self):
        r = transform("export class MyClass {}")
        assert "class MyClass" in r
        assert "export" not in r


# ─────────────────────────────────────────────────────────────────────────────
# CommonJS require() / module.exports
# ─────────────────────────────────────────────────────────────────────────────

class TestCommonJS:
    def test_require_destructured(self):
        r = transform("const { isEven, filterEvens } = require('./utils');")
        assert "from utils import isEven, filterEvens" in r

    def test_require_destructured_with_alias(self):
        r = transform("const { foo: bar } = require('./mod');")
        assert "from mod import foo as bar" in r

    def test_require_default(self):
        r = transform("const utils = require('./utils');")
        assert "import utils as utils" in r

    def test_module_exports_object(self):
        r = transform("module.exports = { isEven, filterEvens };")
        assert "__all__ = ['isEven', 'filterEvens']" in r

    def test_module_exports_default_value_commented(self):
        r = transform("module.exports = MyClass;")
        assert r.strip().startswith("#")
        assert "MyClass" in r


# ─────────────────────────────────────────────────────────────────────────────
# Higher-order array methods
# ─────────────────────────────────────────────────────────────────────────────

class TestHigherOrderArrayMethods:
    def test_filter(self):
        r = transform("const evens = numbers.filter(isEven);")
        assert "list(filter(isEven, numbers))" in r

    def test_map(self):
        r = transform("const doubled = numbers.map(double);")
        assert "list(map(double, numbers))" in r

    def test_filter_inline_arrow(self):
        r = transform("const evens = numbers.filter(a => a > 0);")
        assert "list(filter(lambda a: a > 0, numbers))" in r

    def test_map_inline_arrow_with_nested_call(self):
        r = transform("const balances = accounts.map(a => a.getBalance());")
        assert "list(map(lambda a: a.getBalance(), balances" not in r
        assert "list(map(lambda a: a.getBalance(), accounts))" in r

    def test_reduce_inline_arrow_with_initial_value(self):
        r = transform("const total = numbers.reduce((sum, b) => sum + b, 0);")
        assert "functools.reduce(lambda sum, b: sum + b, numbers, 0)" in r
        assert "import functools" in r

    def test_reduce_without_initial_value(self):
        r = transform("const total = numbers.reduce((sum, b) => sum + b);")
        assert "functools.reduce(lambda sum, b: sum + b, numbers)" in r


# ─────────────────────────────────────────────────────────────────────────────
# Variable declarations
# ─────────────────────────────────────────────────────────────────────────────

class TestVariables:
    def test_const(self):
        r = transform("const x = 5")
        assert "x = 5" in r
        assert "const" not in r

    def test_let(self):
        r = transform("let name = 'world'")
        assert "name = 'world'" in r
        assert "let" not in r

    def test_typed_const(self):
        r = transform("const count: number = 0")
        assert "count" in r and "= 0" in r
        assert "number" not in r
        assert "const" not in r


# ─────────────────────────────────────────────────────────────────────────────
# Functions
# ─────────────────────────────────────────────────────────────────────────────

class TestFunctions:
    def test_simple_function(self):
        r = transform("function add(a, b) {\n    return a + b;\n}")
        assert "def add(a, b):" in r
        assert "return a + b" in r

    def test_typed_function(self):
        r = transform("function greet(name: string): string {\n    return name;\n}")
        assert "def greet(name):" in r
        assert "return name" in r

    def test_async_function(self):
        r = transform("async function fetch(url: string): Promise<string> {\n    return url;\n}")
        assert "async def fetch(url):" in r

    def test_arrow_function_block(self):
        r = transform("const double = (x: number) => {\n    return x * 2;\n}")
        assert "def double(x):" in r

    def test_arrow_function_expression(self):
        r = transform("const square = (x: number) => x * x")
        assert "square" in r and "lambda" in r or "def square" in r

    def test_optional_param(self):
        r = transform("function greet(name?: string) {}")
        assert "name=None" in r


# ─────────────────────────────────────────────────────────────────────────────
# Classes
# ─────────────────────────────────────────────────────────────────────────────

class TestClasses:
    def test_simple_class(self):
        r = transform("class Animal {\n}")
        assert "class Animal:" in r

    def test_class_extends(self):
        r = transform("class Dog extends Animal {\n}")
        assert "class Dog(Animal):" in r

    def test_constructor(self):
        code = "class Foo {\n    constructor(private name: string) {}\n}"
        r = transform(code)
        assert "def __init__(self" in r
        assert "name" in r

    def test_method_gets_self(self):
        code = "class Counter {\n    increment(amount: number) {\n        return amount;\n    }\n}"
        r = transform(code)
        assert "def increment(self, amount):" in r

    def test_static_method(self):
        code = "class Util {\n    static parse(s: string): number {\n        return parseInt(s);\n    }\n}"
        r = transform(code)
        assert "@staticmethod" in r
        assert "def parse(s):" in r

    def test_this_to_self(self):
        r = transform("    this.name = 'Alice'")
        assert "self.name = 'Alice'" in r

    def test_constructor_parameter_property_shorthand(self):
        code = "class Foo {\n    constructor(private name: string, age: number) {}\n}"
        r = transform(code)
        assert "def __init__(self, name, age):" in r
        assert "self.name = name" in r
        assert "private" not in r

    def test_mutable_default_field_hoisted_into_constructor(self):
        code = "class Foo {\n    private items: string[] = [];\n    constructor() {}\n}"
        r = transform(code)
        assert "def __init__(self):" in r
        assert "self.items = []" in r
        # should not also remain as a shared class-level attribute
        assert "items: list" not in r and "items = []  #" not in r

    def test_mutable_default_field_without_constructor_is_flagged(self):
        code = "class Foo {\n    private items: string[] = [];\n    bar() {\n        return 1;\n    }\n}"
        r = transform(code)
        assert "items = []" in r
        assert "WARNING" in r


# ─────────────────────────────────────────────────────────────────────────────
# Interface / type → dataclass
# ─────────────────────────────────────────────────────────────────────────────

class TestInterfaces:
    def test_interface_to_dataclass(self):
        code = "interface Point {\n    x: number;\n    y: number;\n}"
        r = transform(code)
        assert "@dataclass" in r
        assert "class Point:" in r
        assert "x:" in r
        assert "y:" in r

    def test_optional_field_gets_none(self):
        code = "interface Config {\n    debug?: boolean;\n}"
        r = transform(code)
        assert "| None" in r or "= None" in r

    def test_type_alias_to_dataclass(self):
        code = "type User = {\n    name: string;\n    age: number;\n}"
        r = transform(code)
        assert "@dataclass" in r
        assert "class User:" in r

    def test_export_interface(self):
        code = "export interface Shape {\n    area(): number;\n}"
        r = transform(code)
        assert "class Shape:" in r
        assert "export" not in r


# ─────────────────────────────────────────────────────────────────────────────
# Control flow
# ─────────────────────────────────────────────────────────────────────────────

class TestControlFlow:
    def test_if(self):
        r = transform("if (x > 0) {")
        assert "if x > 0:" in r

    def test_else_if(self):
        r = transform("} else if (x < 0) {")
        assert "elif x < 0:" in r

    def test_else(self):
        r = transform("} else {")
        assert "else:" in r

    def test_while(self):
        r = transform("while (running) {")
        assert "while running:" in r

    def test_for_of(self):
        r = transform("for (const item of items) {")
        assert "for item in items:" in r

    def test_for_of_entries(self):
        r = transform("for (const [k, v] of map.entries()) {")
        assert "for k, v in map.items():" in r

    def test_for_range(self):
        r = transform("for (let i = 0; i < 10; i++) {")
        assert "for i in range(10):" in r

    def test_try_catch(self):
        code = "try {\n} catch (err) {\n}"
        r = transform(code)
        assert "try:" in r
        assert "except Exception as err:" in r

    def test_closing_brace_removed(self):
        r = transform("}")
        assert r.strip() == ""


# ─────────────────────────────────────────────────────────────────────────────
# Operators and literals
# ─────────────────────────────────────────────────────────────────────────────

class TestOperators:
    def test_strict_equal(self):
        r = transform("if (a === b) {")
        assert "==" in r and "===" not in r

    def test_strict_not_equal(self):
        r = transform("if (a !== b) {")
        assert "!=" in r and "!==" not in r

    def test_logical_and(self):
        r = transform("const x = a && b")
        assert " and " in r

    def test_logical_or(self):
        r = transform("const x = a || b")
        assert " or " in r

    def test_null_coalescing(self):
        r = transform("const x = val ?? 'default'")
        assert " or " in r

    def test_null_literal(self):
        r = transform("const x = null")
        assert "= None" in r

    def test_undefined_literal(self):
        r = transform("const x = undefined")
        assert "= None" in r

    def test_true_false(self):
        r = transform("const ok = true; const bad = false")
        assert "True" in r
        assert "False" in r


# ─────────────────────────────────────────────────────────────────────────────
# Built-in methods
# ─────────────────────────────────────────────────────────────────────────────

class TestBuiltins:
    def test_console_log(self):
        r = transform("console.log('hello')")
        assert "print(" in r

    def test_parse_int(self):
        r = transform("parseInt('42')")
        assert "int(" in r

    def test_parse_float(self):
        r = transform("parseFloat('3.14')")
        assert "float(" in r

    def test_json_parse(self):
        r = transform("JSON.parse(s)")
        assert "json.loads" in r

    def test_json_stringify(self):
        r = transform("JSON.stringify(obj)")
        assert "json.dumps" in r

    def test_array_push(self):
        r = transform("arr.push(item)")
        assert ".append(item)" in r

    def test_string_trim(self):
        r = transform("str.trim()")
        assert ".strip()" in r

    def test_to_lower(self):
        r = transform("s.toLowerCase()")
        assert ".lower()" in r

    def test_to_upper(self):
        r = transform("s.toUpperCase()")
        assert ".upper()" in r

    def test_length(self):
        r = transform("arr.length")
        assert "len(arr)" in r

    def test_instanceof(self):
        r = transform("x instanceof MyClass")
        assert "isinstance(x, MyClass)" in r

    def test_throw_new_error(self):
        r = transform("throw new Error('oops')")
        assert "raise" in r

    def test_throw_new_error_maps_to_exception(self):
        r = transform("throw new Error('oops')")
        assert "raise Exception('oops')" in r

    def test_bare_new_error_maps_to_exception(self):
        r = transform("const e = new Error('oops')")
        assert "Exception('oops')" in r
        assert "Error(" not in r

    def test_throw_new_custom_error_keeps_class_name(self):
        r = transform("throw new ValidationError('oops')")
        assert "raise ValidationError('oops')" in r

    def test_new_removed(self):
        r = transform("const x = new MyClass()")
        assert "new " not in r

    def test_to_fixed(self):
        r = transform("const s = amount.toFixed(2);")
        assert "format(amount, '.2f')" in r
        assert "toFixed" not in r


# ─────────────────────────────────────────────────────────────────────────────
# Object literals
# ─────────────────────────────────────────────────────────────────────────────

class TestObjectLiterals:
    def test_plain_object_literal_to_dict(self):
        r = transform("const obj = { id: 1, name: 'Alice' };")
        assert "{'id': 1, 'name': 'Alice'}" in r

    def test_typed_object_literal_to_constructor_call(self):
        r = transform("const owner: User = { id: 1, name: 'Alice' };")
        assert "owner = User(id=1, name='Alice')" in r
        assert "{" not in r


# ─────────────────────────────────────────────────────────────────────────────
# Template literals
# ─────────────────────────────────────────────────────────────────────────────

class TestTemplateLiterals:
    def test_simple_template(self):
        r = transform("const msg = `Hello, ${name}!`")
        assert 'f"Hello, {name}!"' in r

    def test_template_with_expression(self):
        r = transform("const s = `Value: ${x + 1}`")
        assert 'f"' in r
        assert '{x + 1}' in r

    def test_template_no_interpolation(self):
        r = transform("const s = `plain string`")
        assert 'f"plain string"' in r


# ─────────────────────────────────────────────────────────────────────────────
# Semicolon removal
# ─────────────────────────────────────────────────────────────────────────────

class TestSemicolons:
    def test_semicolon_removed(self):
        r = transform("const x = 5;")
        assert not r.rstrip().endswith(';')

    def test_multiple_semicolons(self):
        r = transform("let a = 1;\nlet b = 2;")
        assert ';' not in r


# ─────────────────────────────────────────────────────────────────────────────
# Enum
# ─────────────────────────────────────────────────────────────────────────────

class TestEnum:
    def test_enum_to_class(self):
        code = "enum Color {\n    Red = 'red',\n    Blue = 'blue',\n}"
        r = transform(code)
        assert "class Color:" in r
        assert "Red = 'red'" in r
        assert "Blue = 'blue'" in r

    def test_numeric_enum(self):
        code = "enum Direction {\n    Up,\n    Down,\n}"
        r = transform(code)
        assert "class Direction:" in r
        assert "Up" in r


# ─────────────────────────────────────────────────────────────────────────────
# OfflineProvider
# ─────────────────────────────────────────────────────────────────────────────

class TestOfflineProvider:
    def _make(self):
        return OfflineProvider(from_lang="typescript", to_lang="python")

    def test_translates_from_prompt(self):
        p = self._make()
        prompt = (
            "Translate the following typescript code to python.\n\n"
            "Source (typescript):\n"
            "```\n"
            "const x = 5;\n"
            "```"
        )
        r = p.complete(prompt)
        assert "x = 5" in r

    def test_confidence_prompt_returns_json(self):
        p = self._make()
        prompt = 'Score this. Respond with JSON only: {"score": ..., "reason": "..."}'
        r = p.complete(prompt)
        assert '"score"' in r

    def test_extract_code_helper(self):
        prompt = "Some text\n```\nconst y = 1;\n```\nmore text"
        code = _extract_code(prompt)
        assert code is not None
        assert "const y = 1;" in code

    def test_extract_code_falls_back_to_tagged_fence(self):
        # The primary regex expects the fence to open with a bare ``` \n;
        # a fence opened with a language tag (```typescript) only matches
        # the fallback regex.
        prompt = "Some text\n```typescript\nconst z = 2;\n```\nmore text"
        code = _extract_code(prompt)
        assert code is not None
        assert "const z = 2;" in code

    def test_extract_code_returns_none_when_no_fence(self):
        assert _extract_code("no fenced code block here at all") is None

    def test_manifest_prompt_raises_value_error(self):
        p = self._make()
        prompt = "Convert this typescript dependency manifest to an equivalent python manifest."
        with pytest.raises(ValueError, match="does not support manifest translation"):
            p.complete(prompt)

    def test_prompt_with_no_extractable_code_raises_value_error(self):
        p = self._make()
        with pytest.raises(ValueError, match="could not extract source code"):
            p.complete("Translate this but there is no fenced code block")


# ─────────────────────────────────────────────────────────────────────────────
# OfflineTransformer registry
# ─────────────────────────────────────────────────────────────────────────────

class TestOfflineTransformer:
    def test_supports_ts_to_py(self):
        assert OfflineTransformer.supports("typescript", "python")

    def test_supports_js_to_py(self):
        assert OfflineTransformer.supports("javascript", "python")

    def test_does_not_support_py_to_go(self):
        assert not OfflineTransformer.supports("python", "go")

    def test_raises_for_unsupported_pair(self):
        t = OfflineTransformer()
        with pytest.raises(ValueError, match="No offline transformer"):
            t.transform("x = 1", "python", "rust")

    def test_transform_ts_to_py(self):
        t = OfflineTransformer()
        r = t.transform("const x = 1;", "typescript", "python")
        assert "x = 1" in r
