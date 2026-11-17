#!/usr/bin/env python3
"""Draw the AST of an Eta program as a tree, saved as an SVG file.

    python3 tools/draw_ast.py demos/2.5-ast/01_functions.eta          # writes 01_functions.ast.svg
    python3 tools/draw_ast.py prog.eta -o tree.svg --open             # pick the name, open it
    cargo run -q --bin rwetac -- --parse prog.eta | python3 tools/draw_ast.py -o tree.svg

Given a .eta file it runs `rwetac --parse` itself. Otherwise it reads a
`--parse` dump from stdin. It works on the raw dump (with spans) and on the
output of demos/2.5-ast/trim_ast.py.

Colors: gray = declarations, purple = statements, teal = expressions.
The small grey line at the top of a box is the field it hangs from (guard, body, ...).
"""
import argparse
import re
import subprocess
import sys
from html import escape
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- parsing
# The --parse output is Rust's `{:?}` format: Name { field: value, .. },
# Name(arg, ..), [item, ..], "string", 42, 180..181, Ident.

TOKEN = re.compile(r'\s*(?:("(?:[^"\\]|\\.)*")|(-?\d+)|(\.\.)|([A-Za-z_]\w*)|(.))')


class Struct:
    def __init__(self, name, fields):
        self.name, self.fields = name, fields


class Tuple:
    def __init__(self, name, args):
        self.name, self.args = name, args


class List:
    def __init__(self, items):
        self.items = items


class Scalar:
    def __init__(self, text):
        self.text = text


def tokenize(text):
    out = []
    for m in TOKEN.finditer(text):
        s, n, dots, ident, punct = m.groups()
        if s is not None:
            out.append(("str", s[1:-1].encode().decode("unicode_escape")))
        elif n is not None:
            out.append(("num", n))
        elif dots:
            out.append(("..", dots))
        elif ident:
            out.append(("id", ident))
        elif punct and not punct.isspace():
            out.append(("p", punct))
    return out


class Parser:
    def __init__(self, tokens):
        self.t, self.i = tokens, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self, value=None):
        tok = self.peek()
        if value is not None and tok[1] != value:
            raise SyntaxError(f"expected {value!r}, got {tok[1]!r} (token {self.i})")
        self.i += 1
        return tok

    def seq(self, close, item):
        out = []
        while self.peek()[1] != close:
            out.append(item())
            if self.peek()[1] == ",":
                self.take(",")
        self.take(close)
        return out

    def field(self):
        name = self.take()[1]
        self.take(":")
        return name, self.value()

    def value(self):
        kind, v = self.take()
        if kind == "str":
            return Scalar(v)
        if kind == "num":
            if self.peek()[0] == "..":  # a span like 180..181
                self.take()
                return Scalar(f"{v}..{self.take()[1]}")
            return Scalar(v)
        if v == "[":
            return List(self.seq("]", self.value))
        if kind == "id":
            nxt = self.peek()[1]
            if nxt == "{":
                self.take("{")
                return Struct(v, self.seq("}", self.field))
            if nxt == "(":
                self.take("(")
                return Tuple(v, self.seq(")", self.value))
            return Scalar(v)
        raise SyntaxError(f"unexpected {v!r} (token {self.i - 1})")


# ------------------------------------------------- turning it into boxes

DECL = {"Program", "Function", "FunDecl", "Global", "RecordDecl", "Record", "MemberDecl"}
STMT = {"LocalDecl", "Return", "Assign", "Compound", "If", "While", "Procedure", "Block", "VarDeclaration"}
EXPR = {"Var", "Int", "Bool", "ArrayLit", "Binary", "Unary", "Call", "Subscript", "Dot"}
# list fields whose items need no label: every item is obviously one of them
QUIET_LISTS = {"decls", "stmts"}


class Node:
    def __init__(self, title, cat="other"):
        self.title, self.cat = title, cat
        self.label = None  # field name this node hangs from
        self.lines = []  # extra "key: value" lines
        self.children = []


def category(name):
    if name in DECL:
        return "decl"
    if name in STMT:
        return "stmt"
    if name in EXPR:
        return "expr"
    return "other"


def is_inline(v):
    """Small values that read better as a text line than as their own box."""
    if isinstance(v, Scalar):
        return True
    if isinstance(v, List):
        return all(is_inline(x) for x in v.items)
    if isinstance(v, Tuple):
        return v.name not in EXPR and v.name != "Some" and all(is_inline(a) for a in v.args)
    if isinstance(v, Struct):
        return v.name not in DECL | STMT | EXPR and all(is_inline(fv) for k, fv in v.fields if k != "span")
    return False


