/**
 * ARIA — Shared App Utilities
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

  // R-F3311 — a 401 is only a session EXPIRY if there was a session. When no
  // token was ever sent, 401 just means "anonymous", and treating it as expiry
  // makes Auth.logout() redirect to /signin.html.
  //
  // That is not theoretical. model-card.html is public by design and says so in
  // a comment, but it calls Sidebar.init() -> Auth.me() -> API.get('/api/auth/me'),
  // which 401s for a logged-out visitor and bounced them to the sign-in page.
  // The landing page's "Read the model card" button therefore led to a login
  // wall for every anonymous prospect: the exact reader the model card is FOR.
  // The page opting out of Auth.requireAuth() did nothing, because the shell it
  // loads reached auth through a side door.
  //
  // Kicking a user who was never logged in is wrong on every page, so the guard
  // lives here rather than in a per-page workaround. Callers still get null, and
  // a real expired token still logs out exactly as before.
  _handle401() {
    if (this.token()) { Auth.logout(); return; }
    // anonymous: leave the visitor where they are
  },

  // R-F3332 — an account holding an ISSUED temporary password is refused on
  // every API except /me, logout and the password change itself (the server-side
  // gate in requireAuth, via lib/auth/passwordRotation.mjs). THAT is the
  // enforcement; this is the UX that follows it, so the user lands somewhere
  // they can act instead of on a page of failed panels.
  //
  // The pathname guard is not paranoia: set-password.html calls the allowlisted
  // endpoints only, but a redirect that can target the page it is already on is
  // one server-side mistake away from a reload loop.
  _maybeRotate(status, data) {
    if (status !== 403 || !data || data.code !== 'password_change_required') return false;
    if (/\/set-password\.html$/.test(window.location.pathname)) return false;
    window.location.href = '/set-password.html';
    return true;
  },

  async get(path) {
    try {
      const r = await fetch(this.BASE + path, { headers: this.headers() });
      if (r.status === 401) { this._handle401(); return null; }
      const data = await r.json();
      if (this._maybeRotate(r.status, data)) return null;   // R-F3332
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

  // R-F2233 (2026-07-01): status-aware probe for background dashboards.
  // Unlike get(), probe() (a) does NOT auto-logout on 401 — a polling
  // dashboard must not evict the operator on a single auth flap, because
  // logout wipes the token and cascades every SUBSEQUENT fetch to 401,
  // painting the whole page "unreachable" (the phantom 4-vs-6 count); and
  // (b) returns the HTTP status so the caller can tell an auth-gated
  // 401/403 ("sign in") apart from a genuine reachability failure
  // (timeout / network / 5xx / error-envelope). A bounded AbortController
  // timeout makes a stuck endpoint resolve as a deterministic 'timeout'
  // instead of hanging the refresh forever. Root-cause latency is tracked
  // separately (R-F2234); this method only makes the reporting honest.
  async probe(path, { timeoutMs = 20000 } = {}) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const r = await fetch(this.BASE + path, { headers: this.headers(), signal: ctrl.signal });
      let data = null;
      try { data = await r.json(); } catch { data = null; }
      this._maybeRotate(r.status, data);   // R-F3332
      return { status: r.status, ok: r.ok, data, isErrorEnvelope: this._isErrorEnvelope(data) };
    } catch (e) {
      const timedOut = !!(e && e.name === 'AbortError');
      return { status: 0, ok: false, data: null, timedOut, error: e && e.message };
    } finally {
      clearTimeout(timer);
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
      this._maybeRotate(r.status, data);   // R-F3332
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
      if (r.status === 401) { this._handle401(); return { ok: false, data: {} }; }
      let data = {};
      try { data = await r.json(); } catch { data = {}; }
      this._maybeRotate(r.status, data);   // R-F3332
      return { ok: r.ok, data };
    } catch (e) {
      console.error('API.put error:', path, e);
      return { ok: false, data: { error: 'Network error.' } };
    }
  },

  // R-F3531 — real PATCH. The lead workflow applies ONE named action per call
  // (assign owner, add note, set stage, attest); tunnelling that through PUT
  // would misdescribe a partial update as a whole-record replacement, and the
  // route it talks to is registered as app.patch.
  async patch(path, body) {
    try {
      const r = await fetch(this.BASE + path, {
        method: 'PATCH',
        headers: this.headers(),
        body: JSON.stringify(body)
      });
      if (r.status === 401) { this._handle401(); return { ok: false, status: 401, data: {} }; }
      let data = {};
      try { data = await r.json(); } catch { data = {}; }
      this._maybeRotate(r.status, data);   // R-F3332
      return { ok: r.ok, status: r.status, data };
    } catch (e) {
      console.error('API.patch error:', path, e);
      return { ok: false, status: 0, data: { error: 'Network error.' } };
    }
  },

  async del(path) {
    try {
      const r = await fetch(this.BASE + path, {
        method: 'DELETE',
        headers: this.headers()
      });
      if (r.status === 401) { this._handle401(); return { ok: false, data: {} }; }
      let data = {};
      try { data = await r.json(); } catch { data = {}; }
      this._maybeRotate(r.status, data);   // R-F3332
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

  async logout() {
    // R-F2774 — also clear the httpOnly auth cookie server-side (JS can't touch an
    // httpOnly cookie), so a logged-out session can't still load operator pages.
    try { await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' }); } catch (e) {}
    localStorage.removeItem('crucix_token');
    localStorage.removeItem('crucix_user');
    window.location.href = '/signin.html';
  },

  async me() {
    if (this.user) return this.user;
    // R-F3311 — no token means no user. Asking the server anyway produced a 401
    // on every public page load, which is what tripped the auto-logout above.
    // Callers already handle a null user (the shell renders anonymous chrome).
    if (!this.isLoggedIn()) return null;
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
      const av = document.getElementById('nav-avatar-mark') || document.getElementById('nav-avatar');
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
    // R-F3852 — this ONE line carried two defects, and it is the toast every page
    // in the app uses.
    //
    // 1. `msg` went RAW into innerHTML. Callers routinely pass server text —
    //    account.html sends `'Checkout failed: ' + (data.error || resp.status)`,
    //    sidebar.js sends `err.message` — so a hostile or reflected error string
    //    was script execution on whichever page happened to show the toast. It is
    //    escaped now; nothing here was ever meant to render markup.
    // 2. The dismiss button used an inline `onclick`, which CSP
    //    `script-src-attr 'none'` (R-F1919) blocks — so the ✕ did nothing, on
    //    every page, for every toast. A delegated listener restores it.
    const body = document.createElement('span');
    body.textContent = String(msg == null ? '' : msg);
    const close = document.createElement('button');
    close.setAttribute('aria-label', 'Dismiss notification');
    close.textContent = '✕';
    close.addEventListener('click', () => t.remove());
    t.append(body, close);
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

// ── Modal: professional confirm + result dialogs (reuses the sc-mod system) ────
// R-F2293 — replaces window.confirm()/alert() with in-app modals matching the
// design system. Modal.confirm() returns Promise<boolean>; Modal.info() shows a
// read-only result panel (VLS proof/verify, case file). Self-contained: builds
// its own overlay so any page loading app.js gets it with no per-page markup.
// Escape-to-dismiss, focus trap, and focus-restore included.
const Modal = {
  _ov: null, _prevFocus: null, _onKey: null,
  _mount() {
    if (this._ov) return this._ov;
    const ov = document.createElement('div');
    ov.className = 'sc-mod-overlay';
    document.body.appendChild(ov);
    this._ov = ov;
    return ov;
  },
  _focusable() {
    return Array.from(this._ov.querySelectorAll(
      'button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    )).filter(el => !el.disabled && el.offsetParent !== null);
  },
  _open(inner, labelledby) {
    const ov = this._mount();
    this._prevFocus = document.activeElement;
    ov.innerHTML = inner;
    const mod = ov.querySelector('.sc-mod');
    if (mod) {
      mod.setAttribute('role', 'dialog');
      mod.setAttribute('aria-modal', 'true');
      if (labelledby) mod.setAttribute('aria-labelledby', labelledby);
    }
    ov.classList.add('open');
    const auto = ov.querySelector('[data-autofocus]') || this._focusable()[0];
    if (auto) auto.focus();
    return ov;
  },
  _dismiss() {
    if (!this._ov) return;
    this._ov.classList.remove('open');
    this._ov.innerHTML = '';
    if (this._onKey) document.removeEventListener('keydown', this._onKey);
    this._onKey = null;
    if (this._prevFocus && this._prevFocus.focus) { try { this._prevFocus.focus(); } catch (e) {} }
  },
  _trap(e) {
    const f = this._focusable();
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  },
  confirm({ title = 'Are you sure?', message = '', okLabel = 'Confirm', cancelLabel = 'Cancel',
            danger = true, icon = 'bi-exclamation-triangle-fill' } = {}) {
    return new Promise(resolve => {
      const okColor = danger ? 'var(--sc-red)' : 'var(--sc-prime)';
      const iconBg = danger ? 'rgba(220,38,38,0.12)' : 'rgba(124,58,237,0.12)';
      const iconColor = danger ? 'var(--sc-red)' : 'var(--sc-prime)';
      const ov = this._open(
        '<div class="sc-mod" style="max-width:460px">'
        + '<div class="sc-mod-head">'
        + '<div class="sc-mod-ico" style="background:' + iconBg + ';color:' + iconColor + '"><i class="bi ' + icon + '"></i></div>'
        + '<div><h3 id="sc-cf-title">' + escHtml(title) + '</h3><p>' + escHtml(message) + '</p></div>'
        + '<button class="sc-mod-x" data-cf="cancel" type="button" aria-label="Close">&times;</button>'
        + '</div>'
        + '<div class="sc-mod-foot">'
        + '<button class="sc-btn sc-btn-outline" data-cf="cancel" type="button">' + escHtml(cancelLabel) + '</button>'
        + '<button class="sc-btn" data-cf="ok" data-autofocus type="button" style="background:' + okColor + ';color:#fff">' + escHtml(okLabel) + '</button>'
        + '</div></div>', 'sc-cf-title');
      const done = (val) => { this._dismiss(); resolve(val); };
      ov.querySelectorAll('[data-cf="cancel"]').forEach(el => el.onclick = () => done(false));
      ov.querySelector('[data-cf="ok"]').onclick = () => done(true);
      ov.onclick = (e) => { if (e.target === ov) done(false); };
      this._onKey = (e) => { if (e.key === 'Escape') done(false); else if (e.key === 'Tab') this._trap(e); };
      document.addEventListener('keydown', this._onKey);
    });
  },
  // R-F3169 — in-app FORM dialog. Modal.confirm/info (R-F2293) removed
  // window.confirm/alert; this removes the remaining window.prompt, which is
  // the worst of the three: it takes one unlabelled string, cannot validate,
  // cannot show help text, and chains into a queue of popups for a
  // multi-field task. The vetting decision flow was doing exactly that.
  //
  // fields: [{ name, label, type, required, value, options, help, placeholder,
  //            rows, autofocus }]
  //   type: text | date | number | select | textarea | static
  //   'static' renders read-only context (e.g. the engine's current verdict)
  //   so the user decides WITH the facts in front of them, not from memory.
  //
  // validate(values) -> { field: 'message' } | null   (async allowed)
  //   Errors render INLINE against the field. Nothing is ever thrown at the
  //   user as a popup, and the dialog stays open with their input intact.
  //
  // Resolves with the values object, or null if dismissed.
  form({ title = '', message = '', fields = [], okLabel = 'Save',
         cancelLabel = 'Cancel', icon = 'bi-pencil-square', validate = null } = {}) {
    return new Promise(resolve => {
      const fieldHtml = (f) => {
        const id = 'scf-' + f.name;
        const req = f.required ? ' <span style="color:var(--sc-red)">*</span>' : '';
        if (f.type === 'static') {
          return '<div class="sc-field"><label>' + escHtml(f.label) + '</label>'
            + '<div class="sc-form-static">' + (f.html || escHtml(f.value || '')) + '</div></div>';
        }
        let input;
        if (f.type === 'select') {
          input = '<select id="' + id + '" name="' + escHtml(f.name) + '"'
            + (f.autofocus ? ' data-autofocus' : '') + '>'
            + (f.options || []).map(o =>
                '<option value="' + escHtml(o.value) + '"'
                + (String(o.value) === String(f.value || '') ? ' selected' : '') + '>'
                + escHtml(o.label) + '</option>').join('')
            + '</select>';
        } else if (f.type === 'textarea') {
          input = '<textarea id="' + id + '" name="' + escHtml(f.name) + '" rows="'
            + (f.rows || 3) + '" placeholder="' + escHtml(f.placeholder || '') + '"'
            + (f.autofocus ? ' data-autofocus' : '') + '>' + escHtml(f.value || '') + '</textarea>';
        } else {
          input = '<input id="' + id + '" name="' + escHtml(f.name) + '" type="'
            + escHtml(f.type || 'text') + '" value="' + escHtml(f.value || '')
            + '" placeholder="' + escHtml(f.placeholder || '') + '"'
            + (f.autofocus ? ' data-autofocus' : '') + '>';
        }
        return '<div class="sc-field" data-field="' + escHtml(f.name) + '">'
          + '<label for="' + id + '">' + escHtml(f.label) + req + '</label>'
          + input
          + (f.help ? '<div class="sc-form-help">' + escHtml(f.help) + '</div>' : '')
          + '<div class="sc-form-err" data-err-for="' + escHtml(f.name) + '"></div>'
          + '</div>';
      };

      const ov = this._open(
        '<div class="sc-mod" style="max-width:560px">'
        + '<div class="sc-mod-head">'
        + '<div class="sc-mod-ico" style="background:rgba(124,58,237,0.12);color:var(--sc-prime)"><i class="bi ' + icon + '"></i></div>'
        + '<div><h3 id="sc-fm-title">' + escHtml(title) + '</h3>'
        + (message ? '<p>' + escHtml(message) + '</p>' : '') + '</div>'
        + '<button class="sc-mod-x" data-fm="cancel" type="button" aria-label="Close">&times;</button>'
        + '</div>'
        + '<div class="sc-mod-body"><form id="sc-mod-form" novalidate>'
        + fields.map(fieldHtml).join('') + '</form></div>'
        + '<div class="sc-mod-foot">'
        + '<button class="sc-btn sc-btn-outline" data-fm="cancel" type="button">' + escHtml(cancelLabel) + '</button>'
        + '<button class="sc-btn sc-btn-primary" data-fm="ok" type="button">' + escHtml(okLabel) + '</button>'
        + '</div></div>', 'sc-fm-title');

      const done = (val) => { this._dismiss(); resolve(val); };
      const readValues = () => {
        const out = {};
        for (const f of fields) {
          if (f.type === 'static') continue;
          const el = ov.querySelector('#scf-' + CSS.escape(f.name));
          out[f.name] = el ? String(el.value || '').trim() : '';
        }
        return out;
      };
      const showErrors = (errs) => {
        ov.querySelectorAll('.sc-form-err').forEach(e => { e.textContent = ''; });
        ov.querySelectorAll('.sc-field').forEach(e => e.classList.remove('has-error'));
        let firstBad = null;
        for (const [name, msg] of Object.entries(errs || {})) {
          const slot = ov.querySelector('[data-err-for="' + CSS.escape(name) + '"]');
          if (slot) slot.textContent = msg;
          const wrap = ov.querySelector('[data-field="' + CSS.escape(name) + '"]');
          if (wrap) wrap.classList.add('has-error');
          if (!firstBad) firstBad = ov.querySelector('#scf-' + CSS.escape(name));
        }
        if (firstBad) firstBad.focus();
      };

      const submit = async () => {
        const values = readValues();
        const errs = {};
        for (const f of fields) {
          if (f.type !== 'static' && f.required && !values[f.name]) {
            errs[f.name] = 'Required';
          }
        }
        if (Object.keys(errs).length) return showErrors(errs);
        if (validate) {
          const custom = await validate(values);
          if (custom && Object.keys(custom).length) return showErrors(custom);
        }
        done(values);
      };

      ov.querySelectorAll('[data-fm="cancel"]').forEach(el => el.onclick = () => done(null));
      ov.querySelector('[data-fm="ok"]').onclick = submit;
      const formEl = ov.querySelector('#sc-mod-form');
      if (formEl) formEl.onsubmit = (e) => { e.preventDefault(); submit(); };
      ov.onclick = (e) => { if (e.target === ov) done(null); };
      this._onKey = (e) => {
        if (e.key === 'Escape') done(null);
        else if (e.key === 'Tab') this._trap(e);
        else if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA') {
          e.preventDefault(); submit();
        }
      };
      document.addEventListener('keydown', this._onKey);
    });
  },
  info({ title = '', message = '', bodyHtml = '', icon = 'bi-info-circle-fill', tone = 'info' } = {}) {
    const tones = {
      info: ['rgba(37,99,235,0.12)', '#1d4ed8'], success: ['rgba(22,163,74,0.12)', '#15803d'],
      danger: ['rgba(220,38,38,0.12)', 'var(--sc-red)'], vls: ['rgba(124,58,237,0.12)', 'var(--sc-prime)'],
    };
    const [bg, col] = tones[tone] || tones.info;
    const ov = this._open(
      '<div class="sc-mod" style="max-width:520px">'
      + '<div class="sc-mod-head">'
      + '<div class="sc-mod-ico" style="background:' + bg + ';color:' + col + '"><i class="bi ' + icon + '"></i></div>'
      + '<div><h3 id="sc-if-title">' + escHtml(title) + '</h3>' + (message ? '<p>' + escHtml(message) + '</p>' : '') + '</div>'
      + '<button class="sc-mod-x" data-if="close" type="button" aria-label="Close">&times;</button>'
      + '</div>'
      + '<div class="sc-mod-body">' + bodyHtml + '</div>'
      + '<div class="sc-mod-foot"><button class="sc-btn sc-btn-outline" data-if="close" data-autofocus type="button">Close</button></div>'
      + '</div>', 'sc-if-title');
    const done = () => this._dismiss();
    ov.querySelectorAll('[data-if="close"]').forEach(el => el.onclick = done);
    ov.onclick = (e) => { if (e.target === ov) done(); };
    this._onKey = (e) => { if (e.key === 'Escape') done(); else if (e.key === 'Tab') this._trap(e); };
    document.addEventListener('keydown', this._onKey);
  },
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

// R-F2607 — scheme allowlist for data-derived href/URL sinks. Blocks
// javascript:/data:/vbscript: URLs by falling back to '#'. Global (app.js
// loads in page scope) so any page including js/app.js can reuse this copy.
function safeHref(u) {
  const s = String(u == null ? '' : u).trim();
  return /^(https?:|mailto:)/i.test(s) ? s : '#';
}

function severityColor(s) {
  switch ((s || '').toLowerCase()) {
    case 'critical': case 'flash': return '#ef4444';
    case 'high': return '#FF7A41';
    case 'medium': return '#0066FF';
    default: return '#22c55e';
  }
}

// R-F2042 — shared authed() helper. A thin fetch wrapper that attaches the JWT
// (via API.headers()) and returns the RAW Response (callers do res.ok / res.json()).
// Pages were each expected to define this locally; dashboard.html and
// dd-reports.html did, but watchlist.html and vls-chain.html did NOT — so their
// authed(...) calls threw "authed is not defined" and the pages could not load
// data (the live watchlist had 4 entries but the page showed none). Defining it
// once here fixes those pages and every future page that uses it. Supports an
// opts arg (method/body/headers) so POST/DELETE callers work too; opts.headers
// merge over the auth headers.
function authed(path, opts = {}) {
  return fetch(API.BASE + path, {
    ...opts,
    headers: { ...API.headers(), ...(opts.headers || {}) },
  });
}

/* ─────────────────────────────────────────────────────────────────────────
 * R-F3285 — ONE source for how a risk classification is shown.
 *
 * The defect this closes, exactly: dd-reports.html built the pill's CSS class
 * with `sev.toLowerCase().replace(/[^a-z_]/g, '')`. That strips a HYPHEN
 * rather than mapping it to an underscore, so "AMBER-LIGHT" became the class
 * `amberlight` while the stylesheet defined `.dd-pill.amber_light`. The rule
 * never matched. GREEN and RED are single words and matched fine, which is why
 * only amber shipped with no colour around it — and why nothing looked broken
 * enough to notice for months.
 *
 * The label was the second half of the same problem: every surface printed the
 * STORED value verbatim, so an operator read "AMBER-LIGHT" in one place,
 * "AMBERLIGHT" on a PDF and "AMBER" on a finding pill, for one verdict.
 *
 * The stored values do NOT change. AMBER-LIGHT and AMBER-DARK are what the
 * engine computes and what every archived report holds; rewriting them would
 * change the meaning of records already issued to customers. This is a
 * DISPLAY normaliser: the file keeps its precision, the human sees one word.
 * ───────────────────────────────────────────────────────────────────────── */

