"""
TypeScript → Python offline rule-based transformer.

Covers ~75% of idiomatic TypeScript. Complex constructs (optional chaining,
destructuring assignment, generics, switch) emit # TODO comments.
"""
from __future__ import annotations

import re

# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def transform(code: str) -> str:
    lines = code.splitlines()
    output: list[str] = []
    needed: set[str] = set()   # extra import lines to hoist

    ctx = _Context()
    i = 0
    while i < len(lines):
        line = lines[i]

        # ── Multi-line: interface / type { ... }  ─────────────────────────
        m = _IFACE_RE.match(line)
        if m:
            block, consumed = _collect_block(lines, i)
            transformed, extra = _interface_to_dataclass(block, m)
            output.extend(transformed)
            needed.update(extra)
            i += consumed
            ctx.process_block(block)
            continue

        # ── Multi-line: enum { ... }  ─────────────────────────────────────
        m = _ENUM_RE.match(line)
        if m:
            block, consumed = _collect_block(lines, i)
            output.extend(_enum_to_class(block, m))
            i += consumed
            ctx.process_block(block)
            continue

        # ── Single line  ──────────────────────────────────────────────────
        t, extra = _transform_line(line, ctx)
        needed.update(extra)
        leftover = ctx.update(line)
        output.append(t)
        for fld_ind, fname, val in leftover:
            # Class closed with unconsumed mutable-default field(s) — no
            # constructor claimed them, so fall back to a class attribute
            # (still works, but flag the shared-mutable-default risk).
            output.append(f"{fld_ind}{fname} = {val}  # WARNING: shared mutable default — consider initializing in __init__")
        i += 1

    result = '\n'.join(output)
    if needed:
        result = '\n'.join(sorted(needed)) + '\n\n' + result
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Context tracker (class depth)
# ─────────────────────────────────────────────────────────────────────────────

class _Context:
    """Tracks whether we're inside a class body, plus pending field
    initializers for the innermost class (mutable-literal class fields,
    e.g. `private items: T[] = [];`, which need to become per-instance
    `self.items = []` assignments rather than a shared class attribute)."""
    def __init__(self) -> None:
        self._stack: list[str] = []   # 'class' | 'other'
        self._class_pending: list[list[tuple[str, str, str]]] = []  # aligned with 'class' frames

    @property
    def in_class(self) -> bool:
        return bool(self._stack) and self._stack[-1] == 'class'

    def add_pending_field(self, ind: str, name: str, val: str) -> None:
        if self._class_pending:
            self._class_pending[-1].append((ind, name, val))

    def pop_pending_fields(self) -> list[tuple[str, str, str]]:
        if self._class_pending:
            pending = self._class_pending[-1]
            self._class_pending[-1] = []
            return pending
        return []

    def update(self, line: str) -> list[tuple[str, str, str]]:
        stripped = line.strip()
        opens  = line.count('{')
        closes = line.count('}')
        is_class = bool(_CLASS_RE.match(stripped))
        leftover: list[tuple[str, str, str]] = []
        for idx in range(opens):
            frame = 'class' if (is_class and idx == 0) else 'other'
            self._stack.append(frame)
            if frame == 'class':
                self._class_pending.append([])
        for _ in range(closes):
            if self._stack:
                popped = self._stack.pop()
                if popped == 'class' and self._class_pending:
                    leftover.extend(self._class_pending.pop())
        return leftover

    def process_block(self, lines: list[str]) -> None:
        for line in lines:
            self.update(line)


# ─────────────────────────────────────────────────────────────────────────────
# Patterns
# ─────────────────────────────────────────────────────────────────────────────

_IFACE_RE = re.compile(
    r'^(\s*)(export\s+)?(interface|type)\s+(\w+)(?:<[^>]*>)?'
    r'(?:\s+extends\s+[\w,\s<>]+)?\s*[={]'
)
_ENUM_RE  = re.compile(r'^(\s*)(export\s+)?(const\s+)?enum\s+(\w+)\s*\{')
_CLASS_RE = re.compile(
    r'^(export\s+)?(abstract\s+)?class\s+\w+'
)
_FUNC_RE  = re.compile(
    r'^((?:(?:public|private|protected|static|async|override|abstract)\s+)*)'
    r'(async\s+)?(?:function\s+)?(\w+)\s*(?:<[^>]*>)?\s*'
    r'\(([^)]*)\)(?:\s*:\s*[\w<>\[\],\s|&?.*]+)?\s*\{?\s*$'
)
_ARROW_RE = re.compile(
    r'^((?:(?:public|private|protected|static)\s+)*)'
    r'(const|let)\s+(\w+)(?:\s*:\s*[\w<>\[\],\s|&?]+)?\s*=\s*'
    r'(async\s+)?(?:\(([^)]*)\)|(\w+))\s*(?::\s*[\w<>\[\],\s|&?]+)?\s*=>\s*(.*)?$'
)
_REQUIRE_RE = re.compile(
    r"^(?:const|let|var)\s+(?:\{([^}]+)\}|(\w+))\s*=\s*require\(['\"]([^'\"]+)['\"]\)"
)
_MODULE_EXPORTS_RE = re.compile(r'^module\.exports\s*=\s*(.+)$')


