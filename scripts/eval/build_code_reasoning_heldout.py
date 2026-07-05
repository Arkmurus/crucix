"""R-F2431 — builder for the code-reasoning held-out eval set.

Emits ``data/eval/code_reasoning_heldout.jsonl`` from readable task definitions
below. Each task is a SELF-CONTAINED, stdlib-only Python bug mirroring a REAL
ARIA bug class (each is tagged with the R-number whose fix it is derived from),
paired with a reproduce test that FAILS on the buggy code and PASSES on the
fix — the R-F1685 ``reproduce_fail_to_pass`` discipline, scoped to a sandbox so
the eval is objective, verifiable and reproducible on any box with just
Python + pytest (no chromadb/torch/aria_service imports).

Why a builder (not hand-written JSONL): the fixtures embed multi-line source;
authoring them as Python triple-quoted strings keeps them readable and diffable.
The emitted JSONL is committed and is the frozen held-out set the harness runs.

Regenerate:  python scripts/eval/build_code_reasoning_heldout.py
The task set is FROZEN once committed — adding tasks is a new R-number so the
baseline stays comparable across models (mirrors the 500-Q DD eval freeze, §1).
"""
from __future__ import annotations

import json
from pathlib import Path

_OUT = Path(__file__).resolve().parents[2] / "data" / "eval" / "code_reasoning_heldout.jsonl"


# Each task: id, source_r, bug_class, instruction, module_path, buggy, gold,
# test_path, test_content, fail_to_pass (node that must go FAIL->PASS),
# pass_to_pass (nodes that must STAY green — regression guard).
TASKS: list[dict] = []


def _t(**kw):
    kw.setdefault("tier", "floor")
    TASKS.append(kw)


def _h(**kw):
    kw["tier"] = "hard"
    TASKS.append(kw)


# ── 1. boundary/clamp — the REAL first-gold canary (R-F1926) ────────────────
_t(
    id="cre-001-clamp-upper-bound",
    source_r="R-F1926",
    bug_class="boundary/clamp",
    instruction=(
        "Fix gap: clamp_percentage returns 0.0 for values > 100 instead of "
        "clamping to 100.0. The documented contract clamps over-range values "
        "to 100.0. The reproduce test fails on the bug."
    ),
    module_path="canary.py",
    buggy=(
        "def clamp_percentage(value: float) -> float:\n"
        '    """Clamp value into [0, 100]. >100 -> 100.0, <0 -> 0.0."""\n'
        "    if value <= 0:\n"
        "        return 0.0\n"
        "    if value > 100:\n"
        "        return 0.0\n"
        "    return value\n"
    ),
    gold=(
        "def clamp_percentage(value: float) -> float:\n"
        '    """Clamp value into [0, 100]. >100 -> 100.0, <0 -> 0.0."""\n'
        "    if value <= 0:\n"
        "        return 0.0\n"
        "    if value > 100:\n"
        "        return 100.0\n"
        "    return value\n"
    ),
    test_path="test_canary.py",
    test_content=(
        "from canary import clamp_percentage\n\n"
        "def test_upper_bound():\n"
        "    assert clamp_percentage(150) == 100.0\n\n"
        "def test_lower_bound():\n"
        "    assert clamp_percentage(-5) == 0.0\n\n"
        "def test_in_range():\n"
        "    assert clamp_percentage(42) == 42\n"
    ),
    fail_to_pass="test_canary.py::test_upper_bound",
    pass_to_pass=["test_canary.py::test_lower_bound", "test_canary.py::test_in_range"],
)

