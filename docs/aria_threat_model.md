# ARIA Threat Model — R-F1130

## Scope

This document enumerates ARIA's attack surface, maps each vector to its current
defense and the remaining gap, and prioritises hardening by severity.

ARIA is an autonomous intelligence platform that:
- Crawls the web (49+ sources, 5-7 min cadence)
- Processes untrusted documents (PDF, DOCX, XLSX, email attachments)
- Executes shell commands via the `run` tool
- Installs Python packages via `pip`
- Sends/receives email and WhatsApp messages
- Stores credentials and API keys
- Runs autonomous self-modifying code (self_coder)
- Deploys code to production (fly.io)

Each of these is an attack vector.

---

## Threat Inventory

### T1: Malicious Document / File Content
**Vector:** ARIA downloads and processes untrusted files from the web (PDFs from
tender portals, DOCX from email attachments, XLSX from trade data sources).

**Current defenses:**
- `security.py`: dangerous extension blocklist (`.exe`, `.bat`, `.js`, `.py`, etc.)
- `security.py`: max download size (50MB), max attachment size (25MB)
- `security.py`: URL validation (SSRF protection, private IP blocking)
- `file_type_detector.py`: magic-byte validation against claimed extension
- `antivirus.py`: pattern-based prompt injection detection

**Gaps:**
- **No malware content scanning** — files pass through to document_reader without
  being scanned for known malware signatures (EICAR, real malware hashes)
- **No decompression bomb protection** — a zip bomb would pass size checks
  (50MB compressed could be TBs decompressed)
- **No embedded script detection** — PDFs with embedded JavaScript, DOCX with
  macros, XLSX with DDE formulas are not inspected before processing
- **No quarantine workflow** — suspicious files are not isolated before analysis

**Severity: CRITICAL** — this is the highest-exposure vector. ARIA processes
documents from untrusted government portals, email attachments, and web crawls
constantly.

### T2: Supply Chain / Package Installation
**Vector:** The `run` tool can execute `pip install` commands. A compromised
dependency or typosquatted package could execute arbitrary code in ARIA's
environment.

**Current defenses:**
- `safety.py`: cost/rate guardrails
- `constitutional_validator.py`: blocks writes to PROTECTED_FILES
- `self_improve.py`: NO_AUTODEPLOY_FILES guard

**Gaps:**
- **No package allowlist** — any PyPI package can be installed
- **No typosquatting detection** — `transformers` vs `transfomers` not caught
- **No install-scope audit** — no record of what was installed and why
- **No post-install verification** — no hash/signature check on installed packages

**Severity: CRITICAL** — a single `pip install` of a malicious package is
game-over for the environment.

### T3: Prompt Injection / Manipulation
**Vector:** Untrusted content from web pages, emails, or documents contains
prompt injection payloads that attempt to manipulate ARIA's behaviour.

**Current defenses:**
- `antivirus.py`: pattern-based detection (DAN jailbreak, security disable)
- `prompt_injection_suite.py`: comprehensive injection detection
- `premise_verifier.py`: verifies premises before acting
- `constitutional_validator.py`: constitutional validation of all outputs
- `security_protocol.py`: security protocol enforcement
- `rag_store.py`: sanitization for prompt injection in RAG context
- `adversarial_challenge.py`: 23 attack vectors tested weekly (73.9% pass rate)

**Gaps:**
- **No content-vs-instruction separation** — web content and user instructions
  share the same context window
- **No provenance-based trust scoring** — content from a .gov domain is treated
  the same as content from a random blog
- **Multi-turn drift still succeeds** — C_GRADUAL category at 0% on last run

**Severity: HIGH** — defenses are strong (100% on authority spoofing) but
multi-turn drift is a known weakness.

### T4: SSRF / Internal Network Access
**Vector:** ARIA fetches URLs from untrusted sources. A malicious URL could
target internal services (cloud metadata, internal APIs, Redis).

**Current defenses:**
- `security.py`: private IP blocking, blocked hostnames (169.254.169.254 etc.)
- `security.py`: protocol allowlist (http/https only)
- `security.py`: URL length limit (2048 chars)

**Gaps:**
- **No DNS rebinding protection** — a hostname that resolves to a public IP at
  validation time but a private IP at fetch time would bypass the check
- **No redirect chain validation** — a safe URL that redirects to an internal
  URL would not be caught

**Severity: HIGH** — existing defenses cover the common cases but DNS rebinding
and redirect chains are unaddressed.

### T5: Credential / Secret Exfiltration
**Vector:** ARIA stores API keys, portal credentials, and encryption keys.
A compromised module or successful prompt injection could exfiltrate these.

