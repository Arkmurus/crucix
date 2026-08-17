"""R-F3878 — the defect register had no claim-before-write mechanism.

THE DEFECT, MEASURED IN THE LIVE REGISTER 2026-08-11. `docs/cure/defects.md` carries
29 canonical claims under 25 distinct numbers: **C-18, C-19, C-22 and C-23 are each
claimed TWICE, by unrelated work.**

    C-18 · The primary search backend served NOISE as success
    C-18 · Node/JS tier security audit — 10 findings
    C-19 · Noise reached a CUSTOMER-FACING DD report
    C-19 · The C-18 XSS residual, measured and gated
    C-22 · SearXNG's surviving engine was serving a soft-404 page
    C-22 · Deep review of C-19..C-21 — one more live XSS
    C-23 · Task-list closeout — five residuals fixed
    C-23 · Two blind spots in the search stack

The damage is not cosmetic and it compounds: **the register's own cross-references
are already broken.** "The C-18 XSS residual" names one of two unrelated C-18s, and
"Deep review of C-19..C-21" is a range across numbers that are themselves ambiguous.
A defect register whose identifiers cannot be cited has lost the property that makes
it a register.

WHY IT KEPT HAPPENING. A C-number was claimed by *writing a heading into a markdown
file*. That is precisely the mechanism §2 abolished for R-numbers — "Don't claim a
number by writing it in a comment; claim it via the registry" — after 9 collisions in
50h. R-numbers got `reserve_r_number.py`; C-numbers never did, so they went on
colliding, unnoticed, four times.

THE LESSON THIS ALLOCATOR MUST INHERIT RATHER THAN RE-LEARN (R-F3248): **the
reservation file is not the only record, and it is the losable one.** For R-numbers
the un-clobberable second record is git. For C-numbers it is the register itself —
25 numbers are claimed in `defects.md` and have never been in any JSON. An allocator
built only on its own ledger would hand out `C-01` and collide with everything, which
is the R-F3248 defect written fresh.

So allocation is the union of the register's headings and the reservation ledger.
Git is deliberately NOT a third source here — see
`test_git_is_not_an_allocation_source_and_here_is_why`.

WHAT IS DELIBERATELY *NOT* A CLAIM. `C-11a`, `C-14b`, `C-18b`, `C-19-orig` and
`C-22 POSTSCRIPT` are continuations of an existing entry, not new ones. Counting them
as claims would report five false collisions, and a gate that cries wolf is a gate
that gets muted (R-F3858) — the same reason the R-F3865 health tracker refuses to
judge below a minimum sample.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aria_service.intel import c_number_registry as reg
from aria_service.tests._source_probe import repo_path


# ── parsing the register: what counts as a claim ────────────────────────────────

_SYNTHETIC = """\
# Defect register

### C-01 · First real defect — CLOSED
some prose