# ── 2. nonetype-guard crash (researcher.py simulated-bug class) ─────────────
_t(
    id="cre-002-none-guard-crash",
    source_r="R-F(coder-gold)",
    bug_class="nonetype/guard",
    instruction=(
        "Fix gap: normalise_scores crashes with TypeError when the input list "
        "contains a None entry. It must skip None values, not crash. The "
        "reproduce test passes a list containing None."
    ),
    module_path="scoring.py",
    buggy=(
        "def normalise_scores(scores):\n"
        '    """Return scores scaled to [0,1] by max; skip missing entries."""\n'
        "    clean = [s for s in scores]\n"
        "    hi = max(clean) if clean else 1.0\n"
        "    return [s / hi for s in clean]\n"
    ),
    gold=(
        "def normalise_scores(scores):\n"
        '    """Return scores scaled to [0,1] by max; skip missing entries."""\n'
        "    clean = [s for s in scores if s is not None]\n"
        "    hi = max(clean) if clean else 1.0\n"
        "    return [s / hi for s in clean]\n"
    ),
    test_path="test_scoring.py",
    test_content=(
        "from scoring import normalise_scores\n\n"
        "def test_skips_none():\n"
        "    out = normalise_scores([2, None, 4])\n"
        "    assert out == [0.5, 1.0]\n\n"
        "def test_no_none():\n"
        "    assert normalise_scores([1, 2]) == [0.5, 1.0]\n"
    ),
    fail_to_pass="test_scoring.py::test_skips_none",
    pass_to_pass=["test_scoring.py::test_no_none"],
)

# ── 3. missing import (R-F1464 — missing `os` broke the judge) ──────────────
_t(
    id="cre-003-missing-import",
    source_r="R-F1464",
    bug_class="missing-import",
    instruction=(
        "Fix gap: resolve_model reads an environment variable via os.environ "
        "but the module forgot to import os, raising NameError at call time. "
        "Add the missing import."
    ),
    module_path="judge.py",
    buggy=(
        "def resolve_model(default='deepseek-chat'):\n"
        '    """Return the model from ARIA_JUDGE_MODEL env, else the default."""\n'
        "    return os.environ.get('ARIA_JUDGE_MODEL', default)\n"
    ),
    gold=(
        "import os\n\n\n"
        "def resolve_model(default='deepseek-chat'):\n"
        '    """Return the model from ARIA_JUDGE_MODEL env, else the default."""\n'
        "    return os.environ.get('ARIA_JUDGE_MODEL', default)\n"
    ),
    test_path="test_judge.py",
    test_content=(
        "from judge import resolve_model\n\n"
        "def test_default():\n"
        "    assert resolve_model() == 'deepseek-chat'\n\n"
        "def test_override(monkeypatch):\n"
        "    monkeypatch.setenv('ARIA_JUDGE_MODEL', 'aria-llm')\n"
        "    assert resolve_model() == 'aria-llm'\n"
    ),
    fail_to_pass="test_judge.py::test_default",
    # No pass_to_pass: a missing import breaks the whole module, so no sibling
    # test survives on the buggy code to serve as a regression guard. The
    # validator (validate_task) correctly rejects any pass_to_pass that also
    # fails on the bug — honest over a padded-but-invalid guard.
    pass_to_pass=[],
)

# ── 4. off-by-one window (R-F190 / gdelt off-by-one class) ──────────────────
_t(
    id="cre-004-off-by-one-window",
    source_r="R-F190",
    bug_class="off-by-one",
    instruction=(
        "Fix gap: last_n returns n-1 items instead of the last n because the "
        "slice start index is off by one. It must return exactly the last n "
        "items. The reproduce test asserts the count and contents."
    ),
    module_path="window.py",
    buggy=(
        "def last_n(items, n):\n"
        '    """Return the last n items of the list (n>=0)."""\n'
        "    if n <= 0:\n"
        "        return []\n"
        "    return items[len(items) - n + 1:]\n"
    ),
    gold=(
        "def last_n(items, n):\n"
        '    """Return the last n items of the list (n>=0)."""\n'
        "    if n <= 0:\n"
        "        return []\n"
        "    return items[len(items) - n:]\n"
    ),
    test_path="test_window.py",
    test_content=(
        "from window import last_n\n\n"
        "def test_last_three():\n"
        "    assert last_n([1, 2, 3, 4, 5], 3) == [3, 4, 5]\n\n"
        "def test_zero():\n"
        "    assert last_n([1, 2], 0) == []\n"
    ),
    fail_to_pass="test_window.py::test_last_three",
    pass_to_pass=["test_window.py::test_zero"],
)

