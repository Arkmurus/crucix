export const meta = {
  name: 'dd-state-of-the-art-blueprint',
  description: 'Design + grounded audit of ARIA DD tooling → adversarially-verified, R-number-ready enterprise blueprint',
  phases: [
    { title: 'Design pillars', detail: 'define what best-in-class corporate DD requires, per pillar' },
    { title: 'Grounded module audit', detail: 'deep-read each DD module group vs the pillars; emit concrete fix specs (file:line)' },
    { title: 'Adversarial verify', detail: 'skeptics kill hallucinated/redundant/mis-scoped findings (the R-F1787 already-exists trap)' },
    { title: 'Synthesis', detail: 'prioritized implementation blueprint' },
  ],
}

// ── The seven pillars of a best-in-class corporate DD platform ──
// Each is researched/designed independently, grounded in how real KYC/AML/
// third-party-risk platforms (World-Check, LexisNexis, Sayari, Moody's Orbis,
// OpenSanctions/OpenCorporates) actually operate — then turned into a target spec.
const PILLARS = [
  { key: 'evidence_authority', title: 'Evidence & source-authority doctrine',
    prompt: 'Design the EVIDENCE & SOURCE-AUTHORITY doctrine for an enterprise DD platform: source tiering by evidentiary weight (primary registry/regulator > corroborating press/OpenSanctions > grey > weak), N-source corroboration before any adverse finding, divergence/conflict surfacing, provenance capture (url + retrieved_at + tier + verbatim snippet), as-of/point-in-time dating, and confidence derived from tier x corroboration. Contrast with academic CRAAP/SIFT and say exactly why CRAAP is insufficient for DD. Output the target contract (data shapes + rules) a compliance buyer would demand for defensibility.' },
  { key: 'entity_resolution', title: 'Entity resolution & disambiguation',
    prompt: 'Design ENTITY RESOLUTION for DD: resolving legal name + registration number + jurisdiction + domains + aliases into one EntityKey BEFORE search, same-name false-positive/false-negative control, cross-jurisdiction identity stitching. State the target contract and the failure modes that cause legal liability if skipped.' },
  { key: 'sanctions_pep_media', title: 'Sanctions / PEP / adverse-media coverage',
    prompt: 'Design best-in-class SANCTIONS + PEP + ADVERSE-MEDIA screening for DD: list coverage (OFAC/UN/EU/UK/OFSI + sectoral), fuzzy + transliteration + nasab/Arabic + CJK matching, secondary-sanctions & ownership-propagation (50%% rule), multilingual adverse-media, and corroboration. Define precision/recall targets and the false-negative classes that are unacceptable for a corporate buyer.' },
  { key: 'ubo_network', title: 'UBO & network/ownership graph',
    prompt: 'Design the UBO + OWNERSHIP-NETWORK graph for DD: beneficial-ownership walk to natural persons, circular/opaque-structure detection, PSC/officer cross-appointment graph, control vs ownership, depth limits and provenance per edge. State the target contract and the depth competitors reach.' },
  { key: 'provenance_audit', title: 'Provenance, audit trail & defensibility',
    prompt: 'Design the AUDIT/DEFENSIBILITY layer: every report statement traceable to a tiered source with retrieval timestamp, immutable/append-only evidence ledger, signed reports, reproducibility (re-run yields same as-of finding), and a per-report COMPLETENESS MANIFEST (which layers ran/skipped/failed/unreachable — no silent omission). Map to regulatory expectations (EU AMLD, UK MLR, US OFAC guidance).' },
  { key: 'report_quality', title: 'Report quality & confidence calibration',
    prompt: 'Design REPORT QUALITY for DD: BLUF risk classification, calibrated confidence (not overconfident), explicit assumptions/limitations, citation validity (no fabricated/dangling citations), and graceful partial-report rendering when layers are incomplete. Define what a corporate analyst expects to see and the anti-hallucination guarantees.' },
  { key: 'reliability_scale', title: 'Reliability & scale architecture',
    prompt: 'Design RELIABILITY/SCALE for a single-process async DD engine: never block the event loop (offload sync CPU), hard per-run + per-layer deadlines with partial emission, layer isolation (one failing source never kills the report), self-heal wiring (every layer success AND failure reaches the brain as a signal/gap), and concurrency limits. State the target architecture.' },
]

// ── The DD module surface (from the AST probe) grouped for audit ──
const MODULE_GROUPS = [
  { key: 'orchestrator', files: 'aria_service/intel/dd_orchestrator.py (8919L), dd_schema.py, dd_layer_extensions.py, dd_vault.py, financial_dd.py' },
  { key: 'investigation', files: 'aria_service/intel/company_investigator.py, link_investigator.py, network_walker.py, investigation_thread.py' },
  { key: 'sanctions', files: 'aria_service/intel/sanctions.py, country_sanctions.py, crypto_sanctions.py, rca_screening.py, _sanctions_classify.py, sanctions_claim_guard.py, sanctions_divergence.py, sanctions_propagation.py' },
  { key: 'support', files: 'aria_service/intel/researcher.py (4256L), web_search.py, citation_validator.py, adversarial_challenge.py, confidence_footer.py, companies_house.py' },
]

const PILLAR_SCHEMA = {
  type: 'object',
  properties: {
    pillar: { type: 'string' },
    target_contract: { type: 'string', description: 'data shapes + rules the platform must satisfy' },
    why_better_than_baseline: { type: 'string' },
    must_haves: { type: 'array', items: { type: 'string' } },
    measurable_targets: { type: 'array', items: { type: 'string' } },
  },
  required: ['pillar', 'target_contract', 'must_haves'],
}

