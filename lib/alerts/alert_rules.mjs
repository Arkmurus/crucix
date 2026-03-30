// Alert Rules Configuration
// Defines what triggers alerts and how they are formatted

export const ALERT_RULES = {
  // Conflict Escalation Rules
  conflict: {
    enabled: true,
    thresholds: {
      fatalities: 10,      // Alert if >10 fatalities in a single event
      eventCount: 5,       // Alert if >5 conflict events in a region
      newFront: true       // Alert if new conflict zone appears
    },
    regions: [
      'Ukraine', 'Gaza', 'South China Sea', 'Taiwan Strait',
      'Red Sea', 'Bab el-Mandeb', 'Kuwait', 'Iran', 'Israel',
      'Syria', 'Yemen', 'Myanmar', 'Sudan'
    ],
    keywords: [
      'attack', 'strike', 'bomb', 'missile', 'drone', 'explosion',
      'escalation', 'war', 'conflict', 'casualties', 'fatalities'
    ]
  },

  // Defense Contracts Rules
  contracts: {
    enabled: true,
    thresholds: {
      minValue: 100000000,  // Alert on contracts > $100M
      newProgram: true,      // Alert on new weapons programs
      criticalSuppliers: ['Lockheed Martin', 'RTX', 'BAE Systems', 'Northrop Grumman', 'General Dynamics', 'Boeing']
    }
  },

  // Sanctions Rules
  sanctions: {
    enabled: true,
    categories: ['military', 'defense', 'weapons', 'dual-use'],
    countries: ['Russia', 'Iran', 'North Korea', 'China', 'Venezuela'],
    newSanctions: true
  },

  // OSINT Rules
  osint: {
    enabled: true,
    priorityChannels: [
      'intel_center', 'conflict_news', 'defense_alerts',
      'military_news', 'geopolitical_risk'
    ],
    keywords: [
      'URGENT', 'BREAKING', 'FLASH', 'ALERT', 'IMMEDIATE',
      'attack', 'strike', 'missile', 'drone', 'explosion'
    ]
  },

  // Cooldown to prevent spam
  cooldown: {
    conflict: 15 * 60 * 1000,     // 15 minutes
    contract: 30 * 60 * 1000,     // 30 minutes
    sanctions: 60 * 60 * 1000,    // 1 hour
    osint: 10 * 60 * 1000         // 10 minutes
  }
};