# ── 5. async/await misuse (single-process loop discipline class) ────────────
_t(
    id="cre-005-forgot-await",
    source_r="R-F(async-misuse)",
    bug_class="async/await",
    instruction=(
        "Fix gap: fetch_total forgets to await the async helper, so it returns "
        "a coroutine object instead of the summed integer. Await the helper. "
        "The reproduce test asserts the returned value equals the sum."
    ),
    module_path="asum.py",
    buggy=(
        "async def _load(x):\n"
        "    return x * 2\n\n\n"
        "async def fetch_total(values):\n"
        '    """Return the sum of _load(v) over values."""\n'
        "    total = 0\n"
        "    for v in values:\n"
        "        total += _load(v)\n"
        "    return total\n"
    ),
    gold=(
        "async def _load(x):\n"
        "    return x * 2\n\n\n"
        "async def fetch_total(values):\n"
        '    """Return the sum of _load(v) over values."""\n'
        "    total = 0\n"
        "    for v in values:\n"
        "        total += await _load(v)\n"
        "    return total\n"
    ),
    test_path="test_asum.py",
    test_content=(
        "import asyncio\n"
        "from asum import fetch_total\n\n"
        "def test_total():\n"
        "    assert asyncio.run(fetch_total([1, 2, 3])) == 12\n\n"
        "def test_empty():\n"
        "    assert asyncio.run(fetch_total([])) == 0\n"
    ),
    fail_to_pass="test_asum.py::test_total",
    pass_to_pass=["test_asum.py::test_empty"],
)

# ── 6. nested-dict type bug (the reward-is-a-dict class) ────────────────────
_t(
    id="cre-006-nested-dict-access",
    source_r="R-F1674",
    bug_class="type/nested-access",
    instruction=(
        "Fix gap: mean_reward crashes with TypeError because each row's "
        "'reward' is a nested dict {'score': float}, not a float. It must read "
        "reward['score']. The reproduce test passes nested-dict rows."
    ),
    module_path="reward.py",
    buggy=(
        "def mean_reward(rows):\n"
        '    """Return the mean of each row\'s reward score."""\n'
        "    if not rows:\n"
        "        return 0.0\n"
        "    return sum(float(r['reward']) for r in rows) / len(rows)\n"
    ),
    gold=(
        "def mean_reward(rows):\n"
        '    """Return the mean of each row\'s reward score."""\n'
        "    if not rows:\n"
        "        return 0.0\n"
        "    return sum(float(r['reward']['score']) for r in rows) / len(rows)\n"
    ),
    test_path="test_reward.py",
    test_content=(
        "from reward import mean_reward\n\n"
        "def test_nested():\n"
        "    rows = [{'reward': {'score': 1.0}}, {'reward': {'score': 3.0}}]\n"
        "    assert mean_reward(rows) == 2.0\n\n"
        "def test_empty():\n"
        "    assert mean_reward([]) == 0.0\n"
    ),
    fail_to_pass="test_reward.py::test_nested",
    pass_to_pass=["test_reward.py::test_empty"],
)

# ── 7. fail-closed ACL cross-tenant leak (R-F2401 GDPR class) ───────────────
_t(
    id="cre-007-fail-closed-acl",
    source_r="R-F2401",
    bug_class="authz/fail-closed",
    instruction=(
        "Fix gap: get_watchlist leaks other tenants' items — it returns ALL "
        "items when a requester's owner id does not match, instead of "
        "returning only items owned by that requester. Filter by owner "
        "(fail-closed). The reproduce test asserts a stranger sees only their "
        "own (empty) list."
    ),
    module_path="acl.py",
    buggy=(
        "def get_watchlist(items, owner):\n"
        '    """Return only the watchlist items owned by ``owner``."""\n'
        "    mine = [i for i in items if i['owner'] == owner]\n"
        "    if not mine:\n"
        "        return items\n"
        "    return mine\n"
    ),
    gold=(
        "def get_watchlist(items, owner):\n"
        '    """Return only the watchlist items owned by ``owner``."""\n'
        "    return [i for i in items if i['owner'] == owner]\n"
    ),
    test_path="test_acl.py",
    test_content=(
        "from acl import get_watchlist\n\n"
        "ITEMS = [{'owner': 'a', 'v': 1}, {'owner': 'b', 'v': 2}]\n\n"
        "def test_stranger_sees_nothing():\n"
        "    assert get_watchlist(ITEMS, 'stranger') == []\n\n"
        "def test_owner_sees_own():\n"
        "    assert get_watchlist(ITEMS, 'a') == [{'owner': 'a', 'v': 1}]\n"
    ),
    fail_to_pass="test_acl.py::test_stranger_sees_nothing",
    pass_to_pass=["test_acl.py::test_owner_sees_own"],
)

