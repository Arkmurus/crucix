"""Render docs/cure/deps.md from census.json + dependency manifests."""

import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# CLAUDE.md §16: no hardcoded checkout path — derive from this file's location.
REPO = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "cure"
C = json.loads((OUT / "census.json").read_text(encoding="utf-8"))
M = C["modules"]
STAMP = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                       capture_output=True, text=True).stdout.strip()

# ---------------------------------------------------------------- python deps
REQ_RE = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]]+)\s*([=<>!~]+)?\s*(.*)$")


def parse_req(path: Path):
    out = []
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = raw.split("#")[0].strip()
        if not ln or ln.startswith("-"):
            continue
        m = REQ_RE.match(ln)
        if not m:
            continue
        name, op, ver = m.group(1), m.group(2) or "", m.group(3) or ""
        out.append({"name": name.split("[")[0], "op": op, "version": ver, "raw": ln})
    return out


svc = parse_req(REPO / "aria_service" / "requirements.txt")
dev = parse_req(REPO / "requirements-dev.txt")

# imported top-level python packages across non-test, non-vendor modules
STDLIB = set(sys.stdlib_module_names)
imported = set()
for f, m in M.items():
    if m["lang"] != "python" or m["is_test"] or m.get("is_vendor"):
        continue
    for imp in m.get("imports", []):
        top = imp.split(".")[0]
        if top and top not in STDLIB:
            imported.add(top.lower())

# distribution name -> import name differences that matter here
ALIAS = {
    "beautifulsoup4": "bs4", "pillow": "pil", "python-dateutil": "dateutil",
    "pyyaml": "yaml", "python-multipart": "multipart", "faster-whisper": "faster_whisper",
    "sentence-transformers": "sentence_transformers", "opencv-python": "cv2",
    "pymupdf": "fitz", "python-docx": "docx", "scikit-learn": "sklearn",
    "google-generativeai": "google", "python-jose": "jose", "pypdf2": "pypdf2",
    "duckduckgo-search": "duckduckgo_search", "uvicorn": "uvicorn",
    "psycopg2-binary": "psycopg2", "redis": "redis", "httpx": "httpx",
}


def import_name(dist: str) -> str:
    d = dist.lower()
    return ALIAS.get(d, d.replace("-", "_"))


unpinned = [d for d in svc if d["op"] != "=="]
unimported = [d for d in svc if import_name(d["name"]) not in imported]

# ------------------------------------------------------------------ node deps
# This repo is a multi-package tree: root, aria-app/, services/wa-listener/.
# Auditing only the root manifest reports the WA listener's own dependencies
# (@whiskeysockets/baileys) and the Next app's (next) as "undeclared".
MANIFEST_PATHS = sorted(
    (p for p in [
        REPO / "package.json",
        REPO / "aria-app" / "package.json",
        REPO / "services" / "wa-listener" / "package.json",
    ] if p.exists()),
    key=lambda p: -len(p.as_posix()),  # deepest first, for nearest-ancestor match
)
MANIFESTS = {}
for p in MANIFEST_PATHS:
    j = json.loads(p.read_text(encoding="utf-8"))
    rel = p.relative_to(REPO).as_posix()
    # optionalDependencies and peerDependencies are REAL declarations. Reading
    # only dependencies/devDependencies reported stripe, pdf-parse, mammoth,
    # xlsx, baileys and discord.js as undeclared when all sit in
    # optionalDependencies (package.json:64-74).
    MANIFESTS[rel] = {
        "dir": p.parent.relative_to(REPO).as_posix(),
        "deps": j.get("dependencies", {}),
        "dev": j.get("devDependencies", {}),
        "opt": j.get("optionalDependencies", {}),
        "peer": j.get("peerDependencies", {}),
    }

pkg = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
deps = pkg.get("dependencies", {})
devdeps = pkg.get("devDependencies", {})
optdeps = pkg.get("optionalDependencies", {})

