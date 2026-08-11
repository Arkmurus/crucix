/**
 * Shared left icon-rail (Claude-style) injected into every app page.
 * Call: Sidebar.init('page-key')
 *
 * R-F2372 — replaced the sidebar+top-header shell with a single slim icon rail
 * that expands (overlaying the content, never reflowing it) to reveal labels.
 * No top header: each tab owns the full viewport height. Alerts + the account
 * menu now live at the bottom of the rail.
 */
// R-F3871 — escapeText lives at MODULE scope.
//
// It was declared inside Sidebar.init(), so it was invisible to Sidebar.html(),
// where R-F3866 added escaping to the nav renderer. The result was
// `ReferenceError: escapeText is not defined` thrown from link() during
// Sidebar.init on EVERY page — which aborted page initialisation and left the
// dashboard and its content blank.
//
// The escaping was right; the SCOPE was not. Same character set as every other
// escaper in the tree (see test/escaper-equivalence-rf3863.test.mjs).
function escapeText(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

const Sidebar = {
  init(activePage) {
    document.getElementById('sidebar-placeholder').innerHTML = this.html(activePage);
    // Header bar removed — keep the placeholder empty so existing pages don't break.
    const hp = document.getElementById('header-placeholder');
    if (hp) hp.innerHTML = '';

    // Restore the user's expand/collapse preference (default: collapsed rail).
    try { if (localStorage.getItem('aria_nav_expanded') === '1') document.body.classList.add('nav-expanded'); } catch (e) {}

    this._bindEvents();
    Auth.me().then(user => {
      if (!user) return;
      const name = document.getElementById('nav-user-name');
      const av   = document.getElementById('nav-avatar-mark');   // the circular avatar mark
      const role = document.getElementById('nav-role');
      if (name) name.textContent = user.fullName || user.username;
      const nameMenu = document.getElementById('nav-user-name-menu');
      if (nameMenu) nameMenu.textContent = user.fullName || user.username;
      if (av) {
        // R-F2349 — shared profile photo (falls back to initials).
        if (user.avatarUrl) {
          av.textContent = '';
          av.style.backgroundImage = `url("${user.avatarUrl}")`;
          av.style.backgroundSize = 'cover';
          av.style.backgroundPosition = 'center';
          av.style.color = 'transparent';
        } else {
          av.style.backgroundImage = '';
          av.textContent = Auth.initials(user);
        }
      }
      if (role) role.textContent = user.role || 'analyst';
      const em = document.getElementById('nav-user-email'); if (em) em.textContent = user.email || '';   // R-F2359
      // R-F2822 — legacy admin reveal retained for any [data-admin] markup that
      // still exists elsewhere; gated NAV links now use [data-gated] below.
      if (user.role === 'admin') {
        document.querySelectorAll('[data-admin]').forEach(e => e.style.display = '');
      }
    });
    // R-F2822 — reveal gated nav links from the SERVER's entitlement answer.
    // The nav used to hand-maintain a data-admin flag on 3 of 11 gated links, so
    // it drifted from lib/auth/operatorPages.mjs in both directions: five links
    // rendered to everyone and then silently 302'd the user back to the dashboard
    // (reads as a broken link), while powerusers could never SEE leads.html /
    // design-partners.html that they are entitled to.
    //
    // Gated links render HIDDEN and are revealed only on an explicit allow. That
    // is deliberate: if this request fails, or the user is not entitled, the link
    // stays hidden — the same outcome the server gate would produce, instead of a
    // link that bounces. requirePageRole() remains the actual boundary; this only
    // stops the nav lying about what the user can do.
    this._applyNavEntitlement();
  },

  async _applyNavEntitlement() {
    const gated = document.querySelectorAll('[data-gated]');
    if (!gated.length) return;
    let allowed = [];
    try {
      // Reuse API.headers() rather than rebuilding the auth header, so a future
      // change to the token scheme cannot leave this call behind. Deliberately NOT
      // API.get(): that logs the user out on 401 (app.js:46), and a nav-decoration
      // request must never be able to sign someone out.
      const r = await fetch('/api/auth/nav-pages', {
        // R-F2873 — `API` is `const API = {...}` in app.js, loaded as a CLASSIC
        // script. Top-level const/let bind to the global LEXICAL environment and
        // are NEVER window properties (only var/function declarations are). So
        // `window.API` was always undefined, this guard always chose {}, the
        // request went out UNAUTHENTICATED, nav-pages 401'd, and every gated link
        // hid from EVERY user — including the admin. Same class as R-F2354 in
        // network.js. typeof is the correct existence check for a lexical global.
        headers: (typeof API !== 'undefined' && API.headers) ? API.headers() : {},
      });
      if (r.ok) {
        const data = await r.json();
        allowed = Array.isArray(data && data.allowed) ? data.allowed : [];
      }
    } catch (e) {
      // Fail closed — leave the gated links hidden. Never throw into page init.
    }
    const allow = new Set(allowed);
    gated.forEach((el) => {
      if (allow.has(el.getAttribute('data-gated'))) el.style.display = '';
    });
  },

  _bindEvents() {
    const overlay = document.getElementById('sidebar-overlay');
    // R-F2372 — the single toggle expands/collapses the rail (persisted). The
    // rail expands as an OVERLAY, so the main content never reflows.
    const onToggle = () => {
      const expanded = document.body.classList.toggle('nav-expanded');
      try { localStorage.setItem('aria_nav_expanded', expanded ? '1' : '0'); } catch (e) {}
    };
    document.querySelectorAll('[data-sidebar-toggle]').forEach(t => t.addEventListener('click', onToggle));

    // Click the scrim (or press Escape) to collapse the expanded rail.
    if (overlay) overlay.addEventListener('click', () => {
      document.body.classList.remove('nav-expanded');
      try { localStorage.setItem('aria_nav_expanded', '0'); } catch (e) {}
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && document.body.classList.contains('nav-expanded')) {
        document.body.classList.remove('nav-expanded');
        try { localStorage.setItem('aria_nav_expanded', '0'); } catch (e) {}
      }
    });

    // Account menu (opens upward from the avatar).
    const navAvatar = document.getElementById('nav-avatar');
    const dropdown  = document.getElementById('nav-dropdown');
    if (navAvatar && dropdown) {
      navAvatar.addEventListener('click', e => {
        e.stopPropagation();
        const isOpen = dropdown.classList.toggle('open');
        navAvatar.setAttribute('aria-expanded', String(isOpen));
      });
      document.addEventListener('click', e => {
        if (!e.target.closest('#nav-dropdown') && !e.target.closest('#nav-avatar')) {
          dropdown.classList.remove('open');
          navAvatar.setAttribute('aria-expanded', 'false');
        }
      });
    }

    const logoutBtn = document.getElementById('btn-logout');
    if (logoutBtn) logoutBtn.addEventListener('click', e => { e.preventDefault(); Auth.logout(); });

    this._bindAlerts();
  },

  // R-F51: watchlist alerts bell + popup (now anchored at the rail bottom).
  _bindAlerts() {
    const bell = document.getElementById('alerts-bell');
    const popup = document.getElementById('alerts-popup');
    const badge = document.getElementById('alerts-badge');
    const list = document.getElementById('alerts-list');
    const markBtn = document.getElementById('alerts-mark-read');
    if (!bell || !popup || !list || !badge) return;

    const userId = (() => {
      try {
        const u = JSON.parse(localStorage.getItem('crucix_user') || '{}');
        return (u.id || u.username || '').toString().replace(/[^A-Za-z0-9]/g, '');
      } catch { return ''; }
    })();

    const authedFetch = (url, opts = {}) => {
      const token = localStorage.getItem('crucix_token') || '';
      return fetch(url, {
        ...opts,
        headers: {
          ...(opts.headers || {}),
          'Authorization': 'Bearer ' + token,
          ...(opts.body ? { 'Content-Type': 'application/json' } : {}),
        },
      });
    };

    const refreshBadge = async () => {
      if (!userId) return;
      try {
        const resp = await authedFetch(
          '/api/aria/dd/watchlist/alerts/unread-count?user_id=' + encodeURIComponent(userId),
        );
        if (!resp.ok) return;
        const data = await resp.json();
        const n = parseInt(data.unread_count, 10) || 0;
        if (n > 0) {
          badge.textContent = n > 99 ? '99+' : String(n);
          badge.style.display = '';
        } else {
          badge.style.display = 'none';
        }
      } catch {}
    };

    const renderAlerts = (alerts) => {
      list.innerHTML = '';
      if (!alerts.length) {
        list.innerHTML = '<div style="padding:24px;text-align:center;color:#9b968c;font-size:13px"><i class="bi bi-check-circle" style="font-size:18px;margin-bottom:6px;display:block"></i>No watchlist alerts in the last 7 days.</div>';
        return;
      }
      alerts.forEach(a => {
        const sev = (a.change_type || '').toLowerCase();
        const sevColor = (sev === 'new_hit' || sev === 'new_pep') ? '#ef4444' :
                         sev === 'score_change' ? '#f59e0b' :
                         sev === 'removed' ? '#22c55e' : '#7c3aed';
        const ts = a.timestamp ? new Date(a.timestamp).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
        const item = document.createElement('div');
        item.style.cssText = 'padding:12px 16px;border-bottom:1px solid #e9e6df;font-size:13px;' + (a.read ? 'opacity:0.55' : '');
        item.innerHTML =
          '<div style="display:flex;align-items:flex-start;gap:10px">' +
            '<div style="width:6px;height:6px;border-radius:50%;background:' + sevColor + ';flex-shrink:0;margin-top:6px"></div>' +
            '<div style="flex:1;min-width:0">' +
              '<div style="font-weight:600;color:#1f1e1c;line-height:1.35;margin-bottom:2px">' + escapeText(a.entity || '(unknown)') + '</div>' +
              '<div style="font-size:12px;color:#57534b;line-height:1.4">' +
                escapeText((a.change_type || '').replace(/_/g, ' ')) +
                (a.old_status && a.new_status && a.old_status !== a.new_status
                  ? ': ' + escapeText(a.old_status) + ' → ' + escapeText(a.new_status)
                  : '') +
              '</div>' +
              (a.impacted_deals && a.impacted_deals.length
                ? '<div style="font-size:11px;color:#fbbf24;margin-top:3px"><i class="bi bi-link-45deg"></i> ' + a.impacted_deals.length + ' deal' + (a.impacted_deals.length === 1 ? '' : 's') + ' impacted</div>'
                : '') +
              '<div style="font-size:11px;color:#9b968c;margin-top:4px">' + ts + '</div>' +
            '</div>' +
          '</div>';
        list.appendChild(item);
      });
    };

    const loadAlerts = async () => {
      list.innerHTML = '<div style="padding:24px;text-align:center;color:#9b968c;font-size:13px">Loading…</div>';
      try {
        const resp = await authedFetch(
          '/api/aria/dd/watchlist/alerts?since_hours=168&user_id=' + encodeURIComponent(userId),
        );
        if (!resp.ok) {
          list.innerHTML = '<div style="padding:24px;text-align:center;color:#6b6862;font-size:13px">Failed to load alerts (HTTP ' + resp.status + ').</div>';
          return;
        }
        const data = await resp.json();
        renderAlerts(data.alerts || []);
      } catch (err) {
        list.innerHTML = '<div style="padding:24px;text-align:center;color:#6b6862;font-size:13px">Network error: ' + escapeText(err.message) + '</div>';
      }
    };

    bell.addEventListener('click', e => {
      e.stopPropagation();
      const isOpen = popup.style.display !== 'none' && popup.style.display !== '';
      if (isOpen) {
        popup.style.display = 'none';
        bell.setAttribute('aria-expanded', 'false');
      } else {
        popup.style.display = 'flex';
        bell.setAttribute('aria-expanded', 'true');
        loadAlerts();
      }
    });

    document.addEventListener('click', e => {
      if (!e.target.closest('#alerts-popup') && !e.target.closest('#alerts-bell')) {
        popup.style.display = 'none';
        bell.setAttribute('aria-expanded', 'false');
      }
    });

    if (markBtn) {
      markBtn.addEventListener('click', async () => {
        if (!userId) return;
        markBtn.disabled = true;
        markBtn.textContent = 'Marking…';
        try {
          await authedFetch('/api/aria/dd/watchlist/alerts/read', {
            method: 'POST',
            body: JSON.stringify({ user_id: userId }),
          });
          badge.style.display = 'none';
          await loadAlerts();
        } catch {}
        markBtn.disabled = false;
        markBtn.textContent = 'Mark all read';
      });
    }

    refreshBadge();
    setInterval(refreshBadge, 60000);
  },

  html(activePage) {
    // Each nav item: fixed icon column (keeps icons aligned as the rail expands)
    // + a label that only shows when expanded. `title` gives a native tooltip
    // while collapsed.
    const link = (page, href, icon, label, extra = '') => {
      const cls = activePage === page ? ' active' : '';
      // icon may be a bootstrap-icons class ("bi-xxx") OR a raw inline <svg…>
      // (for glyphs the bundled font lacks, e.g. the handshake). Both render
      // inside `.rail-link > i` so they inherit the 18px size + muted/active colour.
      const iconHtml = icon.trim().charAt(0) === '<'
        ? `<i aria-hidden="true">${escapeText(icon)}</i>`
        : `<i class="bi ${escapeText(icon)}" aria-hidden="true"></i>`;
      return `<a href="${escapeText(href)}" class="rail-link${escapeText(cls)} ${escapeText(extra)}" data-page="${escapeText(page)}" title="${escapeText(label)}">
        ${iconHtml}<span class="rail-label">${escapeText(label)}</span>
      </a>`;
    };
    return `
    <div id="sidebar-overlay" class="sidebar-overlay"></div>
    <aside id="app-sidebar">
      <div class="rail-top">
        <div class="rail-toprow">
          <a href="/dashboard.html" class="rail-logo" aria-label="ARIA home"></a>
          <span class="rail-name">ARIA</span>
          <button class="rail-toggle" data-sidebar-toggle title="Expand / collapse menu" aria-label="Expand or collapse the menu">
            <i class="bi bi-layout-sidebar" aria-hidden="true"></i>
          </button>
        </div>
      </div>

      <!-- R-F2381 — flat, evenly-spaced nav (section headers removed) -->
      <nav class="rail-nav">
        ${link('aria',          '/aria.html',            'bi-stars',         'ARIA Chat', 'aria-link')}
        ${link('brief',         '/dashboard.html',       'bi-broadcast-pin', 'Intelligence Brief')}
        ${link('dd-reports',    '/dd-reports.html',      'bi-folder2-open',  'DD Reports')}
        ${link('vetting',       '/vetting.html',         'bi-person-check',  'Vetting')}
        ${link('watchlist',     '/watchlist.html',       'bi-eye',           'Watchlist')}
        ${link('news',          '/news.html',            'bi-newspaper',     'News Monitor')}
        ${link('opportunities', '/opportunities.html',   'bi-briefcase',     'Opportunities')}
        <div data-gated="/bd-intelligence.html" style="display:none">${link('bd',            '/bd-intelligence.html', 'bi-graph-up',      'BD Intelligence')}</div>
        ${link('network',       '/network.html',         'bi-people',        'Network')}
        <div data-gated="/vls-chain.html" style="display:none">${link('vls-chain',     '/vls-chain.html',       'bi-shield-check',  'VLS Chain')}</div>
        <div data-gated="/sources.html" style="display:none">${link('sources',    '/sources.html',    'bi-reception-4', 'Source Health')}</div>
        <div data-gated="/vault.html" style="display:none">${link('vault',      '/vault.html',      'bi-key',         'Signup Vault')}</div>
        <div data-gated="/aria-brain" style="display:none">${link('brain',      '/aria-brain',      'bi-heart-fill',  'ARIA Brain')}</div>

        ${link('model-card', '/model-card.html', 'bi-file-text',   'Model Card')}
        <div data-gated="/leads.html" style="display:none">${link('leads', '/leads.html', 'bi-inbox', 'Access Requests')}</div>
        <div data-gated="/design-partners.html" style="display:none">${link('design-partners', '/design-partners.html', '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m11 17 2 2a1 1 0 1 0 3-3"/><path d="m14 14 2.5 2.5a1 1 0 1 0 3-3l-3.88-3.88a3 3 0 0 0-4.24 0l-.88.88a1 1 0 1 1-3-3l2.81-2.81a5.79 5.79 0 0 1 7.06-.87l.47.28a2 2 0 0 0 1.42.25L21 4"/><path d="m21 3 1 11h-2"/><path d="M3 3 2 14l6.5 6.5a1 1 0 1 0 3-3"/><path d="M3 4h8"/></svg>', 'Design Partners')}</div>
        <div data-gated="/admin.html" style="display:none">${link('admin', '/admin.html', 'bi-shield-lock', 'Users')}</div>
      </nav>

      <div class="rail-bottom">
        <div class="rail-alerts">
          <button class="rail-link" id="alerts-bell" title="Watchlist alerts" aria-label="Open watchlist alerts" aria-expanded="false" aria-controls="alerts-popup">
            <i class="bi bi-bell" aria-hidden="true"></i><span class="rail-label">Alerts</span>
            <span id="alerts-badge" class="rail-badge" style="display:none" aria-label="Unread alert count"></span>
          </button>
          <div id="alerts-popup" class="rail-popup" style="display:none">
            <div class="rail-popup-head">
              <div class="rail-popup-title">Watchlist alerts</div>
              <button id="alerts-mark-read" class="rail-popup-mark">Mark all read</button>
            </div>
            <div id="alerts-list" class="rail-popup-list">
              <div style="padding:24px;text-align:center;color:#9b968c;font-size:13px">Loading…</div>
            </div>
          </div>
        </div>

        <div class="nav-dropdown">
          <button class="rail-link rail-account" id="nav-avatar" title="Account" aria-label="Open account menu" aria-haspopup="true" aria-expanded="false">
            <span class="rail-avatar" id="nav-avatar-mark">?</span>
            <span class="rail-label" id="nav-user-name">Account</span>
          </button>
          <div class="nav-dropdown-menu" id="nav-dropdown">
            <div class="nav-dropdown-user">
              <div class="nav-dropdown-name" id="nav-user-name-menu"></div>
              <div class="nav-dropdown-role" id="nav-role">analyst</div>
              <div id="nav-user-email" style="font-size:11px;color:#9b968c;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></div>
            </div>
            <div class="nav-dropdown-divider"></div>
            <a href="/account.html" class="nav-dropdown-item"><i class="bi bi-person-circle"></i> Account &amp; profile</a>
            <a href="/dashboard.html" class="nav-dropdown-item"><i class="bi bi-broadcast-pin"></i> Intelligence Brief</a>
            <a href="/sources.html" class="nav-dropdown-item" data-gated="/sources.html" style="display:none"><i class="bi bi-reception-4"></i> Source Health</a>
            <a href="/vault.html" class="nav-dropdown-item" data-gated="/vault.html" style="display:none"><i class="bi bi-key"></i> Signup Vault</a>
            <div class="nav-dropdown-divider"></div>
            <button class="nav-dropdown-item danger" id="btn-logout"><i class="bi bi-box-arrow-right"></i> Sign Out</button>
          </div>
        </div>
      </div>
    </aside>`;
  },

  // Header bar removed (R-F2372). Kept for API compatibility — returns nothing.
  headerHtml() { return ''; }
};
