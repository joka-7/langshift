"""
C++ → Python offline rule-based transformer.

Structure, not lines. C++ delimits blocks with braces and statements with
semicolons; Python uses indentation and newlines. A line-by-line pass cannot
bridge that -- ``int main() { foo(); return 0; }`` is one line and three
statements -- so this scans the source into a flat event stream (statement,
block-open, block-close) and re-emits it with Python indentation.

Scope: ordinary procedural C++ plus simple classes. Templates, pointers,
operator overloading, references, RAII and preprocessor logic have no
straightforward Python equivalent; constructs that cannot be translated are
emitted as a ``# TODO(cpp):`` comment carrying the original text rather than
silently dropped or mistranslated.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_INDENT = "    "


def transform(code: str) -> str:
    """Translate C++ source to Python.

    Args:
        code: C++ source text.

    Returns:
        Python source. Untranslatable constructs appear as ``# TODO(cpp):``
        comments preserving the original line, so nothing is silently lost.
    """
    code = _strip_comments(code)
    # `public:` ends nothing in C++ syntax, so the scanner would run it into the
    # declaration that follows ("public: int x"), losing the declaration.
    code = re.sub(r"\b(public|private|protected)\s*:", r"\1:;", code)
    header = _Header()
    body = _emit(_scan(code), header)
    return header.render(body)


# ─────────────────────────────────────────────────────────────────────────────
# Preprocessing
# ─────────────────────────────────────────────────────────────────────────────

def _strip_comments(code: str) -> str:
    """Remove /* */ and // comments without touching string literals."""
    out: list[str] = []
    i, n = 0, len(code)
    while i < n:
        ch = code[i]
        if ch in '"\'':
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                out.append(code[i])
                if code[i] == "\\":
                    i += 1
                    if i < n:
                        out.append(code[i])
                        i += 1
                    continue
                if code[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if code.startswith("//", i):
            while i < n and code[i] != "\n":
                i += 1
            continue
        if code.startswith("/*", i):
            i += 2
            while i < n and not code.startswith("*/", i):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# Scanning: C++ text -> (statement | block open | block close) events
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Event:
    kind: str          # "stmt" | "open" | "close" | "pre"
    text: str = ""


def _scan(code: str) -> list[_Event]:
    """Split C++ into statements and block delimiters.

    Semicolons inside parentheses (a three-clause ``for``) do not end a
    statement, and neither delimiter counts inside a string literal.
    """
    events: list[_Event] = []
    buf: list[str] = []
    paren = 0
    i, n = 0, len(code)

    def flush() -> None:
        text = " ".join("".join(buf).split())
        buf.clear()
        if text:
            events.append(_Event("stmt", text))

    while i < n:
        ch = code[i]

        if ch == "#":                                   # preprocessor line
            j = code.find("\n", i)
            j = n if j == -1 else j
            flush()
            events.append(_Event("pre", code[i:j].strip()))
            i = j
            continue

        if ch in '"\'':
            quote = ch
            buf.append(ch)
            i += 1
            while i < n:
                buf.append(code[i])
                if code[i] == "\\":
                    i += 1
                    if i < n:
                        buf.append(code[i])
                        i += 1
                    continue
                if code[i] == quote:
                    i += 1
                    break
                i += 1
            continue

        if ch == "(":
            paren += 1
        elif ch == ")":
            paren = max(0, paren - 1)

        if paren == 0 and ch == "{":
            pending = " ".join("".join(buf).split())
            if _is_initializer(pending):
                # `vector<int> v = {1,2,3}` -- braces here are a value, not a
                # block. A `for (int i = 0; ...)` header also contains `=`, but
                # inside parentheses, so _split_top does not see it.
                brace, consumed = _read_braced(code, i)
                buf.append(brace)
                i += consumed
                continue
            buf.clear()
            events.append(_Event("open", pending))
            i += 1
            continue
        if paren == 0 and ch == "}":
            flush()
            events.append(_Event("close"))
            i += 1
            continue
        if paren == 0 and ch == ";":
            flush()
            i += 1
            continue

        buf.append(ch)
        i += 1

    flush()
    return events


_BLOCK_KEYWORD_RE = re.compile(
    r"^(if|else|for|while|switch|do|try|catch|class|struct|namespace|enum|union)\b"
)


def _is_initializer(pending: str) -> bool:
    """True when a `{` starts a braced value rather than a block."""
    if not pending or _BLOCK_KEYWORD_RE.match(pending):
        return False
    return len(_split_top(pending, "=")) > 1 or pending.endswith((",", "("))


def _read_braced(code: str, start: int) -> tuple[str, int]:
    """The balanced `{...}` beginning at `start`, and how many chars it spans."""
    depth = 0
    for i in range(start, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return code[start:i + 1], i + 1 - start
    return code[start:], len(code) - start


# ─────────────────────────────────────────────────────────────────────────────
# Emission
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Header:
    """Imports and trailers the body turns out to need."""

    imports: set[str] = field(default_factory=set)
    needs_main_call: bool = False

    def render(self, body: str) -> str:
        parts: list[str] = []
        if self.imports:
            parts.append("\n".join(sorted(self.imports)))
        parts.append(body.rstrip("\n"))
        if self.needs_main_call:
            parts.append('if __name__ == "__main__":\n    main()')
        return "\n\n\n".join(p for p in parts if p.strip()) + "\n"


@dataclass
class _Scope:
    """A class body being emitted: its fields need `self.` inside its methods."""

    name: str
    fields: set[str] = field(default_factory=set)


def _emit(events: list[_Event], header: _Header) -> str:
    lines: list[str] = []
    depth = 0
    # Depth at which each open class body sits, so we know when it closes.
    classes: dict[int, _Scope] = {}
    known_classes: set[str] = set()
    # Every class's scope, kept by name after its body closes -- a derived
    # class's own _Scope is seeded from its base's fields, which is only
    # knowable if the base's scope survives the base's own "close" event.
    class_scopes: dict[str, _Scope] = {}
    # Blocks opened without a Python counterpart (a bare `{ }` scope): we must
    # not indent for them, or the body drifts one level too deep.
    phantom: list[int] = []
    emitted_at: list[int] = []
    # Blocks with no Python equivalent (a switch). Their contents are emitted as
    # comments: dropping them would read as a clean translation of code that was
    # never translated at all.
    commenting: list[int] = []
    # Depths whose body is a `do { ... }`. The `while (cond);` that follows
    # the closing brace is scanned as an ordinary sibling statement, not part
    # of the block -- closing one of these depths looks ahead for it and folds
    # it into the loop as `if not (cond): break` instead of emitting it as a
    # dangling statement after the loop.
    do_bodies: set[int] = set()

    def put(text: str) -> None:
        if not text:
            lines.append("")
            return
        prefix = _INDENT * depth
        lines.append(f"{prefix}# {text}" if commenting else prefix + text)

    def enclosing_class() -> _Scope | None:
        return classes.get(max(classes)) if classes else None

    i, n = 0, len(events)
    while i < n:
        ev = events[i]
        i += 1

        if ev.kind == "pre":
            _emit_preprocessor(ev.text, header, put)
            continue

        if ev.kind == "close":
            if phantom and phantom[-1] == depth:
                phantom.pop()
                if commenting and commenting[-1] == depth:
                    commenting.pop()
                continue
            if depth in do_bodies:
                do_bodies.discard(depth)
                if i < n and events[i].kind == "stmt":
                    cond_m = re.match(r"^while\s*\((.*)\)\s*$", events[i].text.strip())
                    if cond_m:
                        put(f"if not ({_expr(cond_m.group(1), enclosing_class())}):")
                        depth += 1
                        put("break")
                        depth -= 1
                        i += 1
            # Pop before decrementing: `depth` is the body we are leaving. Doing
            # it after would drop the enclosing class when a method closes.
            classes.pop(depth, None)
            depth = max(0, depth - 1)
            if emitted_at and emitted_at[-1] == depth:
                emitted_at.pop()
            else:
                put("pass") if not _block_has_body(lines, depth) else None
            continue

        if ev.kind == "open":
            scope = enclosing_class()
            rendered = _translate_header(ev.text, header, known_classes, scope)
            if rendered is None:
                phantom.append(depth)
                continue
            if rendered.lstrip().startswith("# TODO(cpp):"):
                # Not a Python block: emit the marker, then comment the body.
                put(rendered.lstrip()[2:] if commenting else rendered)
                phantom.append(depth)
                commenting.append(depth)
                continue
            for line in rendered.splitlines():
                put(line)
            text = ev.text.strip()
            if text.startswith(("class ", "struct ")):
                name = _class_name(text)
                known_classes.add(name)
                base_m = re.search(r":\s*(?:public|private|protected)?\s*(\w+)", text)
                base_scope = class_scopes.get(base_m.group(1)) if base_m else None
                new_scope = _Scope(name, set(base_scope.fields) if base_scope else set())
                classes[depth + 1] = new_scope
                class_scopes[name] = new_scope
            depth += 1
            if text == "do":
                do_bodies.add(depth)
            emitted_at.append(depth - 1)
            continue

        scope = enclosing_class()
        for line in _translate_statement(ev.text, header, known_classes, scope):
            put(line)

    return "\n".join(lines)


def _block_has_body(lines: list[str], depth: int) -> bool:
    want = _INDENT * (depth + 1)
    for line in reversed(lines):
        if not line.strip():
            continue
        return line.startswith(want)
    return False


def _emit_preprocessor(text: str, header: _Header, put) -> None:
    match = re.match(r'#\s*include\s*[<"]([^>"]+)[>"]', text)
    if match:
        for imp in _INCLUDE_MAP.get(match.group(1).replace(".h", ""), []):
            header.imports.add(imp)
        return
    if re.match(r"#\s*(pragma|ifndef|define|endif|ifdef|if|else)\b", text):
        return
    put(f"# TODO(cpp): {text}")


_INCLUDE_MAP: dict[str, list[str]] = {
    "cmath": ["import math"],
    "math": ["import math"],
    "deque": ["from collections import deque"],
    "queue": ["from collections import deque"],
    "ctime": ["import time"],
    "cstdlib": ["import sys"],
}


# ─────────────────────────────────────────────────────────────────────────────
# Block headers
# ─────────────────────────────────────────────────────────────────────────────

# A C++ type: optional const, size/sign modifiers, a name, one level of nested
# template arguments, and any number of pointer/reference markers.
_TYPE = (
    r"(?:const\s+)?"
    r"(?:unsigned\s+|signed\s+|long\s+|short\s+)*"
    r"[\w:]+"
    r"(?:\s*<[^<>]*(?:<[^<>]*>)?[^<>]*>)?"
    r"(?:\s*[*&])*"
)
_FUNC_RE = re.compile(rf"^{_TYPE}\s+(\w+)\s*\((.*)\)\s*(?:const\s*)?$")
_CTOR_RE = re.compile(r"^(\w+)\s*\((.*)\)\s*(?::.*)?$")


def _class_name(header: str) -> str:
    match = re.match(r"^(?:class|struct)\s+(\w+)", header.strip())
    return match.group(1) if match else "Unknown"


def _translate_header(
    header: str, hdr: _Header, classes: set[str], scope: _Scope | None
) -> str | None:
    """Python for a block header, or None for a block with no Python counterpart.

    Returning None means the caller must open no indentation level -- a bare
    C++ scope, or `extern "C" {`, has no Python equivalent and its contents
    belong to the enclosing block.
    """
    h = header.strip()
    if not h:
        return None

    if h.startswith(("class ", "struct ")):
        name = _class_name(h)
        base = re.search(r":\s*(?:public|private|protected)?\s*(\w+)", h)
        return f"class {name}({base.group(1)}):" if base else f"class {name}:"

    if re.match(r"^(?:int|void)\s+main\s*\(", h):
        hdr.needs_main_call = True
        return "def main():"

    if h == "else":
        return "else:"
    if h.startswith("else if"):
        return f"elif {_expr(h[len('else if'):].strip().strip('()'), scope)}:"
    for kw in ("if", "while", "switch"):
        if re.match(rf"^{kw}\s*\(", h):
            cond = _strip_outer_parens(h[len(kw):].strip())
            if kw == "switch":
                return f"# TODO(cpp): switch({cond}) has no Python equivalent"
            return f"{kw} {_expr(cond, scope)}:"
    if re.match(r"^for\s*\(", h):
        return _translate_for(_strip_outer_parens(h[3:].strip()), scope)
    if h in ("try", "do"):
        return "try:" if h == "try" else "while True:  # TODO(cpp): do-while"
    if h.startswith("catch"):
        return "except Exception:"

    if h in ("public:", "private:", "protected:", "public", "private", "protected"):
        return None

    if scope is not None:
        ctor = _CTOR_RE.match(h)
        if ctor and ctor.group(1) == scope.name:
            return f"def __init__(self{_params(ctor.group(2), lead=True)}):"

    func = _FUNC_RE.match(h)
    if func:
        name, params = func.group(1), func.group(2)
        if scope is not None:
            return f"def {name}(self{_params(params, lead=True)}):"
        return f"def {name}({_params(params)}):"

    if h.startswith(("namespace", 'extern "C"')) or h == "":
        return None

    return f"# TODO(cpp): {h} {{"


def _params(params: str, lead: bool = False) -> str:
    """Parameter names from a C++ parameter list, types dropped."""
    names: list[str] = []
    for part in _split_top(params, ","):
        part = part.strip().rstrip(")")
        if not part or part == "void":
            continue
        default = None
        if "=" in part:
            part, default = part.split("=", 1)
            default = default.strip()
        match = re.search(r"(\w+)\s*(?:\[\s*\])?$", part.strip())
        if not match:
            continue
        names.append(f"{match.group(1)}={default}" if default else match.group(1))
    joined = ", ".join(names)
    if not joined:
        return ""
    return (", " if lead else "") + joined


def _translate_for(clauses: str, scope: _Scope | None) -> str:
    """A C++ for-header as a Python for/while."""
    # Range-based: for (int x : v)
    if ":" in clauses and ";" not in clauses:
        var, _, seq = clauses.partition(":")
        name = re.search(r"(\w+)\s*$", var.strip())
        return f"for {name.group(1) if name else 'item'} in {_expr(seq.strip(), scope)}:"

    parts = _split_top(clauses, ";")
    if len(parts) != 3:
        return f"# TODO(cpp): for ({clauses})"
    init, cond, step = (p.strip() for p in parts)

    var_m = re.search(r"(\w+)\s*=\s*(.+)$", init)
    cmp_m = re.match(r"^\s*(\w+)\s*(<=|<|>=|>)\s*(.+?)\s*$", cond)
    step_m = re.match(r"^\s*(\w+)\s*(\+\+|--|\+=\s*(\S+)|-=\s*(\S+))\s*$", step)

    if var_m and cmp_m and step_m and var_m.group(1) == cmp_m.group(1) == step_m.group(1):
        var = var_m.group(1)
        start, op, end = _expr(var_m.group(2), scope), cmp_m.group(2), _expr(cmp_m.group(3), scope)
        raw = step_m.group(2)
        if raw == "++":
            stride = "1"
        elif raw == "--":
            stride = "-1"
        else:
            stride = (step_m.group(3) or "").strip() or f"-{(step_m.group(4) or '').strip()}"
        if op == "<=":
            end = f"{end} + 1"
        elif op == ">=":
            end = f"{end} - 1"
        args = f"{start}, {end}" if stride == "1" else f"{start}, {end}, {stride}"
        if start == "0" and stride == "1":
            args = end
        return f"for {var} in range({args}):"

    return f"# TODO(cpp): for ({clauses}) -- not a countable loop"


# ─────────────────────────────────────────────────────────────────────────────
# Statements
# ─────────────────────────────────────────────────────────────────────────────

_DECL_RE = re.compile(rf"^{_TYPE}\s+(\w+)\s*(?:=\s*(.+))?$")
_ARRAY_DECL_RE = re.compile(rf"^{_TYPE}\s+(\w+)\s*\[[^\]]*\]\s*(?:=\s*(.+))?$")

_DEFAULT_FOR_TYPE: list[tuple[str, str]] = [
    ("vector", "[]"), ("array", "[]"), ("list", "[]"), ("deque", "deque()"),
    ("unordered_map", "{}"), ("map", "{}"), ("unordered_set", "set()"), ("set", "set()"),
    ("string", '""'), ("char", '""'),
    ("double", "0.0"), ("float", "0.0"),
    ("bool", "False"),
    ("int", "0"), ("long", "0"), ("short", "0"), ("size_t", "0"),
]


def _translate_statement(
    stmt: str, hdr: _Header, classes: set[str], scope: _Scope | None
) -> list[str]:
    s = stmt.strip().rstrip(";").strip()
    if not s:
        return []

    if s in ("public:", "private:", "protected:", "break", "continue"):
        return [] if s.endswith(":") else [s]

    if s.startswith("using namespace") or s.startswith("using std::"):
        return []

    if s.startswith("return"):
        rest = s[len("return"):].strip()
        return ["return" if not rest else f"return {_expr(rest, scope)}"]

    if re.match(r"^(?:std::)?cout\b", s) or re.match(r"^(?:std::)?cerr\b", s):
        return [_translate_cout(s, scope)]
    if re.match(r"^(?:std::)?cin\b", s):
        return [_translate_cin(s, scope)]

    if s.startswith("assert"):
        inner = _strip_outer_parens(s[len("assert"):].strip())
        return [f"assert {_expr(inner, scope)}"]

    if s.startswith("delete ") or s == "delete":
        return [f"# TODO(cpp): {s}  (Python manages memory)"]

    # i++ / ++i / i-- / --i
    incr = re.match(r"^(?:\+\+|--)?(\w+(?:\.\w+)*)(?:\+\+|--)?$", s)
    if incr and ("++" in s or "--" in s):
        return [f"{_expr(incr.group(1), scope)} {'+=' if '++' in s else '-='} 1"]

    # Declaration of a known class: `Point p;` -> `p = Point()`
    obj = re.match(r"^(\w+)\s+(\w+)\s*(?:\((.*)\))?$", s)
    if obj and obj.group(1) in classes:
        args = _expr(obj.group(3), scope) if obj.group(3) else ""
        return [f"{_lhs(obj.group(2), scope)} = {obj.group(1)}({args})"]

    array = _ARRAY_DECL_RE.match(s)
    if array and not _looks_like_call(s):
        name, value = array.group(1), array.group(2)
        return [f"{_lhs(name, scope)} = {_expr(value, scope) if value else '[]'}"]

    decl = _DECL_RE.match(s)
    if decl and not _looks_like_call(s):
        name, value = decl.group(1), decl.group(2)
        if scope is not None and value is None:
            scope.fields.add(name)
            return [f"{name} = {_default_for(s)}"]
        rendered = _expr(value, scope) if value else _default_for(s)
        return [f"{_lhs(name, scope)} = {rendered}"]

    return [_expr(s, scope)]


def _looks_like_call(s: str) -> bool:
    """`foo(a, b)` and `obj.m()` are calls, not declarations."""
    return bool(re.match(r"^[\w:.]+\s*\(", s.strip()))


def _default_for(decl: str) -> str:
    for token, default in _DEFAULT_FOR_TYPE:
        if re.search(rf"\b{token}\b", decl):
            return default
    return "None"


def _lhs(name: str, scope: _Scope | None) -> str:
    if scope is not None and name in scope.fields:
        return f"self.{name}"
    return name


def _translate_cout(s: str, scope: _Scope | None) -> str:
    parts = [p.strip() for p in _split_top(s, "<<")]
    parts = [p for p in parts[1:] if p]           # drop the leading `cout`
    newline = True
    if parts and re.fullmatch(r"(?:std::)?endl", parts[-1]):
        parts.pop()
    elif parts and parts[-1].endswith(r'\n"'):
        parts[-1] = parts[-1][:-3] + '"'
    else:
        newline = False
    args = [_expr(p, scope) for p in parts if p]
    if not args:
        return "print()"
    body = ", ".join(args)
    # Several << operands are one line of output, not space-separated values.
    sep = ', sep=""' if len(args) > 1 else ""
    end = "" if newline else ', end=""'
    return f"print({body}{sep}{end})"


def _translate_cin(s: str, scope: _Scope | None) -> str:
    targets = [p.strip() for p in _split_top(s, ">>")][1:]
    if not targets:
        return "input()"
    return "; ".join(f"{_lhs(t, scope)} = input()" for t in targets if t)


# ─────────────────────────────────────────────────────────────────────────────
# Expressions
# ─────────────────────────────────────────────────────────────────────────────

_EXPR_SUBS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bstd::endl\b"), '"\\n"'),
    (re.compile(r"\bstd::"), ""),
    (re.compile(r"\bnullptr\b|\bNULL\b"), "None"),
    (re.compile(r"\btrue\b"), "True"),
    (re.compile(r"\bfalse\b"), "False"),
    (re.compile(r"&&"), " and "),
    (re.compile(r"\|\|"), " or "),
    (re.compile(r"->"), "."),
    (re.compile(r"(?<![!<>=+\-*/%&|^])!(?!=)"), " not "),
]

_METHOD_SUBS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b([\w.\[\]]+)\.size\(\)"), r"len(\1)"),
    (re.compile(r"\b([\w.\[\]]+)\.length\(\)"), r"len(\1)"),
    (re.compile(r"\b([\w.\[\]]+)\.empty\(\)"), r"(not \1)"),
    (re.compile(r"\b([\w.\[\]]+)\.push_back\("), r"\1.append("),
    (re.compile(r"\b([\w.\[\]]+)\.pop_back\(\)"), r"\1.pop()"),
    (re.compile(r"\bpush_back\("), "append("),
]

