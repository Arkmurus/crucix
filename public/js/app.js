/**
 * CRUCIX — Shared App Utilities
 * Auth helpers, API wrappers, JWT management
 */

const API = {
  BASE: '',

  token() {
    return localStorage.getItem('crucix_token');
  },

  headers() {
    const t = this.token();
    return {
      'Content-Type': 'application/json',
      ...(t ? { 'Authorization': `Bearer ${t}` } : {})
    };
  },

  // R-F464 (2026-05-14): error-payload detection. Pre-R-F464 GET would
  // return `{error: "ARIA service offline"}` (from seenode's
  // _brainFallback or fly proxy timeout) as if it were data. Callers
  // doing `if (!data) return` proceeded with the error object as the
  // happy-path payload — silent fallback rendered fake values.
  // We now return null when the parsed body is an envelope-shaped
  // error response, matching the honesty discipline that fetchJson()
  // in aria-brain.html already enforced locally. The result: every
  // page using API.get gets the same loud-fail treatment on proxy
  // breakage instead of silently rendering placeholder data.
  _isErrorEnvelope(data) {
    if (!data || typeof data !== 'object' || Array.isArray(data)) return false;
    const keys = Object.keys(data);
    if (!keys.includes('error') || !data.error) return false;
    // Only treat as failure when the response is dominantly error-shape.
    // Real APIs may return {results: [...], error: null} on partial
    // success — don't break those. The seenode/fly error envelopes have
    // at most 3 fields (error, fly_status, fly_error, path).
    const errEnvelope = new Set(['error', 'fly_status', 'fly_error', 'path', 'detail']);
    return keys.every(k => errEnvelope.has(k));
  },

  async get(path) {
    try {
      const r = await fetch(this.BASE + path, { headers: this.headers() });
      if (r.status === 401) { Auth.logout(); return null; }
      const data = await r.json();
      // Network OK but body is an error envelope — surface as null.
      if (this._isErrorEnvelope(data)) {
        console.warn('API.get error envelope:', path, data.error);
        return null;
      }
      return data;
    } catch (e) {
      console.error('API.get error:', path, e);
      return null;
    }
  },

  async post(path, body) {
    try {
      const r = await fetch(this.BASE + path, {
        method: 'POST',
        headers: this.headers(),
        body: JSON.stringify(body)
      });
      // Do NOT auto-logout on 401 for POST — login endpoint legitimately returns 401 for wrong credentials
      let data = {};
      try { data = await r.json(); } catch { data = { error: 'Server returned an unexpected response.' }; }
      return { ok: r.ok, status: r.status, data };
    } catch (e) {
      console.error('API.post error:', path, e);
      return { ok: false, status: 0, data: { error: 'Network error — please check your connection and try again.' } };
    }
  },

  async put(path, body) {
    try {
      const r = await fetch(this.BASE + path, {
        method: 'PUT',
        headers: this.headers(),
        body: JSON.stringify(body)
      });
      if (r.status === 401) { Auth.logout(); return { ok: false, data: {} }; }
      let data = {};
      try { data = await r.json(); } catch { data = {}; }
      return { ok: r.ok, data };
    } catch (e) {
      console.error('API.put error:', path, e);
      return { ok: false, data: { error: 'Network error.' } };
    }
  },

  async del(path) {
    try {
      const r = await fetch(this.BASE + path, {
        method: 'DELETE',
        headers: this.headers()
      });
      if (r.status === 401) { Auth.logout(); return { ok: false, data: {} }; }
      let data = {};
      try { data = await r.json(); } catch { data = {}; }
      return { ok: r.ok, data };
    } catch (e) {
      console.error('API.del error:', path, e);
      return { ok: false, data: { error: 'Network error.' } };
    }
  }
};