### C-02 · Second real defect
### C-02b · a sub-entry that continues C-02, NOT a new claim
### C-03 · Third
### C-03 POSTSCRIPT (2026-08-11) — a continuation, not a claim
### C-04-orig · the superseded original text
not a heading: ### C-99 mentioned inside a code fence
"""


def test_only_canonical_headings_are_claims(tmp_path):
    """`### C-NN ·` is the claim form. Sub-letters, POSTSCRIPTs and -orig are
    continuations of an existing entry — counting them would invent collisions."""
    f = tmp_path / "defects.md"
    f.write_text(_SYNTHETIC, encoding="utf-8")
    claims, readable = reg.claims_in_register(f)
    assert readable is True
    assert sorted(claims) == [1, 2, 3], f"got {sorted(claims)}"


def test_a_missing_register_is_unreadable_not_empty(tmp_path):
    """§22 — 'could not measure' is never 'measured and found nothing'. An absent
    register that read as zero claims would make the allocator hand out C-01."""
    claims, readable = reg.claims_in_register(tmp_path / "nope.md")
    assert claims == {}
    assert readable is False


# ── the collision the register actually has, and the ability to come back clean ──

def test_the_live_register_collisions_are_detected():
    """Data-driven against the REAL file. Asserted on the real document rather than
    a paraphrase: R-F3868's classify_429 defect survived review and was caught only
    because a test used the real body text."""
    claims, readable = reg.claims_in_register(repo_path("docs/cure/defects.md"))
    assert readable is True
    collisions = {n: len(t) for n, t in claims.items() if len(t) > 1}
    # Tracked against the baseline CONSTANT, not a literal set: LEGACY_COLLISIONS is
    # shrink-only, so resolving one must not require editing a magic list here too
    # (the same fragility that pinned a live claim COUNT and broke the same day).
    # A NEW collision still fails this, which is the point.
    assert set(collisions) == set(reg.LEGACY_COLLISIONS), (
        f"live collisions {sorted(collisions)} != baseline "
        f"{sorted(reg.LEGACY_COLLISIONS)} — a new one appeared, or one was resolved "
        f"without shrinking LEGACY_COLLISIONS")


def test_a_clean_register_reports_no_collisions(tmp_path):
    """R-F3858 — a guard that cannot come back clean is not a guard, it is an alarm
    that gets muted."""
    f = tmp_path / "defects.md"
    f.write_text(_SYNTHETIC, encoding="utf-8")
    claims, _ = reg.claims_in_register(f)
    assert [n for n, t in claims.items() if len(t) > 1] == []


def test_a_new_collision_is_detected_on_top_of_the_legacy_ones(tmp_path):
    """The legacy set is baselined so the gate can be enabled TODAY against four
    pre-existing collisions — but a THIRD claim on an already-collided number must
    still fail, or baselining would be a permanent amnesty."""
    f = tmp_path / "defects.md"
    f.write_text("### C-18 · a\n### C-18 · b\n### C-18 · c\n", encoding="utf-8")
    claims, _ = reg.claims_in_register(f)
    new = reg.new_collisions(claims)
    assert 18 in new, "a 3rd claim on C-18 exceeds the baselined 2 and must fail"


def test_the_legacy_collisions_alone_do_not_fail_the_gate():
    """...and the converse, or the gate could never be turned on."""
    claims, _ = reg.claims_in_register(repo_path("docs/cure/defects.md"))
    assert reg.new_collisions(claims) == {}, (
        "the live register must pass the gate on its baselined collisions alone")


# ── allocation: the R-F3248 lesson, inherited ───────────────────────────────────

def test_an_empty_ledger_still_allocates_above_the_register(tmp_path):
    """THE ROOT CAUSE OF THE WHOLE CLASS. 25 C-numbers live only in defects.md and
    have never been in any JSON. An allocator reading only its own ledger would
    return C-01 and collide with every one of them — R-F3248's defect, rewritten."""
    ledger = tmp_path / "c_number_reservations.json"
    register = tmp_path / "defects.md"
    register.write_text("### C-01 · a\n### C-07 · b\n", encoding="utf-8")

    nxt = reg.peek_next(path=ledger, register=register)
    assert nxt == "C-08", f"expected the register max + 1, got {nxt}"


def test_reserve_records_and_reads_back(tmp_path):
    """R-F3187 — a claim is not a claim until it reads back. Returning a number that
    was never recorded is how R-F3133/3134/3135 were lost."""
    ledger = tmp_path / "c_number_reservations.json"
    register = tmp_path / "defects.md"
    register.write_text("### C-01 · a\n", encoding="utf-8")

    c = reg.reserve("my defect", agent="test", path=ledger, register=register)
    assert c == "C-02"
    data = json.loads(ledger.read_text(encoding="utf-8"))
    entry = data["reservations"][-1]
    assert entry["c_number"] == "C-02"
    assert entry["status"] == "open"
    assert entry["title"] == "my defect"


