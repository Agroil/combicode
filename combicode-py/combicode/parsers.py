import ast
import re
from pathlib import Path


def compute_byte_size(lines, start, end):
    """Compute byte size of lines[start..end] (0-indexed)."""
    size = 0
    for i in range(start, min(end + 1, len(lines))):
        size += len(lines[i].encode("utf-8")) + (i < len(lines) - 1)  # +1 for newline
    return size


def build_element(etype, label, start, end, lines):
    """Build a code element dict. start/end are 0-indexed."""
    end = max(start, min(end, len(lines) - (2 if lines[-1] == "" else 1)))
    return {
        "type": etype,
        "label": label,
        "start_line": start + 1,  # 1-indexed
        "end_line": end + 1,
        "size": compute_byte_size(lines, start, end),
        "children": [],
    }


# --- Block-end finders ---


def find_indent_block_end(lines, start_line):
    if start_line >= len(lines):
        return start_line
    base_indent = len(lines[start_line]) - len(lines[start_line].lstrip())
    for i in range(start_line + 1, len(lines)):
        line = lines[i]
        if line.strip() == "":
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= base_indent:
            return i - 1
    return len(lines) - 1


def find_brace_block_end(lines, start_line):
    depth = 0
    found_open = False
    quote = None
    block_comment = False
    for i in range(start_line, len(lines)):
        line = lines[i]
        j = 0
        while j < len(line):
            ch = line[j]
            next_ch = line[j + 1 : j + 2]
            if block_comment:
                if ch == "*" and next_ch == "/":
                    block_comment = False
                    j += 1
            elif quote:
                if ch == "\\":
                    j += 1
                elif ch == quote:
                    quote = None
            elif ch == "/" and next_ch == "/":
                break
            elif ch == "/" and next_ch == "*":
                block_comment = True
                j += 1
            elif ch in ('"', "`") or (ch == "'" and "'" in line[j + 1 :]):
                quote = ch
            elif ch == "{":
                depth += 1
                found_open = True
            elif ch == "}" and found_open:
                depth -= 1
                if depth == 0:
                    return i
            j += 1
    return len(lines) - 1


def find_ruby_block_end(lines, start_line):
    depth = 0
    for i in range(start_line, len(lines)):
        trimmed = lines[i].strip()
        if re.match(r"^(class|module|def|do|if|unless|case|while|until|for|begin)\b", trimmed):
            depth += 1
        if re.match(r"^end\b", trimmed):
            depth -= 1
            if depth == 0:
                return i
    return len(lines) - 1


def find_lua_block_end(lines, start_line):
    depth = 0
    for i in range(start_line, len(lines)):
        trimmed = lines[i].strip()
        if re.search(r"\b(function|if|for|while|repeat)\b", trimmed):
            depth += 1
        if re.match(r"^end\b", trimmed) or trimmed == "end":
            depth -= 1
            if depth == 0:
                return i
    return len(lines) - 1


# --- Python Parser (AST-based) ---


def parse_python(content, lines):
    elements = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        # Fallback to regex
        return parse_python_regex(lines)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            start = node.lineno - 1
            end = node.end_lineno - 1
            elements.append(build_element("class", f"class {node.name}", start, end, lines))

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno - 1
            end = node.end_lineno - 1

            sig = ast.unparse(node.args)
            ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""

            name = node.name
            is_async = isinstance(node, ast.AsyncFunctionDef)

            if name == "__init__":
                etype = "ctor"
                label = f"ctor {name}({sig}){ret}"
            elif name.startswith("test_"):
                etype = "test"
                label = f"test {name}({sig}){ret}"
            elif is_async:
                etype = "async"
                label = f"async {name}({sig}){ret}"
            else:
                etype = "fn"
                label = f"fn {name}({sig}){ret}"

            elements.append(build_element(etype, label, start, end, lines))

        elif isinstance(node, (ast.For, ast.While)):
            start = node.lineno - 1
            end = node.end_lineno - 1
            if end - start + 1 > 5:
                loop_line = lines[start].strip().rstrip(":")
                elements.append(build_element("loop", f"loop {loop_line}", start, end, lines))

    return elements


