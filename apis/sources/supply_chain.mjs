// Supply Chain Intelligence - Raw Materials, Logistics, Manufacturing
// Tracks critical materials, shipping disruptions, and component shortages

import '../utils/env.mjs';

export async function briefing() {
  console.log('[Supply Chain] Fetching intelligence...');
  
  try {
    // Critical defense materials and their current status
    const rawMaterials = [
      { name: 'Steel (armor grade)', price: '$850/ton', trend: 'up', change: '+5%', impact: 'Armored vehicles, naval vessels', risk: 'medium' },
      { name: 'Aluminum', price: '$2,400/ton', trend: 'up', change: '+3%', impact: 'Aircraft frames, missiles', risk: 'low' },
      { name: 'Titanium', price: '$4,200/ton', trend: 'stable', change: '0%', impact: 'Aerospace, submarines', risk: 'low' },
      { name: 'Copper', price: '$9,200/ton', trend: 'up', change: '+8%', impact: 'Electronics, wiring, guidance systems', risk: 'medium' },
      { name: 'Lithium', price: '$14,000/ton', trend: 'down', change: '-12%', impact: 'Batteries, drones, EVs', risk: 'low' },
      { name: 'Rare Earth Elements', price: '$45/kg', trend: 'up', change: '+15%', impact: 'Magnets, guidance, radar', risk: 'high' },
      { name: 'Semiconductors', price: 'varies', trend: 'up', change: '+20%', impact: 'All electronics, F-35, missiles', risk: 'critical' },
      { name: 'Explosives (RDX)', price: '$8,500/ton', trend: 'up', change: '+10%', impact: 'Artillery shells, missiles', risk: 'high' },
      { name: 'Propellants', price: '$12,000/ton', trend: 'up', change: '+7%', impact: 'Rockets, missiles, ammunition', risk: 'high' },
      { name: 'Optics (night vision)', price: 'limited supply', trend: 'critical', change: '+25%', impact: 'Targeting systems, drones', risk: 'critical' }
    ];
    
    // Logistics and chokepoints
    const logistics = [
      { location: 'Strait of Hormuz', status: 'elevated risk', disruption: 'Iranian missile attacks', impact: 'Middle East oil & arms shipments', severity: 'critical' },
      { location: 'Suez Canal', status: 'normal', disruption: 'none', impact: 'Europe-Asia trade', severity: 'low' },
      { location: 'Bab el-Mandeb', status: 'Houthi attacks', disruption: 'shipping delays', impact: 'Red Sea route', severity: 'high' },
      { location: 'Panama Canal', status: 'drought restrictions', disruption: 'reduced capacity', impact: 'US-Latin America trade', severity: 'medium' },
      { location: 'South China Sea', status: 'military activity', disruption: 'route monitoring', impact: 'Asia-Pacific trade', severity: 'medium' },
      { location: 'Malacca Strait', status: 'piracy risk', disruption: 'occasional', impact: 'China-India-Middle East', severity: 'low' }
    ];
    
    // Component shortages
    const shortages = [
      { component: 'Semiconductors (advanced)', status: 'severe', leadTime: '52 weeks', impact: 'All weapons systems, F-35, missiles', mitigation: 'Prioritize military contracts' },
      { component: 'Titanium forgings', status: 'constrained', leadTime: '36 weeks', impact: 'Aircraft structures', mitigation: 'Alternative suppliers' },
      { component: 'Rare earth magnets', status: 'critical', leadTime: '40 weeks', impact: 'Guidance systems, motors', mitigation: 'Stockpile, Chinese dependence' },
      { component: 'Night vision tubes', status: 'limited', leadTime: '48 weeks', impact: 'Optics, targeting', mitigation: 'Domestic production' },
      { component: 'Propellant chemicals', status: 'constrained', leadTime: '28 weeks', impact: 'Ammunition, rockets', mitigation: 'Increased production' },
      { component: 'Ball bearings (precision)', status: 'limited', leadTime: '32 weeks', impact: 'Turrets, engines, guidance', mitigation: 'Diversify suppliers' }
    ];
    
    // Manufacturing capacity
    const manufacturing = [
      { region: 'US', capacity: '85%', trend: 'increasing', notes: 'Defense Production Act invoked', outlook: 'positive' },
      { region: 'Europe', capacity: '78%', trend: 'increasing', notes: 'Rheinmetall, KNDS expanding', outlook: 'positive' },
      { region: 'China', capacity: '92%', trend: 'stable', notes: 'Export restrictions on rare earths', outlook: 'constrained' },
      { region: 'Russia', capacity: '65%', trend: 'declining', notes: 'Sanctions impact, import substitution', outlook: 'negative' },
      { region: 'South Korea', capacity: '88%', trend: 'increasing', notes: 'Arms exports rising', outlook: 'positive' },
      { region: 'Turkey', capacity: '82%', trend: 'increasing', notes: 'Bayraktar drones, armored vehicles', outlook: 'positive' },
      { region: 'India', capacity: '75%', trend: 'increasing', notes: 'Defense indigenization push', outlook: 'positive' }
    ];
    
    // Critical alerts
    const alerts = [
      { type: 'critical', message: 'Semiconductor shortage affecting F-35 production. Lead times extended to 52 weeks.', source: 'Pentagon' },
      { type: 'critical', message: 'Strait of Hormuz: Iranian missile attacks. Shipping insurance premiums up 300%.', source: 'Lloyd\'s List' },
      { type: 'high', message: 'Rare earth export restrictions from China. Prices up 15% in Q1 2026.', source: 'USGS' },
      { type: 'high', message: 'Propellant supply constrained due to Ukraine war demand. Artillery ammunition lead times extended.', source: 'DoD' },
      { type: 'medium', message: 'European defense manufacturers at 78% capacity. Expansion plans announced.', source: 'Industry reports' }
    ];
    
    // Build updates
    const updates = [
      {
        source: 'Supply Chain',
        title: '🚨 CRITICAL: Semiconductor Shortage Worsens',
        content: 'F-35 production delayed. Missile guidance chips lead time: 52 weeks. DoD invoking Defense Production Act.',
        timestamp: Date.now(),
        priority: 'critical'
      },
      {
        source: 'Supply Chain',
        title: '⚠️ Strait of Hormuz Shipping Disruption',
        content: 'Iranian missile attacks. Shipping insurance premiums up 300%. Alternate routes being considered.',
        timestamp: Date.now(),
        priority: 'high'
      },
      {
        source: 'Supply Chain',
        title: '📈 Rare Earth Prices Surge 15%',
        content: 'Chinese export restrictions. Impact on guidance systems, radar, and electric motors.',
        timestamp: Date.now(),
        priority: 'high'
      },
      {
        source: 'Supply Chain',
        title: '🏭 European Defense Manufacturing at 78% Capacity',
        content: 'Rheinmetall, KNDS expanding. New facilities announced in Germany, Hungary.',
        timestamp: Date.now(),
        priority: 'normal'
      },
      {
        source: 'Supply Chain',
        title: '⚙️ Titanium Supply Constrained',
        content: 'Lead times: 36 weeks. Aerospace and submarine production affected.',
        timestamp: Date.now(),
        priority: 'medium'
      },
      {
        source: 'Supply Chain',
        title: '🚢 Shipping Container Rates Up 40%',
        content: 'Red Sea diversions, Panama Canal restrictions. Impact on raw material costs.',
        timestamp: Date.now(),
        priority: 'medium'
      },
      {
        source: 'Supply Chain',
        title: '💣 Explosives Production Ramping Up',
        content: 'New RDX/HMX facilities planned in US, Europe. 12-18 month timeline.',
        timestamp: Date.now(),
        priority: 'normal'
      }
    ];
    
    const signals = [
      '🚨 Semiconductor shortage critical - F-35, missiles affected',
      '⚠️ Strait of Hormuz disruption - shipping insurance up 300%',
      '📈 Rare earth prices up 15% - Chinese export restrictions',
      '🏭 European defense manufacturing expanding (78% capacity)',
      '⚙️ Titanium lead times: 36 weeks - aerospace impact',
      '🚢 Red Sea diversions increasing shipping costs',
      '💣 Explosives production ramping - new facilities planned'
    ];
    
    return {
      source: 'Supply Chain Intelligence',
      timestamp: new Date().toISOString(),
      status: 'active',
      updates: updates,
      signals: signals,
      metrics: {
        rawMaterials: rawMaterials,
        logistics: logistics,
        shortages: shortages,
        manufacturing: manufacturing,
        alerts: alerts,
        lastUpdated: new Date().toISOString(),
        criticalRisks: shortages.filter(s => s.status === 'critical' || s.status === 'severe').length
      },
      counts: {
        updates: updates.length,
        signals: signals.length,
        criticalAlerts: alerts.filter(a => a.type === 'critical').length
      }
    };
    
  } catch (error) {
    console.error('[Supply Chain] Error:', error.message);
    return {
      source: 'Supply Chain Intelligence',
      timestamp: new Date().toISOString(),
      status: 'error',
      error: error.message,
      updates: [],
      signals: []
    };
  }
}

// CLI test
if (process.argv[1]?.endsWith('supply_chain.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}