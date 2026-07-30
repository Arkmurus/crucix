# R-F3474 — Mem0 and Chroma erasure/readiness audit

Date: 2026-07-30
Scope: code and committed deployment configuration only
Conclusion: **FAIL — blocks expansion of continuous personal-data ingestion**

This is not a conclusion that all existing processing is unlawful.  Article 17 is
a qualified right, not an unconditional deletion command.  It is a finding that
ARIA cannot currently demonstrate reliable, subject-scoped retention and erasure
across every store that can surface personal data.  The ICO requires organisations
to justify retention periods, review retained data, and erase or anonymise data
that is no longer needed; its erasure guidance also requires operational handling
of valid requests.

Authoritative guidance:

- ICO, [Storage limitation](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/data-protection-principles/a-guide-to-the-data-protection-principles/storage-limitation/)
- ICO, [Right to erasure](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/individual-rights/individual-rights/right-to-erasure/)

## Verified findings

| Control | Result | Code evidence | Consequence |
|---|---|---|---|
| Stable subject key | Fail | `mem0.py:290,293`; `knowledge.py:2074-2125` | Facts are identified through a session-bearing source string and removed by substring. This cannot prove complete subject erasure and can over-delete namesakes. |
| All searchable Chroma collections erased | Fail | Retrieval uses hot, cold and facts at `rag_store.py:1590-1593`; purge scans only hot and facts at `rag_store.py:2176-2195` | A removed document can remain searchable from `aria_documents_cold`. |
| Retention contract | Fail | `mem0.py:194-299` | No mandatory retention class, expiry, lawful basis, subject ID, or erasure state accompanies a memory fact. |
| Tenant isolation | Fail | `mem0.py:360-386` | Retrieval reads the shared fact cache and selects `mem0:` entries lexically; it is not restricted by tenant or data subject. |
| Immutable/cold-store policy | Fail | `rag_store.py:104-105` | “Never deleted” conflicts with the need for a documented, enforceable retention outcome. |
| Committed region configuration | Partial | `fly.toml:2`; `fly.web.toml:30`; `fly.wa.toml:25` | London is the configured primary region, but configuration alone does not prove live volume, replica, snapshot, processor, or transfer geography. |
| Backup and restore erasure | Unverified | volume mounts at `fly.toml:129-131`, `fly.web.toml:40-42`, `fly.wa.toml:30-32` | No tested lineage shows that an erasure remains erased after restore or snapshot recovery. |

The machine-readable control register is
`data/audit_reports/rf3474_memory_erasure_controls.json`.  It is validated by a
test so an audit control cannot silently lose its evidence or status.

## Required target contract

Before Phase 2 expands personal-data ingestion, each observation and every derived
memory/embedding must carry:

1. `tenant_id`, stable `data_subject_id`, evidence ID, and derivation lineage;
2. evidence class, purpose, documented lawful basis, acquired time, retention
   policy version, and `erase_after`;
3. storage locations containing the item, including hot Chroma, cold Chroma,
   facts, knowledge snapshots, exports, and backups;
4. deletion state and tombstone hash, without retaining the erased personal data;
5. a verified erasure receipt listing rows deleted, locations checked, failures,
   and retry/adjudication state.

Where immutable provenance is required, retain a non-reversible tombstone and the
minimum audit metadata.  Do not retain the erased payload under the label
“immutable”.  Crypto-shredding is viable only if encryption keys are genuinely
scoped finely enough that destroying a key does not erase unrelated subjects.

## Acceptance tests

The gate is not satisfied by another design document.  It requires executable
proof:

- ingest one synthetic subject into knowledge, Mem0, hot documents, cold
  documents, and facts; erase by stable subject ID; prove zero retrieval from all
  collections;
- prove a namesake in the same tenant and the same person in a different tenant
  are not deleted;
- force one collection deletion to fail and prove the receipt is incomplete and
  no success is reported;
- restore a test backup and prove the tombstone/reconciliation job prevents the
  erased payload from resurfacing;
- prove expiry by retention class and preservation under a documented legal hold;
- record live Fly machine, volume, replica and snapshot regions plus the processor
  and international-transfer assessment.

Until these pass, the safe implementation is to limit ingestion to admitted
sources and minimise personal-data retention, not to call the current stores
erasure-capable.