# ─────────────────────────────────────────────────────────────────────────────
# Block collector
# ─────────────────────────────────────────────────────────────────────────────

def _collect_block(lines: list[str], start: int) -> tuple[list[str], int]:
    block = [lines[start]]
    depth = lines[start].count('{') - lines[start].count('}')
    i = start + 1
    while i < len(lines) and depth > 0:
        block.append(lines[i])
        depth += lines[i].count('{') - lines[i].count('}')
        i += 1
    return block, i - start


# ─────────────────────────────────────────────────────────────────────────────
# Interface / type → @dataclass
# ─────────────────────────────────────────────────────────────────────────────

def _interface_to_dataclass(
    block: list[str], m: re.Match
) -> tuple[list[str], set[str]]:
    ind  = m.group(1)
    name = m.group(4)
    out  = [f"{ind}@dataclass", f"{ind}class {name}:"]
    needed: set[str] = {"from dataclasses import dataclass"}

    found = False
    for raw in block[1:]:
        s = raw.strip()
        if not s or s in ('{', '}', '};'):
            continue
        if s.startswith('//') or s.startswith('*') or s.startswith('/*'):
            out.append(f"{ind}    # {s.lstrip('/*').strip()}")
            continue
        # field: Type  /  field?: Type
        m2 = re.match(r'(\w+)(\??):\s*(.+)', s.rstrip(';,'))
        if m2:
            fname    = m2.group(1)
            optional = m2.group(2) == '?'
            ftype    = _ts_type(m2.group(3).strip())
            if optional:
                out.append(f"{ind}    {fname}: {ftype} | None = None")
            else:
                out.append(f"{ind}    {fname}: {ftype}")
            found = True
        else:
            out.append(f"{ind}    # TODO: {s}")

    if not found:
        out.append(f"{ind}    pass")

    return out, needed


# ─────────────────────────────────────────────────────────────────────────────
# Enum → class with constants
# ─────────────────────────────────────────────────────────────────────────────

def _enum_to_class(block: list[str], m: re.Match) -> list[str]:
    ind  = m.group(1)
    name = m.group(4)
    out  = [f"{ind}class {name}:"]
    for raw in block[1:]:
        s = raw.strip()
        if not s or s in ('{', '}', '};'):
            continue
        # Name = value  or  Name,
        m2 = re.match(r'(\w+)\s*=\s*(.+)', s.rstrip(',;'))
        if m2:
            out.append(f"{ind}    {m2.group(1)} = {m2.group(2).strip()}")
        else:
            val = s.rstrip(',;')
            if val:
                out.append(f"{ind}    {val} = '{val}'")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Single-line transformer
# ─────────────────────────────────────────────────────────────────────────────

def _transform_line(line: str, ctx: _Context) -> tuple[str, set[str]]:
    needed: set[str] = set()
    ind = _indent(line)
    s   = line.strip()

    # Blank lines — pass through
    if not s:
        return line, needed

    # Comment lines
    if s.startswith('//'):
        return ind + '# ' + s[2:].lstrip(), needed
    if s.startswith('*') or s.startswith('/*'):
        return ind + '# ' + re.sub(r'^/?\*+/?', '', s).strip(), needed

    # ── Imports ──────────────────────────────────────────────────────────
    if s.startswith('import '):
        return _transform_import(line), needed

    # ── CommonJS require() ────────────────────────────────────────────────
    m = _REQUIRE_RE.match(s)
    if m:
        return ind + _transform_require(m), needed

    # ── CommonJS module.exports ───────────────────────────────────────────
    m = _MODULE_EXPORTS_RE.match(s)
    if m:
        return ind + _transform_module_exports(m), needed

    # ── Exports ──────────────────────────────────────────────────────────
    if s.startswith('export '):
        line = _transform_export(line)
        s    = line.strip()

    # ── Variable declarations ─────────────────────────────────────────────
    m = re.match(r'^(const|let|var)\s+(.*)', s)
    if m:
        rest = m.group(2)
        # Arrow function: const name = (...) => ...
        am = _ARROW_RE.match(s)
        if am:
            line, needed = _transform_arrow(am, ind)
            return line, needed
        # const x: TypeName = { ... }  →  x = TypeName(key=val, ...)  (an object
        # literal matching a declared PascalCase type is almost always meant to
        # be that type's instance, not a bare dict)
        m_obj = re.match(r'(\w+)\s*:\s*([A-Z]\w*)\s*=\s*(\{[^{}]*\})\s*;?\s*$', rest)
        if m_obj:
            varname, typename, obj = m_obj.groups()
            line = f"{ind}{varname} = {typename}({_object_literal_to_kwargs(obj)})"
        else:
            # Otherwise strip keyword + optional type annotation
            line = ind + _strip_type_from_decl(rest)
        s    = line.strip()

    # ── class declaration ─────────────────────────────────────────────────
    if _CLASS_RE.match(s):
        return _transform_class(line), needed

    # ── function declaration ──────────────────────────────────────────────
    if re.match(r'^(async\s+)?function\s+', s):
        return _transform_function(line), needed

    # ── class method / constructor (context-aware) ────────────────────────
    if ctx.in_class:
        r = _transform_method(line, ctx)
        if r is not None:
            return r, needed

    # ── Control flow ──────────────────────────────────────────────────────
    cf = _transform_control_flow(line)
    if cf is not None:
        line = cf
        s    = line.strip()

    # ── throw / return type annotations ──────────────────────────────────
    # return x;  (keep, just strip semicolon later)

    # ── Higher-order array methods (inline arrow callbacks) ────────────────
    line, hof_needed = _transform_higher_order(line)
    needed.update(hof_needed)

    # ── Operators, literals, builtins ─────────────────────────────────────
    line = _transform_expressions(line)

    # ── Template literals ─────────────────────────────────────────────────
    if '`' in line:
        line = _transform_template(line)

    # ── Brace / semicolon cleanup ─────────────────────────────────────────
    line = _cleanup(line)

    return line, needed