const AUDIT_SCHEMA = {
  type: 'object',
  properties: {
    group: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          file: { type: 'string' }, line: { type: 'string' },
          gap: { type: 'string', description: 'what is missing/weak vs the design pillars' },
          fix: { type: 'string', description: 'concrete fix, implementation-ready' },
          pillar: { type: 'string' },
          severity: { type: 'string', enum: ['P0', 'P1', 'P2', 'P3'] },
          value: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          effort: { type: 'string', enum: ['S', 'M', 'L'] },
          already_exists_check: { type: 'string', description: 'evidence (file:line) you checked this is NOT already implemented — the R-F1787 trap' },
        },
        required: ['title', 'file', 'gap', 'fix', 'severity', 'value', 'already_exists_check'],
      },
    },
  },
  required: ['group', 'findings'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['confirmed', 'redundant', 'hallucinated', 'misscoped'] },
    reason: { type: 'string' },
    corrected_fix: { type: 'string' },
  },
  required: ['verdict', 'reason'],
}

phase('Design pillars')
const designs = await parallel(PILLARS.map(p => () =>
  agent(`You are designing one pillar of a best-in-class corporate due-diligence platform.\n\nPILLAR: ${p.title}\n\n${p.prompt}\n\nGround your design in how real enterprise DD/KYC platforms operate. Be concrete and demanding — this is sold to big corporations; primitive tooling is unacceptable. Return the target contract.`,
    { label: `design:${p.key}`, phase: 'Design pillars', schema: PILLAR_SCHEMA, effort: 'high' })
))
const pillarDesigns = designs.filter(Boolean)
log(`Designed ${pillarDesigns.length}/${PILLARS.length} pillars`)

const designContext = pillarDesigns.map(d =>
  `### ${d.pillar}\nMUST-HAVES: ${(d.must_haves || []).join('; ')}\nCONTRACT: ${(d.target_contract || '').slice(0, 600)}`
).join('\n\n')

// Pipeline: each module group audits vs the pillars, then each of its findings is
// adversarially verified as soon as that group's audit completes (no barrier).
const audited = await pipeline(
  MODULE_GROUPS,
  g => agent(
    `Deep-read these ARIA DD modules and audit them against the TARGET DESIGN for a best-in-class corporate DD platform.\n\nMODULES: ${g.files}\n\nTARGET DESIGN (the bar to hit):\n${designContext}\n\nFor EACH gap between the current code and the target, emit a finding with the exact file:line, the gap, and an implementation-ready fix mapped to a pillar.\n\nCRITICAL — avoid the "already-exists" trap (we just abandoned an R-number for re-proposing a deadline that R-F1628 already built): for every finding, READ the surrounding code and fill already_exists_check with the file:line evidence proving the capability is NOT already present. If it is already present, do not emit the finding. Be concrete; this becomes a build plan.`,
    { label: `audit:${g.key}`, phase: 'Grounded module audit', agentType: 'Explore', schema: AUDIT_SCHEMA, effort: 'high' }
  ),
  (auditResult, g) => {
    const findings = (auditResult && auditResult.findings) || []
    if (!findings.length) return { group: g.key, confirmed: [] }
    return parallel(findings.map(f => () =>
      agent(
        `Adversarially verify this proposed DD fix. Default to skeptical.\n\nFINDING: ${f.title}\nFILE: ${f.file}:${f.line || '?'}\nGAP: ${f.gap}\nPROPOSED FIX: ${f.fix}\nAUTHOR'S already-exists check: ${f.already_exists_check}\n\nRead the actual code. Decide:\n- redundant: the capability already exists (cite file:line)\n- hallucinated: the file/line/behavior described is wrong\n- misscoped: real gap but wrong fix or wrong severity\n- confirmed: a real, non-redundant, correctly-scoped gap\n\nIf confirmed but the fix is weak, provide corrected_fix.`,
        { label: `verify:${g.key}:${(f.file || '').split('/').pop()}`, phase: 'Adversarial verify', agentType: 'Explore', schema: VERDICT_SCHEMA, effort: 'high' }
      ).then(v => ({ ...f, group: g.key, verdict: v }))
    )).then(rs => ({ group: g.key, confirmed: rs.filter(Boolean).filter(x => x.verdict && x.verdict.verdict === 'confirmed') }))
  }
)

const confirmed = audited.filter(Boolean).flatMap(a => a.confirmed || [])
log(`Confirmed ${confirmed.length} real, verified findings across ${MODULE_GROUPS.length} module groups`)

phase('Synthesis')
const blueprint = await agent(
  `Synthesize the final ENTERPRISE DD BLUEPRINT for ARIA from the verified findings below.\n\nPILLAR DESIGNS:\n${designContext}\n\nVERIFIED FINDINGS (${confirmed.length}):\n${JSON.stringify(confirmed.map(f => ({ title: f.title, file: f.file, line: f.line, fix: f.verdict?.corrected_fix || f.fix, pillar: f.pillar, severity: f.severity, value: f.value, effort: f.effort })), null, 1)}\n\nProduce:\n1. An executive summary of where ARIA's DD stands vs best-in-class and the biggest gaps to close.\n2. A PRIORITIZED, sequenced build plan grouped into workstreams (value x effort), each item ready to become an R-number with file:line + concrete fix + a capability-test idea.\n3. The target data contracts (EntityKey, Finding/provenance, SourceTier, CompletenessManifest) as concrete shapes.\n4. Measurable acceptance criteria per workstream (what proves it's best-in-class).\n\nBe decisive and concrete. Markdown.`,
  { label: 'synthesize-blueprint', phase: 'Synthesis', effort: 'high' }
)

return { pillars: pillarDesigns.length, confirmed_findings: confirmed.length, blueprint }
