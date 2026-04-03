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

  async get(path) {
    const r = await fetch(this.BASE + path, { headers: this.headers() });
    if (r.status === 401) { Auth.logout(); return null; }
    return r.json();
  },

  async post(path, body) {
    const r = await fetch(this.BASE + path, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify(body)
    });
    return { ok: r.ok, status: r.status, data: await r.json() };
  },

  async put(path, body) {
    const r = await fetch(this.BASE + path, {
      method: 'PUT',
      headers: this.headers(),
      body: JSON.stringify(body)
    });
    return { ok: r.ok, data: await r.json() };
  },

  async del(path) {
    const r = await fetch(this.BASE + path, {
      method: 'DELETE',
      headers: this.headers()
    });
    return { ok: r.ok, data: await r.json() };
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
    t.innerHTML = `<span>${msg}</span><button onclick="this.parentElement.remove()">✕</button>`;
    container.appendChild(t);
    setTimeout(() => t.remove(), duration);
  },
  _createContainer() {
    const c = document.createElement('div');
    c.id = 'toast-container';
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