# ─────────────────────────────────────────────────────────────────────────────
# Import
# ─────────────────────────────────────────────────────────────────────────────

def _transform_import(line: str) -> str:
    ind = _indent(line)
    s   = line.strip()

    # import type { ... } from '...'
    if re.match(r'import\s+type\b', s):
        return f"{ind}# {s}  # type-only import removed"

    # import { a, b as c } from 'mod'
    m = re.match(r"import\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]", s)
    if m:
        names  = ', '.join(n.strip() for n in m.group(1).split(','))
        module = _module_name(m.group(2))
        return f"{ind}from {module} import {names}"

    # import * as x from 'mod'
    m = re.match(r"import\s+\*\s+as\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]", s)
    if m:
        module = _module_name(m.group(2))
        return f"{ind}import {module} as {m.group(1)}"

    # import Default from 'mod'
    m = re.match(r"import\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]", s)
    if m:
        module = _module_name(m.group(2))
        return f"{ind}from {module} import {m.group(1)}  # TODO: verify"

    # import 'side-effect'
    m = re.match(r"import\s+['\"]([^'\"]+)['\"]", s)
    if m:
        return f"{ind}# import '{m.group(1)}'  # side-effect import"

    return line


def _transform_require(m: re.Match) -> str:
    """const { a, b } = require('./mod')  →  from mod import a, b
       const mod = require('./mod')       →  import mod as mod
    """
    destructured, default_name, raw_module = m.group(1), m.group(2), m.group(3)
    module = _module_name(raw_module)
    if destructured is not None:
        names = []
        for item in destructured.split(','):
            item = item.strip()
            if not item:
                continue
            if ':' in item:
                orig, alias = item.split(':', 1)
                names.append(f"{orig.strip()} as {alias.strip()}")
            else:
                names.append(item)
        return f"from {module} import {', '.join(names)}"
    return f"import {module} as {default_name}"


def _transform_module_exports(m: re.Match) -> str:
    """module.exports = { a, b }  →  __all__ = ['a', 'b']
       module.exports = foo       →  commented out (no Python equivalent for a default export)
    """
    rest = m.group(1).rstrip(';').strip()
    obj_m = re.match(r'^\{([^}]*)\}$', rest)
    if obj_m:
        names = [n.strip().split(':')[0].strip() for n in obj_m.group(1).split(',') if n.strip()]
        return f"__all__ = {names!r}"
    return f"# module.exports = {rest}  # TODO: convert default export"


def _module_name(raw: str) -> str:
    """Convert JS module path to Python-style module name."""
    # Relative paths: './foo/bar' → foo.bar, '../util' → util
    name = re.sub(r'^\.\.?/', '', raw)   # strip leading ./ or ../
    name = name.replace('/', '.').replace('-', '_')
    return name


# ─────────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────────

def _transform_export(line: str) -> str:
    ind = _indent(line)
    s   = line.strip()

    # export default ...
    m = re.match(r'export\s+default\s+(.*)', s)
    if m:
        return ind + m.group(1)

    # export { a, b }  /  export { a } from '...'
    m = re.match(r'export\s+\{([^}]+)\}(?:\s+from\s+[\'"][^\'"]+[\'"])?', s)
    if m:
        names = [n.strip().split(' as ')[0] for n in m.group(1).split(',')]
        return f"{ind}__all__ = {names}"

    # export const/let/var/function/class/abstract/async
    m = re.match(r'export\s+((?:abstract\s+|async\s+)?(?:const|let|var|function|class|enum))\s+(.*)', s)
    if m:
        return ind + m.group(1) + ' ' + m.group(2)

    # export type/interface — handled by IFACE_RE before this point
    m = re.match(r'export\s+(interface|type)\s+(.*)', s)
    if m:
        return ind + m.group(1) + ' ' + m.group(2)

    return line


# ─────────────────────────────────────────────────────────────────────────────
# Variable declaration helpers
# ─────────────────────────────────────────────────────────────────────────────

def _strip_type_from_decl(rest: str) -> str:
    """Strip ': Type' from 'name: Type = value' → 'name = value'."""
    # name?: Type = value
    m = re.match(r'(\w+)\??\s*:\s*(?:[\w<>[\]|,\s&?.]+?)\s*(=.*)$', rest)
    if m:
        return m.group(1) + ' ' + m.group(2)
    # name?: Type  (no assignment — declaration only, e.g. in class body)
    m = re.match(r'(\w+)\??\s*:\s*([\w<>[\]|,\s&?.]+?)$', rest)
    if m:
        return f"{m.group(1)}: {_ts_type(m.group(2).strip())} = None  # TODO: initialise"
    return rest