def test_a_reserved_number_is_not_handed_out_twice(tmp_path):
    """The whole point. Two claims in a row must differ even though neither has been
    written into the register yet — that gap is exactly where C-24 collided."""
    ledger = tmp_path / "c_number_reservations.json"
    register = tmp_path / "defects.md"
    register.write_text("### C-01 · a\n", encoding="utf-8")

    a = reg.reserve("first", path=ledger, register=register)
    b = reg.reserve("second", path=ledger, register=register)
    assert a != b, "claim-before-write must survive a register that has not caught up"
    assert (a, b) == ("C-02", "C-03")


def test_the_ledger_and_the_register_are_unioned_not_preferred(tmp_path):
    """A number claimed in EITHER source is taken. Preferring one would reintroduce
    the collision through whichever source was ignored."""
    ledger = tmp_path / "c_number_reservations.json"
    register = tmp_path / "defects.md"
    register.write_text("### C-09 · written straight into the register\n",
                        encoding="utf-8")
    ledger.write_text(json.dumps({
        "schema_version": 1,
        "reservations": [{"c_number": "C-04", "title": "reserved only",
                          "status": "open"}],
    }), encoding="utf-8")

    assert reg.peek_next(path=ledger, register=register) == "C-10"


def test_an_unreadable_register_refuses_to_allocate(tmp_path):
    """§22 + the §1 lesson that an absence must never read as a measurement. If the
    register cannot be read, its 25 claims are invisible and ANY number handed out
    may already be taken. Failing loudly beats issuing a colliding number."""
    ledger = tmp_path / "c_number_reservations.json"
    with pytest.raises(reg.RegisterUnreadableError):
        reg.reserve("x", path=ledger, register=tmp_path / "absent.md")


# ── formatting + lifecycle ──────────────────────────────────────────────────────

def test_numbers_are_zero_padded_to_match_the_register():
    """The register writes C-01..C-09, so an allocator emitting `C-1` would create a
    second spelling of the same identifier — a collision that greps as two."""
    assert reg.format_c(1) == "C-01"
    assert reg.format_c(9) == "C-09"
    assert reg.format_c(25) == "C-25"
    assert reg.format_c(100) == "C-100"


def test_mark_closed_records_the_r_numbers(tmp_path):
    """A C-number closes by pointing at the R-numbers that fixed it — that link is
    the only thing connecting the register to shipped code."""
    ledger = tmp_path / "c_number_reservations.json"
    register = tmp_path / "defects.md"
    register.write_text("### C-01 · a\n", encoding="utf-8")

    c = reg.reserve("t", path=ledger, register=register)
    reg.mark_closed(c, ["R-F3873", "R-F3874"], path=ledger)
    entry = json.loads(ledger.read_text(encoding="utf-8"))["reservations"][-1]
    assert entry["status"] == "closed"
    assert entry["r_numbers"] == ["R-F3873", "R-F3874"]
    assert entry["closed_at"]


def test_closing_an_unreserved_number_raises(tmp_path):
    ledger = tmp_path / "c_number_reservations.json"
    ledger.write_text(json.dumps({"schema_version": 1, "reservations": []}),
                      encoding="utf-8")
    with pytest.raises(KeyError):
        reg.mark_closed("C-99", ["R-F1"], path=ledger)


# ── the concurrency lessons are INHERITED, not re-implemented ───────────────────

def test_the_hard_won_write_primitives_are_reused_not_copied():
    """r_number_registry paid for these across R-F1026/R-F3187/R-F3200: a per-PID
    temp file, a Windows PermissionError retry, a fail-open cross-process lock, and
    read-back verification. Copying them would fork the lessons and let them drift;
    §26 forbids refactoring them into a shared module inside a fix PR, so they are
    imported."""
    from aria_service.tests._source_probe import module_source

    src = module_source(reg)
    assert "from .r_number_registry import" in src or \
           "from . import r_number_registry" in src, \
        "the C allocator must reuse the R allocator's write primitives"
    for copied in ("def _file_lock", "def _save_atomic"):
        assert copied not in src, (
            f"{copied} is re-implemented here — it must be imported, or the "
            f"R-F1026/R-F3187 fixes will drift between the two registries")