function riskKey(raw) {
  // Hyphen, space and underscore all mean the same separator here. Normalising
  // BEFORE the strip is the whole fix.
  return String(raw || '').trim().toUpperCase().replace(/[\s-]+/g, '_');
}

function riskTone(raw) {
  const k = riskKey(raw);
  if (!k) return 'unknown';
  if (k.includes('HARD_STOP') || (k.includes('RED') && !k.includes('AMBER'))) return 'red';
  if (k.includes('AMBER') || k === 'HIGH' || k === 'ORANGE') return 'amber';
  if (k.includes('GREEN') || k.includes('CLEAR') || k.includes('PROCEED') || k === 'LOW') return 'green';
  if (k === 'MEDIUM') return 'medium';
  if (k === 'PENDING' || k === 'UNKNOWN') return 'unknown';
  return 'unknown';
}

function riskLabel(raw) {
  const k = riskKey(raw);
  if (!k) return '';
  // AMBER-LIGHT / AMBER-DARK / AMBERLIGHT all read as AMBER to a human. The
  // light/dark gradation is an internal ranking used to order verdicts; it has
  // never meant anything to the person reading the report, and showing it
  // invited exactly the "is AMBER-LIGHT better or worse than AMBER?" question
  // that a traffic light exists to prevent.
  if (k.includes('AMBER')) return 'AMBER';
  if (k.includes('HARD_STOP')) return 'HARD STOP';
  return k.replace(/_/g, ' ');
}

// Attached to `window`, not exported. package.json declares "type": "module",
// so a CommonJS `module.exports` here is dead code that READS like an export —
// `require()` of this file returns an empty namespace and any test written
// against it would silently assert nothing. The pages load this as a classic
// <script>, so the global is the real interface; the guard test reads the
// source and evaluates these three functions directly.
if (typeof window !== 'undefined') {
  window.riskKey = riskKey;
  window.riskTone = riskTone;
  window.riskLabel = riskLabel;
}