# ─────────────────────────────────────────────────────────────────────────────
# Function
# ─────────────────────────────────────────────────────────────────────────────

_BODY_TAIL = r'\s*(?:\{\s*\})?\s*\{?\s*$'   # matches "", "{", or "{}"


def _transform_function(line: str) -> str:
    ind = _indent(line)
    s   = line.strip()
    m   = re.match(
        r'(async\s+)?function\s+(\w+)(?:<[^>]*>)?\s*\(([^)]*)\)'
        r'(?:\s*:\s*[\w<>[\]|,\s&?.]+)?' + _BODY_TAIL,
        s,
    )
    if m:
        async_kw = 'async ' if m.group(1) else ''
        name     = m.group(2)
        params   = _strip_params(m.group(3))
        return f"{ind}{async_kw}def {name}({params}):"
    return line


# ─────────────────────────────────────────────────────────────────────────────
# Arrow function
# ─────────────────────────────────────────────────────────────────────────────

def _transform_arrow(m: re.Match, ind: str) -> tuple[str, set[str]]:
    needed: set[str] = set()
    name     = m.group(3)
    async_kw = 'async ' if m.group(4) else ''
    params   = _strip_params(m.group(5) or m.group(6) or '')
    body     = (m.group(7) or '').strip().rstrip(';')

    # Static modifier
    static = 'static' in (m.group(1) or '')

    if body and body != '{':
        # Single-expression arrow → lambda (keep simple)
        if '\n' not in body and len(body) < 60 and params.count(',') <= 2:
            prefix = '@staticmethod\n' + ind if static else ''
            return f"{ind}{prefix}{async_kw}{name} = lambda {params}: {body}", needed

    prefix = '@staticmethod\n' + ind if static else ''
    return f"{ind}{prefix}{async_kw}def {name}({params}):", needed


# ─────────────────────────────────────────────────────────────────────────────
# Class declaration
# ─────────────────────────────────────────────────────────────────────────────

def _transform_class(line: str) -> str:
    ind = _indent(line)
    s   = line.strip()
    m   = re.match(
        r'(export\s+)?(abstract\s+)?class\s+(\w+)(?:<[^>]*>)?'
        r'(?:\s+extends\s+([\w<>, ]+?))?(?:\s+implements\s+[\w,\s<>]+)?' + _BODY_TAIL,
        s,
    )
    if m:
        name    = m.group(3)
        extends = m.group(4)
        if extends:
            extends = re.sub(r'<[^>]+>', '', extends).strip()
            return f"{ind}class {name}({extends}):"
        return f"{ind}class {name}:"
    return line


# ─────────────────────────────────────────────────────────────────────────────
# Class method (context-aware — only called when in_class)
# ─────────────────────────────────────────────────────────────────────────────

_ACCESS_RE = re.compile(
    r'^((?:(?:public|private|protected|static|async|override|abstract|readonly)\s+)+)'
)
_CONSTRUCTOR_RE = re.compile(r'^(public\s+|private\s+|protected\s+)?constructor\s*\(([^)]*)\)(?:[^{]*)?')
_PARAM_MODIFIER_RE = re.compile(r'^(?:(?:public|private|protected|readonly)\s+)+')


def _strip_constructor_params(params: str) -> tuple[str, list[str]]:
    """Strip TS parameter-property modifiers (private/public/protected/readonly)
    from constructor params, returning the cleaned param list plus the names
    TS auto-assigns to `this.x` — those need an explicit self.x = x in __init__."""
    if not params.strip():
        return '', []
    props: list[str] = []
    cleaned_parts: list[str] = []
    for part in _split_params(params):
        part = part.strip()
        if not part:
            continue
        has_modifier = bool(_PARAM_MODIFIER_RE.match(part))
        part = _PARAM_MODIFIER_RE.sub('', part)
        if has_modifier:
            m = re.match(r'(\w+)', part)
            if m:
                props.append(m.group(1))
        cleaned_parts.append(part)
    return _strip_params(', '.join(cleaned_parts)), props