# ── 8. dedup guard for duplicate tool names (R-F2398 DeepSeek 400 class) ────
_t(
    id="cre-008-dedup-tool-names",
    source_r="R-F2398",
    bug_class="dedup-guard",
    instruction=(
        "Fix gap: build_tools can emit duplicate tool names, which DeepSeek "
        "rejects with 400 'Tool names must be unique'. Deduplicate by name, "
        "keeping first occurrence and preserving order. The reproduce test "
        "passes a list with a duplicate name."
    ),
    module_path="tools.py",
    buggy=(
        "def build_tools(specs):\n"
        '    """Return specs deduped by \'name\', preserving first-seen order."""\n'
        "    out = []\n"
        "    for s in specs:\n"
        "        out.append(s)\n"
        "    return out\n"
    ),
    gold=(
        "def build_tools(specs):\n"
        '    """Return specs deduped by \'name\', preserving first-seen order."""\n'
        "    seen = set()\n"
        "    out = []\n"
        "    for s in specs:\n"
        "        if s['name'] in seen:\n"
        "            continue\n"
        "        seen.add(s['name'])\n"
        "        out.append(s)\n"
        "    return out\n"
    ),
    test_path="test_tools.py",
    test_content=(
        "from tools import build_tools\n\n"
        "def test_dedup():\n"
        "    specs = [{'name': 'fetch'}, {'name': 'fetch'}, {'name': 'search'}]\n"
        "    out = build_tools(specs)\n"
        "    assert [s['name'] for s in out] == ['fetch', 'search']\n\n"
        "def test_no_dup():\n"
        "    specs = [{'name': 'a'}, {'name': 'b'}]\n"
        "    assert build_tools(specs) == specs\n"
    ),
    fail_to_pass="test_tools.py::test_dedup",
    pass_to_pass=["test_tools.py::test_no_dup"],
)

# ── 9. routing regex omission (§22a doc-reference class) ────────────────────
_t(
    id="cre-009-routing-regex-omission",
    source_r="R-F(22a doc-review routing)",
    bug_class="regex/classifier",
    instruction=(
        "Fix gap: is_document_reference must match legal-doc nouns so an "
        "attached-document review routes to the LLM path, not an external "
        "tool. It currently misses 'nda', 'agreement' and 'contract'. Extend "
        "the pattern to cover them (case-insensitive). The reproduce test "
        "checks 'review the NDA for feedback'."
    ),
    module_path="routing.py",
    buggy=(
        "import re\n\n"
        "_DOC_RE = re.compile(r'\\b(document|attachment|file)\\b', re.I)\n\n\n"
        "def is_document_reference(text):\n"
        '    """True if the text references an attached document to review."""\n'
        "    return bool(_DOC_RE.search(text))\n"
    ),
    gold=(
        "import re\n\n"
        "_DOC_RE = re.compile(\n"
        "    r'\\b(document|attachment|file|nda|agreement|contract)\\b', re.I)\n\n\n"
        "def is_document_reference(text):\n"
        '    """True if the text references an attached document to review."""\n'
        "    return bool(_DOC_RE.search(text))\n"
    ),
    test_path="test_routing.py",
    test_content=(
        "from routing import is_document_reference\n\n"
        "def test_nda():\n"
        "    assert is_document_reference('review the NDA for feedback') is True\n\n"
        "def test_plain_doc():\n"
        "    assert is_document_reference('check this document') is True\n\n"
        "def test_negative():\n"
        "    assert is_document_reference('who is the CEO of Acme') is False\n"
    ),
    fail_to_pass="test_routing.py::test_nda",
    pass_to_pass=["test_routing.py::test_plain_doc", "test_routing.py::test_negative"],
)