ALL_DECLARED = set()
for man in MANIFESTS.values():
    ALL_DECLARED |= (set(man["deps"]) | set(man["dev"])
                     | set(man["opt"]) | set(man["peer"]))
# `overrides` pins a transitive version; it is not a direct declaration, but a
# package named there is deliberately managed, so flag it separately not as "missing".
OVERRIDDEN = set(pkg.get("overrides", {}))


def owning_manifest(path: str) -> str:
    for rel, man in MANIFESTS.items():  # deepest first
        d = man["dir"]
        if d == "" or path.startswith(d + "/"):
            return rel
    return "package.json"


def top_spec(spec: str) -> str:
    s = spec.replace("node:", "")
    return "/".join(s.split("/")[:2]) if s.startswith("@") else s.split("/")[0]


node_imports = set()
imports_by_manifest = defaultdict(set)
for f, m in M.items():
    if m["lang"] != "node" or m["is_test"] or m.get("is_vendor"):
        continue
    own = owning_manifest(f)
    for spec in m.get("imports", []):
        if spec.startswith(".") or spec.startswith("/"):
            continue
        t = top_spec(spec)
        node_imports.add(t)
        imports_by_manifest[own].add(t)

node_unused = sorted(d for d in deps if d not in node_imports)
# undeclared = in no manifest, under any dependency field, anywhere in the tree
node_undeclared = sorted(
    i for i in node_imports if i not in ALL_DECLARED and i not in OVERRIDDEN
)
node_override_only = sorted(
    i for i in node_imports if i not in ALL_DECLARED and i in OVERRIDDEN
)
BUILTIN = {
    "fs", "path", "http", "https", "crypto", "os", "url", "util", "events", "stream",
    "child_process", "zlib", "buffer", "net", "tls", "dns", "assert", "readline",
    "worker_threads", "timers", "querystring", "string_decoder", "perf_hooks",
    "process", "module", "v8", "vm", "cluster", "test", "constants", "tty", "punycode",
    "async_hooks", "diagnostics_channel", "inspector", "repl", "sys", "wasi", "trace_events",
}
node_undeclared = [i for i in node_undeclared if i not in BUILTIN]

# ---------------------------------------------------------------------- render
L = []
L.append(f"""<!-- GENERATED by the Cure Protocol Phase 0.2 census engine. -->
# deps.md — dependency audit

**Crucix Cure Protocol — Phase 0.2 static census** · commit `{STAMP}`

> **Scope limit, stated plainly.** This is a *declaration-vs-import* audit. It does
> **not** check for known vulnerabilities: `pip-audit`, `npm audit`, `deptry` and
> `knip` are **not installed** in this environment, so no CVE claim is made here.
> Phase 0.2 asks for "unused, unpinned, vulnerable" — two of three are delivered;
> the third is an explicit gap, not a silent pass.

## Totals

| Manifest | Entries |
|---|---|
| `aria_service/requirements.txt` | {len(svc)} |
| `requirements-dev.txt` | {len(dev)} |
{chr(10).join(f"| `{rel}` deps / dev / **optional** / peer | {len(man['deps'])} / {len(man['dev'])} / **{len(man['opt'])}** / {len(man['peer'])} |" for rel, man in sorted(MANIFESTS.items()))}

> **`optionalDependencies` counts as declared.** The root manifest keeps
> `pdf-parse`, `mammoth`, `xlsx`, `stripe`, `discord.js`, `imap`, `pino`,
> `qrcode-terminal` and `@whiskeysockets/baileys` there (package.json:64-74). An
> audit reading only `dependencies`/`devDependencies` reports all nine as
> undeclared — which is wrong, and was wrong in the first draft of this file.
> Optional is a deliberate choice: `npm install` does not fail when these cannot
> be built, so callers must guard them, and a degraded path must be **wired**
> (see defects.md C-03).

**This is a multi-package tree.** Node dependencies are declared across
{len(MANIFESTS)} manifests ({', '.join(f'`{r}`' for r in sorted(MANIFESTS))}). An audit
that reads only the root manifest reports the WA listener's own
`@whiskeysockets/baileys` and the Next app's `next` as undeclared. Each source file
below is attributed to its nearest ancestor manifest.

## Python — unpinned ({len(unpinned)} of {len(svc)})

A build is only reproducible if every dependency is pinned. Unpinned entries mean two
builds of the same commit can ship different code — which makes `build_rev`
verification (CLAUDE.md §11) prove less than it appears to.

""")