# `new int(5)`/`new Point(...)` -> a plain constructor call. Python has no
# heap-vs-stack distinction, so `new` itself carries no meaning; only the
# type name might need remapping (a primitive becomes its Python builtin,
# a class name is used as-is).
_NEW_TYPE_MAP: dict[str, str] = {
    "int": "int", "long": "int", "short": "int", "size_t": "int",
    "double": "float", "float": "float",
    "bool": "bool",
    "char": "str", "string": "str",
}


def _translate_new(out: str) -> str:
    return re.sub(
        r"\bnew\s+(\w+)\s*\(",
        lambda m: f"{_NEW_TYPE_MAP.get(m.group(1), m.group(1))}(",
        out,
    )


def _translate_at(out: str) -> str:
    """`v.at(i)` -> `v[i]`, matching the *balanced* closing paren.

    A plain regex can turn the opening `.at(` into `[` but has no way to
    find the matching `)` when the index expression itself contains
    parens, so it always leaves a stray `)` behind.
    """
    pattern = re.compile(r"\b(\w+)\s*\.\s*at\s*\(")
    result: list[str] = []
    i = 0
    while True:
        m = pattern.search(out, i)
        if not m:
            result.append(out[i:])
            break
        result.append(out[i:m.start()])
        result.append(f"{m.group(1)}[")
        depth = 1
        j = m.end()
        start = j
        while j < len(out) and depth > 0:
            if out[j] == "(":
                depth += 1
            elif out[j] == ")":
                depth -= 1
            j += 1
        result.append(out[start:j - 1])
        result.append("]")
        i = j
    return "".join(result)


