"""R-F4335 (C-281) — prove the sovereign endpoint serves the model it NAMES.

THE DEFECT THIS EXISTS TO END. Measured live 2026-08-25 against the operator's
own endpoint: ``GET /v1/models`` returned TWO entries carrying the IDENTICAL id
``aria-llm-v0.4-dpo`` — one the base Mistral-7B-Instruct-v0.3 snapshot
(``parent: null``), one the LoRA adapter at ``/root/adapters/aria_llm_v0_4_dpo``
(``parent: aria-llm-v0.4-dpo``). vLLM resolves an incoming ``model`` against the
base served-names BEFORE the LoRA names, so the base won every request and the
adapter was NEVER applied. The CLI banner printed ``aria-llm/aria-llm-v0.4-dpo``
throughout — it was naming a model that was not answering.

Confirmed behaviourally, four independent probes, all temperature 0:
  * "Who are you?"                    -> "I am a model trained by Mistral AI."
  * "What does ARIA stand for ...?"   -> "Accessible Rich Internet Applications"
  * "... crucix R-number?"            -> "the reproduction number", "R packages"
  * a verbatim row from the training corpus returned a generic privacy refusal
    instead of the trained citation-contract refusal it was fine-tuned to give.

The whole v0.4 tool-use DPO programme was sitting on disk, unused, and no
surface in the tree could say so. That is the §1 "certified by an absence"
shape: an ambiguity that reads exactly like health.

WHY A NAME COLLISION IS THE RIGHT THING TO DETECT, and not model identity.
Asking the model who it is cannot work — it costs a paid turn, the answer is a
generation (so it can be wrong in both directions), and a LoRA trained only on
tool-use would legitimately still answer "Mistral". The COLLISION is structural,
free, and decidable from the server's own inventory: if the requested id matches
more than one entry, the adapter has no distinct address, whatever it contains.

TRI-STATE, and the third state is load-bearing (§1 / R-F2639). ``unknown`` means
COULD NOT MEASURE and is NEVER rendered as healthy. An unreadable endpoint is an
honest unknown; collapsing it to "ok" would rebuild the exact blindness above.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx

#: Providers whose endpoint can serve a LoRA adapter behind a served name, i.e.
#: where a collision is possible at all. Scoped for the same reason R-F4325 and
#: R-F4329 are scoped: a vendor endpoint has no adapters, so probing it could
#: only ever produce a false positive. An unknown provider is NOT probed.
_ADAPTER_CAPABLE_PROVIDERS = frozenset({"aria-llm"})

#: States. Only ``ok`` means "the requested id resolves to exactly one model".
OK = "ok"
COLLISION = "collision"
ABSENT = "absent"
UNKNOWN = "unknown"


@dataclass
class ModelIdentity:
    """What the endpoint actually serves under the requested name."""
    state: str = UNKNOWN
    model: str = ""
    matches: int = 0
    adapter_registered: bool = False
    detail: str = ""
    served_ids: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        """True ONLY for a measured, unambiguous match. ``unknown`` is not
        healthy — that is the whole point of the third state."""
        return self.state == OK

    @property
    def is_breach(self) -> bool:
        """A MEASURED fault the operator must act on. ``unknown`` is not a
        breach either: could-not-measure is not measured-and-failed."""
        return self.state in (COLLISION, ABSENT)


def identity_check_active(provider: str) -> bool:
    """Should ``provider`` be probed?

    ARIA_CLI_MODEL_IDENTITY_CHECK=0/1 overrides, mirroring the shape of
    ARIA_CLI_COMPACT_PROMPT so an operator finds the same lever in the same
    place. Default ON for an adapter-capable endpoint, OFF elsewhere.
    """
    flag = (os.getenv("ARIA_CLI_MODEL_IDENTITY_CHECK") or "").strip()
    if flag in ("0", "1"):
        return flag == "1"
    return (provider or "").strip().lower() in _ADAPTER_CAPABLE_PROVIDERS


def _remedy(model: str) -> str:
    return (
        f"relaunch vLLM giving the base and the adapter DISTINCT names, e.g. "
        f"--served-model-name aria-llm-base --lora-modules {model}=<adapter-path> "
        f"(scripts/train/serve_sovereign.sh does this and refuses to start on a "
        f"collision)"
    )


def evaluate_models_payload(payload: dict, model: str) -> ModelIdentity:
    """Pure decision function over a ``/v1/models`` body. No IO — so the whole
    contract is testable without a server, and the probe below stays a thin
    transport wrapper."""
    ident = ModelIdentity(model=model)
    data = (payload or {}).get("data")
    if not isinstance(data, list) or not data:
        ident.state = UNKNOWN
        ident.detail = "endpoint returned no model inventory"
        return ident

    entries = [e for e in data if isinstance(e, dict)]
    ident.served_ids = [str(e.get("id", "")) for e in entries]
    matches = [e for e in entries if str(e.get("id", "")) == model]
    ident.matches = len(matches)
    # A LoRA card is the one carrying a parent; vLLM sets it to the base's
    # served name. Its PRESENCE is what makes a shared id fatal rather than
    # merely untidy.
    ident.adapter_registered = any(e.get("parent") for e in entries)

    if len(matches) > 1:
        ident.state = COLLISION
        ident.detail = (
            f"{len(matches)} models are served under the id '{model}' — the base "
            f"model and the LoRA adapter share one name, so the adapter has NO "
            f"distinct address and the BASE model answers every request. "
            f"Fine-tuning is silently inert. Fix: {_remedy(model)}"
        )
    elif not matches:
        ident.state = ABSENT
        ident.detail = (
            f"the endpoint serves no model with the id '{model}' (it serves: "
            f"{', '.join(sorted(set(ident.served_ids))) or 'nothing'}) — every "
            f"request will return HTTP 404. Fix ARIA_LLM_MODEL, or relaunch the "
            f"server under the expected name."
        )
    else:
        ident.state = OK
        ident.detail = f"'{model}' resolves to exactly one served model"
    return ident


def probe_model_identity(*, base_url: str, model: str, api_key: str = "",
                         timeout: float = 8.0) -> ModelIdentity:
    """Read the endpoint's model inventory and judge it. Never raises.

    Best-effort and ONCE per session: a transport failure yields ``unknown``,
    which never blocks the CLI and is never reported as health.
    """
    url = f"{(base_url or '').rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        # no-breaker: one best-effort preflight per CLI session, never retried and
        # never on a hot path; a failure degrades to `unknown` rather than to an
        # error, so there is no repeated call for a breaker to protect.
        resp = httpx.get(url, headers=headers, timeout=timeout)
        if resp.status_code >= 400:
            return ModelIdentity(
                state=UNKNOWN, model=model,
                detail=f"model inventory unreadable: {url} returned HTTP "
                       f"{resp.status_code}")
        return evaluate_models_payload(resp.json(), model)
    except Exception as exc:  # noqa: BLE001 — a preflight must never break a session
        return ModelIdentity(
            state=UNKNOWN, model=model,
            detail=f"model inventory unreachable at {url}: "
                   f"{type(exc).__name__}: {exc}")


#: Resolved once per process. The banner renders up to three times per session
#: (start, /clear, resume) and the probe must not become three HTTP calls — nor
#: three brain signals.
_SESSION_IDENTITY: ModelIdentity | None = None
_SESSION_REPORTED = False


def reset_session_identity() -> None:
    """Test seam — forget the memoised probe."""
    global _SESSION_IDENTITY, _SESSION_REPORTED
    _SESSION_IDENTITY = None
    _SESSION_REPORTED = False


def session_model_identity(*, provider: str, base_url: str, model: str,
                           api_key: str = "", self_mode: bool = False,
                           ) -> ModelIdentity | None:
    """The session's identity verdict, probed at most once.

    Returns ``None`` when the provider is not adapter-capable — an explicit
    "not applicable", never a fabricated ``ok``.

    §21a: BOTH branches reach the brain. A breach is the signal that matters,
    but a clean probe is recorded too — otherwise "no signal" would be
    ambiguous between "verified" and "never ran", which is the ambiguity this
    whole module exists to remove.
    """
    global _SESSION_IDENTITY, _SESSION_REPORTED
    if not identity_check_active(provider):
        return None
    if _SESSION_IDENTITY is None:
        _SESSION_IDENTITY = probe_model_identity(
            base_url=base_url, model=model, api_key=api_key)
    ident = _SESSION_IDENTITY
    if not _SESSION_REPORTED:
        _SESSION_REPORTED = True
        try:
            from . import brain as _brain  # local: keeps this module import-light
            _brain.report_signal(
                signal_type=("aria_cli_model_identity_breach" if ident.is_breach
                             else "aria_cli_model_identity"),
                content=(f"ARIA CLI model identity [{ident.state}] for "
                         f"'{ident.model}' on {provider}: {ident.detail}"),
                self_mode=self_mode,
                metadata={"channel": "cli", "state": ident.state,
                          "model": ident.model, "provider": provider,
                          "matches": ident.matches,
                          "adapter_registered": ident.adapter_registered},
            )
        except Exception:  # noqa: BLE001 — the brain never blocks a session
            pass
    return ident
