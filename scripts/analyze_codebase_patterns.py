"""Analyze codebase patterns to understand what the coder needs to replicate."""
from __future__ import annotations

import ast
import os
from collections import Counter


def analyze():
    total_funcs = 0
    total_async = 0
    total_private = 0
    total_with_docstring = 0
    total_with_return = 0
    total_with_try = 0
    total_with_logging = 0
    arg_counts = Counter()
    decorator_counts = Counter()
    return_type_counts = Counter()
    name_prefixes = Counter()
    common_imports = Counter()

    for root, dirs, files in os.walk("aria_service"):
        if "__pycache__" in dirs:
            dirs.remove("__pycache__")
        for f in files:
            if not f.endswith(".py") or f.startswith("test_"):
                continue
            try:
                with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                    tree = ast.parse(fh.read())
            except Exception:
                continue

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    total_funcs += 1
                    if isinstance(node, ast.AsyncFunctionDef):
                        total_async += 1
                    if node.name.startswith("_"):
                        total_private += 1

                    # Docstring
                    if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                        total_with_docstring += 1

                    # Return statement
                    if any(isinstance(n, ast.Return) for n in ast.walk(node)):
                        total_with_return += 1

                    # Try/except
                    if any(isinstance(n, ast.Try) for n in ast.walk(node)):
                        total_with_try += 1

                    # Logging
                    source = ast.unparse(node)
                    if "logger." in source or "log." in source:
                        total_with_logging += 1

                    # Args
                    n_args = len(node.args.args)
                    arg_counts[n_args] += 1

                    # Return type
                    if node.returns:
                        return_type_counts[ast.unparse(node.returns)] += 1

                    # Name prefix
                    prefix = node.name.split("_")[0] if "_" in node.name else node.name
                    name_prefixes[prefix] += 1

                    # Decorators
                    for dec in node.decorator_list:
                        if isinstance(dec, ast.Name):
                            decorator_counts[dec.id] += 1
                        elif isinstance(dec, ast.Attribute):
                            decorator_counts[ast.unparse(dec)] += 1

                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        common_imports[alias.name] += 1
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        common_imports[node.module] += 1

    print("=== CODEBASE STATS ===")
    print(f"Total functions: {total_funcs}")
    print(f"  Async: {total_async} ({100*total_async//total_funcs}%)")
    print(f"  Private (_): {total_private} ({100*total_private//total_funcs}%)")
    print(f"  With docstring: {total_with_docstring} ({100*total_with_docstring//total_funcs}%)")
    print(f"  With return: {total_with_return} ({100*total_with_return//total_funcs}%)")
    print(f"  With try/except: {total_with_try} ({100*total_with_try//total_funcs}%)")
    print(f"  With logging: {total_with_logging} ({100*total_with_logging//total_funcs}%)")
    print()

    print("=== ARGUMENT COUNTS ===")
    for n, count in arg_counts.most_common(10):
        print(f"  {n} args: {count}")

    print()
    print("=== TOP 15 NAME PREFIXES ===")
    for prefix, count in name_prefixes.most_common(15):
        print(f"  {prefix}: {count}")

    print()
    print("=== TOP 10 RETURN TYPES ===")
    for rt, count in return_type_counts.most_common(10):
        print(f"  {rt}: {count}")

    print()
    print("=== TOP 10 DECORATORS ===")
    for dec, count in decorator_counts.most_common(10):
        print(f"  {dec}: {count}")

    print()
    print("=== TOP 15 IMPORTS ===")
    for imp, count in common_imports.most_common(15):
        print(f"  {imp}: {count}")


if __name__ == "__main__":
    analyze()