def _transform_method(line: str, ctx: _Context) -> str | None:
    ind = _indent(line)
    s   = line.strip()

    # constructor
    m = _CONSTRUCTOR_RE.match(s)
    if m:
        cleaned, props = _strip_constructor_params(m.group(2))
        header = f"{ind}def __init__(self, {cleaned}):" if cleaned else f"{ind}def __init__(self):"
        pending = ctx.pop_pending_fields()
        body_lines  = [f"{ind}    self.{p} = {p}" for p in props]
        body_lines += [f"{ind}    self.{fname} = {val}" for (_, fname, val) in pending]
        if body_lines:
            return header + '\n' + '\n'.join(body_lines)
        return header

    # Method with access modifier(s)
    m = _ACCESS_RE.match(s)
    if m:
        modifiers = m.group(1)
        rest      = s[m.end():]
        is_static = 'static' in modifiers
        is_async  = 'async' in modifiers

        # Method declaration
        m2 = re.match(
            r'(\w+)(?:<[^>]*>)?\s*\(([^)]*)\)(?:\s*:\s*[\w<>[\]|,\s&?.]+)?' + _BODY_TAIL,
            rest,
        )
        if m2:
            name   = m2.group(1)
            params = _strip_params(m2.group(2))
            async_kw = 'async ' if is_async else ''
            if is_static:
                if params:
                    return f"{ind}@staticmethod\n{ind}{async_kw}def {name}({params}):"
                return f"{ind}@staticmethod\n{ind}{async_kw}def {name}():"
            if params:
                return f"{ind}{async_kw}def {name}(self, {params}):"
            return f"{ind}{async_kw}def {name}(self):"

        # Property declaration: private count: number = 0  →  count: float = 0
        m3 = re.match(r'(\w+)\??\s*(?::\s*([\w<>[\]|,\s&?.]+?))?\s*(?:=\s*(.+))?;?\s*$', rest)
        if m3 and '(' not in rest:
            fname = m3.group(1)
            ftype = _ts_type(m3.group(2).strip()) if m3.group(2) else None
            val   = m3.group(3).rstrip(';').strip() if m3.group(3) else None
            if val and (val.startswith('[') or val.startswith('{')):
                # Mutable literal default — must become a per-instance
                # self.x = val in __init__, not a shared class attribute.
                ctx.add_pending_field(ind, fname, val)
                return ''
            if ftype and val:
                return f"{ind}{fname}: {ftype} = {val}"
            elif ftype:
                return f"{ind}{fname}: {ftype} = None"
            elif val:
                return f"{ind}{fname} = {val}"
            return f"{ind}{fname} = None"

    # Undecorated method (best-effort: matches method-like pattern at indent > 0)
    if ind:
        m = re.match(
            r'^(async\s+)?(\w+)(?:<[^>]*>)?\s*\(([^)]*)\)'
            r'(?:\s*:\s*[\w<>[\]|,\s&?.]+)?' + _BODY_TAIL,
            s,
        )
        if m and m.group(2) not in (
            'if', 'while', 'for', 'switch', 'catch', 'new', 'return',
            'function', 'class', 'import', 'export', 'throw',
        ):
            async_kw = 'async ' if m.group(1) else ''
            name     = m.group(2)
            params   = _strip_params(m.group(3))
            if params:
                return f"{ind}{async_kw}def {name}(self, {params}):"
            return f"{ind}{async_kw}def {name}(self):"

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Control flow
# ─────────────────────────────────────────────────────────────────────────────

def _transform_control_flow(line: str) -> str | None:
    ind = _indent(line)
    s   = line.strip()

    # } else if (...) {
    m = re.match(r'\}\s*else\s+if\s*\((.+)\)\s*\{?\s*$', s)
    if m:
        return f"{ind}elif {_cond(m.group(1))}:"

    # } else {
    if re.match(r'\}\s*else\s*\{?\s*$', s):
        return f"{ind}else:"

    # } catch (e) { / } catch (e: Type) {
    m = re.match(r'\}\s*catch\s*\((\w+)(?:\s*:\s*[\w<>[\]|,\s]+)?\)\s*\{?\s*$', s)
    if m:
        return f"{ind}except Exception as {m.group(1)}:"

    # } finally {
    if re.match(r'\}\s*finally\s*\{?\s*$', s):
        return f"{ind}finally:"

    # if (cond) {
    m = re.match(r'if\s*\((.+)\)\s*\{?\s*$', s)
    if m:
        return f"{ind}if {_cond(m.group(1))}:"

    # else if (cond) {  — without leading }
    m = re.match(r'else\s+if\s*\((.+)\)\s*\{?\s*$', s)
    if m:
        return f"{ind}elif {_cond(m.group(1))}:"

    # else {
    if re.match(r'else\s*\{?\s*$', s):
        return f"{ind}else:"

    # while (cond) {
    m = re.match(r'while\s*\((.+)\)\s*\{?\s*$', s)
    if m:
        return f"{ind}while {_cond(m.group(1))}:"

    # do {  ...  } while (cond) — just convert do → while True:
    if re.match(r'do\s*\{?\s*$', s):
        return f"{ind}while True:  # TODO: do-while — add break condition"

    # for (const [k, v] of obj.entries()) {
    m = re.match(
        r'for\s*\(\s*(?:const|let|var)\s+\[(\w+),\s*(\w+)\]\s+of\s+(.+?)\.entries\(\)\s*\)\s*\{?\s*$', s
    )
    if m:
        return f"{ind}for {m.group(1)}, {m.group(2)} in {m.group(3).strip()}.items():"

    # for (const x of arr) {
    m = re.match(r'for\s*\(\s*(?:const|let|var)\s+(\w+)\s+of\s+(.+?)\)\s*\{?\s*$', s)
    if m:
        return f"{ind}for {m.group(1)} in {m.group(2).strip()}:"

    # for (let i = start; i < end; i++) {
    m = re.match(
        r'for\s*\(\s*(?:let|var)\s+(\w+)\s*=\s*(\d+)\s*;\s*\1\s*<\s*(.+?)\s*;\s*\1\+\+\s*\)\s*\{?\s*$', s
    )
    if m:
        var, start, end = m.group(1), m.group(2), m.group(3).strip()
        if start == '0':
            return f"{ind}for {var} in range({end}):"
        return f"{ind}for {var} in range({start}, {end}):"

    # for (const k in obj) {
    m = re.match(r'for\s*\(\s*(?:const|let|var)\s+(\w+)\s+in\s+(.+?)\)\s*\{?\s*$', s)
    if m:
        return f"{ind}for {m.group(1)} in {m.group(2).strip()}:"

    # try {
    if re.match(r'try\s*\{?\s*$', s):
        return f"{ind}try:"

    # switch — leave as TODO
    m = re.match(r'switch\s*\((.+)\)\s*\{?\s*$', s)
    if m:
        return f"{ind}# TODO: switch ({m.group(1).strip()}):"

    return None


