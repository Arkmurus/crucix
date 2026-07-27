# R-F3225 — watchlist policy and alert lifecycle

- A watchlist review schedule belongs to each entity, not to a global UI action.
  Persist a validated cadence and let an hourly scheduler select only due entries.
- Customer watchlist mutations must be explicitly owner-pinned in the web proxy
  for every role; a generic privileged proxy is not an adequate tenant boundary.
- Alerts need stable identifiers even when legacy records predate IDs. Derive an
  opaque ID from canonical alert content and delete the exact stored list value.
- DD reports and vetting cases should use one prefilled watchlist add contract so
  entity identity, provenance, and review policy cannot drift between surfaces.