# ── 10. duplicate-registration first-wins shadow (R-F2278 class) ────────────
_t(
    id="cre-010-duplicate-registration",
    source_r="R-F2278",
    bug_class="registry/dedup",
    instruction=(
        "Fix gap: register_routes silently lets a duplicate path overwrite an "
        "earlier registration, shadowing it (FastAPI-first-wins class). It "
        "must raise ValueError on a duplicate path so collisions are caught. "
        "The reproduce test registers the same path twice and expects "
        "ValueError."
    ),
    module_path="router.py",
    buggy=(
        "def register_routes(pairs):\n"
        '    """Build a path->handler map; duplicate paths are an error."""\n'
        "    table = {}\n"
        "    for path, handler in pairs:\n"
        "        table[path] = handler\n"
        "    return table\n"
    ),
    gold=(
        "def register_routes(pairs):\n"
        '    """Build a path->handler map; duplicate paths are an error."""\n'
        "    table = {}\n"
        "    for path, handler in pairs:\n"
        "        if path in table:\n"
        "            raise ValueError(f'duplicate route: {path}')\n"
        "        table[path] = handler\n"
        "    return table\n"
    ),
    test_path="test_router.py",
    test_content=(
        "import pytest\n"
        "from router import register_routes\n\n"
        "def test_duplicate_raises():\n"
        "    with pytest.raises(ValueError):\n"
        "        register_routes([('/a', 1), ('/a', 2)])\n\n"
        "def test_unique_ok():\n"
        "    assert register_routes([('/a', 1), ('/b', 2)]) == {'/a': 1, '/b': 2}\n"
    ),
    fail_to_pass="test_router.py::test_duplicate_raises",
    pass_to_pass=["test_router.py::test_unique_ok"],
)


# ════════════════════ HARD TIER — subtle, discriminating bugs ══════════════
# The floor tier saturates a strong model (DeepSeek = 100%). These require
# reading semantics, not spotting a one-token typo — the tier meant to
# DISCRIMINATE a candidate sovereign from DeepSeek.

# H1 — mutable default argument (classic shared-state trap)
_h(
    id="creh-011-mutable-default-arg",
    source_r="R-F(state-leak)",
    bug_class="mutable-default",
    instruction=(
        "Fix gap: collect(item, into=[]) reuses the SAME list across calls "
        "because of a mutable default argument, so state leaks between "
        "independent calls. Each call with no 'into' must start fresh. The "
        "reproduce test makes two independent calls and asserts they don't share."
    ),
    module_path="collect.py",
    buggy=(
        "def collect(item, into=[]):\n"
        '    """Append item to ``into`` and return it; default starts as a fresh list."""\n'
        "    into.append(item)\n"
        "    return into\n"
    ),
    gold=(
        "def collect(item, into=None):\n"
        '    """Append item to ``into`` and return it; default starts as a fresh list."""\n'
        "    if into is None:\n"
        "        into = []\n"
        "    into.append(item)\n"
        "    return into\n"
    ),
    test_path="test_collect.py",
    test_content=(
        "from collect import collect\n\n"
        "def test_no_shared_state():\n"
        "    a = collect(1)\n"
        "    b = collect(2)\n"
        "    assert a == [1] and b == [2]\n\n"
        "def test_explicit_target():\n"
        "    target = []\n"
        "    collect(9, target)\n"
        "    assert target == [9]\n"
    ),
    fail_to_pass="test_collect.py::test_no_shared_state",
    pass_to_pass=["test_collect.py::test_explicit_target"],
)

# H2 — order-sensitive bracket matching (count != structure)
_h(
    id="creh-012-bracket-order",
    source_r="R-F(parser)",
    bug_class="parser/order",
    instruction=(
        "Fix gap: is_balanced only counts opening vs closing brackets, so it "
        "wrongly accepts mismatched/misordered brackets like '(]' and '([)]'. "
        "It must verify nesting with a stack. The reproduce test asserts '(]' "
        "is NOT balanced while '([])' is."
    ),
    module_path="brackets.py",
    buggy=(
        "def is_balanced(s):\n"
        '    """True iff brackets ()[]{} are correctly nested and matched."""\n'
        "    pairs = {')': '(', ']': '[', '}': '{'}\n"
        "    opens = set(pairs.values())\n"
        "    depth = 0\n"
        "    for ch in s:\n"
        "        if ch in opens:\n"
        "            depth += 1\n"
        "        elif ch in pairs:\n"
        "            depth -= 1\n"
        "            if depth < 0:\n"
        "                return False\n"
        "    return depth == 0\n"
    ),
    gold=(
        "def is_balanced(s):\n"
        '    """True iff brackets ()[]{} are correctly nested and matched."""\n'
        "    pairs = {')': '(', ']': '[', '}': '{'}\n"
        "    opens = set(pairs.values())\n"
        "    stack = []\n"
        "    for ch in s:\n"
        "        if ch in opens:\n"
        "            stack.append(ch)\n"
        "        elif ch in pairs:\n"
        "            if not stack or stack.pop() != pairs[ch]:\n"
        "                return False\n"
        "    return not stack\n"
    ),
    test_path="test_brackets.py",
    test_content=(
        "from brackets import is_balanced\n\n"
        "def test_mismatch_rejected():\n"
        "    assert is_balanced('(]') is False\n"
        "    assert is_balanced('([)]') is False\n\n"
        "def test_valid_accepted():\n"
        "    assert is_balanced('([])') is True\n"
        "    assert is_balanced('') is True\n"
    ),
    fail_to_pass="test_brackets.py::test_mismatch_rejected",
    pass_to_pass=["test_brackets.py::test_valid_accepted"],
)