def text(v):
    if isinstance(v, Scalar):
        return v.text
    if isinstance(v, List):
        return "[" + ", ".join(text(x) for x in v.items) + "]"
    if isinstance(v, Tuple):
        return f"{v.name}({', '.join(text(a) for a in v.args)})"
    if isinstance(v, Struct) and v.name == "FunType":
        f = dict(v.fields)
        params = ", ".join(text(x) for x in f["param_types"].items)
        rets = [text(x) for x in f["ret_type"].items]
        ret = "()" if not rets else rets[0] if len(rets) == 1 else "(" + ", ".join(rets) + ")"
        return f"({params}) -> {ret}"
    if isinstance(v, Struct):
        inner = ", ".join(f"{k}: {text(fv)}" for k, fv in v.fields if k != "span")
        return f"{v.name} {{ {inner} }}"
    return "?"


def add_child(node, child_value, label=None):
    """Attach a value under node: lists spread into several children."""
    if isinstance(child_value, List):
        for item in child_value.items:
            add_child(node, item, label)
        return
    child = to_node(child_value)
    if child is None:
        return
    if label and child.label is None:
        child.label = label
    node.children.append(child)


def to_node(v):
    if isinstance(v, Scalar):
        return Node(v.text, "expr" if v.text.lstrip("-").isdigit() else "other")

    if isinstance(v, Struct):
        fields = [(k, fv) for k, fv in v.fields if k != "span"]
        # Expression { kind, span } / Statement { kind, span }: the kind is the node
        if len(fields) == 1 and fields[0][0] == "kind":
            return to_node(fields[0][1])
        node = Node(v.name, category(v.name))
        for k, fv in fields:
            if isinstance(fv, Tuple) and fv.name == "None" or (isinstance(fv, Scalar) and fv.text == "None"):
                node.lines.append(f"{k}: None")
            elif is_inline(fv):
                node.lines.append(f"{k}: {text(fv)}")
            else:
                add_child(node, fv, None if k in QUIET_LISTS else k)
        return node

    if isinstance(v, Tuple):
        # Some(x), and the assignment-target wrappers E(expr) / V(declaration)
        if v.name in ("Some", "Box", "E", "V") and len(v.args) == 1:
            return to_node(v.args[0])
        scalars = [a for a in v.args if is_inline(a) and not isinstance(a, List)]
        complex_ = [a for a in v.args if a not in scalars]
        # Wrapper like FunDecl(Function { .. }): one box, titled by the wrapper
        if not scalars and len(complex_) == 1 and isinstance(complex_[0], Struct):
            inner = to_node(complex_[0])
            if inner.title == complex_[0].name:
                inner.title, inner.cat = v.name, category(v.name)
            elif inner.label is None:
                inner.label = v.name
            return inner
        title = " ".join([v.name] + [text(a) for a in scalars])
        node = Node(title, category(v.name))
        for a in complex_:
            if isinstance(a, List) and is_inline(a) and a.items:
                node.lines.append(text(a))
            else:
                add_child(node, a)
        return node

    if isinstance(v, List):
        node = Node("[ ]")
        add_child(node, v)
        return node
    return None


# ----------------------------------------------------------------- layout

CHAR_W, LINE_H, PAD_X, PAD_Y = 7.8, 18, 12, 8
H_GAP, V_GAP, MARGIN = 18, 46, 20


def box_lines(n):
    """(text, style) pairs drawn inside the box, top to bottom."""
    out = []
    if n.label:
        out.append((n.label, "label"))
    out.append((n.title, "title"))
    out += [(line, "line") for line in n.lines]
    return out


def measure(n, depth=0):
    n.depth = depth
    lines = box_lines(n)
    n.w = max(len(t) for t, _ in lines) * CHAR_W + 2 * PAD_X
    n.h = len(lines) * LINE_H + 2 * PAD_Y
    for c in n.children:
        measure(c, depth + 1)
    kids = sum(c.span for c in n.children) + H_GAP * max(0, len(n.children) - 1)
    n.span = max(n.w, kids)


def level_heights(n, acc):
    acc[n.depth] = max(acc.get(n.depth, 0), n.h)
    for c in n.children:
        level_heights(c, acc)
    return acc


