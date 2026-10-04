"""Read-only AST inventory for the two AIR snapshots; no execution of project code."""
import ast
import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).parent
NEW = BASE / "AIR"
OLD = BASE.parent / "AIR"


def module_name(path, root):
    parts = path.relative_to(root).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


files = {module_name(p, NEW): p for p in (NEW / "src").rglob("*.py")}
edges = {m: set() for m in files}
tests = {m: set() for m in files}
syntax = []


def imports(path, module):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (SyntaxError, UnicodeError) as exc:
        syntax.append([str(path.relative_to(NEW)), str(exc)])
        return set()
    found = set()
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            prefix = node.module or ""
            if node.level:
                parent = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
                prefix = ".".join(parent[:len(parent) - node.level + 1] + ([prefix] if prefix else []))
            names = [prefix, *[prefix + "." + a.name for a in node.names]]
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name in ("__import__", "import_module") and isinstance(node.args[0].value, str):
                names = [node.args[0].value]
        for name in names:
            if name in files:
                found.add(name)
    return found


for module, path in files.items():
    edges[module] = imports(path, module)
for path in (NEW / "tests").rglob("*.py"):
    for module in imports(path, module_name(path, NEW)):
        tests[module].add(str(path.relative_to(NEW)))

reachable = set()
pending = [m for m in ("src.server", "src.main", "src.team_api", "src.kb.__main__") if m in files]
while pending:
    module = pending.pop()
    if module in reachable:
        continue
    reachable.add(module)
    pending.extend(edges[module] - reachable)

unreachable = []
for module, path in sorted(files.items()):
    if module not in reachable and path.name != "__init__.py":
        unreachable.append({"module": module, "lines": len(path.read_text(encoding="utf-8-sig").splitlines()),
                            "src_callers": sorted(m for m in files if module in edges[m]),
                            "tests": sorted(tests[module])})

def inventory(root):
    return {str(p.relative_to(root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (root / "src").rglob("*")
            if p.is_file() and p.suffix in (".py", ".ts", ".css", ".html")
            and not any(part in ("node_modules", "dist", "assets", "tests", "__pycache__", ".venv") for part in p.parts)}

a, b = inventory(OLD), inventory(NEW)
report = {"new_source_files": len(b), "old_source_files": len(a),
                  "added": sorted(b.keys() - a.keys()), "removed": sorted(a.keys() - b.keys()),
                  "changed": sorted(k for k in a.keys() & b.keys() if a[k] != b[k]),
                  "python_source_modules_parsed": len(files),
                  "syntax_errors": syntax, "unreachable_from_entries": unreachable}
if "--summary" in sys.argv:
    report = {k: (len(v) if isinstance(v, list) else v) for k, v in report.items()}
print(json.dumps(report, ensure_ascii=False, indent=2))