# ── the gate is actually wired into something that runs ────────────────────────

def test_the_precommit_gate_exists_and_is_invoked():
    """§21a — a guard nobody calls is dark. No git hook is installed in this tree
    (`.git/hooks` holds only samples), so the real enforcement point is CI, which
    runs `python scripts/pre-commit --check-all`."""
    checks = repo_path("scripts/pre_commit_checks.py").read_text(encoding="utf-8")
    assert "def check_c_number_collisions" in checks

    hook = repo_path("scripts/pre-commit").read_text(encoding="utf-8")
    assert hook.count("check_c_number_collisions(") >= 2, (
        "the gate must run in BOTH the staged-commit path and the --check-all CI "
        "path; wiring only one leaves the other blind")


def test_the_gate_reports_a_duplicate_with_an_actionable_message(tmp_path):
    """A gate whose message does not say what to DO gets bypassed."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_pcc", repo_path("scripts/pre_commit_checks.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    f = tmp_path / "defects.md"
    f.write_text("### C-18 · a\n### C-18 · b\n### C-18 · c\n", encoding="utf-8")
    issues = mod.check_c_number_collisions(register=f)
    assert issues, "three claims on C-18 must be reported"
    blob = "\n".join(issues)
    assert "C-18" in blob
    assert "reserve_c_number" in blob, "the message must name the allocator to use"


def test_git_is_not_an_allocation_source_and_here_is_why():
    r"""R-F3248's git scan is CORRECT for R-numbers and WRONG for C-numbers, and the
    difference is measured, not assumed.

    Copying the R-number design exactly was the obvious move. It was implemented, and
    it moved the next number from `C-26` to **`C-296`**, because `\bC-\d+\b` is not
    a defect reference — it is a common real-world token. Both matches are genuine
    text in this repo's history:

        "aircraft (Super Tucano, Gripen, FA-50, C-295)"   <- the Airbus C-295
        "Closes the last C-3 false positive"              <- an unrelated gate

    `R-F####` is a coined token and safe to grep; `C-` is a bigram that appears in
    aircraft designations, standards and prose. The failure is not benign: `peek`
    began contradicting `audit`, which is the exact "a peek that promises a number
    reserve would refuse" lie R-F3248 names, and an absurd number is how a tool gets
    bypassed and the collisions resume.

    This test exists so the scan is not helpfully re-added by someone who reads
    r_number_registry and notices the asymmetry.
    """
    from aria_service.tests._source_probe import module_source

    src = module_source(reg)
    assert "subprocess" not in src, (
        "a git scan was re-added to the C-number allocator — see this test's "
        "docstring: it inflates the next number by ~270 on this repo's history")
    assert "C-295" in src, "the reason git is excluded must stay recorded in the module"


# ── backfill: the drift signal must be usable, and honest ──────────────────────

def test_backfill_imports_headings_and_is_idempotent(tmp_path):
    """Without this, `audit` reports 25 unreserved headings forever and the 26th —
    the real drift — arrives inside noise. Same reasoning as the R-F3873 block alert
    firing on the transition rather than on every query."""
    ledger = tmp_path / "c_number_reservations.json"
    register = tmp_path / "defects.md"
    register.write_text("### C-01 · a — CLOSED\n### C-02 · b\n", encoding="utf-8")

    first = reg.backfill_from_register(path=ledger, register=register)
    assert first["count"] == 2
    again = reg.backfill_from_register(path=ledger, register=register)
    assert again["count"] == 0, "backfill must be idempotent"


def test_backfilled_rows_never_claim_to_be_reservations(tmp_path):
    """They are imported headings. Nobody reserved them — that IS the defect — so a
    fabricated timestamp would put invented data into the one log that exists to be
    trustworthy."""
    ledger = tmp_path / "c_number_reservations.json"
    register = tmp_path / "defects.md"
    register.write_text("### C-01 · a\n", encoding="utf-8")

    reg.backfill_from_register(path=ledger, register=register)
    row = json.loads(ledger.read_text(encoding="utf-8"))["reservations"][0]
    assert row["claimed_by"] == "backfill:register"
    assert row["claimed_at"] is None, "a claim never made has no honest timestamp"
    assert "never reserved" in row["notes"]


def test_backfill_preserves_a_collided_number_s_other_titles(tmp_path):
    """One ledger row per NUMBER, but a collision has two unrelated titles and
    losing one would erase the evidence of the collision."""
    ledger = tmp_path / "c_number_reservations.json"
    register = tmp_path / "defects.md"
    register.write_text("### C-18 · search noise\n### C-18 · node security audit\n",
                        encoding="utf-8")

    reg.backfill_from_register(path=ledger, register=register)
    row = json.loads(ledger.read_text(encoding="utf-8"))["reservations"][0]
    assert "COLLIDED" in row["notes"]
    assert "node security audit" in row["notes"]


def test_backfill_does_not_disturb_a_real_reservation(tmp_path):
    """An entry someone actually claimed must survive untouched."""
    ledger = tmp_path / "c_number_reservations.json"
    register = tmp_path / "defects.md"
    register.write_text("### C-01 · a\n", encoding="utf-8")

    reg.reserve("real claim", agent="me", path=ledger, register=register)  # -> C-02
    register.write_text("### C-01 · a\n### C-02 · real claim\n", encoding="utf-8")
    reg.backfill_from_register(path=ledger, register=register)

    rows = {r["c_number"]: r for r in
            json.loads(ledger.read_text(encoding="utf-8"))["reservations"]}
    assert rows["C-02"]["claimed_by"] == "me"
    assert rows["C-02"]["claimed_at"] is not None


# ── the gate must run on the commits it exists to catch ────────────────────────

def test_the_gate_has_a_trigger_that_actually_fires_on_the_register():
    """THE HOLE SELF-REVIEW FOUND. The gate is wired into scripts/pre-commit, which
    ci.yml runs via --check-all — but ci.yml's push trigger carries
    `paths-ignore: ['docs/**', 'data/**', '**/*.md']` (R-F1408, deliberately), and a
    commit adding a colliding heading touches ONLY docs/cure/defects.md, which
    matches two of those patterns.

    So on the push-to-main path this repo actually uses, CI is skipped for precisely
    the commits the gate exists to catch: wired, and never firing. That is the
    R-F3791 blind-guard shape — the difference between a guard that exists and a
    guard that runs."""
    import re as _re

    ci = repo_path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "paths-ignore" in ci, (
        "if ci.yml no longer skips doc pushes, re-check whether this extra workflow "
        "is still needed rather than leaving two gates to drift")

    wf = repo_path(".github/workflows/defect-register-gate.yml")
    assert wf.exists(), "the register needs a trigger ci.yml's paths-ignore cannot swallow"
    body = wf.read_text(encoding="utf-8")
    assert "docs/cure/defects.md" in body, "it must trigger on the register itself"
    assert "reserve_c_number.py audit" in body, "it must actually run the audit"
    # `audit` exits 1 only on a NEW collision, which is what makes this gate usable
    # today against four baselined ones.
    assert _re.search(r"on:\s*\n\s*push:", body), "must fire on push, not only on PRs"


def test_the_audit_cli_can_print_a_new_collision_on_a_cp1252_console():
    """THE FIRST LIVE CATCH CRASHED THE REPORTER.

    Within an hour of shipping, the gate caught a real new collision (C-25, claimed
    by two agents) — and `audit` died with
    `UnicodeEncodeError: '\u2190'`, the arrow in its "<- NEW" marker, because
    Windows consoles default to cp1252. The CLEAN path printed fine, which is exactly
    why it looked healthy: the branch that reports a collision had never been
    exercised (R-F3858 — a guard whose failure path is untested is a guard that fails
    when it finally matters).

    Pins both halves of the fix: the source is cp1252-safe, and stdout is
    reconfigured anyway because defect titles are arbitrary prose."""
    src = repo_path("scripts/admin/reserve_c_number.py").read_text(encoding="utf-8")
    assert "reconfigure" in src, "stdout must be forced to UTF-8 for arbitrary titles"
    try:
        src.encode("cp1252")
    except UnicodeEncodeError as exc:
        raise AssertionError(
            f"reserve_c_number.py contains a character a Windows console cannot "
            f"encode ({exc.object[exc.start:exc.end]!r} at {exc.start}). The audit "
            f"crashed on exactly this once already."
        ) from None


def test_a_stray_heading_level_cannot_evade_the_gate(tmp_path):
    """Found by adversarial review of this module, not by a failing test.

    A parser pinned to `###` made an entry written as `## C-30 ·` or
    `#### C-30 ·` INVISIBLE: not counted as a claim, so the allocator would
    reissue the number, and not counted as a collision, so the gate would pass
    it. A guard that one stray `#` defeats is the R-F3791 blind-guard shape.

    R-F4086 (C-133) corrects the premise this docstring used to state — "every
    entry in the register happens to use `###`". It did not: the register uses
    `###` for C-1..C-38 and `##` from C-39 onward, and has since long before
    R-F3878 was written. The hazard was never hypothetical, which makes the
    widening load-bearing rather than defensive."""
    for level in ("##", "###", "####"):
        f = tmp_path / f"d{len(level)}.md"
        f.write_text(f"{level} C-30 · a\n{level} C-30 · b\n", encoding="utf-8")
        claims, _ = reg.claims_in_register(f)
        assert 30 in claims, f"a {level} heading must count as a claim"
        assert reg.new_collisions(claims), f"a {level} collision must be detected"


def test_widening_the_heading_level_reads_only_entry_headings():
    """The converse control (R-F3858): broadening `###` to `#{2,4}` must not start
    counting prose or continuations (C-11a, C-14b, C-18b, C-19-orig) as claims.

    Asserted as an INVARIANT, not a count. A first version pinned
    `len(claims) == 26` and broke the same day when a peer legitimately added
    C-27 — a test that fails whenever the register is USED is worse than no
    test, because the only way to green it is to bump a magic number, which
    proves nothing and trains the next person to ignore it.

    R-F4086 (C-133): the SECOND version made the same mistake one level up. It
    asserted `set(claims) == narrow` — the wide reading must equal the `###`-only
    reading — which is true only while every entry is `###`. The register moved
    to `##` at C-39 and never moved back, so from the day it was written this
    test could report nothing but "widening changed the reading: [39, 40, 41,
    …]" and grew one more number every time the register was used. It was red
    for ~94 entries. **A permanently-red test carries no information**: it
    cannot go green, so it can never distinguish a healthy register from a
    broken parser, and the tempting way to green it is to narrow the parser back
    to `###` — which would re-open the exact blind spot the sibling test above
    exists to close, and hide 88 live claims from the allocator.

    So ask the question that can actually be answered wrong. Not "is the wide
    reading identical to the narrow one" (a premise about formatting) but "did
    the wide reading pick up anything that is not an entry heading" (the hazard).
    Three falsifiable invariants, each with a distinct failure mode:
      1. widening LOSES nothing — a parser regression drops a `###` entry;
      2. every claim is sourced from a real level-2/3 entry heading — a match
         from prose, a `#####` sub-heading, or a suffixed continuation appears
         as a difference;
      3. fenced code blocks contribute nothing — a `## C-N ·` line inside a
         ```yaml example would otherwise claim a number nobody reserved.
    """
    import re

    path = repo_path("docs/cure/defects.md")
    text = path.read_text(encoding="utf-8")
    claims, readable = reg.claims_in_register(path)
    assert readable is True

    def _nums(pattern: str, src: str) -> set[int]:
        return {int(m.group(1))
                for m in re.finditer(pattern, src, re.MULTILINE)}

    narrow = _nums(r"^###\s+C-(\d{1,4})\s+·", text)
    entry_headings = narrow | _nums(r"^##\s+C-(\d{1,4})\s+·", text)

    # 1. Widening must never lose a claim the narrow pattern could see.
    assert narrow <= set(claims), (
        "widening the heading level LOST entries the `###` pattern reads: "
        f"{sorted(narrow - set(claims))}")

    # 2. Everything read must come from a level-2 or level-3 entry heading.
    #    `C-11a`/`C-19-orig` are continuations and must stay invisible — the
    #    `C-<digits> ·` shape is what excludes them.
    assert set(claims) == entry_headings, (
        "the wide pattern read something that is not a level-2/3 entry heading "
        f"— extra: {sorted(set(claims) - entry_headings)}; "
        f"missing: {sorted(entry_headings - set(claims))}")

    # 3. A heading inside a fenced code block is an EXAMPLE, not a claim.
    unfenced = re.sub(r"^```.*?^```", "", text, flags=re.MULTILINE | re.DOTALL)
    assert set(claims) == _nums(r"^#{2,3}\s+C-(\d{1,4})\s+·", unfenced), (
        "a C-number heading inside a fenced code block was counted as a claim")

    assert set(reg.new_collisions(claims)) == set(), "the live register must stay green"