def place(n, left, tops):
    n.y = tops[n.depth]
    if n.children:
        kids = sum(c.span for c in n.children) + H_GAP * (len(n.children) - 1)
        x = left + (n.span - kids) / 2
        for c in n.children:
            place(c, x, tops)
            x += c.span + H_GAP
        n.cx = (n.children[0].cx + n.children[-1].cx) / 2
        # keep the box inside its own slot so it never overlaps a neighbour
        n.cx = min(max(n.cx, left + n.w / 2), left + n.span - n.w / 2)
    else:
        n.cx = left + n.span / 2


def walk(n):
    yield n
    for c in n.children:
        yield from walk(c)


# -------------------------------------------------------------------- svg

# Colors go straight on each shape as attributes: image viewers (unlike
# browsers) often ignore CSS variables and would draw every box black.
COLORS = {  # category: (fill, stroke, text)
    "decl": ("#F1EFE8", "#888780", "#2C2C2A"),
    "stmt": ("#EEEDFE", "#7F77DD", "#3C3489"),
    "expr": ("#E1F5EE", "#1D9E75", "#085041"),
    "other": ("#FFFFFF", "#B4B2A9", "#444441"),
}
BG, EDGE, LABEL = "#FFFFFF", "#9A9890", "#888780"
FONT = 'font-family="DejaVu Sans Mono, Menlo, Consolas, monospace"'


def render(root):
    measure(root)
    heights = level_heights(root, {})
    tops, y = {}, MARGIN
    for d in sorted(heights):
        tops[d] = y
        y += heights[d] + V_GAP
    place(root, MARGIN, tops)
    width = root.span + 2 * MARGIN
    height = y - V_GAP + MARGIN

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
           f'viewBox="0 0 {width:.0f} {height:.0f}" {FONT} font-size="13">',
           f'<rect width="100%" height="100%" fill="{BG}"/>']
    for n in walk(root):
        for c in n.children:
            x1, y1, x2, y2 = n.cx, n.y + n.h, c.cx, c.y
            my = (y1 + y2) / 2
            out.append(f'<path fill="none" stroke="{EDGE}" d="M{x1:.1f},{y1:.1f} C{x1:.1f},{my:.1f} {x2:.1f},{my:.1f} {x2:.1f},{y2:.1f}"/>')
    for n in walk(root):
        x = n.cx - n.w / 2
        fill, stroke, ink = COLORS[n.cat]
        out.append(f'<rect x="{x:.1f}" y="{n.y:.1f}" width="{n.w:.1f}" height="{n.h:.1f}" rx="5" '
                   f'fill="{fill}" stroke="{stroke}"/>')
        for i, (t, style) in enumerate(box_lines(n)):
            ty = n.y + PAD_Y + LINE_H * i + LINE_H / 2
            look = {"label": f'fill="{LABEL}" font-size="11"',
                    "title": f'fill="{ink}" font-weight="bold"',
                    "line": f'fill="{ink}"'}[style]
            out.append(f'<text {look} x="{n.cx:.1f}" y="{ty:.1f}" text-anchor="middle" '
                       f'dominant-baseline="central">{escape(t)}</text>')
    out.append("</svg>")
    return "\n".join(out)


# -------------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description="Draw an Eta AST as an SVG tree.")
    ap.add_argument("file", nargs="?", help=".eta file (omit to read a --parse dump from stdin)")
    ap.add_argument("-o", "--output", help="SVG to write (default: <file>.ast.svg in the current folder)")
    ap.add_argument("--open", action="store_true", help="open the SVG when done")
    args = ap.parse_args()

    if args.file:
        res = subprocess.run(
            ["cargo", "run", "-q", "--bin", "rwetac", "--", "--parse", str(Path(args.file).resolve())],
            cwd=REPO, capture_output=True, text=True)
        if res.returncode != 0:
            sys.exit(f"rwetac failed:\n{res.stderr or res.stdout}")
        dump = res.stdout
        out = Path(args.output or Path(args.file).stem + ".ast.svg")
    else:
        dump = sys.stdin.read()
        out = Path(args.output or "ast.svg")

    start = dump.find("Program")
    if start < 0:
        sys.exit("no `Program { ... }` found in the input")
    try:
        tree = Parser(tokenize(dump[start:])).value()
    except SyntaxError as e:
        sys.exit(f"could not read the AST dump: {e}")

    out.write_text(render(to_node(tree)))
    print(f"wrote {out}")
    if args.open:
        subprocess.run(["xdg-open", str(out)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
