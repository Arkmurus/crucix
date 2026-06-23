# Phase 3 — per-service token split (cutover runbook)

**R-F1827 (staged).** Closes authz-review finding **H1**: today one shared token
(`ARIA_INTERNAL_TOKEN`, held by the WA listener + web tier) grants **full brain
control** — autonomous control, self-deploy, cost-cap, purge, credentials. A
compromise of the most-exposed limb (the WA Baileys process) = total ecosystem
compromise. This splits that into two tiers:

- **OPERATOR token** (`ARIA_OPERATOR_TOKEN`) — full access incl. the control plane.
- **SERVICE token** (`ARIA_SERVICE_TOKEN`) — chat / read / telemetry only. Held by
  the WA listener + web tier. **Cannot** drive control/destructive routes.

The code is **live but OFF by default** — no behavior change until you flip the flag.
Operator-only routes (enforced when on): `autonomous/*`, `autonomy/*`,
`self/{improve,deploy,code}`, `coder/*`, `cost/set-cap`, `cost/reset-task`,
`admin/purge*`, `capability-gaps/purge`, `memory/backup/restore`,
`student/mastery/reset`, `portal/credentials`, `session/forget`, `eval/*`,
`operating-mode/set`, `knowledge/fact`. (See `_OPERATOR_ONLY_RE` in `routes/aria.py`.)

## Cutover (zero-downtime, additive)

1. **Mint two strong tokens** (32+ random bytes each), e.g.:
   ```bash
   OP=$(openssl rand -hex 32); SVC=$(openssl rand -hex 32)
   ```
2. **Set them on the brain** (both accepted immediately — additive, no flag yet):
   ```bash
   flyctl secrets set ARIA_OPERATOR_TOKEN="$OP" ARIA_SERVICE_TOKEN="$SVC" -a aria-intel
   ```
3. **Point each caller at the right tier:**
   - WA listener + web tier → SERVICE token:
     ```bash
     flyctl secrets set ARIA_INTERNAL_TOKEN="$SVC" -a aria-wa
     flyctl secrets set ARIA_INTERNAL_TOKEN="$SVC" -a aria-web   # (or WEB_BRAIN_TOKEN if split further)
     ```
   - Operator/admin tooling that needs the control plane → OPERATOR token.
   - (Until you do this, both old `ARIA_API_TOKEN`/`ARIA_INTERNAL_TOKEN` still work —
     they remain accepted, so nothing breaks mid-rollout.)
4. **Enable enforcement:**
   ```bash
   flyctl secrets set ARIA_TOKEN_SCOPING=1 -a aria-intel
   ```
   Now a SERVICE-token caller hitting a control/destructive route gets **403**;
   chat/read/telemetry are unaffected.
5. **Verify:**
   ```bash
   # service token on a control route → 403
   curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $SVC" \
     -X POST https://aria-intel.fly.dev/api/aria/autonomous/pause   # expect 403
   # operator token on the same route → 200/expected
   curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $OP" \
     -X POST https://aria-intel.fly.dev/api/aria/autonomous/pause
   ```
6. **(Optional, after a clean observation window)** retire the legacy shared token:
   remove `ARIA_API_TOKEN` / the old `ARIA_INTERNAL_TOKEN` value from the brain's
   accepted set once nothing presents it.

## Rollback
`flyctl secrets unset ARIA_TOKEN_SCOPING -a aria-intel` — instantly reverts to
"all accepted tokens have full access" (the pre-Phase-3 behavior). No redeploy needed.

## Notes
- Capability-tested: `aria_service/tests/test_rf1827_token_scoping.py` (off=back-compat;
  on=service-token blocked on the control plane, operator allowed, chat allowed).
- This is the brain-side tier. Decoupling the aria-web admin grant (`server.mjs:3801`,
  internal-token → `role:'admin'`) from the service token is a follow-up so a service
  credential is not also an interactive-admin credential.
