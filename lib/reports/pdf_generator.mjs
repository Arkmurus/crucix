// lib/reports/pdf_generator.mjs
// PDF Report Generator — Monthly Brief + Approach Pack
// Uses pdfkit (pure Node.js, no Python, no native deps)

import PDFDocument from 'pdfkit';

// ── Brand colours ────────────────────────────────────────────────────────────
const PURPLE = '#913BFF';
const DARK = '#0f0a1e';
const BLUE = '#0066FF';
const WHITE = '#ffffff';
const GREY = '#aaaaaa';
const RED = '#ef4444';
const GREEN = '#22c55e';
const ORANGE = '#FF7A41';

// ── Helpers ──────────────────────────────────────────────────────────────────
function addHeader(doc, title, subtitle) {
  doc.rect(0, 0, doc.page.width, 80).fill(DARK);
  doc.fontSize(22).fillColor(PURPLE).text('ARKMURUS', 40, 20);
  doc.fontSize(9).fillColor(GREY).text('INTELLIGENCE', 165, 28);
  doc.fontSize(14).fillColor(WHITE).text(title, 40, 50);
  if (subtitle) doc.fontSize(9).fillColor(GREY).text(subtitle, 40, 68);
  doc.fillColor('#333333');
  doc.y = 100;
}

function addSection(doc, title) {
  if (doc.y > doc.page.height - 120) doc.addPage();
  doc.moveDown(0.5);
  doc.fontSize(11).fillColor(PURPLE).text(title.toUpperCase(), { underline: true });
  doc.moveDown(0.3);
  doc.fillColor('#333333').fontSize(9);
}

function addBullet(doc, text, indent = 40) {
  if (doc.y > doc.page.height - 60) doc.addPage();
  doc.fontSize(9).fillColor('#333333').text('•  ' + text, indent, doc.y, { width: doc.page.width - indent - 40 });
  doc.moveDown(0.2);
}

function addKeyValue(doc, key, value) {
  if (doc.y > doc.page.height - 60) doc.addPage();
  doc.fontSize(9).fillColor(GREY).text(key + ':', 40, doc.y, { continued: true });
  doc.fillColor('#333333').text('  ' + (value || '—'));
  doc.moveDown(0.1);
}

function addParagraph(doc, text) {
  if (doc.y > doc.page.height - 80) doc.addPage();
  doc.fontSize(9).fillColor('#444444').text(text, 40, doc.y, { width: doc.page.width - 80, lineGap: 3 });
  doc.moveDown(0.5);
}

function addFooter(doc) {
  const pages = doc.bufferedPageRange();
  for (let i = pages.start; i < pages.start + pages.count; i++) {
    doc.switchToPage(i);
    doc.fontSize(7).fillColor(GREY)
      .text('ARKMURUS INTELLIGENCE — CONFIDENTIAL', 40, doc.page.height - 30)
      .text(`Page ${i + 1} of ${pages.count}`, doc.page.width - 100, doc.page.height - 30);
  }
}