const Auth = {
  user: null,

  isLoggedIn() {
    return !!localStorage.getItem('crucix_token');
  },

  logout() {
    localStorage.removeItem('crucix_token');
    localStorage.removeItem('crucix_user');
    window.location.href = '/signin.html';
  },

  async me() {
    if (this.user) return this.user;
    const cached = localStorage.getItem('crucix_user');
    if (cached) { this.user = JSON.parse(cached); return this.user; }
    const data = await API.get('/api/auth/me');
    if (data) {
      this.user = data;
      localStorage.setItem('crucix_user', JSON.stringify(data));
    }
    return data;
  },

  requireAuth() {
    if (!this.isLoggedIn()) {
      window.location.href = '/signin.html';
      return false;
    }
    return true;
  },

  requireAdmin() {
    if (!this.isLoggedIn()) { window.location.href = '/signin.html'; return false; }
    const u = JSON.parse(localStorage.getItem('crucix_user') || '{}');
    if (u.role !== 'admin') { window.location.href = '/dashboard.html'; return false; }
    return true;
  },

  initials(user) {
    if (!user) return '?';
    const n = user.fullName || user.username || '';
    return n.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2) || '?';
  }
};

// ── Sidebar / Nav helpers ─────────────────────────────────────────────────────
const Nav = {
  init(activePage) {
    if (!Auth.requireAuth()) return;
    Auth.me().then(user => {
      if (!user) return;
      const el = document.getElementById('nav-user-name');
      if (el) el.textContent = user.fullName || user.username;
      const av = document.getElementById('nav-avatar');
      if (av) av.textContent = Auth.initials(user);
      const adminLinks = document.querySelectorAll('[data-admin-only]');
      adminLinks.forEach(l => { l.style.display = user.role === 'admin' ? '' : 'none'; });
    });
    // Mark active link
    if (activePage) {
      const link = document.querySelector(`[data-page="${activePage}"]`);
      if (link) link.classList.add('active');
    }
    // Sidebar toggle
    const toggle = document.getElementById('sidebar-toggle');
    const sidebar = document.getElementById('app-sidebar');
    const main = document.getElementById('app-main');
    if (toggle && sidebar) {
      toggle.addEventListener('click', () => {
        sidebar.classList.toggle('collapsed');
        if (main) main.classList.toggle('expanded');
      });
    }
    // Logout
    const logoutBtn = document.getElementById('nav-logout');
    if (logoutBtn) logoutBtn.addEventListener('click', e => { e.preventDefault(); Auth.logout(); });
  }
};

// ── Toast notifications ───────────────────────────────────────────────────────
const Toast = {
  show(msg, type = 'info', duration = 4000) {
    const container = document.getElementById('toast-container') || this._createContainer();
    const t = document.createElement('div');
    t.className = `crucix-toast toast-${type}`;
    // R-F113 (2026-05-09): screen-reader announce. role=alert for danger
    // (interrupts), role=status for info/success/warn (polite). Each toast
    // gets aria-live so it's spoken when injected.
    t.setAttribute('role', type === 'danger' || type === 'error' ? 'alert' : 'status');
    t.setAttribute('aria-live', type === 'danger' || type === 'error' ? 'assertive' : 'polite');
    t.setAttribute('aria-atomic', 'true');
    t.innerHTML = `<span>${msg}</span><button aria-label="Dismiss notification" onclick="this.parentElement.remove()">✕</button>`;
    container.appendChild(t);
    setTimeout(() => t.remove(), duration);
  },
  _createContainer() {
    const c = document.createElement('div');
    c.id = 'toast-container';
    // Container itself is a live region so toasts announced even if the
    // individual toasts re-use existing dom (rare; defence-in-depth).
    c.setAttribute('aria-live', 'polite');
    c.setAttribute('aria-atomic', 'false');
    document.body.appendChild(c);
    return c;
  }
};

// ── Utilities ─────────────────────────────────────────────────────────────────
function fmtDate(d) {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

function fmtDateTime(d) {
  if (!d) return '—';
  return new Date(d).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function truncate(str, n = 160) {
  if (!str) return '';
  return str.length > n ? str.slice(0, n) + '…' : str;
}

function escHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function severityColor(s) {
  switch ((s || '').toLowerCase()) {
    case 'critical': case 'flash': return '#ef4444';
    case 'high': return '#FF7A41';
    case 'medium': return '#0066FF';
    default: return '#22c55e';
  }
}