def _expr(text: str | None, scope: _Scope | None) -> str:
    """Translate a C++ expression to Python, preserving string literals."""
    if text is None:
        return "None"
    out = text.strip()
    if not out:
        return ""

    literals: list[str] = []

    def stash(m: re.Match[str]) -> str:
        literals.append(m.group(0))
        return f"\x00{len(literals) - 1}\x00"

    out = re.sub(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'', stash, out)

    out = re.sub(r"\{([^{}]*)\}", r"[\1]", out)          # {1,2,3} -> [1,2,3]
    for pattern, repl in _METHOD_SUBS:
        out = pattern.sub(repl, out)
    for pattern, repl in _EXPR_SUBS:
        out = pattern.sub(repl, out)
    out = re.sub(r"\bstatic_cast<\s*int\s*>\s*\(", "int(", out)
    out = re.sub(r"\bstatic_cast<\s*(?:double|float)\s*>\s*\(", "float(", out)
    out = _translate_new(out)
    out = _translate_at(out)

    if scope is not None and scope.fields:
        names = "|".join(sorted(map(re.escape, scope.fields), key=len, reverse=True))
        out = re.sub(rf"(?<![\w.]) ?\b({names})\b", lambda m: f"self.{m.group(1)}", out)

    out = re.sub(r"\x00(\d+)\x00", lambda m: literals[int(m.group(1))], out)
    return " ".join(out.split())


# ─────────────────────────────────────────────────────────────────────────────
# Small text helpers
# ─────────────────────────────────────────────────────────────────────────────

def _strip_outer_parens(text: str) -> str:
    text = text.strip()
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        for i, ch in enumerate(text):
            depth += (ch == "(") - (ch == ")")
            if depth == 0 and i < len(text) - 1:
                return text
        text = text[1:-1].strip()
    return text


def _split_top(text: str, sep: str) -> list[str]:
    """Split on `sep` only at bracket depth zero and outside string literals."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in '"\'':
            quote = ch
            buf.append(ch)
            i += 1
            while i < n:
                buf.append(text[i])
                if text[i] == "\\":
                    i += 1
                    if i < n:
                        buf.append(text[i])
                        i += 1
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if depth <= 0 and text.startswith(sep, i):
            parts.append("".join(buf))
            buf.clear()
            i += len(sep)
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts]