**Current defenses:**
- `portal_registry.py`: Fernet encryption for stored credentials
- `security_protocol.py`: blocks secrets in output
- `constitutional_validator.py`: blocks secret disclosure

**Gaps:**
- **No comprehensive secret scanning** — not all output paths check for secrets
- **No outbound data-loss prevention** — no monitoring of what data leaves the
  environment
- **No secret rotation** — no automated key rotation

**Severity: HIGH** — encryption exists but exfiltration detection is weak.

### T6: Autonomous Self-Coder as Internal Vector
**Vector:** The self_coder can modify any non-PROTECTED file. A compromised
gap_detector or LLM provider could cause the coder to write malicious code.

**Current defenses:**
- `constitutional_validator.py`: PROTECTED_FILES (constitutional core, deploy
  pipeline, security modules)
- `self_improve.py`: NO_AUTODEPLOY_FILES (boot path, auth, rate limiter)
- `safety.py`: cost/rate guardrails ($300/mo cap)
- `self_coder.py`: R-F1128 protected-file gap filter
- `claude_reviewer.py`: Claude review hook for staged changes

**Gaps:**
- **No code-diff review for non-protected files** — the coder can modify any
  non-protected file without review
- **No behavioural anomaly detection** — unexpected write patterns (mass
  deletion, unusual imports) are not flagged
- **No rollback capability** — a bad deploy can't be automatically reverted

**Severity: HIGH** — PROTECTED_FILES covers the critical path but non-protected
files (intel modules, routes) are wide open.

### T7: Outbound Communication Abuse
**Vector:** ARIA sends email, WhatsApp messages, and API calls. A compromised
module could send malicious or misleading communications.

**Current defenses:**
- `commitment_guard.py`: blocks unauthorised commitments
- `protective_reply_drafter.py`: reviews outbound content
- `stream_honesty.py`: honesty checks on streamed output

**Gaps:**
- **No outbound content scanning** — no malware/phishing check on outbound
  messages
- **No rate anomaly detection** — unexpected burst of outbound messages not
  flagged

**Severity: MEDIUM** — existing guards cover the most dangerous cases.

### T8: Dependency Vulnerability
**Vector:** ARIA depends on 100+ Python packages. A vulnerability in any
dependency could be exploited.

**Current defenses:**
- `requirements.txt`: pinned versions
- Docker build: fresh install on each deploy

**Gaps:**
- **No automated vulnerability scanning** — no `pip audit` or Dependabot
- **No SBOM** — no software bill of materials for tracking

**Severity: MEDIUM** — pinned versions reduce risk but don't eliminate it.

---

## Prioritised Hardening Plan

| Priority | Threat | Defense | R-number | Status |
|----------|--------|---------|----------|--------|
| **P1** | T1: Malicious documents | Content scanner + quarantine | R-F1131 | Planned |
| **P1** | T2: Supply chain | Package allowlist + typosquat detect | R-F1132 | Planned |
| **P2** | T3: Prompt injection | Prove existing defenses + close gaps | R-F1133 | Planned |
| **P2** | T5: Secret protection | Comprehensive secret scanning | R-F1134 | Planned |
| **P2** | T6: Malware knowledge base | IOC + malware family knowledge | R-F1135 | Planned |
| **P3** | T4: SSRF | DNS rebinding + redirect chain protection | R-F1136 | Planned |
| **P3** | T6: Self-coder anomaly | Behavioural anomaly detection | R-F1137 | Planned |
| **P3** | T7: Outbound abuse | Outbound content scanning | R-F1138 | Planned |
| **P3** | T8: Dependency vulns | pip audit + SBOM | R-F1139 | Planned |

---

## Current Defense Coverage Map

```
Vector                  | Detect | Prevent | Respond | Recover
------------------------|--------|---------|---------|--------
Malicious documents     | Partial| Partial | None    | None
Supply chain            | None   | None    | None    | None
Prompt injection        | Strong | Strong  | Partial | None
SSRF                    | Strong | Strong  | None    | None
Credential exfiltration | Weak   | Partial | None    | None
Self-coder abuse        | Strong | Strong  | Weak    | None
Outbound abuse          | Weak   | Partial | None    | None
Dependency vulns        | None   | Weak    | None    | None
```

**Key insight:** ARIA is strong at DETECT and PREVENT for the most obvious
vectors (SSRF, prompt injection) but has almost NO RESPOND or RECOVER capability.
If a threat gets through, there's no quarantine, no rollback, no incident response.