def test_the_widening_control_can_still_fail(tmp_path):
    """R-F4086 (C-133): the control above was rewritten from an assertion that
    could only fail into one that can pass — so prove it can still FAIL.

    A guard that cannot fail is the shape this whole module exists to catch
    (R-F3858), and rewriting a red test is exactly when that risk is highest."""
    import re

    def _read(md: str) -> tuple[set[int], set[int]]:
        f = tmp_path / f"d{abs(hash(md))}.md"
        f.write_text(md, encoding="utf-8")
        claims, ok = reg.claims_in_register(f)
        assert ok is True
        entries = {int(m.group(1))
                   for m in re.finditer(r"^#{2,3}\s+C-(\d{1,4})\s+·", md,
                                        re.MULTILINE)}
        return set(claims), entries

    # A level-4 heading is NOT an entry heading, but the parser reads it —
    # invariant 2 catches the divergence.
    claims, entries = _read("## C-1 · a\n#### C-2 · b\n")
    assert claims != entries and 2 in claims - entries

    # A heading inside a fence is an example — invariant 3 catches it.
    fenced = "## C-1 · a\n\n```md\n## C-999 · an example\n```\n"
    claims, _ = _read(fenced)
    unfenced = re.sub(r"^```.*?^```", "", fenced,
                      flags=re.MULTILINE | re.DOTALL)
    stripped = {int(m.group(1))
                for m in re.finditer(r"^#{2,3}\s+C-(\d{1,4})\s+·", unfenced,
                                     re.MULTILINE)}
    assert 999 in claims and 999 not in stripped

    # And a continuation must stay invisible under every level.
    claims, _ = _read("## C-1 · a\n## C-1a · a continuation\n")
    assert claims == {1}


def test_backfill_does_not_guess_a_status(tmp_path):
    """A first cut inferred status from the word "CLOSED" in the title and got five
    wrong in one pass: C-03/C-12/C-16/C-18 say "**FIXED**", C-19 "**REMEDIATED**",
    C-14 "**CONTAINED + ROOT-FIXED**" — all resolved, all stamped `open`.

    A fabricated status is worse than none because it reads as a measurement. The
    register is the authority on whether a defect is resolved; this ledger allocates
    numbers and must not claim to know state it never observed."""
    ledger = tmp_path / "c_number_reservations.json"
    register = tmp_path / "defects.md"
    register.write_text(
        "### C-01 · a — **CLOSED (R-F1)**\n"
        "### C-02 · b — **FIXED (R-F2)**\n"
        "### C-03 · c — still open\n", encoding="utf-8")

    reg.backfill_from_register(path=ledger, register=register)
    rows = json.loads(ledger.read_text(encoding="utf-8"))["reservations"]
    assert {r["status"] for r in rows} == {"imported"}, (
        "backfilled rows must not carry a guessed open/closed status")