// ── Monthly Intelligence Brief ───────────────────────────────────────────────
export function generateMonthlyBrief(data) {
  return new Promise((resolve, reject) => {
    try {
      const doc = new PDFDocument({ size: 'A4', margin: 40, bufferPages: true });
      const chunks = [];
      doc.on('data', c => chunks.push(c));
      doc.on('end', () => resolve(Buffer.concat(chunks)));

      const now = new Date();
      const monthName = now.toLocaleString('en-GB', { month: 'long', year: 'numeric' });

      addHeader(doc, `Monthly Intelligence Brief — ${monthName}`,
        `Generated ${now.toISOString().slice(0, 16).replace('T', ' ')} UTC | ARIA v3.0`);

      // 1. Executive Summary
      addSection(doc, '1. Executive Summary');
      const bd = data.bdIntelligence;
      const opps = data.opportunities || [];
      const tenders = bd?.tenders || [];
      const ideas = bd?.ideas || [];
      const brain = bd?.brain;
      addParagraph(doc,
        `This month, ARIA monitored ${data.meta?.sourcesQueried || 48} intelligence sources across ${opps.length} target markets. ` +
        `${tenders.length} procurement tenders were identified and ${ideas.length} strategic ideas generated. ` +
        (brain?.weeklyPriority?.action ? `Top priority: ${brain.weeklyPriority.action}` : 'No brain priority set this cycle.'));

      // 2. Top Opportunities
      addSection(doc, '2. Top Procurement Opportunities');
      if (opps.length) {
        opps.slice(0, 8).forEach(o => {
          addBullet(doc, `${o.market} — Score ${o.score}/100 (${o.tier}) | ${(o.procurementNeeds || []).slice(0, 3).join(', ')} | ${o.complianceStatus}`);
          if (o.notes) addParagraph(doc, '   ' + o.notes);
        });
      } else {
        addParagraph(doc, 'No opportunities detected this cycle. Run a sweep to refresh.');
      }

      // 3. Brain Strategy
      if (brain) {
        addSection(doc, '3. Brain Strategy Assessment');
        if (brain.weeklyPriority) {
          addKeyValue(doc, 'TOP PRIORITY', brain.weeklyPriority.action);
          addKeyValue(doc, 'Market', brain.weeklyPriority.market);
          addKeyValue(doc, 'Why now', brain.weeklyPriority.whyNow);
          addKeyValue(doc, 'First step', brain.weeklyPriority.firstStep);
        }
        if (brain.salesLeads?.length) {
          doc.moveDown(0.5);
          doc.fontSize(10).fillColor(PURPLE).text('Sales Leads:');
          brain.salesLeads.forEach(l => {
            addBullet(doc, `[${l.urgency}] ${l.market}: ${(l.lead || '').slice(0, 100)} — ${l.estimatedValue || '?'}`);
          });
        }
        if (brain.selfLearning?.strategyAdjustment) {
          doc.moveDown(0.3);
          addKeyValue(doc, 'Strategy adjustment', brain.selfLearning.strategyAdjustment);
        }
      }

      // 4. Active Tenders
      addSection(doc, '4. Active Tenders');
      if (tenders.length) {
        tenders.slice(0, 10).forEach(t => {
          addBullet(doc, `[${t.leadQuality || '—'}] ${t.market} — ${(t.title || '').slice(0, 80)} (Score: ${t.score}, Win: ${t.winProbability || '?'}%)`);
        });
      } else {
        addParagraph(doc, 'No tenders identified this cycle.');
      }

      // 5. Pipeline
      addSection(doc, '5. Deal Pipeline');
      const pipeline = bd?.pipeline || [];
      if (pipeline.length) {
        pipeline.slice(0, 10).forEach(d => {
          addBullet(doc, `${d.id || '?'} | ${d.market} | ${d.stage} | ${(d.title || '').slice(0, 60)}`);
        });
      } else {
        addParagraph(doc, 'Pipeline empty.');
      }

      // 6. Correlations
      addSection(doc, '6. Regional Situation Awareness');
      const corrs = data.correlations || [];
      if (corrs.length) {
        corrs.slice(0, 8).forEach(c => {
          addBullet(doc, `${c.region} [${(c.severity || '').toUpperCase()}]: ${(c.topSignals?.[0]?.text || '').slice(0, 120)}`);
        });
      }

      // 7. Compliance
      addSection(doc, '7. Compliance Notes');
      addParagraph(doc, 'All opportunities pre-screened against OFAC SDN, OFSI, UN SC, EU consolidated sanctions lists. Export control flags (ITAR/EAR/EU dual-use) noted per tender. End-user certificate requirements apply to all transactions.');

      // 8. Next Actions
      addSection(doc, '8. Recommended Actions');
      addBullet(doc, 'Review top 3 opportunities and decide GO/NO-GO within 48 hours');
      addBullet(doc, 'Check active relationship windows (/windows on Telegram)');
      addBullet(doc, 'Follow up on stale pipeline deals');
      addBullet(doc, 'Run /sweep daily to maintain intelligence freshness');

      addFooter(doc);
      doc.end();
    } catch (e) { reject(e); }
  });
}