# H3 — later-wins merge (config override semantics)
_h(
    id="creh-013-merge-override",
    source_r="R-F(config-merge)",
    bug_class="merge/precedence",
    instruction=(
        "Fix gap: merge_configs must let LATER dicts override earlier keys "
        "(last-wins), but it keeps the FIRST value seen so overrides are "
        "ignored. Fix the precedence. The reproduce test asserts a later dict "
        "overrides an earlier key."
    ),
    module_path="merge.py",
    buggy=(
        "def merge_configs(configs):\n"
        '    """Merge dicts left-to-right; later dicts override earlier keys."""\n'
        "    out = {}\n"
        "    for cfg in configs:\n"
        "        for k, v in cfg.items():\n"
        "            if k not in out:\n"
        "                out[k] = v\n"
        "    return out\n"
    ),
    gold=(
        "def merge_configs(configs):\n"
        '    """Merge dicts left-to-right; later dicts override earlier keys."""\n'
        "    out = {}\n"
        "    for cfg in configs:\n"
        "        for k, v in cfg.items():\n"
        "            out[k] = v\n"
        "    return out\n"
    ),
    test_path="test_merge.py",
    test_content=(
        "from merge import merge_configs\n\n"
        "def test_later_wins():\n"
        "    assert merge_configs([{'a': 1}, {'a': 2, 'b': 3}]) == {'a': 2, 'b': 3}\n\n"
        "def test_single():\n"
        "    assert merge_configs([{'a': 1}]) == {'a': 1}\n"
    ),
    fail_to_pass="test_merge.py::test_later_wins",
    pass_to_pass=["test_merge.py::test_single"],
)

# H4 — binary search boundary (misses last element)
_h(
    id="creh-014-binary-search-boundary",
    source_r="R-F(search-boundary)",
    bug_class="algorithm/boundary",
    instruction=(
        "Fix gap: bsearch uses hi = len(a) - 1 with a `while lo < hi` loop, so "
        "it never examines the final candidate and returns -1 for an element "
        "at the last position. Fix the loop bounds so present elements are "
        "found. The reproduce test searches for the last element."
    ),
    module_path="bsearch.py",
    buggy=(
        "def bsearch(a, x):\n"
        '    """Return index of x in sorted list a, else -1."""\n'
        "    lo, hi = 0, len(a) - 1\n"
        "    while lo < hi:\n"
        "        mid = (lo + hi) // 2\n"
        "        if a[mid] == x:\n"
        "            return mid\n"
        "        if a[mid] < x:\n"
        "            lo = mid + 1\n"
        "        else:\n"
        "            hi = mid - 1\n"
        "    return -1\n"
    ),
    gold=(
        "def bsearch(a, x):\n"
        '    """Return index of x in sorted list a, else -1."""\n'
        "    lo, hi = 0, len(a) - 1\n"
        "    while lo <= hi:\n"
        "        mid = (lo + hi) // 2\n"
        "        if a[mid] == x:\n"
        "            return mid\n"
        "        if a[mid] < x:\n"
        "            lo = mid + 1\n"
        "        else:\n"
        "            hi = mid - 1\n"
        "    return -1\n"
    ),
    test_path="test_bsearch.py",
    test_content=(
        "from bsearch import bsearch\n\n"
        "def test_last_element():\n"
        "    assert bsearch([1, 3, 5, 7, 9], 9) == 4\n\n"
        "def test_middle_and_missing():\n"
        "    assert bsearch([1, 3, 5], 3) == 1\n"
        "    assert bsearch([1, 3, 5, 7, 9], 4) == -1\n"
    ),
    fail_to_pass="test_bsearch.py::test_last_element",
    pass_to_pass=["test_bsearch.py::test_middle_and_missing"],
)

