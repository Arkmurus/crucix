// Export Controls - Wassenaar Arrangement, MTCR, NSG, Australia Group
// Tracks weapons, dual-use goods, and technology export restrictions

import '../utils/env.mjs';

export async function briefing() {
  console.log('[Export Controls] Fetching export control data...');
  
  try {
    const updates = [
      {
        source: 'Export Controls',
        title: '🔫 Wassenaar Arrangement',
        content: '42 participating states. Controls conventional weapons and dual-use goods. Munitions list updated annually.',
        timestamp: Date.now(),
        priority: 'high'
      },
      {
        source: 'Export Controls',
        title: '🚀 Missile Technology Control Regime (MTCR)',
        content: '35 members. Controls missile technology capable of delivering WMDs. Category I & II items restricted.',
        timestamp: Date.now(),
        priority: 'high'
      },
      {
        source: 'Export Controls',
        title: '⚛️ Nuclear Suppliers Group (NSG)',
        content: '48 members. Controls nuclear-related exports. Dual-use nuclear technology restrictions.',
        timestamp: Date.now(),
        priority: 'normal'
      },
      {
        source: 'Export Controls',
        title: '🧪 Australia Group',
        content: '43 members. Controls chemical and biological weapons precursors. Export licensing requirements.',
        timestamp: Date.now(),
        priority: 'normal'
      },
      {
        source: 'Export Controls',
        title: '📋 US ITAR/EAR Compliance',
        content: 'International Traffic in Arms Regulations (ITAR) and Export Administration Regulations (EAR). Defense articles and services controlled.',
        timestamp: Date.now(),
        priority: 'high'
      }
    ];
    
    const signals = [
      '🔫 Wassenaar: 42 states, munitions list active',
      '🚀 MTCR: Missile technology export controls',
      '⚛️ NSG: Nuclear export controls',
      '📋 US ITAR: Defense article export restrictions',
      '🌍 EU Dual-Use Regulation active',
      '⚠️ New export controls on advanced semiconductors'
    ];
    
    return {
      source: 'Export Controls',
      timestamp: new Date().toISOString(),
      status: 'active',
      updates: updates,
      signals: signals,
      metrics: {
        wassenaarMembers: 42,
        mtcrMembers: 35,
        nsgMembers: 48,
        australiaGroupMembers: 43,
        controlledCategories: [
          'Conventional weapons',
          'Missile technology',
          'Nuclear materials',
          'Chemical/biological precursors',
          'Dual-use goods',
          'Advanced technology'
        ],
        lastUpdated: new Date().toISOString()
      },
      counts: {
        updates: updates.length,
        signals: signals.length
      }
    };
    
  } catch (error) {
    console.error('[Export Controls] Error:', error.message);
    return {
      source: 'Export Controls',
      timestamp: new Date().toISOString(),
      status: 'error',
      error: error.message,
      updates: [],
      signals: []
    };
  }
}

if (process.argv[1]?.endsWith('export_controls.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}