def _cond(cond: str) -> str:
    cond = cond.strip()
    cond = re.sub(r'\s*===\s*', ' == ', cond)
    cond = re.sub(r'\s*!==\s*', ' != ', cond)
    cond = re.sub(r'\s*&&\s*', ' and ', cond)
    cond = re.sub(r'\s*\|\|\s*', ' or ', cond)
    cond = re.sub(r'^!\s*', 'not ', cond)
    cond = re.sub(r'\bnull\b', 'None', cond)
    cond = re.sub(r'\bundefined\b', 'None', cond)
    cond = re.sub(r'\btrue\b', 'True', cond)
    cond = re.sub(r'\bfalse\b', 'False', cond)
    return cond


# ─────────────────────────────────────────────────────────────────────────────
# Higher-order array methods (inline arrow callbacks)
# ─────────────────────────────────────────────────────────────────────────────

_HOF_RE = re.compile(r'\b([\w.]+)\.(filter|map|reduce)\(')


def _split_top_level_commas(s: str) -> list[str]:
    """Split on top-level commas using only paren/bracket/brace depth — unlike
    _split_params, does NOT treat '<'/'>' as brackets, since those collide
    with the '=>' in arrow-function callbacks."""
    depth = 0
    cur: list[str] = []
    out: list[str] = []
    for ch in s:
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        if ch == ',' and depth == 0:
            out.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append(''.join(cur))
    return out


def _arrow_to_lambda(arg: str) -> str:
    """'a => a.f()' / '(a, b) => a + b' → 'lambda a: a.f()' / 'lambda a, b: a + b'.
    A plain callback reference (no '=>') is passed through unchanged."""
    arg = arg.strip()
    m = re.match(r'\(?\s*([\w\s,]*)\s*\)?\s*=>\s*(.+)$', arg, re.DOTALL)
    if m:
        params = m.group(1).strip()
        body   = m.group(2).strip()
        return f"lambda {params}: {body}"
    return arg


def _transform_higher_order(line: str) -> tuple[str, set[str]]:
    """obj.filter(cb) / obj.map(cb) / obj.reduce(cb, init) → list(filter(...)) etc.,
    handling both plain callback references and inline arrow expressions
    (which may contain their own parens, so this can't be a simple regex sub)."""
    needed: set[str] = set()
    out: list[str] = []
    i = 0
    while True:
        m = _HOF_RE.search(line, i)
        if not m:
            out.append(line[i:])
            break
        out.append(line[i:m.start()])
        receiver, method = m.group(1), m.group(2)
        depth = 1
        k = m.end()
        while k < len(line) and depth > 0:
            if line[k] == '(':
                depth += 1
            elif line[k] == ')':
                depth -= 1
            k += 1
        args_str = line[m.end():k - 1]
        if method in ('filter', 'map'):
            cb = _arrow_to_lambda(args_str)
            out.append(f"list({method}({cb}, {receiver}))")
        else:  # reduce
            parts = _split_top_level_commas(args_str)
            cb = _arrow_to_lambda(parts[0]) if parts else ''
            needed.add('import functools')
            if len(parts) > 1:
                out.append(f"functools.reduce({cb}, {receiver}, {parts[1].strip()})")
            else:
                out.append(f"functools.reduce({cb}, {receiver})")
        i = k
    return ''.join(out), needed


# ─────────────────────────────────────────────────────────────────────────────
# Object literal → dict literal
# ─────────────────────────────────────────────────────────────────────────────

def _object_literal_to_kwargs(obj: str) -> str:
    """{ id: 1, name: 'Alice' } → "id=1, name='Alice'" (for use as constructor
    call kwargs, e.g. `User(id=1, name='Alice')`)."""
    inner = obj.strip()
    if inner.startswith('{') and inner.endswith('}'):
        inner = inner[1:-1]
    parts = []
    for part in _split_top_level_commas(inner):
        part = part.strip()
        if not part:
            continue
        kv = re.match(r'(\w+)\s*:\s*(.+)$', part)
        parts.append(f"{kv.group(1)}={kv.group(2)}" if kv else part)
    return ', '.join(parts)


def _transform_object_literals(line: str) -> str:
    """{ id: 1, name: 'Alice' } → {'id': 1, 'name': 'Alice'}  (best-effort,
    single-line, non-nested — requires at least one 'key:' pair to avoid
    matching code blocks)."""
    def repl(m: re.Match) -> str:
        parts = []
        for part in _split_top_level_commas(m.group(1)):
            part = part.strip()
            if not part:
                continue
            kv = re.match(r'(\w+)\s*:\s*(.+)$', part)
            parts.append(f"'{kv.group(1)}': {kv.group(2)}" if kv else part)
        return '{' + ', '.join(parts) + '}'
    return re.sub(r'\{([^{}]*\w+\s*:\s*[^{}]*)\}', repl, line)


