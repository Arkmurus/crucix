"""tenant_namespace — multi-tenant isolation primitive (R-F81, 2026-05-09).

Why this module exists
──────────────────────
Per peer review of the architecture doc: 'The current architecture
stores knowledge in a single shared brain. As paying customers bring
proprietary documents (Layer 5c commercial coherence drops), the
question of whether Customer A's ingested doc influences Customer B's
DD output becomes a data-isolation issue.'

Today's pattern: shared corpus with provenance tags. Tomorrow's
requirement (when first paying Pro Intel customer signs up): customer-
namespaced RAG + knowledge for proprietary uploads.

What this module is (and isn't)
───────────────────────────────
This is a SCAFFOLD — the scope-resolution layer that downstream
modules query before reading from or writing to knowledge / RAG /
ledger. It defines:

  - the tenant model (default = shared 'public' tenant; customers get
    an isolated namespace identified by their account ID)
  - the visibility rules (see 'Visibility model' below)
  - the resolution helper that returns the correct namespace prefix
    for a request

It does NOT yet:
  - actually segregate the existing knowledge.json / aria_signals.json
    by namespace (those remain shared as today; this is forward
    compatible — when the first proprietary customer arrives, knowledge
    consumers will start passing a tenant_id arg and the storage layer
    handles namespacing without breaking the shared corpus)
  - wire the namespace into RAG retrieval (next iteration: rag_store
    .search(tenant_id=...))
  - encrypt at rest per-tenant (Tier 4 — cryptographic isolation
    requires customer-held keys)

Visibility model
────────────────
Three visibility classes for any knowledge fact / RAG chunk / signal:

  1. PUBLIC      — visible to all tenants. The default for OSINT-derived
                   facts (sweep ingest, FCPA, sanctions divergence,
                   knowledge-base seeding). This is today's only class.

  2. SHARED-OEM  — visible to all tenants in a 'cohort' (e.g. all
                   defence-broker tenants share the broker corpus, but
                   not banking-insurance tenants). Cohort is the user's
                   `sector` field from R-F48.

  3. TENANT      — visible only to one tenant. Required for proprietary
                   uploads, customer-issued DD reports, customer-bound
                   API key activity.

A request from tenant T sees: PUBLIC ∪ SHARED-OEM(cohort=T.sector) ∪
                              TENANT(tenant_id=T.id)

Public API
──────────
    visibility_for_request(user) -> dict
        { tenant_id, sector, allowed_classes, namespace_prefix }

    namespace_prefix_for_visibility(class_, tenant_id, sector) -> str
        Build a Redis/storage key prefix that matches the visibility
        class. Used by storage layers (knowledge.py, rag_store.py,
        intel_ledger.py) when they're upgraded to tenant-aware mode.

    is_visible(record, request_visibility) -> bool
        Test whether a single record is visible to the request.

    summary() -> dict
"""
from __future__ import annotations

from typing import Any

# ── Constants ────────────────────────────────────────────────────────

PUBLIC_TENANT     = "public"
SHARED_OEM_PREFIX = "oem"   # SHARED-OEM:<cohort> e.g. SHARED-OEM:broker

VISIBILITY_PUBLIC     = "PUBLIC"
VISIBILITY_SHARED_OEM = "SHARED_OEM"
VISIBILITY_TENANT     = "TENANT"

ALL_VISIBILITY_CLASSES = (
    VISIBILITY_PUBLIC,
    VISIBILITY_SHARED_OEM,
    VISIBILITY_TENANT,
)


# ── Visibility resolution ───────────────────────────────────────────

def visibility_for_request(user: dict[str, Any] | None) -> dict[str, Any]:
    """Compute the visibility envelope for an authenticated request.

    A user dict (from findUserById on the seenode side, or the request
    context on fly) carries:
        - id (tenant_id)
        - sector (cohort for SHARED_OEM)
        - role ('admin' bypasses tenant boundaries — operator support)

    Returns the envelope the storage layer should filter against.
    """
    if not user:
        # Anonymous / system-internal — only PUBLIC
        return {
            "tenant_id":         PUBLIC_TENANT,
            "sector":            None,
            "allowed_classes":   {VISIBILITY_PUBLIC},
            "is_admin":          False,
            "namespace_prefix":  f"crucix:tenant:{PUBLIC_TENANT}",
        }
    tenant_id = (user.get("id") or PUBLIC_TENANT).strip() or PUBLIC_TENANT
    sector = (user.get("sector") or "").strip().lower() or None
    is_admin = (user.get("role") or "").strip().lower() == "admin"

    allowed: set[str] = {VISIBILITY_PUBLIC}
    if sector:
        allowed.add(VISIBILITY_SHARED_OEM)
    if tenant_id and tenant_id != PUBLIC_TENANT:
        allowed.add(VISIBILITY_TENANT)
    if is_admin:
        # Admin sees everything — note this for audit logging on the
        # caller side; we don't refuse admin reads here.
        allowed.update(ALL_VISIBILITY_CLASSES)

    return {
        "tenant_id":         tenant_id,
        "sector":             sector,
        "allowed_classes":   allowed,
        "is_admin":          is_admin,
        "namespace_prefix":  f"crucix:tenant:{tenant_id}",
    }