if unpinned:
    L.append("| Package | Constraint |\n|---|---|")
    for d in unpinned:
        L.append(f"| `{d['name']}` | `{d['op']}{d['version']}`" +
                 (" — **no constraint at all**" if not d["op"] else "") + " |")
    L.append("")
else:
    L.append("All entries pinned with `==`.\n")

L.append(f"""
## Python — declared but never imported ({len(unimported)} of {len(svc)})

Candidates only. A package can be required without a source-level `import`: pulled in
as a transitive runtime need, loaded by a plugin system, or used only by tooling. Each
line needs a human check before removal.

""")
L.append("| Package | Declared as |\n|---|---|")
for d in unimported:
    L.append(f"| `{d['name']}` | `{d['raw']}` |")
L.append("")

L.append(f"""
### Known-absent on this machine (not a dependency defect)

CLAUDE.md §16 records that five requirements publish **no win-arm64 wheel** —
`PyMuPDF`, `chromadb`, `opencv-python`, `sentence-transformers` (torch),
`faster-whisper`. All are import-guarded, so the service boots without them and
PDF-via-fitz, RAG, OCR, embeddings and voice transcription are inert locally. If any
appear above as "never imported", verify against the Linux image before concluding
anything — this census ran on Windows/ARM64.

## Node — declared but never imported ({len(node_unused)} of {len(deps)})

""")
if node_unused:
    L.append("| Package | Version |\n|---|---|")
    for d in node_unused:
        L.append(f"| `{d}` | `{deps[d]}` |")
    L.append("")
else:
    L.append("None — every declared dependency is imported somewhere.\n")

L.append(f"""
## Node — imported but not declared ({len(node_undeclared)})

Checked against `dependencies`, `devDependencies`, **`optionalDependencies`** and
`peerDependencies` across all {len(MANIFESTS)} manifests. Node builtins excluded. These
resolve today only by transitive hoisting, and a lockfile change can remove them silently.

""")
if node_undeclared:
    L.append("| Specifier |\n|---|")
    for d in node_undeclared:
        L.append(f"| `{d}` |")
    L.append("")
else:
    L.append("None.\n")

L.append(f"""
### Pinned via `overrides` only ({len(node_override_only)})

Named in the root `overrides` block but not declared as a direct dependency. The
version is deliberately managed, so this is weaker than "undeclared" — but the import
still relies on the package being present transitively.

""")
if node_override_only:
    L.append("| Specifier | Override |\n|---|---|")
    for d in node_override_only:
        L.append(f"| `{d}` | `{pkg.get('overrides', {}).get(d)}` |")
    L.append("")
else:
    L.append("None.\n")

L.append("""
## Required follow-up (Phase 0.2 gap)

1. Install and run `pip-audit` (Python) and `npm audit` (Node) for the vulnerability
   half of this audit. Neither is available here.
2. Install `deptry` / `knip` to cross-check the unused-dependency lists above with a
   purpose-built tool rather than this census's import heuristic.
3. Re-run on Linux so the five arm64-absent packages are analysed in the environment
   that actually ships.
""")

(OUT / "deps.md").write_text("\n".join(L), encoding="utf-8")
print("wrote deps.md")
print("  py reqs:", len(svc), "unpinned:", len(unpinned), "unimported:", len(unimported))
print("  node deps:", len(deps), "unused:", len(node_unused),
      "undeclared:", len(node_undeclared))
print("  undeclared sample:", node_undeclared[:12])
