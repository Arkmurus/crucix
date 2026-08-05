"""Crucix Cure Protocol - Phase 0.2 static census engine.

Read-only. Emits census.json describing every tracked Python/Node module,
its importers, entrypoints, routes, loops and store references.

Run:  python census.py <repo_root> <out_json>
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# CLAUDE.md §16: never hardcode a checkout path. Derive it from this file's
# location (scripts/cure/census.py -> repo root is two parents up), overridable
# by argv for out-of-tree runs.
_DEFAULT_REPO = Path(__file__).resolve().parents[2]
REPO = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else _DEFAULT_REPO
OUT = (
    Path(sys.argv[2]).resolve()
    if len(sys.argv) > 2
    else REPO / "docs" / "cure" / "census.json"
)
OUT.parent.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- file census


def tracked_files() -> list[str]:
    r = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


ALL = tracked_files()
PY = [f for f in ALL if f.endswith(".py")]
NODE = [
    f
    for f in ALL
    if f.endswith((".mjs", ".js"))
    and "node_modules" not in f
    and not f.startswith("public/vendor")
]

VENDOR_PREFIXES = ("awesome-deepseek-agent-main/", "searxng/")


def is_test(path: str) -> bool:
    return (
        "/tests/" in path
        or path.startswith("test/")
        or path.startswith("tests/")
        or re.search(r"(^|/)test_[^/]+\.(py|mjs|js)$", path) is not None
        or re.search(r"\.(test|spec)\.(mjs|js)$", path) is not None
    )


def is_vendor(path: str) -> bool:
    return path.startswith(VENDOR_PREFIXES)


# ------------------------------------------------------------ python analysis

PY_MOD: dict[str, str] = {}  # dotted module name -> path
for f in PY:
    dotted = f[:-3].replace("/", ".")
    if dotted.endswith(".__init__"):
        dotted = dotted[: -len(".__init__")]
    PY_MOD[dotted] = f

STORE_KEY_RE = re.compile(r"^(crucix:[A-Za-z0-9_]+:[A-Za-z0-9_]+)")
DB_RE = re.compile(r"[\w./\\-]+\.(db|sqlite3?|jsonl?)$")
VOLUME_RE = re.compile(r"^/(data|mnt|var)/[\w./-]*")

LOOP_SPAWNERS = {
    "create_task",
    "_singleton_task",
    "ensure_future",
    "run_coroutine_threadsafe",
    "add_job",
    "spawn",
}
ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "api_route"}


class PyVisitor(ast.NodeVisitor):
    def __init__(self, path: str, src: str):
        self.path = path
        self.lines = src.splitlines()
        self.imports: set[str] = set()
        self.dynamic_imports: set[str] = set()
        self.routes: list[dict] = []
        self.loops: list[dict] = []
        self.spawns: list[dict] = []
        self.redis_keys: set[str] = set()
        self.files_touched: set[str] = set()
        self.volumes: set[str] = set()
        self.sqlite_calls: list[dict] = []
        self.defs: list[str] = []
        self.has_main = False
        self.router_prefix: str | None = None
        self.router_names: set[str] = set()
        self._fn_stack: list[str] = []

    # -- imports
    def visit_Import(self, node: ast.Import):
        for a in node.names:
            self.imports.add(a.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.level:  # relative
            base = self.path[:-3].replace("/", ".")
            if base.endswith(".__init__"):
                base = base[: -len(".__init__")]
            parts = base.split(".")
            up = node.level
            pkg = parts[: max(0, len(parts) - up)]
            mod = ".".join(pkg + ([node.module] if node.module else []))
            self.imports.add(mod)
            for a in node.names:
                self.imports.add(f"{mod}.{a.name}" if mod else a.name)
        elif node.module:
            self.imports.add(node.module)
            for a in node.names:
                self.imports.add(f"{node.module}.{a.name}")
        self.generic_visit(node)

    # -- assignments: APIRouter(prefix=...)
    def visit_Assign(self, node: ast.Assign):
        v = node.value
        if isinstance(v, ast.Call) and _callee(v.func) in ("APIRouter",):
            for kw in v.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    self.router_prefix = str(kw.value.value)
            for t in node.targets:
                if isinstance(t, ast.Name):
                    self.router_names.add(t.id)
        self.generic_visit(node)

    # -- functions
    def _handle_fn(self, node):
        self.defs.append(node.name)
        self._fn_stack.append(node.name)
        for d in node.decorator_list:
            if not isinstance(d, ast.Call):
                continue
            f = d.func
            if not isinstance(f, ast.Attribute) or f.attr not in ROUTE_METHODS:
                continue
            owner = _callee(f.value)
            path_arg = None
            if d.args and isinstance(d.args[0], ast.Constant):
                path_arg = str(d.args[0].value)
            methods = None
            for kw in d.keywords:
                if kw.arg == "methods":
                    methods = _literal(kw.value)
            self.routes.append(
                {
                    "owner": owner,
                    "decorator": f.attr,
                    "methods": methods,
                    "path": path_arg,
                    "handler": node.name,
                    "line": d.lineno,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                }
            )
        self.generic_visit(node)
        self._fn_stack.pop()

    def visit_FunctionDef(self, node):
        self._handle_fn(node)

    def visit_AsyncFunctionDef(self, node):
        self._handle_fn(node)

    # -- loops
    def visit_While(self, node: ast.While):
        cond = node.test
        forever = (isinstance(cond, ast.Constant) and cond.value is True) or (
            isinstance(cond, ast.Name) and cond.id == "True"
        )
        if forever:
            self.loops.append(
                {
                    "line": node.lineno,
                    "enclosing": self._fn_stack[-1] if self._fn_stack else "<module>",
                    "sleeps": _has_sleep(node),
                }
            )
        self.generic_visit(node)

    # -- calls: task spawns, sqlite, store keys
    def visit_Call(self, node: ast.Call):
        name = _callee(node.func)
        short = name.rsplit(".", 1)[-1]
        if short in LOOP_SPAWNERS:
            target = None
            if node.args:
                target = _callee(node.args[0])
            self.spawns.append(
                {
                    "line": node.lineno,
                    "spawner": short,
                    "target": target,
                    "enclosing": self._fn_stack[-1] if self._fn_stack else "<module>",
                }
            )
        if short == "connect" and "sqlite" in name:
            self.sqlite_calls.append({"line": node.lineno, "call": name})
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str):
            s = node.value
            m = STORE_KEY_RE.match(s)
            if m:
                self.redis_keys.add(m.group(1))
            if VOLUME_RE.match(s):
                self.volumes.add(s)
            elif DB_RE.search(s) and len(s) < 200 and " " not in s:
                self.files_touched.add(s)
        self.generic_visit(node)

    def visit_If(self, node: ast.If):
        t = node.test
        if (
            isinstance(t, ast.Compare)
            and isinstance(t.left, ast.Name)
            and t.left.id == "__name__"
        ):
            self.has_main = True
        self.generic_visit(node)


def _callee(n) -> str:
    if isinstance(n, ast.Name):
        return n.id
    if isinstance(n, ast.Attribute):
        return f"{_callee(n.value)}.{n.attr}"
    if isinstance(n, ast.Call):
        return _callee(n.func)
    return "<expr>"


def _literal(n):
    try:
        return ast.literal_eval(n)
    except Exception:
        return None


def _has_sleep(node) -> bool:
    for c in ast.walk(node):
        if isinstance(c, ast.Call) and _callee(c.func).endswith("sleep"):
            return True
    return False


py_data: dict[str, dict] = {}
parse_errors: list[dict] = []

for f in PY:
    p = REPO / f
    try:
        src = p.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except SyntaxError as e:
        parse_errors.append({"file": f, "error": f"{type(e).__name__}: {e}"})
        continue
    except Exception as e:  # unreadable
        parse_errors.append({"file": f, "error": f"{type(e).__name__}: {e}"})
        continue
    v = PyVisitor(f, src)
    v.visit(tree)
    py_data[f] = {
        "loc": len(src.splitlines()),
        "imports": sorted(v.imports),
        "routes": v.routes,
        "router_prefix": v.router_prefix,
        "router_names": sorted(v.router_names),
        "loops": v.loops,
        "spawns": v.spawns,
        "redis_keys": sorted(v.redis_keys),
        "files_touched": sorted(v.files_touched),
        "volumes": sorted(v.volumes),
        "sqlite_calls": v.sqlite_calls,
        "has_main": v.has_main,
        "n_defs": len(v.defs),
        "is_test": is_test(f),
        "is_vendor": is_vendor(f),
    }

# ---- importer graph (who imports me)
importers: dict[str, set[str]] = defaultdict(set)
for f, d in py_data.items():
    for imp in d["imports"]:
        # longest-prefix match onto a real module
        parts = imp.split(".")
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand in PY_MOD:
                tgt = PY_MOD[cand]
                if tgt != f:
                    importers[tgt].add(f)
                break

# dynamic import strings across the whole tree (catches importlib / __import__)
DYN_RE = re.compile(r"""import_module\(\s*["']([\w.]+)["']|__import__\(\s*["']([\w.]+)["']""")
for f in PY:
    try:
        src = (REPO / f).read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    for m in DYN_RE.finditer(src):
        name = m.group(1) or m.group(2)
        parts = name.split(".")
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand in PY_MOD and PY_MOD[cand] != f:
                importers[PY_MOD[cand]].add(f)
                break

# ---- string-reference sweep: module basename mentioned in any tracked text file
#      (catches config-driven / getattr dispatch that AST import analysis misses)
name_hits: dict[str, set[str]] = defaultdict(set)
basenames = {}
for dotted, path in PY_MOD.items():
    bn = dotted.rsplit(".", 1)[-1]
    if bn and bn != "__init__":
        basenames.setdefault(bn, []).append(path)

TEXT_EXT = (".py", ".mjs", ".js", ".json", ".md", ".yml", ".yaml", ".toml", ".sh", ".html")

# The census's OWN OUTPUT must never count as evidence that a module is alive.
# docs/cure/modules.md lists every DEAD-CANDIDATE by path, so once it is
# committed the sweep finds those paths in a non-test file and promotes them to
# ACTIVE-STATIC. Observed exactly that: DEAD-CANDIDATE collapsed 109 -> 27 and
# DORMANT 19 -> 2 on the first re-run after the reports were committed, with no
# code deleted. A measurement that reads its own report is measuring itself.
SELF_OUTPUT_PREFIXES = ("docs/cure/",)

# Tokenize each file ONCE, then set-intersect against module basenames.
# (A per-basename regex sweep is O(files x basenames) and does not finish.)
LOOKUP = {bn: paths for bn, paths in basenames.items() if len(bn) >= 5}
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{4,}")
for f in ALL:
    if not f.endswith(TEXT_EXT):
        continue
    if f.startswith(SELF_OUTPUT_PREFIXES):
        continue
    try:
        src = (REPO / f).read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    for tok in set(TOKEN_RE.findall(src)) & LOOKUP.keys():
        for pth in LOOKUP[tok]:
            if pth != f:
                name_hits[pth].add(f)

# ------------------------------------------------------------- node analysis

NODE_IMPORT_RE = re.compile(
    r"""(?:import\s[^'"]*from\s*['"]([^'"]+)['"])|(?:require\(\s*['"]([^'"]+)['"]\s*\))|(?:import\(\s*['"]([^'"]+)['"]\s*\))"""
)
NODE_ROUTE_RE = re.compile(
    r"""\b(app|router|api|r)\s*\.\s*(get|post|put|patch|delete|use|all)\s*\(\s*['"`]([^'"`]+)['"`]"""
)
NODE_TIMER_RE = re.compile(r"\b(setInterval|setTimeout|cron\.schedule|schedule\.scheduleJob)\s*\(")

node_data: dict[str, dict] = {}
for f in NODE:
    try:
        src = (REPO / f).read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    imps = []
    for m in NODE_IMPORT_RE.finditer(src):
        imps.append(m.group(1) or m.group(2) or m.group(3))
    routes = [
        {"owner": m.group(1), "method": m.group(2), "path": m.group(3),
         "line": src[: m.start()].count("\n") + 1}
        for m in NODE_ROUTE_RE.finditer(src)
    ]
    timers = [
        {"kind": m.group(1), "line": src[: m.start()].count("\n") + 1}
        for m in NODE_TIMER_RE.finditer(src)
    ]
    node_data[f] = {
        "loc": len(src.splitlines()),
        "imports": sorted(set(i for i in imps if i)),
        "routes": routes,
        "timers": timers,
        "redis_keys": sorted(set(m.group(1) for m in STORE_KEY_RE.finditer(src) if m)),
        "is_test": is_test(f),
        "is_vendor": is_vendor(f),
    }

# node importer graph (resolve relative specifiers)
node_importers: dict[str, set[str]] = defaultdict(set)
node_set = set(NODE)
for f, d in node_data.items():
    base = Path(f).parent
    for spec in d["imports"]:
        if not spec.startswith("."):
            continue
        cand = (base / spec).as_posix()
        cand = re.sub(r"/\./", "/", cand)
        # normalise ..
        parts: list[str] = []
        for seg in cand.split("/"):
            if seg == "..":
                if parts:
                    parts.pop()
            elif seg not in (".", ""):
                parts.append(seg)
        cand = "/".join(parts)
        for guess in (cand, cand + ".mjs", cand + ".js", cand + "/index.mjs", cand + "/index.js"):
            if guess in node_set and guess != f:
                node_importers[guess].add(f)
                break

# ------------------------------------------------- entrypoint / asset proofs
# A module can be live without ANY importer: a deployment entrypoint is named
# in a Dockerfile/fly.toml/package.json/CI workflow, and a browser asset is
# named in an HTML <script src>. Missing these marks live code DEAD-CANDIDATE
# (it marked the aria-wa listener dead on the first pass).

DEPLOY_RE = re.compile(
    r"(^|/)(Dockerfile[\w.\-]*|Procfile[\w.\-]*|fly[\w.\-]*\.toml|package\.json|"
    r"ecosystem\.config\.[cm]?js)$"
)
WORKFLOW_RE = re.compile(r"^\.github/workflows/.*\.ya?ml$")
SCRIPT_RE = re.compile(r"(^|/)([\w.\-]*\.sh|[\w.\-]*\.ps1|Makefile)$")

DEPLOY_MANIFESTS = [f for f in ALL if DEPLOY_RE.search(f) or WORKFLOW_RE.match(f)]
SCRIPT_MANIFESTS = [f for f in ALL if SCRIPT_RE.search(f)]

CODE_FILES = [f for f in ALL if f.endswith((".py", ".mjs", ".js"))]
BASENAME_TO_PATHS: dict[str, list[str]] = defaultdict(list)
for f in CODE_FILES:
    BASENAME_TO_PATHS[f.rsplit("/", 1)[-1]].append(f)
# dotted-module form, for `python -m aria_service.main`
DOTTED_TO_PATH = dict(PY_MOD)


def _strip_comments(src: str, path: str) -> str:
    """Drop whole-line comments. A file named only in a comment is not a proof."""
    if path.endswith(".json"):
        return src  # JSON has no comments; '#' inside strings is data
    out = []
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith("#") or s.startswith("//"):
            continue
        out.append(ln)
    return "\n".join(out)


# INVOKED = something runs it. SHIPPED = it is merely copied into the image.
# Both prove production reach; only the first is an entrypoint.
INVOKE_RES = [
    re.compile(r"""["']([\w./\-]+\.(?:mjs|js|py))["']"""),          # exec-form array
    re.compile(r"""\b(?:node|python3?|bash|sh|tsx)\s+([\w./\-]+\.(?:mjs|js|py))"""),
]
SHIP_RES = [
    re.compile(r"""^\s*COPY\s+(?:--\S+\s+)*([\w./\-]+\.(?:mjs|js|py))""", re.M),
]
DOTTED_RES = [
    re.compile(r"""\bpython3?\s+-m\s+([\w.]+)"""),                  # shell form
    re.compile(r"""["']-m["']\s*,\s*["']([\w.]+)["']"""),           # exec form
    re.compile(r"""\buvicorn\s+([\w.]+):"""),
]


def _resolve(tok: str, found: set[str]) -> None:
    tok = tok.lstrip("./")
    if tok in tracked_set_all:
        found.add(tok)
        return
    paths = BASENAME_TO_PATHS.get(tok.rsplit("/", 1)[-1], [])
    if len(paths) == 1:  # unambiguous basename, e.g. CMD ["node","x.mjs"]
        found.add(paths[0])


def _refs_in(src: str, path: str) -> tuple[set[str], set[str]]:
    """Return (invoked, shipped) tracked paths named by one manifest."""
    cleaned = _strip_comments(src, path)
    invoked: set[str] = set()
    shipped: set[str] = set()
    for rx in INVOKE_RES:
        for m in rx.finditer(cleaned):
            _resolve(m.group(1), invoked)
    for rx in DOTTED_RES:
        for m in rx.finditer(cleaned):
            if m.group(1) in DOTTED_TO_PATH:
                invoked.add(DOTTED_TO_PATH[m.group(1)])
    for rx in SHIP_RES:
        for m in rx.finditer(cleaned):
            _resolve(m.group(1), shipped)
    return invoked, shipped - invoked


tracked_set_all = set(ALL)
entrypoint_refs: dict[str, set[str]] = defaultdict(set)   # CMD / npm script / CI step
shipped_refs: dict[str, set[str]] = defaultdict(set)      # COPY'd into the image
script_refs: dict[str, set[str]] = defaultdict(set)       # named by a dev .sh/.ps1
for mf in DEPLOY_MANIFESTS:
    try:
        src = (REPO / mf).read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    inv, shp = _refs_in(src, mf)
    for tgt in inv:
        if tgt != mf:
            entrypoint_refs[tgt].add(mf)
    for tgt in shp:
        if tgt != mf:
            shipped_refs[tgt].add(mf)
for mf in SCRIPT_MANIFESTS:
    try:
        src = (REPO / mf).read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    inv, shp = _refs_in(src, mf)
    for tgt in inv | shp:
        if tgt != mf:
            script_refs[tgt].add(mf)

# HTML asset references: <script src=...> / <link href=...>
HTML_REF_RE = re.compile(r"""(?:src|href)\s*=\s*['"]([^'"]+)['"]""")
asset_refs: dict[str, set[str]] = defaultdict(set)
tracked_set = set(ALL)
for hf in [f for f in ALL if f.endswith((".html", ".htm"))]:
    try:
        src = (REPO / hf).read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    base = Path(hf).parent
    for m in HTML_REF_RE.finditer(src):
        ref = m.group(1).split("?")[0].split("#")[0]
        if not ref or ref.startswith(("http://", "https://", "//", "data:", "mailto:")):
            continue
        cands = []
        if ref.startswith("/"):
            cands.append("public" + ref)
            cands.append(ref.lstrip("/"))
        else:
            cands.append((base / ref).as_posix())
            cands.append(("public" / Path(ref)).as_posix())
        for cand in cands:
            parts: list[str] = []
            for seg in cand.split("/"):
                if seg == "..":
                    if parts:
                        parts.pop()
                elif seg not in (".", ""):
                    parts.append(seg)
            norm = "/".join(parts)
            if norm in tracked_set and norm != hf:
                asset_refs[norm].add(hf)
                break

# ------------------------------------------------------------ classification


def classify(path: str, imps: set[str], hits: set[str]) -> dict:
    """Static + test proofs. Runtime proof is UNKNOWN until Phase 0.3."""
    non_test_importers = {i for i in imps if not is_test(i)}
    test_importers = {i for i in imps if is_test(i)}
    non_test_hits = {h for h in hits if not is_test(h)}
    test_hits = {h for h in hits if is_test(h)}
    eps = entrypoint_refs.get(path, set())
    shipped = shipped_refs.get(path, set())
    assets = asset_refs.get(path, set())
    scripts = script_refs.get(path, set())

    static_referenced = bool(non_test_importers) or bool(non_test_hits)
    tested = bool(test_importers) or bool(test_hits)

    # Strongest proof wins: invoked > imported > shipped > browser-served >
    # dev-script > test-only > nothing. Only the last is a deletion candidate.
    if is_test(path):
        cls = "TEST"
    elif eps:
        cls = "DEPLOY-ENTRYPOINT"  # a CMD / npm script / CI step runs it
    elif static_referenced:
        cls = "ACTIVE-STATIC"  # imported somewhere; runtime unconfirmed
    elif shipped:
        cls = "DEPLOYED-DEP"  # COPY'd into an image but no importer found
    elif assets:
        cls = "SERVED-ASSET"  # loaded by an HTML <script src>
    elif scripts:
        cls = "SCRIPT-REFERENCED"  # only a dev .sh/.ps1/Makefile invokes it
    elif tested:
        cls = "DORMANT"  # only tests reach it
    else:
        cls = "DEAD-CANDIDATE"
    return {
        "classification": cls,
        "proof_static": (
            "INVOKED"
            if eps
            else "REFERENCED"
            if static_referenced
            else "SHIPPED"
            if shipped
            else "SERVED-ASSET"
            if assets
            else "SCRIPT-ONLY"
            if scripts
            else "UNREFERENCED"
        ),
        "proof_runtime": "UNKNOWN-PHASE-0.3-NOT-RUN",
        "proof_test": "TESTED" if tested else "UNTESTED",
        "entrypoint_refs": sorted(eps)[:10],
        "shipped_refs": sorted(shipped)[:10],
        "asset_refs": sorted(assets)[:10],
        "script_refs": sorted(scripts)[:10],
        "importers_non_test": sorted(non_test_importers)[:20],
        "n_importers_non_test": len(non_test_importers),
        "n_importers_test": len(test_importers),
        "n_string_hits_non_test": len(non_test_hits),
    }


modules = {}
for f, d in py_data.items():
    imps = importers.get(f, set())
    hits = name_hits.get(f, set())
    modules[f] = {**d, **classify(f, imps, hits), "lang": "python"}
for f, d in node_data.items():
    imps = node_importers.get(f, set())
    modules[f] = {**d, **classify(f, imps, set()), "lang": "node"}

census = {
    "repo": str(REPO),
    "counts": {
        "tracked_files": len(ALL),
        "python_files": len(PY),
        "node_files": len(NODE),
        "python_parsed": len(py_data),
        "parse_errors": len(parse_errors),
    },
    "parse_errors": parse_errors,
    "modules": modules,
}

OUT.write_text(json.dumps(census, indent=1), encoding="utf-8")

# ------------------------------------------------------------------- summary
by_cls: dict[str, int] = defaultdict(int)
for f, m in modules.items():
    by_cls[m["classification"]] += 1
print("tracked:", len(ALL), "py:", len(PY), "node:", len(NODE))
print("parse_errors:", len(parse_errors))
for k in sorted(by_cls):
    print(f"  {k}: {by_cls[k]}")
print("routes(py):", sum(len(m.get("routes", [])) for m in modules.values() if m["lang"] == "python"))
print("routes(node):", sum(len(m.get("routes", [])) for m in modules.values() if m["lang"] == "node"))
print("while-True loops:", sum(len(m.get("loops", [])) for m in modules.values() if m["lang"] == "python"))
print("task spawns:", sum(len(m.get("spawns", [])) for m in modules.values() if m["lang"] == "python"))
print("wrote:", OUT)
