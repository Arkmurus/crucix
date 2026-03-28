// Arkumurus - Custom data source for Arkumurus.com
// Collects business intelligence, analytics, and location data

import '../utils/env.mjs';

// ============================================
// CONFIGURATION
// ============================================
const ARKUMURUS_API = process.env.ARKUMURUS_API_URL || 'https://api.arkumurus.com/v1';
const ARKUMURUS_API_KEY = process.env.ARKUMURUS_API_KEY;

// Data endpoints (customize based on your API structure)
const ENDPOINTS = {
  locations: `${ARKUMURUS_API}/locations`,
  updates: `${ARKUMURUS_API}/updates`,
  alerts: `${ARKUMURUS_API}/alerts`,
  metrics: `${ARKUMURUS_API}/metrics`,
};

// ============================================
// DATA FETCHING
// ============================================

// Generic fetch with error handling
async function fetchArkumurusData(endpoint, options = {}) {
  if (!ARKUMURUS_API_KEY) {
    return { error: 'No ARKUMURUS_API_KEY configured' };
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  
  try {
    const res = await fetch(endpoint, {
      signal: controller.signal,
      headers: {
        'User-Agent': 'Crucix/1.0',
        'Authorization': `Bearer ${ARKUMURUS_API_KEY}`,
        'Content-Type': 'application/json',
        ...options.headers,
      },
      ...options,
    });
    clearTimeout(timer);
    
    if (!res.ok) {
      return { error: `HTTP ${res.status}: ${res.statusText}` };
    }
    
    return await res.json();
  } catch (e) {
    clearTimeout(timer);
    return { error: e.message };
  }
}

// Fetch all Arkumurus data sources
async function fetchAllArkumurusData() {
  const [locations, updates, alerts, metrics] = await Promise.all([
    fetchArkumurusData(ENDPOINTS.locations),
    fetchArkumurusData(ENDPOINTS.updates),
    fetchArkumurusData(ENDPOINTS.alerts),
    fetchArkumurusData(ENDPOINTS.metrics),
  ]);
  
  return { locations, updates, alerts, metrics };
}

// ============================================
// NEWS API - REAL DATA SOURCE
// ============================================

// Fetch real news about Arkumurus and business intelligence
async function fetchRealNews() {
  const key = process.env.NEWSAPI_KEY;
  
  if (!key) {
    console.log('[Arkumurus] No NewsAPI key found');
    return [];
  }
  
  try {
    const response = await fetch(
      `https://newsapi.org/v2/everything?q=arkumurus+OR+business+intelligence&apiKey=${key}&pageSize=10&language=en&sortBy=publishedAt`
    );
    
    if (!response.ok) {
      console.error('[Arkumurus] NewsAPI error:', response.status);
      return [];
    }
    
    const data = await response.json();
    
    if (data.status !== 'ok') {
      console.error('[Arkumurus] NewsAPI error:', data.message);
      return [];
    }
    
    return data.articles.map(article => ({
      source: 'NewsAPI',
      title: article.title || 'No title',
      content: article.description || article.content || 'No description',
      url: article.url,
      timestamp: new Date(article.publishedAt).getTime(),
      priority: 'normal'
    }));
  } catch (error) {
    console.error('[Arkumurus] NewsAPI fetch error:', error.message);
    return [];
  }
}

// ============================================
// DATA PROCESSING
// ============================================

// Process location data for globe markers
function processLocations(locations) {
  if (!locations || locations.error || !Array.isArray(locations)) {
    return [];
  }
  
  return locations
    .filter(loc => loc.lat && loc.lng)
    .map(loc => ({
      lat: parseFloat(loc.lat),
      lng: parseFloat(loc.lng),
      type: loc.type || 'info',
      title: loc.name || loc.title || 'Arkumurus Location',
      description: loc.description || loc.details || '',
      value: loc.value || null,
      status: loc.status || 'active',
      lastUpdated: loc.timestamp || new Date().toISOString(),
    }));
}

// Process updates for OSINT feed
function processUpdates(updates) {
  if (!updates || updates.error || !Array.isArray(updates)) {
    return [];
  }
  
  return updates.map(update => ({
    source: 'Arkumurus',
    title: update.title || 'Update',
    content: update.content || update.summary || update.body,
    url: update.url || update.link,
    timestamp: new Date(update.date || update.timestamp || Date.now()).getTime(),
    priority: update.priority || 'normal',
    category: update.category || 'general',
  }));
}

// Process alerts for ticker
function processAlerts(alerts) {
  if (!alerts || alerts.error || !Array.isArray(alerts)) {
    return [];
  }
  
  return alerts.map(alert => ({
    text: alert.message || alert.text || alert.title,
    priority: alert.priority || 'normal',
    timestamp: new Date(alert.date || alert.timestamp || Date.now()).getTime(),
  }));
}

// Process metrics for dashboard
function processMetrics(metrics) {
  if (!metrics || metrics.error) {
    return {
      totalLocations: 0,
      activeAlerts: 0,
      recentUpdates: 0,
      status: 'unavailable',
    };
  }
  
  return {
    totalLocations: metrics.totalLocations || 0,
    activeAlerts: metrics.activeAlerts || 0,
    recentUpdates: metrics.recentUpdates || 0,
    lastSync: metrics.timestamp || new Date().toISOString(),
    status: metrics.status || 'active',
    ...metrics,
  };
}

// ============================================
// MAIN BRIEFING FUNCTION
// ============================================

export async function briefing() {
  // Check if Arkumurus is enabled
  const enabled = process.env.ARKUMURUS_ENABLED === 'true';
  if (!enabled) {
    return {
      source: 'Arkumurus',
      timestamp: new Date().toISOString(),
      status: 'disabled',
      message: 'Set ARKUMURUS_ENABLED=true and ARKUMURUS_API_KEY to enable this source',
    };
  }
  
  try {
    // Fetch data from Arkumurus API
    const { locations, updates, alerts, metrics } = await fetchAllArkumurusData();
    
    // Fetch real news from NewsAPI
    const newsItems = await fetchRealNews();
    
    // Process each data type
    const processedLocations = processLocations(locations);
    const processedArkumurusUpdates = processUpdates(updates);
    const processedAlerts = processAlerts(alerts);
    const processedMetrics = processMetrics(metrics);
    
    // Combine Arkumurus updates with news items
    const allUpdates = [...processedArkumurusUpdates, ...newsItems];
    
    // Generate signals/alerts for significant events
    const signals = [];
    
    if (processedAlerts.length > 0) {
      const highPriorityAlerts = processedAlerts.filter(a => a.priority === 'high');
      if (highPriorityAlerts.length > 0) {
        signals.push(`${highPriorityAlerts.length} high-priority Arkumurus alerts`);
      }
      signals.push(`${processedAlerts.length} active alerts from Arkumurus`);
    }
    
    if (newsItems.length > 0) {
      signals.push(`${newsItems.length} news articles about business intelligence`);
    }
    
    if (allUpdates.length > 10) {
      signals.push(`${allUpdates.length} total updates from Arkumurus and news`);
    }
    
    if (processedMetrics.status === 'degraded') {
      signals.push('Arkumurus service status: DEGRADED');
    }
    
    // Return structured data
    return {
      source: 'Arkumurus',
      timestamp: new Date().toISOString(),
      status: 'active',
      
      // For globe markers
      locations: processedLocations,
      
      // For OSINT feed (combined)
      updates: allUpdates,
      
      // For ticker
      alerts: processedAlerts,
      
      // Metrics for dashboard
      metrics: processedMetrics,
      
      // Signals for notifications
      signals,
      
      // Raw counts
      counts: {
        locations: processedLocations.length,
        updates: allUpdates.length,
        alerts: processedAlerts.length,
        news: newsItems.length
      },
    };
    
  } catch (error) {
    console.error('[Arkumurus] Error:', error);
    return {
      source: 'Arkumurus',
      timestamp: new Date().toISOString(),
      status: 'error',
      error: error.message,
    };
  }
}

// ============================================
// CLI EXECUTION (for testing)
// ============================================
if (process.argv[1]?.endsWith('arkumurus.mjs')) {
  const data = await briefing();
  console.log(JSON.stringify(data, null, 2));
}