def parse_python_regex(lines):
    elements = []
    for i, line in enumerate(lines):
        trimmed = line.strip()

        m = re.match(r"^class\s+(\w+)(\(.*?\))?\s*:", trimmed)
        if m:
            end = find_indent_block_end(lines, i)
            elements.append(build_element("class", f"class {m.group(1)}", i, end, lines))
            continue

        m = re.match(r"^async\s+def\s+(\w+)\s*\((.*?)\)(\s*->.*?)?\s*:", trimmed)
        if m:
            end = find_indent_block_end(lines, i)
            sig = f"{m.group(1)}({m.group(2)}){m.group(3) or ''}"
            etype = "ctor" if m.group(1) == "__init__" else "async"
            elements.append(build_element(etype, f"{etype} {sig}", i, end, lines))
            continue

        m = re.match(r"^def\s+(\w+)\s*\((.*?)\)(\s*->.*?)?\s*:", trimmed)
        if m:
            end = find_indent_block_end(lines, i)
            sig = f"{m.group(1)}({m.group(2)}){m.group(3) or ''}"
            if m.group(1) == "__init__":
                etype = "ctor"
            elif m.group(1).startswith("test_"):
                etype = "test"
            else:
                etype = "fn"
            label = f"{etype} {sig}"
            elements.append(build_element(etype, label, i, end, lines))
            continue

        m = re.match(r"^(for|while)\s+(.+):\s*$", trimmed)
        if m:
            end = find_indent_block_end(lines, i)
            if end - i + 1 > 5:
                elements.append(
                    build_element("loop", f"loop {m.group(1)} {m.group(2)}", i, end, lines)
                )

    return elements


# --- JavaScript/TypeScript Parsers ---


