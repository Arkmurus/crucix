"""R-F4262 — a failed knowledge seed must not be recorded as success (dossier E2+E7).

The defect: every seeder catches its per-section exceptions, returns
`{"sections_ingested": 0}` **without raising**, and `main.py` read "no
exception" as success — stamping a **30-day** skip hash. A module whose ingest
failed completely was then skipped for the next month, while DD Layer 4c went on
stamping `source: "RAG:regional_compliance"` on report content attributed to a
store that may never have been filled. That is a provenance claim in a
customer-facing report.

E7 is the same defect through a different door: the 14 seeders are reached via
`__import__(f"aria_service.intel.{modname}")` from a string table. A rename
raises `ModuleNotFoundError` straight into the swallowing `except Exception` two
lines below, is logged as a WARNING, and the seed silently does not happen.
Nothing in the repo asserted those names still resolve.
"""
from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from aria_service.main import _seed_ingested_something as ingested

MAIN = pathlib.Path(__file__).resolve().parents[1] / "main.py"


class TestDidItActuallyIngest:
    def test_a_total_failure_is_not_success(self):
        """The exact payload the defect produced."""
        assert ingested({"sections_ingested": 0, "total_sections": 14}) is False

    def test_a_real_ingest_is_success(self):
        assert ingested({"sections_ingested": 14, "total_sections": 14}) is True

    def test_a_partial_ingest_counts(self):
        assert ingested({"sections_ingested": 1, "total_sections": 14}) is True

    def test_the_other_shape_is_covered_too(self):
        """13 seeders report sections_ingested; dd_case_library reports
        cases_ingested. Keying on one literal would exempt the other."""
        assert ingested({"cases_ingested": 0, "total_cases": 9}) is False
        assert ingested({"cases_ingested": 9, "total_cases": 9}) is True

    def test_a_seeder_that_did_not_say_is_UNKNOWN_not_success(self):
        """'I could not tell' must never render as 'it worked'."""
        assert ingested({"error": "boom"}) is None
        assert ingested({}) is None
        assert ingested({"total_sections": 14}) is None

    @pytest.mark.parametrize("bad", [None, "nope", 42, [], ("a",)])
    def test_a_non_dict_result_is_unknown(self, bad):
        assert ingested(bad) is None

    def test_booleans_are_not_counted_as_counts(self):
        """`True` is an int in Python; a flag must not read as one ingested."""
        assert ingested({"cache_ingested": True}) is None


class TestTheThirtyDayHashIsGatedOnIt:
    SOURCE = MAIN.read_text(encoding="utf-8")

    def test_the_stamp_requires_a_proven_ingest(self):
        assert "_seed_ingested_something(result)" in self.SOURCE
        assert "if _ingested is True else" in self.SOURCE, (
            "the hash must only be computed when the ingest is PROVEN"
        )

    def test_it_is_true_specifically_not_merely_truthy(self):
        """`None` (could not tell) must not stamp either."""
        assert "_ingested is not True" in self.SOURCE

    def test_a_skipped_stamp_is_announced(self):
        """A silent non-stamp would be its own invisible behaviour."""
        assert "NOT stamping the 30-day skip hash" in self.SOURCE


class TestEverySeederInTheDispatchTableResolves:
    """Dossier E7 — the guard nothing in the repo had.

    A rename or typo in this table raises ModuleNotFoundError into the
    swallowing `except Exception`, is logged as a WARNING, and the seed silently
    does not happen. This is the test that turns that into a red build.
    """

    @staticmethod
    def _table() -> list[tuple[str, str, str]]:
        """Read the literal table out of main.py by AST — never by import, so a
        broken table is a test failure rather than a collection error."""
        tree = ast.parse(MAIN.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(getattr(t, "id", None) == "modules" for t in node.targets):
                continue
            if not isinstance(node.value, ast.List) or len(node.value.elts) < 10:
                continue
            out = []
            for el in node.value.elts:
                if isinstance(el, ast.Tuple) and len(el.elts) == 3:
                    vals = [e.value for e in el.elts if isinstance(e, ast.Constant)]
                    if len(vals) == 3:
                        out.append(tuple(vals))
            if out:
                return out
        return []

    def test_the_table_was_found_and_is_the_expected_size(self):
        table = self._table()
        assert len(table) >= 12, (
            f"only found {len(table)} seeders — if the table moved, this guard "
            f"has gone blind and must be repointed, not deleted"
        )

    def test_every_module_imports_and_every_function_exists_and_is_async(self):
        failures = []
        for modname, label, fn in self._table():
            try:
                mod = __import__(f"aria_service.intel.{modname}", fromlist=[fn])
            except Exception as exc:
                failures.append(f"{modname}: import failed — {type(exc).__name__}: {exc}")
                continue
            target = getattr(mod, fn, None)
            if target is None:
                failures.append(f"{modname}.{fn}: function does not exist")
            elif not inspect.iscoroutinefunction(target):
                failures.append(f"{modname}.{fn}: not async — main.py awaits it")
        assert not failures, (
            "the boot knowledge-seed dispatch table does not resolve:\n  "
            + "\n  ".join(failures)
        )


class TestTheExemplarModuleTellsTheTruth:
    """regional_compliance is the seeder the dossier traced to a customer-facing
    provenance claim. Its wire is now gated; ten more still are not (recorded)."""

    SOURCE = (pathlib.Path(__file__).resolve().parents[1]
              / "intel/regional_compliance.py").read_text(encoding="utf-8")

    def test_success_is_gated_on_having_ingested_something(self):
        assert "if success > 0:" in self.SOURCE

    def test_failure_is_actually_wired_not_merely_imported(self):
        """`wire_failure` was imported and never called — that was the defect."""
        assert self.SOURCE.count("wire_failure(") >= 1

    def test_the_failure_branch_names_what_broke(self):
        assert "produced NOTHING" in self.SOURCE
        assert "gap_type=\"knowledge_gap\"" in self.SOURCE
