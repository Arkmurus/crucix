"""ARIA Guardian (R-F1979) — the safety-companion subsystem.

Guardian is ARIA's second identity: it ACTS in the real world on the user's
behalf (texts as them, alerts their trusted circle, runs check-ins) — a
different RISK CLASS from the analyst. Everything dangerous goes through ONE
hardened door, the Action Gateway, with consent tiers, an encrypted trusted
circle, a tamper-evident audit chain, a panic kill-switch, a durable queue, and
§25 delivery-outcome wiring. Fail-safe: a SAFETY action that can't be delivered
ESCALATES; a non-consented action is REFUSED — never silently sent.

Phase 0 (this module): the gateway spine + the check-in / dead-man's switch.
The law is the boundary (AGENTS.md): bodyguard for the user and THEIR circle,
never covert surveillance of non-consenting third parties.
"""