def parse_javascript(lines):
    elements = []
    for i, line in enumerate(lines):
        trimmed = line.strip()

        m = re.match(r"^(export\s+)?(default\s+)?class\s+(\w+)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("class", f"class {m.group(3)}", i, end, lines))
            continue

        m = re.match(r'^describe\s*\(\s*[\'"`]([^\'"`]+)[\'"`]', trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("describe", f"describe {m.group(1)}", i, end, lines))
            continue

        m = re.match(r'^(it|test)\s*\(\s*[\'"`]([^\'"`]+)[\'"`]', trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("test", f"test {m.group(2)}", i, end, lines))
            continue

        m = re.match(r"^(export\s+)?(default\s+)?async\s+function\s+(\w+)\s*\((.*?)\)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(
                build_element("async", f"async {m.group(3)}({m.group(4)})", i, end, lines)
            )
            continue

        m = re.match(r"^(export\s+)?(default\s+)?function\s+(\w+)\s*\((.*?)\)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            etype = "ctor" if m.group(3) == "constructor" else "fn"
            elements.append(
                build_element(etype, f"{etype} {m.group(3)}({m.group(4)})", i, end, lines)
            )
            continue

        m = re.match(
            r"^(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(async\s+)?\(?(.*?)\)?\s*=>", trimmed
        )
        if m and "{" in trimmed:
            end = find_brace_block_end(lines, i)
            if end > i:
                is_async = bool(m.group(4))
                etype = "async" if is_async else "fn"
                elements.append(
                    build_element(etype, f"{etype} {m.group(3)}({m.group(5) or ''})", i, end, lines)
                )
            continue

        m = re.match(r"^(for|while)\s*\((.+)\)\s*\{?", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            if end - i + 1 > 5:
                elements.append(
                    build_element("loop", f"loop {m.group(1)} {m.group(2)}", i, end, lines)
                )

    return elements


def parse_typescript(lines):
    elements = []
    for i, line in enumerate(lines):
        trimmed = line.strip()

        m = re.match(r"^(export\s+)?(default\s+)?interface\s+(\w+)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("class", f"interface {m.group(3)}", i, end, lines))
            continue

        m = re.match(r"^(export\s+)?(default\s+)?(abstract\s+)?class\s+(\w+)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("class", f"class {m.group(4)}", i, end, lines))
            continue

        m = re.match(r'^describe\s*\(\s*[\'"`]([^\'"`]+)[\'"`]', trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("describe", f"describe {m.group(1)}", i, end, lines))
            continue

        m = re.match(r'^(it|test)\s*\(\s*[\'"`]([^\'"`]+)[\'"`]', trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("test", f"test {m.group(2)}", i, end, lines))
            continue

        m = re.match(r"^(export\s+)?(default\s+)?async\s+function\s+(\w+)\s*\((.*?)\)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(
                build_element("async", f"async {m.group(3)}({m.group(4)})", i, end, lines)
            )
            continue

        m = re.match(r"^(export\s+)?(default\s+)?function\s+(\w+)\s*\((.*?)\)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("fn", f"fn {m.group(3)}({m.group(4)})", i, end, lines))
            continue

        m = re.match(
            r"^(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(async\s+)?\(?(.*?)\)?\s*=>", trimmed
        )
        if m and "{" in trimmed:
            end = find_brace_block_end(lines, i)
            if end > i:
                is_async = bool(m.group(4))
                etype = "async" if is_async else "fn"
                elements.append(
                    build_element(etype, f"{etype} {m.group(3)}({m.group(5) or ''})", i, end, lines)
                )
            continue

        m = re.match(r"^(for|while)\s*\((.+)\)\s*\{?", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            if end - i + 1 > 5:
                elements.append(
                    build_element("loop", f"loop {m.group(1)} {m.group(2)}", i, end, lines)
                )

    return elements


# --- Go Parser ---


def parse_go(lines):
    elements = []
    for i, line in enumerate(lines):
        trimmed = line.strip()

        m = re.match(r"^type\s+(\w+)\s+struct\b", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("class", f"struct {m.group(1)}", i, end, lines))
            continue

        m = re.match(r"^type\s+(\w+)\s+interface\b", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("class", f"interface {m.group(1)}", i, end, lines))
            continue

        m = re.match(r"^func\s+(\(.*?\)\s*)?(\w+)\s*\((.*?)\)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            receiver = (m.group(1) or "").strip()
            receiver = (receiver + " ") if receiver else ""
            name = m.group(2)
            etype = "test" if name.startswith("Test") else "fn"
            elements.append(
                build_element(etype, f"{etype} {receiver}{name}({m.group(3)})", i, end, lines)
            )
            continue

        m = re.match(r"^for\s+(.+)\s*\{", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            if end - i + 1 > 5:
                elements.append(build_element("loop", f"loop for {m.group(1)}", i, end, lines))

    return elements


# --- Rust Parser ---


def parse_rust(lines):
    elements = []
    for i, line in enumerate(lines):
        trimmed = line.strip()

        m = re.match(r"^(pub\s+)?struct\s+(\w+)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("class", f"struct {m.group(2)}", i, end, lines))
            continue

        m = re.match(r"^(pub\s+)?enum\s+(\w+)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("class", f"enum {m.group(2)}", i, end, lines))
            continue

        m = re.match(r"^(pub\s+)?trait\s+(\w+)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("class", f"trait {m.group(2)}", i, end, lines))
            continue

        m = re.match(r"^impl\s+(.+?)\s*\{", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("impl", f"impl {m.group(1)}", i, end, lines))
            continue

        m = re.match(r"^(pub\s+)?(async\s+)?fn\s+(\w+)\s*\((.*?)\)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            is_async = bool(m.group(2))
            is_test = m.group(3).startswith("test_")
            etype = "test" if is_test else ("async" if is_async else "fn")
            elements.append(
                build_element(etype, f"{etype} {m.group(3)}({m.group(4)})", i, end, lines)
            )
            continue

        m = re.match(r"^(for|while|loop)\b(.*)?\{", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            if end - i + 1 > 5:
                rest = (m.group(2) or "").strip()
                elements.append(
                    build_element(
                        "loop", f"loop {m.group(1)}{' ' + rest if rest else ''}", i, end, lines
                    )
                )

    return elements


# --- Java Parser ---


def parse_java(lines):
    elements = []
    for i, line in enumerate(lines):
        trimmed = line.strip()

        m = re.match(
            r"^(public\s+|private\s+|protected\s+)?(static\s+)?(abstract\s+)?(final\s+)?class\s+(\w+)",
            trimmed,
        )
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("class", f"class {m.group(5)}", i, end, lines))
            continue

        m = re.match(r"^(public\s+|private\s+|protected\s+)?interface\s+(\w+)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("class", f"interface {m.group(2)}", i, end, lines))
            continue

        m = re.match(r"^(public\s+|private\s+|protected\s+)?enum\s+(\w+)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("class", f"enum {m.group(2)}", i, end, lines))
            continue

        m = re.match(
            r"^(public\s+|private\s+|protected\s+)?(static\s+)?(abstract\s+)?(final\s+)?(synchronized\s+)?(\w+\s+)?(\w+)\s*\((.*?)\)\s*(\{|throws)",
            trimmed,
        )
        if m and m.group(7) not in ("if", "for", "while", "switch", "catch", "return"):
            end = find_brace_block_end(lines, i)
            name = m.group(7)
            has_return_type = m.group(6) and m.group(6).strip()
            etype = "ctor" if not has_return_type else ("test" if name.startswith("test") else "fn")
            elements.append(build_element(etype, f"{etype} {name}({m.group(8)})", i, end, lines))
            continue

        m = re.match(r"^(for|while)\s*\((.+)\)\s*\{?", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            if end - i + 1 > 5:
                elements.append(
                    build_element("loop", f"loop {m.group(1)} {m.group(2)}", i, end, lines)
                )

    return elements


# --- C/C++ Parser ---


def parse_c_cpp(lines):
    elements = []
    for i, line in enumerate(lines):
        trimmed = line.strip()

        m = re.match(r"^class\s+(\w+)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("class", f"class {m.group(1)}", i, end, lines))
            continue

        m = re.match(r"^(typedef\s+)?struct\s+(\w+)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("class", f"struct {m.group(2)}", i, end, lines))
            continue

        m = re.match(r"^(\w[\w\s*&]+?)\s+(\w+)\s*\(([^)]*)\)\s*(\{|$)", trimmed)
        if m and m.group(2) not in (
            "if",
            "for",
            "while",
            "switch",
            "return",
            "typedef",
            "struct",
            "class",
            "enum",
        ):
            end = find_brace_block_end(lines, i)
            elements.append(build_element("fn", f"fn {m.group(2)}({m.group(3)})", i, end, lines))
            continue

        m = re.match(r"^(for|while)\s*\((.+)\)\s*\{?", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            if end - i + 1 > 5:
                elements.append(
                    build_element("loop", f"loop {m.group(1)} {m.group(2)}", i, end, lines)
                )

    return elements


# --- C# Parser ---


def parse_csharp(lines):
    elements = []
    for i, line in enumerate(lines):
        trimmed = line.strip()

        m = re.match(
            r"^(public\s+|private\s+|protected\s+|internal\s+)?(static\s+)?(abstract\s+|sealed\s+)?(partial\s+)?(class|struct|interface|enum|record)\s+(\w+)",
            trimmed,
        )
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("class", f"{m.group(5)} {m.group(6)}", i, end, lines))
            continue

        m = re.match(
            r"^(public\s+|private\s+|protected\s+|internal\s+)?(static\s+)?(async\s+)?(virtual\s+|override\s+|abstract\s+)?(\w[\w<>\[\],\s]*?)\s+(\w+)\s*\((.*?)\)\s*\{?",
            trimmed,
        )
        if m and m.group(6) not in (
            "if",
            "for",
            "while",
            "switch",
            "catch",
            "return",
            "class",
            "struct",
            "interface",
            "enum",
        ):
            end = find_brace_block_end(lines, i)
            is_async = bool(m.group(3))
            etype = "async" if is_async else "fn"
            elements.append(
                build_element(etype, f"{etype} {m.group(6)}({m.group(7)})", i, end, lines)
            )
            continue

        m = re.match(r"^(for|foreach|while)\s*\((.+)\)\s*\{?", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            if end - i + 1 > 5:
                elements.append(
                    build_element("loop", f"loop {m.group(1)} {m.group(2)}", i, end, lines)
                )

    return elements


# --- PHP Parser ---


def parse_php(lines):
    elements = []
    for i, line in enumerate(lines):
        trimmed = line.strip()

        m = re.match(r"^(abstract\s+)?(final\s+)?(class|interface|trait)\s+(\w+)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("class", f"{m.group(3)} {m.group(4)}", i, end, lines))
            continue

        m = re.match(
            r"^(public\s+|private\s+|protected\s+)?(static\s+)?function\s+(\w+)\s*\((.*?)\)",
            trimmed,
        )
        if m:
            end = find_brace_block_end(lines, i)
            etype = (
                "ctor"
                if m.group(3) == "__construct"
                else ("test" if m.group(3).startswith("test") else "fn")
            )
            elements.append(
                build_element(etype, f"{etype} {m.group(3)}({m.group(4)})", i, end, lines)
            )
            continue

        m = re.match(r"^(for|foreach|while)\s*\((.+)\)\s*\{?", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            if end - i + 1 > 5:
                elements.append(
                    build_element("loop", f"loop {m.group(1)} {m.group(2)}", i, end, lines)
                )

    return elements


# --- Ruby Parser ---


def parse_ruby(lines):
    elements = []
    for i, line in enumerate(lines):
        trimmed = line.strip()

        m = re.match(r"^class\s+(\w+)", trimmed)
        if m:
            end = find_ruby_block_end(lines, i)
            elements.append(build_element("class", f"class {m.group(1)}", i, end, lines))
            continue

        m = re.match(r"^module\s+(\w+)", trimmed)
        if m:
            end = find_ruby_block_end(lines, i)
            elements.append(build_element("class", f"module {m.group(1)}", i, end, lines))
            continue

        m = re.match(r"^def\s+(self\.)?(\w+[?!=]?)\s*(\(.*?\))?", trimmed)
        if m:
            end = find_ruby_block_end(lines, i)
            prefix = m.group(1) or ""
            etype = (
                "ctor"
                if m.group(2) == "initialize"
                else ("test" if m.group(2).startswith("test_") else "fn")
            )
            elements.append(
                build_element(
                    etype, f"{etype} {prefix}{m.group(2)}{m.group(3) or ''}", i, end, lines
                )
            )

    return elements


# --- Swift Parser ---


def parse_swift(lines):
    elements = []
    for i, line in enumerate(lines):
        trimmed = line.strip()

        m = re.match(
            r"^(public\s+|private\s+|internal\s+|open\s+|fileprivate\s+)?(final\s+)?(class|struct|enum|protocol)\s+(\w+)",
            trimmed,
        )
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("class", f"{m.group(3)} {m.group(4)}", i, end, lines))
            continue

        m = re.match(
            r"^(public\s+|private\s+|internal\s+|open\s+)?(static\s+|class\s+)?(override\s+)?func\s+(\w+)\s*\((.*?)\)",
            trimmed,
        )
        if m:
            end = find_brace_block_end(lines, i)
            name = m.group(4)
            etype = "ctor" if name == "init" else ("test" if name.startswith("test") else "fn")
            elements.append(build_element(etype, f"{etype} {name}({m.group(5)})", i, end, lines))
            continue

        m = re.match(r"^(for|while)\s+(.+)\s*\{", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            if end - i + 1 > 5:
                elements.append(
                    build_element("loop", f"loop {m.group(1)} {m.group(2)}", i, end, lines)
                )

    return elements


# --- Kotlin Parser ---


def parse_kotlin(lines):
    elements = []
    for i, line in enumerate(lines):
        trimmed = line.strip()

        m = re.match(
            r"^(open\s+|abstract\s+|data\s+|sealed\s+)?(class|interface|object)\s+(\w+)", trimmed
        )
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("class", f"{m.group(2)} {m.group(3)}", i, end, lines))
            continue

        m = re.match(
            r"^(public\s+|private\s+|protected\s+|internal\s+)?(override\s+)?(suspend\s+)?fun\s+(\w+)\s*\((.*?)\)",
            trimmed,
        )
        if m:
            end = find_brace_block_end(lines, i)
            is_suspend = bool(m.group(3))
            etype = "test" if m.group(4).startswith("test") else ("async" if is_suspend else "fn")
            elements.append(
                build_element(etype, f"{etype} {m.group(4)}({m.group(5)})", i, end, lines)
            )
            continue

        m = re.match(r"^(for|while)\s*\((.+)\)\s*\{?", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            if end - i + 1 > 5:
                elements.append(
                    build_element("loop", f"loop {m.group(1)} {m.group(2)}", i, end, lines)
                )

    return elements


# --- Scala Parser ---


def parse_scala(lines):
    elements = []
    for i, line in enumerate(lines):
        trimmed = line.strip()

        m = re.match(r"^(case\s+)?(class|object|trait)\s+(\w+)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            prefix = m.group(1) or ""
            elements.append(
                build_element("class", f"{prefix}{m.group(2)} {m.group(3)}", i, end, lines)
            )
            continue

        m = re.match(r"^(override\s+)?def\s+(\w+)\s*(\(.*?\))?", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            etype = "test" if m.group(2).startswith("test") else "fn"
            elements.append(
                build_element(etype, f"{etype} {m.group(2)}{m.group(3) or ''}", i, end, lines)
            )

    return elements


# --- Lua Parser ---


def parse_lua(lines):
    elements = []
    for i, line in enumerate(lines):
        trimmed = line.strip()

        m = re.match(r"^(local\s+)?function\s+([\w.:]+)\s*\((.*?)\)", trimmed)
        if m:
            end = find_lua_block_end(lines, i)
            elements.append(build_element("fn", f"fn {m.group(2)}({m.group(3)})", i, end, lines))
            continue

        m = re.match(r"^(for|while)\s+(.+)\s+do", trimmed)
        if m:
            end = find_lua_block_end(lines, i)
            if end - i + 1 > 5:
                elements.append(
                    build_element("loop", f"loop {m.group(1)} {m.group(2)}", i, end, lines)
                )

    return elements


# --- Perl Parser ---


def parse_perl(lines):
    elements = []
    for i, line in enumerate(lines):
        trimmed = line.strip()

        m = re.match(r"^package\s+([\w:]+)", trimmed)
        if m:
            elements.append(build_element("class", f"package {m.group(1)}", i, i, lines))
            continue

        m = re.match(r"^sub\s+(\w+)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("fn", f"fn {m.group(1)}", i, end, lines))

    return elements


# --- Bash Parser ---


def parse_bash(lines):
    elements = []
    for i, line in enumerate(lines):
        trimmed = line.strip()

        m = re.match(r"^function\s+(\w+)", trimmed)
        if m:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("fn", f"fn {m.group(1)}", i, end, lines))
            continue

        m = re.match(r"^(\w+)\s*\(\s*\)\s*\{?", trimmed)
        if m and "()" in trimmed:
            end = find_brace_block_end(lines, i)
            elements.append(build_element("fn", f"fn {m.group(1)}", i, end, lines))
            continue

        m = re.match(r"^(for|while)\s+(.+?);\s*do", trimmed)
        if not m:
            m = re.match(r"^(for|while)\s+(.+)", trimmed)
        if m:
            end = i
            for j in range(i + 1, len(lines)):
                if lines[j].strip() == "done":
                    end = j
                    break
            if end - i + 1 > 5:
                elements.append(
                    build_element("loop", f"loop {m.group(1)} {m.group(2)}", i, end, lines)
                )

    return elements


# --- Dispatcher ---

PARSER_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c_cpp",
    ".h": "c_cpp",
    ".cpp": "c_cpp",
    ".hpp": "c_cpp",
    ".cc": "c_cpp",
    ".cxx": "c_cpp",
    ".cs": "csharp",
    ".php": "php",
    ".rb": "ruby",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".sc": "scala",
    ".lua": "lua",
    ".pl": "perl",
    ".pm": "perl",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
}

PARSER_FUNCS = {
    "python": None,  # handled specially (AST)
    "javascript": parse_javascript,
    "typescript": parse_typescript,
    "go": parse_go,
    "rust": parse_rust,
    "java": parse_java,
    "c_cpp": parse_c_cpp,
    "csharp": parse_csharp,
    "php": parse_php,
    "ruby": parse_ruby,
    "swift": parse_swift,
    "kotlin": parse_kotlin,
    "scala": parse_scala,
    "lua": parse_lua,
    "perl": parse_perl,
    "bash": parse_bash,
}


def parse_code_structure(file_path, content):
    ext = Path(file_path).suffix.lower()
    parser_name = PARSER_MAP.get(ext)
    if not parser_name:
        return []

    lines = content.split("\n")

    if parser_name == "python":
        return parse_python(content, lines)

    func = PARSER_FUNCS.get(parser_name)
    if func:
        return func(lines)
    return []


# ---------------------------------------------------------------------------
# Nesting
# ---------------------------------------------------------------------------


def nest_elements(elements):
    if not elements:
        return []

    sorted_els = sorted(
        elements, key=lambda e: (e["start_line"], -(e["end_line"] - e["start_line"]))
    )

    root = []
    stack = []

    for el in sorted_els:
        node = {**el, "children": []}

        while stack:
            parent = stack[-1]
            if (
                el["start_line"] >= parent["start_line"]
                and el["end_line"] <= parent["end_line"]
                and (el["start_line"] > parent["start_line"] or el["end_line"] < parent["end_line"])
            ):
                break
            stack.pop()

        if stack:
            stack[-1]["children"].append(node)
        else:
            root.append(node)

        stack.append(node)

    return root