// ── Approach Pack ────────────────────────────────────────────────────────────
export function generateApproachPack(market, product, approach, gtm, contacts) {
  return new Promise((resolve, reject) => {
    try {
      const doc = new PDFDocument({ size: 'A4', margin: 40, bufferPages: true });
      const chunks = [];
      doc.on('data', c => chunks.push(c));
      doc.on('end', () => resolve(Buffer.concat(chunks)));

      addHeader(doc, `Approach Pack — ${market}`,
        `${product || 'General'} | Generated ${new Date().toISOString().slice(0, 16).replace('T', ' ')} UTC`);

      // 1. Market Profile
      addSection(doc, '1. Market Profile');
      if (approach?.profile) {
        addKeyValue(doc, 'Language', approach.profile.language);
        addKeyValue(doc, 'Formality', approach.profile.formality);
        addKeyValue(doc, 'Greeting', approach.profile.greeting);
        addKeyValue(doc, 'Timezone', approach.profile.timezone);
        addKeyValue(doc, 'Currency', approach.profile.currency);
        addKeyValue(doc, 'Approach', approach.profile.approach);
      }

      // 2. GTM Strategy
      if (gtm) {
        addSection(doc, '2. Go-To-Market Strategy');
        addKeyValue(doc, 'Relationship Tier', gtm.tier);
        addKeyValue(doc, 'Time to First Deal', gtm.timeToFirstDeal);
        addKeyValue(doc, 'Exhibition', gtm.exhibition);
        addKeyValue(doc, 'Best OEMs', gtm.bestOEM);
        addKeyValue(doc, 'Offset', gtm.offset);
        addKeyValue(doc, 'Partner Needed', String(gtm.playbook?.partnerNeeded || 'No'));
        addKeyValue(doc, 'Local Agent', String(gtm.playbook?.localAgentNeeded || 'No'));
        addKeyValue(doc, 'Key Risk', gtm.playbook?.keyRisk);
        doc.moveDown(0.3);
        doc.fontSize(10).fillColor(PURPLE).text('Steps:');
        (gtm.playbook?.steps || []).forEach((s, i) => addBullet(doc, `${i + 1}. ${s}`));
      }

      // 3. Recommended OEMs
      addSection(doc, '3. Ranked OEM Partners');
      (approach?.rankedOEMs || []).forEach((o, i) => {
        addBullet(doc, `${i + 1}. ${o.oem} (${o.country}) — Price: ${o.price} | Africa: ${o.africa} | ${o.itar ? 'ITAR-CONTROLLED' : 'Non-ITAR'}`);
        addParagraph(doc, `   Products: ${o.products}`);
      });

      // 4. Key Contacts
      addSection(doc, '4. Key Decision Makers');
      if (contacts?.length) {
        contacts.slice(0, 6).forEach(c => {
          addBullet(doc, `${c.name} — ${c.title || c.role}`);
          if (c.organisation) addParagraph(doc, `   ${c.organisation} | Influence: ${c.influence || '—'}`);
        });
      } else {
        addParagraph(doc, 'No contacts in database for this market. Recommend HUMINT development.');
      }

      // 5. Compliance Checklist
      addSection(doc, '5. Compliance Checklist');
      (approach?.compliance || []).forEach(c => addBullet(doc, c));

      // 6. Draft Opening Message
      if (approach?.draftMessage) {
        addSection(doc, '6. Draft Opening Message');
        addParagraph(doc, approach.draftMessage);
      }

      // 7. Estimated Timeline
      addSection(doc, '7. Deal Economics');
      addKeyValue(doc, 'Estimated Cycle', approach?.estimatedCycle || '3-12 months');
      addKeyValue(doc, 'Commission Range', '5-12% of total deal value');
      addParagraph(doc, 'Note: factor training package (5-15% of equipment cost), spares (10-20%), logistics (3-8%), and offset obligations into total deal value before calculating commission.');

      addFooter(doc);
      doc.end();
    } catch (e) { reject(e); }
  });
}