# ─────────────────────────────────────────────────────────────────────────────
# Expression-level transformations (operators, builtins, literals)
# ─────────────────────────────────────────────────────────────────────────────

def _transform_expressions(line: str) -> str:
    # Operators — avoid breaking string contents (best-effort)
    line = re.sub(r'(?<![=!<>])===(?!=)', '==', line)
    line = re.sub(r'!==', '!=', line)
    line = re.sub(r'\s*&&\s*', ' and ', line)
    line = re.sub(r'\s*\|\|\s*', ' or ', line)
    line = re.sub(r'\s*\?\?\s*', ' or ', line)   # null coalescing

    # Literals
    line = re.sub(r'\bnull\b', 'None', line)
    line = re.sub(r'\bundefined\b', 'None', line)
    line = re.sub(r'\btrue\b', 'True', line)
    line = re.sub(r'\bfalse\b', 'False', line)

    # Object literal → dict literal (best-effort, single-line, non-nested)
    line = _transform_object_literals(line)

    # this. → self.
    line = re.sub(r'\bthis\.', 'self.', line)

    # Non-null assertion: x! → x  (before dot/bracket)
    line = re.sub(r'(\w)!([.\[(])', r'\1\2', line)

    # console.log/error/warn/debug → print
    line = re.sub(r'\bconsole\.\w+\b', 'print', line)

    # Math
    line = re.sub(r'\bMath\.floor\b', 'int', line)
    line = re.sub(r'\bMath\.ceil\b', 'math.ceil', line)
    line = re.sub(r'\bMath\.round\b', 'round', line)
    line = re.sub(r'\bMath\.abs\b', 'abs', line)
    line = re.sub(r'\bMath\.min\b', 'min', line)
    line = re.sub(r'\bMath\.max\b', 'max', line)
    line = re.sub(r'\bMath\.sqrt\b', 'math.sqrt', line)
    line = re.sub(r'\bMath\.pow\b', 'math.pow', line)
    line = re.sub(r'\bMath\.PI\b', 'math.pi', line)
    line = re.sub(r'\bMath\.random\(\)', 'random.random()', line)

    # JSON
    line = re.sub(r'\bJSON\.parse\b', 'json.loads', line)
    line = re.sub(r'\bJSON\.stringify\b', 'json.dumps', line)

    # Number.prototype.toFixed(n) → format(x, '.nf')
    line = re.sub(r'(\w+(?:\.\w+)*)\.toFixed\(\s*(\d+)\s*\)', r"format(\1, '.\2f')", line)

    # Type conversions
    line = re.sub(r'\bparseInt\s*\(', 'int(', line)
    line = re.sub(r'\bparseFloat\s*\(', 'float(', line)
    line = re.sub(r'\bNumber\s*\(', 'float(', line)
    line = re.sub(r'\bString\s*\(', 'str(', line)
    line = re.sub(r'\bBoolean\s*\(', 'bool(', line)

    # Object methods
    line = re.sub(r'\bObject\.keys\s*\(', 'list(', line)    # list(x.keys()) — TODO: imperfect
    line = re.sub(r'\bObject\.values\s*\(', 'list(', line)
    line = re.sub(r'\bObject\.entries\s*\(', 'list(', line)
    line = re.sub(r'\bArray\.isArray\s*\(([^)]+)\)', r'isinstance(\1, list)', line)
    line = re.sub(r'\bArray\.from\s*\(', 'list(', line)

    # instanceof → isinstance (TODO: syntax differs — isinstance(x, Y))
    line = re.sub(r'(\w+)\s+instanceof\s+(\w+)', r'isinstance(\1, \2)', line)

    # throw new Error(...) → raise Exception(...)  (must run before the
    # generic "throw new X(" rule below, else Error already became "raise Error(")
    line = re.sub(r'\bthrow\s+new\s+Error\s*\(', 'raise Exception(', line)
    # throw new X(...) → raise X(...)  (other custom error classes)
    line = re.sub(r'\bthrow\s+new\s+(\w+)\s*\(', r'raise \1(', line)
    # new Error(...) → Exception(...)  (no throw, e.g. assigned to a variable)
    line = re.sub(r'\bnew\s+Error\s*\(', 'Exception(', line)
    line = re.sub(r'\bnew\s+(\w+)\s*\(', r'\1(', line)

    # Array methods (chained)
    line = re.sub(r'\.push\s*\(', '.append(', line)
    line = re.sub(r'\.unshift\s*\(([^)]+)\)', r'.insert(0, \1)', line)
    line = re.sub(r'\.indexOf\s*\(', '.index(', line)
    line = re.sub(r'\.includes\s*\(', '.__contains__(', line)
    line = re.sub(r'\.join\s*\(', '.join(', line)
    line = re.sub(r'\.trim\s*\(\)', '.strip()', line)
    line = re.sub(r'\.trimStart\s*\(\)', '.lstrip()', line)
    line = re.sub(r'\.trimEnd\s*\(\)', '.rstrip()', line)
    line = re.sub(r'\.toLowerCase\s*\(\)', '.lower()', line)
    line = re.sub(r'\.toUpperCase\s*\(\)', '.upper()', line)
    line = re.sub(r'\.startsWith\s*\(', '.startswith(', line)
    line = re.sub(r'\.endsWith\s*\(', '.endswith(', line)
    line = re.sub(r'\.replaceAll\s*\(', '.replace(', line)
    line = re.sub(r'\.toString\s*\(\)', '', line)  # removed — caller already str()

    # x.length → len(x)  (simple identifier)
    line = re.sub(r'\b(\w+)\.length\b', r'len(\1)', line)

    # await is same in Python async
    # async/await → pass through

    return line