# H5 — regression-sensitive: fix retry backoff WITHOUT breaking the cap
_h(
    id="creh-015-backoff-cap-preserve",
    source_r="R-F(backoff)",
    bug_class="logic/regression-sensitive",
    instruction=(
        "Fix gap: backoff_delay should grow exponentially (base * 2**attempt) "
        "but is capped at `cap`. It currently returns base*attempt (linear), "
        "so it under-waits. Make it exponential while STILL honouring the cap. "
        "The reproduce test checks exponential growth; a sibling test checks "
        "the cap is still enforced (do not break it)."
    ),
    module_path="backoff.py",
    buggy=(
        "def backoff_delay(attempt, base=1, cap=30):\n"
        '    """Exponential backoff base*2**attempt, clamped to cap seconds."""\n'
        "    return min(base * attempt, cap)\n"
    ),
    gold=(
        "def backoff_delay(attempt, base=1, cap=30):\n"
        '    """Exponential backoff base*2**attempt, clamped to cap seconds."""\n'
        "    return min(base * (2 ** attempt), cap)\n"
    ),
    test_path="test_backoff.py",
    test_content=(
        "from backoff import backoff_delay\n\n"
        "def test_exponential():\n"
        "    assert backoff_delay(0) == 1\n"
        "    assert backoff_delay(1) == 2\n"
        "    assert backoff_delay(3) == 8\n\n"
        "def test_cap_enforced():\n"
        "    # cap holds on both the buggy (linear) and fixed (exponential) impls\n"
        "    assert backoff_delay(100, base=1, cap=30) == 30\n"
    ),
    fail_to_pass="test_backoff.py::test_exponential",
    pass_to_pass=["test_backoff.py::test_cap_enforced"],
)

# H6 — even-length median (must average the two middle values)
_h(
    id="creh-016-median-even",
    source_r="R-F(stats-median)",
    bug_class="algorithm/median",
    instruction=(
        "Fix gap: median returns the single element at index n//2, which is "
        "wrong for even-length inputs — it must average the TWO middle values. "
        "Fix the even-length case without breaking the odd-length case. The "
        "reproduce test uses an even-length list; a sibling test guards the "
        "odd-length case."
    ),
    module_path="median.py",
    buggy=(
        "def median(values):\n"
        '    """Median of a numeric list (even length averages the two middles)."""\n'
        "    s = sorted(values)\n"
        "    n = len(s)\n"
        "    return s[n // 2]\n"
    ),
    gold=(
        "def median(values):\n"
        '    """Median of a numeric list (even length averages the two middles)."""\n'
        "    s = sorted(values)\n"
        "    n = len(s)\n"
        "    if n % 2 == 0:\n"
        "        return (s[n // 2 - 1] + s[n // 2]) / 2\n"
        "    return s[n // 2]\n"
    ),
    test_path="test_median.py",
    test_content=(
        "from median import median\n\n"
        "def test_even():\n"
        "    assert median([1, 2, 3, 4]) == 2.5\n\n"
        "def test_odd():\n"
        "    assert median([3, 1, 2]) == 2\n"
    ),
    fail_to_pass="test_median.py::test_even",
    pass_to_pass=["test_median.py::test_odd"],
)


def main() -> None:
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    ids = [t["id"] for t in TASKS]
    assert len(ids) == len(set(ids)), f"duplicate task ids: {ids}"
    with _OUT.open("w", encoding="utf-8") as fh:
        for t in TASKS:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")
    print(f"wrote {len(TASKS)} tasks -> {_OUT}")
    from collections import Counter
    print("bug_class:", dict(Counter(t["bug_class"] for t in TASKS)))


if __name__ == "__main__":
    main()
