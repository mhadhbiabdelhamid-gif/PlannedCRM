"""
Finds every route that can remove data, and reports who is allowed to use it.

    python audit_deletes.py

Reads the source only - it changes nothing. Anything marked OPEN or LOGIN ONLY
can be triggered by someone who is not an administrator.
"""
import ast
import os
import sys

DESTRUCTIVE = ("DELETE FROM", "DROP TABLE", "TRUNCATE")
GUARD_RANK = {"admin_required": 3, "manager_required": 2, "login_required": 1}
LABEL = {3: "admin only", 2: "managers too", 1: "LOGIN ONLY", 0: "OPEN"}


def decorator_names(node):
    names = []
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Attribute):
            names.append(target.attr)
    return names


def scan(path):
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except SyntaxError as exc:
        print(f"  ! could not read {path}: {exc}")
        return []

    source_lines = open(path, encoding="utf-8").read().splitlines()
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decs = decorator_names(node)
        if not any(d == "route" for d in decs):
            continue

        body = "\n".join(source_lines[node.lineno - 1:
                                      (node.end_lineno or node.lineno)])
        upper = body.upper()
        hits = [word for word in DESTRUCTIVE if word in upper]
        if not hits:
            continue

        rank = max([GUARD_RANK.get(d, 0) for d in decs] or [0])
        findings.append({
            "file": os.path.basename(path),
            "func": node.name,
            "line": node.lineno,
            "rank": rank,
            "guards": [d for d in decs if d in GUARD_RANK] or ["none"],
            "what": ", ".join(hits),
        })
    return findings


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    files = sorted(f for f in os.listdir(folder)
                   if f.endswith(".py") and f != os.path.basename(__file__))

    all_findings = []
    for name in files:
        all_findings += scan(os.path.join(folder, name))

    if not all_findings:
        print("No routes that delete data were found.")
        return

    all_findings.sort(key=lambda r: (r["rank"], r["file"], r["line"]))
    print(f"{'ROUTE':<38} {'FILE':<20} {'GUARD':<18} ALLOWED")
    print("-" * 92)
    for r in all_findings:
        print(f"{r['func']+'()':<38} {r['file']+':'+str(r['line']):<20} "
              f"{','.join(r['guards']):<18} {LABEL[r['rank']]}")

    weak = [r for r in all_findings if r["rank"] < 3]
    print()
    if weak:
        print(f"{len(weak)} route(s) can delete data without being an admin:")
        for r in weak:
            print(f"  - {r['file']}:{r['line']}  {r['func']}()")
        print("\nAdd @admin_required above each, below its @bp.route line.")
    else:
        print("Every route that deletes data requires an administrator.")


if __name__ == "__main__":
    main()