# ─────────────────────────────────────────────────────────────────────────────
# Template literal → f-string
# ─────────────────────────────────────────────────────────────────────────────

def _transform_template(line: str) -> str:
    result: list[str] = []
    i = 0
    while i < len(line):
        if line[i] != '`':
            result.append(line[i])
            i += 1
            continue
        # Find closing backtick (naive — no nested backticks in expressions)
        j = i + 1
        content: list[str] = []
        while j < len(line) and line[j] != '`':
            if line[j] == '\\' and j + 1 < len(line):
                content.append(line[j + 1])
                j += 2
            else:
                content.append(line[j])
                j += 1
        raw = ''.join(content)
        # ${expr} → {expr}, escape lone { and }
        raw = raw.replace('{', '{{').replace('}', '}}')
        raw = re.sub(r'\$\{\{([^}]+)\}\}', r'{\1}', raw)   # undo escape inside ${}
        # Use double quotes for the f-string
        result.append(f'f"{raw}"')
        i = j + 1
    return ''.join(result)


# ─────────────────────────────────────────────────────────────────────────────
# Brace / semicolon cleanup
# ─────────────────────────────────────────────────────────────────────────────

def _cleanup(line: str) -> str:
    s = line.strip()

    # Lines that are only closing braces
    if re.match(r'^[}\]]+[;,]?\s*$', s):
        return ''  # drop the line

    # Remove trailing semicolons (not inside strings — best effort)
    line = re.sub(r';(\s*)$', r'\1', line)

    # Remove trailing opening brace (the : was added by control flow / function)
    line = re.sub(r'\s*\{\s*$', '', line)

    return line


# ─────────────────────────────────────────────────────────────────────────────
# Parameter stripping
# ─────────────────────────────────────────────────────────────────────────────

def _strip_params(params: str) -> str:
    """Strip TypeScript type annotations from a parameter list."""
    if not params.strip():
        return ''
    result = []
    for part in _split_params(params):
        part = part.strip()
        if not part:
            continue
        # ...rest: Type[]
        m = re.match(r'(\.{3})(\w+)(?:\??\s*:\s*.+)?$', part)
        if m:
            result.append(f'*{m.group(2)}')
            continue
        # name?: Type = default  /  name: Type = default  /  name: Type  /  name?
        m = re.match(r'(\w+)(\??)(?:\s*:\s*[\w<>[\]|,\s&?.]+?)?\s*(?:=\s*(.+))?$', part)
        if m:
            name    = m.group(1)
            opt     = m.group(2) == '?'
            default = (m.group(3) or '').strip()
            if default:
                result.append(f'{name}={default}')
            elif opt:
                result.append(f'{name}=None')
            else:
                result.append(name)
        else:
            result.append(part)  # fallback: pass through
    return ', '.join(result)


def _split_params(params: str) -> list[str]:
    depth = 0
    cur: list[str] = []
    out: list[str] = []
    for ch in params:
        if ch in '<([{':
            depth += 1
        elif ch in '>)]}':
            depth -= 1
        if ch == ',' and depth == 0:
            out.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append(''.join(cur))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Type conversion
# ─────────────────────────────────────────────────────────────────────────────

_TYPE_MAP = [
    (r'\bstring\b',            'str'),
    (r'\bnumber\b',            'float'),
    (r'\bboolean\b',           'bool'),
    (r'\bany\b',               'Any'),
    (r'\bunknown\b',           'Any'),
    (r'\bnever\b',             'None'),
    (r'\bvoid\b',              'None'),
    (r'\bnull\b',              'None'),
    (r'\bundefined\b',         'None'),
    (r'\bObject\b',            'dict'),
    (r'Array<(.+?)>',          r'list[\1]'),
    (r'(\w+)\[\]',             r'list[\1]'),
    (r'Record<([^,]+),\s*([^>]+)>', r'dict[\1, \2]'),
    (r'Promise<(.+?)>',        r'Awaitable[\1]'),
    (r'Map<([^,]+),\s*([^>]+)>', r'dict[\1, \2]'),
    (r'Set<(.+?)>',            r'set[\1]'),
    (r'Partial<(.+?)>',        r'\1'),
    (r'Required<(.+?)>',       r'\1'),
    (r'Readonly<(.+?)>',       r'\1'),
]


def _ts_type(ts: str) -> str:
    ts = ts.strip()
    for pattern, repl in _TYPE_MAP:
        ts = re.sub(pattern, repl, ts)
    # T | null / T | undefined → T | None
    ts = re.sub(r'\|\s*null\b', '| None', ts)
    ts = re.sub(r'\|\s*undefined\b', '| None', ts)
    return ts


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _indent(line: str) -> str:
    return line[:len(line) - len(line.lstrip())]