def namespace_prefix_for_visibility(
    visibility_class: str,
    tenant_id: str | None = None,
    sector: str | None = None,
) -> str:
    """Build the storage-layer key prefix for a given visibility class.

    Examples:
        PUBLIC                        -> 'crucix:tenant:public:'
        SHARED_OEM (sector=broker)    -> 'crucix:cohort:broker:'
        TENANT (tenant_id=cust_42)    -> 'crucix:tenant:cust_42:'
    """
    if visibility_class == VISIBILITY_PUBLIC:
        return f"crucix:tenant:{PUBLIC_TENANT}:"
    if visibility_class == VISIBILITY_SHARED_OEM:
        if not sector:
            raise ValueError("sector required for SHARED_OEM namespace")
        return f"crucix:cohort:{sector}:"
    if visibility_class == VISIBILITY_TENANT:
        if not tenant_id:
            raise ValueError("tenant_id required for TENANT namespace")
        return f"crucix:tenant:{tenant_id}:"
    raise ValueError(f"unknown visibility class: {visibility_class}")


def is_visible(record: dict[str, Any], request_visibility: dict[str, Any]) -> bool:
    """Test whether a record is visible to a request.

    A record carries:
        visibility:  PUBLIC / SHARED_OEM / TENANT (defaults to PUBLIC
                     when the field is absent — backwards compatible)
        cohort:      required if visibility=SHARED_OEM
        tenant_id:   required if visibility=TENANT

    A request_visibility carries:
        allowed_classes:  set of classes the user can see
        sector:           the user's cohort
        tenant_id:        the user's tenant id
        is_admin:         admin bypass
    """
    if request_visibility.get("is_admin"):
        return True

    rec_class = (record.get("visibility") or VISIBILITY_PUBLIC).upper()
    if rec_class not in request_visibility.get("allowed_classes", set()):
        return False

    if rec_class == VISIBILITY_PUBLIC:
        return True

    if rec_class == VISIBILITY_SHARED_OEM:
        rec_cohort = (record.get("cohort") or "").lower()
        return rec_cohort == (request_visibility.get("sector") or "").lower()

    if rec_class == VISIBILITY_TENANT:
        return record.get("tenant_id") == request_visibility.get("tenant_id")

    return False


def filter_visible(
    records: list[dict[str, Any]],
    request_visibility: dict[str, Any],
) -> list[dict[str, Any]]:
    """Filter a record list to only those visible to the request."""
    return [r for r in records if is_visible(r, request_visibility)]


def tag_record_with_visibility(
    record: dict[str, Any],
    *,
    visibility: str = VISIBILITY_PUBLIC,
    tenant_id: str | None = None,
    cohort: str | None = None,
) -> dict[str, Any]:
    """Stamp a record on write with its visibility metadata.

    Storage layers should call this before persisting any new record:
    knowledge.add_fact(), rag_store.ingest_document(), intel_ledger.append().

    Backwards-compatible: omitting the visibility arg keeps the record
    in the shared PUBLIC class — same behaviour as today's storage.
    """
    if visibility not in ALL_VISIBILITY_CLASSES:
        raise ValueError(f"unknown visibility class: {visibility}")
    if visibility == VISIBILITY_TENANT and not tenant_id:
        raise ValueError("tenant_id required for TENANT visibility")
    if visibility == VISIBILITY_SHARED_OEM and not cohort:
        raise ValueError("cohort required for SHARED_OEM visibility")

    out = dict(record)
    out["visibility"] = visibility
    if tenant_id:
        out["tenant_id"] = tenant_id
    if cohort:
        out["cohort"] = cohort
    return out


def summary() -> dict[str, Any]:
    return {
        "module":             "tenant_namespace",
        "purpose":            "multi-tenant isolation scaffold for proprietary uploads",
        "visibility_classes": list(ALL_VISIBILITY_CLASSES),
        "default_class":      VISIBILITY_PUBLIC,
        "wired_into":         [],   # populated as storage layers adopt
        "todo":               [
            "wire into rag_store.search(tenant_id=...)",
            "wire into knowledge.search(tenant_id=...)",
            "wire into intel_ledger.recent(tenant_id=...)",
            "encryption at rest per-tenant (Tier 4 — customer-held keys)",
        ],
    }
